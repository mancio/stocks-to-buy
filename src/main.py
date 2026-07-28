"""Pipeline orchestrator.

Usage:
    python -m src.main

Flow:
  1. Load US + EU universe.
  2. Batch-download 1y prices via Yahoo.
  3. Compute support + 52w metrics; keep only "near support" rows.
  4. Fetch Finnhub ratings for survivors only.
  5. Drop every row whose rating is not Buy / Strong Buy.
  6. Sort ascending by last close.
  7. Render report.html.
"""
from __future__ import annotations

from tqdm import tqdm

from . import config
from .community import community_signal, get_community_map
from .prices import download_history
from .ratings import fetch_rating
from .report import render_report
from .signals import compute_score
from .support import compute_support, is_near_support
from .universe import get_universe

_ALLOWED_RATINGS = {"Buy", "Strong Buy"}


def run() -> None:
    # Fail fast if the key is missing (the skill mandates asking the user).
    config.require_finnhub_key()

    print("[1/8] Loading universe (S&P 500 + STOXX Europe 600)...")
    universe = get_universe()
    print(f"      {len(universe)} tickers "
          f"({(universe['market'] == 'US').sum()} US, "
          f"{(universe['market'] == 'EU').sum()} EU)")

    print("[2/8] Downloading 1y price history from Yahoo...")
    history = download_history(universe["ticker"].tolist())
    print(f"      Got history for {len(history)} tickers")

    meta = {r.ticker: r for r in universe.itertuples(index=False)}

    print("[3/8] Computing support levels...")
    candidates: list[dict] = []
    for ticker, df in tqdm(history.items(), total=len(history)):
        res = compute_support(df)
        if res is None or not is_near_support(res, config.NEAR_THRESHOLD_PCT):
            continue
        info = meta.get(ticker)
        candidates.append(
            {
                "ticker": ticker,
                "name": getattr(info, "name", ticker),
                "market": getattr(info, "market", "US"),
                "last_close": res.last_close,
                "support": res.support,
                "distance_pct": res.distance_pct,
                "high_52w": res.high_52w,
                "drawdown_pct": res.drawdown_pct,
                "days_since_high": res.days_since_high,
            }
        )
    print(f"      {len(candidates)} tickers near support")

    print("[4/8] Fetching Finnhub analyst ratings (survivors only)...")
    rows: list[dict] = []
    for c in tqdm(candidates):
        rating = fetch_rating(c["ticker"])
        if rating is None:
            continue
        c["rating"] = rating.label
        c["num_analysts"] = rating.num_analysts
        rows.append(c)

    print("[5/8] Filtering to Buy / Strong Buy...")
    rows = [r for r in rows if r["rating"] in _ALLOWED_RATINGS]
    print(f"      {len(rows)} rows remain")

    print("[6/8] Fetching community attention (ApeWisdom / Reddit)...")
    community = get_community_map()
    print(f"      {len(community)} tickers in community feed")

    print("[7/8] Scoring & ranking (support + analyst + drawdown + momentum + community)...")
    for r in rows:
        csub, creason = community_signal(r["ticker"], community)
        result = compute_score(
            r, history.get(r["ticker"]), config.NEAR_THRESHOLD_PCT, csub, creason
        )
        r["score"] = result.score
        r["community"] = round(result.subscores["community"] * 100, 1)
        r["reasons"] = result.reasons
    # Rank by composite score (desc); tie-break cheapest first.
    rows.sort(key=lambda r: (-r["score"], r["last_close"]))

    print("[8/8] Rendering report.html...")
    path = render_report(rows)

    us = sum(1 for r in rows if r["market"] == "US")
    eu = sum(1 for r in rows if r["market"] == "EU")
    print(f"Wrote {path.name} with {len(rows)} rows ({us} US, {eu} EU)")


if __name__ == "__main__":
    run()
