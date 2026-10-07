//! Fixed yt-dlp argument vectors. Nothing from the webview is ever parsed as
//! an option: the URL comes after `--`, the format selector is its own argv
//! item after `-f` and passed validate::format_id, the folder is its own item
//! after `-P`. Config files and plugin dirs are ignored so a yt-dlp.conf
//! dropped next to the exe or in the profile cannot add `--exec`.

use crate::progress::{DL_MARK, FILE_MARK, PP_MARK};
use std::ffi::OsString;
use std::path::Path;

pub struct Tools<'a> {
    pub ffmpeg: &'a Path,
    pub deno: &'a Path,
}

/// Output name: title capped at 150 bytes + id. paths.rs relies on names
/// ending in `[<id>].<ext>` to recognise per-format intermediates.
pub const OUTPUT_TEMPLATE: &str = "%(title).150B [%(id)s].%(ext)s";

/// What `-x` converts to. m4a when the chosen selector asks for m4a (stream
/// copy when the source is already AAC, which YouTube's 140 is), otherwise
/// mp3 (re-encoded with libmp3lame, VBR quality 0).
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum AudioFormat {
    M4a,
    Mp3,
}

pub fn audio_format_for(format_id: Option<&str>) -> AudioFormat {
    match format_id {
        Some(f) if f.contains("ext=m4a") => AudioFormat::M4a,
        _ => AudioFormat::Mp3,
    }
}

fn common(tools: &Tools<'_>) -> Vec<OsString> {
    let mut a: Vec<OsString> = vec![
        "--ignore-config".into(),
        "--no-plugin-dirs".into(),
        "--no-update".into(),
        "--color".into(),
        "never".into(),
        // Point yt-dlp at OUR verified ffmpeg/ffprobe (same folder), so it
        // never runs one found on PATH, even for its version check.
        "--ffmpeg-location".into(),
        tools.ffmpeg.as_os_str().to_owned(),
        "--no-js-runtimes".into(),
        "--js-runtimes".into(),
    ];
    let mut deno = OsString::from("deno:");
    deno.push(tools.deno.as_os_str());
    a.push(deno);
    a
}

/// `--cookies <file>` and, only then, the slow-down flags (PLAN-32D §3.3: a
/// signed-in account that fetches in bursts gets rate limited or checked).
/// The path is its own argv item, so it is never parsed as an option.
fn cookie_args(a: &mut Vec<OsString>, cookies: Option<&Path>) {
    if let Some(p) = cookies {
        a.push("--cookies".into());
        a.push(p.as_os_str().to_owned());
        a.extend(["--sleep-requests", "1", "--sleep-interval", "2", "--max-sleep-interval", "5"].map(OsString::from));
    }
}

pub fn probe_args(tools: &Tools<'_>, url: &str, cookies: Option<&Path>) -> Vec<OsString> {
    let mut a = common(tools);
    a.extend(["-J", "--no-playlist"].map(OsString::from));
    cookie_args(&mut a, cookies);
    a.push("--".into());
    a.push(url.into());
    a
}

/// Channel / playlist listing (C1 §4). `--flat-playlist` lists entries
/// without resolving each video; `youtubetab:approximate_date` makes the
/// YouTube tab extractor fill `timestamp` from "3 weeks ago" so the UI can
/// filter by date (ignored by other extractors).
pub fn channel_fetch_args(tools: &Tools<'_>, url: &str, limit: u32, cookies: Option<&Path>) -> Vec<OsString> {
    let mut a = common(tools);
    a.extend(["--flat-playlist", "-J", "--playlist-end"].map(OsString::from));
    a.push(limit.to_string().into());
    a.extend(["--extractor-args", "youtubetab:approximate_date"].map(OsString::from));
    cookie_args(&mut a, cookies);
    a.push("--".into());
    a.push(url.into());
    a
}

/// Name for a download whose URL says nothing useful (Douyin direct CDN link):
/// the caller supplies the title and an id, both already validated.
pub struct OutName<'a> {
    pub title: &'a str,
    pub id: &'a str,
}

/// `<title, 150 bytes max> [<id>].%(ext)s`. Everything Windows rejects in a
/// file name, and `%` (yt-dlp template syntax), becomes a space. The id is a
/// literal (the generic extractor's own id would be the CDN path and could
/// collide between videos). None when nothing usable is left.
pub fn named_template(n: &OutName<'_>) -> Option<String> {
    let id_ok = !n.id.is_empty() && n.id.len() <= 64 && n.id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
    if !id_ok {
        return None;
    }
    let clean: String = n
        .title
        .chars()
        .map(|c| if c.is_control() || "<>:\"/\\|?*%".contains(c) { ' ' } else { c })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let mut cut = String::new();
    for c in clean.chars() {
        if cut.len() + c.len_utf8() > 150 {
            break;
        }
        cut.push(c);
    }
    let cut = cut.trim_end_matches(['.', ' ']);
    if cut.is_empty() {
        return None;
    }
    Some(format!("{cut} [{}].%(ext)s", n.id))
}

