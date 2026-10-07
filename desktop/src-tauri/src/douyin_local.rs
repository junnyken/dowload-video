//! Douyin on the user's machine without the server (app 0.7.2 experiment).
//!
//! yt-dlp's Douyin extractor calls `/aweme/v1/web/aweme/detail/` without the
//! page's JS signature (a_bogus) and gets an empty answer ("Fresh cookies are
//! needed"), whatever cookies it is given. So a HIDDEN, InPrivate webview
//! (douyin_window.rs) opens the video page with the user's saved Douyin
//! cookies and lets Douyin's own JS do the signed request. A fixed script
//! (`page_script`) then finds the video's direct media link and reports it
//! through `document.title` (`VGRES:` / `VGERR:` + URL-encoded text): the page
//! never gets IPC. Everything the title carries is untrusted (the page can set
//! any title) and is checked here before the link reaches yt-dlp.
//!
//! This file is plain Rust (no Tauri) so `cargo test` covers it on Linux.

use crate::cookies::domain_matches;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Hosts the hidden window may load (main frame) and whose links count as Douyin.
pub const PAGE_HOSTS: &[&str] = &["douyin.com", "iesdouyin.com", "douyinstatic.com"];
/// Hosts a pasted video link may have.
const LINK_HOSTS: &[&str] = &["douyin.com", "iesdouyin.com"];

pub const REFERER: &str = "https://www.douyin.com/";
/// Same desktop UA the server uses for Douyin direct links; used only when the
/// webview's own `navigator.userAgent` did not come back usable.
pub const FALLBACK_UA: &str =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

pub const RES_PREFIX: &str = "VGRES:";
pub const ERR_PREFIX: &str = "VGERR:";

/// What the hidden window has to open.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    /// Numeric aweme id, read from the pasted link.
    Video(String),
    /// v.douyin.com/<code>: the webview follows the redirect; the id is read
    /// from the URL it lands on (`video_id_of`).
    Short(url::Url),
}

fn valid_id(s: &str) -> bool {
    (8..=25).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit())
}

fn douyin_link(u: &url::Url, hosts: &[&str]) -> bool {
    matches!(u.scheme(), "https" | "http")
        && u.username().is_empty()
        && u.password().is_none()
        && u.host_str().is_some_and(|h| hosts.iter().any(|d| domain_matches(h, d)))
}

/// Video id of douyin.com/video/<id>, douyin.com/...?modal_id=<id>,
/// iesdouyin.com/share/video/<id>, on a Douyin host only.
pub fn video_id_of(u: &url::Url) -> Option<String> {
    if !douyin_link(u, LINK_HOSTS) {
        return None;
    }
    let segs: Vec<&str> = u.path_segments().map(|s| s.filter(|x| !x.is_empty()).collect()).unwrap_or_default();
    let from_path = match segs.as_slice() {
        ["video", id, ..] | ["share", "video", id, ..] => Some(*id),
        _ => None,
    };
    if let Some(id) = from_path.filter(|id| valid_id(id)) {
        return Some(id.to_string());
    }
    u.query_pairs().find(|(k, _)| k == "modal_id").map(|(_, v)| v.into_owned()).filter(|v| valid_id(v))
}

