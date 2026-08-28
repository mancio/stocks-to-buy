"""Analyst bucket -> consensus label / count logic."""
from __future__ import annotations

import pytest

from src.ratings import BUCKET_ORDER, _pick_from_buckets


def buckets(**kwargs) -> dict[str, int]:
    return {b: int(kwargs.get(b, 0)) for b in BUCKET_ORDER}


def test_winning_bucket_becomes_the_label():
    rating = _pick_from_buckets(buckets(strongBuy=2, buy=9, hold=3, sell=1))
    assert rating is not None
    assert rating.label == "Buy"


def test_total_is_the_sum_of_every_bucket():
    rating = _pick_from_buckets(buckets(strongBuy=2, buy=9, hold=3, sell=1, strongSell=4))
    assert rating is not None
    assert rating.num_analysts == 19
    assert sum(rating.counts.values()) == rating.num_analysts


def test_counts_are_preserved_verbatim():
    raw = buckets(strongBuy=11, buy=13, hold=9)
    rating = _pick_from_buckets(raw)
    assert rating is not None
    assert rating.counts == raw


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"strongBuy": 5, "buy": 5}, "Strong Buy"),
        ({"buy": 5, "hold": 5}, "Buy"),
        ({"hold": 5, "sell": 5}, "Hold"),
        ({"sell": 5, "strongSell": 5}, "Sell"),
    ],
)
def test_ties_break_towards_the_stronger_bucket(kwargs, expected):
    rating = _pick_from_buckets(buckets(**kwargs))
    assert rating is not None
    assert rating.label == expected


def test_no_analysts_yields_no_rating():
    assert _pick_from_buckets(buckets()) is None
