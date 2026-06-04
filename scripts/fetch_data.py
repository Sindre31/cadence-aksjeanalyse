#!/usr/bin/env python3
"""Cadence — import the full Nordnet catalog + compute trading-timing analytics.

Outputs (all static, served by Vite/Vercel from public/):
  public/data/catalog.json   search index — EVERY Nordnet share (all exchanges),
                             fund and ETF (~14k rows, lazy-loaded by the picker)
  public/data/s/<n>.json     256 detail shards (id % 256) for every instrument:
                             Nordnet return summary, ratios, fund facts
  public/data/a/<id>.json    full timing analytics for the analytics universe:
                             weekday / hour / month edge stats, day x hour edge
                             matrix (from real Yahoo hourly bars), earnings
                             drift, recommendation per timeframe (1Y/3Y/5Y)
  public/data/meta.json      asOf + counts

Analytics universe: all Oslo Bors (NO) shares + the most-owned foreign shares
on Nordnet + most-owned ETFs + the OSEBX/OBX index. Daily history 5y, hourly
bars 730d (Yahoo's max), earnings dates for the most-owned names.

Usage:
    python3 -m venv .venv && .venv/bin/pip install yfinance numpy pandas
    .venv/bin/python scripts/fetch_data.py
"""
import json, math, sys, time, datetime as dt
from pathlib import Path
import urllib.request
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "data"
NN = "https://www.nordnet.no/api/2"
HDRS = {"Accept": "application/json", "client-id": "NEXT",
        "User-Agent": "Mozilla/5.0 (compatible; cadence/1.0; personal project)"}

FOREIGN_TOP = 400        # most-owned foreign shares to give full analytics
ETF_TOP = 100            # most-owned ETFs to give full analytics
EARNINGS_TOP = 150       # most-owned analytics stocks to fetch earnings dates for
SHARDS = 256
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TFS = {"1Y": 252, "3Y": 756, "5Y": 1260}

YSUF = {"NO": ".OL", "SE": ".ST", "DK": ".CO", "FI": ".HE", "DE": ".DE", "US": "",
        "CA": ".TO", "FR": ".PA", "NL": ".AS", "BE": ".BR", "IT": ".MI", "ES": ".MC",
        "PT": ".LS", "AT": ".VI", "CH": ".SW", "GB": ".L", "IE": ".IR"}
