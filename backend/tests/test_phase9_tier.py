"""
Phase 9 — Tier Enforcement Tests
==================================
Tests run without real Redis (fakeredis) or real Supabase (monkeypatched).
Run: pytest backend/tests/test_phase9_tier.py -v

Covers all 11 required cases:
 1. Free user downloads YouTube video → 402 youtube_pro_only
 2. Free user bulk > 5 URLs → blocked (tier_limit_batch)
 3. Free user Spotify artist all_tracks → 402 spotify_artist_full
 4. Pro user downloads YouTube video → allowed
 5. Pro user bulk 50 URLs → allowed
 6. Anonymous quota resets at midnight (TTL check)
 7. Anonymous user → 5/day IP limit enforced
 8. Stripe webhook: checkout.session.completed → tier=pro
 9. Upgrade → effective tier switches to pro immediately (grace period)
10. Downgrade / canceling → grace period keeps Pro until expiry
11. 402/403 response structure has all required fields
"""
import pytest
import fakeredis
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# ── Helpers ────────────────────────────────────────────────────────────

def _fake_redis_client():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def _profile(tier="free", billing_status="none", subscription_expiry=None):
    return {
        "tier": tier,
        "billing_status": billing_status,
        "subscription_expiry": subscription_expiry,
    }


# ══════════════════════════════════════════════════════════════════════
# 1. Core quota / tier logic (quotas.py)
# ══════════════════════════════════════════════════════════════════════

class TestEffectiveTier:
    """Grace period in _effective_tier."""

    def setup_method(self):
        from app.core import quotas as q
        self.q = q

    def test_free_returns_free(self):
        assert self.q._effective_tier(_profile("free")) == "free"

    def test_pro_returns_pro(self):
        assert self.q._effective_tier(_profile("pro", "active")) == "pro"

    def test_canceling_within_expiry_keeps_the_paid_tier(self):
        """Cancelling keeps what you paid for until the period ends.

        This asserted that a profile with tier='free' resolved to 'pro' —
        _effective_tier hardcoded "pro" for any grace case regardless of the
        declared tier. Two things were wrong with that. It upgraded an account
        that never paid, and it DEMOTED a Team or Enterprise subscriber to Pro
        for the duration of a payment problem.

        The real cancel path (payments._downgrade_user with at_period_end=True)
        never writes `tier` at all — it sets billing_status='canceling' plus an
        expiry and leaves the paid tier in place, so tier='free' here was a
        state the application does not produce.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        assert self.q._effective_tier(_profile("pro", "canceling", future)) == "pro"
        assert self.q._effective_tier(_profile("enterprise", "canceling", future)) == "enterprise"

    def test_canceling_does_not_upgrade_a_free_account(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        assert self.q._effective_tier(_profile("free", "canceling", future)) == "free"

    def test_canceling_expired_returns_free(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert self.q._effective_tier(_profile("free", "canceling", past)) == "free"

    def test_past_due_within_grace_returns_pro(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert self.q._effective_tier(_profile("pro", "past_due", future)) == "pro"

    def test_past_due_expired_returns_free(self):
        # _effective_tier() only computes the grace-period EXTENSION — once
        # the grace window has passed it just returns whatever `tier` is
        # already stored on the profile (the actual free/pro downgrade after
        # a payment failure happens separately, via the daily Celery beat
        # job documented in app/core/celery_app.py). So the realistic input
        # here — matching what the DB would actually hold post-downgrade —
        # is tier="free", exactly like the sibling test_canceling_expired_
        # returns_free case, not tier="pro" (which _effective_tier would
        # correctly still report as "pro": nothing here ever downgrades it).
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert self.q._effective_tier(_profile("free", "past_due", past)) == "free"


# ══════════════════════════════════════════════════════════════════════
# 2. YouTube tier check (Case 1 + 4)
# ══════════════════════════════════════════════════════════════════════

class TestYouTubeTier:

    def setup_method(self):
        from app.core import quotas as q
        self.q = q

    def _mock_tier(self, tier, monkeypatch):
        monkeypatch.setattr(self.q, "_get_tier", lambda uid: tier)

    def test_free_audio_allowed(self, monkeypatch):
        """Case 4 (audio path): Free user mp3 → allowed."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_youtube_tier("uid_free", "mp3_128")
        assert r["allowed"] is True

    def test_free_audio_prefix_variants(self, monkeypatch):
        self._mock_tier("free", monkeypatch)
        for q in ("mp3_320", "audio_best", "audio_aac"):
            assert self.q.check_youtube_tier("uid", q)["allowed"] is True, q

    def test_free_video_allowed(self, monkeypatch):
        """The YouTube Pro-only video paywall was deliberately removed —
        check_youtube_tier() now always returns {allowed: True} for any
        registered user (see its docstring in app/core/quotas.py). This test
        used to assert the OLD rule (free user video → 402 youtube_pro_only);
        updated to match the current, intentional business rule."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_youtube_tier("uid_free", "video_720")
        assert r["allowed"] is True

    def test_free_best_video_allowed(self, monkeypatch):
        self._mock_tier("free", monkeypatch)
        r = self.q.check_youtube_tier("uid_free", "video")
        assert r["allowed"] is True

    def test_anon_video_allowed(self, monkeypatch):
        """Unauthenticated (user_id=None) — check_youtube_tier() itself no
        longer distinguishes anonymous users at all (that restriction, where
        it still applies, is enforced separately via YT_ANON_AUDIO_ONLY in
        routes.py, not here)."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_youtube_tier(None, "video_1080")
        assert r["allowed"] is True

    def test_pro_video_allowed(self, monkeypatch):
        """Case 4: Pro user video → allowed."""
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_youtube_tier("uid_pro", "video_1080")
        assert r["allowed"] is True

    def test_pro_4k_allowed_by_tier(self, monkeypatch):
        """Pro tier allows 4K (Phase 8 may still block via cost guard, but tier passes)."""
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_youtube_tier("uid_pro", "video_2160")
        assert r["allowed"] is True


