//! Channels (C1-CONTRACT.md §4): mapping `yt-dlp --flat-playlist -J` output to
//! ChannelListing, channel URL normalisation, validation of what the UI saves,
//! and the SQLite tables `channels` / `channel_seen` (schema v2, history.rs).

use crate::error::Code;
use crate::history::Db;
use crate::validate;
use rusqlite::params;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

pub const DEFAULT_LIMIT: u32 = 200;
pub const MAX_LIMIT: u32 = 5000;
pub const MAX_PENDING: usize = 500;
pub const MAX_SEEN_PER_CALL: usize = 5000;
pub const INTERVALS: &[u32] = &[1, 3, 6, 12, 24];
pub const NOTIFY_TITLE_MAX: usize = 120;
pub const NOTIFY_BODY_MAX: usize = 500;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChannelVideo {
    pub id: String,
    pub url: String,
    pub title: String,
    pub duration: Option<f64>,
    /// YYYYMMDD
    pub upload_date: Option<String>,
    pub thumbnail: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChannelListing {
    pub channel_id: String,
    pub url: String,
    pub title: String,
    pub platform: String,
    pub uploader: Option<String>,
    pub thumbnail: Option<String>,
    pub videos: Vec<ChannelVideo>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Channel {
    pub id: String,
    pub url: String,
    pub title: String,
    pub platform: String,
    pub thumbnail: Option<String>,
    pub mode: String,
    pub quality: String,
    pub out_dir: String,
    pub check_every_hours: u32,
    pub enabled: bool,
    pub last_checked_at: Option<String>,
    pub last_error: Option<String>,
    pub pending_new: Vec<ChannelVideo>,
    pub created_at: String,
}

// ---------------------------------------------------------------- limits & ids

pub fn clamp_limit(limit: Option<u32>) -> u32 {
    limit.unwrap_or(DEFAULT_LIMIT).clamp(1, MAX_LIMIT)
}

/// Channel and video ids are DB keys and travel back to the UI; keep them to
/// a boring alphabet (YouTube `UC…`/`@handle`, TikTok `MS4w…`/numeric ids).
pub fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 128 && id.chars().all(|c| c.is_ascii_alphanumeric() || "-_.:@".contains(c))
}

fn hash_id(s: &str) -> String {
    let d = Sha256::digest(s.as_bytes());
    let hex: String = d.iter().take(8).map(|b| format!("{b:02x}")).collect();
    format!("h{hex}")
}

/// Cuts to at most `max` characters (not bytes, so Vietnamese text is never
/// split inside a code point).
pub fn cap_chars(s: &str, max: usize) -> String {
    match s.char_indices().nth(max) {
        Some((i, _)) => s[..i].to_string(),
        None => s.to_string(),
    }
}

// ---------------------------------------------------------------- URL

const YT_HOSTS: &[&str] = &["youtube.com", "www.youtube.com", "m.youtube.com"];

/// Validates an http(s) URL and, for a YouTube channel ROOT
/// (`/@handle`, `/channel/ID`, `/c/name`, `/user/name`, optional trailing
/// slash, no tab), appends `/videos`: the root lists the channel's tabs as
/// nested playlists, not videos.
pub fn normalize_url(raw: &str) -> Result<String, String> {
    let s = validate::url(raw)?;
    let mut u = url::Url::parse(&s).map_err(|_| "not a valid URL".to_string())?;
    let host = u.host_str().unwrap_or("").to_ascii_lowercase();
    if YT_HOSTS.contains(&host.as_str()) {
        let segs: Vec<String> = u
            .path_segments()
            .map(|it| it.filter(|x| !x.is_empty()).map(String::from).collect())
            .unwrap_or_default();
        let root = match segs.as_slice() {
            [h] if h.starts_with('@') && h.len() > 1 => true,
            [kind, _] if matches!(kind.as_str(), "channel" | "c" | "user") => true,
            _ => false,
        };
        if root {
            let path = format!("/{}/videos", segs.join("/"));
            u.set_path(&path);
        }
    }
    Ok(u.to_string())
}

// ---------------------------------------------------------------- mapping

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::trim).filter(|x| !x.is_empty()).map(String::from)
}

