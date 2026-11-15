//! 0.22.0 (quality #7 app): the subtitle files yt-dlp wrote next to a finished
//! download (`<stem>.<lang>.srt`, Settings → Phụ đề → "Tệp .srt riêng"), read
//! for the server's translation job and the translation written back beside it.
//!
//! The webview names a file the app produced (paths::check_produced) and a
//! language code; it never names the subtitle path itself.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// Same cap as the server's upload (`transcript_translate._MAX_UPLOAD_MB`).
pub const MAX_BYTES: u64 = 2 * 1024 * 1024;

/// yt-dlp language codes: "en", "vi", "en-US", "zh-Hans", "en-orig".
pub fn valid_lang(lang: &str) -> bool {
    (1..=20).contains(&lang.len()) && lang.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

fn stem_and_dir(media: &Path) -> Option<(&Path, &str)> {
    Some((media.parent()?, media.file_stem()?.to_str()?))
}

/// The `.srt` path for `lang` beside `media`.
pub fn srt_path(media: &Path, lang: &str) -> Option<PathBuf> {
    if !valid_lang(lang) {
        return None;
    }
    let (dir, stem) = stem_and_dir(media)?;
    Some(dir.join(format!("{stem}.{lang}.srt")))
}

/// Languages that have a regular `<stem>.<lang>.srt` file beside `media`, sorted.
pub fn list(media: &Path) -> Vec<String> {
    let Some((dir, stem)) = stem_and_dir(media) else { return vec![] };
    let prefix = format!("{stem}.");
    let mut out: Vec<String> = fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))
        .filter_map(|e| {
            let name = e.file_name().into_string().ok()?;
            let lang = name.strip_prefix(&prefix)?.strip_suffix(".srt")?;
            valid_lang(lang).then(|| lang.to_string())
        })
        .collect();
    out.sort();
    out
}

/// UTF-8 text of the `lang` subtitle beside `media` (BOM dropped).
pub fn read(media: &Path, lang: &str) -> Result<String, String> {
    let p = srt_path(media, lang).ok_or("invalid subtitle language")?;
    let meta = fs::symlink_metadata(&p).map_err(|_| "subtitle file not found")?;
    if !meta.is_file() {
        return Err("subtitle file not found".into());
    }
    if meta.len() > MAX_BYTES {
        return Err("subtitle file is larger than 2 MB".into());
    }
    let text = String::from_utf8(fs::read(&p).map_err(|e| e.to_string())?).map_err(|_| "subtitle file is not UTF-8")?;
    Ok(text.strip_prefix('\u{feff}').unwrap_or(&text).to_string())
}

/// Writes `content` as `<stem>.<lang>.srt` beside `media`; never replaces an
/// existing file. Returns the written path.
pub fn write(media: &Path, lang: &str, content: &str) -> Result<PathBuf, String> {
    let p = srt_path(media, lang).ok_or("invalid subtitle language")?;
    if content.len() as u64 > 2 * MAX_BYTES || content.contains('\0') || content.trim().is_empty() {
        return Err("not a subtitle file".into());
    }
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&p)
        .map_err(|e| if e.kind() == std::io::ErrorKind::AlreadyExists { "subtitle file already exists".to_string() } else { e.to_string() })?;
    if let Err(e) = f.write_all(content.as_bytes()) {
        drop(f);
        let _ = fs::remove_file(&p);
        return Err(e.to_string());
    }
    Ok(p)
}

fn format_srt_ts(raw: &str) -> Option<String> {
    let parts: Vec<&str> = raw.trim().split(':').collect();
    let (h, m, rest) = match parts.as_slice() {
        [m, s] => (0u32, m.parse::<u32>().ok()?, *s),
        [h, m, s] => (h.parse::<u32>().ok()?, m.parse::<u32>().ok()?, *s),
        _ => return None,
    };
    let (sec_str, ms_str) = rest.split_once('.').or_else(|| rest.split_once(','))?;
    let s = sec_str.parse::<u32>().ok()?;
    if m >= 60 || s >= 60 {
        return None;
    }
    let mut ms = ms_str.chars().take(3).collect::<String>();
    if !ms.chars().all(|c| c.is_ascii_digit()) || ms.is_empty() {
        return None;
    }
    while ms.len() < 3 {
        ms.push('0');
    }
    Some(format!("{h:02}:{m:02}:{s:02},{ms}"))
}

