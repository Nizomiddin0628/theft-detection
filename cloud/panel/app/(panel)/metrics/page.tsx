"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { BarChart, LineChart, PALETTE, type Series } from "@/components/Charts";
import { api, type Branch, type MetricRow } from "@/lib/api";
import { useData } from "@/lib/useData";

const PERIODS = [
  { h: 24, label: "24 soat" },
  { h: 24 * 7, label: "7 kun" },
  { h: 24 * 30, label: "30 kun" },
];

function bySource(rows: MetricRow[], field: keyof MetricRow, scale = 1): Series[] {
  const map = new Map<number, Series>();
  rows
    .filter((r) => r.source_id > 0)
    .forEach((r) => {
      let s = map.get(r.source_id);
      if (!s) {
        s = { name: r.source_name || `Kamera ${r.source_id}`, color: PALETTE[map.size % PALETTE.length], points: [] };
        map.set(r.source_id, s);
      }
      const v = r[field] as number | null;
      s.points.push({ x: r.hour, y: v === null || v === undefined ? null : v * scale });
    });
  return Array.from(map.values());
}

function Metrics() {
  const params = useSearchParams();
  const router = useRouter();
  const [hours, setHours] = useState(24);
  const branches = useData(() => api<{ branches: Branch[] }>("/branches").then((r) => r.branches));
  const bid = params.get("branch") ?? (branches.data?.[0] ? String(branches.data[0].id) : null);
  const m = useData(
    () => (bid ? api<{ rows: MetricRow[] }>(`/branches/${bid}/metrics?hours=${hours}`).then((r) => r.rows) : Promise.resolve([] as MetricRow[])),
    [bid, hours],
  );
  const rows = useMemo(() => m.data ?? [], [m.data]);

  const people = useMemo(() => bySource(rows, "persons_avg"), [rows]);
  const uptime = useMemo(() => bySource(rows, "up_pct", 100), [rows]);
  const fps = useMemo(() => bySource(rows, "fps_ai"), [rows]);
  const sys = useMemo<Series[]>(() => {
    const r0 = rows.filter((r) => r.source_id === 0);
    return [
      { name: "Protsessor", color: PALETTE[2], points: r0.map((r) => ({ x: r.hour, y: r.cpu })) },
      { name: "Xotira", color: PALETTE[4], points: r0.map((r) => ({ x: r.hour, y: r.ram })) },
    ];
  }, [rows]);
  const alerts = useMemo(() => {
    const acc = new Map<number, number>();
    rows.filter((r) => r.source_id > 0).forEach((r) => acc.set(r.hour, (acc.get(r.hour) ?? 0) + (r.alerts || 0)));
    return Array.from(acc, ([x, y]) => ({ x, y })).sort((a, b) => a.x - b.x);
  }, [rows]);

  const cams = rows.filter((r) => r.source_id > 0);
  const totalAlerts = alerts.reduce((a, b) => a + b.y, 0);
  const peak = cams.reduce((a, r) => Math.max(a, r.persons_max || 0), 0);
  const upVals = cams.map((r) => r.up_pct).filter((v): v is number => v !== null);
  const upAvg = upVals.length ? (upVals.reduce((a, b) => a + b, 0) / upVals.length) * 100 : null;
  const cpuVals = rows.filter((r) => r.source_id === 0 && r.cpu !== null).map((r) => r.cpu as number);
  const cpuAvg = cpuVals.length ? cpuVals.reduce((a, b) => a + b, 0) / cpuVals.length : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Ko&apos;rsatkichlar</h1>
          <p className="muted">Soatlik yig&apos;indi: kameralar oldidagi odamlar, signallar, kameralar va kompyuter holati.</p>
        </div>
      </div>
      <div className="filters">
        {(branches.data?.length ?? 0) > 0 ? (
          <label className="field">
            <span>Filial</span>
            <select className="select" value={bid ?? ""} onChange={(e) => router.replace(`/metrics/?branch=${e.target.value}`)}>
              {branches.data!.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="tabs" role="tablist" aria-label="Davr" style={{ borderBottom: 0 }}>
          {PERIODS.map((p) => (
            <button key={p.h} role="tab" aria-selected={hours === p.h} className={hours === p.h ? "tab on" : "tab"} onClick={() => setHours(p.h)}>
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {m.error ? <p className="error">{m.error}</p> : null}
      {!m.loading && !rows.length ? (
        <div className="empty">
          <strong>Bu davr uchun ma&apos;lumot yo&apos;q</strong>
          Do&apos;kon kompyuteri har daqiqada ko&apos;rsatkich yuboradi; aloqa tiklangach bu yer to&apos;ladi.
        </div>
      ) : (
        <>
          <div className="kpis">
            <div className="kpi">
              <b>{totalAlerts}</b>
              <span>signal</span>
            </div>
            <div className="kpi">
              <b>{peak}</b>
              <span>bir vaqtda eng ko&apos;p odam (bitta kamera)</span>
            </div>
            <div className="kpi">
              <b>{upAvg === null ? "—" : `${upAvg.toFixed(1)}%`}</b>
              <span>kameralar ishlagan vaqt</span>
            </div>
            <div className="kpi">
              <b>{cpuAvg === null ? "—" : `${Math.round(cpuAvg)}%`}</b>
              <span>protsessor yuklamasi, o&apos;rtacha</span>
            </div>
          </div>
          <div className="charts">
            <div className="panel chart">
              <h3>Kamera oldidagi odamlar</h3>
              <p className="sub">Soatlik o&apos;rtacha, kamera bo&apos;yicha</p>
              <LineChart series={people} />
            </div>
            <div className="panel chart">
              <h3>Signallar</h3>
              <p className="sub">Har soatda barcha kameralar bo&apos;yicha</p>
              <BarChart points={alerts} color="var(--off)" />
            </div>
            <div className="panel chart">
              <h3>Kameralar ishlagan vaqt</h3>
              <p className="sub">Soat ichida kadr kelgan ulush</p>
              <LineChart series={uptime} unit="%" max={100} fmt={(v) => v.toFixed(0)} />
            </div>
            <div className="panel chart">
              <h3>Do&apos;kon kompyuteri</h3>
              <p className="sub">Protsessor va xotira yuklamasi</p>
              <LineChart series={sys} unit="%" max={100} fmt={(v) => v.toFixed(0)} />
            </div>
            <div className="panel chart">
              <h3>Tahlil tezligi</h3>
              <p className="sub">Bir soniyada tahlil qilingan kadrlar, kamera bo&apos;yicha</p>
              <LineChart series={fps} />
            </div>
          </div>
        </>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Metrics />
    </Suspense>
  );
}
