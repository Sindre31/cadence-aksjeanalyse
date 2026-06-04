/* Cadence — main dashboard app */
import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  Heatmap, BarChart, ColumnChart, IntradayCurve, Gauge, StatChip, EarningsChart, fmtPct,
} from './charts.jsx';
import {
  loadMeta, loadCatalog, fetchAnalytics, fetchDetail, searchCatalog, buildView,
} from './datasource.js';

const TF_OPTS = ['1Y', '3Y', '5Y'];

/* ---- theme presets: the visual "directions" from the design -------------- */
const THEMES = {
  navy: {
    label: 'Navy',
    vars: {
      '--bg': '#0c1626', '--bg2': '#0a1320', '--surface': '#13233a', '--surface2': '#0f1d31',
      '--line': '#1f3354', '--text': '#eaf1fb', '--muted': '#8aa0c0', '--faint': '#5d7390',
      '--accent': '#3ddc97', '--accent-ink': '#06281b',
      '--pos': '#3ddc97', '--neg': '#ff6b7a',
      '--heat-neg': '#1b3a55', '--heat-mid': '#26405f', '--heat-pos': '#3ddc97',
      '--gauge-track': '#1f3354',
    },
  },
  terminal: {
    label: 'Terminal',
    vars: {
      '--bg': '#070a09', '--bg2': '#050706', '--surface': '#0e1311', '--surface2': '#0b100e',
      '--line': '#1c2723', '--text': '#e6f2ea', '--muted': '#7e9488', '--faint': '#506057',
      '--accent': '#16d672', '--accent-ink': '#03200f',
      '--pos': '#16d672', '--neg': '#ff4d4d',
      '--heat-neg': '#3a1414', '--heat-mid': '#16211b', '--heat-pos': '#16d672',
      '--gauge-track': '#1c2723',
    },
  },
  nordic: {
    label: 'Nordic',
    vars: {
      '--bg': '#eef1f5', '--bg2': '#e7ebf1', '--surface': '#ffffff', '--surface2': '#f6f8fb',
      '--line': '#dce3ec', '--text': '#15233a', '--muted': '#5d7390', '--faint': '#9aabc2',
      '--accent': '#1f8a5b', '--accent-ink': '#ffffff',
      '--pos': '#1f8a5b', '--neg': '#d64550',
      '--heat-neg': '#dfe9f1', '--heat-mid': '#eef3f7', '--heat-pos': '#1f8a5b',
      '--gauge-track': '#e2e8f0',
    },
  },
};
const THEME_ORDER = ['navy', 'terminal', 'nordic'];

function applyTheme(key) {
  const root = document.documentElement;
  const t = THEMES[key] || THEMES.navy;
  Object.entries(t.vars).forEach(([k, v]) => root.style.setProperty(k, v));
  root.setAttribute('data-theme', key);
}

function typeTag(c) {
  if (c.type === 'IDX') return <span className="tag index">INDEX</span>;
  if (c.type === 'FND') return <span className="tag fund">FUND</span>;
  if (c.type === 'ETF') return <span className="tag">{c.sym || 'ETF'}</span>;
  return <span className="tag">{c.sym}</span>;
}