fn parse_cue_timing(line: &str) -> Option<String> {
    let (lhs, rhs_full) = line.split_once("-->")?;
    let rhs = rhs_full.trim_start().split_whitespace().next()?;
    Some(format!("{} --> {}", format_srt_ts(lhs)?, format_srt_ts(rhs)?))
}

fn strip_vtt_tags(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    let mut in_tag = false;
    for ch in line.chars() {
        if ch == '<' {
            in_tag = true;
        } else if ch == '>' && in_tag {
            in_tag = false;
        } else if !in_tag {
            out.push(ch);
        }
    }
    out.trim().to_string()
}

/// Converts WebVTT text (including concatenated HLS WebVTT segments) into UTF-8 `.srt`,
/// or normalizes text that is already SubRip `.srt`.
pub fn vtt_to_srt(vtt: &str) -> String {
    let clean = vtt.strip_prefix('\u{feff}').unwrap_or(vtt).replace("\r\n", "\n").replace('\r', "\n");
    let trimmed = clean.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    if !trimmed.starts_with("WEBVTT") && trimmed.contains(" --> ") && trimmed.contains(',') {
        let mut s = trimmed.to_string();
        s.push('\n');
        return s;
    }
    let mut cues: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for block in clean.split("\n\n") {
        let lines: Vec<&str> = block.lines().map(str::trim_end).skip_while(|l| l.trim().is_empty()).collect();
        if lines.is_empty() {
            continue;
        }
        let head = lines[0].trim();
        if head.starts_with("WEBVTT")
            || head.starts_with("STYLE")
            || head.starts_with("NOTE")
            || head.starts_with("REGION")
            || head.starts_with("X-TIMESTAMP-MAP=")
        {
            continue;
        }
        let Some((idx, timing)) = lines.iter().enumerate().find_map(|(i, l)| parse_cue_timing(l.trim()).map(|t| (i, t))) else {
            continue;
        };
        let body: Vec<String> = lines[idx + 1..]
            .iter()
            .map(|l| strip_vtt_tags(l))
            .filter(|l| !l.is_empty())
            .collect();
        if body.is_empty() {
            continue;
        }
        let joined = body.join("\n");
        let key = format!("{timing}|{joined}");
        if !seen.insert(key) {
            continue;
        }
        cues.push(format!("{}\n{timing}\n{joined}", cues.len() + 1));
    }
    if cues.is_empty() {
        String::new()
    } else {
        let mut out = cues.join("\n\n");
        out.push('\n');
        out
    }
}

/// Writes an external companion subtitle (`srt` or `ass`) beside `media`, picking `<lang>-2`
/// if `<stem>.<lang>.<ext>` already exists so existing files are never overwritten.
pub fn write_companion(media: &Path, lang: &str, ext: &str, content: &str) -> Result<PathBuf, String> {
    if !matches!(ext, "srt" | "ass") {
        return Err("unsupported subtitle extension".into());
    }
    if !valid_lang(lang) {
        return Err("invalid subtitle language".into());
    }
    if content.len() as u64 > 2 * MAX_BYTES || content.contains('\0') || content.trim().is_empty() {
        return Err("not a subtitle file".into());
    }
    let (dir, stem) = stem_and_dir(media).ok_or("invalid media path")?;
    for n in 1..=8u8 {
        let tag = if n == 1 { lang.to_string() } else { format!("{lang}-{n}") };
        if !valid_lang(&tag) {
            break;
        }
        let candidate = dir.join(format!("{stem}.{tag}.{ext}"));
        match fs::OpenOptions::new().write(true).create_new(true).open(&candidate) {
            Ok(mut f) => {
                if let Err(e) = f.write_all(content.as_bytes()) {
                    drop(f);
                    let _ = fs::remove_file(&candidate);
                    return Err(e.to_string());
                }
                return Ok(candidate);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.to_string()),
        }
    }
    Err("subtitle file already exists".into())
}

