"""Wikipedia ticker -> Yahoo symbol resolution.

Network is never touched here: `_search` is monkeypatched with recorded Yahoo
payloads so the matching rules are tested deterministically.
"""
from __future__ import annotations

import pytest

from src import symbols
from src.symbols import (
    NAME_MATCH_THRESHOLD,
    base_symbol,
    name_similarity,
    pick_match,
    query_variants,
    resolve_symbol,
    same_base,
    strip_accents,
    yahoo_variants,
)


# --------------------------------------------------------------------------- #
# Mechanical variants
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, suffix, expected",
    [
        ("SAP", ".DE", ["SAP.DE"]),
        ("VOLV B", ".ST", ["VOLV-B.ST", "VOLV.ST"]),        # share class
        ("NDA FI", ".HE", ["NDA-FI.HE", "NDA.HE"]),         # Nordea Helsinki line
        ("MAERSK B", ".CO", ["MAERSK-B.CO", "MAERSK.CO"]),
        ("EPI A", ".ST", ["EPI-A.ST", "EPI.ST"]),
        ("SAP:GR", ".DE", ["SAP.DE"]),                      # Bloomberg suffix stripped
        ("BT.A", ".L", ["BT-A.L", "BT.L"]),                  # dotted share class
        ("  ", ".DE", []),
    ],
)
def test_yahoo_variants(raw, suffix, expected):
    assert yahoo_variants(raw, suffix) == expected


def test_variants_are_deduplicated():
    assert yahoo_variants("SAP SAP", ".DE") == ["SAP-SAP.DE", "SAP.DE"]


@pytest.mark.parametrize(
    "ticker, expected",
    [
        ("VOLV-B.ST", "VOLV"),
        ("SAP.DE", "SAP"),
        ("BRK-B", "BRK"),
        ("aapl", "AAPL"),
    ],
)
def test_base_symbol(ticker, expected):
    assert base_symbol(ticker) == expected


@pytest.mark.parametrize(
    "symbol, base, expected",
    [
        ("VOLV-B.ST", "VOLV", True),    # Wikipedia split the class off: "VOLV B"
        ("ERIC-B.ST", "ERICB", True),   # Wikipedia glued it on: "ERICB"
        ("ATCO-A.ST", "ATCOA", True),
        ("SAP.DE", "SAP", True),
        ("VOLCAR-B.ST", "VOLV", False),
        ("AKRBPO.OL", "AKERBP", False),
    ],
)
def test_same_base_accepts_both_share_class_spellings(symbol, base, expected):
    assert same_base(symbol, base) is expected


# --------------------------------------------------------------------------- #
# Name matching
# --------------------------------------------------------------------------- #
def test_name_similarity_ignores_legal_suffixes():
    assert name_similarity("Volvo", "Volvo, AB ser. B") > NAME_MATCH_THRESHOLD
    assert name_similarity("GSK plc", "GSK") > NAME_MATCH_THRESHOLD
    assert name_similarity("Ericsson", "Nokia") < NAME_MATCH_THRESHOLD


def test_name_similarity_handles_empty_input():
    assert name_similarity("", "Volvo") == 0.0
    assert name_similarity("Volvo", "") == 0.0


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Cr\u00e9dit Agricole", "Credit Agricole"),
        ("Herm\u00e8s", "Hermes"),
        ("SAP", "SAP"),
    ],
)
def test_strip_accents(text, expected):
    assert strip_accents(text) == expected


@pytest.mark.parametrize(
    "name, base, expected",
    [
        (
            "Cr\u00e9dit Agricole",
            "CAGR",
            ["Cr\u00e9dit Agricole", "Credit Agricole", "CAGR"],
        ),
        ("Volvo", "VOLV", ["Volvo", "VOLV"]),  # no accent -> no duplicate query
        ("SAP", "SAP", ["SAP"]),
        ("", "HMB", ["HMB"]),
    ],
)
def test_query_variants(name, base, expected):
    assert query_variants(name, base) == expected


# --------------------------------------------------------------------------- #
# Picking a match out of a Yahoo search response
# --------------------------------------------------------------------------- #
VOLVO_QUOTES = [
    {"symbol": "VOLCAR-B.ST", "quoteType": "EQUITY", "shortname": "Volvo Car AB ser. B"},
    {"symbol": "VOLV-B.ST", "quoteType": "EQUITY", "shortname": "Volvo, AB ser. B"},
    {"symbol": "VLVOF", "quoteType": "EQUITY", "shortname": "VOLVO CAR AB"},
    {"symbol": "VOLV-A.ST", "quoteType": "EQUITY", "shortname": "Volvo, AB ser. A"},
]

