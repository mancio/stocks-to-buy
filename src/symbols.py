"""Resolve Wikipedia constituent tickers to the symbols Yahoo actually uses.

Wikipedia lists exchange-local tickers ("VOLV B", "NDA FI"); Yahoo wants
"VOLV-B.ST" / "NDA-FI.HE". `yahoo_variants` covers that mechanically. When even
those fail, `resolve_symbol` falls back to Yahoo's own free symbol search
(`/v1/finance/search`) keyed on the **company name** — a second naming source
for the same constituent, no API key required.

A match is only accepted when it is an equity on the expected exchange *and*
either its base symbol matches Wikipedia's or the company name is a close match.
Nothing is guessed: an unresolved constituent stays dropped.

Search responses are cached to `.cache/symbols/{query}.json` for 30 days.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests

from . import config

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_HEADERS = {"User-Agent": "Mozilla/5.0 (stocks-near-support/1.0)"}
_THROTTLE_SECONDS = 0.3

# Below this name similarity a search hit is only trusted on an exact base match.
NAME_MATCH_THRESHOLD = 0.72

_SPLIT_RE = re.compile(r"[\s._]+")
_NOISE_RE = re.compile(
    r"\b(ab|ag|as|asa|nv|n\.v|sa|s\.a|se|plc|spa|s\.p\.a|oyj|a/s|inc|corp|"
    r"corporation|company|co|group|holding|holdings|ser|class|the)\b"
)


def yahoo_variants(raw_ticker: str, suffix: str) -> list[str]:
    """Yahoo symbol candidates for a Wikipedia ticker, best guess first.

    "VOLV B" + ".ST" -> ["VOLV-B.ST", "VOLV.ST"]
    "SAP"    + ".DE" -> ["SAP.DE"]
    """
    cleaned = raw_ticker.split(":")[0].strip().upper()
    parts = [p for p in _SPLIT_RE.split(cleaned) if p]
    if not parts:
        return []
    out = []
    if len(parts) > 1:
        out.append(f"{'-'.join(parts)}{suffix}")
    out.append(f"{parts[0]}{suffix}")
    return list(dict.fromkeys(out))


def base_symbol(ticker: str) -> str:
    """Strip the Yahoo exchange suffix and share class: 'VOLV-B.ST' -> 'VOLV'."""
    return ticker.split(".")[0].split("-")[0].upper()


def _normalise_name(name: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", strip_accents(name).lower())
    return " ".join(_NOISE_RE.sub(" ", text).split())


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_accents(name).lower())


def name_similarity(a: str, b: str) -> float:
    na, nb = _normalise_name(a), _normalise_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def names_agree(a: str, b: str) -> bool:
    """True when two spellings clearly denote the same issuer.

    Substring containment catches the abbreviations Yahoo expands
    ('H&M' inside 'Hennes & Mauritz AB, H & M ser.').
    """
    sa, sb = _squash(a), _squash(b)
    if not sa or not sb:
        return False
    return sa in sb or sb in sa or name_similarity(a, b) >= NAME_MATCH_THRESHOLD


def _cache_path(query: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", query).strip("_")[:80] or "query"
    return config.SYMBOLS_CACHE_DIR / f"{safe}.json"


def _search(query: str) -> list[dict]:
    """Yahoo symbol search for a company name, cached for 30 days."""
    path = _cache_path(query)
    if path.exists() and time.time() - path.stat().st_mtime <= config.SYMBOL_CACHE_TTL_SECONDS:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    params = {"q": query, "quotesCount": 10, "newsCount": 0}
    quotes: list[dict] = []
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = requests.get(
                SEARCH_URL, params=params, headers=_HEADERS, timeout=config.HTTP_TIMEOUT
            )
            resp.raise_for_status()
            quotes = resp.json().get("quotes", []) or []
            break
        except Exception as exc:  # noqa: BLE001 - network/JSON errors
            if attempt < config.HTTP_RETRIES:
                time.sleep(2 ** attempt)
            else:
                print(f"[symbols] search failed for {query!r} ({type(exc).__name__})")
                return []

    time.sleep(_THROTTLE_SECONDS)
    if not quotes:
        # An empty answer is usually throttling, not "no such company" - do not
        # freeze it into the cache for 30 days.
        return []
    config.SYMBOLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(quotes), encoding="utf-8")
    return quotes


def _matches_exchange(symbol: str, suffix: str) -> bool:
    """EU rows need the country suffix; US rows must carry no suffix at all."""
    return symbol.upper().endswith(suffix.upper()) if suffix else "." not in symbol


def same_base(symbol: str, base: str) -> bool:
    """True when Wikipedia's base names the same line as the Yahoo symbol.

    Wikipedia writes the share class either as a separate word ('VOLV B' ->
    base 'VOLV') or glued on ('ERICB' -> Yahoo 'ERIC-B'), so both readings of
    the Yahoo symbol count.
    """
    core = symbol.split(".")[0].upper()
    return base.upper() in {core.split("-")[0], core.replace("-", "")}


def pick_match(quotes: list[dict], name: str, base: str, suffix: str) -> str | None:
    """Best equity on the expected exchange, or None when nothing is convincing.

    Accepted only when the base symbol matches *and* the names agree, or when
    the name alone is a close enough match. A base-symbol collision such as
    'ACA' -> 'AC-A' is therefore not enough on its own.
    """
    scored = []
    for q in quotes:
        symbol = str(q.get("symbol") or "")
        if not symbol or q.get("quoteType") != "EQUITY":
            continue
        if not _matches_exchange(symbol, suffix):
            continue
        listed = [str(q.get(key) or "") for key in ("shortname", "longname")]
        similarity = max(name_similarity(name, other) for other in listed)
        exact = same_base(symbol, base)
        if not (
            (exact and any(names_agree(name, other) for other in listed))
            or similarity >= NAME_MATCH_THRESHOLD
        ):
            continue
        # Shortest symbol wins ties: the primary line, not a secondary listing.
        scored.append((exact, similarity, -len(symbol), symbol))

    return max(scored)[3] if scored else None


def strip_accents(text: str) -> str:
    """Yahoo search returns 0 hits for accented spellings ('Credit Agricole')."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def class_split(base: str) -> str | None:
    """'ERICB' -> 'ERIC-B'. Wikipedia sometimes glues the share class on."""
    core = base.upper()
    if len(core) >= 3 and core[-1] in "ABC":
        return f"{core[:-1]}-{core[-1]}"
    return None


