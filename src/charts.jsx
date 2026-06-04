/* Cadence — chart components. Theme-aware via CSS variables. */
import React, { useState } from 'react';

/* ---- shared helpers ------------------------------------------------------ */
export function fmtPct(v, withSign) {
  return (v >= 0 && withSign ? '+' : '') + v.toFixed(2) + '%';
}
// map an edge score 0..100 to a colour on the palette ramp (set in CSS vars)
export function edgeColor(e) {
  const t = e / 100;
  if (t < 0.5) {
    const k = t / 0.5;
    return `color-mix(in oklab, var(--heat-neg) ${Math.round((1 - k) * 100)}%, var(--heat-mid))`;
  }
  const k = (t - 0.5) / 0.5;
  return `color-mix(in oklab, var(--heat-mid) ${Math.round((1 - k) * 100)}%, var(--heat-pos))`;
}

/* ---- Tooltip ------------------------------------------------------------- */
function useTip() {
  const [tip, setTip] = useState(null);
  const show = (e, content) => {
    const r = e.currentTarget.closest('[data-tipwrap]').getBoundingClientRect();
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, content });
  };
  const hide = () => setTip(null);
  const node = tip ? (
    <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>
  ) : null;
  return { show, hide, node };
}

/* ---- Day × Hour heatmap (signature viz) ----------------------------------
   Cells are {e, ret, n, t}: edge score plus signal stats. Cells whose mean
   return is statistically indistinguishable from noise (|t| < 1) are dimmed.
   `now` ({di,hi}) outlines the current trading window; `report` ({di,label})
   flags the weekday an upcoming quarterly report lands on. */
