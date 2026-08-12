#!/usr/bin/env python3
"""Cadence — fast price-only refresh.

The full refresh (scripts/fetch_data.py) rebuilds the whole analytics corpus and
takes ~50 minutes of Yahoo bandwidth, so it runs weekly. Prices go stale much
faster than weekday/hour edges do, so this script tops up just the quotes —
around 4 minutes, and it writes exactly two files:

  public/data/prices.json    {id: [px, chg]} for every priced instrument
  public/data/meta.json      pricesAsOf timestamp + how many quotes moved

Quotes live in prices.json alone, and nowhere else. Carrying them in
catalog.json and the 256 detail shards as well cost ~3.6 MB of git history per
run, twice a trading day; this one file is ~25x smaller.

Prices come from Nordnet. The venues Nordnet does not stream to logged-out
clients (most Oslo Bors majors, Stockholm, Toronto, Paris) report last = 0, so
those fall back to the latest Yahoo daily close — the same backfill the full
refresh does, but only for instruments that already carry analytics.

Instruments that are not in the catalog yet are left to the weekly full refresh,
which is what builds catalog rows and shards in the first place.

Usage:
    python3 scripts/update_prices.py
"""
import json, math, sys, time, datetime as dt
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "data"
NN = "https://www.nordnet.no/api/2"
HDRS = {"Accept": "application/json", "client-id": "NEXT",
        "User-Agent": "Mozilla/5.0 (compatible; cadence/1.0; personal project)"}
# small chunks on purpose: the time budget is only checked between them, and a
# throttled 200-ticker chunk can sit in yfinance's internal retries for tens of
# minutes — long enough for the 25-minute job timeout to kill the run before it
# commits anything
YAHOO_CHUNK = 60
YAHOO_BUDGET = 8 * 60

