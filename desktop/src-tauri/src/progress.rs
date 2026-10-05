//! Machine-readable progress from yt-dlp.
//!
//! yt-dlp is started with (see engine.rs):
//!   --progress-template "download:VGDL %(progress)j"
//!   --progress-template "postprocess:VGPP %(progress)j"
//!   --print "after_move:VGFILE %(filepath)j"
//! so every update is one line holding a JSON object, and the final path is
//! one JSON string. Measured with yt-dlp 2026.03.17 (`-q --progress`):
//! VGDL and VGFILE lines go to stdout, VGPP lines to stderr; both pipes are
//! parsed, so this does not depend on which stream a version uses.

use serde::Serialize;
use serde_json::Value;
use std::time::{Duration, Instant};

pub const DL_MARK: &str = "VGDL ";
pub const PP_MARK: &str = "VGPP ";
pub const FILE_MARK: &str = "VGFILE ";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Stage {
    Downloading,
    Merging,
    Processing,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Update {
    pub stage: Option<Stage>,
    pub percent: Option<f64>,
    pub downloaded_bytes: Option<u64>,
    pub total_bytes: Option<u64>,
    pub speed_bps: Option<f64>,
    pub eta_sec: Option<f64>,
    /// `true` for the last update of a stream/step (always emitted).
    pub finished: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Line {
    Progress {
        update: Update,
        /// Paths yt-dlp is writing (tmpfilename, filename); used to know which
        /// partial files belong to this job.
        files: Vec<String>,
    },
    FinalPath(String),
    /// Not one of ours: forward to the log drawer as is.
    Other,
}

fn num(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64).filter(|x| x.is_finite() && *x >= 0.0)
}

fn bytes(v: &Value, key: &str) -> Option<u64> {
    num(v, key).map(|x| x as u64)
}

pub fn parse_line(line: &str) -> Line {
    if let Some(i) = line.find(DL_MARK) {
        if let Ok(v) = serde_json::from_str::<Value>(line[i + DL_MARK.len()..].trim()) {
            return parse_download(&v);
        }
    } else if let Some(i) = line.find(PP_MARK) {
        if let Ok(v) = serde_json::from_str::<Value>(line[i + PP_MARK.len()..].trim()) {
            return parse_postprocess(&v);
        }
    } else if let Some(i) = line.find(FILE_MARK) {
        if let Ok(Value::String(p)) = serde_json::from_str::<Value>(line[i + FILE_MARK.len()..].trim()) {
            if !p.is_empty() && p != "NA" {
                return Line::FinalPath(p);
            }
        }
    }
    Line::Other
}

fn parse_download(v: &Value) -> Line {
    let status = v.get("status").and_then(Value::as_str).unwrap_or("");
    let downloaded = bytes(v, "downloaded_bytes");
    let total = bytes(v, "total_bytes").or_else(|| bytes(v, "total_bytes_estimate")).filter(|t| *t > 0);
    let finished = status == "finished";
    let percent = if finished {
        Some(100.0)
    } else if let (Some(d), Some(t)) = (downloaded, total) {
        Some((d as f64 / t as f64 * 100.0).min(100.0))
    } else if let (Some(i), Some(n)) = (num(v, "fragment_index"), num(v, "fragment_count")) {
        (n > 0.0).then(|| (i / n * 100.0).min(100.0))
    } else {
        num(v, "_percent").map(|p| p.min(100.0))
    };
    let files = ["tmpfilename", "filename"]
        .iter()
        .filter_map(|k| v.get(*k).and_then(Value::as_str))
        .filter(|s| !s.is_empty())
        .map(String::from)
        .collect();
    Line::Progress {
        update: Update {
            stage: Some(Stage::Downloading),
            percent,
            downloaded_bytes: downloaded,
            total_bytes: total,
            speed_bps: num(v, "speed"),
            eta_sec: num(v, "eta"),
            finished,
        },
        files,
    }
}

fn parse_postprocess(v: &Value) -> Line {
    let pp = v.get("postprocessor").and_then(Value::as_str).unwrap_or("");
    let status = v.get("status").and_then(Value::as_str).unwrap_or("");
    // MoveFiles only renames the finished file; reporting it would flash a
    // "processing" state at the very end.
    if pp == "MoveFiles" {
        return Line::Other;
    }
    let stage = if pp == "Merger" { Stage::Merging } else { Stage::Processing };
    Line::Progress {
        update: Update { stage: Some(stage), finished: status == "finished", ..Default::default() },
        files: Vec::new(),
    }
}

/// At most one progress event per `interval` per job, except that a stage
/// change and a `finished` update always go through.
pub struct Throttle {
    interval: Duration,
    last: Option<Instant>,
    last_stage: Option<Stage>,
}

impl Throttle {
    pub fn new(interval: Duration) -> Self {
        Throttle { interval, last: None, last_stage: None }
    }

    pub fn allow(&mut self, now: Instant, update: &Update) -> bool {
        let stage_changed = update.stage != self.last_stage;
        let due = self.last.is_none_or(|t| now.duration_since(t) >= self.interval);
        if stage_changed || update.finished || due {
            self.last = Some(now);
            self.last_stage = update.stage;
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Verbatim lines captured from yt-dlp 2026.03.17 in the workspace
    // (scratch HTTP server, generic extractor, path shortened).
    const DL: &str = r#"VGDL {"status": "downloading", "downloaded_bytes": 3072, "total_bytes": 3000000, "tmpfilename": "/o/video [video].mp4.part", "filename": "/o/video [video].mp4", "eta": 3, "speed": 860887.4115053117, "elapsed": 0.005436420440673828, "ctx_id": null, "_eta_str": "00:03", "_percent": 0.1024, "_default_template": "  0.1% of    2.86MiB at  840.71KiB/s ETA 00:03"}"#;
    const DONE: &str = r#"VGDL {"downloaded_bytes": 3000000, "total_bytes": 3000000, "filename": "/o/video [video].mp4", "status": "finished", "elapsed": 2.86, "ctx_id": null, "speed": 1046739.4}"#;
    const PP: &str = r#"VGPP {"status": "started", "postprocessor": "Merger", "_default_template": "Merger started"}"#;
    const FILE: &str = r#"VGFILE "/o/video [video].mp4""#;

    #[test]
    fn parses_download_line() {
        match parse_line(DL) {
            Line::Progress { update, files } => {
                assert_eq!(update.stage, Some(Stage::Downloading));
                assert_eq!(update.downloaded_bytes, Some(3072));
                assert_eq!(update.total_bytes, Some(3_000_000));
                assert!((update.percent.unwrap() - 0.1024).abs() < 1e-9);
                assert_eq!(update.eta_sec, Some(3.0));
                assert!(update.speed_bps.unwrap() > 800_000.0);
                assert!(!update.finished);
                assert_eq!(files, vec!["/o/video [video].mp4.part", "/o/video [video].mp4"]);
            }
            other => panic!("{other:?}"),
        }
        match parse_line(DONE) {
            Line::Progress { update, .. } => {
                assert!(update.finished);
                assert_eq!(update.percent, Some(100.0));
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn null_and_missing_fields_become_none() {
        let l = r#"VGDL {"status": "downloading", "downloaded_bytes": 10, "total_bytes": null, "speed": null, "eta": null, "fragment_index": 3, "fragment_count": 12}"#;
        match parse_line(l) {
            Line::Progress { update, .. } => {
                assert_eq!(update.total_bytes, None);
                assert_eq!(update.speed_bps, None);
                assert_eq!(update.percent, Some(25.0));
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn parses_postprocess_and_final_path() {
        match parse_line(PP) {
            Line::Progress { update, .. } => assert_eq!(update.stage, Some(Stage::Merging)),
            other => panic!("{other:?}"),
        }
        let x = r#"VGPP {"status": "started", "postprocessor": "ExtractAudio"}"#;
        match parse_line(x) {
            Line::Progress { update, .. } => assert_eq!(update.stage, Some(Stage::Processing)),
            other => panic!("{other:?}"),
        }
        assert_eq!(parse_line(r#"VGPP {"status": "started", "postprocessor": "MoveFiles"}"#), Line::Other);
        assert_eq!(parse_line(FILE), Line::FinalPath("/o/video [video].mp4".into()));
        // Non-ASCII arrives \u-escaped (json.dumps default).
        assert_eq!(parse_line(r#"VGFILE "C:\\D\\Vi\u1ec7t [x].mp4""#), Line::FinalPath("C:\\D\\Việt [x].mp4".into()));
    }

    #[test]
    fn garbage_is_other() {
        assert_eq!(parse_line("[download] Destination: x"), Line::Other);
        assert_eq!(parse_line("VGDL {not json"), Line::Other);
        assert_eq!(parse_line("VGFILE NA"), Line::Other);
        assert_eq!(parse_line(""), Line::Other);
    }

    #[test]
    fn throttle_limits_rate_but_keeps_stage_changes_and_finish() {
        let mut t = Throttle::new(Duration::from_millis(250));
        let t0 = Instant::now();
        let dl = Update { stage: Some(Stage::Downloading), ..Default::default() };
        assert!(t.allow(t0, &dl));
        assert!(!t.allow(t0 + Duration::from_millis(100), &dl));
        assert!(!t.allow(t0 + Duration::from_millis(249), &dl));
        assert!(t.allow(t0 + Duration::from_millis(250), &dl));
        let fin = Update { finished: true, ..dl.clone() };
        assert!(t.allow(t0 + Duration::from_millis(260), &fin));
        let merge = Update { stage: Some(Stage::Merging), ..Default::default() };
        assert!(t.allow(t0 + Duration::from_millis(270), &merge));
        // 100 updates in one second -> at most 4 (+1 for the first) emitted.
        let mut t = Throttle::new(Duration::from_millis(250));
        let n = (0..100).filter(|i| t.allow(t0 + Duration::from_millis(i * 10), &dl)).count();
        assert!(n <= 4, "{n}");
    }
}
