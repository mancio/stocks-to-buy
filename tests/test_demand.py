"""Demand signal: money-flow estimate and order-book summary arithmetic.

No network and no API key -- the order-book half runs against synthetic
Databento-shaped records, the money-flow half against hand-built OHLCV frames.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src import config
from src.demand import (
    CMF_WINDOW,
    Demand,
    _fmt_money,
    _fmt_shares,
    _summarise,
    chaikin_money_flow,
    demand_signal,
)


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


def test_eu_tickers_get_no_demand_signal(monkeypatch):
    # An exchange suffix means the symbol is not in a US equities dataset.
    monkeypatch.setattr(config, "DEMAND_PROVIDER", "databento")
    assert demand_signal("SAP.DE") == (None, None)


def test_unknown_provider_disables_the_signal(monkeypatch):
    monkeypatch.setattr(config, "DEMAND_PROVIDER", "none")
    assert demand_signal("AAPL", _bars(close="high")) == (None, None)


def test_session_date_comes_from_the_records():
    result = _summarise(_records(30, 500, 500))
    assert result.session == "2026-08-28"


@pytest.mark.parametrize(
    "value, expected",
    [(950, "950"), (1500, "1.5k"), (2_400_000, "2.4M"), (2_509_800_000, "2.5B")],
)
def test_share_formatting(value, expected):
    assert _fmt_shares(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(950, "950"), (12_400, "12k"), (3_200_000, "3.2M"), (1_100_000_000, "1.1B")],
)
def test_money_formatting(value, expected):
    assert _fmt_money(value) == expected


def test_demand_dataclass_round_trips_through_the_cache_shape():
    d = Demand("AAPL", "2026-08-28", 390, 0.61, 1200.0, 780.0, 264000.0)
    assert Demand(**d.__dict__) == d


# --- Chaikin Money Flow (the free `derived` provider) ------------------------


def _bars(close: str, n: int = CMF_WINDOW, volume: float = 1000.0) -> pd.DataFrame:
    """OHLCV frame whose closes sit at the high, the low, or the midpoint."""
    price = {"high": 11.0, "low": 9.0, "mid": 10.0}[close]
    return pd.DataFrame(
        {
            "High": [11.0] * n,
            "Low": [9.0] * n,
            "Close": [price] * n,
            "Volume": [volume] * n,
        }
    )


def test_closing_at_the_high_is_full_accumulation():
    assert chaikin_money_flow(_bars("high")) == pytest.approx(1.0)


def test_closing_at_the_low_is_full_distribution():
    assert chaikin_money_flow(_bars("low")) == pytest.approx(-1.0)


def test_closing_mid_bar_is_neutral():
    assert chaikin_money_flow(_bars("mid")) == pytest.approx(0.0)


def test_money_flow_is_volume_weighted():
    # One heavy accumulation bar outweighs nineteen light distribution bars.
    frame = pd.DataFrame(
        {
            "High": [11.0] * 20,
            "Low": [9.0] * 20,
            "Close": [9.0] * 19 + [11.0],
            "Volume": [100.0] * 19 + [1_900.0],
        }
    )
    assert chaikin_money_flow(frame) == pytest.approx(0.0)


def test_flat_and_zero_volume_bars_are_skipped_not_counted_as_neutral():
    frame = _bars("high")
    frame.loc[0:4, ["High", "Low"]] = 10.0  # no intraday range
    assert chaikin_money_flow(frame) == pytest.approx(1.0)


def test_too_few_usable_bars_returns_no_reading():
    frame = _bars("high")
    frame.loc[0:14, "Volume"] = 0.0  # only 5 of 20 bars usable
    assert chaikin_money_flow(frame) is None
    assert chaikin_money_flow(pd.DataFrame()) is None
    assert chaikin_money_flow(None) is None


def test_money_flow_ignores_bars_older_than_the_window():
    old = pd.DataFrame(
        {
            "High": [11.0] * 50,
            "Low": [9.0] * 50,
            "Close": [9.0] * 30 + [11.0] * 20,
            "Volume": [1000.0] * 50,
        }
    )
    assert chaikin_money_flow(old) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "close, expected_sub",
    [("high", 1.0), ("mid", 0.5), ("low", 0.0)],
)
def test_derived_subscore_maps_money_flow_onto_zero_to_one(
    monkeypatch, close, expected_sub
):
    monkeypatch.setattr(config, "DEMAND_PROVIDER", "derived")
    sub, reason = demand_signal("AAPL", _bars(close))
    assert sub == pytest.approx(expected_sub)
    assert f"{expected_sub * 100:.0f}% buy-side" in reason


def test_derived_reason_reports_net_shares_and_value(monkeypatch):
    monkeypatch.setattr(config, "DEMAND_PROVIDER", "derived")
    _, reason = demand_signal("AAPL", _bars("high", volume=2000.0))
    # CMF = 1.0, so the whole average volume is attributed to buyers.
    assert "+2.0k sh" in reason
    assert "+22k" in reason  # 2000 shares x 11.00 close


def test_derived_provider_needs_no_symbol_coverage(monkeypatch):
    """Unlike the order book, money flow works for EU tickers too."""
    monkeypatch.setattr(config, "DEMAND_PROVIDER", "derived")
    sub, reason = demand_signal("SAP.DE", _bars("high"))
    assert sub == pytest.approx(1.0)
    assert reason


def test_derived_provider_without_history_yields_nothing(monkeypatch):
    monkeypatch.setattr(config, "DEMAND_PROVIDER", "derived")
    assert demand_signal("AAPL", None) == (None, None)