BNP_QUOTES = [
    {"symbol": "BNP.PA", "quoteType": "EQUITY", "shortname": "BNP PARIBAS ACT.A"},
    {"symbol": "0P00007ZH9", "quoteType": "MUTUALFUND", "shortname": "BNP Paribas Funds"},
    {"symbol": "ANAV.DE", "quoteType": "ETF", "shortname": "BNP EASY II NASDAQ 100 ETF"},
]


def test_exact_base_symbol_beats_a_closer_name():
    # "Volvo Car" is the closer name, but Wikipedia's base symbol is VOLV.
    assert pick_match(VOLVO_QUOTES, "Volvo", "VOLV", ".ST") in {"VOLV-B.ST", "VOLV-A.ST"}


def test_name_match_is_used_when_the_base_symbol_differs():
    # Wikipedia says BNPP, Yahoo says BNP -> only the name can bridge it.
    assert pick_match(BNP_QUOTES, "BNP Paribas", "BNPP", ".PA") == "BNP.PA"


def test_non_equity_quotes_are_ignored():
    assert pick_match(BNP_QUOTES, "BNP Paribas", "BNPP", ".DE") is None


def test_wrong_exchange_is_rejected():
    assert pick_match(VOLVO_QUOTES, "Volvo", "VOLV", ".DE") is None


def test_us_rows_only_accept_suffix_free_symbols():
    quotes = [
        {"symbol": "ERIC-B.ST", "quoteType": "EQUITY", "shortname": "Ericsson ser. B"},
        {"symbol": "ERIC", "quoteType": "EQUITY", "shortname": "Ericsson"},
    ]
    assert pick_match(quotes, "Ericsson", "ERIC", "") == "ERIC"


def test_unrelated_company_is_not_matched():
    quotes = [
        {"symbol": "NOKIA.HE", "quoteType": "EQUITY", "shortname": "Nokia Oyj"},
    ]
    assert pick_match(quotes, "Ericsson", "ERICB", ".HE") is None


def test_empty_search_result_resolves_to_none():
    assert pick_match([], "Whatever", "WTV", ".DE") is None


def test_malformed_quotes_are_skipped():
    quotes = [
        {"quoteType": "EQUITY", "shortname": "No symbol"},
        {"symbol": "", "quoteType": "EQUITY"},
        {"symbol": "SAP.DE", "quoteType": "EQUITY", "shortname": None, "longname": "SAP SE"},
    ]
    assert pick_match(quotes, "SAP", "SAP", ".DE") == "SAP.DE"


def test_glued_share_class_is_recognised():
    quotes = [
        {"symbol": "ERIC-B.ST", "quoteType": "EQUITY", "shortname": "Ericsson, Telefonab. L M ser. B"},
    ]
    assert pick_match(quotes, "Ericsson", "ERICB", ".ST") == "ERIC-B.ST"


def test_shortest_symbol_wins_a_tie():
    """Aker BP's primary Oslo line is AKRBP.OL, not the longer secondary line."""
    quotes = [
        {"symbol": "AKRBPO.OL", "quoteType": "EQUITY", "shortname": "AKER BP"},
        {"symbol": "AKRBP.OL", "quoteType": "EQUITY", "shortname": "AKER BP"},
    ]
    assert pick_match(quotes, "Aker BP", "AKERBP", ".OL") == "AKRBP.OL"


# --------------------------------------------------------------------------- #
# resolve_symbol wiring (no network)
# --------------------------------------------------------------------------- #
def test_resolve_symbol_uses_the_search_result(monkeypatch):
    monkeypatch.setattr(symbols, "_search", lambda query: VOLVO_QUOTES)
    assert resolve_symbol("Volvo", "VOLV", ".ST").startswith("VOLV-")


