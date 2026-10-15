//! Channel listing through a real browser page (app 0.16.2, owner 15/10/2026).
//!
//! yt-dlp cannot list some channels from the user's machine:
//! - TikTok `/@user`: the profile answers a plain client with a JavaScript
//!   challenge, so yt-dlp fails with "Unable to extract secondary user ID"
//!   (measured 15/10: 0/3 plain, 0–1/3 with --impersonate).
//! - Facebook pages: yt-dlp has no extractor for a page's video list
//!   ("Unsupported URL").
//! A WebView2 page runs the platform's JavaScript like any browser, on the
//! user's own IP. page_window.rs opens it hidden; this module decides which
//! URLs go there, builds the page script (page_listing.js) and turns its
//! answer into a ChannelListing. Measured with Chromium 15/10: TikTok
//! @editerloxinchao 14 videos + secUid, @tiktok 12/12; Facebook NASA logged
//! out 1 video (more with the user's saved Facebook cookies).

use crate::channels::{valid_id, ChannelListing, ChannelVideo};
use crate::validate;
use serde_json::Value;

const SCRIPT: &str = include_str!("page_listing.js");

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    TikTok,
    Facebook,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::TikTok => "tiktok",
            Kind::Facebook => "facebook",
        }
    }
    /// Saved-cookie slug (cookies.rs table).
    pub fn cookie_slug(self) -> &'static str {
        self.as_str()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub kind: Kind,
    /// The page the hidden window opens.
    pub page: url::Url,
}

#[derive(Debug, PartialEq)]
pub enum PageError {
    /// The platform showed a verification / captcha page.
    Verify,
    /// A login wall and no videos (Facebook).
    Login,
    /// The account does not exist / is banned (TikTok statusCode 10202, 10221).
    NotFound,
    /// A private account (TikTok statusCode 10222).
    Private,
    /// The page loaded but listed no video. `sec_uid`: the TikTok channel id
    /// the page still gave, so yt-dlp can try `tiktokuser:<sec_uid>`.
    /// `title` / `thumbnail`: the channel name and avatar the page showed, so
    /// that listing is not titled with the bare secUid (0.16.4).
    Empty { sec_uid: Option<String>, status: i64, title: Option<String>, thumbnail: Option<String> },
    /// The answer was not what the script sends.
    Bad,
}

fn host_of(u: &url::Url) -> String {
    u.host_str().unwrap_or_default().to_ascii_lowercase()
}

fn is_host(h: &str, base: &str) -> bool {
    h == base || h.ends_with(&format!(".{base}"))
}

/// Channel URLs that may be listed through a page. None = leave it to yt-dlp alone.
pub fn target(raw: &str) -> Option<Target> {
    let u = url::Url::parse(raw).ok()?;
    if u.scheme() != "https" && u.scheme() != "http" {
        return None;
    }
    let h = host_of(&u);
    let segs: Vec<&str> = u.path_segments().map(|s| s.filter(|x| !x.is_empty()).collect()).unwrap_or_default();
    if is_host(&h, "tiktok.com") {
        // /@user or /@user/ — not /@user/video/<id>.
        let user = match segs.as_slice() {
            [u] if u.starts_with('@') && u.len() > 1 => *u,
            _ => return None,
        };
        if !user[1..].chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.') {
            return None;
        }
        return url::Url::parse(&format!("https://www.tiktok.com/{user}")).ok().map(|page| Target { kind: Kind::TikTok, page });
    }
    if is_host(&h, "facebook.com") || h == "fb.com" {
        if segs.first() == Some(&"profile.php") {
            let id = u.query_pairs().find(|(k, _)| k == "id").map(|(_, v)| v.to_string())?;
            if !id.chars().all(|c| c.is_ascii_digit()) || id.is_empty() {
                return None;
            }
            return url::Url::parse(&format!("https://www.facebook.com/profile.php?id={id}&sk=videos"))
                .ok()
                .map(|page| Target { kind: Kind::Facebook, page });
        }
        let name = match segs.as_slice() {
            [n] | [n, "videos"] | [n, "reels"] | [n, "videos", ""] => *n,
            _ => return None,
        };
        const NOT_PAGES: &[&str] = &["watch", "reel", "reels", "videos", "groups", "events", "share", "story.php", "photo", "photos", "login", "marketplace", "gaming", "live"];
        if NOT_PAGES.contains(&name) || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-' || c == '_') {
            return None;
        }
        let tab = if segs.get(1) == Some(&"reels") { "reels" } else { "videos" };
        return url::Url::parse(&format!("https://www.facebook.com/{name}/{tab}")).ok().map(|page| Target { kind: Kind::Facebook, page });
    }
    None
}

