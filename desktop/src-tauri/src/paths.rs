//! Path guards: what open_path may open, and which files cancel may delete.
//!
//! Rules (C1-CONTRACT.md §1):
//! - reveal/open only accept files this app produced (recorded in the DB at
//!   download completion, never from the webview), and open additionally
//!   needs a media extension.
//! - cancel deletes only this job's partial files, only directly inside the
//!   job's output folder, never a symlink/directory, never a produced file.

use std::fs;
use std::path::{Path, PathBuf};

pub const MEDIA_EXTENSIONS: &[&str] = &[
    "mp4", "mkv", "webm", "mov", "m4a", "mp3", "opus", "ogg", "wav", "flac", "aac", "jpg", "png", "webp", "srt",
    "vtt",
];

pub fn has_media_extension(p: &Path) -> bool {
    p.extension()
        .and_then(|e| e.to_str())
        .map(|e| MEDIA_EXTENSIONS.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// Canonical form used as the key of the produced-paths table.
pub fn canonical(p: &Path) -> std::io::Result<PathBuf> {
    fs::canonicalize(p)
}

/// `fs::canonicalize` returns `\\?\C:\...` on Windows. Explorer and
/// ShellExecute want the plain form.
pub fn display_form(p: &Path) -> PathBuf {
    let s = p.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = s.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        p.to_path_buf()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Refusal {
    NotFound,
    NotProduced,
    NotMedia,
}

/// Checks a path coming from the webview. Returns the canonical path to act
/// on. `is_produced` looks the canonical path up in the DB.
pub fn check_produced(
    raw: &str,
    need_media: bool,
    is_produced: impl Fn(&Path) -> bool,
) -> Result<PathBuf, Refusal> {
    let p = Path::new(raw);
    if raw.is_empty() || !p.is_absolute() {
        return Err(Refusal::NotProduced);
    }
    let canon = canonical(p).map_err(|_| Refusal::NotFound)?;
    let meta = fs::metadata(&canon).map_err(|_| Refusal::NotFound)?;
    if !meta.is_file() {
        return Err(Refusal::NotProduced);
    }
    if !is_produced(&canon) {
        return Err(Refusal::NotProduced);
    }
    if need_media && !has_media_extension(&canon) {
        return Err(Refusal::NotMedia);
    }
    Ok(canon)
}

/// `<stem>].f<id>.<ext>` = a per-format intermediate that yt-dlp merges and
/// then deletes. Our output template always ends in `[%(id)s].%(ext)s`, so
/// the stem must end with `]`.
fn intermediate_stem(name: &str) -> Option<&str> {
    let (rest, ext) = name.rsplit_once('.')?;
    let (stem, fpart) = rest.rsplit_once('.')?;
    let fid = fpart.strip_prefix('f')?;
    let ok = !fid.is_empty()
        && !ext.is_empty()
        && ext.len() <= 5
        && ext.chars().all(|c| c.is_ascii_alphanumeric())
        && fid.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        && stem.ends_with(']');
    ok.then_some(stem)
}

/// Lists the files cancel may delete for one job.
///
/// `tracked` are the paths yt-dlp reported while downloading this job
/// (`tmpfilename` / `filename`). From each one we derive only partial-file
/// names; the final file name itself is never a candidate unless it is a
/// per-format intermediate.
pub fn deletion_targets(out_dir: &Path, tracked: &[PathBuf], is_produced: impl Fn(&Path) -> bool) -> Vec<PathBuf> {
    let Ok(dir) = canonical(out_dir) else { return Vec::new() };
    let mut names: Vec<String> = Vec::new();
    let listing: Vec<String> = fs::read_dir(&dir)
        .map(|rd| rd.filter_map(|e| e.ok()).filter_map(|e| e.file_name().into_string().ok()).collect())
        .unwrap_or_default();

    for t in tracked {
        // The tracked file must sit directly in out_dir.
        let Some(parent) = t.parent() else { continue };
        if parent.as_os_str().is_empty() || canonical(parent).ok().as_deref() != Some(dir.as_path()) {
            continue;
        }
        let Some(name) = t.file_name().and_then(|n| n.to_str()) else { continue };
        let base = name.strip_suffix(".part").unwrap_or(name);
        names.push(format!("{base}.part"));
        names.push(format!("{base}.ytdl"));
        let frag = format!("{base}.part-Frag");
        names.extend(listing.iter().filter(|n| n.starts_with(&frag)).cloned());
        if let Some(stem) = intermediate_stem(base) {
            // The per-format file itself and the merger's `<stem>.temp.<ext>`.
            names.push(base.to_string());
            let temp = format!("{stem}.temp.");
            names.extend(
                listing
                    .iter()
                    .filter(|n| n.strip_prefix(&temp).is_some_and(|e| !e.is_empty() && e.len() <= 5 && e.chars().all(|c| c.is_ascii_alphanumeric())))
                    .cloned(),
            );
        }
    }

    names.sort();
    names.dedup();
    names
        .into_iter()
        .filter(|n| !n.contains(['/', '\\', ':']) && n != "." && n != "..")
        .map(|n| dir.join(n))
        .filter(|p| fs::symlink_metadata(p).map(|m| m.file_type().is_file()).unwrap_or(false))
        .filter(|p| !is_produced(p))
        .collect()
}

/// Deletes with a few retries: on Windows the killed processes can keep the
/// handle open for a moment after TerminateJobObject returns.
pub fn delete_files(files: &[PathBuf]) -> Vec<(PathBuf, String)> {
    let mut failed = Vec::new();
    for f in files {
        let mut last = None;
        for attempt in 0..10 {
            match fs::remove_file(f) {
                Ok(()) => {
                    last = None;
                    break;
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                    last = None;
                    break;
                }
                Err(e) => {
                    last = Some(e.to_string());
                    std::thread::sleep(std::time::Duration::from_millis(100 * (attempt + 1)));
                }
            }
        }
        if let Some(e) = last {
            failed.push((f.clone(), e));
        }
    }
    failed
}

/// Job ids come from the UI and are used as DB keys and in event payloads.
pub fn valid_job_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    fn tmpdir(tag: &str) -> PathBuf {
        static N: AtomicU32 = AtomicU32::new(0);
        let d = std::env::temp_dir().join(format!(
            "vg-paths-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn touch(p: &Path) {
        fs::write(p, b"x").unwrap();
    }

    #[test]
    fn open_refuses_non_produced_and_non_media() {
        let d = tmpdir("open");
        let video = d.join("a [x].mp4");
        let exe = d.join("evil.exe");
        touch(&video);
        touch(&exe);
        let produced = [canonical(&video).unwrap(), canonical(&exe).unwrap()];
        let is_p = |p: &Path| produced.iter().any(|q| q == p);

        assert!(check_produced(video.to_str().unwrap(), true, is_p).is_ok());
        // Produced but not media -> refused for open (reveal would allow it).
        assert_eq!(check_produced(exe.to_str().unwrap(), true, is_p), Err(Refusal::NotMedia));
        assert!(check_produced(exe.to_str().unwrap(), false, is_p).is_ok());
        // A media file that this app did not produce.
        let other = d.join("other.mp4");
        touch(&other);
        assert_eq!(check_produced(other.to_str().unwrap(), true, is_p), Err(Refusal::NotProduced));
        // Same produced file reached through `..` is still recognised (canonical).
        let sub = d.join("sub");
        fs::create_dir(&sub).unwrap();
        let dotted = sub.join("..").join("a [x].mp4");
        assert!(check_produced(dotted.to_str().unwrap(), true, is_p).is_ok());
        // Relative, empty, missing, directory.
        assert_eq!(check_produced("a [x].mp4", true, is_p), Err(Refusal::NotProduced));
        assert_eq!(check_produced("", true, is_p), Err(Refusal::NotProduced));
        assert_eq!(check_produced(d.join("missing.mp4").to_str().unwrap(), true, is_p), Err(Refusal::NotFound));
        assert_eq!(check_produced(d.to_str().unwrap(), false, |_| true), Err(Refusal::NotProduced));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn media_extension_allowlist() {
        assert!(has_media_extension(Path::new("x.MP4")));
        assert!(has_media_extension(Path::new("x.vtt")));
        for bad in ["x.exe", "x.lnk", "x.bat", "x.mp4.exe", "x", "x.html", "x.url", "x.scr"] {
            assert!(!has_media_extension(Path::new(bad)), "{bad}");
        }
    }

    #[test]
    fn cancel_deletes_only_partials_of_the_job() {
        let d = tmpdir("cancel");
        let part = d.join("Song [abc].f137.mp4.part");
        let ytdl = d.join("Song [abc].f137.mp4.ytdl");
        let frag = d.join("Song [abc].f137.mp4.part-Frag3");
        let inter_audio = d.join("Song [abc].f140.m4a"); // finished intermediate
        let merge_tmp = d.join("Song [abc].temp.mp4");
        let completed_earlier = d.join("Other [zzz].mp4"); // unrelated, completed
        let same_final = d.join("Single [def].mp4"); // final name of a single-format job
        let single_part = d.join("Single [def].mp4.part");
        for f in [&part, &ytdl, &frag, &inter_audio, &merge_tmp, &completed_earlier, &same_final, &single_part] {
            touch(f);
        }
        let tracked = vec![
            part.clone(),
            d.join("Song [abc].f137.mp4"),
            d.join("Song [abc].f140.m4a.part"),
            d.join("Song [abc].f140.m4a"),
            single_part.clone(),
            same_final.clone(),
        ];
        let mut got = deletion_targets(&d, &tracked, |_| false);
        got.sort();
        let dc = canonical(&d).unwrap();
        let mut want: Vec<PathBuf> = [
            "Single [def].mp4.part",
            "Song [abc].f137.mp4.part",
            "Song [abc].f137.mp4.part-Frag3",
            "Song [abc].f137.mp4.ytdl",
            "Song [abc].f140.m4a",
            "Song [abc].temp.mp4",
        ]
        .iter()
        .map(|n| dc.join(n))
        .collect();
        want.sort();
        assert_eq!(got, want);
        // Never the completed/final files.
        assert!(!got.contains(&dc.join("Single [def].mp4")));
        assert!(!got.contains(&dc.join("Other [zzz].mp4")));

        assert!(delete_files(&got).is_empty());
        assert!(same_final.exists() && completed_earlier.exists());
        assert!(!part.exists() && !merge_tmp.exists());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn cancel_never_deletes_outside_out_dir() {
        let root = tmpdir("outside");
        let out = root.join("out");
        let elsewhere = root.join("elsewhere");
        fs::create_dir_all(&out).unwrap();
        fs::create_dir_all(&elsewhere).unwrap();
        let victim = elsewhere.join("x [a].mp4.part");
        touch(&victim);
        let victim2 = root.join("y [b].mp4.part");
        touch(&victim2);

        let tracked = vec![
            victim.clone(),                                // absolute path in another folder
            out.join("..").join("y [b].mp4.part"),          // `..` escape
            out.join("..").join("elsewhere").join("x [a].mp4.part"),
            PathBuf::from("y [b].mp4.part"),                // relative
            out.join("sub").join("z.part"),                 // subfolder
        ];
        assert!(deletion_targets(&out, &tracked, |_| false).is_empty());
        assert!(victim.exists() && victim2.exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn cancel_skips_produced_symlinks_and_dirs() {
        let d = tmpdir("guards");
        let part = d.join("a [x].mp4.part");
        touch(&part);
        // A produced file never goes, even if its name looks partial.
        let got = deletion_targets(&d, &[part.clone()], |_| true);
        assert!(got.is_empty());
        // A directory with a partial-looking name is not deleted.
        fs::create_dir(d.join("b [y].mp4.part")).unwrap();
        assert!(deletion_targets(&d, &[d.join("b [y].mp4")], |_| false).is_empty());
        #[cfg(unix)]
        {
            let outside = tmpdir("guards-target");
            let target = outside.join("keep.mp4");
            touch(&target);
            std::os::unix::fs::symlink(&target, d.join("c [z].mp4.part")).unwrap();
            assert!(deletion_targets(&d, &[d.join("c [z].mp4")], |_| false).is_empty());
            assert!(target.exists());
            let _ = fs::remove_dir_all(&outside);
        }
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn intermediate_detection() {
        assert_eq!(intermediate_stem("Song [abc].f137.mp4"), Some("Song [abc]"));
        assert_eq!(intermediate_stem("Song [abc].fhls-720p.mp4"), Some("Song [abc]"));
        assert_eq!(intermediate_stem("Song feat. X [abc].mp4"), None);
        assert_eq!(intermediate_stem("Song [abc].mp4"), None);
        assert_eq!(intermediate_stem("a.f1.mp4"), None, "stem must end with ]");
    }

    #[test]
    fn display_form_strips_verbatim_prefix() {
        assert_eq!(display_form(Path::new(r"\\?\C:\a\b.mp4")), PathBuf::from(r"C:\a\b.mp4"));
        assert_eq!(display_form(Path::new(r"\\?\UNC\srv\s\b.mp4")), PathBuf::from(r"\\srv\s\b.mp4"));
        assert_eq!(display_form(Path::new("/x/y")), PathBuf::from("/x/y"));
    }

    #[test]
    fn job_ids() {
        assert!(valid_job_id("job-1_a"));
        assert!(!valid_job_id(""));
        assert!(!valid_job_id("../x"));
        assert!(!valid_job_id(&"a".repeat(65)));
    }
}
