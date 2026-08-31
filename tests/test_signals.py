"""Composite score arithmetic."""
from __future__ import annotations

import pandas as pd
import pytest

from src.signals import (
    WEIGHTS,
    _analyst_score,
    _drawdown_score,
    _momentum_score,
    _support_score,
    compute_score,
)


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "distance, threshold, expected",
    [(0.0, 5.0, 1.0), (2.5, 5.0, 0.5), (5.0, 5.0, 0.0), (9.0, 5.0, 0.0)],
)
def test_support_subscore_is_linear_in_distance(distance, threshold, expected):
    assert _support_score(distance, threshold) == pytest.approx(expected)


@pytest.mark.parametrize(
    "label, analysts, expected",
    [
        ("Strong Buy", 20, 1.0),          # full confidence
        ("Strong Buy", 0, 0.6),           # no analysts -> confidence floor
        ("Buy", 20, 0.75),
        ("Buy", 10, 0.75 * 0.8),
        ("Hold", 20, 0.40),
    ],
)
def test_analyst_subscore(label, analysts, expected):
    assert _analyst_score(label, analysts) == pytest.approx(expected)


@pytest.mark.parametrize(
    "drawdown, expected",
    [(20.0, 1.0), (0.0, 0.5), (40.0, 0.5), (60.0, 0.0), (95.0, 0.0)],
)
def test_drawdown_subscore_peaks_at_20_percent(drawdown, expected):
    assert _drawdown_score(drawdown) == pytest.approx(expected)


def test_momentum_subscore_maps_sma_ratio():
    assert _momentum_score(None) == pytest.approx(0.5)  # neutral
    assert _momentum_score(0.90) == pytest.approx(0.0)
    assert _momentum_score(1.00) == pytest.approx(0.5)
    assert _momentum_score(1.10) == pytest.approx(1.0)
    assert _momentum_score(1.50) == pytest.approx(1.0)  # clamped


def test_momentum_uses_the_50_day_average():
    # 49 bars at 100 + 1 bar at 150 -> sma50 = 101, ratio = 150/101
    closes = [100.0] * 49 + [150.0]
    df = pd.DataFrame({"Close": closes})
    result = compute_score(
        {"distance_pct": 0.0, "drawdown_pct": 20.0, "rating": "Buy", "num_analysts": 20},
        df,
        5.0,
    )
    expected_ratio = 150.0 / (sum(closes) / 50)
    assert result.subscores["momentum"] == pytest.approx(
        _momentum_score(expected_ratio)
    )


def test_composite_score_equals_the_weighted_sum():
    candidate = {
        "distance_pct": 0.0,
        "drawdown_pct": 20.0,
        "rating": "Strong Buy",
        "num_analysts": 20,
    }
    result = compute_score(candidate, pd.DataFrame(), 5.0, community_sub=0.5)

    assert result.subscores == pytest.approx(
        {
            "support": 1.0,
            "analyst": 1.0,
            "drawdown": 1.0,
            "momentum": 0.5,   # empty history -> neutral
            "community": 0.5,
            "demand": 0.5,     # no order-book data -> neutral
        }
    )
    expected = sum(
        result.subscores[k] * WEIGHTS[k] for k in WEIGHTS
    ) * 100.0
    assert result.score == pytest.approx(expected, abs=0.05)


def test_demand_subscore_defaults_to_neutral_and_is_passed_through():
    candidate = {
        "distance_pct": 0.0,
        "drawdown_pct": 20.0,
        "rating": "Buy",
        "num_analysts": 20,
    }
    neutral = compute_score(candidate, pd.DataFrame(), 5.0)
    heavy_bid = compute_score(candidate, pd.DataFrame(), 5.0, demand_sub=0.9)

    assert neutral.subscores["demand"] == pytest.approx(0.5)
    assert heavy_bid.subscores["demand"] == pytest.approx(0.9)
    assert heavy_bid.score - neutral.score == pytest.approx(
        0.4 * WEIGHTS["demand"] * 100.0, abs=0.05
    )


def test_score_stays_within_zero_and_one_hundred():
    worst = compute_score(
        {"distance_pct": 99.0, "drawdown_pct": 99.0, "rating": "Strong Sell", "num_analysts": 0},
        pd.DataFrame(),
        5.0,
        community_sub=0.0,
        demand_sub=0.0,
    )
    best = compute_score(
        {"distance_pct": 0.0, "drawdown_pct": 20.0, "rating": "Strong Buy", "num_analysts": 50},
        pd.DataFrame({"Close": [100.0] * 49 + [200.0]}),
        5.0,
        community_sub=1.0,
        demand_sub=1.0,
    )
    assert 0.0 <= worst.score <= best.score <= 100.0


def test_reasons_string_reports_the_candidate_numbers():
    result = compute_score(
        {"distance_pct": 2.34, "drawdown_pct": 27.6, "rating": "Buy", "num_analysts": 12},
        pd.DataFrame(),
        5.0,
        community_reason="Reddit: 10 mentions",
        demand_reason="Book: 61% bid-side",
    )
    assert "Buy (12 analysts)" in result.reasons
    assert "2.3% above support" in result.reasons
    assert "28% off 52w high" in result.reasons
    assert "Reddit: 10 mentions" in result.reasons
    assert "Book: 61% bid-side" in result.reasons
