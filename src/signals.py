"""Composite buy-attractiveness scoring.

Combines several *independent, transparent* signals into a single 0-100 score so
rows can be ranked. Nothing here is investment advice — it is a weighted, fully
auditable blend of the data already gathered from the price and rating sources.

Signals (each normalised to 0..1, higher = more attractive):
  * support   — proximity to the computed support level (closer = better)
  * analyst   — consensus rating strength, scaled by analyst-count confidence
  * drawdown  — pullback sweet spot (rewards a moderate dip, penalises crashes)
  * momentum  — last close vs its 50-day moving average (recovering = better)
  * community — Reddit attention (US only, neutral elsewhere)
  * demand    — share of resting order-book size sitting on the bid (US only)

Weights are explicit constants below and sum to 1.0. Change them in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Explicit, auditable weights (must sum to 1.0).
WEIGHTS: dict[str, float] = {
    "support": 0.25,
    "analyst": 0.29,
    "drawdown": 0.16,
    "momentum": 0.12,
    "community": 0.08,
    "demand": 0.10,
}

_RATING_BASE = {
    "Strong Buy": 1.00,
    "Buy": 0.75,
    "Hold": 0.40,
    "Sell": 0.10,
    "Strong Sell": 0.00,
}

# Drawdown sweet spot: score peaks at this % off the 52w high.
_DRAWDOWN_PEAK = 20.0
_DRAWDOWN_SPREAD = 40.0


@dataclass
class ScoreResult:
    score: float  # 0..100
    subscores: dict[str, float] = field(default_factory=dict)
    reasons: str = ""


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _support_score(distance_pct: float, threshold_pct: float) -> float:
    if threshold_pct <= 0:
        return 0.0
    return _clamp(1.0 - distance_pct / threshold_pct)


def _analyst_score(rating_label: str, num_analysts: int) -> float:
    base = _RATING_BASE.get(rating_label, 0.40)
    confidence = _clamp(num_analysts / 20.0)  # ~20+ analysts = full confidence
    return _clamp(base * (0.6 + 0.4 * confidence))


def _drawdown_score(drawdown_pct: float) -> float:
    return _clamp(1.0 - abs(drawdown_pct - _DRAWDOWN_PEAK) / _DRAWDOWN_SPREAD)


def _momentum_ratio(df: pd.DataFrame) -> float | None:
    """Last close divided by its 50-day simple moving average."""
    if df is None or df.empty or "Close" not in df.columns:
        return None
    close = df["Close"].astype(float)
    if len(close) < 50:
        return None
    sma50 = float(close.tail(50).mean())
    if sma50 <= 0:
        return None
    return float(close.iloc[-1]) / sma50


def _momentum_score(ratio: float | None) -> float:
    if ratio is None:
        return 0.5  # neutral when history is too short
    # 0.90x SMA -> 0.0, 1.10x SMA -> 1.0
    return _clamp((ratio - 0.90) / 0.20)


def compute_score(
    candidate: dict,
    history: pd.DataFrame,
    threshold_pct: float,
    community_sub: float | None = None,
    community_reason: str | None = None,
    demand_sub: float | None = None,
    demand_reason: str | None = None,
) -> ScoreResult:
    """Return the composite score, per-signal breakdown, and a reasons string.

    `candidate` must contain: distance_pct, drawdown_pct, rating, num_analysts.
    `history` is the same OHLC frame used for support (for the momentum signal).
    `community_sub` is the ApeWisdom attention sub-score (None -> neutral 0.5).
    `demand_sub` is the order-book bid-side share (None -> neutral 0.5).
    """
    ratio = _momentum_ratio(history)

    subs = {
        "support": _support_score(candidate["distance_pct"], threshold_pct),
        "analyst": _analyst_score(candidate["rating"], candidate["num_analysts"]),
        "drawdown": _drawdown_score(candidate["drawdown_pct"]),
        "momentum": _momentum_score(ratio),
        "community": community_sub if community_sub is not None else 0.5,
        "demand": demand_sub if demand_sub is not None else 0.5,
    }

    score = sum(subs[k] * WEIGHTS[k] for k in WEIGHTS) * 100.0

    trend = "above" if (ratio is not None and ratio >= 1.0) else "below"
    reason_parts = [
        f"{candidate['rating']} ({candidate['num_analysts']} analysts)",
        f"{candidate['distance_pct']:.1f}% above support",
        f"{candidate['drawdown_pct']:.0f}% off 52w high",
        f"{trend} 50-day avg",
    ]
    if community_reason:
        reason_parts.append(community_reason)
    if demand_reason:
        reason_parts.append(demand_reason)
    reasons = "; ".join(reason_parts)

    return ScoreResult(score=round(score, 1), subscores=subs, reasons=reasons)


if __name__ == "__main__":
    demo = {
        "distance_pct": 2.0,
        "drawdown_pct": 22.0,
        "rating": "Strong Buy",
        "num_analysts": 25,
    }
    print(compute_score(demo, pd.DataFrame(), 5.0))
