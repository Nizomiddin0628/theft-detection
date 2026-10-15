"use client";

import { useState, type FormEvent } from "react";

import { useSession } from "@/components/Shell";
import { api, type Branch, type HealthRow } from "@/lib/api";
import { ago, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useData, useInterval } from "@/lib/useData";

function SecretBox({ title, token, onClose }: { title: string; token: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`EDGE_TOKEN=${token}`);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="secret" role="alert" data-testid="token-box">
      <h3>{title}</h3>
      <p>
        Bu token <b>faqat hozir</b> ko&apos;rinadi. Do&apos;kondagi kompyuterda Hushyor AI papkasidagi <code style={{ display: "inline", padding: "0 4px" }}>.env</code> fayliga
        yozing va dasturni qayta ishga tushiring:
      </p>
      <code data-testid="token">EDGE_TOKEN={token}</code>
      <div className="inline-confirm">
        <button className="btn small" onClick={copy}>
          {copied ? "Nusxa olindi" : "Nusxa olish"}
        </button>
        <button className="btn small primary" onClick={onClose}>
          Saqladim, yopish
        </button>
      </div>
    </div>
  );
}

function CreateBranch({ onDone }: { onDone: (name: string, token: string) => void }) {
  const [name, setName] = useState("");
  const [dev, setDev] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ device_token: string; branch: { name: string } }>("/branches", {
        method: "POST",
        json: { name: name.trim(), device_id: dev.trim(), address: address.trim() },
      });
      setName("");
      setDev("");
      setAddress("");
      onDone(r.branch.name, r.device_token);
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="panel" onSubmit={submit} style={{ display: "grid", gap: 14 }} aria-label="Filial qo'shish">
      <h2>Filial qo&apos;shish</h2>
      <div className="form-grid">
        <label className="field">
          <span>Filial nomi</span>
          <input className="input" name="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Chilonzor do'koni" required />
        </label>
        <label className="field">
          <span>
            Qurilma identifikatori <span className="hint">(edge paneli: Tizim holati, Bulut)</span>
          </span>
          <input
            className="input"
            name="device_id"
            value={dev}
            onChange={(e) => setDev(e.target.value)}
            pattern="[A-Za-z0-9_\-]{1,64}"
            placeholder="dokon-1a2b3c"
            required
          />
        </label>
        <label className="field">
          <span>
            Manzil <span className="hint">(ixtiyoriy)</span>
          </span>
          <input className="input" name="address" value={address} onChange={(e) => setAddress(e.target.value)} />
        </label>
        <button className="btn primary" type="submit" disabled={busy || !name.trim() || !dev.trim()}>
          {busy ? "Yaratilmoqda…" : "Filialni yaratish"}
        </button>
      </div>
      {err ? <p className="error">{err}</p> : null}
    </form>
  );
}

