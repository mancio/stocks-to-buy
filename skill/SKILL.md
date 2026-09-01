---
name: stocks-near-support-report
description: Build from zero a Python app that generates a static HTML report listing S&P 500 (US) and STOXX Europe 600 (EU) stocks currently trading near a technical support level, sorted from cheapest to most expensive, annotated with the consensus analyst rating (Strong Buy / Buy / Hold / Sell / Strong Sell). Use when the user asks to "build the stocks-near-support app", "generate the support report", "rebuild the EU+US support screener", or similar. The skill prescribes the exact data sources, support algorithm, project layout, and HTML template to use; it must NOT invent placeholder data — every value comes from a live free API.
source: https://github.com/mancio/mySkills/blob/main/stock-market/SKILL.md
---

# Stocks Near Support — HTML Report Builder

## Goal

Produce a **single** `report.html` containing one sortable table of every
S&P 500 (US) **and** STOXX Europe 600 (EU) constituent that:

1. is currently within a configurable distance of a computed support level, **and**
2. has a consensus analyst rating of **Buy** or **Strong Buy** (other ratings are dropped).

Rows sorted ascending by last close (cheapest → most expensive).
Columns (exact order):

| Ticker | Name | Market | Last | Support | Dist % | 52w High | Drawdown | Days since high | Rating | # Analysts |

- **Market** = `US` or `EU`.
- **Drawdown** = `(52w high − last close) / 52w high × 100`, rendered as a negative percent.
- **Days since high** = trading-day distance from the bar of the 52w high to the last bar.
- **Rating** must be only `Buy` or `Strong Buy` — anything else excludes the row entirely.

No mock data. If a required secret is missing, **stop and ask the user** to provide it.

## Data sources (all free tier)

| Need | Source | Auth | Notes |
|---|---|---|---|
| S&P 500 constituents | Wikipedia `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` | none | parse with `pandas.read_html` |
| STOXX Europe 600 constituents | Wikipedia `https://en.wikipedia.org/wiki/STOXX_Europe_600` | none | parse with `pandas.read_html`; ticker column needs exchange suffix mapping (see below) |
| Yahoo symbol for a constituent | Yahoo `https://query2.finance.yahoo.com/v1/finance/search` | none | second naming source when Wikipedia's ticker is not a Yahoo symbol (see "Symbol resolution") |
| Daily OHLC history | `yfinance` (Yahoo Finance) | none | batch download, `period="1y"`, `interval="1d"` |
| Analyst consensus rating | Finnhub `/stock/recommendation` endpoint | **API key required** → ask user for `FINNHUB_API_KEY` | returns monthly buckets `strongBuy/buy/hold/sell/strongSell`; use most recent row |
| Balance sheet | `yfinance.Ticker(sym).balance_sheet` / `.quarterly_balance_sheet` | none | detail-panel assets & liabilities |
| Community attention | ApeWisdom `https://apewisdom.io/api/v1.0/filter/all-stocks` | none | US-only Reddit mention counts |
| Buy-side demand (order book) | **User picks one of five** — see "Buy-side demand" below | none for `derived` | `derived` is free and covers EU too; the rest are US-only |
| Short-sale volume | FINRA `https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt` | none | free public file, no account; US consolidated tape only |

If Finnhub rate-limits (60 req/min on free tier), throttle with `time.sleep(1.1)`
between calls and cache responses to `.cache/finnhub/{symbol}.json` for 24 h.

### EU ticker → Yahoo suffix mapping (minimum set)

```
Germany     → .DE     France      → .PA     Netherlands → .AS
Switzerland → .SW     UK          → .L      Italy       → .MI
Spain       → .MC     Sweden      → .ST     Denmark     → .CO
Belgium     → .BR     Finland     → .HE     Norway      → .OL
Ireland     → .IR     Portugal    → .LS     Austria     → .VI
Poland      → .WA     Luxembourg  → .LU     Czechia     → .PR
Greece      → .AT
```

