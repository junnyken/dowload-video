//! The one error type every command returns, and the mapping from yt-dlp's
//! stderr to the contract's machine-readable codes (C1-CONTRACT.md §1).
//!
//! `message` is English and meant for logs; the UI shows a Vietnamese text
//! chosen by `code`. The raw yt-dlp line is kept in `message` so support can
//! see what actually happened.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Code {
    InvalidUrl,
    Unsupported,
    PrivateOrLogin,
    GeoBlocked,
    NotFound,
    Network,
    Forbidden,
    DiskFull,
    ToolMissing,
    ToolTampered,
    Timeout,
    Cancelled,
    /// cookies_login_finish found no cookie of the platform in the login window.
    CookieRequired,
    /// PLAN-32E P3: the server requires a signed claim token and none valid
    /// came with start_download (quota_gate.rs).
    ClaimRequired,
    /// PLAN-32E P3: the API is unreachable and today's offline slots are used.
    OfflineGraceUsed,
    Unknown,
}

impl Code {
    pub fn as_str(self) -> &'static str {
        match self {
            Code::InvalidUrl => "invalid_url",
            Code::Unsupported => "unsupported",
            Code::PrivateOrLogin => "private_or_login",
            Code::GeoBlocked => "geo_blocked",
            Code::NotFound => "not_found",
            Code::Network => "network",
            Code::Forbidden => "forbidden",
            Code::DiskFull => "disk_full",
            Code::ToolMissing => "tool_missing",
            Code::ToolTampered => "tool_tampered",
            Code::Timeout => "timeout",
            Code::Cancelled => "cancelled",
            Code::CookieRequired => "cookie_required",
            Code::ClaimRequired => "claim_required",
            Code::OfflineGraceUsed => "offline_grace_used",
            Code::Unknown => "unknown",
        }
    }
}

/// Serialized as `{ "code": "...", "message": "..." }`.
#[derive(Debug, Clone, Serialize)]
pub struct CommandError {
    pub code: Code,
    pub message: String,
}

impl CommandError {
    pub fn new(code: Code, message: impl Into<String>) -> Self {
        CommandError { code, message: message.into() }
    }
    pub fn unknown(message: impl Into<String>) -> Self {
        Self::new(Code::Unknown, message)
    }
}

impl std::fmt::Display for CommandError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code.as_str(), self.message)
    }
}

pub type CmdResult<T> = Result<T, CommandError>;

/// Ordered rules: the first rule with a matching needle wins, so the more
/// specific ones come first (e.g. "HTTP Error 404" before the generic
/// "Unable to download webpage", which yt-dlp prefixes to both).
/// Needles are matched case-insensitively.
const RULES: &[(Code, &[&str])] = &[
    (
        Code::DiskFull,
        &[
            "no space left on device",
            "not enough space on the disk",
            "errno 28]",
            "winerror 112]",
            "disk quota exceeded",
        ],
    ),
    (
        Code::ToolMissing,
        &[
            "ffmpeg not found",
            "ffmpeg is not installed",
            "ffprobe not found",
            "--ffmpeg-location",
            "no supported javascript runtime",
        ],
    ),
    (
        Code::GeoBlocked,
        &[
            "not available in your country",
            "not made this video available in your country",
            "geo restriction",
            "geo-restriction",
            "geo-restricted",
            "geo restricted",
            "not available in your region",
            "blocked it in your country",
        ],
    ),
    (
        Code::PrivateOrLogin,
        &[
            "private video",
            "this video is private",
            "video is private",
            "account is private",
            "sign in to confirm",
            "login required",
            "log in to",
            "requires authentication",
            "authentication required",
            "members-only",
            "members only",
            "join this channel",
            "only available for registered users",
            "use --cookies",
            // yt-dlp DouyinIE without fresh cookies (PLAN-32D §2: Douyin L1).
            "fresh cookies",
            "--cookies-from-browser",
            "requires a subscription",
            "age-restricted",
            "inappropriate for some users",
        ],
    ),
    (Code::Unsupported, &["unsupported url", "no video formats found", "requested format is not available"]),
    (
        Code::NotFound,
        &[
            "http error 404",
            "http error 410",
            "video unavailable",
            "this video has been removed",
            "video has been removed",
            "does not exist",
            "no longer available",
            "has been deleted",
            "not found",
        ],
    ),
    // A signed CDN link that expired (Douyin) or a refused request. Before
    // Network, which also matches the "Unable to download" prefix.
    (Code::Forbidden, &["http error 403", "403: forbidden"]),
    (
        Code::Network,
        &[
            "unable to download webpage",
            "unable to download json",
            "unable to download api",
            "connection refused",
            "connection reset",
            "connection aborted",
            "timed out",
            "getaddrinfo failed",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
            "network is unreachable",
            "no route to host",
            "winerror 10060]",
            "winerror 10061]",
            "winerror 10054]",
            "winerror 10051]",
            "errno 11001]",
            "ssl:",
            "certificate verify failed",
            "http error 5",
            "remote end closed connection",
            "incompleteread",
            "giving up after",
        ],
    ),
];

