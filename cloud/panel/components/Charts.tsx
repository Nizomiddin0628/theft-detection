"use client";
// Oddiy SVG grafiklar: soatlik chiziq va ustun. Kutubxonasiz; o'lcham konteynerga moslanadi (matn har doim 1:1).
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";

export interface Series {
  name: string;
  color: string;
  points: { x: number; y: number | null }[];
}

const H = 200;
const PAD = { l: 44, r: 12, t: 10, b: 26 };

export const PALETTE = ["#0e7c6b", "#e8a317", "#3b6fb6", "#c2410c", "#7a5ab8", "#5b6878"];

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(560);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((es) => {
      const v = Math.round(es[0].contentRect.width);
      if (v > 0) setW(v);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, w };
}

/** Chiroyli bo'linmalar: 1-2-2.5-5 qadam; butun sonlar uchun qadam ≥ 1 */
function ticks(max: number, integer: boolean): { top: number; step: number } {
  const raw = Math.max(max, integer ? 1 : 0.1) / 4;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / p;
  let step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  if (integer) step = Math.max(1, Math.ceil(step));
  return { top: step * Math.max(1, Math.ceil(max / step)), step };
}

function fmtTick(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function label(ts: number, spanH: number): string {
  const d = new Date(ts * 1000);
  if (spanH <= 30) return `${String(d.getHours()).padStart(2, "0")}:00`;
  return `${d.getDate()}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function stamp(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}, ${p(d.getHours())}:00`;
}

function scales(xs: number[], top: number, W: number) {
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const span = Math.max(3600, x1 - x0);
  const sx = (x: number) => PAD.l + ((x - x0) / span) * (W - PAD.l - PAD.r);
  const sy = (y: number) => PAD.t + (1 - y / top) * (H - PAD.t - PAD.b);
  const spanH = span / 3600;
  const room = Math.max(2, Math.floor((W - PAD.l - PAD.r) / 64)); // ~64 px ga bitta yorliq
  const cand = [1, 2, 3, 4, 6, 12, 24, 48, 72, 120, 168, 240];
  const every = cand.find((h) => spanH / h <= room) ?? 240;
  const xt: number[] = [];
  const offset = new Date().getTimezoneOffset() * 60;
  // mahalliy soat bo'yicha yaxlit nuqtalar
  const start = Math.ceil((x0 - offset) / (every * 3600)) * every * 3600 + offset;
  for (let t = start; t <= x1; t += every * 3600) xt.push(t);
  return { sx, sy, spanH, xt };
}

function Axes({ W, top, step, unit, sc }: { W: number; top: number; step: number; unit: string; sc: ReturnType<typeof scales> }) {
  const ys: number[] = [];
  for (let y = 0; y <= top + 1e-9; y += step) ys.push(y);
  return (
    <>
      <g className="grid">
        {ys.map((y) => (
          <line key={y} x1={PAD.l} x2={W - PAD.r} y1={sc.sy(y)} y2={sc.sy(y)} />
        ))}
      </g>
      <g className="axis">
        {ys.map((y) => (
          <text key={y} x={PAD.l - 8} y={sc.sy(y) + 4} textAnchor="end">
            {fmtTick(y)}
            {unit && y === top ? unit : ""}
          </text>
        ))}
        {sc.xt.map((t) => (
          <text key={t} x={sc.sx(t)} y={H - 6} textAnchor="middle">
            {label(t, sc.spanH)}
          </text>
        ))}
      </g>
    </>
  );
}

export function LineChart({
  series,
  unit = "",
  max,
  fmt = (v) => v.toFixed(1),
}: {
  series: Series[];
  unit?: string;
  max?: number;
  fmt?: (v: number) => string;
}) {
  const { ref, w: W } = useWidth();
  const xs = useMemo(() => {
    const all = series.flatMap((s) => s.points.map((p) => p.x));
    return all.length ? all : [Date.now() / 1000 - 3600, Date.now() / 1000];
  }, [series]);
  const peak = Math.max(0, ...series.flatMap((s) => s.points.map((p) => p.y ?? 0)));
  const { top, step } = max ? { top: max, step: max / 4 } : ticks(peak * 1.05, false);
  const sc = scales(xs, top, W);
  const uniq = useMemo(() => Array.from(new Set(xs)).sort((a, b) => a - b), [xs]);
  const [hover, setHover] = useState<number | null>(null);

  const move = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    let best = uniq[0];
    for (const x of uniq) if (Math.abs(sc.sx(x) - px) < Math.abs(sc.sx(best) - px)) best = x;
    setHover(best ?? null);
  };

  const path = (pts: Series["points"]) => {
    let d = "";
    let prev: number | null = null;
    for (const p of [...pts].sort((a, b) => a.x - b.x)) {
      if (p.y === null || p.y === undefined) {
        prev = null;
        continue;
      }
      const gap = prev === null || p.x - prev > 3600 * 1.5; // ma'lumot yo'q soatlarda chiziq uziladi
      d += `${gap ? "M" : "L"}${sc.sx(p.x).toFixed(1)},${sc.sy(Math.min(p.y, top)).toFixed(1)}`;
      prev = p.x;
    }
    return d;
  };

  return (
    <div ref={ref}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" onPointerMove={move} onPointerLeave={() => setHover(null)}>
        <Axes W={W} top={top} step={step} unit={unit} sc={sc} />
        {series.map((se) => (
          <path key={se.name} d={path(se.points)} fill="none" stroke={se.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {hover !== null ? (
          <g>
            <line x1={sc.sx(hover)} x2={sc.sx(hover)} y1={PAD.t} y2={H - PAD.b} stroke="currentColor" strokeOpacity={0.25} />
            {series.map((se) => {
              const p = se.points.find((q) => q.x === hover);
              return p && p.y !== null ? <circle key={se.name} cx={sc.sx(hover)} cy={sc.sy(Math.min(p.y, top))} r={3.5} fill={se.color} /> : null;
            })}
          </g>
        ) : null}
      </svg>
      <div className="legend">
        {hover !== null ? <span style={{ color: "var(--text)" }}>{stamp(hover)}</span> : null}
        {series.map((se) => {
          const p = hover !== null ? se.points.find((q) => q.x === hover) : null;
          return (
            <span key={se.name}>
              <i style={{ background: se.color }} />
              {se.name}
              {p && p.y !== null ? `: ${fmt(p.y)}${unit}` : ""}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function BarChart({ points, color = "#c2410c" }: { points: { x: number; y: number }[]; color?: string }) {
  const { ref, w: W } = useWidth();
  const xs = useMemo(() => (points.length ? points.map((p) => p.x) : [Date.now() / 1000 - 3600, Date.now() / 1000]), [points]);
  const { top, step } = ticks(Math.max(1, ...points.map((p) => p.y)), true);
  const sc = scales(xs, top, W);
  const hours = Math.max(1, sc.spanH);
  const bw = Math.max(2, Math.min(16, ((W - PAD.l - PAD.r) / hours) * 0.7));
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  return (
    <div ref={ref}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" onPointerLeave={() => setHover(null)}>
        <Axes W={W} top={top} step={step} unit="" sc={sc} />
        {points.map((p) => (
          <rect
            key={p.x}
            x={sc.sx(p.x) - bw / 2}
            y={sc.sy(p.y)}
            width={bw}
            height={Math.max(0, sc.sy(0) - sc.sy(p.y))}
            style={{ fill: color }}
            rx={1.5}
            opacity={hover && hover.x !== p.x ? 0.5 : 1}
            onPointerEnter={() => setHover(p)}
          />
        ))}
      </svg>
      <div className="legend">
        {hover ? (
          <span style={{ color: "var(--text)" }}>
            {stamp(hover.x)}: {hover.y} ta signal
          </span>
        ) : (
          <span>Ustun ustiga olib boring — aniq son ko&apos;rinadi</span>
        )}
      </div>
    </div>
  );
}
