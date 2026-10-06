//! Maps `yt-dlp -J` output to the contract's ProbeResult.
//!
//! The list starts with presets (yt-dlp selectors, robust against format ids
//! changing between probe and download), then two audio-only presets, then
//! the raw formats. Every `id` here is something `start_download` accepts as
//! `formatId` (it passes validate::format_id).

use crate::validate;
use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ProbeFormat {
    pub id: String,
    pub label: String,
    pub height: Option<u32>,
    pub ext: String,
    pub vcodec: Option<String>,
    pub acodec: Option<String>,
    pub fps: Option<f64>,
    pub filesize: Option<u64>,
    pub requires_merge: bool,
    pub audio_only: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ProbeResult {
    pub url: String,
    pub platform: String,
    pub title: String,
    pub thumbnail: Option<String>,
    pub duration: Option<f64>,
    pub uploader: Option<String>,
    pub formats: Vec<ProbeFormat>,
}

/// Preset classes, high to low. A preset is offered when at least one video
/// format's class (`res`) falls in (next lower preset, this preset].
pub const PRESET_HEIGHTS: &[u32] = &[2160, 1440, 1080, 720, 480, 360];

/// Selector for a preset: best video whose height is ≤ `max_h` plus best
/// audio, or the best single file ≤ `max_h` when the site has no separate
/// streams. `max_h` is the tallest real format in that preset's class, so a
/// portrait 1080x1920 video selects with height<=1920.
pub fn preset_selector(max_h: u32) -> String {
    format!("bv*[height<={max_h}]+ba/b[height<={max_h}]")
}

/// Resolution class of a video format: the SHORT side when both sides are
/// known. "1080p" means 1080 lines on the short side; using `height` alone
/// put a portrait TikTok (1080x1920) under "2160p (4K)" (seen on Windows,
/// 2026-10-06).
fn res(v: &Value) -> Option<u32> {
    match (f(v, "width"), f(v, "height")) {
        (Some(w), Some(h)) if w > 0.0 && h > 0.0 => Some(w.min(h) as u32),
        (_, Some(h)) => Some(h as u32),
        _ => None,
    }
}

/// Audio presets. `start_download` with `audioOnly: true` converts with -x;
/// a selector containing `ext=m4a` keeps M4A (stream copy when the source is
/// already AAC), anything else becomes MP3.
pub const AUDIO_M4A_SELECTOR: &str = "ba[ext=m4a]/ba/b";
pub const AUDIO_MP3_SELECTOR: &str = "ba/b";

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).filter(|x| !x.is_empty()).map(String::from)
}

fn f(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64).filter(|x| x.is_finite() && *x >= 0.0)
}

/// yt-dlp uses "none" for an absent stream and null/missing for "unknown".
fn codec(v: &Value, k: &str) -> Option<String> {
    s(v, k)
}

fn has_video(v: &Value) -> bool {
    match codec(v, "vcodec").as_deref() {
        Some("none") => false,
        Some(_) => true,
        // Unknown codec: trust height/width if present.
        None => f(v, "height").is_some() || f(v, "width").is_some(),
    }
}

fn has_audio(v: &Value) -> bool {
    match codec(v, "acodec").as_deref() {
        Some("none") => false,
        Some(_) => true,
        // Unknown: a format with no video info is usually a muxed file.
        None => !matches!(codec(v, "vcodec").as_deref(), Some(c) if c != "none") || f(v, "height").is_none(),
    }
}

fn size(v: &Value) -> Option<u64> {
    f(v, "filesize").or_else(|| f(v, "filesize_approx")).map(|x| x as u64)
}

fn is_junk(v: &Value) -> bool {
    let ext = s(v, "ext").unwrap_or_default();
    let note = s(v, "format_note").unwrap_or_default().to_lowercase();
    ext == "mhtml" || note.contains("storyboard") || (!has_video(v) && !has_audio(v))
}

fn rank(v: &Value) -> (u64, u64) {
    (f(v, "height").unwrap_or(0.0) as u64, (f(v, "tbr").unwrap_or(0.0) * 1000.0) as u64)
}