The suffix alone is not enough: Wikipedia's ticker column also carries share
classes and local spellings. See **Symbol resolution** below.

## Support algorithm

Compute per ticker on the last 252 trading days (1 year):

1. Find swing lows: a bar `i` is a swing low if `low[i] == min(low[i-5 : i+5+1])` (5-bar fractal, both sides).
2. Cluster swing-low prices that are within 1.5% of each other (simple bucketing).
3. The **support level** = the highest cluster price that is **strictly below** the last close. (i.e. nearest support from above-down view.)
4. **Distance %** = `(last_close - support) / last_close * 100`.
5. A stock is "near support" if `0 <= distance% <= NEAR_THRESHOLD_PCT` (default `5.0`, configurable via `.env`).

If no qualifying support exists below last close, drop the ticker.

## Buy-side demand (order-book interest) — ask the user which provider

Every other signal is built from *executed* trades or from opinions. This one
measures what is actually resting on the book waiting to buy — the closest a
retail-accessible feed gets to "how many shares / how much money want to buy
this stock right now".

**Before writing any code, ask the user which provider to use.** Present these
five choices verbatim and write the answer to `.env` as `DEMAND_PROVIDER`:

| Choice | What you get | Cost | Catch |
|---|---|---|---|
| `derived` *(default)* | Chaikin Money Flow over the daily bars **already downloaded** for the support algorithm | **free, no key, no account** | An inference from where each close landed inside its bar, not a measurement of resting orders |
| `databento` | True L2 depth (`mbp-10`, 10 levels of bid/ask size) or cheap per-minute BBO (`bbo-1m`); historical and live | **$125 free signup credits**, then $199/mo | Credits expire 6 months, one set per team. `mbp-10` burns them fast — default to `bbo-1m`. US-only |
| `alpaca` | Real-time websocket quotes with `bs` / `as` (bid & ask size), plus an `imbalances` channel | **$0/mo, permanent** | IEX feed only (~2% of US volume), 30 symbols max per websocket, top-of-book only |
| `tiingo` | IEX endpoint with top-of-book bid/ask size | **$0/mo** | 500 unique symbols/month, 50 requests/hour, IEX-only |
| `none` | Signal disabled, neutral 0.5 for every row | — | — |

Prefer `derived` unless the user specifically wants real order-book depth. It
is the only option that needs no account and the only one that covers **EU
tickers**, so it removes the US-only coverage hole every other demand source
has.

Whichever is chosen, the contract is identical so the rest of the pipeline does
not change:

```python
# src/demand.py
def demand_signal(
    symbol: str, history: pd.DataFrame | None = None
) -> tuple[float | None, str | None]:
    """(subscore 0..1, human reason) or (None, None) when there is no data."""
```

### `derived` — Chaikin Money Flow (no API at all)

For each of the last 20 daily bars, locate the close inside its range and
weight that by the bar's volume:

$$\text{MFM} = \frac{(C - L) - (H - C)}{H - L} \qquad \text{CMF} = \frac{\sum \text{MFM}_i \cdot V_i}{\sum V_i}$$

A close near the high means buyers absorbed the session, so that bar's volume
counts as accumulation. CMF lands in $[-1, 1]$; the sub-score is
$(\text{CMF} + 1) / 2$. Bars with no intraday range ($H = L$) or zero volume
carry no directional information and are **skipped**, not treated as neutral;
if fewer than half the window is usable, return no reading rather than a
misleading one.

Be explicit in the docstring that this is an *estimate*. `CMF x average volume`
gives the net shares attributed to buyers per day, which is what the reason
string reports.

### `databento` / `alpaca` / `tiingo` — order book

The sub-score is the **bid share of resting size**, which is already
normalised:

$$\text{imbalance} = \frac{\text{bid size}}{\text{bid size} + \text{ask size}}$$

