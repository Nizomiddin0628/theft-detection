"use client";

// 2.1.0 Oqim: filial rejasida bo'lim tashriflari, bo'limdan bo'limga yo'nalishlar va heatmap (bandlik / o'tganlar)
import { useEffect, useMemo, useRef, useState } from "react";

import { BarChart } from "@/components/Charts";
import { api, type Flow, type FlowHeat, type FlowPlan } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import { useData, useInterval } from "@/lib/useData";

const PERIODS = [1, 7, 30];
const pad = (n: number) => String(n).padStart(2, "0");
const STOPS: [number, number[]][] = [
  [0, [37, 99, 235]],
  [0.35, [22, 163, 74]],
  [0.65, [234, 179, 8]],
  [1, [220, 38, 38]],
];

function ramp(t: number): number[] {
  t = Math.max(0, Math.min(1, t));
  let i = 0;
  while (i < STOPS.length - 2 && t > STOPS[i + 1][0]) i++;
  const [t0, c0] = STOPS[i];
  const [t1, c1] = STOPS[i + 1];
  const f = (t - t0) / (t1 - t0 || 1);
  return [0, 1, 2].map((j) => Math.round(c0[j] + (c1[j] - c0[j]) * f));
}

// Reja: bo'limlar (metrda) va heatmap kataklari — brauzerda chiziladi (reja rasmi bulutga yuborilmaydi)
function PlanHeat({ plan, heat, visits }: { plan: FlowPlan; heat: FlowHeat; visits: Record<number, number> }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !plan.w_m || !plan.h_m) return;
    const W = 1000;
    const k = W / plan.w_m;
    cv.width = W;
    cv.height = Math.round(plan.h_m * k);
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.strokeStyle = "#e2e8f0";
    ctx.lineWidth = 1;
    for (let x = 0; x <= plan.w_m; x++) {
      ctx.beginPath();
      ctx.moveTo(x * k, 0);
      ctx.lineTo(x * k, cv.height);
      ctx.stroke();
    }
    for (let y = 0; y <= plan.h_m; y++) {
      ctx.beginPath();
      ctx.moveTo(0, y * k);
      ctx.lineTo(cv.width, y * k);
      ctx.stroke();
    }
    if (heat.max > 0 && plan.nx && plan.ny) {
      const raw = atob(heat.data);
      const oc = document.createElement("canvas");
      oc.width = plan.nx;
      oc.height = plan.ny;
      const octx = oc.getContext("2d");
      if (octx && raw.length === plan.nx * plan.ny) {
        const im = octx.createImageData(plan.nx, plan.ny);
        for (let i = 0; i < raw.length; i++) {
          const v = raw.charCodeAt(i) / 255;
          if (v <= 0.01) continue;
          const t = Math.pow(v, 0.6);
          const c = ramp(t);
          im.data.set([c[0], c[1], c[2], Math.round(70 + 150 * t)], i * 4);
        }
        octx.putImageData(im, 0, 0);
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(oc, 0, 0, plan.nx * plan.cell * k, plan.ny * plan.cell * k);
      }
    }
    ctx.font = "600 14px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const s of plan.sections) {
      if (s.pts.length < 3) continue;
      ctx.beginPath();
      s.pts.forEach((p, i) => (i ? ctx.lineTo(p[0] * k, p[1] * k) : ctx.moveTo(p[0] * k, p[1] * k)));
      ctx.closePath();
      ctx.lineWidth = 2;
      ctx.strokeStyle = s.color;
      ctx.stroke();
      const cx = s.pts.reduce((a, p) => a + p[0], 0) / s.pts.length;
      const cy = s.pts.reduce((a, p) => a + p[1], 0) / s.pts.length;
      const label = `${s.name}: ${visits[s.id] ?? 0}`;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = s.color;
      ctx.fillRect(cx * k - tw / 2 - 6, cy * k - 11, tw + 12, 22);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, cx * k, cy * k + 1);
    }
  }, [plan, heat, visits]);
  return <canvas ref={ref} className="flow-canvas" data-testid="flow-canvas" />;
}

