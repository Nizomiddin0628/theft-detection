"use client";
// 3.0.0 Video sinovi: video shu yerda yuklanadi, uni tanlangan filialning do'kon kompyuteri (AI o'sha yerda) tahlil
// qiladi; natija (signallar, rasm, klip) shu sahifada va Hodisalar'da. 3.1.0: video kadri ustiga zonalar chiziladi
// (navbat, sanash, bo'limlar) va natijada o'lchovlar chiqadi; signallar va yakuniy natija yuklaganga Telegram'da.
import Link from "next/link";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  clipUrl,
  isSafety,
  KIND_UZ,
  STATUS_UZ,
  type DrawnZone,
  type VideoTest,
  type VideoTestEvent,
  type VideoTestsResp,
} from "@/lib/api";
import { ago, when } from "@/lib/format";
import { useData, useInterval } from "@/lib/useData";

import Metrics from "./Metrics";
import ZoneEditor from "./ZoneEditor";
import "./videos.css";

const mb = (n: number) =>
  n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
const vtime = (t: number | null) => {
  if (t == null) return "";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `videoning ${m}:${String(s).padStart(2, "0")} soniyasi`;
};

function uploadVideo(
  file: File,
  branchId: number,
  zoneId: number | null,
  zones: DrawnZone[],
  onProgress: (f: number) => void,
): { done: Promise<VideoTest>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const q = new URLSearchParams({ branch_id: String(branchId), name: file.name });
  if (zones.length) q.set("zones", JSON.stringify(zones));
  else if (zoneId) q.set("zone_source_id", String(zoneId));
  const done = new Promise<VideoTest>((resolve, reject) => {
    xhr.open("POST", `/api/v1/video-tests?${q.toString()}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: { test?: VideoTest; detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText || "{}");
      } catch {
        body = {};
      }
      if (xhr.status >= 200 && xhr.status < 300 && body.test) resolve(body.test);
      else if (xhr.status === 401) {
        window.location.replace("/login/?next=" + encodeURIComponent("/videos/"));
        reject(new Error("Kirish talab qilinadi"));
      } else reject(new Error(typeof body.detail === "string" ? body.detail : `xato ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Server bilan aloqa uzildi. Internetni tekshirib, qayta yuklang."));
    xhr.onabort = () => reject(new Error("Yuklash bekor qilindi"));
    xhr.send(file);
  });
  return { done, abort: () => xhr.abort() };
}

function Upload({ data, onDone }: { data: VideoTestsResp; onDone: () => void }) {
  const usable = data.branches;
  const [branch, setBranch] = useState<string>("");
  const [zone, setZone] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [drawn, setDrawn] = useState<DrawnZone[]>([]);         // 3.1.0: video ustiga chizilgan zonalar
  const [prog, setProg] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!branch && usable.length) {
      const first = usable.find((b) => b.ok && b.online) ?? usable.find((b) => b.ok) ?? usable[0];
      setBranch(String(first.id));
    }
  }, [usable, branch]);
  // 3.2.0: yuklash davomida sahifani yopish/yangilash — brauzer ogohlantiradi (aks holda video jimgina yo'qolardi)
  const uploading = prog !== null;
  useEffect(() => {
    if (!uploading) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);
  const sel = usable.find((b) => String(b.id) === branch) ?? null;
  const maxBytes = data.limits.max_mb * 1024 * 1024;
  const tooBig = !!file && file.size > maxBytes;
  const draw = zone === "draw";
  const old31 = !!sel && !atLeast(sel.version, [3, 1, 0]);
  // zonalar URL parametrida ketadi — juda uzun bo'lsa (nginx so'rov qatori), yuklashdan oldin aytiladi
  const zonesJson = draw && drawn.length ? JSON.stringify(drawn) : "";
  const zonesTooLong = zonesJson.length > 3000 || encodeURIComponent(zonesJson).length > 6500;

  const start = async () => {
    if (!file || !sel) return;
    setErr(null);
    setOk(null);
    setProg(0);
    const up = uploadVideo(file, sel.id, zone && !draw ? Number(zone) : null, draw ? drawn : [], setProg);
    abortRef.current = up.abort;
    try {
      const t = await up.done;
      setOk(
        sel.online
          ? `«${t.filename}» yuklandi — do'kon kompyuteri uni bir necha soniyada oladi va tahlil qiladi.`
          : `«${t.filename}» yuklandi. Do'kon kompyuteri hozir aloqada emas — yoqilganda tahlil boshlanadi.`,
      );
      setFile(null);
      setDrawn([]);
      if (inputRef.current) inputRef.current.value = "";
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setProg(null);
      abortRef.current = null;
    }
  };

  if (!usable.length) {
    return (
      <div className="panel empty" data-testid="vt-no-branch">
        <strong>Hali birorta filial yo&apos;q</strong>
        Video tahlilini do&apos;kon kompyuteri qiladi. Avval <Link href="/branches/">Filiallar</Link> sahifasida filial
        qo&apos;shing va do&apos;kondagi (yoki noutbukdagi) Hushyor AI&apos;ni bulutga ulang.
      </div>
    );
  }
  return (
    <form
      className="panel vt-upload"
      aria-label="Video yuklash"
      onSubmit={(e) => {
        e.preventDefault();
        void start();
      }}
    >
      <h2>Video yuklash</h2>
      <div className="form-grid">
        <label className="field">
          <span>Filial (tahlilni qiladigan do&apos;kon kompyuteri)</span>
          <select
            className="select"
            name="branch"
            value={branch}
            onChange={(e) => {
              setBranch(e.target.value);
              setZone("");
            }}
            disabled={prog !== null}
          >
            {usable.map((b) => (
              <option key={b.id} value={b.id} disabled={!b.ok}>
                {b.name}
                {!b.ok ? " — kompyuter eski versiyada" : b.online ? "" : " — hozir aloqada emas"}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>
            Zonalar <span className="hint">(polka, kassa — qaysi kameraniki qo&apos;llansin)</span>
          </span>
          <select className="select" name="zone" value={zone} onChange={(e) => setZone(e.target.value)} disabled={prog !== null} data-testid="vt-zone-select">
            <option value="">Zonalarsiz (butun kadr)</option>
            <option value="draw" disabled={old31}>
              ✏️ Video ustida chizaman (navbat, sanash, bo&apos;limlar){old31 ? " — kompyuterni 3.1.0 ga yangilang" : ""}
            </option>
            {(sel?.cameras ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                «{c.name}» kamerasining zonalari
              </option>
            ))}
          </select>
        </label>
        <label className="field vt-file">
          <span>
            Video fayl <span className="hint">(ko&apos;pi bilan {data.limits.max_mb} MB: {data.limits.exts.map((x) => x.slice(1)).join(", ")})</span>
          </span>
          <input
            ref={inputRef}
            className="input"
            type="file"
            name="video"
            accept={[...data.limits.exts, "video/*"].join(",")}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setErr(null);
              setOk(null);
            }}
            disabled={prog !== null}
          />
        </label>
      </div>
      {draw ? <ZoneEditor file={file} zones={drawn} onChange={setDrawn} disabled={prog !== null} /> : null}
      {sel && !sel.ok ? (
        <p className="error">
          Bu filialning do&apos;kon kompyuteri {sel.version ? `${sel.version} versiyada` : "hali bulutga ulanmagan"} — video
          sinovi uchun uni 3.0.0 ga yangilang.
        </p>
      ) : null}
      {sel && sel.ok && !sel.online ? (
        <p className="vt-note">
          Do&apos;kon kompyuteri hozir aloqada emas. Video bulutda navbatda turadi va kompyuter yoqilib, bulutga ulanganda tahlil
          qilinadi (2 kun ichida).
        </p>
      ) : null}
      {tooBig ? <p className="error">Video juda katta: {mb(file!.size)} (ko&apos;pi bilan {data.limits.max_mb} MB).</p> : null}
      {zonesTooLong ? (
        <p className="error">Zonalar juda ko&apos;p yoki nuqtalari ko&apos;p — bir nechtasini o&apos;chiring yoki kamroq nuqta bilan chizing.</p>
      ) : null}
      {prog !== null ? (
        <div className="vt-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(prog * 100)} aria-label="Yuklanmoqda">
          <div className="vt-bar">
            <i style={{ width: `${Math.round(prog * 100)}%` }} />
          </div>
          <span>
            Bulutga yuklanmoqda… {Math.round(prog * 100)}%{file ? ` (${mb(file.size)})` : ""}
          </span>
          <button type="button" className="btn small" onClick={() => abortRef.current?.()}>
            To&apos;xtatish
          </button>
        </div>
      ) : (
        <div className="vt-actions">
          <button className="btn primary" type="submit" disabled={!file || !sel || !sel.ok || tooBig || zonesTooLong} data-testid="vt-submit">
            Yuklash va tahlil qilish
          </button>
          {file && !tooBig ? <span className="muted small">{file.name} · {mb(file.size)}</span> : null}
        </div>
      )}
      {err ? (
        <p className="error" role="alert" data-testid="vt-error">
          {err}
        </p>
      ) : null}
      {ok ? (
        <p className="vt-ok" role="status" data-testid="vt-ok">
          {ok}
        </p>
      ) : null}
    </form>
  );
}

