"use client";

// 1.9.0 Tashriflar: do'kon eshigidagi sanash chizig'i bo'yicha kirdi/chiqdi — filiallar, kunlar, bugun soatlar
import Link from "next/link";
import { useMemo, useState } from "react";

import { BarChart } from "@/components/Charts";
import { api, type Branch, type Visits } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import { useData, useInterval } from "@/lib/useData";

const PERIODS = [7, 14, 30, 90];
const pad = (n: number) => String(n).padStart(2, "0");

export default function VisitsPage() {
  const [days, setDays] = useState(14);
  const [bid, setBid] = useState<string>("");
  // Kamida 15 kun so'raladi: haftalik taqqoslash (oxirgi 7 kun bugunsiz ↔ undan oldingi 7 kun) uchun
  const v = useData(() => api<Visits>(`/visits?days=${Math.max(days, 15)}${bid ? `&branch_id=${bid}` : ""}`), [days, bid]);
  const all = useData(() => api<{ branches: Branch[] }>("/branches").then((r) => r.branches), []);
  useInterval(() => v.reload(), 60_000);

  const data = v.data;
  const shown = useMemo(() => data?.branches ?? [], [data]);
  const todayIn = shown.reduce((a, b) => a + b.today_in, 0);
  const inside = shown.reduce((a, b) => a + b.inside, 0);
  const hourly = useMemo(() => {
    const h = Array(24).fill(0) as number[];
    shown.forEach((b) => b.hourly.forEach((x, i) => (h[i] += x)));
    return h;
  }, [shown]);
  const busiest = hourly.some((x) => x > 0) ? hourly.indexOf(Math.max(...hourly)) : null;
  const allDays = data?.days ?? [];                              // eskisi birinchi, oxirgisi — bugun
  const periodDays = allDays.slice(-days);
  const periodIn = periodDays.reduce((a, d) => a + d.in, 0);
  const sumIn = (a: typeof allDays) => a.reduce((x, d) => x + d.in, 0);
  const week = sumIn(allDays.slice(-8, -1));
  const prevWeek = sumIn(allDays.slice(-15, -8));
  const weekPct = prevWeek ? Math.round(((week - prevWeek) / prevWeek) * 100) : null;
  const daily = periodDays.map((d) => ({ x: d.day + 43200, y: d.in }));
  const hours = data ? hourly.map((y, i) => ({ x: data.day0 + i * 3600 + 1800, y })) : [];
  // Soat yorlig'i do'kon vaqtida (brauzer boshqa vaqt mintaqasida bo'lsa ham): kun boshidan nechanchi soat
  const hourLabel = (x: number) => `${pad(Math.floor((x - (data?.day0 ?? 0)) / 3600))}:00`;
  const branchCols = shown.map((b) => b);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tashriflar</h1>
          <p className="muted">
            Do&apos;kon eshigidagi sanash chizig&apos;i bo&apos;yicha: necha kishi kirdi, hozir ichkarida qancha, eng gavjum soat.
            Xodimlar va ish vaqtidan tashqari o&apos;tishlar hisobga olinmaydi.
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
          <strong>Hali sanash ma&apos;lumoti yo&apos;q</strong>
          Do&apos;kon kompyuterining panelida eshikni ko&apos;radigan kameraga <b>sanash chizig&apos;i</b> chizing (Kameralar →
          Zonalar → &quot;Sanash chizig&apos;i&quot;). Ma&apos;lumot har daqiqada shu yerga keladi.
        </div>
      ) : data ? (
        <>
          <div className="kpis">
            <div className="kpi" data-testid="visits-inside">
              <b>{inside}</b>
              <span>hozir ichkarida</span>
            </div>
            <div className="kpi">
              <b>{todayIn}</b>
              <span>bugun kirdi</span>
            </div>
            <div className="kpi">
              <b>{busiest === null ? "—" : `${pad(busiest)}:00`}</b>
              <span>bugun eng gavjum soat</span>
            </div>
            <div className="kpi">
              <b>{periodIn}</b>
              <span>{days} kunda kirdi</span>
            </div>
            <div className="kpi" data-testid="visits-week">
              <b>
                {week}
                {weekPct !== null ? (
                  <small className={weekPct >= 0 ? "up" : "down"}> {weekPct >= 0 ? `+${weekPct}` : `−${-weekPct}`}%</small>
                ) : null}
              </b>
              <span>oxirgi 7 kun (bugunsiz){weekPct !== null ? " — oldingi 7 kunga nisbatan" : ""}</span>
            </div>
          </div>
          <div className="charts">
            <div className="panel chart">
              <h3>Kunlar bo&apos;yicha</h3>
              <p className="sub">Kirgan xaridorlar, {shown.length > 1 ? "barcha tanlangan filiallar" : shown[0]?.name}</p>
              <BarChart points={daily} color="var(--teal)" unit="kishi" slot={86400} label={(x) => dayLabel(x)} />
            </div>
            <div className="panel chart">
              <h3>Bugun soatlar bo&apos;yicha</h3>
              <p className="sub">Har soatda kirganlar</p>
              <BarChart points={hours} color="var(--teal)" unit="kishi" label={hourLabel} />
            </div>
          </div>
          <h2 style={{ margin: "20px 0 10px" }}>Filiallar</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Filial</th>
                  <th className="num">Hozir ichkarida</th>
                  <th className="num">Bugun kirdi</th>
                  <th className="num">Bugun chiqdi</th>
                  <th className="num">Eng gavjum soat</th>
                  <th className="num">{days} kunda</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <Link href={`/branches/#b${b.id}`}>{b.name}</Link>
                    </td>
                    <td className="num">
                      <b>{b.inside}</b>
                    </td>
                    <td className="num">{b.today_in}</td>
                    <td className="num">{b.today_out}</td>
                    <td className="num">{b.busiest_hour === null ? "—" : `${pad(b.busiest_hour)}:00`}</td>
                    <td className="num">{periodDays.reduce((a, d) => a + (d.branches[String(b.id)]?.in ?? 0), 0)}</td>
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
                  {branchCols.map((b) => (
                    <th key={b.id} className="num">
                      {b.name}
                    </th>
                  ))}
                  {branchCols.length > 1 ? <th className="num">Jami</th> : null}
                </tr>
              </thead>
              <tbody>
                {periodDays
                  .slice()
                  .reverse()
                  .map((d) => (
                    <tr key={d.day}>
                      <td>{dayLabel(d.day + 43200)}</td>
                      {branchCols.map((b) => {
                        const x = d.branches[String(b.id)];
                        return (
                          <td key={b.id} className="num" title={x ? `chiqdi ${x.out}, xodim ${x.staff}` : ""}>
                            {x ? x.in : "—"}
                          </td>
                        );
                      })}
                      {branchCols.length > 1 ? <td className="num">{d.in}</td> : null}
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
