"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useSession } from "@/components/Shell";
import { api, type ChannelStats, type Channels, type Prefs, type SystemStatus, type Target } from "@/lib/api";
import { ago, secs } from "@/lib/format";
import { beep, getPref, setPref, unlockAudio } from "@/lib/live";
import { useData } from "@/lib/useData";

interface NotifyInfo {
  prefs: Prefs;
  defaults: Prefs;
  targets: Target[];
  channels: Channels;
  email: string;
}

const CH_UZ = { telegram: "Telegram", email: "Email", push: "Brauzer" } as const;

function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function TargetList({ items, onChange }: { items: Target[]; onChange: () => void }) {
  if (!items.length) return null;
  return (
    <ul className="targets">
      {items.map((t) => (
        <li key={t.id} data-testid={`target-${t.channel}`}>
          <span>
            {t.address}
            {!t.enabled ? <span className="bad"> o&apos;chirilgan{t.last_error ? `: ${t.last_error}` : ""}</span> : null}
            {t.enabled && t.last_error ? <span className="bad"> oxirgi xato: {t.last_error}</span> : null}
            {t.enabled && t.last_ok ? <span className="small muted"> oxirgi xabar {ago(t.last_ok)}</span> : null}
          </span>
          <button
            className="btn small quiet"
            onClick={async () => {
              await api(`/me/targets/${t.id}/delete`, { method: "POST", json: {} }).catch(() => undefined);
              onChange();
            }}
          >
            Uzish
          </button>
        </li>
      ))}
    </ul>
  );
}