0.5 = balanced book, > 0.5 = more size queued to buy than to sell. Averaged over
the sampled session. Sum every level the schema exposes (one for BBO, ten for
`mbp-10`). The reason string must also carry the average resting **bid
notional** (`avg bid size x mid price`) — that is the "value people want to buy"
figure, and it is the whole point of the signal.

### Hard rules for this signal

- **Optional by construction.** No key, no SDK, or `DEMAND_PROVIDER=none` →
  `(None, None)` → a neutral 0.5 sub-score. The pipeline must never fail
  because demand data is unavailable.
- **The order-book providers are US-only.** Any ticker carrying an exchange
  suffix (`.DE`, `.PA`, …) is not in a US equities dataset; return
  `(None, None)` rather than guessing. There is no free EU depth source — do
  not invent one, and do not scrape an exchange or broker web page for it
  (breaks their terms of service and the underlying market-data licence, and
  the markup changes without notice). `derived` is the EU answer.
- Discard one-sided books (auctions, halts) and zero/negative mid prices.
- Reject a session with fewer than ~20 usable snapshots instead of scoring it.
- Cache per symbol to `.cache/demand/{symbol}.json` for 24 h; cache the empty
  result too, so a symbol with no coverage is not re-fetched all day.
- **Never log the request URL or the key.** Report failures by exception type
  only. Databento takes the key through the SDK constructor, not a query param.
- Never fabricate a book. A symbol with no data gets the neutral score and *no*
  reason string — the same treatment ApeWisdom gets for EU tickers.

## Short-sale volume (FINRA, free, no account)

FINRA publishes the short-sold share of consolidated volume for every US
trading day as a pipe-delimited text file on a public CDN:

```
https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
```

`short_pct = ShortVolume / TotalVolume x 100`. Market-wide this sits near
45-50%, because most short volume is market-maker hedging rather than
directional bearish positioning.

### Hard rules for this source

- **Display it, do not score it.** A high reading is genuinely ambiguous — it
  can mean bearish pressure or squeeze fuel. Render the column and let the
  reader judge; do not fold it into the composite score and do not attach a
  direction to it.
- This is **short volume**, not **short interest** (open positions, published
  fortnightly). Do not label it as the latter.
- Walk back day by day to find the newest published file: weekends have none,
  holidays 404, and there is a publication lag. Cap the walk (~7 days).
- Drop rows that are impossible (`total <= 0`, `short > total`, `short < 0`,
  non-numeric, missing symbol) and the `Total` footer row. Never repair them.
- US consolidated tape only. EU tickers get `None`, and the report must render
  that as `n/a` — never `0.0`.
- Cache the whole map to `.cache/finra/short_volume.json` for 24 h; one
  download serves every ticker.

## Analyst rating mapping

From the latest Finnhub recommendation row, pick the bucket with the highest
count. Tie-break order: `strongBuy > buy > hold > sell > strongSell`. Display
the human label and the total count `strongBuy+buy+hold+sell+strongSell`.

## Project layout

```
/
├── .env.example          # FINNHUB_API_KEY=, NEAR_THRESHOLD_PCT=5.0
├── requirements.txt      # yfinance, pandas, lxml, requests, python-dotenv, jinja2, tqdm, pytest
├── src/
│   ├── __init__.py
│   ├── config.py         # loads .env, exposes constants
│   ├── universe.py       # get_sp500() + get_stoxx600() → DataFrame[ticker, name, exchange]
│   ├── symbols.py        # Wikipedia ticker → Yahoo symbol resolution
│   ├── prices.py         # download_history(tickers) → dict[str, DataFrame]
│   ├── support.py        # compute_support(df) → (support, last_close, distance_pct) or None
│   ├── ratings.py        # fetch_rating(symbol) with on-disk cache
│   ├── fundamentals.py   # fetch_fundamentals(symbol) → balance sheet
│   ├── community.py      # ApeWisdom attention signal
│   ├── signals.py        # composite score + reasons
│   ├── report.py         # renders templates/report.html.j2 → report.html
│   └── main.py           # orchestrates the pipeline with tqdm progress bars
├── templates/
│   └── report.html.j2    # standalone HTML, inline CSS, sortable via small vanilla JS
├── tests/                # pytest suite (see "Tests")
└── report.html           # output (gitignored)
```

