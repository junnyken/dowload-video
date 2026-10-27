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
}
