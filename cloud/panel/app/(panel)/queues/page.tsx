"use client";

// 2.0.0 Navbat: filiallar kassalaridagi navbat hozir, bugungi kutish vaqti, soatlar, kunlar va qoida signallari
import Link from "next/link";
import { useMemo, useState } from "react";

import { BarChart } from "@/components/Charts";
import { api, type Branch, type QueueLive, type Queues } from "@/lib/api";
import { dayLabel, when } from "@/lib/format";
import { useData, useInterval } from "@/lib/useData";

const PERIODS = [7, 14, 30];
const pad = (n: number) => String(n).padStart(2, "0");
const STATE_UZ: Record<string, string> = {
  navbat: "uzun navbat",
  kassir_yoq: "navbat bor, kassir yo'q",
  band: "band",
  bosh: "bo'sh",
  yopiq: "yopiq",
  nomalum: "ma'lumot yo'q",
};

function fmtWait(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const v = Math.max(0, Math.round(s));
  if (v < 60) return `${v} s`;
  const m = Math.floor(v / 60);
  return m < 60 ? `${m}:${pad(v % 60)}` : `${Math.floor(m / 60)}:${pad(m % 60)}:${pad(v % 60)}`;
}

function QueueCard({ q, branch }: { q: QueueLive; branch: string }) {
  const st = q.live ? q.state : "nomalum";
  return (
    <div className={`qcard q-${st}`} data-testid="queue-card">
      <div className="qcard-h">
        <span>
          <b>{q.name}</b>
          <small>{branch}</small>
        </span>
        <span className={`qstate q-${st}`}>{STATE_UZ[st] ?? st}</span>
      </div>
      <div className="qcard-n">
        <b>{q.live ? q.length : "—"}</b> kishi navbatda
      </div>
      <p className="small">
        {!q.live ? "Kamera ishlamayapti" : q.length ? `Eng uzoq kutayotgan: ${fmtWait(q.max_wait)}` : "Navbat yo'q"}
        {q.cashier === null ? "" : q.cashier ? " · kassir bor" : " · kassir yo'q"}
      </p>
    </div>
  );
}