/// Picks the line that best explains a failure: the last `ERROR:` line, or
/// else the last non-empty line.
pub fn last_error_line(lines: &[String]) -> Option<&str> {
    lines
        .iter()
        .rev()
        .find(|l| l.trim_start().starts_with("ERROR:"))
        .or_else(|| lines.iter().rev().find(|l| !l.trim().is_empty()))
        .map(|s| s.trim())
}

/// Classifies yt-dlp's stderr into a contract error code. Only `ERROR:` lines
/// are considered when there are any, so a harmless WARNING mentioning
/// "cookies" does not turn a network failure into `private_or_login`.
pub fn classify(stderr_lines: &[String]) -> Code {
    let errors: Vec<String> = stderr_lines
        .iter()
        .filter(|l| l.trim_start().starts_with("ERROR:"))
        .map(|l| l.to_lowercase())
        .collect();
    let haystack: Vec<String> = if errors.is_empty() {
        stderr_lines.iter().map(|l| l.to_lowercase()).collect()
    } else {
        errors
    };
    for (code, needles) in RULES {
        if haystack.iter().any(|l| needles.iter().any(|n| l.contains(n))) {
            return *code;
        }
    }
    Code::Unknown
}

#[cfg(test)]
mod tests {
    use super::*;

    fn c(s: &str) -> Code {
        classify(&s.lines().map(String::from).collect::<Vec<_>>())
    }

    #[test]
    fn classifies_real_ytdlp_messages() {
        // Lines below were produced by yt-dlp 2026.03.17 in the workspace or
        // copied from yt-dlp extractor sources.
        assert_eq!(c("ERROR: [generic] Unable to download webpage: HTTP Error 404: File not found (caused by <HTTPError 404: File not found>)"), Code::NotFound);
        assert_eq!(c("ERROR: [generic] Unable to download webpage: [Errno 111] Connection refused (caused by TransportError('[Errno 111] Connection refused'))"), Code::Network);
        assert_eq!(c("ERROR: [generic] Unable to download webpage: [Errno -2] Name or service not known"), Code::Network);
        assert_eq!(c("ERROR: [generic] Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>"), Code::Network);
        assert_eq!(c("ERROR: Unsupported URL: https://example.com/"), Code::Unsupported);
        assert_eq!(c("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video"), Code::PrivateOrLogin);
        assert_eq!(c("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication."), Code::PrivateOrLogin);
        assert_eq!(c("ERROR: [youtube] abc: The uploader has not made this video available in your country"), Code::GeoBlocked);
        assert_eq!(c("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader"), Code::NotFound);
        assert_eq!(c("ERROR: unable to write data: [Errno 28] No space left on device"), Code::DiskFull);
        assert_eq!(c("ERROR: unable to write data: [WinError 112] There is not enough space on the disk"), Code::DiskFull);
        assert_eq!(c("ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path using --ffmpeg-location"), Code::ToolMissing);
        assert_eq!(c("ERROR: unable to download video data: HTTP Error 403: Forbidden"), Code::Forbidden);
        assert_eq!(c("ERROR: something nobody has seen before"), Code::Unknown);
        // yt-dlp/extractor/tiktok.py DouyinIE
        assert_eq!(c("ERROR: [Douyin] 7311: Fresh cookies (not necessarily logged in) are needed"), Code::PrivateOrLogin);
    }

    #[test]
    fn warnings_do_not_override_errors() {
        let lines = "WARNING: [youtube] Use --cookies for better results\nERROR: [generic] Unable to download webpage: [WinError 10061] No connection could be made";
        assert_eq!(c(lines), Code::Network);
    }

    #[test]
    fn last_error_line_prefers_error() {
        let lines: Vec<String> = vec!["WARNING: x".into(), "ERROR: boom".into(), "trailing".into()];
        assert_eq!(last_error_line(&lines), Some("ERROR: boom"));
        let lines: Vec<String> = vec!["a".into(), "b".into(), "  ".into()];
        assert_eq!(last_error_line(&lines), Some("b"));
    }

    #[test]
    fn serializes_as_contract_shape() {
        let e = CommandError::new(Code::DiskFull, "x");
        assert_eq!(serde_json::to_string(&e).unwrap(), r#"{"code":"disk_full","message":"x"}"#);
        // serde's snake_case and as_str() agree for the newest code too
        let e = CommandError::new(Code::CookieRequired, "x");
        assert_eq!(serde_json::to_string(&e).unwrap(), r#"{"code":"cookie_required","message":"x"}"#);
        assert_eq!(Code::CookieRequired.as_str(), "cookie_required");
    }
}
