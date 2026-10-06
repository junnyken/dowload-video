//! Local SQLite store (rusqlite, bundled SQLite) in the app data dir.
//!
//! Tables (schema v2, tracked with PRAGMA user_version):
//! - history: the contract's HistoryItem, written by the UI via history_add.
//! - produced_paths: canonical paths of files finished downloads produced.
//!   Written ONLY by Rust at completion; reveal_path/open_path check it. The
//!   UI cannot add to it (history_add's filePath is not trusted for that).
//! - job_partials: paths yt-dlp reported while downloading a job, so cancel
//!   still knows what to delete after a pause or an app restart.
//! - v2: channels (§4 Channel; pendingNew as JSON text) and channel_seen
//!   (channel_id, video_id). Queries live in channels.rs.

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

#[cfg(test)]
pub const SCHEMA_VERSION: i64 = 2;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct HistoryItem {
    pub id: String,
    pub url: String,
    pub title: String,
    pub platform: String,
    pub format_label: String,
    pub file_path: Option<String>,
    pub file_size: Option<u64>,
    pub state: String,
    pub error_code: Option<String>,
    pub created_at: String,
    pub finished_at: Option<String>,
    pub synced: bool,
}

impl HistoryItem {
    pub fn validate(&self) -> Result<(), String> {
        if !crate::paths::valid_job_id(&self.id) {
            return Err("invalid history id".into());
        }
        if !matches!(self.state.as_str(), "completed" | "failed") {
            return Err("state must be completed or failed".into());
        }
        let too_long = |s: &str, n: usize| s.len() > n;
        if too_long(&self.url, 4096)
            || too_long(&self.title, 2048)
            || too_long(&self.platform, 64)
            || too_long(&self.format_label, 256)
            || self.file_path.as_deref().is_some_and(|p| too_long(p, 4096))
            || self.error_code.as_deref().is_some_and(|p| too_long(p, 64))
            || too_long(&self.created_at, 64)
            || self.finished_at.as_deref().is_some_and(|p| too_long(p, 64))
        {
            return Err("history field too long".into());
        }
        Ok(())
    }
}

pub struct Db {
    conn: Mutex<Connection>,
}

fn migrate(conn: &Connection) -> rusqlite::Result<()> {
    migrate_v1(conn)?;
    migrate_v2(conn)
}

fn user_version(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("PRAGMA user_version", [], |r| r.get(0))
}

fn migrate_v1(conn: &Connection) -> rusqlite::Result<()> {
    let v = user_version(conn)?;
    if v < 1 {
        conn.execute_batch(
            "BEGIN;
             CREATE TABLE IF NOT EXISTS history (
               id TEXT PRIMARY KEY,
               url TEXT NOT NULL,
               title TEXT NOT NULL,
               platform TEXT NOT NULL,
               format_label TEXT NOT NULL,
               file_path TEXT,
               file_size INTEGER,
               state TEXT NOT NULL CHECK (state IN ('completed','failed')),
               error_code TEXT,
               created_at TEXT NOT NULL,
               finished_at TEXT,
               synced INTEGER NOT NULL DEFAULT 0
             );
             CREATE INDEX IF NOT EXISTS history_created_at ON history(created_at DESC);
             CREATE TABLE IF NOT EXISTS produced_paths (
               path TEXT PRIMARY KEY,
               job_id TEXT,
               created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
             );
             CREATE TABLE IF NOT EXISTS job_partials (
               job_id TEXT NOT NULL,
               out_dir TEXT NOT NULL,
               path TEXT NOT NULL,
               PRIMARY KEY (job_id, path)
             );
             PRAGMA user_version = 1;
             COMMIT;",
        )?;
    }
    Ok(())
}