export default function FlowPage() {
  const [days, setDays] = useState(7);
  const [sel, setSel] = useState<string>("");
  const [kind, setKind] = useState<"occ" | "pas">("occ");
  const [bid, pid] = sel ? sel.split(":") : ["", ""];
  const v = useData(
    () => api<Flow>(`/flow?days=${days}${bid ? `&branch_id=${bid}&plan_id=${pid}` : ""}`),
    [days, sel],
  );
  useInterval(() => v.reload(), 120_000);
  const data = v.data;
  const plan = data?.plan ?? null;
  const visits = useMemo(() => {
    const o: Record<number, number> = {};
    (data?.sections ?? []).forEach((s) => (o[s.id] = s.visits));
    return o;
  }, [data]);
  const colors = useMemo(() => {
    const o: Record<number, string> = {};
    (plan?.sections ?? []).forEach((s) => (o[s.id] = s.color));
    return o;
  }, [plan]);
  const todayTotal = (data?.today_hours ?? []).reduce((a, h) => a + Object.values(h).reduce((x, y) => x + y, 0), 0);
  const hours = data
    ? data.today_hours.map((h, i) => ({ x: data.day0 + i * 3600 + 1800, y: Object.values(h).reduce((a, b) => a + b, 0) }))
    : [];
  const daily = data ? data.per_day.map((y, i) => ({ x: data.t0 + i * 86400 + 43200, y })) : [];
  const hourLabel = (x: number) => `${pad(Math.floor((x - (data?.day0 ?? 0)) / 3600))}:00`;
  const maxV = Math.max(1, ...(data?.sections ?? []).map((s) => s.visits));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Oqim</h1>
          <p className="muted">
            Do&apos;kon rejasida: qaysi bo&apos;limga necha xaridor kirdi, odamlar bo&apos;limdan bo&apos;limga qanday
            yuradi, qayer gavjum. To&apos;xtamay o&apos;tib ketganlar tashrif emas (heatmap&apos;dagi &quot;o&apos;tganlar&quot;da
            ko&apos;rinadi), xodimlar hisobga olinmaydi.
          </p>
        </div>
      </div>
      <div className="filters">
        {(data?.plans.length ?? 0) > 1 ? (
          <label className="field">
            <span>Filial va reja</span>
            <select
              className="select"
              value={sel || (plan ? `${plan.branch_id}:${plan.id}` : "")}
              onChange={(e) => setSel(e.target.value)}
            >
              {(data?.plans ?? []).map((p) => (
                <option key={`${p.branch_id}:${p.plan_id}`} value={`${p.branch_id}:${p.plan_id}`}>
                  {p.branch_name} · {p.plan_name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="tabs" role="tablist" aria-label="Davr" style={{ borderBottom: 0 }}>
          {PERIODS.map((d) => (
            <button key={d} role="tab" aria-selected={days === d} className={days === d ? "tab on" : "tab"} onClick={() => setDays(d)}>
              {d === 1 ? "Bugun" : `${d} kun`}
            </button>
          ))}
        </div>
      </div>
      {v.error ? <p className="error">{v.error}</p> : null}
      {!v.loading && data && !plan ? (
        <div className="empty">
          <strong>Hali oqim ma&apos;lumoti yo&apos;q</strong>
          Do&apos;kon kompyuterining panelida: <b>Oqim → Reja va kameralar</b> — reja yuklang, bo&apos;limlarni chizing va
          kamerani rejaga bog&apos;lang (kalibrlash). Ma&apos;lumot har daqiqada shu yerga keladi.
        </div>
      ) : data && plan ? (
        <>
          <div className="kpis">
            <div className="kpi" data-testid="flow-total">
              <b>{data.total}</b>
              <span>{days === 1 ? "bugun" : `${days} kunda`} bo&apos;lim tashriflari</span>
            </div>
            <div className="kpi">
              <b>{todayTotal}</b>
              <span>bugun</span>
            </div>
            <div className="kpi">
              <b>{data.sections[0]?.visits ? data.sections[0].name : "—"}</b>
              <span>eng ko&apos;p tashrif</span>
            </div>
            <div className="kpi">
              <b>{data.moves[0] ? `${data.moves[0].from_name} → ${data.moves[0].to_name}` : "—"}</b>
              <span>eng ko&apos;p yo&apos;nalish{data.moves[0] ? ` (${data.moves[0].n})` : ""}</span>
            </div>
          </div>
          <div className="panel" style={{ marginTop: 16 }}>
            <div className="flow-head">
              <h3>
                {plan.branch_name} · {plan.name} <small className="muted">({plan.w_m} × {plan.h_m} m)</small>
              </h3>
              <div className="tabs" role="tablist" aria-label="Heatmap turi" style={{ borderBottom: 0 }}>
                <button role="tab" aria-selected={kind === "occ"} className={kind === "occ" ? "tab on" : "tab"} onClick={() => setKind("occ")}>
                  Bandlik
                </button>
                <button role="tab" aria-selected={kind === "pas"} className={kind === "pas" ? "tab on" : "tab"} onClick={() => setKind("pas")}>
                  O&apos;tganlar
                </button>
              </div>
            </div>
            <PlanHeat plan={plan} heat={data.heat[kind]} visits={visits} />
            <p className="sub">
              {kind === "occ" ? "Bandlik — odamlar qayerda ko'p vaqt bo'lgan" : "O'tganlar — har joydan necha kishi o'tgan"} ·
              ko&apos;k — kam, qizil — ko&apos;p · 1 katak = 1 m
            </p>
          </div>
          <div className="charts">
            <div className="panel">
              <h3>Bo&apos;limlar</h3>
              <table className="data">
                <thead>
                  <tr>
                    <th>Bo&apos;lim</th>
                    <th className="num">Tashrif</th>
                    <th className="num">Ulush</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sections.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <span className="swatch" style={{ background: colors[s.id] ?? "#64748b" }} /> {s.name}
                        <div className="bar">
                          <i style={{ width: `${(s.visits / maxV) * 100}%`, background: colors[s.id] ?? "#64748b" }} />
                        </div>
                      </td>
                      <td className="num">
                        <b>{s.visits}</b>
                      </td>
                      <td className="num">{Math.round(s.share * 100)}%</td>
                    </tr>
                  ))}
                  {!data.sections.length ? (
                    <tr>
                      <td colSpan={3} className="muted">
                        Bu davrda tashrif yo&apos;q.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <div className="panel">
              <h3>Yo&apos;nalishlar</h3>
              <p className="sub">Bo&apos;limdan → bo&apos;limga (3 daqiqa ichida, bitta kamera ichida)</p>
              <table className="data">
                <tbody>
                  {data.moves.slice(0, 10).map((m) => (
                    <tr key={`${m.from}-${m.to}`}>
                      <td>
                        {m.from_name} → <b>{m.to_name}</b>
                      </td>
                      <td className="num">{m.n}</td>
                    </tr>
                  ))}
                  {!data.moves.length ? (
                    <tr>
                      <td className="muted">Hali yo&apos;nalish yo&apos;q.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
          <div className="charts">
            <div className="panel chart">
              <h3>Bugun soatlar bo&apos;yicha</h3>
              <BarChart points={hours} color="var(--teal)" unit="tashrif" label={hourLabel} />
            </div>
            <div className="panel chart">
              <h3>Kunlar bo&apos;yicha</h3>
              <BarChart points={daily} color="var(--teal)" unit="tashrif" slot={86400} label={(x) => dayLabel(x)} />
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