## Build steps (execute in order)

1. **Ask the user for the Finnhub API key** before writing any code. Do not proceed without it. Tell them to register at `https://finnhub.io/register` (free) and paste the key. Save to `.env`.
2. **Ask the user which buy-side demand provider to use** (`derived` / `databento` / `alpaca` / `tiingo` / `none`) using the table in "Buy-side demand" above. Save the answer as `DEMAND_PROVIDER` and, if they picked a keyed provider, ask for that key too. Default to `derived`, which needs nothing.
3. Create `requirements.txt` and install: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt` (Windows / PowerShell).
4. Implement modules in the order: `config → symbols → universe → prices → support → ratings → fundamentals → community → demand → short_volume → signals → report → main`.
5. Each module gets a `__main__` block with a tiny smoke test (e.g. `python -m src.universe` prints the first 10 rows).
6. `main.py` flow:
   - load universe (US + EU concatenated)
   - batch-download prices via `yfinance.download(tickers, period="1y", group_by="ticker", auto_adjust=False, threads=True)`
   - **re-resolve every ticker that returned no history** and download the recovered symbols
   - for each ticker compute support; keep only "near support"
   - compute 52w high, drawdown %, and days-since-high from the same history (no extra fetch)
   - fetch ratings for the survivors only (minimises API calls)
   - **drop every row whose rating is not `Buy` or `Strong Buy`**
   - fetch the community signal and the buy-side demand signal, then score and rank the survivors
   - fetch the FINRA short-volume map once and attach `short_pct` to each row (display only)
   - fetch balance-sheet fundamentals for the survivors only
   - render the single `report.html`
7. Print final summary: `"Wrote report.html with N rows (X US, Y EU)"`.
8. Run `python -m pytest tests -q` — the suite must be green before the build is done.

## HTML template requirements

- Single self-contained file (no external CSS/JS).
- Sticky header, zebra rows, monospace numerics, right-aligned numbers.
- Color the rating cell: Strong Buy = `#0a7d28`, Buy = `#3aa856`, Hold = `#8a8a8a`, Sell = `#d97706`, Strong Sell = `#b91c1c` (white text on dark backgrounds).
- Tiny vanilla-JS click-to-sort on every column header (no libraries).
- **Row selection**: clicking anywhere in a row highlights it and keeps it
  highlighted; selecting another row clears the previous one (single-select,
  never multi-select). Highlight = `#dbeafe` background + `inset 3px 0 0 #2563eb`
  left marker, and it must win over the zebra and `:hover` rules.
- **Detail panel**: the Ticker and Name cells are buttons with an info icon
  (`&#9432;`). Clicking either opens a modal (TradingView-style) for that stock,
  built entirely from the embedded per-ticker JSON. Sections, in order:
  1. **Key metrics** — score, last, support, distance, 52w high, drawdown,
     days since high, community, reasons.
  2. **Analyst recommendations** — one horizontal bar per bucket
     (Strong Buy / Buy / Hold / Sell / Strong Sell) with the **actual analyst
     count** next to it, plus the consensus label. Bars use the rating colors.
  3. **Balance sheet** — short- and long-term assets and liabilities with a
     Quarterly / Annual toggle and up to 4 reported periods as columns.
  Close via the X button, a click on the backdrop, or Escape.
- Panel content is built with `document.createElement` + `textContent` (never
  `innerHTML`) so issuer-supplied names cannot inject markup.
- Footer shows generation timestamp (UTC) and the `NEAR_THRESHOLD_PCT` used.

## Detail-panel data (real only)

