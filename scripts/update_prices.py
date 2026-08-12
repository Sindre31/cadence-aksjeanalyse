#!/usr/bin/env python3
"""Cadence — fast price-only refresh.

The full refresh (scripts/fetch_data.py) rebuilds the whole analytics corpus and
takes ~50 minutes of Yahoo bandwidth, so it runs weekly. Prices go stale much
faster than weekday/hour edges do, so this script tops up just the quotes —
around 2-5 minutes, no analytics touched:

  public/data/catalog.json   px / chg per instrument (search + picker)
  public/data/s/<n>.json     px / chg in the detail shards
  public/data/meta.json      pricesAsOf timestamp + how many rows moved

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
SHARDS = 256
YAHOO_CHUNK = 200
# the workflow times out at 25 min; leave room to write files and push
DEADLINE = time.time() + 16 * 60

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
cat_by_id = {str(c["id"]): c for c in catalog}
log(f"catalog: {len(catalog)} instruments")

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
    c = cat_by_id.get(cid)
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
    log(f"Yahoo close fallback for {len(tickers)} tickers...")
    try:
        import yfinance as yf
    except ImportError:
        tickers = []
        log("  yfinance not installed — skipping fallback")
    for i in range(0, len(tickers), YAHOO_CHUNK):
        if time.time() > DEADLINE:
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
                prev = float(col.iloc[-2])
                chg = f((last / prev - 1) * 100) if prev > 0 else None
            for cid in need.get(t, []):
                quotes[cid]["px"] = f(last)
                if quotes[cid]["chg"] is None:
                    quotes[cid]["chg"] = chg
                n_yahoo += 1
        time.sleep(1)
    log(f"  filled {n_yahoo} instruments from Yahoo closes")

# ---------------- 4. write catalog + the shards that moved ----------------
n_cat, n_unknown = 0, 0
for cid, q in quotes.items():
    c = cat_by_id.get(cid)
    if c is None:
        n_unknown += 1
        continue
    px = q["px"] if (q["px"] or 0) > 0 else None
    if px is None:
        continue          # keep the last known price rather than blanking it
    if c.get("px") != px or c.get("chg") != q["chg"]:
        c["px"], c["chg"] = px, q["chg"]
        n_cat += 1

# a run between sessions (Monday morning after Friday's close) legitimately moves
# nothing — leave the tree untouched so the workflow commits nothing and passes
if n_cat == 0:
    log("DONE: no price changed since the last run")
    raise SystemExit(0)

sdir = OUT / "s"
n_shard, touched = 0, set()
for n in range(SHARDS):
    p = sdir / f"{n}.json"
    if not p.exists():
        continue
    sh = json.loads(p.read_text(encoding="utf-8"))
    dirty = False
    for cid, row in sh.items():
        c = cat_by_id.get(cid)
        if c is None:
            continue
        if row.get("px") != c.get("px") or row.get("chg") != c.get("chg"):
            row["px"], row["chg"] = c.get("px"), c.get("chg")
            dirty, n_shard = True, n_shard + 1
    if dirty:
        p.write_text(json.dumps(sh, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        touched.add(n)

(OUT / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, allow_nan=False),
                                  encoding="utf-8")

meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
meta["pricesAsOf"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
meta["pricesUpdated"] = n_cat
(OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

log(f"\nDONE: {n_cat} catalog prices · {n_shard} shard rows in {len(touched)} shards · "
    f"{n_yahoo} from Yahoo · {n_unknown} instruments not in the catalog yet")
