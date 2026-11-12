"""SPY RV20 for the top bar (scanner/spy_rv.py). Expected values are worked by hand below."""
import math
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scanner import daily_refresh, spy_rv
from scanner.live_scanner import LiveScanner
from scanner.state import SymbolState

ET = ZoneInfo("America/New_York")


def _zigzag(step: float, n_returns: int = 20, start: float = 100.0) -> list[float]:
    """Closes whose returns alternate +step, -step: n_returns + 1 closes."""
    closes = [start]
    for i in range(n_returns):
        closes.append(closes[-1] * (1 + (step if i % 2 == 0 else -step)))
    return closes


def _spy_daily(closes: list[float], last_day: str = "2026-11-11") -> pd.DataFrame:
    """SPY daily bars stamped at midnight New York time in UTC, as both feeds return them."""
    days = pd.bdate_range(end=last_day, periods=len(closes))
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz=ET).tz_convert("UTC") for d in days])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0})


# Hand-worked: 20 returns of +1% and -1% in turn have mean 0, so the sample variance is
# 20 x 0.01^2 / 19. RV20 = 0.01 x sqrt(20/19) x sqrt(252) x 100 = 1.0259784 x 15.8745079 = 16.2870.
RV_1PCT = 16.2870
# Half the step, half the RV: 8.1435.
RV_HALF_PCT = 8.1435


def test_rv20_of_plus_minus_one_percent_days():
    assert spy_rv.rv20(_zigzag(0.01)) == pytest.approx(RV_1PCT, abs=1e-3)


def test_rv20_of_plus_minus_half_percent_days():
    assert spy_rv.rv20(_zigzag(0.005)) == pytest.approx(RV_HALF_PCT, abs=1e-3)


def test_rv20_uses_only_the_last_21_closes():
    # A 50% jump before the window must not count.
    closes = [10.0, 15.0, 15.0] + _zigzag(0.01)
    assert spy_rv.rv20(closes) == pytest.approx(RV_1PCT, abs=1e-3)


def test_rv20_of_flat_closes_is_zero():
    assert spy_rv.rv20([400.0] * 21) == 0.0


def test_rv20_needs_21_closes():
    assert spy_rv.rv20(_zigzag(0.01)[1:]) is None      # 20 closes, 19 returns
    assert spy_rv.rv20([]) is None


def test_rv20_refuses_a_bad_close():
    # A zero or NaN close is a bad bar, not a calm day: no value rather than a wrong one.
    bad = _zigzag(0.01)
    bad[5] = 0.0
    assert spy_rv.rv20(bad) is None
    bad[5] = float("nan")
    assert spy_rv.rv20(bad) is None


def test_calm_is_at_or_below_10_5():
    assert spy_rv.reading(10.5, "2026-11-11")["calm"] is True
    assert spy_rv.reading(10.51, "2026-11-11")["calm"] is False
    assert spy_rv.reading(None, "2026-11-11") is None


def test_from_daily_reports_the_value_the_last_session_and_calm():
    r = spy_rv.from_daily(_spy_daily(_zigzag(0.005)))
    assert r == {"rv20": 8.14, "through": "2026-11-11", "calm": True, "calm_max": 10.5}
    r = spy_rv.from_daily(_spy_daily(_zigzag(0.01), last_day="2026-11-10"))
    assert r["rv20"] == 16.29 and r["through"] == "2026-11-10" and r["calm"] is False


def test_from_daily_without_enough_history():
    assert spy_rv.from_daily(_spy_daily(_zigzag(0.01)[:10])) is None
    assert spy_rv.from_daily(pd.DataFrame()) is None
    assert spy_rv.from_daily(None) is None


def test_warmup_sets_the_reading():
    spy = _spy_daily(_zigzag(0.01, n_returns=40))
    sc = LiveScanner([], feed=None)
    assert sc._spy_rv is None
    sc.warmup(spy, {})
    assert sc._spy_rv["through"] == "2026-11-11"
    assert math.isclose(sc._spy_rv["rv20"], 16.29)


def test_daily_refresh_rolls_the_reading_forward():
    old = _spy_daily(_zigzag(0.01), last_day="2026-11-10")
    sc = SimpleNamespace(_states={}, _spy_state=SymbolState.from_history("SPY", old, old),
                         _series={}, _profiles=None, _sector_map={}, _spy_rv=spy_rv.from_daily(old))
    assert sc._spy_rv["through"] == "2026-11-10"
    new = _spy_daily(_zigzag(0.005), last_day="2026-11-11")
    daily_refresh.apply(sc, new, {}, {}, {})
    assert sc._spy_rv == {"rv20": 8.14, "through": "2026-11-11", "calm": True, "calm_max": 10.5}



def test_clock_carries_the_reading_and_none_before_it_is_known():
    from fastapi.testclient import TestClient
    from scanner.api import AppState, create_app
    from tests.test_api_v2 import FakeScanner, _states
    scanner = FakeScanner(_states())
    c = TestClient(create_app(AppState(scanner=scanner, feed=None)))  # type: ignore[arg-type]
    assert c.get("/api/v2/clock").json()["spy_rv"] is None
    scanner._spy_rv = spy_rv.reading(12.345, "2026-11-11")
    assert c.get("/api/v2/clock").json()["spy_rv"] == {
        "rv20": 12.35, "through": "2026-11-11", "calm": False, "calm_max": 10.5}