| Panel section | Source | Module |
|---|---|---|
| Analyst buckets + counts | Finnhub `/stock/recommendation` (US) or Yahoo `recommendations` (EU) — the same payload already used for the consensus label | `src/ratings.py` (`Rating.counts`) |
| Balance sheet | `yfinance.Ticker(sym).balance_sheet` / `.quarterly_balance_sheet` | `src/fundamentals.py` |

Line items extracted (Yahoo row names in parentheses): total current assets
(`Current Assets`), total non-current assets (`Total Non Current Assets`), total
assets (`Total Assets`), total current liabilities (`Current Liabilities`), total
non-current liabilities (`Total Non Current Liabilities Net Minority Interest`),
total liabilities (`Total Liabilities Net Minority Interest`), total equity
(`Stockholders Equity`), total debt (`Total Debt`), cash & equivalents
(`Cash And Cash Equivalents`).

**Real data only.** A line item Yahoo does not report is *omitted from the
payload and from the rendered table* — never zero-filled, interpolated, or
estimated. A ticker with no balance sheet at all shows an explicit "no balance
sheet" message. Same for the analyst breakdown: if the source returns no
per-bucket counts, say so instead of inventing a distribution. Fundamentals are
cached to `.cache/fundamentals/{symbol}.json` for 24 h and fetched **only for the
rows that survive every filter**.

### Self-contradicting source totals

Yahoo occasionally publishes a total that contradicts its own components
(`ALC.SW` reports total liabilities equal to total assets). A reported total is
**dropped** — never silently corrected — when it deviates by more than
`TOTAL_TOLERANCE` (1%) from:

- `total_assets` vs `current_assets + non_current_assets`
- `total_assets` vs `total_liabilities + equity_gross`
- `total_liabilities` vs `current_liabilities + non_current_liabilities`

The drop is logged and the panel then shows the components only. `equity_gross`
(`Total Equity Gross Minority Interest`) is stored alongside `equity`
(`Stockholders Equity`) so the identity $A = L + E$ is verifiable; minority
interest can be negative, so `L + stockholders equity <= A` is **not** a valid
assumption.

## Symbol resolution (`src/symbols.py`)

Wikipedia is the only *constituent list*, but it is **not** the only naming
source. Its tickers are exchange-local (`VOLV B`, `NDA FI`, `BT.A`) while Yahoo
wants `VOLV-B.ST`, `NDA-FI.HE`, `BT-A.L`, and some constituents are listed under
a completely different symbol (`BNPP` -> `BNP.PA`, `FERR` -> `RACE.MI`,
`DANO` -> `BN.PA`). Resolution runs in stages, cheapest first:

1. **Mechanical** (`yahoo_variants`): join the share-class token with a dash and
   append the country suffix. This is what the universe emits.
2. **Plain base**: retry `BASE + suffix` for anything that returned no history.
3. **Name search**: Yahoo's free `/v1/finance/search` endpoint queried with the
   company name, then its accent-free spelling (Yahoo returns 0 hits for
   "Crédit Agricole"), then the bare base symbol.
4. **Glued share class**: `HMB` -> `HM-B`, accepted only if a search for that
   exact symbol returns an equity whose name matches the constituent.

A candidate is accepted only when it is an **equity on the expected exchange**
and either its base symbol matches *and* the names agree, or the name alone is a
close match (`NAME_MATCH_THRESHOLD`). Ties go to the shortest symbol, which is
the primary line rather than a secondary listing (`AKRBP.OL`, not `AKRBPO.OL`).
Every accepted symbol still has to return real price history before it is used,
and anything unresolved stays dropped. Search results are cached to
`.cache/symbols/` for 30 days; empty responses are **not** cached, since they are
usually throttling rather than "no such company".

This recovers about half of the constituents Yahoo does not recognise under
Wikipedia's spelling (104 misses -> ~50 recovered on a recent run).

## Tests (`tests/`, pytest)

