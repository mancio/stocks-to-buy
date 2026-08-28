"""Universe parsing + the pipeline's missing-ticker recovery step."""
from __future__ import annotations

import pandas as pd
import pytest

from src import main, universe as universe_mod


# --------------------------------------------------------------------------- #
# Wikipedia -> Yahoo ticker construction
# --------------------------------------------------------------------------- #
STOXX_TABLE = pd.DataFrame(
    {
        "Ticker": ["SAP", "VOLV B", "NDA FI", "ABI", "XYZ"],
        "Company": ["SAP SE", "Volvo", "Nordea Bank", "AB InBev", "Somewhere Ltd"],
        "Country": ["Germany", "Sweden", "Finland", "Belgium", "Atlantis"],
    }
)


def test_share_classes_are_kept_not_truncated(monkeypatch):
    monkeypatch.setattr(universe_mod, "_read_tables", lambda url: [STOXX_TABLE])

    out = universe_mod.get_stoxx600()

    assert list(out["ticker"]) == ["SAP.DE", "VOLV-B.ST", "NDA-FI.HE", "ABI.BR"]
    assert list(out["market"]) == ["EU"] * 4
    assert list(out["exchange"]) == ["DE", "ST", "HE", "BR"]


def test_rows_in_unmapped_countries_are_dropped(monkeypatch):
    monkeypatch.setattr(universe_mod, "_read_tables", lambda url: [STOXX_TABLE])

    out = universe_mod.get_stoxx600()

    assert "Somewhere Ltd" not in set(out["name"])


def test_us_class_shares_use_the_yahoo_dash_form(monkeypatch):
    table = pd.DataFrame({"Symbol": ["AAPL", "BRK.B"], "Security": ["Apple", "Berkshire"]})
    monkeypatch.setattr(universe_mod, "_read_tables", lambda url: [table])

    out = universe_mod.get_sp500()

    assert list(out["ticker"]) == ["AAPL", "BRK-B"]


def test_unparseable_table_raises_with_the_url(monkeypatch):
    monkeypatch.setattr(universe_mod, "_read_tables", lambda url: [pd.DataFrame({"A": [1]})])

    with pytest.raises(RuntimeError, match="STOXX Europe 600"):
        universe_mod.get_stoxx600()


# --------------------------------------------------------------------------- #
# Recovery of tickers Yahoo did not recognise
# --------------------------------------------------------------------------- #
def make_universe(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "name", "exchange", "market"])


def bars() -> pd.DataFrame:
    return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0]})


def test_nothing_to_do_when_every_ticker_has_history(monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("no lookup should happen")

    monkeypatch.setattr(main, "resolve_symbol", boom)
    monkeypatch.setattr(main, "download_history", boom)

    uni = make_universe([("SAP.DE", "SAP SE", "DE", "EU")])
    out_uni, out_hist, renames = main.resolve_missing(uni, {"SAP.DE": bars()})

    assert renames == {}
    assert list(out_uni["ticker"]) == ["SAP.DE"]
    assert set(out_hist) == {"SAP.DE"}


def test_plain_base_symbol_is_tried_before_searching(monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("search should not be needed")

    monkeypatch.setattr(main, "resolve_symbol", lambda *a: None)
    monkeypatch.setattr(main, "download_history", lambda tickers: {"VOLV.ST": bars()})

    uni = make_universe([("VOLV-B.ST", "Volvo", "ST", "EU")])
    out_uni, out_hist, renames = main.resolve_missing(uni, {})

    assert renames == {"VOLV-B.ST": "VOLV.ST"}
    assert list(out_uni["ticker"]) == ["VOLV.ST"]
    assert "VOLV.ST" in out_hist


def test_name_search_recovers_a_differently_named_symbol(monkeypatch):
    monkeypatch.setattr(main, "resolve_symbol", lambda name, base, suffix: "BNP.PA")
    monkeypatch.setattr(main, "download_history", lambda tickers: {"BNP.PA": bars()})

    uni = make_universe([("BNPP.PA", "BNP Paribas", "PA", "EU")])
    out_uni, out_hist, renames = main.resolve_missing(uni, {})

    assert renames == {"BNPP.PA": "BNP.PA"}
    assert list(out_uni["ticker"]) == ["BNP.PA"]
    assert "BNP.PA" in out_hist


def test_search_is_asked_for_the_expected_exchange_suffix(monkeypatch):
    seen = []

    def spy(name, base, suffix):
        seen.append((name, base, suffix))
        return None

    monkeypatch.setattr(main, "resolve_symbol", spy)
    monkeypatch.setattr(main, "download_history", lambda tickers: {})

    uni = make_universe(
        [("VOLV-B.ST", "Volvo", "ST", "EU"), ("FOO", "Foo Inc", "US", "US")]
    )
    main.resolve_missing(uni, {})

    assert ("Volvo", "VOLV", ".ST") in seen
    assert ("Foo Inc", "FOO", "") in seen  # US rows must stay suffix-free


def test_unresolvable_tickers_are_left_alone(monkeypatch):
    monkeypatch.setattr(main, "resolve_symbol", lambda *a: None)
    monkeypatch.setattr(main, "download_history", lambda tickers: {})

    uni = make_universe([("GHOST.DE", "Ghost AG", "DE", "EU")])
    out_uni, out_hist, renames = main.resolve_missing(uni, {})

    assert renames == {}
    assert list(out_uni["ticker"]) == ["GHOST.DE"]
    assert out_hist == {}


def test_a_rename_never_collides_with_an_existing_ticker(monkeypatch):
    monkeypatch.setattr(main, "resolve_symbol", lambda *a: None)
    monkeypatch.setattr(main, "download_history", lambda tickers: {"SAP.DE": bars()})

    uni = make_universe(
        [("SAP.DE", "SAP SE", "DE", "EU"), ("SAP-X.DE", "SAP SE", "DE", "EU")]
    )
    existing = bars()
    out_uni, out_hist, renames = main.resolve_missing(uni, {"SAP.DE": existing})

    assert renames == {}
    assert out_hist["SAP.DE"] is existing
    assert list(out_uni["ticker"]) == ["SAP.DE", "SAP-X.DE"]