export default function QueuesPage() {
  const [days, setDays] = useState(14);
  const [bid, setBid] = useState<string>("");
  const v = useData(() => api<Queues>(`/queues?days=${days}${bid ? `&branch_id=${bid}` : ""}`), [days, bid]);
  const all = useData(() => api<{ branches: Branch[] }>("/branches").then((r) => r.branches), []);
  useInterval(() => v.reload(), 20_000);

  const data = v.data;
  const shown = useMemo(() => data?.branches ?? [], [data]);
  const lives = shown.flatMap((b) => (b.live ?? []).map((q) => ({ q, b: b.name, bid: b.id })));
  const nowLen = lives.filter((x) => x.q.live).reduce((a, x) => a + x.q.length, 0);
  const longest = lives.filter((x) => x.q.live).reduce<{ q: QueueLive; b: string } | null>((m, x) => (!m || x.q.length > m.q.length ? x : m), null);
  const noCash = lives.filter((x) => x.q.live && x.q.state === "kassir_yoq").length;
  const served = shown.reduce((a, b) => a + b.today.served, 0);
  const waits = shown.filter((b) => b.today.avg_wait !== null);
  const avgWait = waits.length
    ? waits.reduce((a, b) => a + (b.today.avg_wait ?? 0) * b.today.served, 0) / Math.max(1, waits.reduce((a, b) => a + b.today.served, 0))
    : null;
  const hoursLen = useMemo(() => {
    const h = Array(24).fill(0) as number[];
    shown.forEach((b) => b.hours_len.forEach((x, i) => (h[i] += x)));
    return h;
  }, [shown]);
  const hoursServed = useMemo(() => {
    const h = Array(24).fill(0) as number[];
    shown.forEach((b) => b.hours_served.forEach((x, i) => (h[i] += x)));
    return h;
  }, [shown]);
  const hourLabel = (x: number) => `${pad(Math.floor((x - (data?.day0 ?? 0)) / 3600))}:00`;
  const pts = (arr: number[]) => (data ? arr.map((y, i) => ({ x: data.day0 + i * 3600 + 1800, y: Math.round(y * 10) / 10 })) : []);
  const periodDays = (data?.days ?? []).slice().reverse();

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Navbat</h1>
          <p className="muted">
            Har kassada navbat va kutish vaqti, kassir bor/yo&apos;q. Signallar: qo&apos;shimcha kassa oching, kassani tekshiring,
            kassir jadvali, bir vaqtda bir nechta filialda navbat.
          </p>
        </div>
      </div>
      <div className="filters">
        {(all.data?.length ?? 0) > 1 || bid ? (
          <label className="field">
            <span>Filial</span>
            <select className="select" value={bid} onChange={(e) => setBid(e.target.value)}>
              <option value="">Barcha filiallar</option>
              {(all.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="tabs" role="tablist" aria-label="Davr" style={{ borderBottom: 0 }}>
          {PERIODS.map((d) => (
            <button key={d} role="tab" aria-selected={days === d} className={days === d ? "tab on" : "tab"} onClick={() => setDays(d)}>
              {d} kun
            </button>
          ))}
        </div>
      </div>
      {v.error ? <p className="error">{v.error}</p> : null}
      {!v.loading && data && !shown.length ? (
        <div className="empty">
          <strong>Hali navbat ma&apos;lumoti yo&apos;q</strong>
          Do&apos;kon kompyuterining panelida kassani ko&apos;radigan kameraga <b>Kassa navbati</b> va <b>Kassir joyi</b> zonalarini chizing
          (Kameralar → Zonalar). Do&apos;kon dasturi 2.0.0 yoki yangiroq bo&apos;lishi kerak.
        </div>
      ) : data ? (
        <>
          <div className="kpis">
            <div className="kpi" data-testid="queue-now">
              <b>{nowLen}</b>
              <span>hozir navbatda (jami)</span>
            </div>
            <div className="kpi">
              <b>{longest ? longest.q.length : "—"}</b>
              <span>{longest && longest.q.length ? `eng uzun: ${longest.b} · ${longest.q.name}` : "eng uzun navbat"}</span>
            </div>
            <div className="kpi">
              <b className={noCash ? "warn" : ""}>{noCash}</b>
              <span>kassir yo&apos;q kassa (navbat bor)</span>
            </div>
            <div className="kpi">
              <b>{served}</b>
              <span>bugun navbatdan o&apos;tdi</span>
            </div>
            <div className="kpi">
              <b>{fmtWait(avgWait)}</b>
              <span>bugun o&apos;rtacha kutish</span>
            </div>
          </div>
          <h2 style={{ margin: "20px 0 10px" }}>Kassalar — hozir</h2>
          {lives.length ? (
            <div className="qcards">
              {lives.map((x) => (
                <QueueCard key={`${x.bid}:${x.q.zone_id}`} q={x.q} branch={x.b} />
              ))}
            </div>
          ) : (
            <p className="muted">Filiallardan hozirgi holat kelmayapti (aloqa yo&apos;q yoki kamera o&apos;chiq).</p>
          )}
          {shown.some((b) => b.stale) ? (
            <p className="small muted">Aloqa yo&apos;q filiallar: {shown.filter((b) => b.stale).map((b) => b.name).join(", ")}</p>
          ) : null}
          <div className="charts">
            <div className="panel chart">
              <h3>Bugun: o&apos;rtacha navbat</h3>
              <p className="sub">Kishi, soatlar bo&apos;yicha{shown.length > 1 ? " (filiallar yig'indisi)" : ""}</p>
              <BarChart points={pts(hoursLen)} color="#db2777" unit="kishi" label={hourLabel} />
            </div>
            <div className="panel chart">
              <h3>Bugun: navbatdan o&apos;tganlar</h3>
              <p className="sub">Har soatda</p>
              <BarChart points={pts(hoursServed)} color="#4f46e5" unit="kishi" label={hourLabel} />
            </div>
          </div>
          <h2 style={{ margin: "20px 0 10px" }}>Signallar</h2>
          {data.alerts.length ? (
            <ul className="qalerts">
              {data.alerts.map((a) => (
                <li key={a.id} className={`qa-${a.rule}`}>
                  <span className="qrule" title={data.rules[a.rule]}>
                    {a.rule.replace("R", "")}
                  </span>
                  <div>
                    <b>{a.title}</b>
                    <small>
                      {" "}
                      · {when(a.t)}
                      {a.branch_name ? ` · ${a.branch_name}` : ""}
                      {a.zone_name ? ` · ${a.zone_name}` : ""}
                    </small>
                    <p>{a.text}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Bu davrda signal bo&apos;lmagan.</p>
          )}
          <h2 style={{ margin: "20px 0 10px" }}>Filiallar</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Filial</th>
                  <th className="num">Hozir navbatda</th>
                  <th className="num">Bugun o&apos;tdi</th>
                  <th className="num">O&apos;rtacha kutish</th>
                  <th className="num">Eng uzoq</th>
                  <th className="num">Eng uzun navbat</th>
                  <th className="num">Uzun navbat (daq)</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <Link href={`/branches/#b${b.id}`}>{b.name}</Link>
                    </td>
                    <td className="num">
                      <b>{b.live ? b.live.filter((q) => q.live).reduce((a, q) => a + q.length, 0) : "—"}</b>
                    </td>
                    <td className="num">{b.today.served}</td>
                    <td className="num">{fmtWait(b.today.avg_wait)}</td>
                    <td className="num">{b.today.max_wait ? fmtWait(b.today.max_wait) : "—"}</td>
                    <td className="num">{b.today.max_len}</td>
                    <td className="num">{b.today.long_min}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h2 style={{ margin: "20px 0 10px" }}>Kunlar</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Kun</th>
                  <th className="num">O&apos;tdi</th>
                  <th className="num">O&apos;rtacha kutish</th>
                  <th className="num">Eng uzoq</th>
                  <th className="num">Eng uzun navbat</th>
                  <th className="num">Uzun navbat (daq)</th>
                </tr>
              </thead>
              <tbody>
                {periodDays.map((d) => (
                  <tr key={d.day}>
                    <td>{dayLabel(d.day + 43200)}</td>
                    <td className="num">{d.served}</td>
                    <td className="num">{fmtWait(d.avg_wait)}</td>
                    <td className="num">{d.wait_max ? fmtWait(d.wait_max) : "—"}</td>
                    <td className="num">{d.len_max}</td>
                    <td className="num">{d.long_min}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </>
  );
}
