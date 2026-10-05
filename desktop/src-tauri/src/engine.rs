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

pub fn probe_args(tools: &Tools<'_>, url: &str) -> Vec<OsString> {
    let mut a = common(tools);
    a.extend(["-J", "--no-playlist", "--"].map(OsString::from));
    a.push(url.into());
    a
}

pub fn download_args(
    tools: &Tools<'_>,
    url: &str,
    out_dir: &Path,
    format_id: Option<&str>,
    audio_only: bool,
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
            OUTPUT_TEMPLATE,
        ]
        .map(OsString::from),
    );
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
            probe_args(&t, "https://x/--exec=calc"),
            download_args(&t, "https://x/--exec=calc", Path::new("/out"), Some("137+ba"), false),
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
    fn download_progress_and_audio_flags() {
        let (f, d) = tools();
        let t = Tools { ffmpeg: &f, deno: &d };
        let s = strs(&download_args(&t, "u", Path::new("/out"), None, false));
        assert!(s.contains(&"download:VGDL %(progress)j".into()));
        assert!(s.contains(&"postprocess:VGPP %(progress)j".into()));
        assert!(s.contains(&"after_move:VGFILE %(filepath)j".into()));
        assert!(!s.contains(&"-x".into()));
        assert!(!s.contains(&"-f".into()));

        let s = strs(&download_args(&t, "u", Path::new("/out"), None, true));
        let i = s.iter().position(|a| a == "--audio-format").unwrap();
        assert_eq!(s[i + 1], "mp3");
        assert!(s.windows(2).any(|w| w[0] == "-f" && w[1] == "ba/b"));

        let s = strs(&download_args(&t, "u", Path::new("/out"), Some("ba[ext=m4a]/ba/b"), true));
        let i = s.iter().position(|a| a == "--audio-format").unwrap();
        assert_eq!(s[i + 1], "m4a");
    }
}
