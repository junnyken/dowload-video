//! Native multi-connection 4 MiB Range chunked downloader for:
//! 1. Google Drive `/videoplayback` view-only streams (12 parallel short-lived 4 MiB
//!    HTTP Range requests, bypassing Google's ~140 KB/s long-lived connection throttle).
//! 2. Mega.nz public file links (6 parallel 4 MiB `/start-end` requests, on-the-fly
//!    AES-128-CTR decryption at exact block offset, streaming directly to disk for
//!    1–10+ GB files, followed by full Mega CBC-MAC integrity verification).

use aes::cipher::{generic_array::GenericArray, BlockDecrypt, BlockEncrypt, KeyInit};
use aes::Aes128;
use base64::Engine as _;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicU8, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const CHUNK_BYTES: u64 = 4 * 1024 * 1024; // 4 MiB (multiple of 16 for AES-CTR)
pub const DRIVE_POOL: usize = 12;
pub const MEGA_POOL: usize = 6;
const READ_BUF_BYTES: usize = 256 * 1024;
const MAX_CHUNK_ATTEMPTS: usize = 3;

#[derive(Debug, Clone)]
pub struct ChunkProgress {
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
    pub speed_bps: f64,
    pub eta_sec: Option<f64>,
}

#[derive(Debug, Clone)]
pub enum ChunkError {
    Stopped(u8),
    StreamExpired(String),
    QuotaExceeded(String),
    InvalidKey(String),
    IntegrityFailed(String),
    Other(String),
}

impl ChunkError {
    pub fn message(&self) -> String {
        match self {
            ChunkError::Stopped(_) => "stopped".into(),
            ChunkError::StreamExpired(m)
            | ChunkError::QuotaExceeded(m)
            | ChunkError::InvalidKey(m)
            | ChunkError::IntegrityFailed(m)
            | ChunkError::Other(m) => m.clone(),
        }
    }
}

pub fn is_gdrive_videoplayback(url: &str) -> bool {
    let lower = url.trim().to_ascii_lowercase();
    let path = lower.split(['?', '#']).next().unwrap_or("");
    path.ends_with("/videoplayback")
        && (lower.contains(".drive.google.com/") || lower.contains(".docs.google.com/"))
}

pub fn parse_mega_file_url(url: &str) -> Option<(String, String)> {
    let s = url.trim();
    let lower = s.to_ascii_lowercase();
    let host_idx = lower.find("mega.nz/").or_else(|| lower.find("mega.co.nz/"))?;
    let after_host = &s[host_idx..];
    let slash = after_host.find('/')?;
    let rest = &after_host[slash + 1..];
    if let Some(after) = rest.strip_prefix("file/").or_else(|| rest.strip_prefix("FILE/")) {
        let (id, key) = after.split_once('#')?;
        let id = id.trim();
        let key = key.split(['?', '&', '/']).next().unwrap_or("").trim();
        if !id.is_empty() && !key.is_empty() {
            return Some((id.to_string(), key.to_string()));
        }
    } else if let Some(after) = rest.strip_prefix("#!") {
        let (id, key) = after.split_once('!')?;
        let id = id.trim();
        let key = key.split(['?', '&', '/']).next().unwrap_or("").trim();
        if !id.is_empty() && !key.is_empty() {
            return Some((id.to_string(), key.to_string()));
        }
    }
    None
}

pub fn is_native_chunked_url(url: &str) -> bool {
    is_gdrive_videoplayback(url) || parse_mega_file_url(url).is_some()
}

