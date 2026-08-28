"""Universe loaders.

Builds the combined US + EU screening universe from live Wikipedia pages.
Never falls back to a hard-coded list: if parsing fails, a clear error is raised
pointing at the offending URL.

Public API:
    get_sp500()   -> DataFrame[ticker, name, exchange, market]
    get_stoxx600()-> DataFrame[ticker, name, exchange, market]
    get_universe()-> concatenation of the two, de-duplicated on ticker
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from . import config
from .symbols import yahoo_variants

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
STOXX600_URL = "https://en.wikipedia.org/wiki/STOXX_Europe_600"

_HEADERS = {"User-Agent": "Mozilla/5.0 (stocks-near-support/1.0)"}

# Country name (as it appears on Wikipedia) -> Yahoo Finance exchange suffix.
COUNTRY_SUFFIX = {
    "Germany": ".DE",
    "France": ".PA",
    "Netherlands": ".AS",
    "Switzerland": ".SW",
    "United Kingdom": ".L",
    "UK": ".L",
    "Great Britain": ".L",
    "Italy": ".MI",
    "Spain": ".MC",
    "Sweden": ".ST",
    "Denmark": ".CO",
    "Belgium": ".BR",
    "Finland": ".HE",
    "Norway": ".OL",
    "Ireland": ".IR",
    "Portugal": ".LS",
    "Austria": ".VI",
    "Poland": ".WA",
    "Luxembourg": ".LU",
    "Czech Republic": ".PR",
    "Czechia": ".PR",
    "Greece": ".AT",
}


def _fetch_html(url: str) -> str:
    """GET a page with timeout + one exponential-backoff retry."""
    last_exc: Exception | None = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=config.HTTP_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001 - re-raised below
            last_exc = exc
            if attempt < config.HTTP_RETRIES:
                import time

                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_exc}")


def _read_tables(url: str) -> list[pd.DataFrame]:
    html = _fetch_html(url)
    try:
        return pd.read_html(io.StringIO(html))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Could not parse any HTML table from {url}. The page layout may "
            f"have changed. Original error: {exc}"
        ) from exc


def get_sp500() -> pd.DataFrame:
    """Return the S&P 500 constituents as DataFrame[ticker, name, exchange, market]."""
    tables = _read_tables(SP500_URL)
    table = None
    for df in tables:
        cols = {str(c).strip().lower() for c in df.columns}
        if "symbol" in cols and ("security" in cols or "company" in cols):
            table = df
            break
    if table is None:
        raise RuntimeError(
            f"Could not locate the S&P 500 constituents table at {SP500_URL}. "
            "Expected columns 'Symbol' and 'Security'."
        )

    table = table.rename(columns={str(c): str(c).strip() for c in table.columns})
    name_col = "Security" if "Security" in table.columns else "Company"
    out = pd.DataFrame(
        {
            "ticker": table["Symbol"].astype(str).str.strip(),
            "name": table[name_col].astype(str).str.strip(),
        }
    )
    # Yahoo uses '-' instead of '.' for class shares (e.g. BRK.B -> BRK-B).
    out["ticker"] = out["ticker"].str.replace(".", "-", regex=False)
    out["exchange"] = "US"
    out["market"] = "US"
    out = out[out["ticker"].str.len() > 0].drop_duplicates("ticker").reset_index(drop=True)
    return out


def _pick_stoxx_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """Choose the constituents table: has a ticker-ish and a country column,
    and the most rows."""
    best: pd.DataFrame | None = None
    best_rows = -1
    for df in tables:
        cols = {str(c).strip().lower() for c in df.columns}
        has_ticker = any("ticker" in c or "symbol" in c for c in cols)
        has_country = any("country" in c for c in cols)
        if has_ticker and has_country and len(df) > best_rows:
            best = df
            best_rows = len(df)
    if best is None:
        raise RuntimeError(
            f"Could not locate the STOXX Europe 600 constituents table at "
            f"{STOXX600_URL}. Expected 'Ticker' and 'Country' columns."
        )
    return best


def get_stoxx600() -> pd.DataFrame:
    """Return STOXX Europe 600 constituents as DataFrame[ticker, name, exchange, market].

    Applies the country -> Yahoo suffix mapping. Rows in unmapped countries are
    dropped with a warning.
    """
    tables = _read_tables(STOXX600_URL)
    table = _pick_stoxx_table(tables)
    table = table.rename(columns={c: str(c).strip() for c in table.columns})

    def _find(colnames: list[str], *needles: str) -> str | None:
        for c in colnames:
            low = c.lower()
            if all(n in low for n in needles):
                return c
        return None

    cols = list(table.columns)
    ticker_col = _find(cols, "ticker") or _find(cols, "symbol")
    country_col = _find(cols, "country")
    name_col = (
        _find(cols, "name")
        or _find(cols, "company")
        or _find(cols, "constituent")
    )
    if ticker_col is None or country_col is None:
        raise RuntimeError(
            f"STOXX table at {STOXX600_URL} is missing a ticker or country "
            f"column. Found columns: {cols}"
        )

    rows = []
    dropped: dict[str, int] = {}
    for _, r in table.iterrows():
        raw_ticker = str(r[ticker_col]).strip()
        country = str(r[country_col]).strip()
        name = str(r[name_col]).strip() if name_col else raw_ticker
        if not raw_ticker or raw_ticker.lower() == "nan":
            continue
        suffix = COUNTRY_SUFFIX.get(country)
        if not suffix:
            dropped[country] = dropped.get(country, 0) + 1
            continue
        # "VOLV B" -> VOLV-B.ST (share class), "SAP" -> SAP.DE.
        variants = yahoo_variants(raw_ticker, suffix)
        if not variants:
            continue
        rows.append(
            {
                "ticker": variants[0],
                "name": name,
                "exchange": suffix.lstrip("."),
                "market": "EU",
            }
        )

    if dropped:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(dropped.items()))
        print(f"[universe] Dropped rows with unmapped country suffix -> {summary}")

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError(
            f"Parsed the STOXX table at {STOXX600_URL} but produced 0 usable "
            "tickers. The ticker/country columns may have changed format."
        )
    out = out.drop_duplicates("ticker").reset_index(drop=True)
    return out


def get_universe() -> pd.DataFrame:
    """Return the combined US + EU universe."""
    us = get_sp500()
    eu = get_stoxx600()
    combined = pd.concat([us, eu], ignore_index=True)
    combined = combined.drop_duplicates("ticker").reset_index(drop=True)
    return combined


if __name__ == "__main__":
    sp = get_sp500()
    st = get_stoxx600()
    print(f"S&P 500:   {len(sp)} tickers")
    print(sp.head(10).to_string(index=False))
    print()
    print(f"STOXX 600: {len(st)} tickers")
    print(st.head(10).to_string(index=False))