`python -m pytest tests -q` — no network, no API key, runs in about a second.

| File | Covers |
|---|---|
| `test_support.py` | fractal swing lows, 1.5% clustering, nearest support below last close, distance %, 52w high, drawdown, days-since-high — on synthetic series with hand-computed answers |
| `test_signals.py` | each sub-score curve, the 50-day momentum ratio, weights summing to 1.0, the composite as a weighted sum, 0..100 bounds, reasons string |
| `test_ratings.py` | winning bucket, tie-break order, total = sum of buckets, counts preserved, empty -> unrated |
| `test_fundamentals.py` | NaN/inf rejection, omitted (not zero-filled) cells, period cap, dropping self-contradicting totals, plus accounting identities across every **real cached** balance sheet |
| `test_symbols.py` | mechanical variants, share-class spellings, accent folding, name similarity, match acceptance/rejection rules, shortest-symbol tie-break, the verified glued-class fallback — all against recorded Yahoo payloads, no network |
| `test_demand.py` | money flow (close at high/low/mid, volume weighting, flat and zero-volume bars skipped, window cut-off, EU coverage) and order-book folding (bid share of resting size, depth summed across levels, mid-price notional, one-sided books discarded, thin sessions rejected) — synthetic data, no network, no key |
| `test_short_volume.py` | FINRA file parsing: short/total percentage, header and `Total` footer ignored, impossible rows dropped rather than repaired, case-insensitive and dotted-share-class lookup, EU tickers unreadable, empty file — no network |
| `test_universe.py` | Wikipedia -> Yahoo ticker construction, unmapped-country drops, and the pipeline's missing-ticker recovery (base retry, name search, no-collision renames, unresolvable rows left alone) |
| `test_report_numbers.py` | re-derives every figure in the generated `report.html`: distance % and drawdown % from their inputs, support below last close and within threshold, analyst total = sum of buckets, consensus = winning bucket, Buy/Strong Buy filter, score reproducible from its components (implied momentum must land in 0..1), ranking order, and every rendered cell matching the embedded detail payload |

The report-level tests **skip** (not fail) when `report.html` or the caches are
absent, so a clean checkout still passes.

## Hard rules

- **Never** fabricate prices, ratings, or constituents. If a fetch fails for a ticker, log a warning and skip it.
- **Never** hard-code a Wikipedia-ticker → Yahoo-symbol mapping. Resolve it from a live source, require the match to be verifiable (right exchange, matching name), and confirm it by actually downloading price history.
- **Never** commit `.env` or the `.cache/` folder — add both to `.gitignore`.
- If Wikipedia layout changes and parsing fails, raise a clear error pointing at the offending URL; do not fall back to a hard-coded list.
- Keep dependencies to the ones in `requirements.txt`; do not add paid SDKs — the one exception is the demand provider's SDK the user explicitly chose, which must stay optional at import time.
- All network calls must have a 15 s timeout and one retry with exponential backoff.

## Done =

Running `python -m src.main` from a clean checkout (after `pip install -r requirements.txt`
and a populated `.env`) produces a non-empty `report.html` whose first row is the
cheapest qualifying stock, whose **Rating** column contains only `Buy` or `Strong Buy`,
and whose **Drawdown** / **52w High** / **Days since high** columns are populated for
every row.

## Optional follow-up: extra drawdown filter (`src/fallen_near_support.py`)

The drawdown columns are already in `report.html`. An auxiliary module exists to
produce a **secondary, narrower** HTML (`report_fallen.html`) that keeps only rows
with `drawdown% >= --min-drawdown` (default `25.0`) — useful when the user wants
"fallen + near support" pullback candidates only.

$$\text{drawdown\%} = \frac{\text{52w high} - \text{last close}}{\text{52w high}} \times 100$$

### Module

```
src/fallen_near_support.py
```

