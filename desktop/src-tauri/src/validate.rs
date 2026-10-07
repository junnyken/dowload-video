//! Input validation for values that come from the webview.

pub fn url(raw: &str) -> Result<String, String> {
    if raw.len() > 4096 { // signed CDN links (Douyin) can run past 2 KB
        return Err("URL is too long".into());
    }
    let url = url::Url::parse(raw.trim()).map_err(|_| "not a valid URL".to_string())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("only http(s) URLs are allowed".into());
    }
    Ok(url.to_string())
}

/// yt-dlp format selectors look like `137+140`, `bv*+ba/b`, `best[height<=720]`.
/// Allow that alphabet only; the value is passed as a separate argv item after
/// `-f`, so it can never be read as another option.
pub fn format_id(f: &str) -> Result<(), String> {
    let ok = !f.is_empty()
        && f.len() <= 64
        && f.chars().all(|c| c.is_ascii_alphanumeric() || "+-_/.*[]<>=:,".contains(c));
    if ok { Ok(()) } else { Err("invalid format id".into()) }
}

/// Request headers the webview may ask yt-dlp to send (Douyin direct links
/// need the Referer / User-Agent the server resolved them with). Names are
/// allowlisted and returned in canonical case; values are single-line text.
pub fn download_header(name: &str, value: &str) -> Result<(String, String), String> {
    let canonical = match name.trim().to_ascii_lowercase().as_str() {
        "referer" => "Referer",
        "user-agent" => "User-Agent",
        _ => return Err("header not allowed".into()),
    };
    let v = value.trim();
    if v.is_empty() || v.len() > 512 || v.chars().any(|c| c.is_control()) {
        return Err("invalid header value".into());
    }
    if canonical == "Referer" && url(v).is_err() {
        return Err("invalid referer".into());
    }
    Ok((canonical.to_string(), v.to_string()))
}

/// File-name parts for a download whose URL carries no useful name.
pub fn file_id(id: &str) -> Result<(), String> {
    let ok = !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
    if ok { Ok(()) } else { Err("invalid file id".into()) }
}

/// Hosts `open_url` may open in the browser (sign-up, password reset,
/// installer download). Exact match, https only, default port, no userinfo.
pub const OPEN_URL_HOSTS: &[&str] = &["dvid.vibe1.tinhgon.xyz", "dvid-api.vibe1.tinhgon.xyz"];

pub fn open_url(raw: &str) -> Result<String, String> {
    if raw.len() > 4096 { // signed CDN links (Douyin) can run past 2 KB
        return Err("URL is too long".into());
    }
    let u = url::Url::parse(raw.trim()).map_err(|_| "not a valid URL".to_string())?;
    let host_ok = u.host_str().is_some_and(|h| OPEN_URL_HOSTS.contains(&h));
    if u.scheme() != "https" || !host_ok || u.port().is_some() || !u.username().is_empty() || u.password().is_some() {
        return Err("this link is not allowed".into());
    }
    Ok(u.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_validation() {
        assert!(url("https://www.tiktok.com/@a/video/1").is_ok());
        assert!(url("http://example.com/x").is_ok());
        assert!(url("file:///etc/passwd").is_err());
        assert!(url("--exec calc").is_err());
        assert!(url("javascript:alert(1)").is_err());
    }

    #[test]
    fn open_url_allowlist() {
        assert!(open_url("https://dvid.vibe1.tinhgon.xyz/register").is_ok());
        assert!(open_url("https://dvid-api.vibe1.tinhgon.xyz/download/VidGrab_0.2.0_x64-setup.exe").is_ok());
        for bad in [
            "http://dvid.vibe1.tinhgon.xyz/",
            "https://evil.com/",
            "https://dvid.vibe1.tinhgon.xyz.evil.com/",
            "https://evil.com@dvid.vibe1.tinhgon.xyz/",
            "https://user:pw@dvid.vibe1.tinhgon.xyz/",
            "https://dvid.vibe1.tinhgon.xyz:8443/",
            "file:///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "ms-settings:",
            "C:\\Windows\\notepad.exe",
        ] {
            assert!(open_url(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn download_header_validation() {
        assert_eq!(download_header("referer", "https://www.douyin.com/").unwrap(), ("Referer".into(), "https://www.douyin.com/".into()));
        assert_eq!(download_header("User-Agent", "Mozilla/5.0 (X)").unwrap().0, "User-Agent");
        assert!(download_header("Cookie", "a=b").is_err());
        assert!(download_header("Authorization", "Bearer x").is_err());
        assert!(download_header("User-Agent", "a\r\nX: y").is_err());
        assert!(download_header("Referer", "not a url").is_err());
        assert!(download_header("User-Agent", "").is_err());
        assert!(download_header("User-Agent", &"a".repeat(513)).is_err());
        assert!(file_id("7311_a-B").is_ok());
        assert!(file_id("a b").is_err());
        assert!(file_id("").is_err());
    }

    #[test]
    fn format_validation() {
        assert!(format_id("137+140").is_ok());
        assert!(format_id("bv*+ba/b").is_ok());
        assert!(format_id("best[height<=720]").is_ok());
        // Channel downloads pass formats::preset_selector output; keep it valid.
        assert!(format_id("bv*[height<=1080]+ba/b[height<=1080]").is_ok());
        assert!(format_id("bv*[height<=1920]+ba/b[height<=1920]").is_ok());
        assert!(format_id("bv*[height<=2160]+ba/b[height<=2160]/b").is_ok()); // longest preset incl. the /b fallback
        assert!(format_id("a;rm -rf /").is_err());
        assert!(format_id("").is_err());
    }
}