/// The pasted link -> what to open. Profiles and other pages are refused.
pub fn target(raw: &str) -> Result<Target, String> {
    if raw.len() > 4096 {
        return Err("URL is too long".into());
    }
    let u = url::Url::parse(raw.trim()).map_err(|_| "not a valid URL".to_string())?;
    if !douyin_link(&u, LINK_HOSTS) {
        return Err("not a Douyin link".into());
    }
    if let Some(id) = video_id_of(&u) {
        return Ok(Target::Video(id));
    }
    let short_code = u
        .path_segments()
        .map(|s| s.filter(|x| !x.is_empty()).collect::<Vec<_>>())
        .is_some_and(|s| s.len() == 1 && s[0].len() <= 32 && s[0].chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'));
    if u.host_str().is_some_and(|h| h.eq_ignore_ascii_case("v.douyin.com")) && short_code {
        let mut s = u.clone();
        s.set_scheme("https").map_err(|_| "not a valid URL".to_string())?;
        s.set_fragment(None);
        return Ok(Target::Short(s));
    }
    Err("this Douyin link is not a single video".into())
}

/// The page the hidden window reads the video from.
pub fn video_page(id: &str) -> url::Url {
    debug_assert!(valid_id(id));
    url::Url::parse(&format!("https://www.douyin.com/video/{id}")).expect("static URL")
}

/// Main-frame navigations of the hidden window: about:blank (its first page),
/// otherwise https on a Douyin host only.
pub fn navigation_allowed(u: &url::Url) -> bool {
    if u.as_str() == "about:blank" {
        return true;
    }
    u.scheme() == "https" && douyin_link(u, PAGE_HOSTS)
}

// ---------------------------------------------------------------- page scripts

/// Runs before the page's own scripts (initialization script): keeps a copy
/// of the page's own signed `aweme/detail` answers, by aweme id (at most 20).
/// Read-only wrappers; the page's requests go out unchanged.
pub const INIT_SCRIPT: &str = r#"(function () {
  if (window.__vgHook) return; window.__vgHook = 1;
  var box = {}, n = 0;
  try { Object.defineProperty(window, '__vgDetails', { value: box }); } catch (e) { return; }
  function keep(j) { try { var d = j && j.aweme_detail; if (d && d.aweme_id != null && n < 20) { box[String(d.aweme_id)] = d; n++; } } catch (e) {} }
  function hit(u) { return String(u || '').indexOf('/aweme/v1/web/aweme/detail') >= 0; }
  try {
    var of = window.fetch;
    if (typeof of === 'function') window.fetch = function (input) {
      var u = typeof input === 'string' ? input : (input && input.url) || '';
      var p = of.apply(this, arguments);
      if (hit(u)) p.then(function (r) { try { r.clone().json().then(keep, function () {}); } catch (e) {} }, function () {});
      return p;
    };
  } catch (e) {}
  try {
    var oo = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (m, u) {
      if (hit(u)) this.addEventListener('load', function () {
        try { keep(this.responseType === 'json' ? this.response : JSON.parse(this.responseText)); } catch (e) {}
      });
      return oo.apply(this, arguments);
    };
  } catch (e) {}
})();"#;

/// Evaluated by Rust every few seconds; runs once per document. Polls up to
/// 20 s for the video data: (1) the captured aweme/detail answer, (2)
/// `<script id="RENDER_DATA">` (URL-encoded JSON), (3) `window._ROUTER_DATA`,
/// (4) a `<video>`/`<source>` with a non-blob https src. Then reports
/// `VGRES:` + encodeURIComponent(JSON {u, title, author, dur, ua}) or
/// `VGERR:<reason>` in document.title.
const PAGE_SCRIPT: &str = r#"(function () {
  var ID = '__VG_ID__';
  if (!/(^|\.)douyin\.com$/.test(location.hostname) || location.href.indexOf(ID) < 0) return;
  if (window.__vgRun) return; window.__vgRun = 1;
  var t0 = Date.now();
  function s(v, n) { return typeof v === 'string' ? v.slice(0, n) : ''; }
  function abs(x) {
    if (typeof x !== 'string' || !x || x.indexOf('blob:') === 0) return '';
    try {
      var u = new URL(x, 'https://www.douyin.com/');
      if (u.protocol === 'http:') u.protocol = 'https:';
      return u.protocol === 'https:' ? u.href : '';
    } catch (e) { return ''; }
  }
  function find(o, d) {
    if (!o || typeof o !== 'object' || d > 14) return null;
    var aid = o.awemeId != null ? o.awemeId : o.aweme_id;
    if (aid != null && String(aid) === ID && o.video && typeof o.video === 'object') return o;
    var ks = Object.keys(o);
    for (var i = 0; i < ks.length && i < 500; i++) { var r = find(o[ks[i]], d + 1); if (r) return r; }
    return null;
  }
  function links(v) {
    var c = [];
    function add(x) { if (typeof x === 'string') c.push(x); else if (x && typeof x.src === 'string') c.push(x.src); }
    function addr(a) {
      if (!a) return;
      if (Array.isArray(a)) a.forEach(add);
      else if (Array.isArray(a.url_list)) a.url_list.forEach(add);
      else if (Array.isArray(a.urlList)) a.urlList.forEach(add);
    }
    addr(v.play_addr); addr(v.playAddr); addr(v.play_addr_h264); addr(v.playAddrH264);
    add(v.playApi);
    var out = [];
    c.forEach(function (x) { var u = abs(x); if (u && out.indexOf(u) < 0) out.push(u); });
    var clean = out.filter(function (u) { return u.indexOf('/playwm/') < 0; });
    return clean.length ? clean : out;
  }
  function fromAweme(a) {
    var u = links(a.video)[0];
    if (!u) return null;
    var au = a.author || a.authorInfo || {};
    var ms = typeof a.duration === 'number' ? a.duration : (typeof a.video.duration === 'number' ? a.video.duration : 0);
    return { u: u, title: s(a.desc, 300), author: s(au.nickname, 100), dur: ms > 1000 ? Math.round(ms / 1000) : ms };
  }
  function fromVideoTag() {
    var vs = document.querySelectorAll('video');
    for (var i = 0; i < vs.length; i++) {
      var cand = [vs[i].currentSrc, vs[i].src];
      var ss = vs[i].querySelectorAll('source');
      for (var j = 0; j < ss.length; j++) cand.push(ss[j].src);
      for (var k = 0; k < cand.length; k++) {
        var u = abs(cand[k]);
        if (u) return { u: u, title: s(document.title, 300).replace(/\s*-\s*抖音\s*$/, ''), author: '', dur: Math.round(vs[i].duration) || 0 };
      }
    }
    return null;
  }
  function look() {
    var a = window.__vgDetails && window.__vgDetails[ID];
    var r = a && fromAweme(a);
    if (r) return r;
    var el = document.getElementById('RENDER_DATA');
    if (el && el.textContent) {
      try { a = find(JSON.parse(decodeURIComponent(el.textContent)), 0); r = a && fromAweme(a); if (r) return r; } catch (e) {}
    }
    try { a = find(window._ROUTER_DATA, 0); r = a && fromAweme(a); if (r) return r; } catch (e) {}
    return Date.now() - t0 > 8000 ? fromVideoTag() : null;
  }
  function report(t) {
    var n = 0;
    (function set() { if (document.title !== t) document.title = t; if (++n < 6) setTimeout(set, 700); })();
  }
  (function tick() {
    var r = null;
    try { r = look(); } catch (e) {}
    if (r) { r.ua = s(navigator.userAgent, 512); report('VGRES:' + encodeURIComponent(JSON.stringify(r))); return; }
    if (Date.now() - t0 > 20000) {
      var verify = document.querySelector('iframe[src*="verify"],iframe[src*="captcha"],#captcha_container,#captcha-verify-image');
      report('VGERR:' + (verify ? 'verify' : 'not_found'));
      return;
    }
    setTimeout(tick, 500);
  })();
})();"#;

