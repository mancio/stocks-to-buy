"""Support-level computation.

Implements the 5-bar fractal swing-low + clustering algorithm described in the
skill, plus the 52-week-high / drawdown / days-since-high metrics derived from
the same history (no extra fetch).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config


@dataclass
class SupportResult:
    support: float
    last_close: float
    distance_pct: float
    high_52w: float
    drawdown_pct: float
    days_since_high: int


def _swing_lows(lows: list[float], window: int) -> list[float]:
    """Return prices at bars that are a fractal swing low (both-sided min)."""
    out: list[float] = []
    n = len(lows)
    for i in range(window, n - window):
        segment = lows[i - window : i + window + 1]
        if lows[i] == min(segment):
            out.append(lows[i])
    return out


def _cluster_prices(prices: list[float], tol_pct: float) -> list[float]:
    """Bucket prices that are within tol_pct of each other; return cluster means."""
    if not prices:
        return []
    prices = sorted(prices)
    clusters: list[list[float]] = [[prices[0]]]
    for p in prices[1:]:
        anchor = clusters[-1][0]
        if abs(p - anchor) / anchor * 100 <= tol_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [sum(c) / len(c) for c in clusters]


def compute_support(df: pd.DataFrame) -> SupportResult | None:
    """Compute the nearest support below last close plus drawdown metrics.

    Returns None if there is not enough history or no qualifying support exists
    below the last close.
    """
    if df is None or df.empty:
        return None

    df = df.tail(config.LOOKBACK_DAYS)
    if len(df) < 2 * config.SWING_WINDOW + 2:
        return None

    lows = df["Low"].astype(float).tolist()
    highs = df["High"].astype(float)
    last_close = float(df["Close"].astype(float).iloc[-1])
    if last_close <= 0:
        return None

    swing = _swing_lows(lows, config.SWING_WINDOW)
    clusters = _cluster_prices(swing, config.CLUSTER_TOLERANCE_PCT)

    # Highest cluster strictly below last close = nearest support from above.
    below = [c for c in clusters if c < last_close]
    if not below:
        return None
    support = max(below)

    distance_pct = (last_close - support) / last_close * 100.0

    high_52w = float(highs.max())
    high_pos = int(highs.reset_index(drop=True).idxmax())
    days_since_high = len(df) - 1 - high_pos
    drawdown_pct = (high_52w - last_close) / high_52w * 100.0 if high_52w > 0 else 0.0

    return SupportResult(
        support=support,
        last_close=last_close,
        distance_pct=distance_pct,
        high_52w=high_52w,
        drawdown_pct=drawdown_pct,
        days_since_high=days_since_high,
    )


def is_near_support(res: SupportResult, threshold_pct: float) -> bool:
    return 0.0 <= res.distance_pct <= threshold_pct


if __name__ == "__main__":
    from .prices import download_history

    hist = download_history(["AAPL", "MSFT"])
    for tkr, df in hist.items():
        r = compute_support(df)
        if r:
            print(
                f"{tkr}: close={r.last_close:.2f} support={r.support:.2f} "
                f"dist={r.distance_pct:.2f}% drawdown={r.drawdown_pct:.2f}% "
                f"daysSinceHigh={r.days_since_high}"
            )
        else:
            print(f"{tkr}: no qualifying support")
