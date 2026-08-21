"""
One tier rule, three call sites.

An account with tier='enterprise' and billing_status='none' used to get three
different answers depending on who asked:

    quotas.py               → enterprise  (used the declared tier as-is)
    entitlements.py         → free        (no billing record ⇒ free)
    payments/billing-status → enterprise  (returned profiles.tier raw)

So the account menu displayed ENTERPRISE while every require_feature gate
refused, and the download quota let them through unlimited anyway. These tests
pin the shared rule and pin that every caller goes through it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.entitlements import resolve_effective_tier


def _future(days=5):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TestSharedRule:

    def test_active_paid_tier_is_honoured(self):
        for tier in ("pro", "team", "enterprise"):
            assert resolve_effective_tier({"tier": tier, "billing_status": "active"}) == tier

    def test_paid_tier_with_no_billing_record_is_free(self):
        """An admin grant sets billing_status='active'. A paid tier with no
        billing record at all is a leftover row, not an entitlement."""
        assert resolve_effective_tier(
            {"tier": "enterprise", "billing_status": "none"}
        ) == "free"

    def test_canceled_is_free_regardless_of_tier(self):
        assert resolve_effective_tier(
            {"tier": "enterprise", "billing_status": "canceled",
             "subscription_expiry": _future()}
        ) == "free"

    def test_canceling_keeps_the_declared_tier_until_expiry(self):
        """Not a hardcoded 'pro' — that demoted Team and Enterprise accounts
        for the length of a payment problem."""
        assert resolve_effective_tier(
            {"tier": "enterprise", "billing_status": "canceling",
             "subscription_expiry": _future()}
        ) == "enterprise"

    def test_canceling_past_expiry_is_free(self):
        assert resolve_effective_tier(
            {"tier": "pro", "billing_status": "canceling",
             "subscription_expiry": _past()}
        ) == "free"

    def test_canceling_never_upgrades_a_free_account(self):
        assert resolve_effective_tier(
            {"tier": "free", "billing_status": "canceling",
             "subscription_expiry": _future()}
        ) == "free"

    def test_past_due_honours_either_grace_clock(self):
        """_past_due_user writes subscription_expiry; the entitlements path
        reads grace_period_ends_at. Both are in the wild, so both count."""
        assert resolve_effective_tier(
            {"tier": "pro", "billing_status": "past_due",
             "subscription_expiry": _future()}
        ) == "pro"
        assert resolve_effective_tier(
            {"tier": "pro", "billing_status": "past_due",
             "grace_period_ends_at": _future()}
        ) == "pro"

    def test_past_due_after_grace_is_free(self):
        assert resolve_effective_tier(
            {"tier": "pro", "billing_status": "past_due",
             "subscription_expiry": _past(), "grace_period_ends_at": _past()}
        ) == "free"

    def test_missing_and_empty_fields_default_to_free(self):
        assert resolve_effective_tier({}) == "free"
        assert resolve_effective_tier({"tier": None, "billing_status": None}) == "free"

    def test_tier_casing_is_normalised(self):
        assert resolve_effective_tier(
            {"tier": "ENTERPRISE", "billing_status": "ACTIVE"}
        ) == "enterprise"


class TestCallersUseTheSharedRule:

    def test_quotas_delegates(self):
        from app.core import quotas
        profile = {"tier": "enterprise", "billing_status": "none"}
        assert quotas._effective_tier(profile) == resolve_effective_tier(profile)
        assert quotas._effective_tier(profile) == "free"

    def test_quotas_reads_the_grace_clock_column(self):
        """_get_profile omitted grace_period_ends_at, which silently turned a
        past_due account's grace period into an immediate downgrade."""
        import inspect
        from app.core import quotas
        assert "grace_period_ends_at" in inspect.getsource(quotas._get_profile)

    def test_billing_status_endpoint_reports_the_effective_tier(self):
        import inspect
        from app.api import payments
        src = inspect.getsource(payments.billing_status)
        assert "resolve_effective_tier" in src, (
            "the frontend badges and feature locks off this response, so it "
            "must not report a tier the backend will refuse"
        )

    def test_user_usage_endpoint_reports_the_effective_tier(self):
        import inspect
        from app.api import user as user_api
        assert "resolve_effective_tier" in inspect.getsource(user_api.get_usage)


class TestPaidTierDailyLimitIsEnforced:
    """check_user_quota returned allowed=True for pro/team without reading the
    number, so PRO_DAILY_LIMIT and TEAM_DAILY_LIMIT were shown in the UI and
    enforced nowhere — and every extra download can pull paid residential-proxy
    bytes."""

    def test_unlimited_stays_unlimited(self):
        from app.core.quotas import _enforced_daily_limit
        assert _enforced_daily_limit("enterprise", -1) == -1

    def test_paid_tier_limit_is_a_real_number(self):
        from app.core.quotas import _enforced_daily_limit
        assert _enforced_daily_limit("pro", 500) == 500

    def test_free_tier_uses_its_configured_limit(self):
        from app.core.quotas import _enforced_daily_limit
        assert _enforced_daily_limit("free", 10) == 10

    def test_a_paid_tier_never_allows_less_than_free(self, monkeypatch):
        """This deployment sets FREE_DAILY_LIMIT=1000 and leaves
        PRO_DAILY_LIMIT at its default of 100. Enforcing that literally would
        give a paying account a tenth of what a free one gets."""
        from app.core import quotas
        monkeypatch.setitem(quotas.TIER_PERMISSIONS["free"], "daily_limit", 1000)
        assert quotas._enforced_daily_limit("pro", 100) == 1000

    def test_unlimited_free_tier_makes_paid_tiers_unlimited_too(self, monkeypatch):
        from app.core import quotas
        monkeypatch.setitem(quotas.TIER_PERMISSIONS["free"], "daily_limit", -1)
        assert quotas._enforced_daily_limit("pro", 100) == -1
