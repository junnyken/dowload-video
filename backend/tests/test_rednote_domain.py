"""rednote.com (international Xiaohongshu domain) is served as Xiaohongshu."""
import pytest

from app.core.url_normalizer import extract_share_url, normalize, rewrite_rednote
from app.core.platform_key import platform_key
from app.services.china_platforms.adapters.xiaohongshu import XiaohongshuAdapter

NOTE = "6411cf99000000001300b6d9"


@pytest.mark.parametrize("raw, expected", [
    (f"https://www.rednote.com/explore/{NOTE}?xsec_token=AB=&xsec_source=pc_feed",
     f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=AB=&xsec_source=pc_feed"),
    (f"http://rednote.com/discovery/item/{NOTE}",
     f"https://www.xiaohongshu.com/discovery/item/{NOTE}"),
    (f"Xem nè https://www.rednote.com/explore/{NOTE}?xsec_token=Z， 复制",
     f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=Z"),
])
def test_rednote_rewritten_to_xiaohongshu(raw, expected):
    out = extract_share_url(raw)
    assert out == expected
    assert platform_key(out) == "xiaohongshu"
    assert XiaohongshuAdapter().matches(out)


@pytest.mark.parametrize("url", [
    f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=AB=",
    "http://xhslink.com/a/AbCd123",
    f"https://notrednote.com/explore/{NOTE}",
    f"https://example.com/?u=https://rednote.com/explore/{NOTE}",
])
def test_other_urls_untouched(url):
    assert rewrite_rednote(url) == url
    assert extract_share_url(url) == url


def test_normalize_scheme_less_rednote():
    assert normalize(f"rednote.com/explore/{NOTE}").canonical_url == \
        f"https://www.xiaohongshu.com/explore/{NOTE}"