fn http_url(x: Option<String>) -> Option<String> {
    x.filter(|u| u.len() <= 4096 && (u.starts_with("https://") || u.starts_with("http://")))
        .filter(|u| validate::url(u).is_ok())
}

fn https_only(x: Option<String>) -> Option<String> {
    x.filter(|u| u.len() <= 4096 && u.starts_with("https://"))
}

/// Last https thumbnail in `thumbnails` (yt-dlp sorts them worst → best),
/// else `thumbnail`.
fn thumb(v: &Value) -> Option<String> {
    v.get("thumbnails")
        .and_then(Value::as_array)
        .and_then(|a| a.iter().rev().find_map(|t| https_only(s(t, "url"))))
        .or_else(|| https_only(s(v, "thumbnail")))
}

/// Days since 1970-01-01 → (y, m, d), proleptic Gregorian (H. Hinnant).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

pub fn ymd_from_timestamp(ts: f64) -> Option<String> {
    if !ts.is_finite() || !(0.0..=32_503_680_000.0).contains(&ts) {
        return None;
    }
    let (y, m, d) = civil_from_days((ts as i64).div_euclid(86_400));
    Some(format!("{y:04}{m:02}{d:02}"))
}

fn upload_date(v: &Value) -> Option<String> {
    s(v, "upload_date")
        .filter(|d| d.len() == 8 && d.bytes().all(|b| b.is_ascii_digit()))
        .or_else(|| v.get("timestamp").and_then(Value::as_f64).and_then(ymd_from_timestamp))
}

fn video_url(e: &Value) -> Option<String> {
    let ie = s(e, "ie_key").unwrap_or_default();
    let id = s(e, "id");
    http_url(s(e, "url"))
        .or_else(|| http_url(s(e, "webpage_url")))
        .or_else(|| match (ie.as_str(), id) {
            ("Youtube", Some(id)) if valid_id(&id) => Some(format!("https://www.youtube.com/watch?v={id}")),
            _ => None,
        })
}

/// One flat entry → ChannelVideo, or None for things that are not a video
/// (null entries, nested playlists/tabs, entries without any usable URL).
pub fn map_entry(e: &Value) -> Option<ChannelVideo> {
    if !e.is_object() {
        return None;
    }
    if s(e, "_type").as_deref() == Some("playlist") || s(e, "ie_key").is_some_and(|k| k.ends_with("Tab")) {
        return None;
    }
    let url = video_url(e)?;
    let id = s(e, "id").filter(|i| valid_id(i)).unwrap_or_else(|| hash_id(&url));
    let title = cap_chars(&s(e, "title").unwrap_or_else(|| id.clone()), 500);
    Some(ChannelVideo {
        id,
        url,
        title,
        duration: e.get("duration").and_then(Value::as_f64).filter(|d| d.is_finite() && *d >= 0.0),
        upload_date: upload_date(e),
        thumbnail: thumb(e),
    })
}

/// " - Videos" etc. that yt-dlp's YouTube tab extractor adds to the title.
fn strip_tab_suffix(t: &str) -> &str {
    for suf in [" - Videos", " - Shorts", " - Live", " - Streams"] {
        if let Some(x) = t.strip_suffix(suf) {
            return x;
        }
    }
    t
}

/// Why a parsed `-J` document is not a listing.
#[derive(Debug, PartialEq)]
pub enum MapError {
    /// Not a playlist/channel (yt-dlp resolved a single video).
    NotAPlaylist,
    /// A playlist object without a single usable entry; the caller decides
    /// from yt-dlp's exit status / stderr what that means.
    Empty,
}

