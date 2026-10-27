//! 0.21.0 (quality #3): what the finished file really is, measured with the
//! bundled ffprobe — the same fields the server reports as `downloaded_media`
//! (backend format_probe.parse_media_json). Unknown stays None; anything
//! unreadable is no measurement at all, never a guess.

use crate::proc;
use serde::Serialize;
use serde_json::Value;
use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Serialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct Media {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub short_side: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vcodec: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fps: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bitrate_kbps: Option<u32>,
    pub hdr: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub acodec: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub audio_kbps: Option<u32>,
}

const HDR_TRANSFERS: &[&str] = &["smpte2084", "arib-std-b67"];
/// Codec names go to the UI as data; anything odd is dropped, not shown.
fn codec(v: &Value) -> Option<String> {
    let s = v.as_str()?;
    (!s.is_empty() && s.len() <= 32 && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')).then(|| s.to_string())
}

fn num(v: &Value) -> Option<f64> {
    let n = match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }?;
    (n.is_finite() && n > 0.0).then_some(n)
}

/// "30000/1001" → 29.97
fn rate(v: &Value) -> Option<f64> {
    let s = v.as_str()?;
    let (a, b) = s.split_once('/').unwrap_or((s, "1"));
    let (a, b): (f64, f64) = (a.parse().ok()?, b.parse().ok()?);
    let r = a / b;
    (r.is_finite() && r > 0.0 && r < 1000.0).then(|| (r * 100.0).round() / 100.0)
}

/// `ffprobe -show_streams -show_format -of json` of the whole file.
pub fn parse(raw: &[u8], size_bytes: Option<u64>) -> Option<Media> {
    let data: Value = serde_json::from_slice(raw).ok()?;
    let streams = data.get("streams")?.as_array()?;
    let fmt = data.get("format").cloned().unwrap_or(Value::Null);
    let is = |s: &&Value, t: &str| s.get("codec_type").and_then(Value::as_str) == Some(t);
    let v = streams.iter().find(|s| {
        is(s, "video") && s.pointer("/disposition/attached_pic").and_then(Value::as_i64) != Some(1)
    });
    let a = streams.iter().find(|s| is(s, "audio"));
    if v.is_none() && a.is_none() {
        return None;
    }
    let duration = num(&fmt["duration"]).or_else(|| v.or(a).and_then(|s| num(&s["duration"])));
    let size = size_bytes.map(|b| b as f64).or_else(|| num(&fmt["size"]));
    let total = match (size, duration) {
        (Some(s), Some(d)) => Some(s * 8.0 / d),
        _ => num(&fmt["bit_rate"]),
    };
    let mut m = Media {
        bitrate_kbps: total.map(|b| (b / 1000.0).round() as u32),
        acodec: a.and_then(|s| codec(&s["codec_name"])),
        audio_kbps: a.and_then(|s| num(&s["bit_rate"])).map(|b| (b / 1000.0).round() as u32),
        ..Default::default()
    };
    if let Some(v) = v {
        let (w, h) = (num(&v["width"]), num(&v["height"]));
        m.short_side = match (w, h) {
            (Some(w), Some(h)) => Some(w.min(h) as u32),
            _ => None,
        };
        m.vcodec = codec(&v["codec_name"]);
        m.fps = rate(&v["avg_frame_rate"]).or_else(|| rate(&v["r_frame_rate"]));
        let transfer = v["color_transfer"].as_str().unwrap_or("");
        let mastering = v["side_data_list"].as_array().is_some_and(|l| {
            l.iter().any(|d| d["side_data_type"].as_str().is_some_and(|t| t.contains("Mastering display")))
        });
        m.hdr = HDR_TRANSFERS.contains(&transfer) || mastering;
    }
    Some(m)
}

