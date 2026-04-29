/**
 * VidGrab Interceptor — chạy trong MAIN world (cùng context với Douyin JS)
 * Bắt chặn response từ Douyin API trước khi trang xử lý.
 *
 * Endpoint chính: /aweme/v1/web/aweme/post/
 * Response chứa aweme_list[] với đầy đủ metadata + video URL không watermark.
 */
(function () {
  if (window.__vg_interceptor) return;
  window.__vg_interceptor = true;

  const DOUYIN_ENDPOINTS = [
    '/aweme/v1/web/aweme/post/',       // video đăng của user
    '/aweme/v1/web/aweme/favorite/',   // video đã thích
    '/aweme/v1/web/search/item/',      // kết quả tìm kiếm
    '/aweme/v1/web/aweme/listv2/',     // danh sách tổng hợp
  ];

  function isDouyinAPI(url) {
    try {
      const u = typeof url === 'string' ? url : url?.url || '';
      return DOUYIN_ENDPOINTS.some((ep) => u.includes(ep));
    } catch {
      return false;
    }
  }

  function extractAndBroadcast(url, json) {
    const list = json?.aweme_list;
    if (!Array.isArray(list) || list.length === 0) return;

    const videos = list.map((aweme) => {
      const vid = aweme.video || {};
      const playUrls = vid.play_addr?.url_list || [];
      const noWmUrls = vid.play_addr_h264?.url_list || playUrls;

      // /playwm/ → /play/ xoá watermark
      const rawUrl = noWmUrls[0] || playUrls[0] || '';
      const directUrl = rawUrl.replace('/playwm/', '/play/');

      return {
        aweme_id: aweme.aweme_id,
        desc: aweme.desc || '',
        create_time: aweme.create_time || 0,
        canonical_url: `https://www.douyin.com/video/${aweme.aweme_id}`,
        direct_url: directUrl,
        thumbnail: vid.cover?.url_list?.[0] || vid.origin_cover?.url_list?.[0] || '',
        duration: Math.round((vid.duration || 0) / 1000),
        views: aweme.statistics?.play_count || 0,
        likes: aweme.statistics?.digg_count || 0,
        author_uid: aweme.author?.uid || '',
        author_name: aweme.author?.nickname || '',
      };
    });

    window.postMessage(
      {
        __vg_source: 'interceptor',
        type: 'VG_DOUYIN_API',
        videos,
        has_more: json.has_more,
        max_cursor: json.max_cursor,
        endpoint: typeof url === 'string' ? url : url?.url || '',
      },
      '*'
    );
  }

  // ── Patch window.fetch ────────────────────────────────────────────
  const _origFetch = window.fetch;

  window.fetch = async function (...args) {
    const response = await _origFetch.apply(this, args);

    if (isDouyinAPI(args[0])) {
      const clone = response.clone();
      clone
        .json()
        .then((json) => extractAndBroadcast(args[0], json))
        .catch(() => {});
    }

    return response;
  };

  // ── Patch XMLHttpRequest (fallback) ───────────────────────────────
  const _origOpen = XMLHttpRequest.prototype.open;
  const _origSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__vg_url = typeof url === 'string' ? url : '';
    return _origOpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener('load', function () {
      if (!isDouyinAPI(this.__vg_url)) return;
      try {
        const json = JSON.parse(this.responseText);
        extractAndBroadcast(this.__vg_url, json);
      } catch {}
    });
    return _origSend.apply(this, args);
  };
})();