def verify_symbol(symbol: str, name: str) -> bool:
    """Confirm a guessed symbol exists on Yahoo and belongs to the same issuer."""
    for quote in _search(symbol):
        if str(quote.get("symbol") or "").upper() != symbol.upper():
            continue
        if quote.get("quoteType") != "EQUITY":
            return False
        listed = str(quote.get("shortname") or quote.get("longname") or "")
        return names_agree(name, listed)
    return False


def query_variants(name: str, base: str) -> list[str]:
    """Search queries to try, in order: name, accent-free name, bare symbol."""
    candidates = (name, strip_accents(name) if name else "", base)
    return list(dict.fromkeys(q.strip() for q in candidates if q and q.strip()))


def resolve_symbol(name: str, base: str, suffix: str) -> str | None:
    """Look the constituent up by company name, then by its bare base symbol.

    Last resort: split a glued share class ('HMB' -> 'HM-B') and accept it only
    if Yahoo confirms that exact symbol under a matching company name.
    """
    for query in query_variants(name, base):
        match = pick_match(_search(query), name, base, suffix)
        if match:
            return match

    split = class_split(base)
    if split:
        guess = f"{split}{suffix}"
        if verify_symbol(guess, name):
            return guess
    return None


if __name__ == "__main__":
    print(yahoo_variants("VOLV B", ".ST"))
    for company, base, suffix in [
        ("Volvo", "VOLV", ".ST"),
        ("BNP Paribas", "BNPP", ".PA"),
        ("Ericsson", "ERICB", ".ST"),
    ]:
        print(f"{company}: {resolve_symbol(company, base, suffix)}")