/// Maps common BCP-47 / ISO-639-1 subtitle language tags to ISO-639-2/B codes
/// recognized by QuickTime, VLC, MPC-HC, and Windows Media Player for `mov_text`.
pub fn iso639_2_lang(tag: &str) -> &'static str {
    let primary = tag
        .trim()
        .split(['-', '_'])
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    match primary.as_str() {
        "vi" | "vie" => "vie",
        "en" | "eng" => "eng",
        "ja" | "jp" | "jpn" => "jpn",
        "ko" | "kor" => "kor",
        "zh" | "chi" | "zho" | "cn" => "chi",
        "th" | "tha" => "tha",
        "id" | "ind" => "ind",
        "ms" | "may" | "msa" => "may",
        "fr" | "fre" | "fra" => "fre",
        "de" | "ger" | "deu" => "ger",
        "es" | "spa" => "spa",
        "pt" | "por" => "por",
        "ru" | "rus" => "rus",
        "it" | "ita" => "ita",
        "ar" | "ara" => "ara",
        "hi" | "hin" => "hin",
        _ => "und",
    }
}

/// Muxes up to 4 UTF-8 `.srt` tracks directly into `media_mp4` as selectable `mov_text`
/// soft-subtitle streams using bundled `ffmpeg` (`-c copy -c:s mov_text`, zero video/audio re-encode).
/// External `.srt` files remain untouched beside the `.mp4`.
pub fn mux_soft_subs_mp4(
    ffmpeg: &Path,
    media_mp4: &Path,
    srt_tracks: &[(PathBuf, String)],
) -> Result<bool, String> {
    if srt_tracks.is_empty() {
        return Ok(false);
    }
    if !media_mp4
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("mp4") || e.eq_ignore_ascii_case("m4v"))
    {
        return Ok(false);
    }
    let orig_size = fs::metadata(media_mp4).map_err(|e| e.to_string())?.len();
    if orig_size == 0 {
        return Err("empty media file".into());
    }
    let valid_tracks: Vec<&(PathBuf, String)> = srt_tracks
        .iter()
        .filter(|(p, _)| p.is_file() && fs::metadata(p).is_ok_and(|m| m.len() > 0 && m.len() <= 2 * MAX_BYTES))
        .take(4)
        .collect();
    if valid_tracks.is_empty() {
        return Ok(false);
    }
    let (dir, stem) = stem_and_dir(media_mp4).ok_or("invalid media path")?;
    let tmp_out = dir.join(format!("{stem}.vg-submux-{}.mp4", std::process::id()));
    let _ = fs::remove_file(&tmp_out);

    let mut cmd = std::process::Command::new(ffmpeg);
    cmd.args(["-y", "-hide_banner", "-loglevel", "error", "-i"])
        .arg(media_mp4);
    for (srt_path, _) in &valid_tracks {
        cmd.args(["-sub_charenc", "UTF-8", "-i"]).arg(srt_path);
    }
    cmd.args(["-map", "0:v", "-map", "0:a?"]);
    for idx in 0..valid_tracks.len() {
        cmd.arg("-map").arg(format!("{}:0", idx + 1));
    }
    cmd.args(["-c", "copy", "-c:s", "mov_text", "-movflags", "+faststart"]);
    for (idx, (_, lang)) in valid_tracks.iter().enumerate() {
        let iso = iso639_2_lang(lang);
        cmd.arg(format!("-metadata:s:s:{idx}"))
            .arg(format!("language={iso}"));
        cmd.arg(format!("-metadata:s:s:{idx}"))
            .arg(format!("title={}", lang.to_uppercase()));
        if idx == 0 {
            cmd.arg("-disposition:s:0").arg("default");
        }
    }
    cmd.arg(&tmp_out)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());

    let (mut child, tree) = crate::proc::spawn_tree(cmd).map_err(|e| e.to_string())?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    let ok = loop {
        match child.try_wait() {
            Ok(Some(s)) => break s.success(),
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            _ => {
                let _ = tree.kill();
                let _ = child.wait();
                break false;
            }
        }
    };
    if !ok {
        let _ = fs::remove_file(&tmp_out);
        return Err("ffmpeg soft-sub mux failed".into());
    }
    let muxed_size = fs::metadata(&tmp_out).map(|m| m.len()).unwrap_or(0);
    if muxed_size < orig_size.saturating_mul(9) / 10 {
        let _ = fs::remove_file(&tmp_out);
        return Err("muxed mp4 unexpectedly smaller than source".into());
    }
    if let Err(e) = fs::rename(&tmp_out, media_mp4) {
        let _ = fs::remove_file(&tmp_out);
        return Err(e.to_string());
    }
    Ok(true)
}