- Parses `report.html` (regex-strips `<tr>/<td>` cells; tolerates the inner `<span class="pill ...">` / `<span class="rating ...">` wrappers).
- Re-uses `src.prices.download_history` to recompute the 52w high — no separate data source, no new API key.
- Writes `report_fallen.html` with the **same column set** as `report.html`, sorted by drawdown descending.

### CLI

```powershell
# default: ≥25% off 52w high
python -m src.fallen_near_support

# stricter (deep crashes only)
python -m src.fallen_near_support --min-drawdown 40

# any pullback ≥15%
python -m src.fallen_near_support --min-drawdown 15

# top N most-drawn-down
python -m src.fallen_near_support --min-drawdown 25 --top 20
```

### Hard rules

- No new data source — must reuse `download_history`.
- Never fabricate the 52w high; if history is missing for a ticker, drop it.
- Drawdown is computed from raw (not adjusted) `High` to match the support algorithm; if `auto_adjust` is ever flipped on, update this module in sync.
- Output is sorted by drawdown desc; ties broken by ticker asc.

---

# Local build extensions (this workspace)

The following notes document how this workspace's implementation extends and
deviates from the base skill above. They are additive; the core support
algorithm, universe sources, and "no fabricated data" rules are unchanged.

## Ratings: hybrid Finnhub + Yahoo

Finnhub's **free tier only covers US listings** — every EU (`.DE`, `.PA`, …)
symbol returns `403 Forbidden`. To keep EU rows in the report, ratings are
sourced hybrid (`src/ratings.py`):

- **US tickers** → Finnhub `/stock/recommendation` (as the base skill specifies).
- **EU tickers** → Yahoo Finance analyst recommendations via
  `yfinance.Ticker(sym).recommendations` (same `strongBuy/buy/hold/sell/strongSell`
  buckets, most-recent row).
- **US fallback** → if Finnhub fails/empties for a US ticker, fall back to Yahoo.
- Yahoo responses cached to `.cache/yahoo/{symbol}.json` for 24 h.

Security: the Finnhub token travels as a URL query param, so all error messages
are **redacted to the HTTP status** — never log the request URL.

## Composite score & ranking (`src/signals.py`)

Instead of (or in addition to) sorting by last close, rows are ranked by a
transparent 0–100 **composite score** — a weighted blend of independent,
normalised (0..1) signals. Weights are explicit constants summing to 1.0:

```
support 0.25 · analyst 0.29 · drawdown 0.16 · momentum 0.12 · community 0.08 · demand 0.10
```

- **support**   — proximity to the support level (closer = higher).
- **analyst**   — rating strength × analyst-count confidence.
- **drawdown**  — pullback sweet-spot (peaks ~20% off the 52w high).
- **momentum**  — last close vs its 50-day SMA (recovering = higher).
- **community** — ApeWisdom attention (see below); US-only, neutral otherwise.
- **demand**    — resting order-book bid share (see below); US-only, neutral otherwise.

`compute_score()` returns the score, the per-signal breakdown, and a
human-readable **reasons** string. Rows are sorted by score desc, tie-break
cheapest first. This is research tooling, not investment advice — no black-box
buy/sell verdict is emitted.

## Community attention signal (`src/community.py`, ApeWisdom)

Free, no API key. `https://apewisdom.io/api/v1.0/filter/all-stocks/page/{n}`
returns paginated Reddit (r/wallstreetbets et al.) mention/upvote counts. The
whole feed is fetched once and cached to `.cache/apewisdom/map.json` for 6 h.

- Sub-score = 0.6 × log-scaled mention volume + 0.4 × 24h mention growth.
- Reason string includes a 24h trend arrow, e.g. `Reddit: 146 mentions ▲ (rank 8)`.
- **US-only**: ApeWisdom uses suffix-free symbols; EU tickers and any symbol
  absent from the feed get a neutral 0.5 sub-score and no reason.
- This is **attention/buzz, not verified bull/bear sentiment** — hence the small
  0.10 weight. Community data is the highest manipulation-risk source
  (pump-and-dump, bots); raw mention text is never fed back into an LLM.

