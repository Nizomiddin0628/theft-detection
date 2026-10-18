"use client";

// 2.4.0 Rahbariyat: filiallarni taqqoslash — kun yoki hafta, o'tgan davr bilan solishtirish, tavsiyalar
import Link from "next/link";
import { useState } from "react";

import { api, type Compare, type CompareBranch } from "@/lib/api";
import { useData } from "@/lib/useData";

const pad = (n: number) => String(n).padStart(2, "0");

function fmtWait(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const v = Math.max(0, Math.round(s));
  return v < 60 ? `${v} s` : `${Math.floor(v / 60)}:${pad(v % 60)}`;
}

function Chg({ p }: { p: number | null }) {
  if (p === null || p === undefined) return null;
  const cls = p < 0 ? "down" : p > 0 ? "up" : "";
  return (
    <small className={`chg ${cls}`}>
      {p > 0 ? "+" : ""}
      {Math.round(p)}%
    </small>
  );
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
}

// Har ustunda eng yomon qiymat ajratiladi (bir nechta filial bo'lsa)
function worst(rows: CompareBranch[], f: (b: CompareBranch) => number | null | undefined, high = true): number | null {
  const vals = rows.map((b) => ({ id: b.id, v: f(b) })).filter((x) => x.v !== null && x.v !== undefined) as { id: number; v: number }[];
  if (vals.length < 2) return null;
  const w = vals.reduce((m, x) => ((high ? x.v > m.v : x.v < m.v) ? x : m));
  return w.v ? w.id : null;
}

export default function ComparePage() {
  const [period, setPeriod] = useState<"week" | "day">("week");
  const [date, setDate] = useState("");
  const v = useData(() => api<Compare>(`/compare?period=${period}${date ? `&date=${date}` : ""}`), [period, date]);
  const d = v.data;
  const rows = d?.branches ?? [];
  const wWait = worst(rows, (b) => b.queue?.avg_wait);
  const wFalse = worst(rows, (b) => b.theft.false_share);
  const wUp = worst(rows, (b) => b.uptime, false);
  const t = d?.total;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Taqqoslash</h1>
          <p className="muted">
            Filiallar bitta jadvalda: tashriflar (o&apos;tgan davrga nisbatan), navbatda kutish, signallar va xato ulushi, do&apos;kon
            kompyuteri ishlagan vaqt. Har dushanba shu xulosa egasi va menejerlarga Telegram/email orqali keladi.
          </p>
        </div>
      </div>
      <div className="filters">
        <div className="tabs" role="tablist" aria-label="Davr" style={{ borderBottom: 0 }}>
          {(["week", "day"] as const).map((p) => (
            <button key={p} role="tab" aria-selected={period === p} className={period === p ? "tab on" : "tab"} onClick={() => setPeriod(p)}>
              {p === "week" ? "Hafta" : "Kun"}
            </button>
          ))}
        </div>
        <label className="field">
          <span>Sana (bo&apos;sh — oxirgi tugagan)</span>
          <input className="select" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>
      {v.error ? <p className="error">{v.error}</p> : null}
      {d && t ? (
        <>
          <p className="muted" data-testid="compare-label">
            {period === "week" ? "Hafta" : "Kun"}: <b>{d.label}</b>
            {d.partial ? " (hali tugamagan)" : ""} · solishtirish — bir hafta oldingi xuddi shu davr
          </p>
          <div className="kpis">
            <div className="kpi">
              <b>
                {t.visits}
                <Chg p={t.change_pct} />
              </b>
              <span>tashriflar (barcha filiallar)</span>
            </div>
            <div className="kpi">
              <b>{fmtWait(t.avg_wait)}</b>
              <span>o&apos;rtacha kutish ({t.served} xaridor)</span>
            </div>
            <div className="kpi">
              <b>{t.theft}</b>
              <span>o&apos;g&apos;irlik signallari</span>
            </div>
            <div className="kpi">
              <b className={t.unreviewed ? "warn" : ""}>{t.unreviewed}</b>
              <span>javobsiz signallar</span>
            </div>
            <div className="kpi">
              <b>{t.safety}</b>
              <span>xavfsizlik signallari</span>
            </div>
          </div>
          {d.recs.length ? (
            <>
              <h2 style={{ margin: "20px 0 10px" }}>Tavsiyalar</h2>
              <ul className="recs" data-testid="compare-recs">
                {d.recs.map((r) => (
                  <li key={r.key} className={r.level === "warn" ? "warn" : ""}>
                    {r.text}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted" style={{ marginTop: 16 }}>
              Alohida tavsiya yo&apos;q — filiallar ko&apos;rsatkichlari me&apos;yorida.
            </p>
          )}
          <h2 style={{ margin: "20px 0 10px" }}>Filiallar</h2>
          {rows.length ? (
            <div className="table-wrap">
              <table className="data" data-testid="compare-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Filial</th>
                    <th className="num">Tashriflar</th>
                    <th className="num">O&apos;rtacha kutish</th>
                    <th className="num">Signallar</th>
                    <th className="num">✅ / ❌ / javobsiz</th>
                    <th className="num">Xato ulushi</th>
                    <th className="num">Xavfsizlik</th>
                    <th className="num">Ishlagan vaqt</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b, i) => (
                    <tr key={b.id}>
                      <td>{i + 1}</td>
                      <td>
                        <Link href={`/branches/#b${b.id}`}>{b.name}</Link>
                        {b.online ? null : <small className="muted"> · aloqasiz</small>}
                      </td>
                      <td className="num">
                        <b>{b.visits.in}</b>
                        <Chg p={b.visits.change_pct} />
                      </td>
                      <td className={`num ${wWait === b.id ? "warn" : ""}`}>{b.queue ? fmtWait(b.queue.avg_wait) : "—"}</td>
                      <td className="num">{b.theft.total}</td>
                      <td className="num">
                        {b.theft.confirmed} / {b.theft.rejected} / {b.theft.unreviewed}
                      </td>
                      <td className={`num ${wFalse === b.id ? "warn" : ""}`}>{pct(b.theft.false_share)}</td>
                      <td className="num">{b.safety.total}</td>
                      <td className={`num ${wUp === b.id ? "warn" : ""}`}>{pct(b.uptime)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              <strong>Filial yo&apos;q</strong>
              Filiallar sahifasida do&apos;kon qo&apos;shing va do&apos;kon kompyuterini ulang.
            </div>
          )}
        </>
      ) : null}
    </>
  );
}
