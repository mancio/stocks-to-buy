"""Order-book demand summary arithmetic (no network, no API key)."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.demand import Demand, _fmt_money, _fmt_shares, _summarise, demand_signal


class _Level:
    def __init__(self, bid_px, ask_px, bid_sz, ask_sz):
        # Databento fixed-point prices carry 9 implied decimals.
        self.bid_px = bid_px * 1e9
        self.ask_px = ask_px * 1e9
        self.bid_sz = bid_sz
        self.ask_sz = ask_sz


class _Record:
    def __init__(self, levels, ts="2026-08-28T15:30:00"):
        self.levels = levels
        self.pretty_ts_event = datetime.fromisoformat(ts)


def _records(n, bid_sz, ask_sz, px=100.0):
    return [_Record([_Level(px - 0.01, px + 0.01, bid_sz, ask_sz)]) for _ in range(n)]


def test_balanced_book_scores_one_half():
    result = _summarise(_records(50, 500, 500))
    assert result.imbalance == pytest.approx(0.5)
    assert result.snapshots == 50


def test_imbalance_is_the_bid_share_of_resting_size():
    result = _summarise(_records(30, 750, 250))
    assert result.imbalance == pytest.approx(0.75)
    assert result.avg_bid_size == pytest.approx(750)
    assert result.avg_ask_size == pytest.approx(250)


def test_bid_notional_uses_the_mid_price():
    result = _summarise(_records(30, 400, 400, px=50.0))
    assert result.avg_bid_notional == pytest.approx(400 * 50.0)


def test_depth_is_summed_across_every_level():
    levels = [_Level(99.99, 100.01, 100, 50), _Level(99.98, 100.02, 200, 150)]
    result = _summarise([_Record(levels) for _ in range(25)])
    assert result.avg_bid_size == pytest.approx(300)
    assert result.avg_ask_size == pytest.approx(200)
    assert result.imbalance == pytest.approx(0.6)


def test_one_sided_and_zero_priced_books_are_discarded():
    assert _summarise([_Record([_Level(100, 100.02, 0, 500)]) for _ in range(50)]) is None
    assert _summarise([_Record([_Level(0, 0, 500, 500)]) for _ in range(50)]) is None


def test_thin_sessions_are_rejected_rather_than_scored():
    assert _summarise(_records(5, 500, 500)) is None


def test_records_without_levels_are_skipped():
    assert _summarise([_Record([]) for _ in range(50)]) is None


def test_eu_tickers_get_no_demand_signal():
    # An exchange suffix means the symbol is not in a US equities dataset.
    assert demand_signal("SAP.DE") == (None, None)


def test_session_date_comes_from_the_records():
    result = _summarise(_records(30, 500, 500))
    assert result.session == "2026-08-28"


@pytest.mark.parametrize(
    "value, expected",
    [(950, "950"), (1500, "1.5k"), (2_400_000, "2.4M")],
)
def test_share_formatting(value, expected):
    assert _fmt_shares(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(950, "950"), (12_400, "12k"), (3_200_000, "3.2M")],
)
def test_money_formatting(value, expected):
    assert _fmt_money(value) == expected


def test_demand_dataclass_round_trips_through_the_cache_shape():
    d = Demand("AAPL", "2026-08-28", 390, 0.61, 1200.0, 780.0, 264000.0)
    assert Demand(**d.__dict__) == d
