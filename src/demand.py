"""Buy-side demand signal.

Every other signal in this project is derived from *executed* trades or from
opinions (analysts, Reddit). This one tries to measure demand itself. Two
providers are supported, selected with `DEMAND_PROVIDER`:

``derived`` (default, free)
    Chaikin Money Flow over the daily bars already downloaded for the support
    algorithm. No API, no key, no account, and it covers **EU tickers too** --
    the only demand provider here that does. It is an *inference* from where
    each close landed inside its bar, not a measurement of resting orders::

        MFM = ((C - L) - (H - C)) / (H - L)
        CMF = sum(MFM * V) / sum(V)      over the last 20 bars

    A close near the high means buyers absorbed the session, so that bar's
    volume counts as accumulation. CMF lands in [-1, 1]; the sub-score is
    `(CMF + 1) / 2`.

``databento`` (paid, real order book)
    What is actually resting on the book waiting to buy::

        imbalance = bid_size / (bid_size + ask_size)

    Averaged over the sampled session. 0.5 = balanced book, > 0.5 = more size
    queued to buy than to sell. Stronger evidence than CMF, but US-only and it
    needs credits.

Both return the same `(subscore 0..1, reason)` pair, so the rest of the
pipeline does not care which one is active. The reason string always carries a
share *quantity* and a *currency value*, which is the point of the signal.

Cost (databento only)
---------------------
`bbo-1m` is one consolidated best-bid/offer snapshot per minute, ~390 records
per symbol per session -- cheap enough to run daily against Databento's $125 of
free signup credits. `mbp-10` (ten levels of depth) is far more informative and
far more expensive; select it via `DATABENTO_SCHEMA` only if you know your
credit budget covers it.

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

import pandas as pd

from . import config

_CACHE_TTL_SECONDS = 24 * 60 * 60
# Sample this many calendar days back and keep the most recent session found.
_LOOKBACK_DAYS = 5
# Databento embargoes the most recent data on the historical API.
_EMBARGO_DAYS = 1
_MIN_SNAPSHOTS = 20  # below this the session is too thin to score

# Chaikin Money Flow window, in trading days.
CMF_WINDOW = 20


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


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
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:.0f}"


def _fmt_money(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return f"{n:.0f}"


def chaikin_money_flow(history: pd.DataFrame, window: int = CMF_WINDOW) -> float | None:
    """Chaikin Money Flow over the last `window` bars, in [-1, 1].

    Bars with no intraday range (H == L) carry no directional information and
    are skipped rather than treated as neutral.
    """
    if history is None or history.empty:
        return None
    needed = {"High", "Low", "Close", "Volume"}
    if not needed.issubset(history.columns):
        return None

    bars = history.tail(window)
    high = bars["High"].astype(float)
    low = bars["Low"].astype(float)
    close = bars["Close"].astype(float)
    volume = bars["Volume"].astype(float)

    span = high - low
    usable = (span > 0) & (volume > 0)
    if usable.sum() < window // 2:  # too few real bars to trust the reading
        return None

    mfm = ((close - low) - (high - close))[usable] / span[usable]
    vol = volume[usable]
    total = float(vol.sum())
    if total <= 0:
        return None
    return float((mfm * vol).sum() / total)


def _derived_signal(history: pd.DataFrame) -> tuple[float | None, str | None]:
    """Money-flow demand estimate from the daily bars already downloaded."""
    cmf = chaikin_money_flow(history)
    if cmf is None:
        return None, None

    sub = _clamp((cmf + 1.0) / 2.0)
    bars = history.tail(CMF_WINDOW)
    avg_volume = float(bars["Volume"].astype(float).mean())
    last_close = float(bars["Close"].astype(float).iloc[-1])
    # CMF is the volume-weighted share of accumulation, so CMF x volume is the
    # net number of shares the money flow attributes to buyers each day.
    net_shares = cmf * avg_volume

    arrow = "\u25B2" if cmf > 0.05 else ("\u25BC" if cmf < -0.05 else "\u25AC")
    sign = "+" if net_shares >= 0 else "-"
    reason = (
        f"Flow: {sub * 100:.0f}% buy-side {arrow} "
        f"({sign}{_fmt_shares(abs(net_shares))} sh / "
        f"{sign}{_fmt_money(abs(net_shares) * last_close)} per day)"
    )
    return sub, reason


def _order_book_signal(symbol: str) -> tuple[float | None, str | None]:
    """Resting-order demand from Databento."""
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


def demand_signal(
    symbol: str, history: pd.DataFrame | None = None
) -> tuple[float | None, str | None]:
    """Return (subscore 0..1, reason) for a symbol, or (None, None) if no data.

    Dispatches on `DEMAND_PROVIDER`. `history` is the same OHLCV frame already
    used for the support algorithm; only the `derived` provider needs it.
    """
    if config.DEMAND_PROVIDER == "derived":
        return _derived_signal(history)
    if config.DEMAND_PROVIDER == "databento":
        return _order_book_signal(symbol)
    return None, None


if __name__ == "__main__":
    from .prices import download_history

    hist = download_history(["AAPL", "MU", "SAP.DE"])
    for sym in ["AAPL", "MU", "SAP.DE"]:
        print(sym, demand_signal(sym, hist.get(sym)))
