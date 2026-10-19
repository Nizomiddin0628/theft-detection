"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import EventView from "@/components/EventView";
import { api, clipUrl, KIND_UZ, STATUS_UZ, type Branch, type EventRow, type Status } from "@/lib/api";
import { scoreTone, som, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useData, useInterval } from "@/lib/useData";

const TABS: { key: Status | ""; label: string }[] = [
  { key: "yangi", label: "Tekshirilmagan" },
  { key: "tasdiqlangan", label: "Tasdiqlangan" },
  { key: "rad_etilgan", label: "Xato signal" },
  { key: "", label: "Hammasi" },
];

const PERIODS = [
  { key: "1", label: "Bugun" },
  { key: "7", label: "7 kun" },
  { key: "30", label: "30 kun" },
  { key: "", label: "Hammasi" },
];

function since(days: string): number | null {
  if (!days) return null;
  if (days === "1") {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime() / 1000;
  }
  return Date.now() / 1000 - Number(days) * 86400;
}

function EventList({ initial }: { initial: Status | "" }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status | "">(initial);
  const [branch, setBranch] = useState("");
  const [period, setPeriod] = useState("7");
  const branches = useData(() => api<{ branches: Branch[] }>("/branches").then((r) => r.branches));
  const list = useData(() => {
    const q = new URLSearchParams({ limit: "200" });
    if (status) q.set("status", status);
    if (branch) q.set("branch_id", branch);
    const s = since(period);
    if (s) q.set("since", String(Math.floor(s)));
    return api<{ events: EventRow[] }>(`/events?${q}`).then((r) => r.events);
  }, [status, branch, period]);

  useLive((m) => {
    if (m.kind === "event_new" || m.kind === "event_update") list.reload(400);
  });
  useInterval(() => list.reload(0), 60000);

  const evs = list.data ?? [];
  const open = (uid: string) => router.push(`/events/?uid=${encodeURIComponent(uid)}`);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Hodisalar</h1>
          <p className="muted">Kameralar belgilagan barcha shubhali harakatlar. Qatorni bosib videoni oching.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key || "all"} role="tab" aria-selected={status === t.key} className={status === t.key ? "tab on" : "tab"} onClick={() => setStatus(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="filters">
        {(branches.data?.length ?? 0) > 1 ? (
          <label className="field">
            <span>Filial</span>
            <select className="select" value={branch} onChange={(e) => setBranch(e.target.value)}>
              <option value="">Barcha filiallar</option>
              {branches.data!.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="field">
          <span>Davr</span>
          <select className="select" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => (
              <option key={p.key || "all"} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small" style={{ paddingBottom: 10 }}>
          {list.loading ? "Yuklanmoqda…" : `${evs.length}${evs.length >= 200 ? "+" : ""} ta hodisa`}
        </span>
      </div>
      {list.error ? <p className="error">{list.error}</p> : null}
      {!list.loading && evs.length === 0 ? (
        <div className="empty">
          <strong>Bu davrda hodisa yo&apos;q</strong>
          Boshqa holat yoki davrni tanlab ko&apos;ring.
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Kadr</th>
                <th>Vaqt</th>
                <th>Filial</th>
                <th>Kamera</th>
                <th>Harakat</th>
                <th className="num">Ball</th>
                <th>Holat</th>
              </tr>
            </thead>
            <tbody>
              {evs.map((e) => {
                const img = clipUrl(e.thumb_file);
                return (
                  <tr
                    key={e.uid}
                    className="click"
                    tabIndex={0}
                    onClick={() => open(e.uid)}
                    onKeyDown={(k) => {
                      if (k.key === "Enter") open(e.uid);
                    }}
                    data-uid={e.uid}
                  >
                    <td>
                      {img ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img className="thumb-mini" src={img} alt="" loading="lazy" />
                      ) : (
                        <span className="thumb-mini" />
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{when(e.created_at)}</td>
                    <td>{e.branch_name}</td>
                    <td>{e.source_name}</td>
                    <td>{KIND_UZ[e.kind] ?? e.kind}</td>
                    <td className={`num score ${scoreTone(e.score)}`}>{Math.round(e.score)}</td>
                    <td>
                      <span className={`pill ${e.status}`}>{STATUS_UZ[e.status] ?? e.status}</span>
                      {e.status === "tasdiqlangan" && e.loss_sum ? <div className="small">💰 {som(e.loss_sum)}</div> : null}
                      {e.reviewed_by ? <div className="small muted">{e.reviewed_by}</div> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function EventsPage() {
  const params = useSearchParams();
  const uid = params.get("uid");
  if (uid) return <EventView uid={uid} queue={params.get("queue") === "1"} />;
  const st = params.get("status");
  return <EventList initial={st === "tasdiqlangan" || st === "rad_etilgan" || st === "" ? st : "yangi"} />;
}

export default function Page() {
  return (
    <Suspense>
      <EventsPage />
    </Suspense>
  );
}