/// v2 (C1 §4 channels). Additive only: v1 tables and rows are untouched.
fn migrate_v2(conn: &Connection) -> rusqlite::Result<()> {
    if user_version(conn)? < 2 {
        conn.execute_batch(
            "BEGIN;
             CREATE TABLE IF NOT EXISTS channels (
               id TEXT PRIMARY KEY,
               url TEXT NOT NULL,
               title TEXT NOT NULL,
               platform TEXT NOT NULL,
               thumbnail TEXT,
               mode TEXT NOT NULL CHECK (mode IN ('download','notify')),
               quality TEXT NOT NULL,
               out_dir TEXT NOT NULL,
               check_every_hours INTEGER NOT NULL,
               enabled INTEGER NOT NULL DEFAULT 1,
               last_checked_at TEXT,
               last_error TEXT,
               pending_new TEXT NOT NULL DEFAULT '[]',
               created_at TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS channel_seen (
               channel_id TEXT NOT NULL,
               video_id TEXT NOT NULL,
               seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
               PRIMARY KEY (channel_id, video_id)
             );
             PRAGMA user_version = 2;
             COMMIT;",
        )?;
    }
    Ok(())
}

fn row_to_item(r: &rusqlite::Row<'_>) -> rusqlite::Result<HistoryItem> {
    Ok(HistoryItem {
        id: r.get(0)?,
        url: r.get(1)?,
        title: r.get(2)?,
        platform: r.get(3)?,
        format_label: r.get(4)?,
        file_path: r.get(5)?,
        file_size: r.get::<_, Option<i64>>(6)?.map(|x| x.max(0) as u64),
        state: r.get(7)?,
        error_code: r.get(8)?,
        created_at: r.get(9)?,
        finished_at: r.get(10)?,
        synced: r.get::<_, i64>(11)? != 0,
    })
}

fn path_key(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

impl Db {
    pub fn open(path: &Path) -> rusqlite::Result<Db> {
        let conn = Connection::open(path)?;
        Self::init(conn)
    }

    #[cfg(test)]
    pub fn open_in_memory() -> rusqlite::Result<Db> {
        Self::init(Connection::open_in_memory()?)
    }

    fn init(conn: Connection) -> rusqlite::Result<Db> {
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        // WAL keeps readers (history list) from blocking the completion write.
        let _: String = conn.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
        migrate(&conn)?;
        Ok(Db { conn: Mutex::new(conn) })
    }

    pub(crate) fn c(&self) -> std::sync::MutexGuard<'_, Connection> {
        // A panic while holding the lock leaves the connection usable.
        self.conn.lock().unwrap_or_else(|e| e.into_inner())
    }

    #[cfg(test)]
    pub fn schema_version(&self) -> rusqlite::Result<i64> {
        self.c().query_row("PRAGMA user_version", [], |r| r.get(0))
    }

    pub fn list(&self, limit: Option<u32>, offset: Option<u32>, query: Option<&str>) -> rusqlite::Result<Vec<HistoryItem>> {
        let limit = limit.unwrap_or(100).clamp(1, 1000) as i64;
        let offset = offset.unwrap_or(0) as i64;
        let conn = self.c();
        let cols = "id,url,title,platform,format_label,file_path,file_size,state,error_code,created_at,finished_at,synced";
        let q = query.map(str::trim).filter(|q| !q.is_empty());
        let items = match q {
            Some(q) => {
                // LIKE with escaped wildcards; the value is a bound parameter.
                let pat = format!("%{}%", q.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"));
                let mut st = conn.prepare(&format!(
                    "SELECT {cols} FROM history WHERE title LIKE ?1 ESCAPE '\\' OR url LIKE ?1 ESCAPE '\\' OR platform LIKE ?1 ESCAPE '\\'
                     ORDER BY created_at DESC, id DESC LIMIT ?2 OFFSET ?3"
                ))?;
                let v = st.query_map(params![pat, limit, offset], row_to_item)?.collect::<rusqlite::Result<Vec<_>>>()?;
                v
            }
            None => {
                let mut st = conn.prepare(&format!(
                    "SELECT {cols} FROM history ORDER BY created_at DESC, id DESC LIMIT ?1 OFFSET ?2"
                ))?;
                let v = st.query_map(params![limit, offset], row_to_item)?.collect::<rusqlite::Result<Vec<_>>>()?;
                v
            }
        };
        Ok(items)
    }

    pub fn upsert(&self, it: &HistoryItem) -> rusqlite::Result<()> {
        self.c().execute(
            "INSERT INTO history (id,url,title,platform,format_label,file_path,file_size,state,error_code,created_at,finished_at,synced)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)
             ON CONFLICT(id) DO UPDATE SET url=excluded.url, title=excluded.title, platform=excluded.platform,
               format_label=excluded.format_label, file_path=excluded.file_path, file_size=excluded.file_size,
               state=excluded.state, error_code=excluded.error_code, created_at=excluded.created_at,
               finished_at=excluded.finished_at, synced=excluded.synced",
            params![
                it.id,
                it.url,
                it.title,
                it.platform,
                it.format_label,
                it.file_path,
                it.file_size.map(|x| x.min(i64::MAX as u64) as i64),
                it.state,
                it.error_code,
                it.created_at,
                it.finished_at,
                it.synced as i64
            ],
        )?;
        Ok(())
    }

    pub fn delete(&self, id: &str) -> rusqlite::Result<()> {
        self.c().execute("DELETE FROM history WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub fn clear(&self) -> rusqlite::Result<()> {
        self.c().execute("DELETE FROM history", [])?;
        Ok(())
    }

    pub fn mark_synced(&self, ids: &[String]) -> rusqlite::Result<()> {
        let mut conn = self.c();
        let tx = conn.transaction()?;
        {
            let mut st = tx.prepare("UPDATE history SET synced = 1 WHERE id = ?1")?;
            for id in ids {
                st.execute(params![id])?;
            }
        }
        tx.commit()
    }

    pub fn add_produced(&self, canonical: &Path, job_id: &str) -> rusqlite::Result<()> {
        self.c().execute(
            "INSERT OR REPLACE INTO produced_paths (path, job_id) VALUES (?1, ?2)",
            params![path_key(canonical), job_id],
        )?;
        Ok(())
    }

    pub fn is_produced(&self, canonical: &Path) -> bool {
        self.c()
            .query_row("SELECT 1 FROM produced_paths WHERE path = ?1", params![path_key(canonical)], |_| Ok(()))
            .optional()
            .ok()
            .flatten()
            .is_some()
    }

    pub fn add_partial(&self, job_id: &str, out_dir: &Path, path: &Path) -> rusqlite::Result<()> {
        self.c().execute(
            "INSERT OR IGNORE INTO job_partials (job_id, out_dir, path) VALUES (?1, ?2, ?3)",
            params![job_id, path_key(out_dir), path_key(path)],
        )?;
        Ok(())
    }

    /// (out_dir, tracked paths) for a job, if any were recorded.
    pub fn partials(&self, job_id: &str) -> rusqlite::Result<Option<(PathBuf, Vec<PathBuf>)>> {
        let conn = self.c();
        let mut st = conn.prepare("SELECT out_dir, path FROM job_partials WHERE job_id = ?1")?;
        let rows: Vec<(String, String)> =
            st.query_map(params![job_id], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        Ok(rows.first().map(|(d, _)| {
            (PathBuf::from(d), rows.iter().filter(|(od, _)| od == d).map(|(_, p)| PathBuf::from(p)).collect())
        }))
    }

    pub fn clear_partials(&self, job_id: &str) -> rusqlite::Result<()> {
        self.c().execute("DELETE FROM job_partials WHERE job_id = ?1", params![job_id])?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(id: &str, created: &str, title: &str) -> HistoryItem {
        HistoryItem {
            id: id.into(),
            url: format!("https://example.com/{id}"),
            title: title.into(),
            platform: "youtube".into(),
            format_label: "1080p".into(),
            file_path: Some(format!("C:\\Users\\u\\Downloads\\VidGrab\\{id}.mp4")),
            file_size: Some(123),
            state: "completed".into(),
            error_code: None,
            created_at: created.into(),
            finished_at: Some(created.into()),
            synced: false,
        }
    }

    #[test]
    fn migration_sets_version_and_is_idempotent() {
        let dir = std::env::temp_dir().join(format!("vg-db-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("vidgrab.db");
        {
            let db = Db::open(&p).unwrap();
            assert_eq!(db.schema_version().unwrap(), SCHEMA_VERSION);
            db.upsert(&item("a", "2026-10-06T10:00:00Z", "x")).unwrap();
        }
        let db = Db::open(&p).unwrap(); // re-open runs migrate again
        assert_eq!(db.schema_version().unwrap(), SCHEMA_VERSION);
        assert_eq!(db.list(None, None, None).unwrap().len(), 1);
        drop(db);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn migration_v1_to_v2_keeps_history() {
        let dir = std::env::temp_dir().join(format!("vg-db-v1-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("vidgrab.db");
        {
            // A v0.2.x database: v1 schema with rows in every table.
            let conn = Connection::open(&p).unwrap();
            migrate_v1(&conn).unwrap();
            assert_eq!(user_version(&conn).unwrap(), 1);
            conn.execute(
                "INSERT INTO history (id,url,title,platform,format_label,state,created_at,synced)
                 VALUES ('old','https://e/1','Old','youtube','720p','completed','2026-10-01T00:00:00Z',1)",
                [],
            )
            .unwrap();
            conn.execute("INSERT INTO produced_paths (path, job_id) VALUES ('/d/old.mp4','old')", []).unwrap();
            let has_channels: i64 = conn
                .query_row("SELECT count(*) FROM sqlite_master WHERE name='channels'", [], |r| r.get(0))
                .unwrap();
            assert_eq!(has_channels, 0);
        }
        let db = Db::open(&p).unwrap();
        assert_eq!(db.schema_version().unwrap(), 2);
        let items = db.list(None, None, None).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].id, "old");
        assert!(items[0].synced);
        assert!(db.is_produced(Path::new("/d/old.mp4")));
        assert!(db.channel_list().unwrap().is_empty());
        drop(db);
        let db = Db::open(&p).unwrap(); // idempotent
        assert_eq!(db.schema_version().unwrap(), 2);
        assert_eq!(db.list(None, None, None).unwrap().len(), 1);
        drop(db);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn crud_upsert_order_search_sync() {
        let db = Db::open_in_memory().unwrap();
        db.upsert(&item("a", "2026-10-06T10:00:00Z", "Cats 100%")).unwrap();
        db.upsert(&item("b", "2026-10-06T11:00:00Z", "Dogs")).unwrap();
        db.upsert(&item("c", "2026-10-06T09:00:00Z", "cat_video")).unwrap();
        let all = db.list(None, None, None).unwrap();
        assert_eq!(all.iter().map(|i| i.id.as_str()).collect::<Vec<_>>(), ["b", "a", "c"]);

        // Upsert replaces.
        let mut a2 = item("a", "2026-10-06T10:00:00Z", "Cats renamed");
        a2.state = "failed".into();
        a2.error_code = Some("network".into());
        db.upsert(&a2).unwrap();
        let got = db.list(None, None, Some("renamed")).unwrap();
        assert_eq!(got, vec![a2.clone()]);

        // LIKE wildcards in the query are literal.
        assert_eq!(db.list(None, None, Some("100%")).unwrap().len(), 0);
        assert_eq!(db.list(None, None, Some("cat_")).unwrap().iter().map(|i| &i.id).collect::<Vec<_>>(), ["c"]);
        // SQL in the query is just text.
        assert!(db.list(None, None, Some("'; DROP TABLE history; --")).unwrap().is_empty());
        assert_eq!(db.list(None, None, None).unwrap().len(), 3);

        // Paging.
        assert_eq!(db.list(Some(1), Some(1), None).unwrap()[0].id, "a");

        db.mark_synced(&["a".into(), "b".into(), "nope".into()]).unwrap();
        let s: Vec<bool> = db.list(None, None, None).unwrap().iter().map(|i| i.synced).collect();
        assert_eq!(s, [true, true, false]);

        db.delete("b").unwrap();
        assert_eq!(db.list(None, None, None).unwrap().len(), 2);
        db.clear().unwrap();
        assert!(db.list(None, None, None).unwrap().is_empty());
    }

    #[test]
    fn validation() {
        assert!(item("a", "t", "x").validate().is_ok());
        let mut bad = item("a", "t", "x");
        bad.state = "running".into();
        assert!(bad.validate().is_err());
        assert!(item("../a", "t", "x").validate().is_err());
        assert!(item("a", "t", &"x".repeat(5000)).validate().is_err());
    }

    #[test]
    fn produced_and_partials() {
        let db = Db::open_in_memory().unwrap();
        let p = Path::new("/d/a [x].mp4");
        assert!(!db.is_produced(p));
        db.add_produced(p, "j1").unwrap();
        assert!(db.is_produced(p));
        assert!(!db.is_produced(Path::new("/d/b.mp4")));
        // History clear/delete never touch produced paths.
        db.clear().unwrap();
        assert!(db.is_produced(p));

        assert_eq!(db.partials("j1").unwrap(), None);
        db.add_partial("j1", Path::new("/d"), Path::new("/d/a.part")).unwrap();
        db.add_partial("j1", Path::new("/d"), Path::new("/d/a.part")).unwrap();
        db.add_partial("j1", Path::new("/d"), Path::new("/d/b.part")).unwrap();
        let (dir, files) = db.partials("j1").unwrap().unwrap();
        assert_eq!(dir, PathBuf::from("/d"));
        assert_eq!(files.len(), 2);
        db.clear_partials("j1").unwrap();
        assert_eq!(db.partials("j1").unwrap(), None);
    }
}