#[allow(clippy::too_many_arguments)]
pub fn download_args(
    tools: &Tools<'_>,
    url: &str,
    out_dir: &Path,
    format_id: Option<&str>,
    audio_only: bool,
    headers: &[(String, String)],
    name: Option<&OutName<'_>>,
    cookies: Option<&Path>,
) -> Vec<OsString> {
    let mut a = common(tools);
    a.extend(
        [
            "--no-playlist",
            "--newline",
            // --print implies --quiet; --progress keeps the progress lines.
            "-q",
            "--progress",
            "--progress-template",
            &format!("download:{DL_MARK}%(progress)j"),
            "--progress-template",
            &format!("postprocess:{PP_MARK}%(progress)j"),
            "--print",
            &format!("after_move:{FILE_MARK}%(filepath)j"),
            "--no-mtime",
            "--merge-output-format",
            "mp4/mkv",
            "-o",
        ]
        .map(OsString::from),
    );
    a.push(name.and_then(named_template).unwrap_or_else(|| OUTPUT_TEMPLATE.to_string()).into());
    a.push("-P".into());
    a.push(out_dir.as_os_str().to_owned());
    match (format_id, audio_only) {
        (Some(f), _) => {
            a.push("-f".into());
            a.push(f.into());
        }
        (None, true) => {
            a.push("-f".into());
            a.push("ba/b".into());
        }
        (None, false) => {}
    }
    if audio_only {
        a.push("-x".into());
        a.push("--audio-format".into());
        match audio_format_for(format_id) {
            AudioFormat::M4a => a.push("m4a".into()),
            AudioFormat::Mp3 => {
                a.push("mp3".into());
                a.push("--audio-quality".into());
                a.push("0".into());
            }
        }
    }
    // Each header is its own argv item (`Name:Value`); validate::download_header
    // already limited names to Referer / User-Agent and refused control chars.
    for (k, v) in headers {
        a.push("--add-header".into());
        a.push(format!("{k}:{v}").into());
    }
    cookie_args(&mut a, cookies);
    a.push("--".into());
    a.push(url.into());
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tools() -> (std::path::PathBuf, std::path::PathBuf) {
        ("/app/ffmpeg".into(), "/app/deno".into())
    }

    fn strs(v: &[OsString]) -> Vec<String> {
        v.iter().map(|s| s.to_string_lossy().into_owned()).collect()
    }

    #[test]
    fn url_is_last_after_double_dash_and_no_config() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        for args in [
            probe_args(&t, "https://x/--exec=calc", None),
            channel_fetch_args(&t, "https://x/--exec=calc", 200, None),
            download_args(&t, "https://x/--exec=calc", Path::new("/out"), Some("137+ba"), false, &[], None, None),
            download_args(
                &t,
                "https://x/--exec=calc",
                Path::new("/out"),
                None,
                false,
                &[("Referer".into(), "https://www.douyin.com/".into()), ("User-Agent".into(), "UA/1.0".into())],
                Some(&OutName { title: "a", id: "1" }),
                None,
            ),
            probe_args(&t, "https://x/--exec=calc", Some(Path::new("/tmp/ck-j.txt"))),
            channel_fetch_args(&t, "https://x/--exec=calc", 50, Some(Path::new("/tmp/ck-j.txt"))),
            download_args(&t, "https://x/--exec=calc", Path::new("/out"), None, true, &[], None, Some(Path::new("/tmp/ck-j.txt"))),
        ] {
            let s = strs(&args);
            assert_eq!(s[s.len() - 2], "--");
            assert_eq!(s[s.len() - 1], "https://x/--exec=calc");
            assert!(s.contains(&"--ignore-config".into()));
            assert!(s.contains(&"--no-plugin-dirs".into()));
            assert!(s.contains(&"deno:/app/deno".into()));
            assert!(!s.iter().any(|a| a.starts_with("--exec")), "{s:?}");
        }
    }

    #[test]
    fn channel_fetch_flags() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        let s = strs(&channel_fetch_args(&t, "u", 50, None));
        assert!(s.contains(&"--flat-playlist".into()));
        assert!(s.contains(&"-J".into()));
        assert!(s.windows(2).any(|w| w[0] == "--playlist-end" && w[1] == "50"));
        assert!(!s.contains(&"--no-playlist".into()));
    }

    #[test]
    fn download_progress_and_audio_flags() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        let s = strs(&download_args(&t, "u", Path::new("/out"), None, false, &[], None, None));
        assert!(s.contains(&"download:VGDL %(progress)j".into()));
        assert!(s.contains(&"postprocess:VGPP %(progress)j".into()));
        assert!(s.contains(&"after_move:VGFILE %(filepath)j".into()));
        assert!(!s.contains(&"-x".into()));
        assert!(!s.contains(&"-f".into()));

        let s = strs(&download_args(&t, "u", Path::new("/out"), None, true, &[], None, None));
        let i = s.iter().position(|a| a == "--audio-format").unwrap();
        assert_eq!(s[i + 1], "mp3");
        assert!(s.windows(2).any(|w| w[0] == "-f" && w[1] == "ba/b"));

        let s = strs(&download_args(&t, "u", Path::new("/out"), Some("ba[ext=m4a]/ba/b"), true, &[], None, None));
        let i = s.iter().position(|a| a == "--audio-format").unwrap();
        assert_eq!(s[i + 1], "m4a");
    }

    #[test]
    fn headers_become_add_header_args_and_url_stays_last() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        let h = [("Referer".to_string(), "https://www.douyin.com/".to_string()), ("User-Agent".to_string(), "Mozilla/5.0 X".to_string())];
        let s = strs(&download_args(&t, "https://cdn/x.mp4", Path::new("/out"), None, false, &h, None, None));
        assert!(s.windows(2).any(|w| w[0] == "--add-header" && w[1] == "Referer:https://www.douyin.com/"));
        assert!(s.windows(2).any(|w| w[0] == "--add-header" && w[1] == "User-Agent:Mozilla/5.0 X"));
        assert_eq!(s[s.len() - 2], "--");
        assert_eq!(s[s.len() - 1], "https://cdn/x.mp4");
        assert!(s.iter().position(|a| a == "--add-header").unwrap() < s.len() - 2);
        // No headers -> no flag at all.
        let s = strs(&download_args(&t, "u", Path::new("/out"), None, false, &[], None, None));
        assert!(!s.contains(&"--add-header".into()));
    }

    #[test]
    fn named_output_template() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        let n = OutName { title: "Clip: a/b %(x)s? \"q\"\n", id: "7311" };
        assert_eq!(named_template(&n).as_deref(), Some("Clip a b (x)s q [7311].%(ext)s"));
        let s = strs(&download_args(&t, "u", Path::new("/out"), None, false, &[], Some(&n), None));
        let i = s.iter().position(|a| a == "-o").unwrap();
        assert_eq!(s[i + 1], "Clip a b (x)s q [7311].%(ext)s");
        let s = strs(&download_args(&t, "u", Path::new("/out"), None, false, &[], None, None));
        let i = s.iter().position(|a| a == "-o").unwrap();
        assert_eq!(s[i + 1], OUTPUT_TEMPLATE);
        // Unusable id or empty title -> fall back to the default template.
        assert_eq!(named_template(&OutName { title: "x", id: "a b" }), None);
        assert_eq!(named_template(&OutName { title: " ?? ", id: "1" }), None);
        // 150-byte cap on a multi-byte title never splits a character.
        let long = "ệ".repeat(100);
        let out = named_template(&OutName { title: &long, id: "1" }).unwrap();
        assert!(out.len() <= 150 + " [1].%(ext)s".len());
        assert!(out.starts_with('ệ'));
    }

    const SLEEP: [&str; 6] = ["--sleep-requests", "1", "--sleep-interval", "2", "--max-sleep-interval", "5"];

    #[test]
    fn cookies_only_when_given_and_before_the_url() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        let ck = Path::new("/tmp/ck-job-1.txt");
        let with = [
            probe_args(&t, "https://www.douyin.com/video/1", Some(ck)),
            channel_fetch_args(&t, "https://www.douyin.com/video/1", 50, Some(ck)),
            download_args(&t, "https://www.douyin.com/video/1", Path::new("/out"), None, false, &[], None, Some(ck)),
        ];
        for args in with {
            let s = strs(&args);
            let i = s.iter().position(|a| a == "--cookies").expect("--cookies");
            assert_eq!(s[i + 1], "/tmp/ck-job-1.txt");
            assert_eq!(s.iter().filter(|a| *a == "--cookies").count(), 1);
            let dd = s.iter().position(|a| a == "--").unwrap();
            assert!(i < dd);
            assert_eq!(dd, s.len() - 2);
            assert_eq!(s[s.len() - 1], "https://www.douyin.com/video/1");
            assert!(s.windows(6).any(|w| w == SLEEP), "{s:?}");
        }
        let without = [
            probe_args(&t, "u", None),
            channel_fetch_args(&t, "u", 50, None),
            download_args(&t, "u", Path::new("/out"), None, false, &[], None, None),
        ];
        for args in without {
            let s = strs(&args);
            assert!(!s.iter().any(|a| a.contains("cookie")), "{s:?}");
            for flag in ["--sleep-requests", "--sleep-interval", "--max-sleep-interval"] {
                assert!(!s.contains(&flag.to_string()), "{flag}");
            }
        }
    }
}
