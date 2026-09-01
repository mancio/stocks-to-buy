"""End-to-end checks that the numbers in the generated report.html are consistent.

These re-derive every displayed figure from the other figures in the same row
(and from the embedded detail payload) so a formatting, rounding or wiring bug
in the template or the report layer cannot pass unnoticed.
"""
from __future__ import annotations

import pytest

from src.ratings import BUCKET_ORDER
from src.fundamentals import TOTAL_TOLERANCE
from src.signals import WEIGHTS, _analyst_score, _drawdown_score, _support_score
from src import config

# Column indexes in the rendered table.
C_SCORE, C_TICKER, C_NAME, C_MARKET = 0, 1, 2, 3
C_LAST, C_SUPPORT, C_DIST, C_HIGH = 4, 5, 6, 7
C_DRAWDOWN, C_DAYS, C_RATING, C_ANALYSTS = 8, 9, 10, 11
C_COMMUNITY, C_DEMAND, C_SHORT = 12, 13, 14


def test_distance_percent_matches_last_and_support(details):
    for ticker, d in details.items():
        expected = (d["last_close"] - d["support"]) / d["last_close"] * 100.0
        assert d["distance_pct"] == pytest.approx(expected, abs=1e-6), ticker


def test_drawdown_percent_matches_52w_high_and_last(details):
    for ticker, d in details.items():
        expected = (d["high_52w"] - d["last_close"]) / d["high_52w"] * 100.0
        assert d["drawdown_pct"] == pytest.approx(expected, abs=1e-6), ticker


def test_support_is_below_last_close_and_within_threshold(details):
    for ticker, d in details.items():
        assert 0 < d["support"] < d["last_close"], ticker
        assert 0.0 <= d["distance_pct"] <= config.NEAR_THRESHOLD_PCT, ticker


def test_last_close_never_exceeds_the_52w_high(details):
    for ticker, d in details.items():
        assert d["last_close"] <= d["high_52w"] * 1.000001, ticker
        assert 0.0 <= d["drawdown_pct"] < 100.0, ticker


def test_days_since_high_is_within_the_lookback_window(details):
    for ticker, d in details.items():
        assert 0 <= d["days_since_high"] < config.LOOKBACK_DAYS, ticker


def test_analyst_total_equals_the_sum_of_the_buckets(details):
    checked = 0
    for ticker, d in details.items():
        if not d["buckets"]:
            continue
        assert sum(b["count"] for b in d["buckets"]) == d["num_analysts"], ticker
        assert all(b["count"] >= 0 for b in d["buckets"]), ticker
        checked += 1
    assert checked > 0, "no row carried an analyst breakdown"


def test_consensus_label_is_the_winning_bucket(details):
    priority = {b: i for i, b in enumerate(BUCKET_ORDER)}
    for ticker, d in details.items():
        if not d["buckets"]:
            continue
        winner = max(d["buckets"], key=lambda b: (b["count"], -priority[b["key"]]))
        assert d["rating"] == winner["label"], ticker


def test_every_row_survived_the_buy_filter(details):
    for ticker, d in details.items():
        assert d["rating"] in {"Buy", "Strong Buy"}, ticker
        assert d["num_analysts"] > 0, ticker


def test_score_is_reproducible_from_its_components(details):
    """Re-derive the score; the only unknown (momentum) must land inside 0..1."""
    for ticker, d in details.items():
        known = (
            _support_score(d["distance_pct"], config.NEAR_THRESHOLD_PCT) * WEIGHTS["support"]
            + _analyst_score(d["rating"], d["num_analysts"]) * WEIGHTS["analyst"]
            + _drawdown_score(d["drawdown_pct"]) * WEIGHTS["drawdown"]
            + d["community"] / 100.0 * WEIGHTS["community"]
            + d["demand"] / 100.0 * WEIGHTS["demand"]
        )
        implied_momentum = (d["score"] / 100.0 - known) / WEIGHTS["momentum"]
        assert -0.01 <= implied_momentum <= 1.01, f"{ticker}: {implied_momentum}"


def test_score_and_community_are_in_range(details):
    for ticker, d in details.items():
        assert 0.0 <= d["score"] <= 100.0, ticker
        assert 0.0 <= d["community"] <= 100.0, ticker
        assert 0.0 <= d["demand"] <= 100.0, ticker


def test_short_volume_is_a_percentage_of_traded_volume(details):
    """US-only and never fabricated: EU rows must carry no reading at all."""
    for ticker, d in details.items():
        if d["short_pct"] is None:
            continue
        assert d["market"] == "US", ticker
        assert 0.0 <= d["short_pct"] <= 100.0, ticker
    assert all(
        d["short_pct"] is None for d in details.values() if d["market"] == "EU"
    )


def test_rows_are_ranked_by_score_then_price(table_rows, details):
    keys = [
        (-details[t]["score"], details[t]["last_close"]) for t, _ in table_rows
    ]
    assert keys == sorted(keys)


def test_rendered_cells_match_the_detail_payload(table_rows, details):
    assert len(table_rows) == len(details)
    for ticker, cells in table_rows:
        d = details[ticker]
        assert cells[C_TICKER].startswith(ticker)
        assert cells[C_NAME] == d["name"]
        assert cells[C_MARKET] == d["market"]
        assert cells[C_SCORE] == f"{d['score']:.1f}"
        assert cells[C_LAST] == f"{d['last_close']:.2f}"
        assert cells[C_SUPPORT] == f"{d['support']:.2f}"
        assert cells[C_DIST] == f"{d['distance_pct']:.2f}"
        assert cells[C_HIGH] == f"{d['high_52w']:.2f}"
        assert cells[C_DRAWDOWN] == f"{-d['drawdown_pct']:.2f}%"
        assert cells[C_DAYS] == str(d["days_since_high"])
        assert cells[C_RATING] == d["rating"]
        assert cells[C_ANALYSTS] == str(d["num_analysts"])
        assert cells[C_COMMUNITY] == f"{d['community']:.1f}"
        assert cells[C_DEMAND] == f"{d['demand']:.1f}"
        expected_short = (
            "n/a" if d["short_pct"] is None else f"{d['short_pct']:.1f}"
        )
        assert cells[C_SHORT] == expected_short


def test_market_split_matches_the_header_counts(report_html, details):
    us = sum(1 for d in details.values() if d["market"] == "US")
    eu = sum(1 for d in details.values() if d["market"] == "EU")
    assert f"{len(details)} rows" in report_html
    assert f"{us} US" in report_html
    assert f"{eu} EU" in report_html
    assert us + eu == len(details)


def test_detail_payload_balance_sheets_are_real_and_consistent(details):
    checked = 0
    for ticker, d in details.items():
        f = d["fundamentals"]
        if not f:
            continue
        assert f["annual"] or f["quarterly"], ticker
        for period in f["annual"] + f["quarterly"]:
            v = period["values"]
            assert v, f"{ticker} {period['date']} has an empty period"
            if {"current_assets", "non_current_assets", "total_assets"} <= v.keys():
                assert v["current_assets"] + v["non_current_assets"] == pytest.approx(
                    v["total_assets"], rel=TOTAL_TOLERANCE
                ), f"{ticker} {period['date']}"
                checked += 1
    assert checked > 0, "no row carried a usable balance sheet"