/// Main-frame navigation the hidden window allows: https on the platform's own hosts.
pub fn navigation_allowed(kind: Kind, u: &url::Url) -> bool {
    if u.as_str() == "about:blank" {
        return true;
    }
    if u.scheme() != "https" {
        return false;
    }
    let h = host_of(u);
    match kind {
        Kind::TikTok => is_host(&h, "tiktok.com"),
        Kind::Facebook => is_host(&h, "facebook.com") || is_host(&h, "fb.com"),
    }
}

/// `show`: the user sees the window and may solve a check there, so the script waits longer.
pub fn page_script(kind: Kind, limit: u32, show: bool) -> String {
    SCRIPT
        .replace("%KIND%", kind.as_str())
        .replace("%LIMIT%", &limit.clamp(1, 500).to_string())
        .replace("%MAXMS%", if show { "140000" } else { "35000" })
        .replace("%SHOW%", if show { "true" } else { "false" })
}

/// Collects the chunked title answer ("VGL:<i>/<n>:<part>").
#[derive(Default)]
pub struct Assembler {
    parts: Vec<Option<String>>,
}

impl Assembler {
    /// Feeds one document title; returns the whole answer once every chunk is in.
    pub fn push(&mut self, title: &str) -> Option<String> {
        let rest = title.strip_prefix("VGL:")?;
        let (head, part) = rest.split_once(':')?;
        let (i, n) = head.split_once('/')?;
        let (i, n): (usize, usize) = (i.parse().ok()?, n.parse().ok()?);
        if n == 0 || n > 400 || i == 0 || i > n {
            return None;
        }
        if self.parts.len() != n {
            self.parts = vec![None; n];
        }
        self.parts[i - 1] = Some(part.to_string());
        if self.parts.iter().all(Option::is_some) {
            Some(self.parts.iter().map(|p| p.as_deref().unwrap_or("")).collect())
        } else {
            None
        }
    }
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::trim).filter(|x| !x.is_empty()).map(String::from)
}

fn cap(t: &str, n: usize) -> String {
    t.chars().take(n).collect()
}

fn https(x: Option<String>) -> Option<String> {
    x.filter(|u| u.len() <= 4096 && u.starts_with("https://") && validate::url(u).is_ok())
}

