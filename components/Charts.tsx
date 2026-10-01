"use client";

import { useState } from "react";
import { type Week, rupees } from "@/lib/api";

const W = 640;
const H = 220;
const PAD = { top: 16, right: 12, bottom: 28, left: 64 };

const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

function ticks(max: number, n = 4) {
  if (max <= 0) return [0];
  const raw = max / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step);
}

function short(paise: number) {
  const r = paise / 100;
  if (r >= 1e7) return `₹${(r / 1e7).toFixed(1)}Cr`;
  if (r >= 1e5) return `₹${(r / 1e5).toFixed(1)}L`;
  if (r >= 1e3) return `₹${(r / 1e3).toFixed(0)}K`;
  return `₹${r.toFixed(0)}`;
}

function Tip({ x, y, children }: { x: number; y: number; children: React.ReactNode }) {
  const left = Math.min(Math.max((x / W) * 100, 14), 86);
  return (
    <div className="ctip" style={{ left: `${left}%`, top: `${(y / H) * 100}%` }}>
      {children}
    </div>
  );
}

export function WeeklyActionable({ weeks }: { weeks: Week[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const chase = weeks.map((w) => Math.max(w.totals.chase ?? 0, 0));
  const tax = weeks.map((w) => Math.max(w.totals.tax ?? 0, 0));
  const scale = ticks(Math.max(...chase.map((c, i) => c + tax[i]), 1));
  const top = scale[scale.length - 1] || 1;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / Math.max(weeks.length, 1);
  const bar = Math.min(28, slot * 0.62);
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;

  return (
    <div className="chart">
      <div className="legend">
        <span><i className="sw sw-chase" />To chase</span>
        <span><i className="sw sw-tax" />To claim on tax</span>
      </div>
      <div className="chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Money you can act on, week by week">
          {scale.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={PAD.left - 8} y={y(t) + 4} className="axis" textAnchor="end">{short(t)}</text>
            </g>
          ))}
          {weeks.map((w, i) => {
            const cx = PAD.left + slot * i + slot / 2;
            const hc = (chase[i] / top) * innerH;
            const ht = (tax[i] / top) * innerH;
            const base = PAD.top + innerH;
            return (
              <g key={w.start} opacity={hover === null || hover === i ? 1 : 0.45}>
                {ht > 0 && <rect x={cx - bar / 2} y={base - ht} width={bar} height={Math.max(ht - 1, 1)} rx={2} className="bar-tax grow-y" />}
                {hc > 0 && <rect x={cx - bar / 2} y={base - ht - hc} width={bar} height={Math.max(hc - 2, 1)} rx={3} className="bar-chase grow-y" />}
                {i % Math.ceil(weeks.length / 7) === 0 && (
                  <text x={cx} y={H - 8} className="axis" textAnchor="middle">{day(w.start)}</text>
                )}
                <rect x={cx - slot / 2} y={PAD.top} width={slot} height={innerH} fill="transparent"
                  onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
              </g>
            );
          })}
          <line x1={PAD.left} x2={W - PAD.right} y1={PAD.top + innerH} y2={PAD.top + innerH} className="base" />
        </svg>
        {hover !== null && (
          <Tip x={PAD.left + slot * hover + slot / 2} y={y(chase[hover] + tax[hover])}>
            <b>Week of {day(weeks[hover].start)}</b>
            <span>To chase <em className="mono">{rupees(chase[hover])}</em></span>
            <span>To claim <em className="mono">{rupees(tax[hover])}</em></span>
          </Tip>
        )}
      </div>
    </div>
  );
}

export function FeeRate({ weeks, hikeStarted }: { weeks: Week[]; hikeStarted: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const live = weeks.filter((w) => w.gross_paise > 0);
  if (live.length < 2) return null;
  const vals = live.flatMap((w) => [w.fee_rate, w.contract_rate]);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = Math.max(hi - lo, 0.0005);
  const min = lo - span * 0.35;
  const max = hi + span * 0.35;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (live.length === 1 ? innerW / 2 : (i / (live.length - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - ((v - min) / (max - min)) * innerH;
  const path = (key: "fee_rate" | "contract_rate") =>
    live.map((w, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(w[key]).toFixed(1)}`).join(" ");
  const hike = live.findIndex((w) => w.start === hikeStarted);
  const grid = [min + (max - min) * 0.1, (min + max) / 2, max - (max - min) * 0.1];

  return (
    <div className="chart">
      <div className="legend">
        <span><i className="sw sw-line" />Fee actually billed</span>
        <span><i className="sw sw-dash" />What your contract says</span>
      </div>
      <div className="chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Fee rate billed against contract, week by week">
          {grid.map((g) => (
            <g key={g}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(g)} y2={y(g)} className="grid" />
              <text x={PAD.left - 8} y={y(g) + 4} className="axis" textAnchor="end">{(g * 100).toFixed(2)}%</text>
            </g>
          ))}
          {hike >= 0 && (
            <g>
              <line x1={x(hike)} x2={x(hike)} y1={PAD.top} y2={PAD.top + innerH} className="marker" />
              <text x={x(hike) + 6} y={PAD.top + 12} className="marker-label">Hike starts</text>
            </g>
          )}
          <path d={path("contract_rate")} className="line-dash" />
          <path d={path("fee_rate")} className="line-main draw" />
          {live.map((w, i) => (
            <g key={w.start}>
              {(hover === i || i === hike) && <circle cx={x(i)} cy={y(w.fee_rate)} r={5} className="dot" />}
              {i % Math.ceil(live.length / 7) === 0 && (
                <text x={x(i)} y={H - 8} className="axis" textAnchor="middle">{day(w.start)}</text>
              )}
              <rect x={x(i) - innerW / live.length / 2} y={PAD.top} width={innerW / live.length} height={innerH}
                fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
            </g>
          ))}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} className="cross" />}
        </svg>
        {hover !== null && (
          <Tip x={x(hover)} y={y(live[hover].fee_rate)}>
            <b>Week of {day(live[hover].start)}</b>
            <span>Billed <em className="mono">{(live[hover].fee_rate * 100).toFixed(3)}%</em></span>
            <span>Contract <em className="mono">{(live[hover].contract_rate * 100).toFixed(3)}%</em></span>
            {live[hover].overcharge_paise > 0 && <span>Overcharged <em className="mono">{rupees(live[hover].overcharge_paise)}</em></span>}
          </Tip>
        )}
      </div>
    </div>
  );
}