## Report columns (this build)

```
Score | Ticker | Name | Market | Last | Support | Dist % | 52w High |
Drawdown | Days since high | Rating | # Analysts | Community | Demand |
Short % | Reasons
```

`Score` (first), `Community` and `Demand` (numeric, 0–100), `Short %` and
`Reasons` (last) are additions; the middle columns match the base skill's exact
order. `Short %` renders `n/a` for EU rows rather than a fabricated zero.

## Updated project layout (additions)

```
src/
├── signals.py       # composite score + per-signal breakdown + reasons
├── community.py     # ApeWisdom attention signal (US only), cached 6 h
├── demand.py        # buy-side demand: CMF (default) or Databento order book
├── short_volume.py  # FINRA daily short volume (US only), cached 24 h
├── fundamentals.py  # Yahoo balance sheet for the detail panel, cached 24 h
├── symbols.py       # Wikipedia ticker -> Yahoo symbol resolution, cached 30 d
└── ...              # config, universe, prices, support, ratings, report, main
.cache/
├── finnhub/         # US ratings, 24 h
├── yahoo/           # EU (+ US fallback) ratings, 24 h
├── fundamentals/    # balance sheets, 24 h
├── symbols/         # Yahoo symbol search results, 30 d
├── demand/          # order-book summaries, 24 h
├── finra/           # daily short-volume map, 24 h
└── apewisdom/       # community map, 6 h
```

## Buy-side demand signal (`src/demand.py`)

Two providers are implemented; `DEMAND_PROVIDER` selects one.

**`derived` (default, active in this build).** Chaikin Money Flow over the
20 most recent daily bars, taken from the history already downloaded for the
support algorithm. No API, no key, no account, and it is the only demand
provider that covers **EU tickers**. The reason string reads e.g.
`Flow: 60% buy-side ▲ (+7.9M sh / +2.5B per day)` — quantity *and* value.
Honest about its nature: it infers direction from where each close landed
inside its bar, so it is weaker evidence than a real book.

**`databento`.** Real resting-order depth.

- Key from `DATABENTO_API_KEY`; sign up at `https://databento.com/signup` for
  $125 of historical credits (expire after 6 months, one set per team).
- Dataset `DATABENTO_DATASET` (default `XNAS.ITCH`), schema `DATABENTO_SCHEMA`
  (default `bbo-1m`). `bbo-1m` is ~390 records per symbol per session, cheap
  enough for a daily run; `mbp-10` gives ten levels of depth but costs orders of
  magnitude more — switch only with a known credit budget.
- Samples the most recent available session (Databento embargoes the last day on
  the historical API), folds every snapshot into one summary, and caches it to
  `.cache/demand/{symbol}.json` for 24 h. Empty results are cached too.
- US-only; the key never reaches a URL, a cache file, or a log line.

Either way the signal is fully optional: no data yields a neutral 0.5 and no
reason, and `python -m src.main` runs unchanged.

## Short-sale volume (`src/short_volume.py`, FINRA)

One free public file per trading day, no account, ~12,300 US symbols. Walks
back up to 7 days to skip weekends, holidays and the publication lag, then
caches the whole map for 24 h. Rendered as the `Short %` column and in the
detail panel; **deliberately excluded from the composite score** because a high
reading is ambiguous (bearish pressure or squeeze fuel). EU rows show `n/a`.

## Known data-quality notes

- Constituents Yahoo does not list under Wikipedia's spelling are recovered by
  `src/symbols.py` (see above). The remainder are genuinely unavailable —
  delisted or acquired names (Direct Line, DS Smith), or local lines Yahoo's
  search never surfaces. Per the hard rules these are logged and skipped, not
  fabricated or hard-coded.
- Wikipedia's STOXX Europe 600 table currently lists ~460 constituents (not the
  full 600); the app uses whatever the live page provides.

