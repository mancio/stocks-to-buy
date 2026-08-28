"""Balance-sheet extraction: no fabricated cells, and the numbers add up."""
from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from src import config
from src.fundamentals import TOTAL_TOLERANCE, _clean, _extract


def test_clean_rejects_non_finite_and_non_numeric():
    assert _clean(float("nan")) is None
    assert _clean(float("inf")) is None
    assert _clean(None) is None
    assert _clean("n/a") is None
    assert _clean("12.5") == pytest.approx(12.5)
    assert _clean(-3) == pytest.approx(-3.0)


def _frame(rows: dict[str, list], dates: list[str]) -> pd.DataFrame:
    """Mimic a yfinance balance sheet: index = line items, columns = period dates."""
    return pd.DataFrame(rows, index=pd.to_datetime(dates)).T


def test_extract_maps_yahoo_rows_and_dates():
    df = _frame(
        {
            "Current Assets": [100.0, 90.0],
            "Total Non Current Assets": [400.0, 410.0],
            "Total Assets": [500.0, 500.0],
        },
        ["2026-06-30", "2026-03-31"],
    )

    periods = _extract(df)

    assert [p["date"] for p in periods] == ["2026-06-30", "2026-03-31"]
    assert periods[0]["values"]["current_assets"] == pytest.approx(100.0)
    assert periods[0]["values"]["total_assets"] == pytest.approx(500.0)


def test_missing_cells_are_omitted_never_zero_filled():
    df = _frame(
        {"Current Assets": [100.0, float("nan")], "Total Assets": [500.0, 480.0]},
        ["2026-06-30", "2026-03-31"],
    )

    periods = _extract(df)

    assert "current_assets" in periods[0]["values"]
    assert "current_assets" not in periods[1]["values"]
    assert "total_liabilities" not in periods[0]["values"]


def test_extract_caps_the_number_of_periods():
    dates = [f"202{i}-12-31" for i in range(6)]
    df = _frame({"Total Assets": [100.0] * 6}, dates)

    assert len(_extract(df)) == 4


def test_extract_handles_empty_input():
    assert _extract(None) == []
    assert _extract(pd.DataFrame()) == []


def test_total_contradicting_its_components_is_dropped():
    # Yahoo's ALC.SW bug: total liabilities reported equal to total assets.
    df = _frame(
        {
            "Current Liabilities": [3049.0],
            "Total Non Current Liabilities Net Minority Interest": [6471.0],
            "Total Liabilities Net Minority Interest": [31555.0],
        },
        ["2025-12-31"],
    )

    values = _extract(df)[0]["values"]

    assert "total_liabilities" not in values
    assert values["current_liabilities"] == pytest.approx(3049.0)
    assert values["non_current_liabilities"] == pytest.approx(6471.0)


def test_total_within_tolerance_is_kept():
    df = _frame(
        {
            "Current Assets": [100.0],
            "Total Non Current Assets": [400.0],
            "Total Assets": [500.0 * (1 + TOTAL_TOLERANCE / 2)],
        },
        ["2025-12-31"],
    )

    assert "total_assets" in _extract(df)[0]["values"]


# --------------------------------------------------------------------------- #
# Accounting identities over the real cached balance sheets
# --------------------------------------------------------------------------- #
def _cached_periods():
    if not config.FUNDAMENTALS_CACHE_DIR.exists():
        return []
    out = []
    for path in config.FUNDAMENTALS_CACHE_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not payload:
            continue
        for kind in ("annual", "quarterly"):
            for period in payload.get(kind, []):
                out.append((path.stem, kind, period))
    return out


def test_cached_values_are_finite_numbers():
    cached = _cached_periods()
    if not cached:
        pytest.skip("no cached balance sheets (run `python -m src.main`)")
    for symbol, kind, period in cached:
        for key, value in period["values"].items():
            assert isinstance(value, (int, float)), f"{symbol} {kind} {key}"
            assert math.isfinite(value), f"{symbol} {kind} {key}"


def test_current_plus_non_current_equals_total_assets():
    cached = _cached_periods()
    if not cached:
        pytest.skip("no cached balance sheets (run `python -m src.main`)")

    checked = 0
    for symbol, kind, period in cached:
        v = period["values"]
        if not {"current_assets", "non_current_assets", "total_assets"} <= v.keys():
            continue
        total = v["total_assets"]
        assert v["current_assets"] + v["non_current_assets"] == pytest.approx(
            total, rel=TOTAL_TOLERANCE
        ), f"{symbol} {kind} {period['date']}"
        checked += 1

    assert checked > 0, "no ticker reported the full asset breakdown"


def test_current_plus_non_current_equals_total_liabilities():
    cached = _cached_periods()
    if not cached:
        pytest.skip("no cached balance sheets (run `python -m src.main`)")

    checked = 0
    for symbol, kind, period in cached:
        v = period["values"]
        if not {
            "current_liabilities",
            "non_current_liabilities",
            "total_liabilities",
        } <= v.keys():
            continue
        assert v["current_liabilities"] + v["non_current_liabilities"] == pytest.approx(
            v["total_liabilities"], rel=TOTAL_TOLERANCE
        ), f"{symbol} {kind} {period['date']}"
        checked += 1

    assert checked > 0, "no ticker reported the full liability breakdown"


def test_balance_sheet_identity_assets_equal_liabilities_plus_equity():
    """A = L + E, with E including minority interest (which may be negative)."""
    cached = _cached_periods()
    if not cached:
        pytest.skip("no cached balance sheets (run `python -m src.main`)")

    checked = 0
    for symbol, kind, period in cached:
        v = period["values"]
        if not {"total_liabilities", "equity_gross", "total_assets"} <= v.keys():
            continue
        assert v["total_liabilities"] + v["equity_gross"] == pytest.approx(
            v["total_assets"], rel=TOTAL_TOLERANCE
        ), f"{symbol} {kind} {period['date']}"
        checked += 1

    assert checked > 0, "no ticker reported liabilities, equity and total assets"