pub fn plan_ranges(total: u64, chunk_size: u64) -> Vec<(u64, u64)> {
    if total == 0 || chunk_size == 0 {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut start = 0u64;
    while start < total {
        let end = (start + chunk_size - 1).min(total - 1);
        out.push((start, end));
        start = end + 1;
    }
    out
}

pub fn parse_content_range_total(header_val: &str) -> Option<u64> {
    let (_, tot) = header_val.trim().rsplit_once('/')?;
    tot.trim().parse::<u64>().ok().filter(|&n| n > 0)
}

/// Extracts a `Cookie: a=b; c=d` header from a Netscape cookie jar file for `url`.
pub fn cookie_header_from_netscape(path: &Path, target_url: &str) -> Option<String> {
    let parsed = url::Url::parse(target_url).ok()?;
    let host = parsed.host_str()?.to_ascii_lowercase();
    let text = std::fs::read_to_string(path).ok()?;
    let mut pairs: Vec<String> = Vec::new();
    for line in text.lines() {
        let l = line.trim();
        if l.is_empty() || l.starts_with('#') {
            continue;
        }
        let cols: Vec<&str> = l.split('\t').collect();
        if cols.len() < 7 {
            continue;
        }
        let domain = cols[0].trim().trim_start_matches('.').to_ascii_lowercase();
        if domain.is_empty() {
            continue;
        }
        if host == domain || host.ends_with(&format!(".{domain}")) {
            let name = cols[5].trim();
            let val = cols[6].trim();
            if !name.is_empty() {
                pairs.push(format!("{name}={val}"));
            }
        }
    }
    if pairs.is_empty() {
        None
    } else {
        Some(pairs.join("; "))
    }
}

// ---------------------------------------------------------------- Mega crypto

pub fn mega_b64_decode(s: &str) -> Result<Vec<u8>, String> {
    let clean = s.trim().trim_end_matches('=');
    base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(clean)
        .map_err(|_| "invalid_mega_key".to_string())
}

pub fn derive_mega_key(key_b64: &str) -> Result<([u8; 16], [u8; 8], [u8; 8]), String> {
    let raw = mega_b64_decode(key_b64)?;
    if raw.len() < 32 {
        return Err("Khóa giải mã Mega.nz không hợp lệ (cần khóa 32 byte sau dấu #).".into());
    }
    let mut aes_key = [0u8; 16];
    for i in 0..16 {
        aes_key[i] = raw[i] ^ raw[i + 16];
    }
    let mut iv8 = [0u8; 8];
    iv8.copy_from_slice(&raw[16..24]);
    let mut meta_mac = [0u8; 8];
    meta_mac.copy_from_slice(&raw[24..32]);
    Ok((aes_key, iv8, meta_mac))
}

/// In-place AES-128-CTR decryption/encryption at a 16-byte-aligned `byte_offset`.
pub fn aes128_ctr_xor(aes_key: &[u8; 16], iv8: &[u8; 8], byte_offset: u64, data: &mut [u8]) {
    let cipher = Aes128::new(GenericArray::from_slice(aes_key));
    let mut block_idx = byte_offset / 16;
    let mut pos = 0usize;
    while pos < data.len() {
        let mut ctr = [0u8; 16];
        ctr[0..8].copy_from_slice(iv8);
        ctr[8..16].copy_from_slice(&block_idx.to_be_bytes());
        let mut keystream = GenericArray::clone_from_slice(&ctr);
        cipher.encrypt_block(&mut keystream);
        let take = (data.len() - pos).min(16);
        for i in 0..take {
            data[pos + i] ^= keystream[i];
        }
        pos += take;
        block_idx = block_idx.wrapping_add(1);
    }
}

/// Decrypts Mega's zero-padded AES-128-CBC `at` attribute blob and extracts `"n"` (filename).
pub fn decrypt_mega_attr_filename(at_b64: &str, aes_key: &[u8; 16]) -> Result<String, String> {
    let ct = mega_b64_decode(at_b64)?;
    if ct.is_empty() || ct.len() % 16 != 0 {
        return Err("Khóa giải mã Mega.nz không hợp lệ.".into());
    }
    let cipher = Aes128::new(GenericArray::from_slice(aes_key));
    let mut prev = [0u8; 16];
    let mut pt = Vec::with_capacity(ct.len());
    for chunk in ct.chunks_exact(16) {
        let mut blk = GenericArray::clone_from_slice(chunk);
        cipher.decrypt_block(&mut blk);
        for i in 0..16 {
            pt.push(blk[i] ^ prev[i]);
        }
        prev.copy_from_slice(chunk);
    }
    while pt.last() == Some(&0) {
        pt.pop();
    }
    let txt = String::from_utf8(pt).map_err(|_| "Khóa giải mã Mega.nz không hợp lệ.".to_string())?;
    let json_part = txt
        .strip_prefix("MEGA")
        .ok_or_else(|| "Khóa giải mã Mega.nz không hợp lệ.".to_string())?;
    let val: serde_json::Value =
        serde_json::from_str(json_part.trim()).map_err(|_| "Khóa giải mã Mega.nz không hợp lệ.".to_string())?;
    let name = val
        .get("n")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| "Khóa giải mã Mega.nz không hợp lệ.".to_string())?;
    Ok(name.to_string())
}

