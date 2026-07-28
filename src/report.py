"""Render the final standalone report.html from the Jinja2 template."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config

_RATING_CLASS = {
    "Strong Buy": "r-strongbuy",
    "Buy": "r-buy",
    "Hold": "r-hold",
    "Sell": "r-sell",
    "Strong Sell": "r-strongsell",
}

TEMPLATE_DIR = config.ROOT / "templates"
OUTPUT_PATH = config.ROOT / "report.html"


def render_report(rows: list[dict], out_path: Path = OUTPUT_PATH) -> Path:
    """Render `rows` to a self-contained report.html and return its path.

    Each row dict must contain: ticker, name, market, last_close, support,
    distance_pct, high_52w, drawdown_pct, days_since_high, rating, num_analysts.
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
