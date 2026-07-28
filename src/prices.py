"""Price history downloader (Yahoo Finance via yfinance).

Batch-downloads 1 year of daily OHLC for the whole universe and returns a
per-ticker DataFrame with raw (non-adjusted) prices.
"""
from __future__ import annotations

import time
from collections.abc import Iterable

import pandas as pd
import yfinance as yf


def _normalise_single(df: pd.DataFrame) -> pd.DataFrame | None:
    """Ensure a per-ticker frame has the expected OHLC columns and drop NaNs."""
    if df is None or df.empty:
        return None
    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(set(df.columns)):
        return None
    df = df.dropna(subset=["High", "Low", "Close"])
    if df.empty:
        return None
    return df


def download_history(
    tickers: Iterable[str],
    period: str = "1y",
    interval: str = "1d",
    batch_size: int = 100,
) -> dict[str, pd.DataFrame]:
    """Download OHLC history for many tickers.

    Returns a dict mapping ticker -> raw OHLC DataFrame. Tickers that fail or
    return no data are omitted (with a warning), never fabricated.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]  # de-dupe, keep order
    out: dict[str, pd.DataFrame] = {}

    for start in range(0, len(tickers), batch_size):
        batch = tickers[start : start + batch_size]
        try:
            data = yf.download(
                batch,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[prices] Batch download failed ({batch[0]}..): {exc}")
            continue

        if data is None or data.empty:
            continue

        if len(batch) == 1:
            frame = _normalise_single(data)
            if frame is not None:
                out[batch[0]] = frame
        else:
            # Columns are a MultiIndex keyed by ticker at the top level.
            for tkr in batch:
                if tkr not in data.columns.get_level_values(0):
                    continue
                frame = _normalise_single(data[tkr])
                if frame is not None:
                    out[tkr] = frame

        time.sleep(0.5)  # be gentle with Yahoo

    missing = len(tickers) - len(out)
    if missing:
        print(f"[prices] {missing}/{len(tickers)} tickers returned no usable history.")
    return out


if __name__ == "__main__":
    sample = ["AAPL", "MSFT", "SAP.DE", "NESN.SW"]
    hist = download_history(sample)
    for k, v in hist.items():
        print(f"{k}: {len(v)} bars, last close = {v['Close'].iloc[-1]:.2f}")
