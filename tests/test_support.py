"""Support / drawdown maths on synthetic price series with known answers."""
from __future__ import annotations

import pandas as pd
import pytest

from src.support import compute_support, is_near_support


def make_frame(lows: list[float], highs: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Low": lows, "High": highs, "Close": closes})


def flat_series(n: int, low: float, high: float, close: float):
    return [low] * n, [high] * n, [close] * n


def test_picks_highest_cluster_below_last_close():
    lows, highs, closes = flat_series(60, 110.0, 120.0, 100.0)
    lows[10] = 90.0   # deeper support
    lows[30] = 95.0   # nearest support below last close
    highs[5] = 150.0  # 52w high

    res = compute_support(make_frame(lows, highs, closes))

    assert res is not None
    assert res.support == pytest.approx(95.0)
    assert res.last_close == pytest.approx(100.0)
    assert res.distance_pct == pytest.approx(5.0)


def test_drawdown_and_days_since_high():
    lows, highs, closes = flat_series(60, 110.0, 120.0, 100.0)
    lows[30] = 95.0
    highs[5] = 150.0

    res = compute_support(make_frame(lows, highs, closes))

    assert res is not None
    assert res.high_52w == pytest.approx(150.0)
    # (150 - 100) / 150 * 100
    assert res.drawdown_pct == pytest.approx(33.3333, abs=1e-3)
    assert res.days_since_high == 59 - 5


def test_swing_lows_within_tolerance_are_clustered_to_their_mean():
    lows, highs, closes = flat_series(60, 110.0, 120.0, 100.0)
    lows[10] = 90.0
    lows[30] = 91.0  # 1.11% apart -> same cluster as 90.0

    res = compute_support(make_frame(lows, highs, closes))

    assert res is not None
    assert res.support == pytest.approx(90.5)


def test_no_support_below_last_close_returns_none():
    lows, highs, closes = flat_series(60, 110.0, 120.0, 100.0)

    assert compute_support(make_frame(lows, highs, closes)) is None


def test_too_little_history_returns_none():
    lows, highs, closes = flat_series(8, 90.0, 120.0, 100.0)

    assert compute_support(make_frame(lows, highs, closes)) is None


def test_empty_frame_returns_none():
    assert compute_support(pd.DataFrame()) is None
    assert compute_support(None) is None


@pytest.mark.parametrize(
    "distance_pct, expected",
    [(-0.1, False), (0.0, True), (2.5, True), (5.0, True), (5.01, False)],
)
def test_is_near_support_boundaries(distance_pct, expected):
    from src.support import SupportResult

    res = SupportResult(
        support=95.0,
        last_close=100.0,
        distance_pct=distance_pct,
        high_52w=150.0,
        drawdown_pct=33.3,
        days_since_high=10,
    )
    assert is_near_support(res, 5.0) is expected
