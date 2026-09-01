"""Short-sale volume from FINRA's free daily files.

FINRA publishes, for every US trading day, the share of consolidated volume
that was sold short. The files are plain pipe-delimited text on a public CDN:
no API key, no account, no scraping of a rendered page.

    https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt

    Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

What it is
----------
`short_pct = short_volume / total_volume`, as a percentage of that day's
consolidated tape. Market-wide this typically sits near 45-50%, because most
short volume is market-maker hedging rather than directional bearish bets.

What it is *not*
----------------
This is **not** short interest (open short positions, published fortnightly),
and it is deliberately **not** fed into the composite score. A high reading is
genuinely ambiguous -- it can mean bearish pressure or it can mean squeeze
fuel -- so the report displays the number and lets the reader judge, rather
than inventing a direction for it.

Coverage: US consolidated tape only. EU tickers carry an exchange suffix and
are absent from the file.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import requests

from . import config

FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"
_CACHE = config.FINRA_CACHE_DIR / "short_volume.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
# Walk back far enough to clear a long weekend plus FINRA's publication lag.
_MAX_LOOKBACK_DAYS = 7


def _parse(text: str) -> dict[str, dict]:
    """Parse one FINRA daily file into {symbol: {short, total, short_pct}}."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 5 or parts[0].strip().lower() == "date":
            continue  # header, footer ("Total"), or a malformed row
        symbol = parts[1].strip().upper()
        try:
            short = float(parts[2])
            total = float(parts[4])
        except ValueError:
            continue
        if not symbol or total <= 0 or short < 0 or short > total:
            continue
        out[symbol] = {
            "date": parts[0].strip(),
            "short": short,
            "total": total,
            "short_pct": short / total * 100.0,
        }
    return out


def _fetch_latest() -> dict[str, dict]:
    """Download the most recent published daily file, walking back if needed."""
    day = date.today()
    for _ in range(_MAX_LOOKBACK_DAYS):
        day -= timedelta(days=1)
        if day.weekday() >= 5:  # Saturday / Sunday: no file exists
            continue
        url = FINRA_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
        try:
            resp = requests.get(url, timeout=config.HTTP_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            print(f"[short] {day}: {type(exc).__name__}; trying an earlier day")
            continue
        if resp.status_code == 404:
            continue  # holiday, or not published yet
        if not resp.ok:
            print(f"[short] {day}: HTTP {resp.status_code}; trying an earlier day")
            continue
        parsed = _parse(resp.text)
        if parsed:
            return parsed
    print("[short] no FINRA daily file available; skipping short volume")
    return {}


def get_short_volume_map(force: bool = False) -> dict[str, dict]:
    """Return the (cached) map of US symbol -> short-volume stats."""
    if (
        not force
        and _CACHE.exists()
        and time.time() - _CACHE.stat().st_mtime < _CACHE_TTL_SECONDS
    ):
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt cache entry is just a miss
            pass

    data = _fetch_latest()
    if data:
        config.FINRA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(data), encoding="utf-8")
    return data


def short_volume_pct(ticker: str, smap: dict) -> float | None:
    """Short share of consolidated volume for a ticker, or None if uncovered."""
    if "." in ticker:  # EU exchange suffix -> not on the US tape
        return None
    entry = smap.get(ticker.upper()) or smap.get(ticker.upper().replace("-", "."))
    if entry is None:
        return None
    return round(entry["short_pct"], 1)


if __name__ == "__main__":
    smap = get_short_volume_map()
    print(f"FINRA symbols: {len(smap)}")
    for t in ["AAPL", "MU", "NVDA", "SAP.DE"]:
        print(t, short_volume_pct(t, smap))
