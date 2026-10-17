"use client";
// Bitta hodisa: katta video, ball va sabablar, qaror tugmalari (T — tasdiqlash, X — xato signal, U — bekor qilish, Esc — ro'yxat)
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api, clipUrl, isSafety, KIND_UZ, STATUS_UZ, type EventFull, type EventRow, type Status } from "@/lib/api";
import { scoreTone, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useData } from "@/lib/useData";
import { IconCamera, IconCheck, IconUndo, IconX } from "./Icons";
import { useSession } from "./Shell";

type Ev = EventFull & { branch_name?: string };

export default function EventView({ uid, queue }: { uid: string; queue: boolean }) {
  const router = useRouter();
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ev = useData(() => api<{ event: Ev }>(`/events/${encodeURIComponent(uid)}`).then((r) => r.event), [uid]);

  useLive((m) => {
    if (m.data.uid !== uid) return;
    if (m.kind === "event_update") {
      if (m.data.status && ev.data && m.data.status !== ev.data.status && !busy) {
        toast({ tone: "info", title: `Bu hodisani ${String(m.data.reviewed_by || "boshqa xodim")} tekshirdi` });
      }
      ev.reload();
    }
  });

  const next = useCallback(async () => {
    const r = await api<{ events: EventRow[] }>("/events?status=yangi&limit=500");
    const rest = r.events.filter((e) => e.uid !== uid);
    if (!rest.length) {
      toast({ tone: "ok", title: "Hammasi tekshirildi" });
      router.push("/");
      return;
    }
    const oldest = rest.reduce((a, b) => (b.created_at < a.created_at ? b : a));
    router.replace(`/events/?uid=${encodeURIComponent(oldest.uid)}&queue=1`);
  }, [router, toast, uid]);

  // Navbatda keyingi hodisaga o'tganda eski hodisa ma'lumoti ekranda qolsa ham, tugmalar unga ishlamaydi
  const current = ev.data && ev.data.uid === uid ? ev.data : null;

  const decide = useCallback(
    async (status: Status) => {
      if (busy || !current) return;
      setBusy(true);
      setErr(null);
      try {
        const r = await api<{ event: { status: Status; reviewed_by: string } }>(`/events/${encodeURIComponent(uid)}/review`, {
          method: "POST",
          json: { status },
        });
        ev.setData((d) =>
          d ? { ...d, status: r.event.status, data: { ...(d.data ?? {}), reviewed_by: r.event.reviewed_by, reviewed_at: Date.now() / 1000 } } : d,
        );
        toast({
          tone: status === "tasdiqlangan" ? "ok" : "info",
          title: status === "tasdiqlangan" ? "Tasdiqlandi" : status === "rad_etilgan" ? "Xato signal deb belgilandi" : "Qaror bekor qilindi",
        });
        if (queue && status !== "yangi") await next();
      } catch (e) {
        setErr((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [busy, current, ev, next, queue, toast, uid],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const st = current?.status;
      const k = e.key.toLowerCase();
      if (k === "t" && st === "yangi") {
        e.preventDefault();
        void decide("tasdiqlangan");
      } else if (k === "x" && st === "yangi") {
        e.preventDefault();
        void decide("rad_etilgan");
      } else if (k === "u" && st && st !== "yangi") {
        e.preventDefault();
        void decide("yangi");
      } else if (e.key === "Escape") {
        router.push(queue ? "/" : "/events/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, decide, queue, router]);

  if (ev.error) {
    return (
      <div className="empty">
        <strong>Hodisa ochilmadi</strong>
        {ev.error}
        <p style={{ marginTop: 12 }}>
          <Link className="btn" href="/events/">
            Hodisalar ro&apos;yxati
          </Link>
        </p>
      </div>
    );
  }
  const e = current;
  if (!e) return <p className="muted">Yuklanmoqda…</p>;

  const d = (e.data ?? {}) as Record<string, unknown>;
  const reasons = Array.isArray(d.reasons) ? (d.reasons as unknown[]).map(String) : [];
  const clip = clipUrl(e.clip_file);
  const thumb = clipUrl(e.thumb_file);
  const tone = scoreTone(e.score);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{KIND_UZ[e.kind] ?? (e.kind || "Shubhali harakat")}</h1>
          <p className="muted">
            {e.branch_name}, {e.source_name || "kamera"}, {when(e.created_at, true)}
          </p>
        </div>
        <div className="ev-nav">
          {queue ? (
            <button className="btn" onClick={() => void next()} disabled={busy}>
              O&apos;tkazib yuborish
            </button>
          ) : null}
          <Link className="btn quiet" href={queue ? "/" : "/events/"}>
            Ro&apos;yxatga qaytish <kbd>Esc</kbd>
          </Link>
        </div>
      </div>
      <div className="ev">
        <div className="ev-screen">
          {clip ? (
            <video key={clip} src={clip} poster={thumb ?? undefined} controls autoPlay muted playsInline preload="auto" data-testid="clip" />
          ) : thumb ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={thumb} alt="Hodisa kadri" />
          ) : (
            <div style={{ textAlign: "center", padding: 24 }}>
              <IconCamera width={40} height={40} />
              <p style={{ marginTop: 8 }}>Video hali kelmagan</p>
              <p className="small">Do&apos;kon kompyuteri yuborishi bilan shu yerda o&apos;zi paydo bo&apos;ladi.</p>
            </div>
          )}
        </div>
        <aside className="ev-side">
          <div className="panel">
            <div className="ev-score">
              <b className={`score ${tone}`}>{Math.round(e.score)}</b>
              <span>
                ball{typeof d.level === "string" && d.level ? `, ${d.level} xavf` : ""}
              </span>
            </div>
            {reasons.length ? (
              <>
                <h3 style={{ marginTop: 16, marginBottom: 6 }}>Nima uchun belgilandi</h3>
                <ul className="reasons">
                  {reasons.slice(0, 6).map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>

          <div className="panel" style={{ display: "grid", gap: 12 }}>
            {e.status === "yangi" ? (
              <>
                <h3>Videoni ko&apos;rib qaror qiling</h3>
                <div className="decide">
                  <button className="btn confirm" onClick={() => void decide("tasdiqlangan")} disabled={busy} data-testid="confirm">
                    <IconCheck /> Tasdiqlash <kbd>T</kbd>
                  </button>
                  <button className="btn reject" onClick={() => void decide("rad_etilgan")} disabled={busy} data-testid="reject">
                    <IconX /> Xato signal <kbd>X</kbd>
                  </button>
                </div>
                <p className="disclaimer">
                  {isSafety(e.kind)
                    ? "Xavfsizlik signali: Tasdiqlash — haqiqatan shunday bo'lgan, Xato signal — bo'lmagan."
                    : "Bu ayblov emas. Tasdiqlash — videoda shubhali harakat haqiqatan borligini bildiradi."}
                </p>
              </>
            ) : (
              <>
                <div className={`verdict ${e.status}`} data-testid="verdict">
                  {STATUS_UZ[e.status]}
                  {typeof d.reviewed_by === "string" && d.reviewed_by ? (
                    <div className="note-ok">
                      {d.reviewed_by}
                      {typeof d.reviewed_at === "number" ? `, ${when(d.reviewed_at)}` : ""}
                    </div>
                  ) : null}
                </div>
                <button className="btn" onClick={() => void decide("yangi")} disabled={busy}>
                  <IconUndo /> Qarorni bekor qilish <kbd>U</kbd>
                </button>
              </>
            )}
            {err ? <p className="error">{err}</p> : null}
            <p className="note-ok">Qaror do&apos;kondagi kompyuterga va Telegram xabarlariga ham yetkaziladi.</p>
          </div>

          <dl className="facts panel">
            <dt>Filial</dt>
            <dd>{e.branch_name}</dd>
            <dt>Kamera</dt>
            <dd>{e.source_name || "—"}</dd>
            <dt>Vaqt</dt>
            <dd>{when(e.created_at, true)}</dd>
            <dt>Holat</dt>
            <dd>
              <span className={`pill ${e.status}`}>{STATUS_UZ[e.status]}</span>
            </dd>
            {d.job_id !== undefined && d.job_id !== null ? (
              <>
                <dt>Manba</dt>
                <dd>Yuklangan videoni tahlil qilish</dd>
              </>
            ) : null}
            <dt>Raqami</dt>
            <dd>{e.uid}</dd>
          </dl>
        </aside>
      </div>
    </>
  );
}