export function Heatmap({ matrix, daysAxis, hoursAxis, now, report }) {
  const tip = useTip();
  return (
    <div className="heat" data-tipwrap>
      <div className="heat-grid" style={{ gridTemplateColumns: `46px repeat(${hoursAxis.length}, 1fr)` }}>
        <div className="heat-corner"></div>
        {hoursAxis.map(h => <div key={h} className="heat-colhead">{h}</div>)}
        {matrix.map((row, di) => (
          <React.Fragment key={di}>
            <div className="heat-rowhead">
              {report && report.di === di && <span className="heat-flag" title={report.label}>●</span>}
              {daysAxis[di]}
            </div>
            {row.map((c, hi) => {
              const weak = Math.abs(c.t) < 1;
              const isNow = now && now.di === di && now.hi === hi;
              return (
                <div
                  key={hi}
                  className={'heat-cell' + (weak ? ' weak' : '') + (isNow ? ' now' : '')}
                  style={{ background: edgeColor(c.e) }}
                  onMouseMove={(ev) => tip.show(ev, (
                    <span>
                      <b>{daysAxis[di]} {hoursAxis[hi]}:00{isNow ? ' — now' : ''}</b><br />
                      Edge score {c.e}/100 · avg {fmtPct(c.ret, true)}<br />
                      {c.n} bars · {weak ? 'weak signal' : 'solid signal'} (t={c.t})
                    </span>
                  ))}
                  onMouseLeave={tip.hide}
                >
                  <span className="heat-val">{c.e}</span>
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      {tip.node}
      <div className="heat-legend">
        <span>Lower edge</span>
        <div className="heat-ramp"></div>
        <span>Higher edge</span>
        <span className="heat-legend-note">· dimmed = weak signal (|t|&lt;1)</span>
        {now && <span className="heat-legend-note">· outlined = now</span>}
        {report && <span className="heat-legend-note heat-flag-note">● {report.label}</span>}
      </div>
    </div>
  );
}

/* ---- Post-earnings drift curve (cumulative avg return after the print) --- */
export function PeadCurve({ pead }) {
  const tip = useTip();
  const W = 520, H = 110, padX = 10, padY = 14;
  const vals = pead.map(p => p.cum);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = (hi - lo) || 1;
  const x = i => padX + (i / (pead.length - 1)) * (W - padX * 2);
  const y = v => padY + (1 - (v - lo) / span) * (H - padY * 2);
  const pts = pead.map((p, i) => [x(i), y(p.cum)]);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const zero = y(0);
  const endPos = vals[vals.length - 1] >= 0;
  return (
    <div className="curve" data-tipwrap>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="curve-svg" style={{ height: 110 }}>
        <line x1={padX} x2={W - padX} y1={zero} y2={zero}
              stroke="color-mix(in oklab, var(--text) 25%, transparent)" strokeDasharray="4 4" />
        <path d={line} fill="none" stroke={endPos ? 'var(--pos)' : 'var(--neg)'} strokeWidth="2.5"
              vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        {pts.map((p, i) => (
          <circle key={i} cx={p[0]} cy={p[1]} r="9" fill="transparent"
                  onMouseMove={(e) => tip.show(e, (
                    <span><b>+{pead[i].rel} sessions</b><br />cumulative {fmtPct(pead[i].cum, true)}</span>
                  ))}
                  onMouseLeave={tip.hide} />
        ))}
        {pts.map((p, i) => <circle key={'d' + i} cx={p[0]} cy={p[1]} r="2.5" fill={endPos ? 'var(--pos)' : 'var(--neg)'} />)}
      </svg>
      <div className="curve-axis">
        {pead.map(p => <span key={p.rel}>+{p.rel}</span>)}
      </div>
      {tip.node}
    </div>
  );
}

/* ---- Horizontal bar chart (weekday / hour) ------------------------------- */
export function BarChart({ rows, metric, unit, highlightKey, max, tipOf }) {
  const tip = useTip();
  const vals = rows.map(r => r[metric]);
  const lo = Math.min(0, ...vals);
  const hi = max != null ? max : Math.max(...vals);
  const span = (hi - lo) || 1;
  return (
    <div className="bars" data-tipwrap>
      {rows.map((r) => {
        const v = r[metric];
        const pct = ((v - lo) / span) * 100;
        const isNeg = v < 0;
        const isHi = r.key === highlightKey;
        return (
          <div className="bar-row" key={r.key}
               onMouseMove={(e) => tip.show(e, tipOf ? tipOf(r) : (
                 <span><b>{r.key}</b><br />
                   edge {r.edge} · ret {fmtPct(r.ret, true)}<br />
                   win {r.winRate}% · vol {r.vol}%{r.n != null ? <><br />n={r.n} sessions</> : null}</span>
               ))}
               onMouseLeave={tip.hide}>
            <div className="bar-key">{r.key}</div>
            <div className="bar-track">
              <div className={'bar-fill' + (isNeg ? ' neg' : '') + (isHi ? ' hi' : '')}
                   style={{ width: Math.max(2, pct) + '%' }}></div>
            </div>
            <div className="bar-val">{metric === 'ret' ? fmtPct(v, true) : v + (unit || '')}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ---- Intraday curve (SVG) ------------------------------------------------ */
export function IntradayCurve({ hours, metric }) {
  const tip = useTip();
  const W = 520, H = 150, padX = 8, padY = 18;
  const vals = hours.map(h => h[metric]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = (hi - lo) || 1;
  const x = i => padX + (hours.length > 1 ? (i / (hours.length - 1)) : 0.5) * (W - padX * 2);
  const y = v => padY + (1 - (v - lo) / span) * (H - padY * 2);
  const pts = hours.map((h, i) => [x(i), y(h[metric])]);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = line + ` L ${x(hours.length - 1).toFixed(1)} ${H - padY} L ${padX} ${H - padY} Z`;
  return (
    <div className="curve" data-tipwrap>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="curve-svg">
        <defs>
          <linearGradient id="curveFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#curveFill)" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2.5"
              vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        {pts.map((p, i) => (
          <circle key={i} cx={p[0]} cy={p[1]} r="9" fill="transparent"
                  onMouseMove={(e) => tip.show(e, (
                    <span><b>{hours[i].key}:00</b><br />
                      edge {hours[i].edge} · ret {fmtPct(hours[i].ret, true)}<br />
                      liquidity {hours[i].volume}</span>
                  ))}
                  onMouseLeave={tip.hide} />
        ))}
        {pts.map((p, i) => <circle key={'d' + i} cx={p[0]} cy={p[1]} r="3" fill="var(--accent)" />)}
      </svg>
      <div className="curve-axis">
        {hours.map(h => <span key={h.key}>{h.key}</span>)}
      </div>
      {tip.node}
    </div>
  );
}

/* ---- Confidence gauge (semi donut) --------------------------------------- */
export function Gauge({ value, verdict }) {
  const R = 52, C = Math.PI * R;
  const frac = value / 100;
  const cls = verdict === 'BUY' ? 'buy' : verdict === 'SELL' ? 'sell' : 'wait';
  return (
    <div className={'gauge ' + cls}>
      <svg viewBox="0 0 130 78">
        <path d="M 13 70 A 52 52 0 0 1 117 70" fill="none" stroke="var(--gauge-track)" strokeWidth="10" strokeLinecap="round" />
        <path d="M 13 70 A 52 52 0 0 1 117 70" fill="none" stroke="currentColor" strokeWidth="10"
              strokeLinecap="round" strokeDasharray={`${(frac * C).toFixed(1)} ${C.toFixed(1)}`} />
      </svg>
      <div className="gauge-num">{value}<span>%</span></div>
      <div className="gauge-lbl">confidence</div>
    </div>
  );
}

/* ---- Mini stat chips ------------------------------------------------------ */
export function StatChip({ label, value, sub, tone }) {
  return (
    <div className={'statchip' + (tone ? ' ' + tone : '')}>
      <div className="statchip-lbl">{label}</div>
      <div className="statchip-val">{value}</div>
      {sub && <div className="statchip-sub">{sub}</div>}
    </div>
  );
}

/* ---- Vertical column chart (monthly: seasonality / volatility) ----------- */
export function ColumnChart({ rows, metric, unit, highlightKey, max, tipOf }) {
  const tip = useTip();
  const vals = rows.map(r => r[metric]);
  const lo = Math.min(0, ...vals);
  const hi = max != null ? max : Math.max(...vals);
  const span = (hi - lo) || 1;
  return (
    <div className="cols" data-tipwrap>
      <div className="cols-row">
        {rows.map((r) => {
          const v = r[metric];
          const pct = ((v - lo) / span) * 100;
          const isNeg = v < 0;
          const isHi = r.key === highlightKey;
          return (
            <div className="col" key={r.key}
                 onMouseMove={(e) => tip.show(e, tipOf ? tipOf(r) : (
                   <span><b>{r.key}</b><br />
                     edge {r.edge} · ret {fmtPct(r.ret, true)}<br />
                     vol {r.vol}% · liq {r.volume}{r.n != null ? <> · n={r.n}</> : null}</span>
                 ))}
                 onMouseLeave={tip.hide}>
              <div className="col-val">{metric === 'ret' ? fmtPct(v, true) : (Math.round(v * 100) / 100) + (unit || '')}</div>
              <div className="col-track">
                <div className={'col-fill' + (isNeg ? ' neg' : '') + (isHi ? ' hi' : '')}
                     style={{ height: Math.max(3, pct) + '%' }}></div>
              </div>
              <div className="col-key">{r.key[0]}</div>
            </div>
          );
        })}
      </div>
      {tip.node}
    </div>
  );
}

/* ---- Earnings drift chart (relative days around report) ------------------ */
export function EarningsChart({ drift, baseVol }) {
  const tip = useTip();
  const hi = Math.max(...drift.map(d => d.vol)) * 1.08;
  return (
    <div className="earn-chart" data-tipwrap>
      <div className="earn-row">
        <div className="earn-baseline" style={{ bottom: (22 + (baseVol / hi) * 148) + 'px' }}>
          <span className="earn-baseline-lbl">typical</span>
        </div>
        {drift.map((d) => {
          const pct = (d.vol / hi) * 100;
          const isReport = d.rel === 0;
          const elevated = d.rel > 0 && d.vol > baseVol * 1.2;
          return (
            <div className={'ecol' + (isReport ? ' report' : '') + (elevated ? ' elevated' : '')} key={d.rel}
                 onMouseMove={(e) => tip.show(e, (
                   <span><b>{isReport ? 'Report day' : (d.rel > 0 ? d.rel + ' sessions after' : Math.abs(d.rel) + ' sessions before')}</b><br />
                     typical move ±{d.vol}%<br />avg move {fmtPct(d.ret, true)}</span>
                 ))}
                 onMouseLeave={tip.hide}>
              <div className="ecol-track">
                <div className="ecol-fill" style={{ height: Math.max(4, pct) + '%' }}></div>
              </div>
              <div className="ecol-key">{d.label}</div>
            </div>
          );
        })}
      </div>
      {tip.node}
    </div>
  );
}