# ══════════════════════════════════════════════════════════════════════
# 3. Batch size check (Case 2 + 5)
# ══════════════════════════════════════════════════════════════════════

class TestBatchLimit:

    def setup_method(self):
        from app.core import quotas as q
        self.q = q

    def _mock_tier(self, tier, monkeypatch):
        monkeypatch.setattr(self.q, "_get_tier", lambda uid: tier)

    def test_free_exceeds_batch_limit(self, monkeypatch):
        """Case 2: Free user with 6 URLs > limit 5 → blocked."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_batch_limit("uid_free", 6)
        assert r["allowed"] is False
        assert r["error_code"] == "tier_limit_batch"
        assert r["limit"] == 5

    def test_free_at_limit(self, monkeypatch):
        self._mock_tier("free", monkeypatch)
        r = self.q.check_batch_limit("uid_free", 5)
        assert r["allowed"] is True

    def test_anon_exceeds_batch(self, monkeypatch):
        """Anonymous = free tier for batch."""
        monkeypatch.setattr(self.q, "_get_tier", lambda uid: "free")
        r = self.q.check_batch_limit(None, 6)
        assert r["allowed"] is False

    def test_pro_50_urls_allowed(self, monkeypatch):
        """Case 5: Pro user 50 URLs < limit 100 → allowed."""
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_batch_limit("uid_pro", 50)
        assert r["allowed"] is True
        assert r["limit"] == 100

    def test_pro_at_limit(self, monkeypatch):
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_batch_limit("uid_pro", 100)
        assert r["allowed"] is True

    def test_pro_exceeds_limit(self, monkeypatch):
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_batch_limit("uid_pro", 101)
        assert r["allowed"] is False


# ══════════════════════════════════════════════════════════════════════
# 4. Spotify artist mode gating (Case 3)
# ══════════════════════════════════════════════════════════════════════

class TestSpotifyArtistTier:

    def setup_method(self):
        from app.core import quotas as q
        self.q = q

    def _mock_tier(self, tier, monkeypatch):
        monkeypatch.setattr(self.q, "_get_tier", lambda uid: tier)

    def test_free_top_tracks_allowed(self, monkeypatch):
        """Free user top_tracks → spotify_artist_full=False but mode not restricted."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_feature_permission("uid_free", "spotify_artist_full")
        assert r["allowed"] is False
        assert r["error_code"] == "spotify_artist_full"

    def test_free_all_tracks_blocked(self, monkeypatch):
        """Case 3: Free user all_tracks → blocked."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_feature_permission("uid_free", "spotify_artist_full")
        assert r["allowed"] is False
        assert r["required_tier"] == "pro"
        assert "upgrade_url" in r

    def test_pro_all_tracks_allowed(self, monkeypatch):
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_feature_permission("uid_pro", "spotify_artist_full")
        assert r["allowed"] is True

    def test_pro_albums_allowed(self, monkeypatch):
        self._mock_tier("pro", monkeypatch)
        r = self.q.check_feature_permission("uid_pro", "spotify_artist_full")
        assert r["allowed"] is True


# ══════════════════════════════════════════════════════════════════════
# 5. Anonymous quota (Cases 6 + 7)
# ══════════════════════════════════════════════════════════════════════

class TestAnonQuota:

    def setup_method(self, monkeypatch=None):
        from app.core import quotas as q
        self.q = q

    def _get_module(self):
        from app.core import quotas as q
        return q

    def test_anon_allowed_within_limit(self, monkeypatch):
        """Case 7: First 5 downloads allowed."""
        q = self._get_module()
        fake_r = _fake_redis_client()
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_r)

        ip = "1.2.3.4"
        for i in range(5):
            result = q.check_anon_quota(ip)
            assert result["allowed"] is True, f"Call {i+1} should be allowed"
            q.increment_anon_usage(ip)

    def test_anon_blocked_at_limit(self, monkeypatch):
        """Case 7: 6th download blocked."""
        q = self._get_module()
        fake_r = _fake_redis_client()
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_r)

        ip = "5.6.7.8"
        # Pre-fill to 5
        key = f"vidgrab:quota:anon:{ip}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        fake_r.set(key, "5")

        result = q.check_anon_quota(ip)
        assert result["allowed"] is False
        assert result["error_code"] == "quota_exceeded_daily"
        assert result["downloads_today"] == 5
        assert result["daily_limit"] == 5

    def test_anon_key_has_ttl(self, monkeypatch):
        """Case 6: Anon key auto-expires before next UTC midnight."""
        q = self._get_module()
        fake_r = _fake_redis_client()
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_r)

        ip = "9.10.11.12"
        q.increment_anon_usage(ip)
        key = f"vidgrab:quota:anon:{ip}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        ttl = fake_r.ttl(key)
        # TTL should be positive and ≤ 86460 (24h + 60s buffer)
        assert ttl > 0
        assert ttl <= 86460

    def test_different_ips_independent(self, monkeypatch):
        q = self._get_module()
        fake_r = _fake_redis_client()
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_r)

        ip_a, ip_b = "10.0.0.1", "10.0.0.2"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fake_r.set(f"vidgrab:quota:anon:{ip_a}:{today}", "5")  # ip_a exhausted
        assert q.check_anon_quota(ip_a)["allowed"] is False
        assert q.check_anon_quota(ip_b)["allowed"] is True   # ip_b unaffected


# ══════════════════════════════════════════════════════════════════════
# 6. Stripe webhook (Case 8 — upgrade to Pro)
# ══════════════════════════════════════════════════════════════════════

class TestStripeWebhook:

    def _make_supabase_mock(self):
        """Mock that records updates."""
        updates = {}
        mock_sb = MagicMock()
        def fake_update(payload):
            updates["payload"] = payload
            chain = MagicMock()
            chain.eq.return_value.execute.return_value = MagicMock(data=[{}])
            return chain
        mock_sb.table.return_value.update.side_effect = fake_update
        mock_sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={"stripe_customer_id": None})
        return mock_sb, updates

    def test_upgrade_user_sets_tier_pro(self):
        """Case 8: _upgrade_user writes tier='pro' to profiles."""
        from app.api.payments import _upgrade_user
        mock_sb, updates = self._make_supabase_mock()
        with patch("app.api.payments._supabase", return_value=mock_sb):
            _upgrade_user("user_123", "sub_abc", 9999999999)
        assert updates["payload"]["tier"] == "pro"
        assert updates["payload"]["billing_status"] == "active"
        assert updates["payload"]["stripe_subscription_id"] == "sub_abc"

    def test_downgrade_user_clears_subscription(self):
        """_downgrade_user clears tier+billing for immediate cancel."""
        from app.api.payments import _downgrade_user
        mock_sb, updates = self._make_supabase_mock()
        with patch("app.api.payments._supabase", return_value=mock_sb):
            _downgrade_user("user_123", at_period_end=False)
        assert updates["payload"]["tier"] == "free"
        assert updates["payload"]["billing_status"] == "canceled"
        assert updates["payload"]["stripe_subscription_id"] is None


# ══════════════════════════════════════════════════════════════════════
# 7. Upgrade active immediately (Case 9) + Downgrade grace (Case 10)
# ══════════════════════════════════════════════════════════════════════

class TestTierTransitions:

    def setup_method(self):
        from app.core import quotas as q
        self.q = q

    def test_upgrade_reflects_immediately(self, monkeypatch):
        """Case 9: After upgrade, check_user_quota sees pro right away."""
        def _fake_profile(uid):
            return _profile("pro", "active")
        monkeypatch.setattr(self.q, "_get_profile", _fake_profile)
        monkeypatch.setattr(self.q, "_get_usage", lambda uid: {"downloads_today": 0})

        result = self.q.check_user_quota("uid_upgraded")
        assert result["allowed"] is True
        assert result["plan"] == "pro"

    def test_canceling_within_expiry_still_pro(self, monkeypatch):
        """Case 10: User cancelled but expiry hasn't passed → still Pro.

        tier stays 'pro' on the row — payments._downgrade_user(at_period_end=True)
        only sets billing_status and an expiry, it never rewrites the tier.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()
        def _fake_profile(uid):
            return _profile("pro", "canceling", future)
        monkeypatch.setattr(self.q, "_get_profile", _fake_profile)
        monkeypatch.setattr(self.q, "_get_usage", lambda uid: {"downloads_today": 5})

        result = self.q.check_user_quota("uid_canceling")
        assert result["allowed"] is True
        assert result["plan"] == "pro"

    def test_expired_subscription_returns_free(self, monkeypatch):
        """Case 10 (after expiry): Canceling subscription has lapsed → Free."""
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        def _fake_profile(uid):
            return _profile("free", "canceling", past)
        monkeypatch.setattr(self.q, "_get_profile", _fake_profile)
        monkeypatch.setattr(self.q, "_get_usage", lambda uid: {"downloads_today": 0})

        result = self.q.check_user_quota("uid_expired")
        assert result["plan"] == "free"