/// The script's answer → a listing of the channel at `url` (the URL the user gave).
pub fn to_listing(url: &str, t: &Target, answer: &str, limit: u32) -> Result<ChannelListing, PageError> {
    let j: Value = serde_json::from_str(answer).map_err(|_| PageError::Bad)?;
    let meta = j.get("meta").cloned().unwrap_or(Value::Null);
    let status = j.get("status").and_then(Value::as_i64).unwrap_or(0);
    let has_items = j.get("items").and_then(Value::as_array).is_some_and(|a| !a.is_empty());
    if !has_items {
        match status {
            10202 | 10221 => return Err(PageError::NotFound),
            10222 => return Err(PageError::Private),
            _ => {}
        }
    }
    match s(&j, "error").as_deref() {
        Some("verify") => return Err(PageError::Verify),
        Some("login") => return Err(PageError::Login),
        _ => {}
    }
    let mut seen = std::collections::HashSet::new();
    let videos: Vec<ChannelVideo> = j
        .get("items")
        .and_then(Value::as_array)
        .map(|a| a.as_slice())
        .unwrap_or_default()
        .iter()
        .filter_map(|e| {
            let id = s(e, "id").filter(|i| valid_id(i) && i.chars().all(|c| c.is_ascii_digit()))?;
            let vurl = https(s(e, "url"))?;
            let ok_host = url::Url::parse(&vurl).ok().map(|u| navigation_allowed(t.kind, &u)).unwrap_or(false);
            if !ok_host {
                return None;
            }
            Some(ChannelVideo { title: cap(&s(e, "title").unwrap_or_else(|| id.clone()), 500), id, url: vurl, duration: None, upload_date: None, thumbnail: https(s(e, "thumbnail")) })
        })
        .filter(|v| seen.insert(v.id.clone()))
        .take(limit as usize)
        .collect();
    if videos.is_empty() {
        if s(&j, "error").is_none() && j.get("items").is_none() {
            return Err(PageError::Bad);
        }
        let sec_uid = if t.kind == Kind::TikTok { s(&meta, "id").filter(|i| valid_id(i) && i.starts_with("MS4w")) } else { None };
        return Err(PageError::Empty {
            sec_uid,
            status,
            title: s(&meta, "title").map(|x| cap(&x, 500)),
            thumbnail: https(s(&meta, "thumbnail")),
        });
    }
    let path_name = t.page.path().trim_matches('/').split('/').next().unwrap_or("").to_string();
    let channel_id = s(&meta, "id")
        .filter(|i| valid_id(i))
        .unwrap_or_else(|| match t.kind {
            Kind::TikTok => path_name.clone(),
            Kind::Facebook => format!("fb:{}", if path_name == "profile.php" { t.page.query_pairs().find(|(k, _)| k == "id").map(|(_, v)| v.to_string()).unwrap_or_default() } else { path_name.clone() }),
        });
    let channel_id = if valid_id(&channel_id) { channel_id } else { format!("{}:page", t.kind.as_str()) };
    let uploader = s(&meta, "uploader").map(|u| cap(&u, 500));
    let title = s(&meta, "title").map(|x| cap(&x, 500)).or_else(|| uploader.clone()).unwrap_or_else(|| path_name.clone());
    Ok(ChannelListing {
        channel_id,
        url: url.to_string(),
        title,
        platform: t.kind.as_str().into(),
        uploader,
        thumbnail: https(s(&meta, "thumbnail")),
        truncated: videos.len() as u32 >= limit,
        videos,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn targets() {
        let t = target("https://www.tiktok.com/@editerloxinchao").unwrap();
        assert_eq!((t.kind, t.page.as_str()), (Kind::TikTok, "https://www.tiktok.com/@editerloxinchao"));
        assert!(target("https://m.tiktok.com/@a.b_c/").is_some());
        assert!(target("https://www.tiktok.com/@a/video/123").is_none());
        assert!(target("https://www.tiktok.com/@a\"b").is_none());
        assert_eq!(target("https://www.facebook.com/NASA").unwrap().page.as_str(), "https://www.facebook.com/NASA/videos");
        assert_eq!(target("https://facebook.com/NASA/reels").unwrap().page.as_str(), "https://www.facebook.com/NASA/reels");
        assert_eq!(
            target("https://www.facebook.com/profile.php?id=1000123").unwrap().page.as_str(),
            "https://www.facebook.com/profile.php?id=1000123&sk=videos"
        );
        assert!(target("https://www.facebook.com/watch/?v=1").is_none());
        assert!(target("https://www.facebook.com/NASA/videos/123456789").is_none());
        assert!(target("https://www.youtube.com/@x").is_none());
    }

    #[test]
    fn navigation_is_limited_to_the_platform() {
        let u = |s: &str| url::Url::parse(s).unwrap();
        assert!(navigation_allowed(Kind::TikTok, &u("https://www.tiktok.com/@x")));
        assert!(!navigation_allowed(Kind::TikTok, &u("http://www.tiktok.com/@x")));
        assert!(!navigation_allowed(Kind::TikTok, &u("https://evil.com/tiktok.com")));
        assert!(!navigation_allowed(Kind::TikTok, &u("https://nottiktok.com/")));
        assert!(navigation_allowed(Kind::Facebook, &u("https://m.facebook.com/x")));
    }

    #[test]
    fn script_gets_kind_and_limit() {
        let s = page_script(Kind::TikTok, 9999, false);
        assert!(s.contains("const KIND = \"tiktok\"") && s.contains("const LIMIT = 500;"));
        assert!(s.contains("const MAXMS = 35000;") && s.contains("const SHOW = false;"));
        assert!(page_script(Kind::Facebook, 5, true).contains("const SHOW = true;"));
        assert!(!s.contains("%KIND%") && !s.contains("%LIMIT%") && !s.contains("%MAXMS%") && !s.contains("%SHOW%"));
    }

    #[test]
    fn chunks_reassemble_in_any_order() {
        let mut a = Assembler::default();
        assert_eq!(a.push("TikTok - Make Your Day"), None);
        assert_eq!(a.push("VGL:2/2:\"b\"}"), None);
        assert_eq!(a.push("VGL:1/2:{\"a\":"), Some("{\"a\":\"b\"}".into()));
        assert_eq!(a.push("VGL:0/2:x"), None);
    }

    #[test]
    fn listing_from_answer() {
        let t = target("https://www.tiktok.com/@ed").unwrap();
        let ans = r#"{"meta":{"id":"MS4wLjABAAAA_x","title":"Editer","uploader":"ed","thumbnail":"https://p16.tiktokcdn.com/a.jpg"},
            "items":[{"id":"7684631364670426375","url":"https://www.tiktok.com/@ed/video/7684631364670426375","title":"Bài 7","thumbnail":"https://p16.tiktokcdn.com/v.jpg"},
                     {"id":"7684631364670426375","url":"https://www.tiktok.com/@ed/video/7684631364670426375","title":"dup","thumbnail":""},
                     {"id":"1234567","url":"https://evil.com/@ed/video/1234567","title":"x","thumbnail":""},
                     {"id":"12ab","url":"https://www.tiktok.com/@ed/video/12ab","title":"x","thumbnail":""}],"error":null}"#;
        let l = to_listing("https://www.tiktok.com/@ed", &t, ans, 50).unwrap();
        assert_eq!((l.channel_id.as_str(), l.title.as_str(), l.platform.as_str()), ("MS4wLjABAAAA_x", "Editer", "tiktok"));
        assert_eq!(l.videos.len(), 1);
        assert_eq!(l.videos[0].thumbnail.as_deref(), Some("https://p16.tiktokcdn.com/v.jpg"));
        assert!(!l.truncated);
    }

    #[test]
    fn errors_from_answer() {
        let t = target("https://www.facebook.com/NASA").unwrap();
        assert_eq!(to_listing("u", &t, r#"{"error":"login"}"#, 5), Err(PageError::Login));
        assert_eq!(to_listing("u", &t, r#"{"error":"verify"}"#, 5), Err(PageError::Verify));
        assert_eq!(to_listing("u", &t, r#"{"meta":{},"items":[],"error":"empty"}"#, 5), Err(PageError::Empty { sec_uid: None, status: 0, title: None, thumbnail: None }));
        let tt = target("https://www.tiktok.com/@ed").unwrap();
        assert_eq!(
            to_listing("u", &tt, r#"{"meta":{"id":"MS4wLjABAAAA_x","title":"Editer Lỏ","thumbnail":"https://p16.tiktokcdn.com/a.jpg"},"status":0,"items":[],"error":"empty"}"#, 5),
            Err(PageError::Empty {
                sec_uid: Some("MS4wLjABAAAA_x".into()),
                status: 0,
                title: Some("Editer Lỏ".into()),
                thumbnail: Some("https://p16.tiktokcdn.com/a.jpg".into())
            })
        );
        assert_eq!(to_listing("u", &tt, r#"{"meta":{},"status":10202,"items":[],"error":"empty"}"#, 5), Err(PageError::NotFound));
        assert_eq!(to_listing("u", &tt, r#"{"meta":{},"status":10222,"items":[],"error":"empty"}"#, 5), Err(PageError::Private));
        assert_eq!(to_listing("u", &tt, r#"{"meta":{},"status":0,"items":[],"error":"verify"}"#, 5), Err(PageError::Verify));
        assert_eq!(to_listing("u", &t, "not json", 5), Err(PageError::Bad));
        let ok = to_listing("https://www.facebook.com/NASA", &t, r#"{"meta":{"title":"NASA"},"items":[{"id":"2144422086394464","url":"https://www.facebook.com/watch/?v=2144422086394464","title":"Artemis","thumbnail":""}]}"#, 5).unwrap();
        assert_eq!((ok.channel_id.as_str(), ok.platform.as_str(), ok.videos[0].thumbnail.clone()), ("fb:NASA", "facebook", None));
    }
}
