//! Input validation for values that come from the webview.

pub fn url(raw: &str) -> Result<String, String> {
    if raw.len() > 2048 {
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
    fn format_validation() {
        assert!(format_id("137+140").is_ok());
        assert!(format_id("bv*+ba/b").is_ok());
        assert!(format_id("best[height<=720]").is_ok());
        assert!(format_id("a;rm -rf /").is_err());
        assert!(format_id("").is_err());
    }
}
