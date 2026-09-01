"""Render the final standalone report.html from the Jinja2 template."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from .fundamentals import LINE_ITEM_LABELS
from .ratings import BUCKET_LABEL, BUCKET_ORDER

_RATING_CLASS = {
    "Strong Buy": "r-strongbuy",
    "Buy": "r-buy",
    "Hold": "r-hold",
    "Sell": "r-sell",
    "Strong Sell": "r-strongsell",
}

TEMPLATE_DIR = config.ROOT / "templates"
OUTPUT_PATH = config.ROOT / "report.html"


def _detail_payload(row: dict) -> dict:
    """Per-ticker data for the detail panel. Only keys the source actually gave."""
    counts = row.get("rating_counts") or {}
    buckets = [
        {"key": b, "label": BUCKET_LABEL[b], "count": int(counts.get(b, 0) or 0)}
        for b in BUCKET_ORDER
        if b in counts
    ]
    fundamentals = row.get("fundamentals")
    return {
        "ticker": row["ticker"],
        "name": row["name"],
        "market": row["market"],
        "score": row.get("score"),
        "last_close": row["last_close"],
        "support": row["support"],
        "distance_pct": row["distance_pct"],
        "high_52w": row["high_52w"],
        "drawdown_pct": row["drawdown_pct"],
        "days_since_high": row["days_since_high"],
        "rating": row["rating"],
        "rating_class": row["rating_class"],
        "num_analysts": row["num_analysts"],
        "community": row.get("community"),
        "demand": row.get("demand"),
        "short_pct": row.get("short_pct"),
        "reasons": row.get("reasons", ""),
        "buckets": buckets,
        "fundamentals": fundamentals,
    }


def render_report(rows: list[dict], out_path: Path = OUTPUT_PATH) -> Path:
    """Render `rows` to a self-contained report.html and return its path.

    Each row dict must contain: ticker, name, market, last_close, support,
    distance_pct, high_52w, drawdown_pct, days_since_high, rating, num_analysts.
    Optional: rating_counts (analyst buckets) and fundamentals (balance sheet);
    when absent the detail panel says so rather than showing placeholder values.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    template = env.get_template("report.html.j2")

    enriched = []
    for r in rows:
        r = dict(r)
        r["rating_class"] = _RATING_CLASS.get(r["rating"], "r-hold")
        enriched.append(r)

    us_count = sum(1 for r in enriched if r["market"] == "US")
    eu_count = sum(1 for r in enriched if r["market"] == "EU")

    html = template.render(
        rows=enriched,
        details={r["ticker"]: _detail_payload(r) for r in enriched},
        line_items=LINE_ITEM_LABELS,
        row_count=len(enriched),
        us_count=us_count,
        eu_count=eu_count,
        threshold=config.NEAR_THRESHOLD_PCT,
        generated_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    demo = [
        {
            "ticker": "DEMO",
            "name": "Demo Corp",
            "market": "US",
            "last_close": 100.0,
            "support": 97.5,
            "distance_pct": 2.5,
            "high_52w": 140.0,
            "drawdown_pct": 28.57,
            "days_since_high": 42,
            "rating": "Buy",
            "num_analysts": 25,
        }
    ]
    path = render_report(demo)
    print(f"Wrote {path}")
