"""
Task #6171 — POST /bulk-download with Xiaohongshu / Kuaishou channel links.

Guests are refused before any job row or scan task exists: 401 with the
Vietnamese sign-in message when every link is such a channel, one failed row
per link in a mixed batch. Signed-in users get the scan task as for Douyin.
"""
from __future__ import annotations

import pytest

from tests.test_admin_download_metrics import rc, route  # noqa: F401
from tests.test_platform_quota import IP, TT, _bulk, bulk, signed_in  # noqa: F401

XHS_PROFILE = "https://www.xiaohongshu.com/user/profile/5ff0e6410000000001008400"
KS_PROFILE = "https://www.kuaishou.com/profile/3x984ye63jkct29"
SIGN_IN_XHS = "Bạn cần đăng nhập để tải cả kênh Xiaohongshu. Đăng nhập miễn phí rồi thử lại."


class TestGuest:

    def test_only_china_channels_is_401_and_nothing_created(self, app, bulk, rc):
        r = _bulk(app, [XHS_PROFILE], channel_mode=True)
        assert r.status_code == 401
        assert r.json()["detail"] == SIGN_IN_XHS
        assert bulk["sb"].inserts == [] and bulk["sent"] == []

    def test_profile_link_without_channel_mode_is_still_a_channel(self, app, bulk, rc):
        r = _bulk(app, [KS_PROFILE], channel_mode=False)
        assert r.status_code == 401 and "Kuaishou" in r.json()["detail"]

    def test_mixed_batch_refuses_only_the_china_channel(self, app, bulk, rc):
        r = _bulk(app, [XHS_PROFILE, TT.format(1)])
        assert r.status_code == 200, r.text[:300]
        failed = [row for _, row in bulk["sb"].inserts if row["status"] == "failed"]
        assert [(f["original_url"], f["error_message"]) for f in failed] == [(XHS_PROFILE, SIGN_IN_XHS)]
        assert r.json()["videos_queued"] == 1 and len(bulk["sent"]) == 1

    def test_share_link_that_resolves_to_a_profile_is_refused(self, app, bulk, rc, monkeypatch):
        monkeypatch.setattr("app.utils.link_resolver.resolve_short_url", lambda u: XHS_PROFILE if "xhslink" in u else u)
        r = _bulk(app, ["http://xhslink.com/a/AbCdEf", TT.format(2)])
        assert r.status_code == 200
        failed = [row for _, row in bulk["sb"].inserts if row["status"] == "failed"]
        assert [f["error_message"] for f in failed] == [SIGN_IN_XHS]

    def test_single_note_link_is_not_refused(self, app, bulk, rc):
        note = "https://www.xiaohongshu.com/explore/69d8ab670000000022003dbe"
        r = _bulk(app, [note])
        assert r.status_code == 200 and r.json()["videos_queued"] == 1


class TestSignedIn:

    @pytest.mark.parametrize("url", [XHS_PROFILE, KS_PROFILE])
    def test_scan_task_is_dispatched(self, app, bulk, rc, signed_in, url):
        r = _bulk(app, [url], channel_mode=True, max_videos=100)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["channels_detected"] == 1
        assert bulk["sent"][0]["kwargs"] == {"_requester": "user:u-7"}