fn with_id(script: &str, id: &str) -> String {
    // `id` is digits only (valid_id), so it cannot break out of the JS string.
    assert!(valid_id(id), "video id must be digits");
    script.replace("__VG_ID__", id)
}

pub fn page_script(id: &str) -> String {
    with_id(PAGE_SCRIPT, id)
}

// ---------------------------------------------------------------- result

#[derive(Deserialize)]
struct Payload {
    u: String,
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    author: Option<String>,
    #[serde(default)]
    dur: Option<f64>,
    #[serde(default)]
    ua: Option<String>,
}

/// What `douyin_resolve_local` returns to the webview.
#[derive(Serialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Resolved {
    pub id: String,
    pub url: String,
    pub title: String,
    pub author: Option<String>,
    pub duration_sec: Option<u32>,
    /// Referer + User-Agent for yt-dlp (validate::download_header names).
    pub headers: BTreeMap<String, String>,
}

/// Why the page could not give a link (short, fixed vocabulary).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PageError {
    /// Douyin showed a verification (captcha) challenge.
    Verify,
    NotFound,
    /// Anything else, including a malformed or unsafe VGRES payload.
    Bad,
}

/// `%XX` decoding of an encodeURIComponent string; None when malformed or not UTF-8.
fn percent_decode(s: &str) -> Option<String> {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' {
            let h = s.get(i + 1..i + 3)?;
            out.push(u8::from_str_radix(h, 16).ok()?);
            i += 3;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

fn clean_text(s: &str, max_chars: usize) -> String {
    s.chars().filter(|c| !c.is_control()).take(max_chars).collect::<String>().trim().to_string()
}

/// A document title from the hidden window: None when it is not ours (the
/// page's own titles), otherwise the checked result.
pub fn parse_title(title: &str, id: &str) -> Option<Result<Resolved, PageError>> {
    if let Some(reason) = title.strip_prefix(ERR_PREFIX) {
        return Some(Err(match reason {
            "verify" => PageError::Verify,
            "not_found" => PageError::NotFound,
            _ => PageError::Bad,
        }));
    }
    let enc = title.strip_prefix(RES_PREFIX)?;
    if enc.len() > 32 * 1024 {
        return Some(Err(PageError::Bad));
    }
    Some(finish(enc, id).ok_or(PageError::Bad))
}

fn finish(enc: &str, id: &str) -> Option<Resolved> {
    let p: Payload = serde_json::from_str(&percent_decode(enc)?).ok()?;
    let url = crate::validate::media_url(&p.u).ok()?;
    let ua = p
        .ua
        .as_deref()
        .and_then(|ua| crate::validate::download_header("User-Agent", ua).ok())
        .map(|(_, v)| v)
        .unwrap_or_else(|| FALLBACK_UA.to_string());
    let title = clean_text(p.title.as_deref().unwrap_or_default(), 200);
    let author = Some(clean_text(p.author.as_deref().unwrap_or_default(), 100)).filter(|a| !a.is_empty());
    let duration_sec = p.dur.filter(|d| d.is_finite() && *d >= 1.0 && *d <= 86_400.0).map(|d| d.round() as u32);
    let mut headers = BTreeMap::new();
    headers.insert("Referer".to_string(), REFERER.to_string());
    headers.insert("User-Agent".to_string(), ua);
    Some(Resolved { id: id.to_string(), url, title, author, duration_sec, headers })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn enc(s: &str) -> String {
        // encodeURIComponent: keep A-Z a-z 0-9 - _ . ! ~ * ' ( )
        s.bytes()
            .map(|b| {
                if b.is_ascii_alphanumeric() || b"-_.!~*'()".contains(&b) {
                    (b as char).to_string()
                } else {
                    format!("%{b:02X}")
                }
            })
            .collect()
    }

    #[test]
    fn link_forms() {
        let id = "7311234567890123456";
        for ok in [
            format!("https://www.douyin.com/video/{id}"),
            format!("https://www.douyin.com/video/{id}?previous_page=app"),
            format!("https://douyin.com/video/{id}/"),
            format!("http://www.douyin.com/video/{id}"),
            format!("https://www.douyin.com/jingxuan?modal_id={id}"),
            format!("https://www.douyin.com/user/MS4wLjABAAAA?modal_id={id}"),
            format!("https://www.iesdouyin.com/share/video/{id}/?region=CN"),
            format!("  https://m.douyin.com/share/video/{id}  "),
        ] {
            assert_eq!(target(&ok), Ok(Target::Video(id.into())), "{ok}");
        }
        match target("https://v.douyin.com/iRNBho6u/") {
            Ok(Target::Short(u)) => assert_eq!(u.as_str(), "https://v.douyin.com/iRNBho6u/"),
            other => panic!("{other:?}"),
        }
        assert!(matches!(target("http://v.douyin.com/abc_-1#x"), Ok(Target::Short(u)) if u.as_str() == "https://v.douyin.com/abc_-1"));
        for bad in [
            "https://www.douyin.com/user/MS4wLjABAAAA",
            "https://www.douyin.com/",
            "https://www.douyin.com/video/abc",
            "https://www.douyin.com/video/123", // too short to be an aweme id
            "https://www.douyin.com/?modal_id=12x45678901",
            "https://evil-douyin.com/video/7311234567890123456",
            "https://douyin.com.evil.com/video/7311234567890123456",
            "https://www.douyin.com@evil.com/video/7311234567890123456",
            "https://u@www.douyin.com/video/7311234567890123456",
            "https://v.douyin.com/a/b/",
            "https://v.douyin.com/",
            "https://x.douyin.com/abc/",
            "ftp://www.douyin.com/video/7311234567890123456",
            "https://www.tiktok.com/@a/video/7311234567890123456",
            "not a url",
        ] {
            assert!(target(bad).is_err(), "{bad}");
        }
        assert!(target(&format!("https://www.douyin.com/video/{}", "1".repeat(5000))).is_err());
        assert_eq!(video_page(id).as_str(), format!("https://www.douyin.com/video/{id}"));
    }

    #[test]
    fn redirect_target_gives_the_id() {
        let u = url::Url::parse("https://www.iesdouyin.com/share/video/7311234567890123456/?region=CN&mid=1").unwrap();
        assert_eq!(video_id_of(&u).as_deref(), Some("7311234567890123456"));
        let u = url::Url::parse("https://www.iesdouyin.com/share/user/MS4wLjABAAAA?x=1").unwrap();
        assert_eq!(video_id_of(&u), None);
        let u = url::Url::parse("https://evil.com/share/video/7311234567890123456/").unwrap();
        assert_eq!(video_id_of(&u), None);
    }

    #[test]
    fn hidden_window_navigation_rule() {
        for ok in [
            "about:blank",
            "https://www.douyin.com/video/1",
            "https://v.douyin.com/abc/",
            "https://www.iesdouyin.com/share/video/1/",
            "https://lf-security.douyinstatic.com/x",
            "https://sso.douyin.com/passport",
        ] {
            assert!(navigation_allowed(&url::Url::parse(ok).unwrap()), "{ok}");
        }
        for bad in [
            "http://www.douyin.com/",
            "https://evil.com/",
            "https://douyin.com.evil.com/",
            "https://evil-douyin.com/",
            "https://u:p@www.douyin.com/",
            "about:srcdoc",
            "data:text/html,x",
            "file:///C:/x",
            "javascript:alert(1)",
            "tauri://localhost/",
            "http://tauri.localhost/",
            "https://127.0.0.1/",
        ] {
            assert!(!navigation_allowed(&url::Url::parse(bad).unwrap()), "{bad}");
        }
    }

    #[test]
    fn scripts_get_the_id_and_nothing_else() {
        let id = "7311234567890123456";
        let p = page_script(id);
        let i = INIT_SCRIPT.to_string();
        assert!(!p.contains("__VG_ID__") && !i.contains("__VG_ID__"));
        assert!(p.contains(&format!("var ID = '{id}';")));
        assert!(p.contains("VGRES:") && p.contains("VGERR:"));
        // The scripts never touch cookies or the app's IPC.
        for s in [&p, &i] {
            assert!(!s.contains("document.cookie") && !s.contains("__TAURI") && !s.contains("ipc"));
        }
    }

    #[test]
    #[should_panic]
    fn scripts_refuse_a_non_numeric_id() {
        let _ = page_script("1'; alert(1); '");
    }

    #[test]
    fn title_parsing() {
        let id = "7311234567890123456";
        assert_eq!(parse_title("抖音-记录美好生活", id), None);
        assert_eq!(parse_title("", id), None);
        assert_eq!(parse_title("VGERR:verify", id), Some(Err(PageError::Verify)));
        assert_eq!(parse_title("VGERR:not_found", id), Some(Err(PageError::NotFound)));
        assert_eq!(parse_title("VGERR:<script>", id), Some(Err(PageError::Bad)));

        let json = r#"{"u":"https://v26-web.douyinvod.com/a/b/video/tos/cn/x/?a=6383&br=1&l=2","title":"Mèo con 小猫 \u0007ok","author":"Tác giả","dur":15.4,"ua":"Mozilla/5.0 (Windows NT 10.0) Edg/140"}"#;
        let r = parse_title(&format!("VGRES:{}", enc(json)), id).unwrap().unwrap();
        assert_eq!(r.id, id);
        assert_eq!(r.url, "https://v26-web.douyinvod.com/a/b/video/tos/cn/x/?a=6383&br=1&l=2");
        assert_eq!(r.title, "Mèo con 小猫 ok");
        assert_eq!(r.author.as_deref(), Some("Tác giả"));
        assert_eq!(r.duration_sec, Some(15));
        assert_eq!(r.headers.get("Referer").map(String::as_str), Some(REFERER));
        assert_eq!(r.headers.get("User-Agent").map(String::as_str), Some("Mozilla/5.0 (Windows NT 10.0) Edg/140"));
        assert_eq!(r.headers.len(), 2);
        let wire = serde_json::to_string(&r).unwrap();
        assert!(wire.contains("\"durationSec\":15") && wire.contains("\"User-Agent\""));

        // Missing / unusable optional fields -> defaults; a bad UA falls back.
        let r = parse_title(&format!("VGRES:{}", enc(r#"{"u":"https://v3-web.douyinvod.com/x.mp4","dur":-1,"ua":"a\r\nX-Evil: 1"}"#)), id)
            .unwrap()
            .unwrap();
        assert_eq!((r.title.as_str(), r.author.clone(), r.duration_sec), ("", None, None));
        assert_eq!(r.headers["User-Agent"], FALLBACK_UA);
    }

    #[test]
    fn title_with_an_unsafe_link_is_refused() {
        let id = "7311234567890123456";
        for u in [
            "http://v26-web.douyinvod.com/x.mp4",
            "https://127.0.0.1:8080/x",
            "https://localhost/x",
            "https://192.168.0.1/x",
            "blob:https://www.douyin.com/abc",
            "file:///C:/Windows/win.ini",
            "",
        ] {
            let json = serde_json::json!({ "u": u }).to_string();
            assert_eq!(parse_title(&format!("VGRES:{}", enc(&json)), id), Some(Err(PageError::Bad)), "{u}");
        }
        for junk in ["VGRES:", "VGRES:%E0%A4%A", "VGRES:%ZZ", "VGRES:not-json", "VGRES:%7B%7D", "VGRES:%5B1%5D"] {
            assert_eq!(parse_title(junk, id), Some(Err(PageError::Bad)), "{junk}");
        }
        let long = format!("VGRES:{}", "a".repeat(40 * 1024));
        assert_eq!(parse_title(&long, id), Some(Err(PageError::Bad)));
    }
}
