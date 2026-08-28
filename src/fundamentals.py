"""Balance-sheet fundamentals for the report's per-stock detail panel.

Source: Yahoo Finance via `yfinance.Ticker(sym).balance_sheet` (annual) and
`.quarterly_balance_sheet`. No API key, no extra dependency.

Only real values are emitted: a line item that Yahoo does not report is simply
absent from the payload (never zero-filled, never estimated). If the whole
balance sheet is missing, `fetch_fundamentals` returns None and the report shows
"no data" for that ticker.

Responses are cached to `.cache/fundamentals/{symbol}.json` for 24 h.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import yfinance as yf

from . import config

# key -> (display label, Yahoo row-name candidates in priority order)
_LINE_ITEMS: list[tuple[str, str, tuple[str, ...]]] = [
    ("current_assets", "Total current assets", ("Current Assets",)),
    (
        "non_current_assets",
        "Total non-current assets",
        ("Total Non Current Assets",),
    ),
    ("total_assets", "Total assets", ("Total Assets",)),
    ("current_liabilities", "Total current liabilities", ("Current Liabilities",)),
    (
        "non_current_liabilities",
        "Total non-current liabilities",
        ("Total Non Current Liabilities Net Minority Interest",),
    ),
    (
        "total_liabilities",
        "Total liabilities",
        ("Total Liabilities Net Minority Interest",),
    ),
    (
        "equity",
        "Total equity",
        ("Stockholders Equity", "Total Equity Gross Minority Interest"),
    ),
    (
        "equity_gross",
        "Total equity incl. minority interest",
        ("Total Equity Gross Minority Interest",),
    ),
    ("total_debt", "Total debt", ("Total Debt",)),
    (
        "cash",
        "Cash & equivalents",
        ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
    ),
]

LINE_ITEM_LABELS = [{"key": k, "label": lbl} for k, lbl, _ in _LINE_ITEMS]

_MAX_PERIODS = 4

# A reported total may differ from the sum of its parts by at most this fraction.
TOTAL_TOLERANCE = 0.01

_TOTALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("total_assets", ("current_assets", "non_current_assets")),
    ("total_assets", ("total_liabilities", "equity_gross")),
    ("total_liabilities", ("current_liabilities", "non_current_liabilities")),
)


def _cache_path(symbol: str) -> Path:
    return config.FUNDAMENTALS_CACHE_DIR / f"{symbol.replace('/', '_')}.json"


def _read_cache(symbol: str) -> dict | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > config.CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _write_cache(symbol: str, payload: dict) -> None:
    config.FUNDAMENTALS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(symbol).write_text(json.dumps(payload), encoding="utf-8")


def _clean(value) -> float | None:
    """Return a finite float, or None when Yahoo has no value for the cell."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(num) or math.isinf(num) else num


def _drop_inconsistent_totals(values: dict[str, float], symbol: str, date: str) -> None:
    """Discard a reported total that contradicts its own components.

    Yahoo occasionally publishes a broken total (ALC.SW reports total liabilities
    equal to total assets). Such a value is dropped, never silently corrected:
    the panel then shows the components only.
    """
    for total_key, parts in _TOTALS:
        if total_key not in values or not all(p in values for p in parts):
            continue
        total = values[total_key]
        if total == 0:
            continue
        if abs(sum(values[p] for p in parts) - total) / abs(total) > TOTAL_TOLERANCE:
            print(f"[fundamentals] {symbol} {date}: dropped inconsistent {total_key}")
            del values[total_key]


def _extract(df, symbol: str = "?") -> list[dict]:
    """Turn a yfinance balance-sheet frame into a list of period dicts."""
    if df is None or getattr(df, "empty", True):
        return []

    periods: list[dict] = []
    for column in list(df.columns)[:_MAX_PERIODS]:
        values: dict[str, float] = {}
        for key, _label, candidates in _LINE_ITEMS:
            for row_name in candidates:
                if row_name not in df.index:
                    continue
                num = _clean(df.at[row_name, column])
                if num is not None:
                    values[key] = num
                    break
        if not values:
            continue
        date = str(getattr(column, "date", lambda: column)())
        _drop_inconsistent_totals(values, symbol, date)
        periods.append({"date": date, "values": values})
    return periods


def fetch_fundamentals(ticker: str) -> dict | None:
    """Return {currency, annual: [...], quarterly: [...]} or None if unavailable."""
    cached = _read_cache(ticker)
    if cached is not None:
        return cached or None

    try:
        handle = yf.Ticker(ticker)
        annual = _extract(handle.balance_sheet, ticker)
        quarterly = _extract(handle.quarterly_balance_sheet, ticker)
    except Exception as exc:  # noqa: BLE001 - network/parse errors
        print(f"[fundamentals] {ticker}: Yahoo error ({type(exc).__name__})")
        return None

    if not annual and not quarterly:
        _write_cache(ticker, {})
        return None

    currency = ""
    try:
        currency = str(getattr(handle.fast_info, "currency", "") or "")
    except Exception:  # noqa: BLE001
        currency = ""

    payload = {"currency": currency, "annual": annual, "quarterly": quarterly}
    _write_cache(ticker, payload)
    return payload


if __name__ == "__main__":
    for t in ["AAPL", "SAP.DE"]:
        data = fetch_fundamentals(t)
        if data is None:
            print(f"{t}: no balance sheet")
            continue
        latest = (data["quarterly"] or data["annual"])[0]
        print(f"{t} ({data['currency']}) {latest['date']}: {latest['values']}")