/// Runs ffprobe on `file` (15 s limit, no console window). None on any failure.
pub fn probe(ffprobe: &Path, file: &Path) -> Option<Media> {
    let mut cmd = Command::new(ffprobe);
    cmd.args(["-v", "error", "-show_streams", "-show_format", "-of", "json", "--"])
        .arg(file)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let (mut child, tree) = proc::spawn_tree(cmd).ok()?;
    let mut stdout = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        // ffprobe JSON for one file is a few KB; cap it so a broken tool cannot fill memory
        let _ = (&mut stdout).take(4 << 20).read_to_end(&mut buf);
        buf
    });
    let deadline = Instant::now() + Duration::from_secs(15);
    let ok = loop {
        match child.try_wait() {
            Ok(Some(s)) => break s.success(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = tree.kill();
                let _ = child.wait();
                break false;
            }
        }
    };
    let raw = reader.join().ok()?;
    if !ok {
        return None;
    }
    parse(&raw, std::fs::metadata(file).ok().map(|m| m.len()))
}

#[cfg(test)]
mod tests {
    use super::*;

    const VP9_4K: &str = r#"{"streams":[
      {"codec_type":"video","codec_name":"vp9","width":3840,"height":2160,"avg_frame_rate":"60/1","color_transfer":"smpte2084"},
      {"codec_type":"audio","codec_name":"opus","bit_rate":"128000"},
      {"codec_type":"video","codec_name":"mjpeg","width":640,"height":360,"disposition":{"attached_pic":1}}],
      "format":{"duration":"10.0","size":"15000000"}}"#;

    #[test]
    fn video_with_cover_and_hdr() {
        let m = parse(VP9_4K.as_bytes(), None).unwrap();
        assert_eq!(m.short_side, Some(2160));
        assert_eq!(m.vcodec.as_deref(), Some("vp9"));
        assert_eq!(m.fps, Some(60.0));
        assert_eq!(m.bitrate_kbps, Some(12000)); // 15 MB * 8 / 10 s
        assert!(m.hdr);
        assert_eq!((m.acodec.as_deref(), m.audio_kbps), (Some("opus"), Some(128)));
    }

    #[test]
    fn portrait_uses_the_short_side_and_ntsc_rate() {
        let raw = r#"{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,
            "avg_frame_rate":"0/0","r_frame_rate":"30000/1001","color_transfer":"bt709"}],"format":{"bit_rate":"4000000"}}"#;
        let m = parse(raw.as_bytes(), None).unwrap();
        assert_eq!((m.short_side, m.fps, m.bitrate_kbps, m.hdr), (Some(1080), Some(29.97), Some(4000), false));
        assert_eq!(m.acodec, None);
    }

    #[test]
    fn audio_only_file() {
        let raw = r#"{"streams":[{"codec_type":"audio","codec_name":"mp3","bit_rate":"320000"},
            {"codec_type":"video","codec_name":"mjpeg","width":500,"height":500,"disposition":{"attached_pic":1}}],
            "format":{"duration":"60","size":"2400000"}}"#;
        let m = parse(raw.as_bytes(), Some(2_400_000)).unwrap();
        assert_eq!((m.vcodec, m.short_side), (None, None));
        assert_eq!((m.acodec.as_deref(), m.audio_kbps, m.bitrate_kbps), (Some("mp3"), Some(320), Some(320)));
    }

    #[test]
    fn unreadable_is_no_measurement() {
        assert_eq!(parse(b"", None), None);
        assert_eq!(parse(b"not json", None), None);
        assert_eq!(parse(br#"{"streams":[]}"#, None), None);
        assert_eq!(parse(br#"{"streams":[{"codec_type":"data"}]}"#, None), None);
    }

    #[test]
    fn odd_codec_names_are_dropped() {
        let raw = r#"{"streams":[{"codec_type":"video","codec_name":"<img src=x>","width":"x","height":720}]}"#;
        let m = parse(raw.as_bytes(), None).unwrap();
        assert_eq!((m.vcodec, m.short_side, m.fps, m.bitrate_kbps), (None, None, None, None));
    }
}