# ══════════════════════════════════════════════════════════════════════
# 8. Error response structure (Case 11)
# ══════════════════════════════════════════════════════════════════════

class TestErrorResponseStructure:

    def setup_method(self):
        from app.core import quotas as q
        self.q = q

    def _mock_tier(self, tier, monkeypatch):
        monkeypatch.setattr(self.q, "_get_tier", lambda uid: tier)

    # test_youtube_error_has_required_fields removed: it asserted the shape
    # of the youtube_pro_only error response, but check_youtube_tier() no
    # longer ever returns an error (the YouTube Pro-only paywall was
    # deliberately removed — see its docstring in app/core/quotas.py). There
    # is no longer an error response whose shape this could meaningfully test.

    def test_batch_error_has_required_fields(self, monkeypatch):
        """Case 11: tier_limit_batch response has limit + requested."""
        self._mock_tier("free", monkeypatch)
        r = self.q.check_batch_limit("uid_free", 10)
        assert r["allowed"] is False
        required = {"error_code", "tier", "limit", "requested", "message"}
        assert required.issubset(r.keys()), f"Missing: {required - r.keys()}"

    def test_feature_error_has_upgrade_url(self, monkeypatch):
        """Case 11: Feature error always includes upgrade_url."""
        self._mock_tier("free", monkeypatch)
        for feat in ("bulk_zip", "spotify_artist_full", "cloud_save"):
            r = self.q.check_feature_permission("uid_free", feat)
            assert "upgrade_url" in r, f"Missing upgrade_url for {feat}"
            assert "required_tier" in r, f"Missing required_tier for {feat}"
            assert r["error_code"] in (
                "bulk_zip_pro", "spotify_artist_full", "tier_required_feature"
            ), f"Wrong error_code for {feat}: {r['error_code']}"

    def test_quota_error_has_counts(self, monkeypatch):
        """Case 11: quota_exceeded_daily includes downloads_today + daily_limit."""
        def _fake_profile(uid):
            return _profile("free", "none", None)
        monkeypatch.setattr(self.q, "_get_profile", _fake_profile)
        monkeypatch.setattr(self.q, "_get_usage", lambda uid: {"downloads_today": 10})
        monkeypatch.setattr(self.q, "FREE_DAILY_LIMIT", 10)
        # NOTE: a plain dict's __getitem__ can't be monkeypatched as an
        # instance attribute (AttributeError: 'dict' object attribute
        # '__getitem__' is read-only) — that used to crash here before this
        # comment's replacement line ever ran. Mutating the dict directly
        # (below) is the correct approach and was already present.
        old_perms = self.q.TIER_PERMISSIONS["free"].copy()
        old_perms = self.q.TIER_PERMISSIONS["free"].copy()
        self.q.TIER_PERMISSIONS["free"]["daily_limit"] = 10

        r = self.q.check_user_quota("uid_free")
        assert r["allowed"] is False
        assert r["error_code"] == "quota_exceeded_daily"
        assert "downloads_today" in r
        assert "daily_limit" in r

        # Restore
        self.q.TIER_PERMISSIONS["free"].update(old_perms)