fn cbc_mac_slice(cipher: &Aes128, data: &[u8], iv16: &[u8; 16]) -> [u8; 16] {
    let mut state = *iv16;
    let mut pos = 0usize;
    while pos < data.len() {
        let mut blk = [0u8; 16];
        let take = (data.len() - pos).min(16);
        blk[..take].copy_from_slice(&data[pos..pos + take]);
        for i in 0..16 {
            blk[i] ^= state[i];
        }
        let mut ga = GenericArray::clone_from_slice(&blk);
        cipher.encrypt_block(&mut ga);
        state.copy_from_slice(&ga);
        pos += take;
    }
    state
}

/// Computes Mega's 8-byte CBC-MAC streaming from disk (<= 1 MiB RAM).
pub fn compute_mega_meta_mac_file(path: &Path, aes_key: &[u8; 16], iv8: &[u8; 8]) -> std::io::Result<[u8; 8]> {
    let cipher = Aes128::new(GenericArray::from_slice(aes_key));
    let mut chunk_iv = [0u8; 16];
    chunk_iv[0..8].copy_from_slice(iv8);
    chunk_iv[8..16].copy_from_slice(iv8);

    let mut fh = File::open(path)?;
    let mut chunk_macs: Vec<u8> = Vec::new();
    let mut idx = 1usize;
    let mut buf = vec![0u8; 1048576];
    loop {
        let target = if idx <= 7 { idx * 131072 } else { 1048576 };
        idx += 1;
        let mut read_total = 0usize;
        while read_total < target {
            let n = fh.read(&mut buf[read_total..target])?;
            if n == 0 {
                break;
            }
            read_total += n;
        }
        if read_total == 0 {
            break;
        }
        let cmac = cbc_mac_slice(&cipher, &buf[..read_total], &chunk_iv);
        chunk_macs.extend_from_slice(&cmac);
    }

    let final_mac = if chunk_macs.is_empty() {
        [0u8; 16]
    } else {
        cbc_mac_slice(&cipher, &chunk_macs, &[0u8; 16])
    };
    let mut meta = [0u8; 8];
    for i in 0..4 {
        meta[i] = final_mac[i] ^ final_mac[i + 4];
        meta[i + 4] = final_mac[i + 8] ^ final_mac[i + 12];
    }
    Ok(meta)
}

// ---------------------------------------------------------------- Filename helpers

fn sanitize_filename(raw: &str, default_ext: &str) -> String {
    let trimmed = raw.trim();
    let cleaned: String = trimmed
        .chars()
        .map(|c| match c {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect();
    let cleaned = cleaned.trim_matches(|c| c == '.' || c == ' ').to_string();
    let base = if cleaned.is_empty() { "video".to_string() } else { cleaned };
    if base.contains('.') {
        base
    } else {
        format!("{base}.{default_ext}")
    }
}

fn unique_dest_path(out_dir: &Path, filename: &str) -> PathBuf {
    let candidate = out_dir.join(filename);
    if !candidate.exists() {
        return candidate;
    }
    let stem = Path::new(filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("video");
    let ext = Path::new(filename)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("mp4");
    for i in 2..1000 {
        let next = out_dir.join(format!("{stem} ({i}).{ext}"));
        if !next.exists() {
            return next;
        }
    }
    candidate
}

// ---------------------------------------------------------------- Core parallel download

#[derive(Clone)]
enum Mode {
    Drive {
        url: String,
        headers: Vec<(String, String)>,
    },
    Mega {
        base_url: String,
        aes_key: [u8; 16],
        iv8: [u8; 8],
        meta_mac: [u8; 8],
    },
}

fn make_agent(timeout_secs: u64) -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(timeout_secs)))
        .http_status_as_error(false)
        .build()
        .into()
}