/// `url` is the normalised URL that was fetched, `limit` the --playlist-end.
pub fn map_listing(url: &str, j: &Value, limit: u32) -> Result<ChannelListing, MapError> {
    let entries = match (s(j, "_type").as_deref(), j.get("entries").and_then(Value::as_array)) {
        (Some("playlist") | Some("multi_video"), Some(a)) => a,
        _ => return Err(MapError::NotAPlaylist),
    };
    let mut seen = std::collections::HashSet::new();
    let videos: Vec<ChannelVideo> = entries
        .iter()
        .filter_map(map_entry)
        .filter(|v| seen.insert(v.id.clone()))
        .take(limit as usize)
        .collect();
    if videos.is_empty() {
        return Err(MapError::Empty);
    }
    let platform = entries
        .iter()
        .find_map(|e| s(e, "ie_key"))
        .or_else(|| s(j, "extractor_key"))
        .map(|p| cap_chars(&p.to_lowercase(), 64))
        .unwrap_or_else(|| "unknown".into());
    let channel_id = s(j, "id")
        .filter(|i| valid_id(i))
        .or_else(|| s(j, "channel_id").filter(|i| valid_id(i)))
        .unwrap_or_else(|| hash_id(url));
    let uploader = s(j, "uploader").or_else(|| s(j, "channel")).map(|u| cap_chars(&u, 500));
    let title = s(j, "title")
        .map(|t| strip_tab_suffix(&t).trim().to_string())
        .filter(|t| !t.is_empty())
        .or_else(|| uploader.clone())
        .unwrap_or_else(|| channel_id.clone());
    Ok(ChannelListing {
        channel_id,
        url: url.to_string(),
        title: cap_chars(&title, 500),
        platform,
        uploader,
        thumbnail: thumb(j),
        truncated: entries.len() as u64 >= limit as u64,
        videos,
    })
}

/// Error code for a fetch that produced no listing.
pub fn failure_code(map_err: Option<&MapError>, exit_ok: bool, stderr: &[String]) -> Code {
    match map_err {
        Some(MapError::NotAPlaylist) if exit_ok => Code::Unsupported,
        Some(MapError::Empty) if exit_ok => Code::NotFound,
        _ => crate::error::classify(stderr),
    }
}

// ---------------------------------------------------------------- validation

fn check_len(v: &str, max: usize, what: &str) -> Result<(), String> {
    if v.len() > max { Err(format!("{what} is too long")) } else { Ok(()) }
}

pub fn validate_video(v: &ChannelVideo) -> Result<(), String> {
    if !valid_id(&v.id) {
        return Err("invalid video id".into());
    }
    check_len(&v.url, 4096, "video url")?;
    validate::url(&v.url).map_err(|e| format!("video url: {e}"))?;
    check_len(&v.title, 2048, "video title")?;
    if let Some(d) = &v.upload_date {
        if d.len() != 8 || !d.bytes().all(|b| b.is_ascii_digit()) {
            return Err("uploadDate must be YYYYMMDD".into());
        }
    }
    if let Some(t) = &v.thumbnail {
        check_len(t, 4096, "thumbnail")?;
        validate::url(t).map_err(|e| format!("thumbnail: {e}"))?;
    }
    if v.duration.is_some_and(|d| !d.is_finite() || d < 0.0) {
        return Err("invalid duration".into());
    }
    Ok(())
}

impl Channel {
    pub fn validate(&self) -> Result<(), String> {
        if !valid_id(&self.id) {
            return Err("invalid channel id".into());
        }
        check_len(&self.url, 4096, "url")?;
        validate::url(&self.url)?;
        check_len(&self.title, 2048, "title")?;
        check_len(&self.platform, 64, "platform")?;
        if let Some(t) = &self.thumbnail {
            check_len(t, 4096, "thumbnail")?;
            validate::url(t).map_err(|e| format!("thumbnail: {e}"))?;
        }
        if !matches!(self.mode.as_str(), "download" | "notify") {
            return Err("mode must be download or notify".into());
        }
        if self.quality != "best" {
            validate::format_id(&self.quality).map_err(|_| "invalid quality".to_string())?;
        }
        if self.out_dir.trim().is_empty() || !std::path::Path::new(&self.out_dir).is_absolute() {
            return Err("outDir must be an absolute path".into());
        }
        check_len(&self.out_dir, 4096, "outDir")?;
        if !INTERVALS.contains(&self.check_every_hours) {
            return Err("checkEveryHours must be 1, 3, 6, 12 or 24".into());
        }
        check_len(&self.created_at, 64, "createdAt")?;
        if let Some(x) = &self.last_checked_at {
            check_len(x, 64, "lastCheckedAt")?;
        }
        if let Some(x) = &self.last_error {
            check_len(x, 2048, "lastError")?;
        }
        if self.pending_new.len() > MAX_PENDING {
            return Err(format!("pendingNew holds at most {MAX_PENDING} videos"));
        }
        self.pending_new.iter().try_for_each(validate_video)
    }
}