function Telegram({ info, reload }: { info: NotifyInfo; reload: () => void }) {
  const [link, setLink] = useState<{ link: string | null; code: string; bot: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const items = info.targets.filter((t) => t.channel === "telegram");
  const enabled = info.channels.telegram.enabled;
  const count = items.filter((t) => t.enabled).length;

  // Havola berilgach, foydalanuvchi botda /start bosguncha har 3 s tekshiriladi (10 daqiqagacha)
  useEffect(() => {
    if (!link) return;
    const until = Date.now() + 600000;
    const id = window.setInterval(() => {
      if (Date.now() > until) window.clearInterval(id);
      reload();
    }, 3000);
    return () => window.clearInterval(id);
  }, [link, reload]);
  useEffect(() => {
    if (link && count > 0) setLink(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [count]);

  const start = async () => {
    setErr(null);
    try {
      setLink(await api("/me/telegram/link", { method: "POST", json: {} }));
    } catch (x) {
      setErr((x as Error).message);
    }
  };

  return (
    <div className="channel">
      <div>
        <h3>Telegram</h3>
        <p>
          {enabled
            ? `Bulut boti${info.channels.telegram.bot ? ` @${info.channels.telegram.bot}` : ""} hodisa haqida yozadi, video kelgach javob qilib yuboradi; tugmalar bilan shu yerdan tekshirasiz.`
            : "Bulut Telegram boti hali sozlanmagan (bulut serveri .env faylida CLOUD_TELEGRAM_BOT_TOKEN)."}
        </p>
      </div>
      {enabled ? (
        <button className="btn" onClick={start}>
          {count ? "Yana bir chatni ulash" : "Telegram'ni ulash"}
        </button>
      ) : null}
      {link ? (
        <div className="secret" style={{ gridColumn: "1 / -1" }} data-testid="tg-link">
          {link.link ? (
            <p>
              <a className="btn primary" href={link.link} target="_blank" rel="noopener noreferrer">
                Telegram&apos;da ochish
              </a>{" "}
              va <b>Start</b> tugmasini bosing. Havola 10 daqiqa amal qiladi.
            </p>
          ) : null}
          <p className="small">
            Yoki botga shu xabarni yuboring: <code style={{ display: "inline", padding: "0 6px" }}>/start {link.code}</code>
          </p>
          <p className="small muted">Ulanishi bilan bu yerda o&apos;zi ko&apos;rinadi.</p>
        </div>
      ) : null}
      {err ? <p className="error">{err}</p> : null}
      <TargetList items={items} onChange={reload} />
    </div>
  );
}

function Email({ info, reload }: { info: NotifyInfo; reload: () => void }) {
  const items = info.targets.filter((t) => t.channel === "email");
  const on = items.some((t) => t.enabled);
  const [err, setErr] = useState<string | null>(null);
  const enabled = info.channels.email.enabled;
  return (
    <div className="channel">
      <div>
        <h3>Email</h3>
        <p>{enabled ? `Xabarlar ${info.email} manziliga keladi.` : "Email yuborish sozlanmagan (bulut serveri .env faylida CLOUD_SMTP_HOST)."}</p>
      </div>
      {enabled ? (
        <input
          type="checkbox"
          className="switch"
          aria-label="Email xabarlari"
          checked={on}
          onChange={async (e) => {
            setErr(null);
            try {
              await api("/me/email", { method: "POST", json: { enabled: e.target.checked } });
              reload();
            } catch (x) {
              setErr((x as Error).message);
            }
          }}
        />
      ) : null}
      {err ? <p className="error">{err}</p> : null}
    </div>
  );
}

function Push({ info, reload }: { info: NotifyInfo; reload: () => void }) {
  const items = info.targets.filter((t) => t.channel === "push");
  const [state, setState] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const supported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext;
  const enabled = info.channels.push.enabled;

  const subscribe = async () => {
    setBusy(true);
    setState(null);
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") throw new Error("Brauzer bildirishnomaga ruxsat bermadi. Manzil satridagi qulf belgisidan ruxsat bering.");
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const { public_key } = await api<{ public_key: string }>("/push/key");
      const key = b64ToBytes(public_key);
      let sub = await reg.pushManager.getSubscription();
      if (sub) {
        // Server kaliti almashgan bo'lsa (bulut boshqa joyga ko'chirilgan) eski obuna ishlamaydi — yangisi olinadi
        const old = sub.options.applicationServerKey ? new Uint8Array(sub.options.applicationServerKey) : null;
        const same = !!old && old.length === key.length && old.every((v, i) => v === key[i]);
        if (!same) {
          await sub.unsubscribe();
          sub = null;
        }
      }
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await api("/me/push", { method: "POST", json: sub.toJSON() });
      setState("Shu brauzer ulandi.");
      reload();
    } catch (x) {
      setState((x as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="channel">
      <div>
        <h3>Brauzer</h3>
        <p>
          {!enabled
            ? `Brauzer xabarlari o'chirilgan${info.channels.push.error ? `: ${info.channels.push.error}` : ""}.`
            : !supported
              ? "Bu brauzer yoki manzil push xabarni qo'llamaydi: panel https:// orqali ochilgan bo'lishi kerak."
              : "Panel yopiq bo'lsa ham kompyuter yoki telefon ekranida xabar chiqadi."}
        </p>
        {state ? <p className="small">{state}</p> : null}
      </div>
      {enabled && supported ? (
        <button className="btn" onClick={subscribe} disabled={busy}>
          Shu brauzerda yoqish
        </button>
      ) : null}
      <TargetList items={items} onChange={reload} />
    </div>
  );
}

const PREF_ROWS: { key: keyof Omit<Prefs, "min_score">; title: string; hint: string }[] = [
  { key: "event", title: "Shubhali harakat", hint: "Kamera yangi signal berganda (Telegram'da video bilan)" },
  { key: "escalation", title: "Javobsiz hodisa", hint: "Hech kim belgilangan vaqtda tekshirmasa" },
  { key: "camera", title: "Kamera ishlamay qoldi", hint: "Kamera uzilsa va tiklansa" },
  { key: "offline", title: "Do'kon bilan aloqa uzildi", hint: "Do'kondagi kompyuter yoki internet o'chsa" },
];

function PrefsPanel({ info, reload, guard }: { info: NotifyInfo; reload: () => void; guard: boolean }) {
  const [p, setP] = useState<Prefs>(info.prefs);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => setP(info.prefs), [info.prefs]);
  const save = async (next: Prefs) => {
    setP(next);
    setSaved(null);
    try {
      await api("/me/notifications", { method: "PUT", json: { prefs: next } });
      setSaved("Saqlandi");
      reload();
    } catch (x) {
      setSaved((x as Error).message);
    }
  };
  return (
    <div className="panel">
      <h2>Nima haqida xabar beriladi</h2>
      {PREF_ROWS.filter((r) => !(guard && r.key === "escalation")).map((r) => (
        <label className="switch-row" key={r.key}>
          <span>
            {r.title}
            <p>{r.hint}</p>
          </span>
          <input type="checkbox" className="switch" checked={p[r.key]} onChange={(e) => void save({ ...p, [r.key]: e.target.checked })} data-testid={`pref-${r.key}`} />
        </label>
      ))}
      <div className="switch-row">
        <label htmlFor="minscore">
          Eng kichik ball
          <p>Shundan past ballli signallar uchun xabar kelmaydi (panelda baribir ko&apos;rinadi)</p>
        </label>
        <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <input
            id="minscore"
            type="range"
            min={0}
            max={100}
            step={5}
            value={p.min_score}
            onChange={(e) => setP({ ...p, min_score: Number(e.target.value) })}
            onPointerUp={() => void save(p)}
            onKeyUp={() => void save(p)}
          />
          <b style={{ width: 32, textAlign: "right" }}>{p.min_score}</b>
        </span>
      </div>
      {saved ? <p className="small muted">{saved}</p> : null}
    </div>
  );
}

function Stats() {
  const s = useData(() => api<{ channels: Record<string, ChannelStats> }>("/notify/stats?hours=24").then((r) => r.channels));
  if (!s.data) return s.error ? <p className="error">{s.error}</p> : null;
  return (
    <div className="panel">
      <h2>Yetkazish, oxirgi 24 soat</h2>
      <p className="muted small" style={{ margin: "4px 0 12px" }}>
        Kechikish — kamera signal berganidan xabar yetib borguncha (95% xabar shu vaqtdan tez yetgan).
      </p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Kanal</th>
              <th className="num">Yetkazildi</th>
              <th className="num">Xato</th>
              <th className="num">Navbatda</th>
              <th className="num">Kechikish, odatda</th>
              <th className="num">Kechikish, 95%</th>
            </tr>
          </thead>
          <tbody>
            {(Object.keys(CH_UZ) as (keyof typeof CH_UZ)[]).map((ch) => {
              const r = s.data![ch];
              if (!r) return null;
              return (
                <tr key={ch}>
                  <td>{CH_UZ[ch]}</td>
                  <td className="num">{r.sent}</td>
                  <td className="num">{r.failed}</td>
                  <td className="num">{r.pending}</td>
                  <td className="num">{secs(r.event_p50)}</td>
                  <td className="num">{secs(r.event_p95)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PasswordPanel() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [askAll, setAskAll] = useState(false);
  const mismatch = again.length > 0 && again !== next;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ closed_sessions: number }>("/me/password", { method: "POST", json: { current: cur, new: next } });
      setCur("");
      setNext("");
      setAgain("");
      setMsg({ ok: true, text: `Parol almashtirildi.${r.closed_sessions ? ` Boshqa qurilmalardagi ${r.closed_sessions} ta kirish yopildi.` : ""}` });
    } catch (x) {
      setMsg({ ok: false, text: (x as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="panel" onSubmit={submit} aria-label="Parolni almashtirish" style={{ display: "grid", gap: 12 }}>
      <h2>Kirish xavfsizligi</h2>
      <div className="form-grid">
        <label className="field">
          <span>Joriy parol</span>
          <input className="input" name="current" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required />
        </label>
        <label className="field">
          <span>
            Yangi parol <span className="hint">(kamida 8 belgi)</span>
          </span>
          <input className="input" name="new" type="password" autoComplete="new-password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} required />
        </label>
        <label className="field">
          <span>Yangi parol, yana bir marta</span>
          <input className="input" name="again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
        </label>
      </div>
      {mismatch ? <p className="error">Parollar mos emas</p> : null}
      {msg ? <p className={msg.ok ? "small" : "error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p> : null}
      <div className="inline-confirm">
        <button className="btn primary" type="submit" disabled={busy || !cur || next.length < 8 || next !== again}>
          {busy ? "Saqlanmoqda…" : "Parolni almashtirish"}
        </button>
        {!askAll ? (
          <button type="button" className="btn quiet" onClick={() => setAskAll(true)}>
            Hamma qurilmalardan chiqish
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn danger"
              onClick={async () => {
                await api("/auth/logout-all", { method: "POST", json: {} }).catch(() => undefined);
                window.location.replace("/login/");
              }}
            >
              Ha, hamma joydan chiqish
            </button>
            <button type="button" className="btn quiet" onClick={() => setAskAll(false)}>
              Bekor qilish
            </button>
          </>
        )}
      </div>
      <p className="muted small">Telefon yo&apos;qolsa yoki parol begonaga ma&apos;lum bo&apos;lsa — parolni almashtiring: boshqa qurilmalardagi kirishlar yopiladi.</p>
    </form>
  );
}

function ServerPanel() {
  const s = useData(() => api<SystemStatus>("/system"));
  if (!s.data) return s.error ? <p className="error">{s.error}</p> : null;
  const d = s.data;
  const oldBackup = d.backup_hours > 0 && (d.last_backup_age_h === null || d.last_backup_age_h > d.backup_hours * 2);
  const lowDisk = d.free_gb < Math.max(d.min_free_gb, 1) * 1.5;
  return (
    <div className="panel" data-testid="server-status">
      <h2>Bulut serveri</h2>
      <dl className="kv">
        <dt>Versiya</dt>
        <dd>{d.version}</dd>
        <dt>Baza</dt>
        <dd>{d.db === "postgresql" ? "PostgreSQL" : "SQLite"}</dd>
        <dt>Diskda bo&apos;sh joy</dt>
        <dd className={lowDisk ? "bad" : undefined}>{d.free_gb} GB</dd>
        <dt>Kliplar</dt>
        <dd>
          {d.clips} ta, {d.clips_mb} MB{" "}
          <span className="small muted">
            ({d.clip_days > 0 ? `${d.clip_days} kun saqlanadi, tasdiqlangani ${d.confirmed_days} kun` : "muddatsiz"})
          </span>
        </dd>
        <dt>Zaxira nusxa</dt>
        <dd className={oldBackup ? "bad" : undefined}>
          {d.backup_hours <= 0 ? "o'chirilgan" : d.last_backup_age_h === null ? "hali olinmagan" : `${d.last_backup_age_h} soat oldin`}
        </dd>
        <dt>Jonli ulanishlar</dt>
        <dd>{d.sse}</dd>
      </dl>
    </div>
  );
}

export default function SettingsPage() {
  const { me, toast } = useSession();
  const info = useData(() => api<NotifyInfo>("/me/notifications"));
  const [sound, setSound] = useState("1");
  useEffect(() => setSound(getPref("sound", "1")), []);
  const reload = info.reload;

  const test = async () => {
    try {
      const r = await api<{ count: number }>("/me/notifications/test", { method: "POST", json: {} });
      toast({ tone: "ok", title: `Sinov xabari yuborildi: ${r.count} ta manzil` });
    } catch (x) {
      toast({ tone: "error", title: (x as Error).message });
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sozlamalar</h1>
          <p className="muted">Bildirishnomalar sizga qayerga va nima haqida kelishi. Har bir xodim o&apos;zi uchun sozlaydi.</p>
        </div>
        <button className="btn" onClick={test}>
          Sinov xabarini yuborish
        </button>
      </div>
      {info.error ? <p className="error">{info.error}</p> : null}
      {info.data ? (
        <div className="two-col">
          <div className="panel">
            <h2>Qayerga keladi</h2>
            <Telegram info={info.data} reload={reload} />
            <Email info={info.data} reload={reload} />
            <Push info={info.data} reload={reload} />
          </div>
          <div style={{ display: "grid", gap: 16 }}>
            {/* Javobsiz hodisa eslatmasi menejer va egasiga boriladi — qo'riqchiga ko'rsatilmaydi */}
            <PrefsPanel info={info.data} reload={reload} guard={me.user.role === "guard"} />
            <div className="panel">
              <h2>Shu brauzer</h2>
              <label className="switch-row">
                <span>
                  Ovozli signal
                  <p>Panel ochiq turganda yangi hodisa kelsa qisqa ovoz chiqadi</p>
                </span>
                <input
                  type="checkbox"
                  className="switch"
                  checked={sound === "1"}
                  onChange={(e) => {
                    const v = e.target.checked ? "1" : "0";
                    setSound(v);
                    setPref("sound", v);
                    if (v === "1") {
                      unlockAudio();
                      window.setTimeout(beep, 50);
                    }
                  }}
                />
              </label>
            </div>
          </div>
        </div>
      ) : (
        <p className="muted">Yuklanmoqda…</p>
      )}
      <div className="section two-col">
        <PasswordPanel />
        {me.user.role === "owner" ? <ServerPanel /> : null}
      </div>
      {me.user.role !== "guard" ? (
        <div className="section">
          <Stats />
        </div>
      ) : null}
    </>
  );
}