YSUF = {"NO": ".OL", "SE": ".ST", "DK": ".CO", "FI": ".HE", "DE": ".DE", "US": "",
        "CA": ".TO", "FR": ".PA", "NL": ".AS", "BE": ".BR", "IT": ".MI", "ES": ".MC",
        "PT": ".LS", "AT": ".VI", "CH": ".SW", "GB": ".L", "IE": ".IR"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def f(x, d=2):
    try:
        v = round(float(x), d)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def get(url):
    req = urllib.request.Request(url, headers=HDRS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 2:
                raise
            log(f"  retry {url.split('?')[0]}: {e}")
            time.sleep(2)


def page_all(query):
    out, offset = [], 0
    while True:
        d = get(f"{NN}/instrument_search/query/{query}?limit=100&offset={offset}")
        rs = d.get("results", [])
        out += rs
        offset += len(rs)
        if offset >= d.get("total_hits", 0) or not rs:
            return out
        time.sleep(0.2)


def yahoo_sym(sym, country):
    if not sym or country not in YSUF:
        return None
    s = sym.replace(" ", "-")
    if country == "US":
        s = s.replace(".", "-")
    return s + YSUF[country]


def price_of(r):
    p = (r.get("price_info") or {}).get("last") or {}
    return f(p.get("price"))


# ---------------- 1. what we already publish ----------------
catalog = json.loads((OUT / "catalog.json").read_text(encoding="utf-8"))
known = {str(c["id"]): c for c in catalog}
try:
    prev = json.loads((OUT / "prices.json").read_text(encoding="utf-8")).get("p", {})
except FileNotFoundError:
    # first run after the split — seed from whatever the catalog still carries
    prev = {str(c["id"]): [c["px"], c.get("chg")] for c in catalog if c.get("px")}
    log("  no prices.json yet — seeding from catalog")
log(f"catalog: {len(catalog)} instruments · {len(prev)} known quotes")

# ---------------- 2. Nordnet quotes ----------------
quotes = {}   # str(id) -> dict(px, chg, sym, country)
for query in ("stocklist", "fundlist", "etflist"):
    rows = page_all(query)
    log(f"  {query}: {len(rows)} rows")
    for r in rows:
        ii = r.get("instrument_info") or {}
        pi = r.get("price_info") or {}
        hr = r.get("historical_returns_info") or {}
        ei = r.get("exchange_info") or {}
        iid = ii.get("instrument_id")
        if not iid:
            continue
        chg = f(pi.get("diff_pct")) if pi.get("diff_pct") is not None else f(hr.get("yield_1d"))
        quotes[str(iid)] = dict(px=price_of(r), chg=chg, sym=ii.get("symbol"),
                                country=ei.get("exchange_country") or "?")
n_live = sum(1 for q in quotes.values() if (q["px"] or 0) > 0)
log(f"  quotes: {len(quotes)} instruments ({n_live} with a live price)")
if n_live < 2000:
    raise SystemExit(f"only {n_live} live prices from Nordnet — refusing to publish a broken fetch")

# ---------------- 3. Yahoo close for the venues Nordnet zeroes out ----------
# only for instruments that carry analytics — the long tail is not worth the
# bandwidth here, and GB/IE are skipped because Yahoo quotes those in pence
need = {}   # yahoo ticker -> [ids]
for cid, q in quotes.items():
    c = known.get(cid)
    if c is None or not c.get("hasA") or (q["px"] or 0) > 0:
        continue
    if q["country"] in ("GB", "IE"):
        continue
    t = yahoo_sym(q["sym"], q["country"])
    if t:
        need.setdefault(t, []).append(cid)

n_yahoo = 0
if need:
    tickers = sorted(need)
    deadline = time.time() + YAHOO_BUDGET
    log(f"Yahoo close fallback for {len(tickers)} tickers...")
    try:
        import yfinance as yf
    except ImportError:
        tickers = []
        log("  yfinance not installed — skipping fallback")
    for i in range(0, len(tickers), YAHOO_CHUNK):
        if time.time() > deadline:
            log(f"  stopped at {i}/{len(tickers)}: out of time budget")
            break
        part = tickers[i:i + YAHOO_CHUNK]
        try:
            h = yf.download(part, period="5d", interval="1d", auto_adjust=True,
                            progress=False, threads=True, group_by="column")
        except Exception as e:
            log(f"  chunk {i // YAHOO_CHUNK + 1} failed: {e}")
            time.sleep(5)
            continue
        if h is None or h.empty or "Close" not in h.columns.get_level_values(0):
            continue
        cl = h["Close"]
        if not hasattr(cl, "columns"):
            cl = cl.to_frame(name=part[0])
        for t in cl.columns:
            col = cl[t].dropna()
            if len(col) < 1:
                continue
            last = float(col.iloc[-1])
            if last <= 0:
                continue
            chg = None
            if len(col) >= 2:
                prv = float(col.iloc[-2])
                chg = f((last / prv - 1) * 100) if prv > 0 else None
            for cid in need.get(t, []):
                quotes[cid]["px"] = f(last)
                if quotes[cid]["chg"] is None:
                    quotes[cid]["chg"] = chg
                n_yahoo += 1
        time.sleep(1)
    log(f"  filled {n_yahoo} instruments from Yahoo closes")

# ---------------- 4. merge and write ----------------
prices, n_moved, n_unknown = dict(prev), 0, 0
for cid, q in quotes.items():
    if cid not in known:
        n_unknown += 1
        continue
    if (q["px"] or 0) <= 0:
        continue          # keep the last known price rather than blanking it
    row = [q["px"], q["chg"]]
    if prices.get(cid) != row:
        prices[cid] = row
        n_moved += 1

# a run between sessions (Monday morning after Friday's close) legitimately moves
# nothing — leave the tree untouched so the workflow commits nothing and passes
if n_moved == 0:
    log("DONE: no price changed since the last run")
    raise SystemExit(0)

now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
(OUT / "prices.json").write_text(json.dumps(dict(asOf=now, p=prices),
                                            ensure_ascii=False, allow_nan=False),
                                 encoding="utf-8")
meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
meta["pricesAsOf"] = now
meta["pricesUpdated"] = n_moved
(OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

log(f"\nDONE: {n_moved} quotes moved · {len(prices)} priced · {n_yahoo} from Yahoo · "
    f"{n_unknown} instruments not in the catalog yet")