pub fn map_probe(url: &str, j: &Value) -> ProbeResult {
    let formats: Vec<&Value> = j
        .get("formats")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter(|v| !is_junk(v)).collect())
        .unwrap_or_default();
    // Some extractors return a single file with no `formats` array.
    let single;
    let formats = if formats.is_empty() && j.get("url").is_some() {
        single = j.clone();
        vec![&single]
    } else {
        formats
    };

    let videos: Vec<&Value> = formats.iter().copied().filter(|v| has_video(v)).collect();
    let audios: Vec<&Value> = formats.iter().copied().filter(|v| has_audio(v) && !has_video(v)).collect();
    let best_audio = audios.iter().copied().max_by_key(|v| rank(v));

    let mut out = Vec::new();

    for (i, &h) in PRESET_HEIGHTS.iter().enumerate() {
        let lower = PRESET_HEIGHTS.get(i + 1).copied().unwrap_or(0);
        let in_class: Vec<&Value> = videos
            .iter()
            .copied()
            .filter(|v| res(v).is_some_and(|x| x > lower && x <= h))
            .collect();
        let Some(best) = in_class.iter().copied().max_by_key(|v| rank(v)) else { continue };
        let max_h = in_class.iter().filter_map(|v| f(v, "height")).fold(0.0_f64, f64::max) as u32;
        let needs_merge = !has_audio(best) && best_audio.is_some();
        let filesize = match (size(best), needs_merge) {
            (Some(a), true) => best_audio.and_then(size).map(|b| a + b),
            (a, false) => a,
            (None, true) => None,
        };
        out.push(ProbeFormat {
            id: preset_selector(max_h),
            label: format!("{h}p"),
            height: Some(h),
            ext: if needs_merge { "mp4".into() } else { s(best, "ext").unwrap_or_else(|| "mp4".into()) },
            vcodec: codec(best, "vcodec"),
            acodec: if needs_merge { best_audio.and_then(|a| codec(a, "acodec")) } else { codec(best, "acodec") },
            fps: f(best, "fps"),
            filesize,
            requires_merge: needs_merge,
            audio_only: false,
        });
    }

    // Audio presets whenever something has audio (also muxed-only sites: -x
    // extracts the track from the single file).
    if formats.iter().any(|v| has_audio(v)) {
        let m4a = audios.iter().copied().filter(|v| s(v, "ext").as_deref() == Some("m4a")).max_by_key(|v| rank(v));
        for (sel, label, ext, src) in [
            (AUDIO_M4A_SELECTOR, "Audio only (M4A)", "m4a", m4a.or(best_audio)),
            (AUDIO_MP3_SELECTOR, "Audio only (MP3)", "mp3", best_audio),
        ] {
            out.push(ProbeFormat {
                id: sel.into(),
                label: label.into(),
                height: None,
                ext: ext.into(),
                vcodec: None,
                acodec: src.and_then(|a| codec(a, "acodec")),
                fps: None,
                filesize: if ext == "m4a" { src.and_then(size) } else { None },
                requires_merge: false,
                audio_only: true,
            });
        }
    }

    // Raw formats, best first. A video-only stream is offered as "<id>+ba" so
    // choosing it still gives a file with sound.
    let mut raw: Vec<&Value> = formats.clone();
    raw.sort_by_key(|v| std::cmp::Reverse((has_video(v) as u8, rank(v))));
    for v in raw {
        let Some(fid) = s(v, "format_id") else { continue };
        let video = has_video(v);
        let audio = has_audio(v);
        let merge = video && !audio && best_audio.is_some();
        let id = if merge { format!("{fid}+ba") } else { fid.clone() };
        if validate::format_id(&id).is_err() || out.iter().any(|p| p.id == id) {
            continue;
        }
        let height = f(v, "height").map(|x| x as u32);
        let ext = s(v, "ext").unwrap_or_default();
        let note = s(v, "format_note").or_else(|| s(v, "resolution")).unwrap_or_default();
        let label = match (video, height) {
            (true, Some(h)) => format!("{h}p {ext} {note}").split_whitespace().collect::<Vec<_>>().join(" "),
            (true, None) => format!("{ext} {note}").trim().to_string(),
            (false, _) => {
                let abr = f(v, "abr").map(|a| format!("{}k", a.round() as u64)).unwrap_or_default();
                format!("audio {ext} {abr}").split_whitespace().collect::<Vec<_>>().join(" ")
            }
        };
        out.push(ProbeFormat {
            id,
            label,
            height,
            ext: if merge { "mp4".into() } else { ext },
            vcodec: codec(v, "vcodec"),
            acodec: if merge { best_audio.and_then(|a| codec(a, "acodec")) } else { codec(v, "acodec") },
            fps: f(v, "fps"),
            filesize: if merge { size(v).zip(best_audio.and_then(size)).map(|(a, b)| a + b) } else { size(v) },
            requires_merge: merge,
            audio_only: !video,
        });
    }

    let platform = s(j, "extractor_key")
        .or_else(|| s(j, "extractor"))
        .map(|p| p.to_lowercase())
        .unwrap_or_else(|| "unknown".into());
    ProbeResult {
        url: url.to_string(),
        platform,
        title: s(j, "title").or_else(|| s(j, "id")).unwrap_or_default(),
        thumbnail: s(j, "thumbnail").filter(|t| t.starts_with("https://") || t.starts_with("http://")),
        duration: f(j, "duration"),
        uploader: s(j, "uploader").or_else(|| s(j, "channel")),
        formats: out,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn yt_like() -> Value {
        json!({
            "id": "abc", "title": "T", "extractor_key": "Youtube", "duration": 212.0,
            "uploader": "U", "thumbnail": "https://i.ytimg.com/x.jpg",
            "formats": [
                {"format_id": "sb0", "ext": "mhtml", "format_note": "storyboard", "vcodec": "none", "acodec": "none"},
                {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.5, "filesize": 3_000_000, "tbr": 129.5},
                {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 140.0, "filesize": 3_200_000, "tbr": 140.0},
                {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "fps": 30, "filesize": 9_000_000, "tbr": 500.0},
                {"format_id": "136", "ext": "mp4", "height": 720, "vcodec": "avc1.4d401f", "acodec": "none", "fps": 30, "filesize": 20_000_000, "tbr": 1500.0},
                {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1.640028", "acodec": "none", "fps": 30, "filesize_approx": 40_000_000, "tbr": 3000.0},
                {"format_id": "248", "ext": "webm", "height": 1080, "vcodec": "vp9", "acodec": "none", "fps": 30, "tbr": 2500.0}
            ]
        })
    }

    #[test]
    fn presets_follow_available_heights() {
        let r = map_probe("https://youtu.be/abc", &yt_like());
        let ids: Vec<&str> = r.formats.iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids[0], "bv*[height<=1080]+ba/b[height<=1080]");
        assert_eq!(ids[1], "bv*[height<=720]+ba/b[height<=720]");
        assert_eq!(ids[2], "bv*[height<=360]+ba/b[height<=360]");
        assert!(!ids.contains(&"bv*[height<=2160]+ba/b[height<=2160]"));
        assert!(!ids.contains(&"bv*[height<=480]+ba/b[height<=480]"));
        let p1080 = &r.formats[0];
        assert!(p1080.requires_merge);
        assert_eq!(p1080.filesize, Some(43_200_000)); // 137 (approx) + 251 best audio
        let p360 = &r.formats[2];
        assert!(!p360.requires_merge, "format 18 has audio");
        assert_eq!(r.platform, "youtube");
        assert_eq!(r.duration, Some(212.0));
    }

    #[test]
    fn audio_presets_and_raw_formats() {
        let r = map_probe("u", &yt_like());
        let a: Vec<&ProbeFormat> = r.formats.iter().filter(|f| f.audio_only).collect();
        assert_eq!(a[0].id, AUDIO_M4A_SELECTOR);
        assert_eq!(a[0].filesize, Some(3_000_000));
        assert_eq!(a[1].id, AUDIO_MP3_SELECTOR);
        let raw137 = r.formats.iter().find(|f| f.id == "137+ba").expect("video-only gets +ba");
        assert!(raw137.requires_merge);
        let raw18 = r.formats.iter().find(|f| f.id == "18").unwrap();
        assert!(!raw18.requires_merge);
        assert!(r.formats.iter().all(|f| f.id != "sb0"), "storyboards dropped");
        assert!(r.formats.iter().all(|f| validate::format_id(&f.id).is_ok()));
    }

    #[test]
    fn single_file_site_without_separate_audio() {
        let j = json!({"id": "1", "title": "tt", "extractor_key": "TikTok",
            "formats": [{"format_id": "h264_540p", "ext": "mp4", "height": 1024, "width": 576, "vcodec": "h264", "acodec": "aac"}]});
        let r = map_probe("u", &j);
        // Portrait 576x1024: class 720p (short side), selected by its real height.
        assert_eq!(r.formats[0].id, "bv*[height<=1024]+ba/b[height<=1024]");
        assert_eq!(r.formats[0].height, Some(720));
        assert!(!r.formats[0].requires_merge);
        assert!(r.formats.iter().any(|f| f.id == "h264_540p"));
        assert_eq!(r.platform, "tiktok");
    }

    #[test]
    fn portrait_video_is_classed_by_its_short_side() {
        // Windows run 2026-10-06: a portrait TikTok was offered "2160p (4K)"
        // and "1440p (2K)" because presets used the height (1920) alone.
        let j = json!({"extractor_key": "TikTok", "title": "t",
            "formats": [
                {"format_id": "h264_1080", "ext": "mp4", "width": 1080, "height": 1920, "vcodec": "h264", "acodec": "aac", "tbr": 2000.0},
                {"format_id": "h264_720", "ext": "mp4", "width": 720, "height": 1280, "vcodec": "h264", "acodec": "aac", "tbr": 1200.0}
            ]});
        let r = map_probe("u", &j);
        let presets: Vec<(Option<u32>, &str)> = r.formats.iter()
            .filter(|f| f.id.starts_with("bv*"))
            .map(|f| (f.height, f.id.as_str())).collect();
        assert_eq!(presets, vec![
            (Some(1080), "bv*[height<=1920]+ba/b[height<=1920]"),
            (Some(720), "bv*[height<=1280]+ba/b[height<=1280]"),
        ]);
    }

    #[test]
    fn no_formats_array_uses_top_level() {
        let j = json!({"id": "v", "title": "video", "extractor_key": "Generic", "url": "http://x/v.mp4", "ext": "mp4", "format_id": "mp4"});
        let r = map_probe("u", &j);
        assert!(r.formats.iter().any(|f| f.id == "mp4"));
    }

    #[test]
    fn hostile_format_ids_are_dropped_and_thumbnail_scheme_checked() {
        let j = json!({"title": "x", "thumbnail": "javascript:alert(1)",
            "formats": [{"format_id": "a;calc", "ext": "mp4", "height": 720, "vcodec": "h264", "acodec": "aac"}]});
        let r = map_probe("u", &j);
        assert!(r.formats.iter().all(|f| f.id != "a;calc"));
        assert_eq!(r.thumbnail, None);
    }
}