TZ = {"NO": "Europe/Oslo", "SE": "Europe/Stockholm", "DK": "Europe/Copenhagen",
      "FI": "Europe/Helsinki", "DE": "Europe/Berlin", "US": "America/New_York",
      "CA": "America/Toronto", "FR": "Europe/Paris", "NL": "Europe/Amsterdam",
      "BE": "Europe/Brussels", "IT": "Europe/Rome", "ES": "Europe/Madrid",
      "PT": "Europe/Lisbon", "AT": "Europe/Vienna", "CH": "Europe/Zurich",
      "GB": "Europe/London", "IE": "Europe/Dublin"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


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
        if offset % 2000 < 100:
            log(f"  ...{offset}/{d.get('total_hits')}")
        if offset >= d.get("total_hits", 0) or not rs:
            return out
        time.sleep(0.2)


def f(x, d=2):
    try:
        v = round(float(x), d)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def yahoo_sym(sym, country):
    if not sym or country not in YSUF:
        return None
    s = sym.replace(" ", "-")
    if country == "US":
        s = s.replace(".", "-")
    return s + YSUF[country]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------- 1. Nordnet catalog ----------------
log("fetching Nordnet stocklist (ALL countries)...")
stocks_raw = page_all("stocklist")
log(f"  {len(stocks_raw)} shares")
log("fetching Nordnet fundlist...")
funds_raw = page_all("fundlist")
log(f"  {len(funds_raw)} funds")
log("fetching Nordnet etflist...")
etfs_raw = page_all("etflist")
log(f"  {len(etfs_raw)} ETFs")


def price_of(r):
    p = (r.get("price_info") or {}).get("last") or {}
    return f(p.get("price"))


shares, funds, etfs = [], [], []
for r in stocks_raw:
    ii, pi = r.get("instrument_info", {}), r.get("price_info", {})
    kr, hr = r.get("key_ratios_info", {}) or {}, r.get("historical_returns_info", {}) or {}
    ei = r.get("exchange_info", {}) or {}
    if not ii.get("symbol") or not ii.get("instrument_id"):
        continue
    country = ei.get("exchange_country") or "?"
    shares.append(dict(
        id=ii["instrument_id"], sym=ii["symbol"], country=country,
        exch=(ei.get("exchanges") or [country])[0],
        yt=yahoo_sym(ii["symbol"], country),
        name=(ii.get("long_name") or ii.get("name") or ii["symbol"]).title(),
        ccy=ii.get("currency", "?"), isin=ii.get("isin"),
        px=price_of(r), chg=f(pi.get("diff_pct")),
        turn=f((pi.get("turnover_normalized") or 0), 0),
        pe=f(kr.get("pe")), pb=f(kr.get("pb")), ps=f(kr.get("ps")),
        div=f(kr.get("dividend_yield")) or 0,
        owners=(r.get("statistical_info") or {}).get("number_of_owners", 0),
        slug=(r.get("nnx_info") or {}).get("display_slug"),
        y={k: f(v, 1) for k, v in hr.items() if isinstance(v, (int, float))},
    ))

for r in funds_raw:
    ii = r.get("instrument_info", {})
    fi, hr = r.get("fund_info", {}) or {}, r.get("historical_returns_info", {}) or {}
    if not ii.get("instrument_id"):
        continue
    funds.append(dict(
        id=ii["instrument_id"],
        name=ii.get("display_name") or ii.get("name"),
        ccy=ii.get("currency", "?"), isin=ii.get("isin"),
        px=price_of(r), chg=f(hr.get("yield_1d")),
        cat=fi.get("fund_category") or fi.get("fund_type") or "Fond",
        owners=(r.get("statistical_info") or {}).get("number_of_owners", 0),
        slug=(r.get("nnx_info") or {}).get("display_slug"),
        y={k: f(v, 1) for k, v in hr.items() if isinstance(v, (int, float))},
        fund=dict(ms=fi.get("fund_ms_rating"), fee=f(fi.get("fund_yearly_fee")),
                  calcFee=f(fi.get("fund_calculated_fee")), risk=fi.get("fund_raw_risk"),
                  aum=f((fi.get("fund_total_market_value") or 0) / 1e9, 2),
                  admin=fi.get("fund_branding_company") or fi.get("fund_admin_company"),
                  type=fi.get("fund_type"), sfdr=fi.get("fund_sfdr_article"),
                  esg=fi.get("fund_esg_score"), minInv=fi.get("fund_min_investment")),
    ))

for r in etfs_raw:
    ii, pi = r.get("instrument_info", {}), r.get("price_info", {})
    fi, hr = r.get("fund_info", {}) or {}, r.get("historical_returns_info", {}) or {}
    ei = r.get("exchange_info", {}) or {}
    if not ii.get("symbol") or not ii.get("instrument_id"):
        continue
    country = ei.get("exchange_country") or "?"
    etfs.append(dict(
        id=ii["instrument_id"], sym=ii["symbol"], country=country,
        exch=(ei.get("exchanges") or [country])[0] or country,
        yt=yahoo_sym(ii["symbol"], country),
        name=(ii.get("long_name") or ii.get("name") or ii["symbol"]),
        ccy=ii.get("currency", "?"), isin=ii.get("isin"),
        px=price_of(r), chg=f(pi.get("diff_pct")) if pi.get("diff_pct") is not None else f(hr.get("yield_1d")),
        turn=f((pi.get("turnover_normalized") or 0), 0),
        owners=(r.get("statistical_info") or {}).get("number_of_owners", 0),
        slug=(r.get("nnx_info") or {}).get("display_slug"),
        y={k: f(v, 1) for k, v in hr.items() if isinstance(v, (int, float))},
        cat=fi.get("fund_category") or fi.get("fund_type") or "ETF",
        fund=dict(ms=fi.get("fund_ms_rating"), fee=f(fi.get("fund_yearly_fee")),
                  calcFee=f(fi.get("fund_calculated_fee")), risk=fi.get("fund_raw_risk"),
                  aum=f((fi.get("fund_total_market_value") or 0) / 1e9, 2),
                  admin=fi.get("fund_branding_company") or fi.get("fund_admin_company"),
                  type=fi.get("fund_type"), sfdr=fi.get("fund_sfdr_article"),
                  esg=fi.get("fund_esg_score"), minInv=fi.get("fund_min_investment")),
    ))

# ---------------- 2. analytics universe ----------------
no_shares = [s for s in shares if s["country"] == "NO" and s["yt"]]
foreign = sorted([s for s in shares if s["country"] != "NO" and s["yt"] and (s["owners"] or 0) > 0],
                 key=lambda s: s["owners"] or 0, reverse=True)[:FOREIGN_TOP]
etf_top = sorted([e for e in etfs if e["yt"] and (e["owners"] or 0) > 0],
                 key=lambda e: e["owners"] or 0, reverse=True)[:ETF_TOP]
universe = ([dict(s, kind="stock") for s in no_shares]
            + [dict(s, kind="stock") for s in foreign]
            + [dict(e, kind="etf") for e in etf_top])
# de-dup yahoo tickers (some symbols collide)
seen_yt, uni = set(), []
for s in universe:
    if s["yt"] in seen_yt:
        continue
    seen_yt.add(s["yt"])
    uni.append(s)
universe = uni
log(f"analytics universe: {len(no_shares)} NO + {len(foreign)} foreign + {len(etf_top)} ETF "
    f"= {len(universe)} after de-dup")

# index pseudo-instrument
INDEX = dict(id="OSEBX", sym="OSEBX", name="Oslo Børs Benchmark", country="NO",
             ccy="NOK", kind="index", owners=0, slug=None, yt=None)
log("picking index ticker...")
for c in ["OBX.OL", "^OSEAX"]:
    try:
        h = yf.download(c, period="1y", interval="1d", auto_adjust=True, progress=False)["Close"].dropna()
        if len(h) > 150:
            INDEX["yt"] = c
            log(f"  index: {c}")
            break
    except Exception as e:
        log(f"  index {c} failed: {e}")
if not INDEX["yt"]:
    raise SystemExit("no index ticker worked")

# ---------------- 3. daily history (5y) ----------------
all_ts = [s["yt"] for s in universe] + [INDEX["yt"]]
log(f"downloading 5y daily history for {len(all_ts)} tickers...")
daily_px, daily_vol = {}, {}
CH = 400
for i in range(0, len(all_ts), CH):
    ts = all_ts[i:i + CH]
    log(f"  daily chunk {i // CH + 1}/{(len(all_ts) + CH - 1) // CH} ({len(ts)})")
    try:
        h = yf.download(ts, period="5y", interval="1d", auto_adjust=True,
                        progress=False, threads=True, group_by="column")
    except Exception as e:
        log(f"  chunk failed: {e}")
        continue
    if h is None or h.empty:
        continue
    cl = h["Close"] if "Close" in h.columns.get_level_values(0) else None
    vo = h["Volume"] if "Volume" in h.columns.get_level_values(0) else None
    if cl is None:
        continue
    if not hasattr(cl, "columns"):
        cl = cl.to_frame(name=ts[0])
        vo = vo.to_frame(name=ts[0]) if vo is not None else None
    for t in cl.columns:
        col = cl[t].dropna()
        if len(col) >= 60:
            daily_px[t] = col
            if vo is not None and t in vo.columns:
                daily_vol[t] = vo[t].reindex(col.index)
    time.sleep(1)
log(f"  daily history: {len(daily_px)} tickers")

# ---------------- 4. hourly history (730d, per-country chunks for tz) ----------------
log("downloading hourly history (730d)...")
hourly = {}   # yt -> DataFrame [Open, Close, Volume] tz-localized to exchange
by_country = {}
for s in universe + [dict(INDEX, country="NO")]:
    if s["yt"] in daily_px:
        by_country.setdefault(s["country"], []).append(s["yt"])
HCH = 80
for country, ts_all in sorted(by_country.items(), key=lambda kv: -len(kv[1])):
    tz = TZ.get(country)
    if not tz:
        continue
    for i in range(0, len(ts_all), HCH):
        ts = ts_all[i:i + HCH]
        log(f"  hourly {country} chunk {i // HCH + 1}/{(len(ts_all) + HCH - 1) // HCH} ({len(ts)})")
        h = None
        for attempt in range(2):
            try:
                h = yf.download(ts, period="730d", interval="1h", auto_adjust=True,
                                progress=False, threads=True, group_by="ticker")
                break
            except Exception as e:
                log(f"    failed: {e} — backing off 60s")
                time.sleep(60)
        if h is None or h.empty:
            continue
        single = not isinstance(h.columns, pd.MultiIndex)
        for t in ts:
            try:
                d = h if single else h[t]
                d = d[["Open", "Close", "Volume"]].dropna(subset=["Close"])
            except Exception:
                continue
            if len(d) < 200:
                continue
            try:
                idx = d.index.tz_convert(tz)
            except TypeError:
                idx = d.index.tz_localize("UTC").tz_convert(tz)
            d = d.copy()
            d.index = idx
            hourly[t] = d
        time.sleep(2)
log(f"  hourly history: {len(hourly)} tickers")

# ---------------- 5. earnings dates ----------------
log("fetching earnings dates...")
earn_dates = {}   # yt -> sorted list of pd.Timestamp (dates, naive)
earn_targets = sorted([s for s in universe if s["kind"] == "stock"],
                      key=lambda s: (s["owners"] or 0), reverse=True)[:EARNINGS_TOP]
for k, s in enumerate(earn_targets):
    t = s["yt"]
    try:
        ed = yf.Ticker(t).get_earnings_dates(limit=16)
        if ed is not None and len(ed):
            ds = sorted({pd.Timestamp(d).tz_localize(None).normalize() for d in ed.index})
            earn_dates[t] = ds
    except Exception as e:
        if "429" in str(e) or "Too Many" in str(e):
            log("  429 — backing off 60s")
            time.sleep(60)
    time.sleep(0.35)
    if k % 25 == 24:
        log(f"  ...{k + 1}/{len(earn_targets)} ({len(earn_dates)} with dates)")
log(f"  earnings dates: {len(earn_dates)} stocks")

NOW = pd.Timestamp.now().normalize()


# ---------------- 6. analytics computation ----------------
def edge_scores(rows):
    """rows: list of dicts with ret, winRate, volume, vol -> add relative edge 0..100.
    Edge = within-instrument relative favourability: blend of normalized mean
    return (50%), win rate (32%), liquidity (18%), minus a volatility penalty."""
    if not rows:
        return rows

    def norm(vals):
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-12:
            return [0.5] * len(vals)
        return [(v - lo) / (hi - lo) for v in vals]

    rN = norm([r["ret"] for r in rows])
    wN = norm([r["winRate"] for r in rows])
    lN = norm([r["volume"] for r in rows])
    vN = norm([r["vol"] for r in rows])
    for i, r in enumerate(rows):
        raw = 0.5 * rN[i] + 0.32 * wN[i] + 0.18 * lN[i] - 0.18 * vN[i]
        # raw spans [-0.18, 1]; shift so the volatility penalty can't floor everything
        r["edge"] = int(round(clamp(raw + 0.09, 0, 1) * 100))
    return rows


def bucket_stats(rets, vols, keys, order):
    """Aggregate return/volume series grouped by key -> design bucket rows."""
    rows = []
    for key in order:
        m = keys == key
        rr = rets[m]
        if len(rr) < 5:
            rows.append(dict(key=str(key), ret=0.0, winRate=50, volume=0, vol=0.0, n=int(len(rr))))
            continue
        vv = vols[m]
        rows.append(dict(
            key=str(key),
            ret=float(round(rr.mean() * 100, 3)),
            winRate=int(round((rr > 0).mean() * 100)),
            volume=float(vv.mean()) if len(vv) else 0.0,
            vol=float(round(rr.std() * 100, 2)),
            n=int(len(rr)),
        ))
    # scale volume to 0..100 relative
    mx = max((r["volume"] for r in rows), default=0)
    for r in rows:
        r["volume"] = int(round(r["volume"] / mx * 100)) if mx > 0 else 0
    return edge_scores(rows)


def hourly_frames(t):
    """-> per-bar DataFrame with dow, hour, ret(frac), volume; or None."""
    d = hourly.get(t)
    if d is None:
        return None
    d = d.sort_index()
    sess = d.index.date
    close = d["Close"].values
    open_ = d["Open"].values
    ret = np.empty(len(d))
    prev_sess = None
    for i in range(len(d)):
        if sess[i] != prev_sess:
            ret[i] = close[i] / open_[i] - 1 if open_[i] else 0.0
            prev_sess = sess[i]
        else:
            ret[i] = close[i] / close[i - 1] - 1 if close[i - 1] else 0.0
    fr = pd.DataFrame(dict(
        ts=d.index, dow=d.index.dayofweek, hour=d.index.hour,
        ret=ret, volume=d["Volume"].fillna(0).values), index=d.index)
    fr = fr[(fr["dow"] <= 4) & np.isfinite(fr["ret"]) & (np.abs(fr["ret"]) < 0.25)]
    # keep hours present in a meaningful share of sessions (drops auction stubs)
    n_sess = max(1, fr["ts"].dt.date.nunique())
    cnt = fr.groupby("hour")["ret"].count()
    good_hours = sorted([h for h, c in cnt.items() if c >= n_sess * 0.25])
    fr = fr[fr["hour"].isin(good_hours)]
    return fr if len(fr) >= 300 else None


def analytics_for(s):
    t = s["yt"]
    px = daily_px.get(t)
    if px is None or len(px) < 120:
        return None
    dr = px.pct_change().dropna()
    dr = dr[np.abs(dr) < 0.5]
    dvol = daily_vol.get(t)
    dvol = dvol.reindex(dr.index).fillna(0) if dvol is not None else pd.Series(0, index=dr.index)

    hf = hourly_frames(t)
    hours_axis = sorted(hf["hour"].unique()) if hf is not None else []
    hours_axis = [f"{h:02d}" for h in hours_axis]

    # momentum for verdict
    cl = px.values

    def mom(n):
        return float((cl[-1] / cl[-n - 1] - 1) * 100) if len(cl) > n else 0.0

    mom3, mom12 = mom(63), mom(252)

    # earnings
    earn = earnings_for(s, px, dr)

    tf_out = {}
    for tf, n in TFS.items():
        r = dr.tail(n)
        v = dvol.reindex(r.index).fillna(0)
        if len(r) < 60:
            continue
        weekday = bucket_stats(r.values, v.values, r.index.dayofweek.values, list(range(5)))
        for i, row in enumerate(weekday):
            row["key"] = DAYS[i]
        month = bucket_stats(r.values, v.values, r.index.month.values, list(range(1, 13)))
        for i, row in enumerate(month):
            row["key"] = MONTHS[i]

        hour_rows, matrix, intraday_days = None, None, 0
        if hf is not None:
            cutoff = NOW - pd.Timedelta(days=min(365 * int(tf[0]), 730))
            sub = hf[hf.index.tz_localize(None) >= cutoff]
            if len(sub) >= 300:
                intraday_days = int(sub["ts"].dt.date.nunique())
                hh = [int(h) for h in hours_axis]
                hour_rows = bucket_stats(sub["ret"].values, sub["volume"].values,
                                         sub["hour"].values, hh)
                for i, row in enumerate(hour_rows):
                    row["key"] = hours_axis[i]
                # day x hour matrix
                cells = []
                for di in range(5):
                    for h in hh:
                        m = (sub["dow"].values == di) & (sub["hour"].values == h)
                        rr = sub["ret"].values[m]
                        vv = sub["volume"].values[m]
                        if len(rr) >= 5:
                            cells.append(dict(ret=float(rr.mean() * 100),
                                              winRate=float((rr > 0).mean() * 100),
                                              volume=float(vv.mean()),
                                              vol=float(rr.std() * 100)))
                        else:
                            cells.append(dict(ret=0.0, winRate=50.0, volume=0.0, vol=0.0))
                mx = max((c["volume"] for c in cells), default=0)
                for c in cells:
                    c["volume"] = c["volume"] / mx * 100 if mx > 0 else 0
                edge_scores(cells)
                matrix = [[cells[di * len(hh) + hi]["edge"] for hi in range(len(hh))]
                          for di in range(5)]

        # recommendation
        best, worst = None, None
        if matrix:
            for di, row in enumerate(matrix):
                for hi, e in enumerate(row):
                    if best is None or e > best["edge"]:
                        best = dict(day=DAYS[di], hour=hours_axis[hi], edge=e)
                    if worst is None or e < worst["edge"]:
                        worst = dict(day=DAYS[di], hour=hours_axis[hi], edge=e)
        else:
            wb = max(weekday, key=lambda r: r["edge"])
            ww = min(weekday, key=lambda r: r["edge"])
            best = dict(day=wb["key"], hour=None, edge=wb["edge"])
            worst = dict(day=ww["key"], hour=None, edge=ww["edge"])

        win_all = float((r > 0).mean() * 100)
        earnings_soon = bool(earn and earn.get("next") and earn["next"]["daysUntil"] <= 5)
        if earnings_soon:
            verdict = "WAIT"
            confidence = int(clamp(70 + abs(mom3) / 2, 70, 88))
        elif mom3 > 5 and mom12 > 0:
            verdict = "BUY"
            confidence = int(clamp(55 + mom3 / 2 + (win_all - 50), 55, 92))
        elif mom3 < -5 and mom12 < 0:
            verdict = "SELL"
            confidence = int(clamp(55 + abs(mom3) / 2 + (50 - win_all), 55, 92))
        else:
            verdict = "WAIT"
            confidence = int(clamp(48 + abs(mom3), 48, 74))

        tf_out[tf] = dict(
            sampleSize=int(len(r)), intradayDays=intraday_days,
            weekday=weekday, hour=hour_rows, month=month, matrix=matrix,
            recommendation=dict(verdict=verdict, confidence=confidence,
                                bestWindow=best, worstWindow=worst,
                                earningsSoon=earnings_soon),
        )

    if not tf_out:
        return None
    return dict(
        instrument=dict(id=s["id"], name=s["name"], ticker=s.get("sym") or t,
                        kind=s["kind"], currency=s["ccy"], country=s.get("country"),
                        slug=s.get("slug"), owners=s.get("owners") or 0),
        asOf=NOW.date().isoformat(),
        momentum=dict(m3=round(mom3, 1), m12=round(mom12, 1)),
        daysAxis=DAYS, hoursAxis=hours_axis, monthsAxis=MONTHS,
        earnings=earn, tf=tf_out,
    )


def qlabel(ts):
    m, y = ts.month, ts.year
    if m <= 3:
        return f"Q4 {y - 1}"
    return f"Q{(m - 1) // 3} {y}"


def earnings_for(s, px, dr):
    ds = earn_dates.get(s["yt"])
    if not ds or s["kind"] != "stock":
        return None
    base_vol = float(dr.tail(504).std() * 100)
    idx = px.index.tz_localize(None).normalize()
    pos_of = {}
    for d in ds:
        loc = idx.searchsorted(d)
        if 0 <= loc < len(idx) and abs((idx[loc] - d).days) <= 3:
            pos_of[d] = loc
    rels = list(range(-5, 6))
    by_rel = {r: [] for r in rels}
    drv = px.pct_change().values * 100
    for d, loc in pos_of.items():
        if d > NOW:
            continue
        for r in rels:
            j = loc + r
            if 1 <= j < len(drv) and np.isfinite(drv[j]) and abs(drv[j]) < 30:
                by_rel[r].append(drv[j])
    events = len([d for d in pos_of if d <= NOW])
    drift = None
    if events >= 3:
        drift = []
        for r in rels:
            vals = np.array(by_rel[r]) if by_rel[r] else np.array([0.0])
            drift.append(dict(rel=r, label="R" if r == 0 else (f"+{r}" if r > 0 else str(r)),
                              ret=float(round(vals.mean(), 2)),
                              vol=float(round(np.abs(vals).mean(), 2))))
    future = sorted([d for d in ds if d >= NOW])
    past = sorted([d for d in ds if d < NOW], reverse=True)
    nxt = None
    if future:
        nd = future[0]
        nxt = dict(label=qlabel(nd), date=nd.strftime("%d %b %Y"),
                   iso=nd.date().isoformat(), daysUntil=int((nd - NOW).days))
    calendar = [dict(label=qlabel(d), date=d.strftime("%d %b %Y"), iso=d.date().isoformat(),
                     daysUntil=int((d - NOW).days)) for d in (past[:4])]
    out = dict(kind="stock", next=nxt, calendar=calendar, baseVol=round(base_vol, 2))
    if drift:
        post = [d for d in drift if d["rel"] > 0]
        rday = next(d for d in drift if d["rel"] == 0)
        calm = min(post, key=lambda d: d["vol"])
        early = [d for d in drift if d["rel"] in (1, 2, 3)]
        mean_post = float(np.mean([d["ret"] for d in early]))
        out.update(drift=drift, reportDayMove=rday["vol"],
                   postElevatedSessions=len([d for d in post if d["vol"] > base_vol * 1.2]),
                   calmAfter=calm["rel"], surprise="up" if mean_post >= 0 else "down",
                   events=events)
    return out


# ---------------- 7. index analytics + reporting season ----------------
def index_analytics():
    s = dict(INDEX)
    a = analytics_for(dict(s, kind="index"))
    if a is None:
        return None
    # reporting season: share of tracked companies reporting per month
    months = np.zeros(12)
    for t, ds in earn_dates.items():
        for d in ds:
            if (NOW - d).days <= 740 and d <= NOW:
                months[d.month - 1] += 1
    if months.max() > 0:
        season = [dict(key=MONTHS[i], intensity=int(round(months[i] / months.max() * 100)))
                  for i in range(12)]
        peaks = [r["key"] for r in season if r["intensity"] >= 70]
        a["earnings"] = dict(kind="index", season=season, peakMonths=peaks)
    return a


log("computing analytics...")
adir = OUT / "a"
adir.mkdir(parents=True, exist_ok=True)
n_analytics = 0
analytics_ids = set()
for k, s in enumerate(universe):
    try:
        a = analytics_for(s)
    except Exception as e:
        log(f"  analytics failed {s['yt']}: {e}")
        continue
    if a is None:
        continue
    (adir / f"{s['id']}.json").write_text(json.dumps(a, ensure_ascii=False, allow_nan=False),
                                          encoding="utf-8")
    analytics_ids.add(s["id"])
    n_analytics += 1
    if k % 200 == 199:
        log(f"  ...{k + 1}/{len(universe)} ({n_analytics} written)")

idx_a = index_analytics()
if idx_a is None:
    raise SystemExit("index analytics failed")
(adir / "OSEBX.json").write_text(json.dumps(idx_a, ensure_ascii=False, allow_nan=False), encoding="utf-8")
log(f"  analytics written: {n_analytics} instruments + OSEBX index")

# ---------------- 8. catalog + shards ----------------
catalog = [dict(id="OSEBX", sym="OSEBX", name="Oslo Børs Benchmark", type="IDX",
                cat="Index · Oslo", ccy="NOK", px=None, chg=None, owners=10**9, hasA=1)]
for s in shares:
    catalog.append(dict(id=s["id"], sym=s["sym"], name=s["name"], type="EQ",
                        cat=f"Aksje · {s['exch']}", ccy=s["ccy"], px=s["px"], chg=s["chg"],
                        owners=s["owners"], isin=s["isin"],
                        hasA=1 if s["id"] in analytics_ids else 0))
for fd in funds:
    catalog.append(dict(id=fd["id"], sym=None, name=fd["name"], type="FND",
                        cat=fd["cat"], ccy=fd["ccy"], px=fd["px"], chg=fd["chg"],
                        owners=fd["owners"], isin=fd["isin"], hasA=0))
for e in etfs:
    catalog.append(dict(id=e["id"], sym=e["sym"], name=e["name"], type="ETF",
                        cat=f"ETF · {e['cat']}", ccy=e["ccy"], px=e["px"], chg=e["chg"],
                        owners=e["owners"], isin=e["isin"],
                        hasA=1 if e["id"] in analytics_ids else 0))

sdir = OUT / "s"
sdir.mkdir(parents=True, exist_ok=True)
shard_data = [dict() for _ in range(SHARDS)]
for s in shares:
    shard_data[s["id"] % SHARDS][str(s["id"])] = dict(
        id=s["id"], sym=s["sym"], name=s["name"], type="EQ", cat=f"Aksje · {s['exch']}",
        ccy=s["ccy"], isin=s["isin"], px=s["px"], chg=s["chg"], owners=s["owners"],
        slug=s["slug"], y=s["y"], ratios=dict(pe=s["pe"], pb=s["pb"], ps=s["ps"], div=s["div"]))
for fd in funds:
    shard_data[fd["id"] % SHARDS][str(fd["id"])] = dict(
        id=fd["id"], name=fd["name"], type="FND", cat=fd["cat"], ccy=fd["ccy"],
        isin=fd["isin"], px=fd["px"], chg=fd["chg"], owners=fd["owners"],
        slug=fd["slug"], y=fd["y"], fund=fd["fund"])
for e in etfs:
    shard_data[e["id"] % SHARDS][str(e["id"])] = dict(
        id=e["id"], sym=e["sym"], name=e["name"], type="ETF", cat=f"ETF · {e['cat']}",
        ccy=e["ccy"], isin=e["isin"], px=e["px"], chg=e["chg"], owners=e["owners"],
        slug=e["slug"], y=e["y"], fund=e["fund"])
for i, sh in enumerate(shard_data):
    (sdir / f"{i}.json").write_text(json.dumps(sh, ensure_ascii=False, allow_nan=False), encoding="utf-8")

(OUT / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, allow_nan=False), encoding="utf-8")
(OUT / "meta.json").write_text(json.dumps(dict(
    asOf=NOW.date().isoformat(),
    counts=dict(shares=len(shares), funds=len(funds), etfs=len(etfs),
                analytics=n_analytics + 1, withEarnings=len(earn_dates)),
), ensure_ascii=False), encoding="utf-8")

log(f"\nDONE: catalog {len(catalog)} · analytics {n_analytics + 1} · "
    f"shards {SHARDS} · earnings {len(earn_dates)}")