function atLeast(v: string | null | undefined, min: number[]): boolean {
  const n = String(v || "").match(/\d+/g)?.map(Number) ?? [];
  for (let i = 0; i < min.length; i++) {
    const x = n[i] ?? 0;
    if (x !== min[i]) return x > min[i];
  }
  return true;
}

function Results({ id }: { id: number }) {
  const r = useData(() => api<{ test: VideoTest; events: VideoTestEvent[] }>(`/video-tests/${id}`), [id]);
  const t = r.data?.test;
  const evs = r.data?.events ?? [];
  // Hodisalar (va kliplar) holatdan keyinroq yetib kelishi mumkin — hammasi kelguncha so'rab turiladi
  const recent = !t?.done_at || Date.now() / 1000 - t.done_at < 600; // klipi umuman chiqmagan hodisa — abadiy so'ralmasin
  const waiting = !!t && (t.active || (recent && (evs.length < t.events || evs.some((e) => !e.clip_file))));
  useInterval(() => {
    if (waiting) r.reload(0);
  }, 5000);
  if (r.loading && !r.data) return <p className="muted small">Yuklanmoqda…</p>;
  if (r.error) return <p className="error">{r.error}</p>;
  const missing = t ? Math.max(0, t.events - evs.length) : 0;
  const metrics = t && t.status === "tayyor" ? <Metrics m={t.metrics} zones={t.zones?.length ?? 0} /> : null;
  if (!evs.length) {
    let msg = "Bu videoda shubhali harakat topilmadi.";
    if (t?.status === "xato") msg = `Tahlil qilinmadi: ${t.error || "xato"}`;
    else if (t?.status === "bekor") msg = "Sinov bekor qilindi.";
    else if (t?.active) msg = "Tahlil davom etmoqda — signallar shu yerda paydo bo'ladi.";
    else if (missing) msg = `${missing} ta signal topildi — do'kon kompyuteridan yetib kelmoqda…`;
    return (
      <>
        {metrics}
        <p className={t?.status === "xato" ? "error" : "muted small"} data-testid="vt-no-events">
          {msg}
        </p>
      </>
    );
  }
  return (
    <>
    {metrics}
    <div className="vt-events" data-testid="vt-events">
      {missing ? <p className="muted small vt-more">Yana {missing} ta signal yetib kelmoqda…</p> : null}
      {evs.map((e) => {
        const img = clipUrl(e.thumb_file);
        return (
          <Link key={e.uid} href={`/events/?uid=${encodeURIComponent(e.uid)}`} className="vt-ev">
            {img ? <img src={img} alt="" loading="lazy" /> : <span className="vt-ev-ph" aria-hidden />}
            <span className="vt-ev-body">
              <b>{KIND_UZ[e.kind] ?? e.kind}</b>
              <span className="muted small">
                {isSafety(e.kind) ? "xavfsizlik signali" : `ball ${Math.round(e.score)}`}
                {e.video_time != null ? ` · ${vtime(e.video_time)}` : ""}
              </span>
              <span className={`pill ${e.status}`}>{STATUS_UZ[e.status] ?? e.status}</span>
              {!e.clip_file ? <span className="muted small">klip hali kelmagan</span> : null}
            </span>
          </Link>
        );
      })}
    </div>
    </>
  );
}

