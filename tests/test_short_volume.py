"""FINRA daily short-volume file parsing (no network)."""
from __future__ import annotations

import pytest

from src.short_volume import _parse, short_volume_pct

HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"
SAMPLE = "\n".join(
    [
        HEADER,
        "20260828|AAPL|1000|0|4000|Q",
        "20260828|MU|3000|10|4000|Q",
        "20260828|NVDA|0|0|5000|Q",
        "Total|||||",
    ]
)


def test_short_percentage_is_short_over_total_volume():
    parsed = _parse(SAMPLE)
    assert parsed["AAPL"]["short_pct"] == pytest.approx(25.0)
    assert parsed["MU"]["short_pct"] == pytest.approx(75.0)
    assert parsed["NVDA"]["short_pct"] == pytest.approx(0.0)


def test_raw_volumes_and_date_are_preserved():
    entry = _parse(SAMPLE)["AAPL"]
    assert entry["short"] == 1000
    assert entry["total"] == 4000
    assert entry["date"] == "20260828"


def test_header_and_footer_rows_are_not_symbols():
    parsed = _parse(SAMPLE)
    assert set(parsed) == {"AAPL", "MU", "NVDA"}


@pytest.mark.parametrize(
    "row",
    [
        "20260828|BAD|1000|0|0|Q",       # zero total volume
        "20260828|BAD|5000|0|4000|Q",    # short exceeds total
        "20260828|BAD|-1|0|4000|Q",      # negative short volume
        "20260828|BAD|abc|0|4000|Q",     # non-numeric
        "20260828|BAD|1000|0|Q",         # too few fields
        "20260828||1000|0|4000|Q",       # no symbol
    ],
)
def test_impossible_rows_are_dropped_never_repaired(row):
    assert "BAD" not in _parse(HEADER + "\n" + row)


def test_lookup_is_case_insensitive():
    smap = _parse(SAMPLE)
    assert short_volume_pct("aapl", smap) == 25.0


def test_eu_tickers_are_not_on_the_us_tape():
    assert short_volume_pct("SAP.DE", _parse(SAMPLE)) is None


def test_uncovered_symbols_return_none_rather_than_zero():
    assert short_volume_pct("ZZZZ", _parse(SAMPLE)) is None


def test_dotted_us_share_classes_resolve():
    smap = _parse(HEADER + "\n20260828|BRK.B|1000|0|4000|Q")
    assert short_volume_pct("BRK-B", smap) == 25.0


def test_empty_file_parses_to_an_empty_map():
    assert _parse("") == {}
    assert _parse(HEADER) == {}