function History({ branchId }: { branchId: number }) {
  const h = useData(() => api<{ health: HealthRow[] }>(`/branches/${branchId}/cameras`).then((r) => r.health), [branchId]);
  if (h.loading) return <p className="muted small">Yuklanmoqda…</p>;
  if (!h.data?.length) return <p className="muted small">Kamera uzilishlari qayd etilmagan.</p>;
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Vaqt</th>
          <th>Kamera</th>
          <th>Holat</th>
          <th>Sabab</th>
        </tr>
      </thead>
      <tbody>
        {h.data.slice(0, 15).map((r) => (
          <tr key={r.key}>
            <td style={{ whiteSpace: "nowrap" }}>{when(r.t)}</td>
            <td>{r.source_name}</td>
            <td>
              <span className={r.state === "down" ? "pill offline" : "pill online"}>{r.state === "down" ? "Ishlamadi" : "Tiklandi"}</span>
            </td>
            <td className="muted">{r.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BranchCard({ b, owner, onToken }: { b: Branch; owner: boolean; onToken: (name: string, t: string) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [hist, setHist] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cams = b.cameras.filter((c) => c.kind !== "file");
  const rotate = async () => {
    setErr(null);
    try {
      const r = await api<{ device_token: string }>(`/branches/${b.id}/token`, { method: "POST", json: {} });
      setConfirm(false);
      onToken(b.name, r.device_token);
    } catch (x) {
      setErr((x as Error).message);
    }
  };
  return (
    <section className="panel" id={`b${b.id}`} data-testid="branch">
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div>
          <h2>{b.name}</h2>
          <p className="muted small">{b.address || "Manzil kiritilmagan"}</p>
        </div>
        {b.last_seen ? (
          <span className={b.online ? "pill online" : "pill offline"}>{b.online ? "Aloqada" : `Aloqa yo'q, ${ago(b.last_seen)}`}</span>
        ) : (
          <span className="pill yangi">Hali ulanmagan</span>
        )}
      </div>
      <dl className="facts">
        <dt>Qurilma</dt>
        <dd>{b.device_id}</dd>
        <dt>Oxirgi aloqa</dt>
        <dd>{b.last_seen ? `${when(b.last_seen, true)} (${ago(b.last_seen)})` : "Hali ulanmagan"}</dd>
        <dt>Dastur versiyasi</dt>
        <dd>{b.version || "—"}</dd>
        <dt>Yuborilishi kutilayotgan</dt>
        <dd>{b.pending ?? 0} ta yozuv</dd>
        <dt>24 soatdagi signallar</dt>
        <dd>
          {b.events_24h}, tekshirilmagan {b.pending_review}
        </dd>
        {b.clock_skew !== null && b.clock_skew !== undefined && Math.abs(b.clock_skew) > 60 ? (
          <>
            <dt>Soat farqi</dt>
            <dd className="error">Do&apos;kon kompyuteri soati {Math.round(Math.abs(b.clock_skew) / 60)} daqiqa farq qiladi</dd>
          </>
        ) : null}
      </dl>
      <h3 style={{ margin: "18px 0 8px" }}>Kameralar</h3>
      {cams.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Kamera</th>
                <th>Holat</th>
              </tr>
            </thead>
            <tbody>
              {cams.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>
                    <span className={c.state === "ishlayapti" ? "pill online" : "pill offline"}>{c.state || "noma'lum"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted small">Kamera ma&apos;lumoti hali kelmagan.</p>
      )}
      <div className="inline-confirm" style={{ marginTop: 14 }}>
        <button className="btn small" onClick={() => setHist((v) => !v)} aria-expanded={hist}>
          {hist ? "Uzilishlar tarixini yashirish" : "Uzilishlar tarixi"}
        </button>
        {owner ? (
          confirm ? (
            <span className="inline-confirm">
              <span className="small">Eski token ishlamay qoladi. Do&apos;kon kompyuteriga yangisini yozish kerak.</span>
              <button className="btn small danger" onClick={rotate}>
                Ha, tokenni yangilash
              </button>
              <button className="btn small quiet" onClick={() => setConfirm(false)}>
                Bekor qilish
              </button>
            </span>
          ) : (
            <button className="btn small quiet" onClick={() => setConfirm(true)}>
              Tokenni yangilash
            </button>
          )
        ) : null}
      </div>
      {err ? <p className="error">{err}</p> : null}
      {hist ? (
        <div style={{ marginTop: 12 }}>
          <History branchId={b.id} />
        </div>
      ) : null}
    </section>
  );
}

export default function BranchesPage() {
  const { me } = useSession();
  const owner = me.user.role === "owner";
  const [secret, setSecret] = useState<{ title: string; token: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const list = useData(() => api<{ branches: Branch[] }>("/branches").then((r) => r.branches));
  const show = (title: string, token: string) => {
    setSecret({ title, token });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  useLive((m) => {
    if (m.kind === "branch" || m.kind === "health") list.reload();
  });
  useInterval(() => list.reload(0), 30000);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Filiallar</h1>
          <p className="muted">Har bir filial — do&apos;kondagi bitta kompyuter va unga ulangan kameralar.</p>
        </div>
        {owner && !adding ? (
          <button className="btn primary" onClick={() => setAdding(true)}>
            Filial qo&apos;shish
          </button>
        ) : null}
      </div>
      {secret ? (
        <div style={{ marginBottom: 16 }}>
          <SecretBox title={secret.title} token={secret.token} onClose={() => setSecret(null)} />
        </div>
      ) : null}
      {owner && adding ? (
        <div style={{ marginBottom: 16 }}>
          <CreateBranch
            onDone={(name, token) => {
              setAdding(false);
              show(`"${name}" filiali yaratildi`, token);
              list.reload(0);
            }}
          />
        </div>
      ) : null}
      {list.error ? <p className="error">{list.error}</p> : null}
      {!list.loading && !list.data?.length ? (
        <div className="empty">
          <strong>Hali filial yo&apos;q</strong>
          {owner ? "Yuqoridagi tugma bilan birinchi do'konni qo'shing." : "Kompaniya egasi sizga filial biriktirishi kerak."}
        </div>
      ) : null}
      <div className="stack">
        {(list.data ?? []).map((b) => (
          <BranchCard key={b.id} b={b} owner={owner} onToken={(name, token) => show(`"${name}" uchun yangi token`, token)} />
        ))}
      </div>
    </>
  );
}