pub fn validate_seen_ids(channel_id: &str, ids: &[String]) -> Result<(), String> {
    if !valid_id(channel_id) {
        return Err("invalid channel id".into());
    }
    if ids.len() > MAX_SEEN_PER_CALL {
        return Err("too many video ids".into());
    }
    if ids.iter().any(|i| !valid_id(i)) {
        return Err("invalid video id".into());
    }
    Ok(())
}

// ---------------------------------------------------------------- DB

fn row_to_channel(r: &rusqlite::Row<'_>) -> rusqlite::Result<Channel> {
    let pending: String = r.get(12)?;
    Ok(Channel {
        id: r.get(0)?,
        url: r.get(1)?,
        title: r.get(2)?,
        platform: r.get(3)?,
        thumbnail: r.get(4)?,
        mode: r.get(5)?,
        quality: r.get(6)?,
        out_dir: r.get(7)?,
        check_every_hours: r.get::<_, i64>(8)?.clamp(1, 24) as u32,
        enabled: r.get::<_, i64>(9)? != 0,
        last_checked_at: r.get(10)?,
        last_error: r.get(11)?,
        // A corrupt column must not hide the channel; it just loses pending.
        pending_new: serde_json::from_str(&pending).unwrap_or_default(),
        created_at: r.get(13)?,
    })
}