/// Maps ISO-639-2/B/T or BCP-47 tags (e.g. from `ffprobe` stream metadata) to concise
/// ISO-639-1 subtitle codes so UI and companion `.srt` naming stay consistent.
pub fn iso639_1_from_tag(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return "sub".into();
    }
    let lower = trimmed.to_ascii_lowercase();
    if lower == "und" || lower == "unknown" || lower == "unk" {
        return "sub".into();
    }
    if lower == "zh-hans" || lower == "chs" {
        return "zh-Hans".into();
    }
    if lower == "zh-hant" || lower == "cht" {
        return "zh-Hant".into();
    }
    let primary = lower.split(['-', '_']).next().unwrap_or("");
    match primary {
        "vi" | "vie" => "vi".into(),
        "en" | "eng" => "en".into(),
        "ja" | "jp" | "jpn" => "ja".into(),
        "ko" | "kor" => "ko".into(),
        "zh" | "chi" | "zho" | "cmn" | "cn" => "zh".into(),
        "th" | "tha" => "th".into(),
        "id" | "ind" => "id".into(),
        "ms" | "may" | "msa" => "ms".into(),
        "fr" | "fre" | "fra" => "fr".into(),
        "de" | "ger" | "deu" => "de".into(),
        "es" | "spa" => "es".into(),
        "pt" | "por" => "pt".into(),
        "ru" | "rus" => "ru".into(),
        "it" | "ita" => "it".into(),
        "ar" | "ara" => "ar".into(),
        "hi" | "hin" => "hi".into(),
        _ if valid_lang(trimmed) => trimmed.to_string(),
        _ => "sub".into(),
    }
}

/// Collects existing `<stem>.<lang>.srt` companion tracks beside `media`, ordering
/// Vietnamese (`vi` / `vi-*`) first so it becomes track 0 (`disposition: default`) in `.mp4`.
pub fn collect_companion_srts_vi_first(media: &Path) -> Vec<(PathBuf, String)> {
    let mut tracks: Vec<(PathBuf, String)> = Vec::new();
    for lang in list(media) {
        if let Some(p) = srt_path(media, &lang) {
            if p.is_file() {
                tracks.push((p, lang));
            }
        }
    }
    tracks.sort_by_key(|(_, lang)| {
        let low = lang.to_ascii_lowercase();
        let is_vi = low == "vi" || low.starts_with("vi-") || low.starts_with("vi_");
        (!is_vi, low)
    });
    tracks.truncate(4);
    tracks
}

fn run_tree_capture(mut cmd: std::process::Command, max_bytes: u64, timeout: std::time::Duration) -> Result<Vec<u8>, String> {
    use std::io::Read;
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    let (mut child, tree) = crate::proc::spawn_tree(cmd).map_err(|e| e.to_string())?;
    let stdout = child.stdout.take().ok_or("failed to open stdout pipe")?;
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout.take(max_bytes.saturating_add(1)).read_to_end(&mut buf);
        buf
    });
    let deadline = std::time::Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break Ok(s),
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(30));
            }
            _ => {
                let _ = tree.kill();
                let _ = child.wait();
                break Err("sidecar timed out".to_string());
            }
        }
    };
    let out = reader.join().unwrap_or_default();
    let status = status?;
    if !status.success() {
        return Err("sidecar exited with non-zero status".into());
    }
    Ok(out)
}

