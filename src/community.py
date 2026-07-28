"""Community attention signal via ApeWisdom (free, no API key).

ApeWisdom aggregates Reddit (r/wallstreetbets et al.) ticker mentions and
upvotes. It exposes *attention / buzz*, not a verified bullish-vs-bearish
sentiment, so this is deliberately a small-weight, US-only signal built from:
  * mention volume (log-scaled attention)
  * 24h mention growth (rising interest)

Coverage: ApeWisdom uses US symbols only. EU tickers (with an exchange suffix)
and any symbol absent from the feed get a neutral score with no reason.

Security: community metrics are the highest manipulation risk of any source
(pump-and-dump, bot brigading). Keep the weight small and never let it dominate;
the mention text itself is never fed back into the model.
"""
from __future__ import annotations

import json
import math
import time

import requests

from . import config

APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
_MAP_CACHE = config.APEWISDOM_CACHE_DIR / "map.json"
_MAP_TTL_SECONDS = 6 * 60 * 60  # 6 hours (buzz moves fast)
_MAX_PAGES = 20  # safety cap


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _fetch_all() -> dict:
    """Page through ApeWisdom and return ticker -> attention metrics."""
    out: dict[str, dict] = {}
    page = 1
    pages = 1
    while page <= pages and page <= _MAX_PAGES:
        try:
            resp = requests.get(
                APEWISDOM_URL.format(page=page), timeout=config.HTTP_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[community] ApeWisdom page {page} failed "
                  f"({type(exc).__name__}); using partial data")
            break
        pages = int(data.get("pages", 1) or 1)
        for r in data.get("results", []):
            ticker = str(r.get("ticker", "")).upper().strip()
            if not ticker:
                continue
            out[ticker] = {
                "mentions": int(r.get("mentions", 0) or 0),
                "upvotes": int(r.get("upvotes", 0) or 0),
                "rank": int(r.get("rank", 0) or 0),
                "mentions_24h_ago": int(r.get("mentions_24h_ago", 0) or 0),
            }
        page += 1
        time.sleep(0.3)
    return out


def get_community_map(force: bool = False) -> dict:
    """Return the (cached) ApeWisdom attention map: ticker -> metrics."""
    if (
        not force
        and _MAP_CACHE.exists()
        and time.time() - _MAP_CACHE.stat().st_mtime < _MAP_TTL_SECONDS
    ):
        try:
            return json.loads(_MAP_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    data = _fetch_all()
    if data:
        config.APEWISDOM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _MAP_CACHE.write_text(json.dumps(data), encoding="utf-8")
    return data


def community_signal(ticker: str, cmap: dict) -> tuple[float | None, str | None]:
    """Return (subscore 0..1, reason) for a ticker, or (None, None) if no data.

    US-only: EU suffixed tickers and symbols absent from ApeWisdom are neutral.
    """
    if "." in ticker:  # EU exchange suffix -> not covered
        return None, None

    key = ticker.upper()
    entry = cmap.get(key) or cmap.get(key.replace("-", "."))  # BRK-B -> BRK.B
    if entry is None:
        return None, None

    mentions = entry["mentions"]
    prev = entry.get("mentions_24h_ago", 0)

    # Attention: ~500 mentions treated as "very high".
    attention = _clamp(math.log10(mentions + 1) / math.log10(501))

    # Growth: rising mentions vs 24h ago (flat = 0.5, +50% = 1.0, -50% = 0.0).
    if prev > 0:
        growth = _clamp(mentions / prev - 0.5)
    else:
        growth = 0.6 if mentions > 0 else 0.5

    # 24h trend arrow for the human-readable reason.
    if prev <= 0:
        arrow = "\u25B2" if mentions > 0 else "\u25AC"  # new interest / flat
    elif mentions > prev * 1.1:
        arrow = "\u25B2"  # up
    elif mentions < prev * 0.9:
        arrow = "\u25BC"  # down
    else:
        arrow = "\u25AC"  # flat

    sub = _clamp(0.6 * attention + 0.4 * growth)
    reason = f"Reddit: {mentions} mentions {arrow} (rank {entry['rank']})"
    return sub, reason


if __name__ == "__main__":
    cmap = get_community_map()
    print(f"ApeWisdom tickers: {len(cmap)}")
    for t in ["MU", "AAPL", "NVDA", "SAP.DE"]:
        print(f"{t}: {community_signal(t, cmap)}")
