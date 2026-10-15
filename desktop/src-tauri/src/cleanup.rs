//! Auto cleanup (app 0.17.0, owner 15/10/2026): old downloads go to the
//! Recycle Bin, so a mistake can be undone.
//!
//! Only files VidGrab itself downloaded are ever touched: a `completed`
//! history row whose `file_path` is ALSO in `produced_paths` (the canonical
//! paths finished downloads produced), that still exists as a regular file.
//! Nothing the user put in the folder, no directories, no paths from the UI.
//!
//! Two triggers, both optional: older than N days, and "the download drive
//! has less than X GB free" (then the oldest go first until the projected
//! free space reaches X). The UI previews the plan, then runs it.

use crate::history::Db;
use rusqlite::params;
use serde::Serialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub id: String,
    pub path: PathBuf,
    pub size: u64,
    /// RFC 3339, as stored.
    pub finished_at: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct Plan {
    pub count: usize,
    pub bytes: u64,
    /// Free bytes on the download drive now (None if unknown).
    pub free_bytes: Option<u64>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct Outcome {
    pub moved: usize,
    pub bytes: u64,
    pub failed: usize,
}

impl Db {
    /// Completed downloads whose file VidGrab produced, oldest first.
    pub fn cleanup_rows(&self) -> rusqlite::Result<Vec<Row>> {
        let conn = self.c();
        let mut st = conn.prepare(
            "SELECT h.id, h.file_path, COALESCE(h.file_size, 0), COALESCE(h.finished_at, h.created_at)
             FROM history h
             WHERE h.state = 'completed' AND h.file_path IS NOT NULL
             ORDER BY COALESCE(h.finished_at, h.created_at) ASC",
        )?;
        let rows = st
            .query_map([], |r| {
                Ok(Row {
                    id: r.get(0)?,
                    path: PathBuf::from(r.get::<_, String>(1)?),
                    size: r.get::<_, i64>(2)?.max(0) as u64,
                    finished_at: r.get(3)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        drop(st);
        drop(conn);
        Ok(rows.into_iter().filter(|r| self.is_produced(&r.path)).collect())
    }

    /// The file is gone (trashed): the history row stays, without a path.
    pub fn cleanup_forget(&self, id: &str) -> rusqlite::Result<()> {
        self.c().execute("UPDATE history SET file_path = NULL WHERE id = ?1", params![id])?;
        Ok(())
    }
}

/// Which rows to move. `older_than_days`: every row finished before
/// `now - days`. `min_free`: with `free` below it, add the oldest remaining
/// rows until free + their sizes reaches it. Rows must be sorted oldest first.
pub fn select<'a>(rows: &'a [Row], now_unix: i64, older_than_days: Option<u32>, min_free: Option<u64>, free: Option<u64>) -> Vec<&'a Row> {
    let cutoff = older_than_days.map(|d| now_unix - i64::from(d) * 86_400);
    let mut picked: Vec<&Row> = Vec::new();
    let mut gained: u64 = 0;
    for r in rows {
        let ts = parse_rfc3339(&r.finished_at);
        let old = matches!((cutoff, ts), (Some(c), Some(t)) if t < c);
        let need_space = match (min_free, free) {
            (Some(m), Some(f)) => f.saturating_add(gained) < m,
            _ => false,
        };
        if old || need_space {
            gained = gained.saturating_add(r.size);
            picked.push(r);
        }
    }
    picked
}

/// Seconds since the epoch of "YYYY-MM-DDTHH:MM:SS…Z" (what the app stores); None if not that shape.
pub fn parse_rfc3339(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' || (b[10] != b'T' && b[10] != b' ') || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let n = |a: usize, z: usize| s.get(a..z)?.parse::<i64>().ok();
    let (y, mo, d, h, mi, se) = (n(0, 4)?, n(5, 7)?, n(8, 10)?, n(11, 13)?, n(14, 16)?, n(17, 19)?);
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) || h > 23 || mi > 59 || se > 60 {
        return None;
    }
    // Days from civil (Howard Hinnant), UTC.
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = if y2 >= 0 { y2 } else { y2 - 399 } / 400;
    let yoe = y2 - era * 400;
    let mp = (mo + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + h * 3600 + mi * 60 + se)
}

/// Only regular files that still exist; sizes refreshed from disk.
pub fn existing(rows: Vec<Row>) -> Vec<Row> {
    rows.into_iter()
        .filter_map(|mut r| {
            let m = std::fs::symlink_metadata(&r.path).ok()?;
            if !m.file_type().is_file() {
                return None;
            }
            r.size = m.len();
            Some(r)
        })
        .collect()
}

pub fn to_trash(path: &Path) -> Result<(), String> {
    trash::delete(path).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, at: &str, size: u64) -> Row {
        Row { id: id.into(), path: PathBuf::from(format!("/d/{id}.mp4")), size, finished_at: at.into() }
    }

    #[test]
    fn parses_stored_times() {
        assert_eq!(parse_rfc3339("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_rfc3339("2026-10-15T00:00:00.123Z"), Some(1_792_022_400));
        assert_eq!(parse_rfc3339("2026-13-01T00:00:00Z"), None);
        assert_eq!(parse_rfc3339("garbage"), None);
    }

    #[test]
    fn selects_by_age_and_by_space() {
        let now = parse_rfc3339("2026-10-15T00:00:00Z").unwrap();
        let rows = vec![row("a", "2026-08-01T00:00:00Z", 5), row("b", "2026-10-01T00:00:00Z", 7), row("c", "2026-10-14T00:00:00Z", 9)];
        let ids = |v: Vec<&Row>| v.iter().map(|r| r.id.clone()).collect::<Vec<_>>();
        assert_eq!(ids(select(&rows, now, Some(30), None, None)), ["a"]);
        assert_eq!(ids(select(&rows, now, None, None, None)), Vec::<String>::new());
        // 10 free, want 20: a (+5) → 15, b (+7) → 22, stop.
        assert_eq!(ids(select(&rows, now, None, Some(20), Some(10))), ["a", "b"]);
        // Enough space already: nothing.
        assert_eq!(ids(select(&rows, now, None, Some(20), Some(30))), Vec::<String>::new());
        // Unknown free space: the space rule does nothing.
        assert_eq!(ids(select(&rows, now, None, Some(20), None)), Vec::<String>::new());
        // A bad timestamp is never "old".
        let odd = vec![row("x", "?", 1)];
        assert!(select(&odd, now, Some(1), None, None).is_empty());
    }

    #[test]
    fn only_produced_completed_files() {
        let db = Db::open_in_memory().unwrap();
        let dir = std::env::temp_dir().join(format!("vg-clean-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mine = dir.join("mine.mp4");
        let user = dir.join("user.mp4");
        std::fs::write(&mine, b"12345").unwrap();
        std::fs::write(&user, b"x").unwrap();
        let canon = |p: &Path| crate::paths::canonical(p).unwrap();
        for (id, p, state) in [("1", &mine, "completed"), ("2", &user, "completed"), ("3", &mine, "failed")] {
            db.c()
                .execute(
                    "INSERT INTO history (id,url,title,platform,format_label,file_path,file_size,state,created_at,finished_at)
                     VALUES (?1,'https://x','t','youtube','best',?2,1,?3,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')",
                    params![id, canon(p).to_string_lossy(), state],
                )
                .unwrap();
        }
        db.add_produced(&canon(&mine), "1").unwrap(); // user.mp4 was never produced by VidGrab
        let rows = existing(db.cleanup_rows().unwrap());
        assert_eq!(rows.iter().map(|r| r.id.as_str()).collect::<Vec<_>>(), ["1"]);
        assert_eq!(rows[0].size, 5);
        db.cleanup_forget("1").unwrap();
        assert!(db.cleanup_rows().unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