/// Inspects embedded subtitle streams inside `media` via bundled `ffprobe`, returning
/// `(sub_stream_pos, normalized_lang)` for text-based codecs extractable to SRT.
pub fn probe_embedded_text_subs(ffprobe: &Path, media: &Path) -> Vec<(usize, String)> {
    if !media.is_file() {
        return Vec::new();
    }
    let mut cmd = std::process::Command::new(ffprobe);
    cmd.args([
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=codec_name:stream_tags=language",
        "-of",
        "json",
        "--",
    ])
    .arg(media);
    let Ok(bytes) = run_tree_capture(cmd, 256 * 1024, std::time::Duration::from_secs(10)) else {
        return Vec::new();
    };
    let Ok(val) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
        return Vec::new();
    };
    let Some(streams) = val.get("streams").and_then(|s| s.as_array()) else {
        return Vec::new();
    };
    let mut out: Vec<(usize, String)> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for (sub_pos, stream) in streams.iter().enumerate() {
        let codec = stream
            .get("codec_name")
            .and_then(|c| c.as_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !matches!(
            codec.as_str(),
            "subrip" | "srt" | "ass" | "ssa" | "mov_text" | "webvtt" | "text"
        ) {
            continue;
        }
        let raw_lang = stream
            .get("tags")
            .and_then(|t| t.get("language"))
            .and_then(|l| l.as_str())
            .unwrap_or("");
        let lang = iso639_1_from_tag(raw_lang);
        if valid_lang(&lang) && seen.insert(lang.clone()) {
            out.push((sub_pos, lang));
        }
    }
    out
}