/* ---- instrument picker (full Nordnet catalog) ----------------------------- */
function InstrumentPicker({ current, onChange }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [catalog, setCatalog] = useState(null);
  const boxRef = useRef(null);

  useEffect(() => { loadCatalog().then(setCatalog).catch(() => setCatalog([])); }, []);
  useEffect(() => {
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const results = useMemo(
    () => (catalog ? searchCatalog(catalog, q, 50) : []),
    [catalog, q],
  );

  return (
    <div className="picker" ref={boxRef}>
      <button className="picker-btn" onClick={() => setOpen(o => !o)}>
        {current
          ? <>
              {typeTag(current)}
              <span className="picker-name">{current.name}</span>
            </>
          : <span className="picker-name">Loading…</span>}
        <span className="picker-caret">▾</span>
      </button>
      {open && (
        <div className="picker-menu">
          <input autoFocus className="picker-search"
                 placeholder={catalog ? `Search ${catalog.length.toLocaleString('en-GB')} shares, funds & ETFs…` : 'Loading catalog…'}
                 value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="picker-list">
            {results.map(c => (
              <button key={c.id} className={'picker-item' + (current && c.id === current.id ? ' sel' : '')}
                      onClick={() => { onChange(c); setOpen(false); setQ(''); }}>
                {typeTag(c)}
                <span className="picker-iname">{c.name}</span>
                <span className="picker-meta">{c.type === 'IDX' ? 'Oslo Børs' : (c.owners || 0).toLocaleString('en-GB') + ' owners'}</span>
              </button>
            ))}
            {catalog && results.length === 0 && <div className="picker-empty">No match</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function VerdictPill({ verdict }) {
  const cls = verdict === 'BUY' ? 'buy' : verdict === 'SELL' ? 'sell' : 'wait';
  return <span className={'verdict ' + cls}>{verdict}</span>;
}

function Card({ title, hint, sub, children, span }) {
  return (
    <section className={'card' + (span ? ' span-' + span : '')}>
      <header className="card-head">
        <div>
          <h3>{title}</h3>
          {sub && <p className="card-sub">{sub}</p>}
        </div>
        {hint && <span className="card-hint">{hint}</span>}
      </header>
      <div className="card-body">{children}</div>
    </section>
  );
}

/* ---- earnings / quarterly report section ---------------------------------- */
function EarningsSection({ view }) {
  const e = view.earnings;
  if (!e) return null;

  if (e.kind === 'index') {
    if (!e.season) return null;
    const bestM = e.season.reduce((a, b) => (b.intensity > a.intensity ? b : a));
    return (
      <Card title="Reporting season" sub="Relative share of tracked companies reporting, by month" span={12}
            hint="market-wide earnings load">
        <div className="earn-index">
          <div className="earn-index-chart">
            <ColumnChart rows={e.season.map(s => ({ key: s.key, intensity: s.intensity }))}
                         metric="intensity" highlightKey={bestM.key} max={100}
                         tipOf={(r) => <span><b>{r.key}</b><br />reporting load {r.intensity}/100</span>} />
          </div>
          <div className="earn-index-note">
            <span className="earn-tag">peak months</span>
            <div className="earn-peaks">{e.peakMonths.map(m => <span key={m} className="earn-peak">{m}</span>)}</div>
            <p>Volatility broadens across the index when many companies report at once. Heaviest load lands in {e.peakMonths.join(', ')} — size positions and time entries with that in mind.</p>
          </div>
        </div>
      </Card>
    );
  }

  if (!e.drift) return null;
  const verdictClose = e.next && e.next.daysUntil <= 5;
  return (
    <Card title="Around earnings" sub={`Volatility & drift in sessions around ${view.instrument.ticker} quarterly reports (last ${e.events} reports)`} span={12}
          hint="R = report day">
      <div className="earn-layout">
        <div className="earn-main">
          <EarningsChart drift={e.drift} baseVol={e.baseVol} />
          <div className="earn-legend">
            <span><i className="sw report"></i> report day</span>
            <span><i className="sw elevated"></i> elevated after</span>
            <span><i className="sw base"></i> before / normal</span>
          </div>
        </div>
        <div className="earn-side">
          <div className={'earn-next' + (verdictClose ? ' soon' : '')}>
            <span className="earn-next-lbl">Next report</span>
            <span className="earn-next-q">{e.next ? e.next.label : '—'}</span>
            <span className="earn-next-date">{e.next ? e.next.date : 'not announced'}</span>
            <span className="earn-next-cd">{e.next ? `in ${e.next.daysUntil} days` : ''}</span>
          </div>
          <div className="earn-facts">
            <div className="earn-fact"><span>Report-day move</span><b>±{e.reportDayMove}%</b></div>
            <div className="earn-fact"><span>Elevated vol</span><b>~{e.postElevatedSessions} sessions</b></div>
            <div className="earn-fact"><span>Typical drift</span><b className={e.surprise === 'up' ? 'up' : 'down'}>{e.surprise === 'up' ? 'higher ↑' : 'lower ↓'}</b></div>
            <div className="earn-fact"><span>Calmest after</span><b>+{e.calmAfter} sessions</b></div>
          </div>
          <div className="earn-cal">
            <span className="earn-tag">recent reports</span>
            {(e.calendar || []).slice(0, 3).map(c => (
              <div key={c.iso} className="earn-cal-row"><span>{c.label}</span><span className="muted">{c.date}</span></div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

/* ---- summary view: funds + instruments outside the analytics universe ----- */
const Y_LABELS = [
  ['yield_1w', '1W'], ['yield_1m', '1M'], ['yield_3m', '3M'], ['yield_ytd', 'YTD'],
  ['yield_1y', '1Y'], ['yield_3y', '3Y'], ['yield_5y', '5Y'], ['yield_10y', '10Y'],
];

function SummaryView({ detail }) {
  const isFund = detail.type === 'FND';
  const rows = Y_LABELS
    .filter(([k]) => typeof detail.y?.[k] === 'number')
    .map(([k, label]) => ({ key: label, ret: detail.y[k] }));
  const fund = detail.fund || {};
  return (
    <main className="grid">
      <section className="card span-12">
        <div className="sum-head">
          <span className="sum-name">{detail.name}</span>
          {detail.px != null && <span className="sum-px">{detail.px.toLocaleString('en-GB')} {detail.ccy}</span>}
          {detail.chg != null && (
            <span className={'sum-chg ' + (detail.chg >= 0 ? 'up' : 'down')}>{fmtPct(detail.chg, true)} today</span>
          )}
        </div>
        <p className="sum-meta">
          {detail.cat} · {(detail.owners || 0).toLocaleString('en-GB')} owners on Nordnet
          {detail.isin ? ` · ${detail.isin}` : ''}
        </p>
      </section>

      <section className="card span-12">
        <div className="notice">
          <span>
            {isFund
              ? <><b>Funds price once a day at NAV</b> — weekday/hour timing doesn't apply. Order cut-off and seasonality still matter; the return summary below comes straight from Nordnet.</>
              : <><b>No timing analytics for this instrument yet.</b> Intraday timing analytics cover the OSEBX index, all Oslo Børs shares and the most-owned foreign shares and ETFs on Nordnet. Nordnet's return summary is shown below.</>}
          </span>
        </div>
      </section>

      {rows.length > 0 && (
        <Card title="Return summary" sub="Total return per period (Nordnet)" span={isFund ? 8 : 12} hint={`in ${detail.ccy}`}>
          <BarChart rows={rows} metric="ret"
                    highlightKey={rows.reduce((a, b) => (b.ret > a.ret ? b : a)).key}
                    tipOf={(r) => <span><b>{r.key}</b><br />{fmtPct(r.ret, true)}</span>} />
        </Card>
      )}

      {isFund && (
        <Card title="Fund facts" sub={fund.admin || ''} span={4}>
          <div className="earn-facts">
            {fund.ms != null && <div className="earn-fact"><span>Morningstar</span><b className="stars">{'★'.repeat(fund.ms)}{'☆'.repeat(Math.max(0, 5 - fund.ms))}</b></div>}
            {fund.fee != null && <div className="earn-fact"><span>Yearly fee</span><b>{fund.fee}%</b></div>}
            {fund.risk != null && <div className="earn-fact"><span>Risk (KIID)</span><b>{fund.risk}/7</b></div>}
            {fund.aum != null && fund.aum > 0 && <div className="earn-fact"><span>AUM</span><b>{fund.aum} bn</b></div>}
            {fund.sfdr != null && <div className="earn-fact"><span>SFDR</span><b>{typeof fund.sfdr === 'number' ? `Art. ${fund.sfdr}` : String(fund.sfdr)}</b></div>}
            {fund.minInv != null && <div className="earn-fact"><span>Min. invest</span><b>{Number(fund.minInv).toLocaleString('en-GB')}</b></div>}
          </div>
        </Card>
      )}

      {!isFund && detail.ratios && (detail.ratios.pe || detail.ratios.pb || detail.ratios.div) ? (
        <Card title="Key ratios" sub="From Nordnet" span={4}>
          <div className="earn-facts">
            {detail.ratios.pe != null && <div className="earn-fact"><span>P/E</span><b>{detail.ratios.pe}</b></div>}
            {detail.ratios.pb != null && <div className="earn-fact"><span>P/B</span><b>{detail.ratios.pb}</b></div>}
            {detail.ratios.ps != null && <div className="earn-fact"><span>P/S</span><b>{detail.ratios.ps}</b></div>}
            {detail.ratios.div != null && <div className="earn-fact"><span>Dividend yield</span><b>{detail.ratios.div}%</b></div>}
          </div>
        </Card>
      ) : null}
    </main>
  );
}

/* ---- the dashboard --------------------------------------------------------- */
function urlState() {
  const p = new URLSearchParams(window.location.search);
  return {
    id: p.get('id') || 'OSEBX',
    tf: TF_OPTS.includes(p.get('tf')) ? p.get('tf') : '3Y',
  };
}

export default function App() {
  const init = useMemo(urlState, []);
  const [theme, setTheme] = useState(() => localStorage.getItem('cadence-theme') || 'navy');
  const [sel, setSel] = useState({ id: init.id, name: null, type: null });
  const [tf, setTf] = useState(init.tf);
  const [data, setData] = useState(null);       // analytics json
  const [detail, setDetail] = useState(null);   // shard detail (fallback)
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [meta, setMeta] = useState(null);

  useEffect(() => { loadMeta().then(setMeta); }, []);

  useEffect(() => { applyTheme(theme); localStorage.setItem('cadence-theme', theme); }, [theme]);

  // resolve picker label for deep links
  useEffect(() => {
    if (sel.name) return;
    loadCatalog().then(cat => {
      const c = cat.find(x => String(x.id) === String(sel.id));
      if (c) setSel(c);
    }).catch(() => {});
  }, [sel]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const a = await fetchAnalytics(sel.id);
        if (!alive) return;
        if (a) { setData(a); setDetail(null); }
        else {
          const d = await fetchDetail(sel.id);
          if (!alive) return;
          setData(null);
          setDetail(d);
          if (!d) setError('Instrument not found in the dataset.');
        }
      } catch (e) {
        if (alive) setError(String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [sel.id]);

  // keep URL shareable
  useEffect(() => {
    const p = new URLSearchParams();
    if (sel.id !== 'OSEBX') p.set('id', sel.id);
    if (tf !== '3Y') p.set('tf', tf);
    const qs = p.toString();
    window.history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
  }, [sel.id, tf]);

  const view = useMemo(() => (data ? buildView(data, tf) : null), [data, tf]);
  const rec = view && view.recommendation;
  const asOfIso = (view && view.asOf) || (meta && meta.asOf);
  const asOf = asOfIso && new Date(asOfIso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });

  return (
    <div className={'app' + (loading ? ' loading' : '')}>
      {/* ---- top bar ---- */}
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"></span>
          <span className="brand-name">Cadence</span>
          <span className="brand-tag">timing analytics</span>
        </div>
        <div className="topbar-mid">
          <InstrumentPicker
            current={sel.name ? sel : null}
            onChange={(c) => setSel(c)}
          />
          <div className="tf-seg">
            {TF_OPTS.map(o => (
              <button key={o} className={'tf-btn' + (o === tf ? ' on' : '')} onClick={() => setTf(o)}>{o}</button>
            ))}
          </div>
        </div>
        <div className="topbar-right">
          <span className="live-dot"></span>
          <span className="asof">data as of {asOf || '—'}</span>
          <button className="theme-btn" title="Switch theme"
                  onClick={() => setTheme(THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length])}>
            ◐ {THEMES[theme].label}
          </button>
        </div>
      </header>

      {error && !view && !detail && <p className="err">{error}</p>}
      {!view && detail && <SummaryView detail={detail} />}

      {view && (
        <main className="grid">
          {/* ---- recommendation hero ---- */}
          <section className="hero span-12">
            <div className="hero-verdict">
              <div className="hero-vtop">
                <VerdictPill verdict={rec.verdict} />
                <span className="hero-headline">{rec.headline}</span>
              </div>
              <div className="hero-window">
                <div className="hw-item">
                  <span className="hw-lbl">Best window</span>
                  <span className="hw-val">
                    {rec.bestWindow.day}{rec.bestWindow.hour != null ? ` · ${rec.bestWindow.hour}:00–${+rec.bestWindow.hour + 1}:00` : ''}
                  </span>
                </div>
                <div className="hw-sep"></div>
                <div className="hw-item">
                  <span className="hw-lbl">Avoid</span>
                  <span className="hw-val muted">
                    {rec.worstWindow.day}{rec.worstWindow.hour != null ? ` · ${rec.worstWindow.hour}:00` : ''}
                  </span>
                </div>
                <div className="hw-sep"></div>
                <div className="hw-item">
                  <span className="hw-lbl">3-month momentum</span>
                  <span className={'hw-val' + (view.momentum.m3 >= 0 ? '' : ' muted')}>{fmtPct(view.momentum.m3, true)}</span>
                </div>
              </div>
              <ul className="hero-rationale">
                {rec.rationale.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
            <div className="hero-gauge">
              <Gauge value={rec.confidence} verdict={rec.verdict} />
              <div className="hero-stats">
                <StatChip label="Best weekday" value={view.bestDay.key} sub={`edge ${view.bestDay.edge}`} />
                {view.bestHour
                  ? <StatChip label="Best hour" value={view.bestHour.key + ':00'} sub={`edge ${view.bestHour.edge}`} />
                  : <StatChip label="Best month" value={view.bestMonth.key} sub={`edge ${view.bestMonth.edge}`} />}
                <StatChip label="Best month" value={view.bestMonth.key} sub={`edge ${view.bestMonth.edge}`} />
                <StatChip label="Sessions" value={view.sampleSize.toLocaleString('en-GB')} sub={`${tf} sample`} />
              </div>
            </div>
          </section>

          {/* ---- heatmap centrepiece ---- */}
          {view.matrix && (
            <Card title="When to trade" sub={`Edge score by weekday × hour (exchange local time) — intraday sample: ${view.intradayDays} sessions`}
                  hint="0 = weak · 100 = strong" span={12}>
              <Heatmap matrix={view.matrix} daysAxis={view.daysAxis} hoursAxis={view.hoursAxis} />
            </Card>
          )}

          {/* ---- earnings / quarterly reports ---- */}
          <EarningsSection view={view} />

          {/* ---- weekday ---- */}
          <Card title="Best day of week" sub="Edge score by weekday" span={4}>
            <BarChart rows={view.weekday} metric="edge" highlightKey={view.bestDay.key} max={100} />
          </Card>

          {/* ---- intraday ---- */}
          {view.hour && (
            <Card title="Time of day" sub="Edge across the trading session" span={4}>
              <IntradayCurve hours={view.hour} metric="edge" />
            </Card>
          )}

          {/* ---- month ---- */}
          <Card title="Seasonality" sub="Edge score by month" span={4}>
            <ColumnChart rows={view.month} metric="edge" highlightKey={view.bestMonth.key} max={100} />
          </Card>

          {/* ---- return per period ---- */}
          <Card title="Average return" sub="Mean session return by weekday" span={4}>
            <BarChart rows={view.weekday} metric="ret" highlightKey={view.bestDay.key} />
          </Card>

          {/* ---- volume / liquidity ---- */}
          {view.hour && (
            <Card title="Liquidity" sub="Relative volume by hour" span={4}>
              <BarChart rows={view.hour} metric="volume" unit="" highlightKey={view.bestHour && view.bestHour.key} max={100} />
            </Card>
          )}

          {/* ---- volatility ---- */}
          <Card title="Volatility" sub="Average session range by month (%)" span={4}>
            <ColumnChart rows={view.month} metric="vol" unit="%" highlightKey={null} />
          </Card>

          <p className="disclaimer span-12">
            Edge scores blend each period's historical average return, win-rate and liquidity, minus a volatility
            penalty — relative within this instrument. Data: Nordnet (catalog, owners, fund facts) and Yahoo Finance
            (daily & hourly history, earnings dates). Patterns describe the past and are not a forecast — past
            performance does not guarantee future results. Not investment advice.
          </p>
        </main>
      )}

      {!view && !detail && !error && (
        <div className="empty-state">
          <h2>Loading analytics…</h2>
        </div>
      )}
    </div>
  );
}
