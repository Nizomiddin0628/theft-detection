"use client";

import Link from "next/link";
import { useState } from "react";

import { useSession } from "@/components/Shell";
import Tile from "@/components/Tile";
import { api, type Branch, type EventRow, type Losses } from "@/lib/api";
import { ago, som } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useData, useInterval } from "@/lib/useData";

function camsUp(b: Branch) {
  const live = b.cameras.filter((c) => c.kind !== "file");
  return { up: live.filter((c) => c.state === "ishlayapti").length, total: live.length };
}

export default function Dashboard() {
  const { me } = useSession();
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const branches = useData(() => api<{ branches: Branch[] }>("/branches").then((r) => r.branches));
  const pending = useData(() => api<{ events: EventRow[] }>("/events?status=yangi&limit=60").then((r) => r.events));
  const losses = useData(() => api<Losses>("/losses"));      // 3.1.0: oldini olingan zarar

  useLive((m) => {
    if (m.kind === "event_new") {
      const uid = String(m.data.uid);
      setFresh((s) => new Set(s).add(uid));
      pending.reload();
      branches.reload(800);
    } else if (m.kind === "event_update") {
      if (m.data.loss_sum !== undefined || m.data.status === "tasdiqlangan") losses.reload(800);
      if (m.data.status && m.data.status !== "yangi") {
        pending.setData((xs) => (xs ? xs.filter((e) => e.uid !== m.data.uid) : xs));
        branches.reload(800);
      } else {
        pending.reload();
      }
    } else if (m.kind === "branch" || m.kind === "health") {
      branches.reload();
    }
  });
  useInterval(() => {
    branches.reload(0);
    pending.reload(0);
    losses.reload(0);
  }, 60000);

  const evs = pending.data ?? [];
  const oldest = evs.length ? Math.min(...evs.map((e) => e.created_at)) : null;
  const bs = branches.data ?? [];
  const offline = bs.filter((b) => !b.online && b.last_seen).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Bosh sahifa</h1>
          <p className="muted">
            {bs.length
              ? offline
                ? `${bs.length} ta filialdan ${offline} tasi bilan aloqa yo'q.`
                : `${bs.length} ta filial, hammasi aloqada.`
              : "Filiallar ro'yxati yuklanmoqda."}
          </p>
        </div>
      </div>

      {branches.error ? <p className="error">{branches.error}</p> : null}
      {bs.length === 0 && !branches.loading ? (
        <div className="empty">
          <strong>Hali filial yo&apos;q</strong>
          {me.user.role === "owner" ? (
            <>
              Do&apos;kondagi kompyuterni ulash uchun{" "}
              <Link href="/branches/">filial qo&apos;shing</Link>.
            </>
          ) : (
            "Kompaniya egasi sizga filial biriktirishi kerak."
          )}
        </div>
      ) : (
        <div className="branch-strip" aria-label="Filiallar holati">
          {bs.map((b) => {
            const c = camsUp(b);
            return (
              <Link key={b.id} href={`/branches/#b${b.id}`} className={b.online || !b.last_seen ? "plate" : "plate offline"}>
                <span className="plate-top">
                  <span className="plate-name">{b.name}</span>
                  <span className={b.online ? "pill online" : b.last_seen ? "pill offline" : "pill yangi"}>
                    {b.online ? "Aloqada" : b.last_seen ? "Aloqa yo'q" : "Hali ulanmagan"}
                  </span>
                </span>
                <span className="plate-facts">
                  <span className={c.total && c.up < c.total ? "warn" : ""}>
                    <b>
                      {c.up}/{c.total}
                    </b>
                    kamera ishlayapti
                  </span>
                  <span className={b.pending_review ? "warn" : ""}>
                    <b>{b.pending_review}</b>tekshirilmagan
                  </span>
                  {b.visits_today ? (
                    <span title={`Bugun kirdi ${b.visits_today.in}, chiqdi ${b.visits_today.out}`}>
                      <b>{b.visits_today.inside}</b>ichkarida · bugun {b.visits_today.in}
                    </span>
                  ) : null}
                </span>
                {!b.online && b.last_seen ? <span className="small muted">Oxirgi aloqa: {ago(b.last_seen)}</span> : null}
              </Link>
            );
          })}
        </div>
      )}

      {losses.data && (losses.data.total.n > 0 || losses.data.missing > 0) ? (
        <section className="panel loss-strip" aria-label="Oldini olingan zarar" data-testid="loss-strip">
          <span className="loss-main">
            <span className="muted small">Oldini olingan zarar, shu oy</span>
            <br />
            <b data-testid="loss-month">{som(losses.data.month.sum)}</b>
          </span>
          <span>
            <span className="muted small">Bugun</span>
            <br />
            {som(losses.data.today.sum)}
          </span>
          <span>
            <span className="muted small">Jami</span>
            <br />
            {som(losses.data.total.sum)} · {losses.data.total.n} ta holat
          </span>
          {losses.data.missing ? (
            <span className="small">
              {losses.data.missing} ta tasdiqlangan o&apos;g&apos;irlikda summa yozilmagan
              <Link href="/events/?status=tasdiqlangan">Yozish</Link>
            </span>
          ) : null}
        </section>
      ) : null}

      <section className="section wall" aria-labelledby="wall-h">
        <div className="wall-head">
          <div>
            <span className="wall-count" data-testid="pending-count">
              {evs.length}
            </span>
            <h2 id="wall-h" style={{ display: "inline" }}>
              Tekshirish kerak
            </h2>
          </div>
          <span className="wall-sub">
            <span>{oldest ? `Eng eskisi ${ago(oldest)}` : "Yangi signal kelsa, shu yerda paydo bo'ladi"}</span>
            {evs.length > 0 ? (
              <Link className="btn small on-dark" href={`/events/?uid=${encodeURIComponent(evs[evs.length - 1].uid)}&queue=1`}>
                Navbat bilan tekshirish
              </Link>
            ) : null}
          </span>
        </div>
        {pending.error ? <p className="error">{pending.error}</p> : null}
        {evs.length === 0 && !pending.loading ? (
          <div className="wall-empty">
            <strong>Hammasi tekshirilgan</strong>
            Kameralar kuzatishda davom etadi.
          </div>
        ) : (
          <div className="wall-grid">
            {evs.map((e) => (
              <Tile key={e.uid} ev={e} fresh={fresh.has(e.uid)} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