pub fn run_native_download<F>(
    url: &str,
    out_dir: &Path,
    preferred_title: Option<&str>,
    headers: &[(String, String)],
    cookie_file: Option<&Path>,
    stop: Arc<AtomicU8>,
    on_progress: F,
) -> Result<PathBuf, ChunkError>
where
    F: Fn(ChunkProgress) + Send + Sync + 'static,
{
    let agent = make_agent(60);
    let (mode, total_size, filename) = if let Some((file_id, key_b64)) = parse_mega_file_url(url) {
        let (aes_key, iv8, meta_mac) = derive_mega_key(&key_b64).map_err(ChunkError::InvalidKey)?;
        let req_body = serde_json::json!([{"a": "g", "g": 1, "p": file_id}]).to_string();
        let mut resp = agent
            .post("https://g.api.mega.co.nz/cs?id=1")
            .header("Content-Type", "application/json")
            .send(req_body.as_bytes())
            .map_err(|e| ChunkError::Other(format!("Mega API lỗi kết nối: {e}")))?;
        let status = resp.status().as_u16();
        if status == 509 {
            let wait = resp
                .headers()
                .get("x-mega-time-left")
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.trim().parse::<u64>().ok())
                .map(|s| format!(" Thử lại sau ~{} phút.", (s + 59) / 60))
                .unwrap_or_default();
            return Err(ChunkError::QuotaExceeded(format!(
                "Mega.nz đã đạt giới hạn băng thông miễn phí (~5 GB/6h) trên mạng này.{wait}"
            )));
        }
        let body_text = resp
            .body_mut()
            .read_to_string()
            .map_err(|e| ChunkError::Other(format!("Mega API đọc phản hồi lỗi: {e}")))?;
        let val: serde_json::Value = serde_json::from_str(&body_text)
            .map_err(|e| ChunkError::Other(format!("Mega API JSON lỗi: {e}")))?;
        let item = val.as_array().and_then(|a| a.first()).unwrap_or(&val);
        if let Some(code) = item.as_i64() {
            if code == -17 || code == -18 {
                return Err(ChunkError::QuotaExceeded(
                    "Mega.nz đã đạt giới hạn băng thông miễn phí (~5 GB/6h) trên mạng này.".into(),
                ));
            }
            return Err(ChunkError::Other(format!("Mega API trả mã lỗi {code}")));
        }
        let dl_url = item
            .get("g")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                ChunkError::QuotaExceeded(
                    "Mega.nz không cấp đường dẫn tải (có thể đã chạm giới hạn 5 GB/6h).".into(),
                )
            })?
            .trim_end_matches('/')
            .to_string();
        let size = item.get("s").and_then(|v| v.as_u64()).unwrap_or(0);
        let at = item.get("at").and_then(|v| v.as_str()).unwrap_or("");
        let real_name = if !at.is_empty() {
            decrypt_mega_attr_filename(at, &aes_key).map_err(ChunkError::InvalidKey)?
        } else {
            preferred_title.unwrap_or(&file_id).to_string()
        };
        (
            Mode::Mega { base_url: dl_url, aes_key, iv8, meta_mac },
            size,
            sanitize_filename(&real_name, "mp4"),
        )
    } else {
        // Google Drive /videoplayback
        let mut req_headers: Vec<(String, String)> = headers.to_vec();
        let has_referer = req_headers.iter().any(|(k, _)| k.eq_ignore_ascii_case("referer"));
        if !has_referer {
            req_headers.push(("Referer".into(), "https://drive.google.com/".into()));
        }
        let has_ua = req_headers.iter().any(|(k, _)| k.eq_ignore_ascii_case("user-agent"));
        if !has_ua {
            req_headers.push((
                "User-Agent".into(),
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36".into(),
            ));
        }
        let has_cookie = req_headers.iter().any(|(k, _)| k.eq_ignore_ascii_case("cookie"));
        if !has_cookie {
            if let Some(ck_path) = cookie_file {
                if let Some(ck_val) = cookie_header_from_netscape(ck_path, url) {
                    req_headers.push(("Cookie".into(), ck_val));
                }
            }
        }
        // Probe total size with Range: bytes=0-0
        let mut probe_req = agent.get(url).header("Range", "bytes=0-0");
        for (k, v) in &req_headers {
            probe_req = probe_req.header(k.as_str(), v.as_str());
        }
        let probe_resp = probe_req
            .call()
            .map_err(|e| ChunkError::Other(format!("Drive probe lỗi kết nối: {e}")))?;
        let st = probe_resp.status().as_u16();
        if st == 403 || st == 410 {
            return Err(ChunkError::StreamExpired(format!(
                "Liên kết Google Drive đã hết hạn (HTTP {st}) — hãy bấm tải lại từ trình duyệt."
            )));
        }
        if st != 206 && st != 200 {
            return Err(ChunkError::Other(format!("Google Drive trả về HTTP {st}")));
        }
        let size = probe_resp
            .headers()
            .get("content-range")
            .and_then(|v| v.to_str().ok())
            .and_then(parse_content_range_total)
            .or_else(|| {
                probe_resp
                    .headers()
                    .get("content-length")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|s| s.trim().parse::<u64>().ok())
            })
            .unwrap_or(0);
        let name = sanitize_filename(preferred_title.unwrap_or("google_drive_video"), "mp4");
        (Mode::Drive { url: url.to_string(), headers: req_headers }, size, name)
    };

    if total_size == 0 {
        return Err(ChunkError::Other("Không xác định được dung lượng tệp để tải đa luồng.".into()));
    }

    std::fs::create_dir_all(out_dir).map_err(|e| ChunkError::Other(format!("Không tạo được thư mục lưu: {e}")))?;
    let final_path = unique_dest_path(out_dir, &filename);
    let part_path = PathBuf::from(format!("{}.part", final_path.display()));
    let file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&part_path)
        .map_err(|e| ChunkError::Other(format!("Không mở được tệp tạm: {e}")))?;
    file.set_len(total_size)
        .map_err(|e| ChunkError::Other(format!("Không cấp phát được dung lượng tệp: {e}")))?;

    let ranges = Arc::new(plan_ranges(total_size, CHUNK_BYTES));
    let pool = match mode {
        Mode::Drive { .. } => DRIVE_POOL.min(ranges.len()).max(1),
        Mode::Mega { .. } => MEGA_POOL.min(ranges.len()).max(1),
    };
    let next_idx = Arc::new(AtomicUsize::new(0));
    let downloaded = Arc::new(AtomicU64::new(0));
    let first_err: Arc<Mutex<Option<ChunkError>>> = Arc::new(Mutex::new(None));
    let file_arc = Arc::new(Mutex::new(file));
    let last_emit = Arc::new(Mutex::new(Instant::now() - Duration::from_secs(1)));
    let started = Instant::now();
    let on_progress = Arc::new(on_progress);

    let mut handles = Vec::with_capacity(pool);
    for _ in 0..pool {
        let mode = mode.clone();
        let ranges = ranges.clone();
        let next_idx = next_idx.clone();
        let downloaded = downloaded.clone();
        let first_err = first_err.clone();
        let file_arc = file_arc.clone();
        let last_emit = last_emit.clone();
        let stop = stop.clone();
        let on_progress = on_progress.clone();

        handles.push(std::thread::spawn(move || {
            let worker_agent = make_agent(60);
            let mut buf = vec![0u8; READ_BUF_BYTES];
            loop {
                let s_val = stop.load(Ordering::SeqCst);
                if s_val != 0 {
                    let mut g = first_err.lock().unwrap_or_else(|e| e.into_inner());
                    if g.is_none() {
                        *g = Some(ChunkError::Stopped(s_val));
                    }
                    return;
                }
                if first_err.lock().unwrap_or_else(|e| e.into_inner()).is_some() {
                    return;
                }
                let idx = next_idx.fetch_add(1, Ordering::SeqCst);
                if idx >= ranges.len() {
                    return;
                }
                let (start, end) = ranges[idx];
                let expected = end - start + 1;

                let mut chunk_ok = false;
                let mut last_chunk_err: Option<ChunkError> = None;
                for attempt in 0..MAX_CHUNK_ATTEMPTS {
                    if stop.load(Ordering::SeqCst) != 0 {
                        return;
                    }
                    let req_res = match &mode {
                        Mode::Drive { url, headers } => {
                            let mut r = worker_agent
                                .get(url.as_str())
                                .header("Range", format!("bytes={start}-{end}"));
                            for (k, v) in headers {
                                r = r.header(k.as_str(), v.as_str());
                            }
                            r.call()
                        }
                        Mode::Mega { base_url, .. } => {
                            let chunk_url = format!("{base_url}/{start}-{end}");
                            worker_agent.get(&chunk_url).call()
                        }
                    };
                    let mut resp = match req_res {
                        Ok(r) => r,
                        Err(e) => {
                            last_chunk_err = Some(ChunkError::Other(format!("Lỗi mạng khối {idx}: {e}")));
                            std::thread::sleep(Duration::from_millis(400 * (attempt as u64 + 1)));
                            continue;
                        }
                    };
                    let st = resp.status().as_u16();
                    if st == 403 || st == 410 {
                        last_chunk_err = Some(ChunkError::StreamExpired(format!(
                            "Liên kết tải đã hết hạn (HTTP {st})."
                        )));
                        break;
                    }
                    if st == 509 {
                        last_chunk_err = Some(ChunkError::QuotaExceeded(
                            "Mega.nz đã đạt giới hạn băng thông miễn phí (~5 GB/6h) trên mạng này.".into(),
                        ));
                        break;
                    }
                    if st != 206 && st != 200 {
                        last_chunk_err = Some(ChunkError::Other(format!("HTTP {st} tại khối {idx}")));
                        std::thread::sleep(Duration::from_millis(400 * (attempt as u64 + 1)));
                        continue;
                    }

                    let mut reader = resp.body_mut().as_reader();
                    let mut written = 0u64;
                    let mut read_err: Option<String> = None;
                    let mut decrypt_rem_offset = start;
                    while written < expected {
                        if stop.load(Ordering::SeqCst) != 0 {
                            return;
                        }
                        let want = ((expected - written) as usize).min(buf.len());
                        match reader.read(&mut buf[..want]) {
                            Ok(0) => break,
                            Ok(n) => {
                                if let Mode::Mega { aes_key, iv8, .. } = &mode {
                                    // Keep CTR decryption 16-byte aligned by buffering full reads
                                    // or encrypting/decrypting at exact byte offset:
                                    aes128_ctr_xor_unaligned(aes_key, iv8, decrypt_rem_offset, &mut buf[..n]);
                                }
                                {
                                    let mut fh = file_arc.lock().unwrap_or_else(|e| e.into_inner());
                                    if let Err(e) = fh.seek(SeekFrom::Start(start + written)).and_then(|_| fh.write_all(&buf[..n])) {
                                        read_err = Some(format!("Lỗi ghi đĩa: {e}"));
                                        break;
                                    }
                                }
                                written += n as u64;
                                decrypt_rem_offset += n as u64;
                                let done_now = downloaded.fetch_add(n as u64, Ordering::Relaxed) + n as u64;
                                let now = Instant::now();
                                let should_emit = {
                                    let mut le = last_emit.lock().unwrap_or_else(|e| e.into_inner());
                                    if now.duration_since(*le) >= Duration::from_millis(250) || done_now >= total_size {
                                        *le = now;
                                        true
                                    } else {
                                        false
                                    }
                                };
                                if should_emit {
                                    let elapsed = started.elapsed().as_secs_f64().max(0.05);
                                    let speed = done_now as f64 / elapsed;
                                    let eta = if total_size > done_now && speed > 1.0 {
                                        Some((total_size - done_now) as f64 / speed)
                                    } else {
                                        None
                                    };
                                    on_progress(ChunkProgress {
                                        downloaded_bytes: done_now.min(total_size),
                                        total_bytes: total_size,
                                        speed_bps: speed,
                                        eta_sec: eta,
                                    });
                                }
                            }
                            Err(e) => {
                                read_err = Some(format!("Lỗi đọc mạng: {e}"));
                                break;
                            }
                        }
                    }

                    if read_err.is_none() && written == expected {
                        chunk_ok = true;
                        break;
                    }
                    // Roll back progress counter for retried partial bytes
                    if written > 0 {
                        downloaded.fetch_sub(written, Ordering::Relaxed);
                    }
                    last_chunk_err = Some(ChunkError::Other(
                        read_err.unwrap_or_else(|| format!("Khối {idx} thiếu dữ liệu ({written}/{expected})")),
                    ));
                    std::thread::sleep(Duration::from_millis(400 * (attempt as u64 + 1)));
                }

                if !chunk_ok {
                    let mut g = first_err.lock().unwrap_or_else(|e| e.into_inner());
                    if g.is_none() {
                        *g = last_chunk_err.or_else(|| Some(ChunkError::Other(format!("Tải khối {idx} thất bại"))));
                    }
                    return;
                }
            }
        }));
    }

    for h in handles {
        let _ = h.join();
    }
    drop(file_arc);

    let s_val = stop.load(Ordering::SeqCst);
    if s_val != 0 {
        let _ = std::fs::remove_file(&part_path);
        return Err(ChunkError::Stopped(s_val));
    }
    if let Some(err) = first_err.lock().unwrap_or_else(|e| e.into_inner()).take() {
        let _ = std::fs::remove_file(&part_path);
        return Err(err);
    }

    // Mega CBC-MAC verification before finalizing
    if let Mode::Mega { aes_key, iv8, meta_mac, .. } = &mode {
        let actual_mac = compute_mega_meta_mac_file(&part_path, aes_key, iv8)
            .map_err(|e| ChunkError::Other(format!("Lỗi kiểm tra MAC tệp Mega: {e}")))?;
        if actual_mac != *meta_mac {
            let _ = std::fs::remove_file(&part_path);
            return Err(ChunkError::IntegrityFailed(
                "Kiểm tra toàn vẹn dữ liệu (MAC) của tệp Mega.nz không khớp — tệp đã bị hủy để tránh file hỏng.".into(),
            ));
        }
    }

    std::fs::rename(&part_path, &final_path).map_err(|e| {
        let _ = std::fs::remove_file(&part_path);
        ChunkError::Other(format!("Không đổi tên được tệp tải xong: {e}"))
    })?;
    Ok(final_path)
}

