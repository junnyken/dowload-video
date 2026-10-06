"""
2026-10-06 first live admin canary: 3 Douyin runs on natanielsantos~douyin-scraper
reported usageTotalUsd $0.00005 each (run-start fee) while the actor charges
~$0.007 per result. Settling the budget down to that figure would let the
$1/day ceiling admit ~140x the intended runs, so by default a managed run is
never recorded below its estimate (CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE).
"""
import pytest

from app.services.china_platforms import budget_guard
from tests._china_fakes import clean_env, flags_on, rc  # noqa: F401
from tests.test_china_access_stage_a import _reserve, _spend


@pytest.fixture
def floor_on(flags_on):  # noqa: F811
    flags_on.setenv("CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE", "true")
    return flags_on


def test_default_is_floor_on(clean_env):  # noqa: F811
    from app.services.china_platforms import settings
    clean_env.delenv("CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE", raising=False)
    assert settings.cost_floor_at_estimate() is True


def test_reported_cost_below_estimate_keeps_the_estimate(floor_on, rc):  # noqa: F811
    out = budget_guard.reconcile(_reserve(), 0.00005, True)   # what Apify reported live
    assert out["actual_cost_usd"] == pytest.approx(0.0071)
    assert out["cost_flag"] is None
    assert _spend(rc) == [7100, 7100, 7100]


def test_reported_cost_above_estimate_still_raises_spend(floor_on, rc):  # noqa: F811
    out = budget_guard.reconcile(_reserve(), 0.0110, True)
    assert out["cost_flag"] == "over_estimate"
    assert _spend(rc) == [11000, 11000, 11000]


def test_no_run_started_still_refunds(floor_on, rc):  # noqa: F811
    budget_guard.reconcile(_reserve(), None, False)
    assert _spend(rc) == [0, 0, 0]


def test_ceiling_counts_estimate_not_run_start_fee(floor_on, rc, monkeypatch):  # noqa: F811
    """140 runs at the reported $0.00005 would sum to $0.007; with the floor
    they are counted at the estimate, so a $0.02 ceiling stops the 3rd run."""
    monkeypatch.setenv("CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "0.02")
    monkeypatch.setenv("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "0.02")
    monkeypatch.setenv("CHINA_ACCESS_ADMIN_DAILY_RESOLVE_LIMIT", "100")
    budget_guard.reconcile(_reserve(), 0.00005, True)
    budget_guard.reconcile(_reserve(), 0.00005, True)
    with pytest.raises(Exception):
        _reserve()
