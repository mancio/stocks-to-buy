"""Shared fixtures.

The report-level tests read the *generated* `report.html`; they are skipped
when it has not been produced yet so a clean checkout still passes.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "report.html"

_DETAILS_RE = re.compile(
    r'<script type="application/json" id="details-data">(.*?)</script>', re.S
)
_ROW_RE = re.compile(r'<tr data-ticker="([^"]+)">(.*?)</tr>', re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# Payload keys the report-level tests re-derive; a report generated before a
# signal was added is stale rather than wrong, so those tests skip.
_EXPECTED_KEYS = {"score", "community", "demand", "short_pct"}


def _text(markup: str) -> str:
    return html.unescape(_TAG_RE.sub("", markup)).strip()


@pytest.fixture(scope="session")
def report_html() -> str:
    if not REPORT.exists():
        pytest.skip("report.html not generated yet (run `python -m src.main`)")
    return REPORT.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def details(report_html: str) -> dict:
    match = _DETAILS_RE.search(report_html)
    assert match, "report.html is missing the embedded details JSON"
    data = json.loads(match.group(1))
    assert data, "details payload is empty"
    missing = _EXPECTED_KEYS - set(next(iter(data.values())))
    if missing:
        pytest.skip(
            f"report.html predates {sorted(missing)}; re-run `python -m src.main`"
        )
    return data


@pytest.fixture(scope="session")
def table_rows(report_html: str) -> list[tuple[str, list[str]]]:
    """[(ticker, [cell text, ...]), ...] in rendered order."""
    rows = [
        (ticker, [_text(c) for c in _CELL_RE.findall(body)])
        for ticker, body in _ROW_RE.findall(report_html)
    ]
    assert rows, "report.html has no data rows"
    return rows
