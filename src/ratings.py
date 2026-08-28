"""Analyst consensus ratings.

Hybrid source strategy (Finnhub's free tier only supports US listings):
  * US tickers  -> Finnhub `/stock/recommendation`
  * EU tickers  -> Yahoo Finance analyst recommendations (via yfinance)
  * If Finnhub fails for a US ticker (e.g. 403 / empty), fall back to Yahoo.

Both sources return the same monthly buckets (strongBuy/buy/hold/sell/strongSell);
we pick the most recent row, choose the winning bucket, and report the total
analyst count. Responses are cached on disk for 24 h. Never fabricates a rating;
on failure the symbol is reported as unrated and later dropped by main.

Security: the Finnhub token is sent as a query parameter, so it must never be
echoed in log output. All error messages below are redacted to the HTTP status.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import yfinance as yf

from . import config

FINNHUB_URL = "https://finnhub.io/api/v1/stock/recommendation"
_THROTTLE_SECONDS = 1.1  # free tier: 60 req/min

# Bucket -> human label. Tie-break priority order (highest first).
_BUCKET_ORDER = ["strongBuy", "buy", "hold", "sell", "strongSell"]
_BUCKET_LABEL = {
    "strongBuy": "Strong Buy",
    "buy": "Buy",
    "hold": "Hold",
    "sell": "Sell",
    "strongSell": "Strong Sell",
}

# Public aliases for the report layer.
BUCKET_ORDER = tuple(_BUCKET_ORDER)
BUCKET_LABEL = dict(_BUCKET_LABEL)


@dataclass
class Rating:
    label: str  # "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell"
    num_analysts: int
    counts: dict[str, int]  # per-bucket analyst counts, as reported by the source


def _pick_from_buckets(counts: dict[str, int]) -> Rating | None:
    total = sum(counts.values())
    if total == 0:
        return None
    best = max(_BUCKET_ORDER, key=lambda b: (counts[b], -_BUCKET_ORDER.index(b)))
    return Rating(label=_BUCKET_LABEL[best], num_analysts=total, counts=dict(counts))


def _cache_path(base_dir: Path, symbol: str) -> Path:
    safe = symbol.replace("/", "_")
    return base_dir / f"{safe}.json"


def _read_cache(base_dir: Path, symbol: str) -> object | None:
    path = _cache_path(base_dir, symbol)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > config.CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _write_cache(base_dir: Path, symbol: str, payload: object) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(base_dir, symbol).write_text(json.dumps(payload), encoding="utf-8")


def _is_eu(ticker: str) -> bool:
    """EU symbols carry an exchange suffix like `.DE`; US class shares use `-`."""
    return "." in ticker


# --------------------------------------------------------------------------- #
# Finnhub (US)
# --------------------------------------------------------------------------- #
def _finnhub_symbol(ticker: str) -> str:
    # Yahoo '-' class shares map back to Finnhub '.' (e.g. BRK-B -> BRK.B).
    return ticker.replace("-", ".")


def _finnhub_request(symbol: str) -> list:
    key = config.require_finnhub_key()
    params = {"symbol": symbol, "token": key}
    last_status = "unknown error"
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = requests.get(
                FINNHUB_URL, params=params, timeout=config.HTTP_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            # Redact: never surface the URL (it contains the token).
            code = exc.response.status_code if exc.response is not None else "?"
            last_status = f"HTTP {code}"
            if attempt < config.HTTP_RETRIES and code not in (401, 403):
                time.sleep(2 ** attempt)
            else:
                break
        except Exception as exc:  # noqa: BLE001 - network/JSON errors
            last_status = type(exc).__name__
            if attempt < config.HTTP_RETRIES:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Finnhub request failed ({last_status})")


def _fetch_finnhub(ticker: str, throttle: bool) -> Rating | None:
    symbol = _finnhub_symbol(ticker)
    payload = _read_cache(config.FINNHUB_CACHE_DIR, symbol)
    if payload is None:
        try:
            payload = _finnhub_request(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"[ratings] {ticker}: {exc}")
            return None
        _write_cache(config.FINNHUB_CACHE_DIR, symbol, payload)
        if throttle:
            time.sleep(_THROTTLE_SECONDS)

    if not payload:
        return None
    try:
        latest = sorted(payload, key=lambda r: r.get("period", ""), reverse=True)[0]
    except Exception:  # noqa: BLE001
        return None
    counts = {b: int(latest.get(b, 0) or 0) for b in _BUCKET_ORDER}
    return _pick_from_buckets(counts)


# --------------------------------------------------------------------------- #
# Yahoo (EU + US fallback)
# --------------------------------------------------------------------------- #
def _fetch_yahoo(ticker: str) -> Rating | None:
    cached = _read_cache(config.YAHOO_CACHE_DIR, ticker)
    if isinstance(cached, dict):
        counts = {b: int(cached.get(b, 0) or 0) for b in _BUCKET_ORDER}
        return _pick_from_buckets(counts)

    try:
        df = yf.Ticker(ticker).recommendations
    except Exception as exc:  # noqa: BLE001
        print(f"[ratings] {ticker}: Yahoo error ({type(exc).__name__})")
        return None

    if df is None or getattr(df, "empty", True):
        return None
    if not set(_BUCKET_ORDER).issubset(set(df.columns)):
        return None

    # yfinance returns rows for periods '0m' (current), '-1m', ... -> use current.
    row = df.iloc[0]
    counts = {b: int(row.get(b, 0) or 0) for b in _BUCKET_ORDER}
    _write_cache(config.YAHOO_CACHE_DIR, ticker, counts)
    return _pick_from_buckets(counts)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def fetch_rating(ticker: str, throttle: bool = True) -> Rating | None:
    """Return the consensus Rating for a Yahoo ticker, or None if unavailable.

    Routes EU tickers straight to Yahoo (Finnhub free tier rejects them), and US
    tickers to Finnhub with a Yahoo fallback.
    """
    if _is_eu(ticker):
        return _fetch_yahoo(ticker)

    rating = _fetch_finnhub(ticker, throttle=throttle)
    if rating is None:
        rating = _fetch_yahoo(ticker)
    return rating


if __name__ == "__main__":
    for t in ["AAPL", "MSFT", "SAP.DE", "NESN.SW"]:
        print(f"{t}: {fetch_rating(t)}")