/// Extracts embedded subtitle stream `0:s:<sub_pos>` from `media` as UTF-8 SubRip (`.srt`).
pub fn extract_embedded_srt(ffmpeg: &Path, media: &Path, sub_pos: usize) -> Result<String, String> {
    if !media.is_file() {
        return Err("media file not found".into());
    }
    let mut cmd = std::process::Command::new(ffmpeg);
    cmd.args(["-hide_banner", "-loglevel", "error", "-nostdin", "-i"])
        .arg(media)
        .args(["-map", &format!("0:s:{sub_pos}"), "-f", "srt", "-"]);
    let bytes = run_tree_capture(cmd, MAX_BYTES, std::time::Duration::from_secs(30))?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("embedded subtitle is larger than 2 MB".into());
    }
    let text = String::from_utf8(bytes).map_err(|_| "embedded subtitle is not UTF-8")?;
    let clean = text.strip_prefix('\u{feff}').unwrap_or(&text);
    if clean.contains('\0') || clean.trim().is_empty() {
        return Err("embedded subtitle is empty".into());
    }
    Ok(clean.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir() -> PathBuf {
        let d = std::env::temp_dir().join(format!("vg-subs-{}", std::process::id() as u64 * 1000 + rand_suffix()));
        fs::create_dir_all(&d).unwrap();
        d
    }
    fn rand_suffix() -> u64 {
        use std::sync::atomic::{AtomicU64, Ordering};
        static N: AtomicU64 = AtomicU64::new(0);
        N.fetch_add(1, Ordering::Relaxed)
    }

    #[test]
    fn lists_only_this_files_subtitles() {
        let d = dir();
        let media = d.join("Mèo con.mp4");
        for n in ["Mèo con.mp4", "Mèo con.en.srt", "Mèo con.zh-Hans.srt", "Mèo con.en.vtt", "Khác.vi.srt", "Mèo con.a b.srt"] {
            fs::write(d.join(n), "1\n00:00:01,000 --> 00:00:02,000\nhi\n").unwrap();
        }
        assert_eq!(list(&media), vec!["en", "zh-Hans"]);
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn read_strips_bom_and_refuses_bad_input() {
        let d = dir();
        let media = d.join("v.mp4");
        fs::write(d.join("v.en.srt"), "\u{feff}1\nhi\n").unwrap();
        assert_eq!(read(&media, "en").unwrap(), "1\nhi\n");
        assert!(read(&media, "../en").is_err());
        assert!(read(&media, "fr").is_err());
        fs::write(d.join("v.ja.srt"), [0xff, 0xfe, 0x00]).unwrap();
        assert!(read(&media, "ja").is_err());
        fs::write(d.join("v.ko.srt"), vec![b'a'; (MAX_BYTES + 1) as usize]).unwrap();
        assert!(read(&media, "ko").unwrap_err().contains("2 MB"));
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn write_never_replaces_a_file() {
        let d = dir();
        let media = d.join("v.mp4");
        let p = write(&media, "vi", "1\nxin chào\n").unwrap();
        assert_eq!(p, d.join("v.vi.srt"));
        assert_eq!(fs::read_to_string(&p).unwrap(), "1\nxin chào\n");
        assert!(write(&media, "vi", "2\nkhác\n").unwrap_err().contains("already exists"));
        assert_eq!(fs::read_to_string(&p).unwrap(), "1\nxin chào\n");
        assert!(write(&media, "vi/../x", "1\n").is_err());
        assert!(write(&media, "th", "   ").is_err());
        assert!(write(&media, "th", "a\0b").is_err());
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn converts_vtt_and_writes_companion_files() {
        let vtt = "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:90000,LOCAL:00:00:00.000\n\nSTYLE\n::cue { color: yellow; }\n\ncue-1\n00:01.250 --> 00:03.500 align:middle\n<c.yellow>Xin chào</c>\n\nWEBVTT\n\n00:01.250 --> 00:03.500\nXin chào\n\n00:04.000 --> 00:05.100\nTập 01\n";
        let srt = vtt_to_srt(vtt);
        assert_eq!(srt, "1\n00:00:01,250 --> 00:00:03,500\nXin chào\n\n2\n00:00:04,000 --> 00:00:05,100\nTập 01\n");

        let d = dir();
        let media = d.join("Episode 01 [ab12cd34].mp4");
        let p1 = write_companion(&media, "vi", "srt", &srt).unwrap();
        let p2 = write_companion(&media, "vi", "srt", &srt).unwrap();
        let p3 = write_companion(&media, "ja", "ass", "[Script Info]\nTitle: Test\n").unwrap();
        assert_eq!(p1, d.join("Episode 01 [ab12cd34].vi.srt"));
        assert_eq!(p2, d.join("Episode 01 [ab12cd34].vi-2.srt"));
        assert_eq!(p3, d.join("Episode 01 [ab12cd34].ja.ass"));
        assert!(write_companion(&media, "vi", "exe", &srt).is_err());
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn maps_iso639_2_and_guards_soft_sub_mux() {
        assert_eq!(iso639_2_lang("vi-VN"), "vie");
        assert_eq!(iso639_2_lang("ja"), "jpn");
        assert_eq!(iso639_2_lang("en_US"), "eng");
        assert_eq!(iso639_2_lang("zh-Hans"), "chi");
        assert_eq!(iso639_2_lang("sub"), "und");

        let d = dir();
        let non_mp4 = d.join("movie.mkv");
        fs::write(&non_mp4, b"dummy").unwrap();
        let srt = d.join("movie.vi.srt");
        fs::write(&srt, "1\n00:00:00,000 --> 00:00:01,000\nXin chào\n").unwrap();
        assert_eq!(mux_soft_subs_mp4(Path::new("ffmpeg"), &non_mp4, &[(srt, "vi".into())]).unwrap(), false);
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn maps_iso639_1_and_prioritizes_vi_first() {
        assert_eq!(iso639_1_from_tag("eng"), "en");
        assert_eq!(iso639_1_from_tag("jpn"), "ja");
        assert_eq!(iso639_1_from_tag("vie"), "vi");
        assert_eq!(iso639_1_from_tag("chi"), "zh");
        assert_eq!(iso639_1_from_tag("zho"), "zh");
        assert_eq!(iso639_1_from_tag("und"), "sub");

        let d = dir();
        let media = d.join("anime.mp4");
        fs::write(&media, b"mp4").unwrap();
        for lang in ["ja", "en", "vi"] {
            fs::write(d.join(format!("anime.{lang}.srt")), "1\n00:00:01,000 --> 00:00:02,000\nhi\n").unwrap();
        }
        let ordered: Vec<String> = collect_companion_srts_vi_first(&media).into_iter().map(|(_, l)| l).collect();
        assert_eq!(ordered, vec!["vi", "en", "ja"]);
        fs::remove_dir_all(d).unwrap();
    }
}
