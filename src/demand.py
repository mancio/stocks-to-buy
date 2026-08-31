"""Buy-side demand signal from real order-book data (Databento).

Every other signal in this project is derived from *executed* trades or from
opinions (analysts, Reddit). This one measures what is actually resting on the
book waiting to buy: the bid side of the quote, in shares and in currency.

Metric
------
For each quote snapshot in the sampled session::

    imbalance = bid_size / (bid_size + ask_size)

Averaged over the session this gives a 0..1 number: 0.5 = balanced book,
> 0.5 = more size queued to buy than to sell. That average *is* the sub-score,
so the signal needs no arbitrary rescaling.

The reason string also carries the average resting bid notional
(`avg bid size x mid price`), which is the closest thing a retail-accessible
feed gives to "how much money wants to buy this stock right now".

Cost
----
`bbo-1m` is one consolidated best-bid/offer snapshot per minute, ~390 records
per symbol per session — cheap enough to run daily against Databento's $125 of
free signup credits. `mbp-10` (ten levels of depth) is far more informative and
far more expensive; select it via `DATABENTO_SCHEMA` only if you know your
credit budget covers it.

Coverage
--------
US listings only. EU tickers carry an exchange suffix and are not in the
US equities datasets, so they get a neutral score and no reason — the same
asymmetry the ApeWisdom community signal already has.

Security
--------
The Databento key is read from the environment and passed to the SDK; it never
appears in a URL, a cache file, or a log line. Failures are reported by
exception type only.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta

from . import config

_CACHE_TTL_SECONDS = 24 * 60 * 60
# Sample this many calendar days back and keep the most recent session found.
_LOOKBACK_DAYS = 5
# Databento embargoes the most recent data on the historical API.
_EMBARGO_DAYS = 1
_MIN_SNAPSHOTS = 20  # below this the session is too thin to score


@dataclass
class Demand:
    """Resting-order summary for one symbol over one session."""

    symbol: str
    session: str  # YYYY-MM-DD of the sampled session
    snapshots: int
    imbalance: float  # 0..1, share of resting size sitting on the bid
    avg_bid_size: float  # shares
    avg_ask_size: float  # shares
    avg_bid_notional: float  # avg_bid_size * mid price, in the listing currency


def _cache_path(symbol: str):
    return config.DEMAND_CACHE_DIR / f"{symbol}.json"


def _read_cache(symbol: str) -> Demand | None:
    path = _cache_path(symbol)
    if not path.exists() or time.time() - path.stat().st_mtime >= _CACHE_TTL_SECONDS:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt cache entry is just a miss
        return None
    if not payload:  # cached "no data for this symbol"
        return None
    return Demand(**payload)


def _write_cache(symbol: str, demand: Demand | None) -> None:
    config.DEMAND_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body = asdict(demand) if demand is not None else {}
    _cache_path(symbol).write_text(json.dumps(body), encoding="utf-8")


def _summarise(records) -> Demand | None:
    """Fold Databento BBO/MBP records into a single-session Demand summary."""
    bid_size = ask_size = notional = 0.0
    count = 0
    session = ""

    for rec in records:
        levels = getattr(rec, "levels", None)
        if not levels:
            continue
        # bbo-* and mbp-1 expose one level; mbp-10 exposes ten.
        bids = sum(float(lv.bid_sz) for lv in levels)
        asks = sum(float(lv.ask_sz) for lv in levels)
        if bids <= 0 or asks <= 0:
            continue  # one-sided book (auction, halt) tells us nothing
        top = levels[0]
        # Databento prices are fixed-point with 9 implied decimals.
        mid = (float(top.bid_px) + float(top.ask_px)) / 2e9
        if mid <= 0:
            continue

        bid_size += bids
        ask_size += asks
        notional += bids * mid
        count += 1
        session = rec.pretty_ts_event.date().isoformat()

    if count < _MIN_SNAPSHOTS:
        return None

    total = bid_size + ask_size
    return Demand(
        symbol="",
        session=session,
        snapshots=count,
        imbalance=bid_size / total,
        avg_bid_size=bid_size / count,
        avg_ask_size=ask_size / count,
        avg_bid_notional=notional / count,
    )


def fetch_demand(symbol: str) -> Demand | None:
    """Return the cached-or-fetched resting-order summary for a US symbol."""
    if config.DEMAND_PROVIDER != "databento" or not config.DATABENTO_API_KEY:
        return None
    if "." in symbol:  # EU exchange suffix -> not in the US equities datasets
        return None

    cached = _read_cache(symbol)
    if cached is not None:
        return cached

    try:
        import databento as db
    except ImportError:
        print("[demand] databento package not installed; skipping demand signal")
        return None

    end = date.today() - timedelta(days=_EMBARGO_DAYS)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    try:
        client = db.Historical(config.DATABENTO_API_KEY)
        data = client.timeseries.get_range(
            dataset=config.DATABENTO_DATASET,
            schema=config.DATABENTO_SCHEMA,
            symbols=[symbol],
            start=start.isoformat(),
            end=end.isoformat(),
        )
        demand = _summarise(data)
    except Exception as exc:  # noqa: BLE001 - never leak the request or the key
        print(f"[demand] {symbol}: {type(exc).__name__}; skipping")
        return None

    if demand is not None:
        demand.symbol = symbol
    _write_cache(symbol, demand)
    return demand


def _fmt_shares(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:.0f}"


def _fmt_money(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return f"{n:.0f}"


def demand_signal(symbol: str) -> tuple[float | None, str | None]:
    """Return (subscore 0..1, reason) for a symbol, or (None, None) if no data."""
    demand = fetch_demand(symbol)
    if demand is None:
        return None, None

    arrow = "\u25B2" if demand.imbalance > 0.55 else (
        "\u25BC" if demand.imbalance < 0.45 else "\u25AC"
    )
    reason = (
        f"Book: {demand.imbalance * 100:.0f}% bid-side {arrow} "
        f"({_fmt_shares(demand.avg_bid_size)} sh / "
        f"{_fmt_money(demand.avg_bid_notional)} resting to buy)"
    )
    return demand.imbalance, reason


if __name__ == "__main__":
    for sym in ["AAPL", "MU", "SAP.DE"]:
        print(sym, demand_signal(sym))