/// AES-128-CTR XOR supporting arbitrary (possibly unaligned) `byte_offset` for streaming reads.
pub fn aes128_ctr_xor_unaligned(aes_key: &[u8; 16], iv8: &[u8; 8], byte_offset: u64, data: &mut [u8]) {
    if data.is_empty() {
        return;
    }
    let cipher = Aes128::new(GenericArray::from_slice(aes_key));
    let mut block_idx = byte_offset / 16;
    let mut skip = (byte_offset % 16) as usize;
    let mut pos = 0usize;
    while pos < data.len() {
        let mut ctr = [0u8; 16];
        ctr[0..8].copy_from_slice(iv8);
        ctr[8..16].copy_from_slice(&block_idx.to_be_bytes());
        let mut ks = GenericArray::clone_from_slice(&ctr);
        cipher.encrypt_block(&mut ks);
        let avail = 16 - skip;
        let take = (data.len() - pos).min(avail);
        for i in 0..take {
            data[pos + i] ^= ks[skip + i];
        }
        pos += take;
        skip = 0;
        block_idx = block_idx.wrapping_add(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_drive_and_mega_urls() {
        assert!(is_gdrive_videoplayback(
            "https://rr2---sn-42u-nbos.c.drive.google.com/videoplayback?expire=123&itag=37"
        ));
        assert!(!is_gdrive_videoplayback("https://drive.google.com/file/d/abc/view"));
        assert_eq!(
            parse_mega_file_url("https://mega.nz/file/AbC123#KeyXYZ"),
            Some(("AbC123".into(), "KeyXYZ".into()))
        );
        assert_eq!(
            parse_mega_file_url("https://mega.co.nz/#!OldId!OldKey"),
            Some(("OldId".into(), "OldKey".into()))
        );
        assert_eq!(parse_mega_file_url("https://mega.nz/file/NoKey"), None);
    }

    #[test]
    fn plans_4mb_ranges_without_gaps() {
        let r = plan_ranges(10 * 1024 * 1024 + 5, CHUNK_BYTES);
        assert_eq!(r.len(), 3);
        assert_eq!(r[0], (0, 4 * 1024 * 1024 - 1));
        assert_eq!(r[1], (4 * 1024 * 1024, 8 * 1024 * 1024 - 1));
        assert_eq!(r[2], (8 * 1024 * 1024, 10 * 1024 * 1024 + 4));
    }

    #[test]
    fn mega_ctr_unaligned_and_cbc_mac_roundtrip() {
        let aes_key: [u8; 16] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];
        let iv8: [u8; 8] = [21, 22, 23, 24, 25, 26, 27, 28];
        let plaintext: Vec<u8> = (0..(200 * 1024)).map(|i| (i % 251) as u8).collect();

        // Encrypt in one pass
        let mut ct = plaintext.clone();
        aes128_ctr_xor(&aes_key, &iv8, 0, &mut ct);
        assert_ne!(ct, plaintext);

        // Decrypt in unaligned pieces (simulating arbitrary TCP read() boundaries)
        let mut dec = ct.clone();
        aes128_ctr_xor_unaligned(&aes_key, &iv8, 0, &mut dec[..10007]);
        aes128_ctr_xor_unaligned(&aes_key, &iv8, 10007, &mut dec[10007..131072]);
        aes128_ctr_xor_unaligned(&aes_key, &iv8, 131072, &mut dec[131072..]);
        assert_eq!(dec, plaintext);

        // Verify CBC-MAC on temp file & corruption detection
        let tmp = std::env::temp_dir().join("vg_chunked_mac_test.bin");
        std::fs::write(&tmp, &plaintext).unwrap();
        let mac1 = compute_mega_meta_mac_file(&tmp, &aes_key, &iv8).unwrap();
        let mut bad = plaintext.clone();
        bad[500] ^= 0xFF;
        std::fs::write(&tmp, &bad).unwrap();
        let mac2 = compute_mega_meta_mac_file(&tmp, &aes_key, &iv8).unwrap();
        let _ = std::fs::remove_file(&tmp);
        assert_ne!(mac1, mac2);
    }

    #[test]
    fn mega_attr_decrypt_recovers_real_filename_and_rejects_wrong_key() {
        let aes_key: [u8; 16] = [9; 16];
        let mut raw = b"MEGA{\"n\":\"Buoi_17_FullHD.mp4\"}".to_vec();
        while raw.len() % 16 != 0 {
            raw.push(0);
        }
        // Encrypt AES-128-CBC zero IV
        let cipher = Aes128::new(GenericArray::from_slice(&aes_key));
        let mut prev = [0u8; 16];
        let mut ct = Vec::new();
        for chunk in raw.chunks_exact(16) {
            let mut blk = [0u8; 16];
            for i in 0..16 {
                blk[i] = chunk[i] ^ prev[i];
            }
            let mut ga = GenericArray::clone_from_slice(&blk);
            cipher.encrypt_block(&mut ga);
            prev.copy_from_slice(&ga);
            ct.extend_from_slice(&ga);
        }
        let b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&ct);
        assert_eq!(decrypt_mega_attr_filename(&b64, &aes_key).unwrap(), "Buoi_17_FullHD.mp4");
        assert!(decrypt_mega_attr_filename(&b64, &[1; 16]).is_err());
    }

    #[test]
    fn netscape_cookie_extracts_drive_stream() {
        let tmp = std::env::temp_dir().join("vg_cookie_test.txt");
        std::fs::write(
            &tmp,
            "# Netscape HTTP Cookie File\n.drive.google.com\tTRUE\t/\tTRUE\t1999999999\tDRIVE_STREAM\tabc123\n.other.com\tTRUE\t/\tTRUE\t1999999999\tFOO\tbar\n",
        )
        .unwrap();
        let hdr = cookie_header_from_netscape(
            &tmp,
            "https://rr2---sn-42u.c.drive.google.com/videoplayback?id=1",
        );
        let _ = std::fs::remove_file(&tmp);
        assert_eq!(hdr.as_deref(), Some("DRIVE_STREAM=abc123"));
    }
}
