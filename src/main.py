"""Pipeline orchestrator.

Usage:
    python -m src.main

Flow:
  1. Load US + EU universe.
  2. Batch-download 1y prices via Yahoo.
  3. Re-resolve the tickers Yahoo did not recognise (Yahoo symbol search).
  4. Compute support + 52w metrics; keep only "near support" rows.
  5. Fetch Finnhub ratings for survivors only.
  6. Drop every row whose rating is not Buy / Strong Buy.
  7. Fetch balance-sheet fundamentals for the survivors (detail panel).
  8. Sort ascending by last close.
  9. Render report.html.
"""
from __future__ import annotations

import pandas as pd
from tqdm import tqdm

from . import config
from .community import community_signal, get_community_map
from .demand import demand_signal
from .fundamentals import fetch_fundamentals
from .prices import download_history
from .ratings import fetch_rating
from .report import render_report
from .short_volume import get_short_volume_map, short_volume_pct
from .signals import compute_score
from .support import compute_support, is_near_support
from .symbols import base_symbol, resolve_symbol
from .universe import get_universe

_ALLOWED_RATINGS = {"Buy", "Strong Buy"}


def resolve_missing(
    universe: pd.DataFrame, history: dict
) -> tuple[pd.DataFrame, dict, dict[str, str]]:
    """Retry tickers with no price history under alternative Yahoo symbols.

    Tries the plain base symbol first, then Yahoo's symbol search keyed on the
    company name. Returns the universe with renamed tickers, the extended
    history, and the applied {old: new} mapping.
    """
    missing = [t for t in universe["ticker"] if t not in history]
    if not missing:
        return universe, history, {}

    meta = {r.ticker: r for r in universe.itertuples(index=False)}
    candidates: dict[str, list[str]] = {}
    for ticker in tqdm(missing):
        info = meta[ticker]
        suffix = f".{info.exchange}" if info.market == "EU" else ""
        base = base_symbol(ticker)
        alts = [alt for alt in (base + suffix,) if alt != ticker]
        found = resolve_symbol(info.name, base, suffix)
        if found and found != ticker and found not in alts:
            alts.append(found)
        if alts:
            candidates[ticker] = alts

    lookup = sorted({alt for alts in candidates.values() for alt in alts})
    if not lookup:
        return universe, history, {}

    extra = download_history(lookup)
    renames: dict[str, str] = {}
    for ticker, alts in candidates.items():
        for alt in alts:
            if alt in extra and alt not in history:
                renames[ticker] = alt
                history[alt] = extra[alt]
                break

    if renames:
        universe = universe.copy()
        universe["ticker"] = universe["ticker"].map(lambda t: renames.get(t, t))
        universe = universe.drop_duplicates("ticker").reset_index(drop=True)
    return universe, history, renames


def run() -> None:
    # Fail fast if the key is missing (the skill mandates asking the user).
    config.require_finnhub_key()

    print("[1/10] Loading universe (S&P 500 + STOXX Europe 600)...")
    universe = get_universe()
    print(f"      {len(universe)} tickers "
          f"({(universe['market'] == 'US').sum()} US, "
          f"{(universe['market'] == 'EU').sum()} EU)")

    print("[2/10] Downloading 1y price history from Yahoo...")
    history = download_history(universe["ticker"].tolist())
    print(f"      Got history for {len(history)} tickers")

    print("[3/10] Re-resolving unrecognised tickers via Yahoo symbol search...")
    unresolved = sum(1 for t in universe["ticker"] if t not in history)
    universe, history, renames = resolve_missing(universe, history)
    print(f"      recovered {len(renames)}/{unresolved} missing tickers")

    meta = {r.ticker: r for r in universe.itertuples(index=False)}

    print("[4/10] Computing support levels...")
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

    print("[5/10] Fetching Finnhub analyst ratings (survivors only)...")
    rows: list[dict] = []
    for c in tqdm(candidates):
        rating = fetch_rating(c["ticker"])
        if rating is None:
            continue
        c["rating"] = rating.label
        c["num_analysts"] = rating.num_analysts
        c["rating_counts"] = rating.counts
        rows.append(c)

    print("[6/10] Filtering to Buy / Strong Buy...")
    rows = [r for r in rows if r["rating"] in _ALLOWED_RATINGS]
    print(f"      {len(rows)} rows remain")

    print("[7/10] Fetching community attention (ApeWisdom / Reddit)...")
    community = get_community_map()
    print(f"      {len(community)} tickers in community feed")

    print("      Fetching FINRA daily short volume...")
    shorts = get_short_volume_map()
    print(f"      {len(shorts)} symbols in the FINRA file")

    print("[8/10] Scoring & ranking (support + analyst + drawdown + momentum "
          "+ community + demand)...")
    with_demand = 0
    for r in tqdm(rows):
        csub, creason = community_signal(r["ticker"], community)
        dsub, dreason = demand_signal(r["ticker"], history.get(r["ticker"]))
        if dsub is not None:
            with_demand += 1
        result = compute_score(
            r,
            history.get(r["ticker"]),
            config.NEAR_THRESHOLD_PCT,
            csub,
            creason,
            dsub,
            dreason,
        )
        r["score"] = result.score
        r["community"] = round(result.subscores["community"] * 100, 1)
        r["demand"] = round(result.subscores["demand"] * 100, 1)
        r["short_pct"] = short_volume_pct(r["ticker"], shorts)
        r["reasons"] = result.reasons
    print(f"      {with_demand}/{len(rows)} rows have demand data")
    # Rank by composite score (desc); tie-break cheapest first.
    rows.sort(key=lambda r: (-r["score"], r["last_close"]))

    print("[9/10] Fetching balance-sheet fundamentals for the detail panel...")
    with_fundamentals = 0
    for r in tqdm(rows):
        data = fetch_fundamentals(r["ticker"])
        if data is not None:
            r["fundamentals"] = data
            with_fundamentals += 1
    print(f"      {with_fundamentals}/{len(rows)} rows have a balance sheet")

    print("[10/10] Rendering report.html...")
    path = render_report(rows)

    us = sum(1 for r in rows if r["market"] == "US")
    eu = sum(1 for r in rows if r["market"] == "EU")
    print(f"Wrote {path.name} with {len(rows)} rows ({us} US, {eu} EU)")


if __name__ == "__main__":
    run()