export default function VideosPage() {
  const v = useData(() => api<VideoTestsResp>("/video-tests"), []);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const anyActive = useMemo(() => (v.data?.tests ?? []).some((t) => t.active), [v.data]);
  useInterval(() => v.reload(0), anyActive ? 4000 : 30000);

  const act = async (t: VideoTest, kind: "cancel" | "delete") => {
    setBusy(t.id);
    setErr(null);
    try {
      if (kind === "cancel") await api(`/video-tests/${t.id}/cancel`, { method: "POST", json: {} });
      else await api(`/video-tests/${t.id}`, { method: "DELETE" });
      if (open === t.id && kind === "delete") setOpen(null);
      v.reload(0);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Video sinovi</h1>
          <p className="muted">
            Do&apos;kon kamerasidan olingan (yoki istalgan) videoni yuklang — uni tanlangan filialning do&apos;kon kompyuteri
            tahlil qiladi: AI o&apos;sha yerda ishlaydi, bulut faqat videoni yetkazadi. Topilgan signallar rasm va klip bilan shu
            yerda, <Link href="/events/">Hodisalar</Link>da va Telegram&apos;ingizda («🧪 Video sinovi» belgisi bilan) chiqadi.
            Kassa navbati, kirganlar soni va bo&apos;limlarni o&apos;lchash uchun «Video ustida chizaman»ni tanlab, zonalarni
            chizing. Tahlildan keyin video bulutdan o&apos;chiriladi.
          </p>
        </div>
      </div>
      {v.error ? <p className="error">{v.error}</p> : null}
      {v.data && v.data.can_upload ? <Upload data={v.data} onDone={() => v.reload(0)} /> : null}
      <div className="panel" style={{ marginTop: 16 }}>
        <h2>Sinovlar</h2>
        {err ? (
          <p className="error" role="alert">
            {err}
          </p>
        ) : null}
        {v.loading && !v.data ? <p className="muted">Yuklanmoqda…</p> : null}
        {v.data && !v.data.tests.length ? (
          <p className="muted" data-testid="vt-empty">
            Hali video yuklanmagan.
          </p>
        ) : null}
        {v.data && v.data.tests.length ? (
          <table className="data vt-table" data-testid="vt-table">
            <thead>
              <tr>
                <th>Video</th>
                <th>Filial</th>
                <th>Holat</th>
                <th className="num">Signallar</th>
                <th>Yuklangan</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {v.data.tests.map((t) => (
                <Fragment key={t.id}>
                  <tr data-testid="vt-row" data-status={t.status}>
                    <td>
                      <b className="vt-name">{t.filename}</b>
                      <div className="muted small">
                        {mb(t.size)}
                        {t.duration ? ` · ${Math.round(t.duration)} s` : ""}
                        {t.zone_source_name ? ` · «${t.zone_source_name}» zonalari` : ""}
                        {t.zones?.length ? ` · ${t.zones.length} ta zona chizilgan` : ""}
                      </div>
                    </td>
                    <td>{t.branch_name}</td>
                    <td>
                      <span className={`vt-st ${t.status}`}>{t.status_uz}</span>
                      {t.active && (t.status === "ishlanmoqda" || t.status === "yuklanmoqda") ? (
                        <div className="vt-bar small" aria-label={`${Math.round(t.progress)}%`}>
                          <i style={{ width: `${Math.round(t.progress)}%` }} />
                        </div>
                      ) : null}
                      {t.active && (t.status === "ishlanmoqda" || t.status === "yuklanmoqda") ? (
                        <span className="muted small"> {Math.round(t.progress)}%</span>
                      ) : null}
                      {t.error && !t.active ? <div className="muted small vt-err">{t.error}</div> : null}
                    </td>
                    <td className="num">
                      <b>{t.status === "kutilmoqda" ? "—" : t.events}</b>
                    </td>
                    <td title={when(t.created_at)}>{ago(t.created_at)}</td>
                    <td className="vt-btns">
                      <button
                        className="btn small"
                        aria-expanded={open === t.id}
                        onClick={() => setOpen(open === t.id ? null : t.id)}
                        disabled={t.status === "kutilmoqda"}
                      >
                        {open === t.id ? "Yopish" : "Natija"}
                      </button>
                      {v.data!.can_upload && t.active ? (
                        <button className="btn small" disabled={busy === t.id} onClick={() => act(t, "cancel")}>
                          Bekor qilish
                        </button>
                      ) : null}
                      {v.data!.can_upload && !t.active ? (
                        <button className="btn small" disabled={busy === t.id} onClick={() => act(t, "delete")} aria-label={`${t.filename} — ro'yxatdan o'chirish`}>
                          O&apos;chirish
                        </button>
                      ) : null}
                    </td>
                  </tr>
                  {open === t.id ? (
                    <tr className="vt-res">
                      <td colSpan={6}>
                        <Results id={t.id} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </>
  );
}