def test_resolve_symbol_falls_back_to_the_base_symbol_query(monkeypatch):
    """'H&M' finds nothing by name; searching the bare 'HMB' still resolves it."""
    responses = {
        "H&M": [{"symbol": "HKDMOP=X", "quoteType": "CURRENCY"}],
        "HMB": [
            {"symbol": "HM-B.ST", "quoteType": "EQUITY", "shortname": "H & M Hennes & Mauritz AB"}
        ],
    }
    queried = []

    def fake_search(query):
        queried.append(query)
        return responses.get(query, [])

    monkeypatch.setattr(symbols, "_search", fake_search)

    assert resolve_symbol("H&M", "HMB", ".ST") == "HM-B.ST"
    assert queried == ["H&M", "HMB"]


def test_resolve_symbol_stops_at_the_first_hit(monkeypatch):
    queried = []

    def fake_search(query):
        queried.append(query)
        return VOLVO_QUOTES

    monkeypatch.setattr(symbols, "_search", fake_search)
    resolve_symbol("Volvo", "VOLV", ".ST")

    assert queried == ["Volvo"]


def test_without_a_company_name_nothing_can_be_verified(monkeypatch):
    monkeypatch.setattr(symbols, "_search", lambda query: VOLVO_QUOTES)
    assert resolve_symbol("", "VOLV", ".ST") is None


def test_resolve_symbol_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(symbols, "_search", lambda query: [])
    assert resolve_symbol("Ghost AG", "GHOST", ".DE") is None


# --------------------------------------------------------------------------- #
# Glued share class ("HMB" -> "HM-B"), accepted only after verification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "base, expected",
    [
        ("ERICB", "ERIC-B"),
        ("HMB", "HM-B"),
        ("ATCOA", "ATCO-A"),
        ("SAP", None),      # does not end in a class letter
        ("AB", None),       # too short to split
    ],
)
def test_class_split(base, expected):
    assert symbols.class_split(base) == expected


def test_names_agree_on_abbreviations():
    assert symbols.names_agree("H&M", "Hennes & Mauritz AB, H & M ser.")
    assert symbols.names_agree("Atlas Copco", "Atlas Copco AB ser. A")
    assert symbols.names_agree("Cr\u00e9dit Agricole", "CREDIT AGRICOLE")
    assert not symbols.names_agree("Ericsson", "Nokia Oyj")
    assert not symbols.names_agree("", "Nokia Oyj")


def test_verify_symbol_accepts_an_exact_hit_for_the_same_issuer(monkeypatch):
    monkeypatch.setattr(
        symbols,
        "_search",
        lambda q: [{"symbol": "HM-B.ST", "quoteType": "EQUITY", "shortname": "Hennes & Mauritz AB, H & M ser."}],
    )
    assert symbols.verify_symbol("HM-B.ST", "H&M") is True


def test_verify_symbol_rejects_a_different_issuer(monkeypatch):
    monkeypatch.setattr(
        symbols,
        "_search",
        lambda q: [{"symbol": "AC-A.PA", "quoteType": "EQUITY", "shortname": "Accor Something"}],
    )
    assert symbols.verify_symbol("AC-A.PA", "Crédit Agricole") is False


def test_verify_symbol_rejects_non_equities_and_misses(monkeypatch):
    monkeypatch.setattr(
        symbols,
        "_search",
        lambda q: [{"symbol": "XX-B.ST", "quoteType": "ETF", "shortname": "XX Fund"}],
    )
    assert symbols.verify_symbol("XX-B.ST", "XX") is False

    monkeypatch.setattr(symbols, "_search", lambda q: [])
    assert symbols.verify_symbol("XX-B.ST", "XX") is False


def test_resolve_symbol_falls_back_to_a_verified_class_split(monkeypatch):
    """Yahoo search never surfaces HM-B.ST by name; the split form is verified."""
    def fake_search(query):
        if query == "HM-B.ST":
            return [
                {
                    "symbol": "HM-B.ST",
                    "quoteType": "EQUITY",
                    "shortname": "Hennes & Mauritz AB, H & M ser.",
                }
            ]
        return [{"symbol": "HKDMOP=X", "quoteType": "CURRENCY"}]

    monkeypatch.setattr(symbols, "_search", fake_search)

    assert resolve_symbol("H&M", "HMB", ".ST") == "HM-B.ST"


def test_an_unverifiable_class_split_is_not_used(monkeypatch):
    monkeypatch.setattr(
        symbols,
        "_search",
        lambda q: [{"symbol": "AC-A.PA", "quoteType": "EQUITY", "shortname": "Accor"}],
    )
    assert resolve_symbol("Crédit Agricole", "ACA", ".PA") is None