impl Db {
    pub fn channel_upsert(&self, ch: &Channel) -> rusqlite::Result<()> {
        let pending = serde_json::to_string(&ch.pending_new).unwrap_or_else(|_| "[]".into());
        self.c().execute(
            "INSERT INTO channels (id,url,title,platform,thumbnail,mode,quality,out_dir,check_every_hours,enabled,
               last_checked_at,last_error,pending_new,created_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14)
             ON CONFLICT(id) DO UPDATE SET url=excluded.url, title=excluded.title, platform=excluded.platform,
               thumbnail=excluded.thumbnail, mode=excluded.mode, quality=excluded.quality, out_dir=excluded.out_dir,
               check_every_hours=excluded.check_every_hours, enabled=excluded.enabled,
               last_checked_at=excluded.last_checked_at, last_error=excluded.last_error,
               pending_new=excluded.pending_new, created_at=excluded.created_at",
            params![
                ch.id,
                ch.url,
                ch.title,
                ch.platform,
                ch.thumbnail,
                ch.mode,
                ch.quality,
                ch.out_dir,
                ch.check_every_hours as i64,
                ch.enabled as i64,
                ch.last_checked_at,
                ch.last_error,
                pending,
                ch.created_at
            ],
        )?;
        Ok(())
    }

    pub fn channel_list(&self) -> rusqlite::Result<Vec<Channel>> {
        let conn = self.c();
        let mut st = conn.prepare(
            "SELECT id,url,title,platform,thumbnail,mode,quality,out_dir,check_every_hours,enabled,
                    last_checked_at,last_error,pending_new,created_at
             FROM channels ORDER BY created_at ASC, id ASC",
        )?;
        let v = st.query_map([], row_to_channel)?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(v)
    }

    /// Removes the channel and its seen list (one transaction). Files are
    /// never touched.
    pub fn channel_delete(&self, id: &str) -> rusqlite::Result<()> {
        let mut conn = self.c();
        let tx = conn.transaction()?;
        tx.execute("DELETE FROM channel_seen WHERE channel_id = ?1", params![id])?;
        tx.execute("DELETE FROM channels WHERE id = ?1", params![id])?;
        tx.commit()
    }

    /// Idempotent: a video already marked stays marked once.
    pub fn channel_seen_add(&self, channel_id: &str, ids: &[String]) -> rusqlite::Result<()> {
        let mut conn = self.c();
        let tx = conn.transaction()?;
        {
            let mut st = tx.prepare("INSERT OR IGNORE INTO channel_seen (channel_id, video_id) VALUES (?1, ?2)")?;
            for id in ids {
                st.execute(params![channel_id, id])?;
            }
        }
        tx.commit()
    }

    pub fn channel_seen_list(&self, channel_id: &str) -> rusqlite::Result<Vec<String>> {
        let conn = self.c();
        let mut st = conn.prepare("SELECT video_id FROM channel_seen WHERE channel_id = ?1 ORDER BY rowid")?;
        let v = st.query_map(params![channel_id], |r| r.get(0))?.collect::<rusqlite::Result<Vec<String>>>()?;
        Ok(v)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn youtube_roots_get_videos_tab() {
        for (i, o) in [
            ("https://www.youtube.com/@YouTube", "https://www.youtube.com/@YouTube/videos"),
            ("https://www.youtube.com/@YouTube/", "https://www.youtube.com/@YouTube/videos"),
            ("https://youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ", "https://youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ/videos"),
            ("https://m.youtube.com/c/Foo", "https://m.youtube.com/c/Foo/videos"),
            ("https://www.youtube.com/user/Foo/", "https://www.youtube.com/user/Foo/videos"),
            // Already a tab, a playlist, a video, another site: unchanged.
            ("https://www.youtube.com/@YouTube/videos", "https://www.youtube.com/@YouTube/videos"),
            ("https://www.youtube.com/@YouTube/shorts", "https://www.youtube.com/@YouTube/shorts"),
            ("https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/playlist?list=PL123"),
            ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
            ("https://www.tiktok.com/@tiktok", "https://www.tiktok.com/@tiktok"),
            ("https://notyoutube.com/@x", "https://notyoutube.com/@x"),
        ] {
            assert_eq!(normalize_url(i).unwrap(), o, "{i}");
        }
        assert!(normalize_url("file:///C:/x").is_err());
        assert!(normalize_url("--exec calc").is_err());
    }

    #[test]
    fn timestamp_to_ymd() {
        assert_eq!(ymd_from_timestamp(0.0).as_deref(), Some("19700101"));
        assert_eq!(ymd_from_timestamp(1571246252.0).as_deref(), Some("20191016"));
        assert_eq!(ymd_from_timestamp(1790812800.0).as_deref(), Some("20261001"));
        assert_eq!(ymd_from_timestamp(951782400.0).as_deref(), Some("20000229"));
        assert_eq!(ymd_from_timestamp(-5.0), None);
    }

    #[test]
    fn single_video_and_empty() {
        let v = json!({"_type": "video", "id": "x", "title": "t"});
        assert_eq!(map_listing("https://a.b/", &v, 10), Err(MapError::NotAPlaylist));
        let v = json!({"id": "x", "title": "t", "formats": []});
        assert_eq!(map_listing("https://a.b/", &v, 10), Err(MapError::NotAPlaylist));
        let v = json!({"_type": "playlist", "id": "x", "entries": [null]});
        assert_eq!(map_listing("https://a.b/", &v, 10), Err(MapError::Empty));
    }

    fn channel() -> Channel {
        Channel {
            id: "UCBR8-60-B28hp2BmDPdntcQ".into(),
            url: "https://www.youtube.com/@YouTube/videos".into(),
            title: "YouTube".into(),
            platform: "youtube".into(),
            thumbnail: Some("https://yt3.googleusercontent.com/x=s0".into()),
            mode: "notify".into(),
            quality: "best".into(),
            out_dir: if cfg!(windows) { "C:\\Users\\u\\Downloads\\VidGrab\\YouTube".into() } else { "/home/u/VidGrab/YouTube".into() },
            check_every_hours: 6,
            enabled: true,
            last_checked_at: None,
            last_error: None,
            pending_new: vec![video("pRrmQUm6Zvg")],
            created_at: "2026-10-06T09:00:00Z".into(),
        }
    }

    fn video(id: &str) -> ChannelVideo {
        ChannelVideo {
            id: id.into(),
            url: format!("https://www.youtube.com/watch?v={id}"),
            title: "Turning the YouTube Logo Into a Monster".into(),
            duration: Some(606.0),
            upload_date: Some("20261001".into()),
            thumbnail: Some("https://i.ytimg.com/vi/x/hq720.jpg".into()),
        }
    }

    #[test]
    fn channel_validation_rejects_bad_input() {
        assert!(channel().validate().is_ok());
        let mut q = channel();
        q.quality = "bv*[height<=1080]+ba/b[height<=1080]".into();
        assert!(q.validate().is_ok());

        let bad: Vec<(&str, Box<dyn Fn(&mut Channel)>)> = vec![
            ("id", Box::new(|c| c.id = "../x".into())),
            ("empty id", Box::new(|c| c.id = String::new())),
            ("url scheme", Box::new(|c| c.url = "file:///C:/Windows".into())),
            ("url option", Box::new(|c| c.url = "--exec calc".into())),
            ("mode", Box::new(|c| c.mode = "delete".into())),
            ("interval", Box::new(|c| c.check_every_hours = 2)),
            ("interval 0", Box::new(|c| c.check_every_hours = 0)),
            ("outDir empty", Box::new(|c| c.out_dir = "  ".into())),
            ("outDir relative", Box::new(|c| c.out_dir = "Downloads\\x".into())),
            ("title", Box::new(|c| c.title = "x".repeat(3000))),
            ("quality", Box::new(|c| c.quality = "best; calc".into())),
            ("thumbnail", Box::new(|c| c.thumbnail = Some("javascript:alert(1)".into()))),
            ("pending too many", Box::new(|c| c.pending_new = (0..501).map(|i| video(&format!("v{i}"))).collect())),
            ("pending bad url", Box::new(|c| c.pending_new[0].url = "file:///x".into())),
            ("pending bad date", Box::new(|c| c.pending_new[0].upload_date = Some("2026-10-01".into()))),
        ];
        for (what, f) in bad {
            let mut c = channel();
            f(&mut c);
            assert!(c.validate().is_err(), "{what} should be rejected");
        }
        let mut ok = channel();
        ok.pending_new = (0..500).map(|i| video(&format!("v{i}"))).collect();
        assert!(ok.validate().is_ok());

        assert!(validate_seen_ids("UC1", &["a".into(), "b-_".into()]).is_ok());
        assert!(validate_seen_ids("UC1", &["a b".into()]).is_err());
        assert!(validate_seen_ids("", &[]).is_err());
        assert!(validate_seen_ids("UC1", &vec!["a".to_string(); MAX_SEEN_PER_CALL + 1]).is_err());
    }

    #[test]
    fn db_round_trip_seen_dedupe_and_delete() {
        let db = Db::open_in_memory().unwrap();
        let mut c = channel();
        db.channel_upsert(&c).unwrap();
        assert_eq!(db.channel_list().unwrap(), vec![c.clone()]);
        // Upsert replaces, pendingNew survives as JSON.
        c.mode = "download".into();
        c.pending_new.push(video("wGA27zJEnaU"));
        c.last_error = Some("network".into());
        db.channel_upsert(&c).unwrap();
        assert_eq!(db.channel_list().unwrap(), vec![c.clone()]);

        db.channel_seen_add(&c.id, &["a".into(), "b".into(), "a".into()]).unwrap();
        db.channel_seen_add(&c.id, &["b".into(), "c".into()]).unwrap();
        db.channel_seen_add("other", &["a".into()]).unwrap();
        assert_eq!(db.channel_seen_list(&c.id).unwrap(), ["a", "b", "c"]);
        assert_eq!(db.channel_seen_list("other").unwrap(), ["a"]);

        db.channel_delete(&c.id).unwrap();
        assert!(db.channel_list().unwrap().is_empty());
        assert!(db.channel_seen_list(&c.id).unwrap().is_empty());
        assert_eq!(db.channel_seen_list("other").unwrap(), ["a"]);
    }

    #[test]
    fn caps_chars_not_bytes() {
        assert_eq!(cap_chars("Tiếng Việt", 4), "Tiến");
        assert_eq!(cap_chars("ab", 5), "ab");
    }
}
