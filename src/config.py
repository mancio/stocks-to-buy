"""Configuration loader.

Reads the `.env` file (if present) and exposes typed constants used across the
pipeline. Never hard-codes secrets; the Finnhub key must come from the
environment.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of the `src` package.
ROOT = Path(__file__).resolve().parent.parent

# Load variables from a local `.env` if it exists (does not override real env).
load_dotenv(ROOT / ".env")

FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "").strip()

try:
    NEAR_THRESHOLD_PCT: float = float(os.getenv("NEAR_THRESHOLD_PCT", "5.0"))
except ValueError:
    NEAR_THRESHOLD_PCT = 5.0

# Buy-side demand (order-book) provider. "none" disables the signal entirely.
DEMAND_PROVIDER: str = os.getenv("DEMAND_PROVIDER", "derived").strip().lower()
DATABENTO_API_KEY: str = os.getenv("DATABENTO_API_KEY", "").strip()
DATABENTO_DATASET: str = os.getenv("DATABENTO_DATASET", "XNAS.ITCH").strip()
# bbo-1m is ~390 records/symbol/session; mbp-10 is orders of magnitude larger.
DATABENTO_SCHEMA: str = os.getenv("DATABENTO_SCHEMA", "bbo-1m").strip()

# Network defaults (all calls must respect these).
HTTP_TIMEOUT = 15  # seconds
HTTP_RETRIES = 1  # one retry with exponential backoff

# On-disk cache locations.
CACHE_DIR = ROOT / ".cache"
FINNHUB_CACHE_DIR = CACHE_DIR / "finnhub"
YAHOO_CACHE_DIR = CACHE_DIR / "yahoo"
APEWISDOM_CACHE_DIR = CACHE_DIR / "apewisdom"
FUNDAMENTALS_CACHE_DIR = CACHE_DIR / "fundamentals"
SYMBOLS_CACHE_DIR = CACHE_DIR / "symbols"
DEMAND_CACHE_DIR = CACHE_DIR / "demand"
FINRA_CACHE_DIR = CACHE_DIR / "finra"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
SYMBOL_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # symbol names change rarely

# Support algorithm constants.
LOOKBACK_DAYS = 252  # ~1 trading year
SWING_WINDOW = 5  # fractal half-window (both sides)
CLUSTER_TOLERANCE_PCT = 1.5  # cluster swing lows within this % of each other


def require_finnhub_key() -> str:
    """Return the Finnhub key or raise a clear error if it is missing."""
    if not FINNHUB_API_KEY:
        raise RuntimeError(
            "FINNHUB_API_KEY is not set. Register free at "
            "https://finnhub.io/register and put the key in a `.env` file "
            "(see `.env.example`)."
        )
    return FINNHUB_API_KEY


if __name__ == "__main__":
    print(f"ROOT              = {ROOT}")
    print(f"FINNHUB_API_KEY   = {'<set>' if FINNHUB_API_KEY else '<MISSING>'}")
    print(f"NEAR_THRESHOLD_PCT= {NEAR_THRESHOLD_PCT}")
    print(f"DEMAND_PROVIDER   = {DEMAND_PROVIDER}")
    print(f"DATABENTO_API_KEY = {'<set>' if DATABENTO_API_KEY else '<MISSING>'}")
