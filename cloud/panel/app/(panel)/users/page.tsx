"use client";

import { useRef, useState, type FormEvent } from "react";

import { useSession } from "@/components/Shell";
import { api, AUDIT_UZ, ROLE_UZ, type AuditRow, type Branch, type Role } from "@/lib/api";
import { when } from "@/lib/format";
import { useData } from "@/lib/useData";

interface UserRow {
  id: number;
  email: string;
  name: string;
  role: Role;
  active: boolean;
  created_at: number;
  branch_ids: number[];
}

const ROLE_HINT: Record<Role, string> = {
  owner: "Hamma filiallar, xodimlar va tokenlar",
  manager: "Biriktirilgan filiallar: hodisalar, ko'rsatkichlar, uzilishlar haqida xabar",
  guard: "Biriktirilgan filiallar: hodisalarni tekshirish",
};

function CreateUser({ branches, onDone }: { branches: Branch[]; onDone: () => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("guard");
  const [bids, setBids] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/users", { method: "POST", json: { name: name.trim(), email: email.trim(), password, role, branch_ids: role === "owner" ? [] : bids } });
      onDone();
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const needBranch = role !== "owner" && bids.length === 0;
  return (
    <form className="panel" onSubmit={submit} style={{ display: "grid", gap: 14 }} aria-label="Xodim qo'shish">
      <h2>Xodim qo&apos;shish</h2>
      <div className="form-grid">
        <label className="field">
          <span>Ismi</span>
          <input className="input" name="name" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Email (login)</span>
          <input className="input" name="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="field">
          <span>
            Parol <span className="hint">(kamida 8 belgi)</span>
          </span>
          <input className="input" name="password" type="text" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required />
        </label>
        <label className="field">
          <span>Roli</span>
          <select className="select" name="role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="guard">{ROLE_UZ.guard}</option>
            <option value="manager">{ROLE_UZ.manager}</option>
            <option value="owner">{ROLE_UZ.owner}</option>
          </select>
        </label>
      </div>
      <p className="muted small">{ROLE_HINT[role]}</p>
      {role !== "owner" ? (
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small" style={{ fontWeight: 500, marginBottom: 6 }}>
            Qaysi filiallarni ko&apos;radi
          </legend>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {branches.map((b) => (
              <label key={b.id} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={bids.includes(b.id)}
                  onChange={(e) => setBids((xs) => (e.target.checked ? [...xs, b.id] : xs.filter((x) => x !== b.id)))}
                />
                {b.name}
              </label>
            ))}
            {!branches.length ? <span className="muted small">Avval filial qo&apos;shing.</span> : null}
          </div>
        </fieldset>
      ) : null}
      {err ? <p className="error">{err}</p> : null}
      <div>
        <button className="btn primary" type="submit" disabled={busy || !email.trim() || password.length < 8 || needBranch}>
          {busy ? "Qo'shilmoqda…" : "Xodimni qo'shish"}
        </button>
      </div>
    </form>
  );
}

function Deactivate({ u, onDone }: { u: UserRow; onDone: () => void }) {
  const [ask, setAsk] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!ask)
    return (
      <button className="btn small quiet" onClick={() => setAsk(true)}>
        O&apos;chirish
      </button>
    );
  return (
    <span className="inline-confirm">
      <button
        className="btn small danger"
        onClick={async () => {
          try {
            await api(`/users/${u.id}/deactivate`, { method: "POST", json: {} });
            onDone();
          } catch (x) {
            setErr((x as Error).message);
          }
        }}
      >
        Ha, kirishini yopish
      </button>
      <button className="btn small quiet" onClick={() => setAsk(false)}>
        Bekor qilish
      </button>
      {err ? <span className="error">{err}</span> : null}
    </span>
  );
}

function BranchChecks({ branches, value, onChange }: { branches: Branch[]; value: number[]; onChange: (v: number[]) => void }) {
  return (
    <div className="checks">
      {branches.map((b) => (
        <label key={b.id}>
          <input
            type="checkbox"
            checked={value.includes(b.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, b.id] : value.filter((x) => x !== b.id))}
          />
          {b.name}
        </label>
      ))}
    </div>
  );
}

function EditUser({ u, self, branches, onDone, onCancel }: { u: UserRow; self: boolean; branches: Branch[]; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(u.name);
  const [role, setRole] = useState<Role>(u.role);
  const [bids, setBids] = useState<number[]>(u.branch_ids);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api(`/users/${u.id}/update`, { method: "POST", json: { name: name.trim(), role, branch_ids: role === "owner" ? [] : bids } });
      onDone();
    } catch (x) {
      setMsg({ ok: false, text: (x as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const resetPw = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ closed_sessions: number }>(`/users/${u.id}/password`, { method: "POST", json: { password: pw } });
      setPw("");
      setMsg({ ok: true, text: `Yangi parol o'rnatildi${r.closed_sessions ? `, ${r.closed_sessions} ta kirish yopildi` : ""}. Xodimga parolni shaxsan ayting.` });
    } catch (x) {
      setMsg({ ok: false, text: (x as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="user-edit" onSubmit={save} aria-label={`${u.email} — o'zgartirish`}>
      <div className="form-grid">
        <label className="field">
          <span>Ismi</span>
          <input className="input" name="name" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Roli</span>
          <select className="select" name="role" value={role} disabled={self} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="guard">{ROLE_UZ.guard}</option>
            <option value="manager">{ROLE_UZ.manager}</option>
            <option value="owner">{ROLE_UZ.owner}</option>
          </select>
        </label>
      </div>
      {role !== "owner" ? <BranchChecks branches={branches} value={bids} onChange={setBids} /> : null}
      <div className="row-actions" style={{ justifyContent: "flex-start" }}>
        <button className="btn small primary" type="submit" disabled={busy || (role !== "owner" && bids.length === 0)}>
          Saqlash
        </button>
        <button className="btn small quiet" type="button" onClick={onCancel}>
          Bekor qilish
        </button>
      </div>
      {!self ? (
        <div className="form-grid">
          <label className="field">
            <span>
              Yangi parol berish <span className="hint">(xodim parolni unutgan bo&apos;lsa)</span>
            </span>
            <input className="input" name="new-password" type="text" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={8} />
          </label>
          <div>
            <button className="btn small" type="button" disabled={busy || pw.length < 8} onClick={resetPw}>
              Parolni o&apos;rnatish
            </button>
          </div>
        </div>
      ) : null}
      {msg ? <p className={msg.ok ? "small" : "error"}>{msg.text}</p> : null}
    </form>
  );
}

function AuditLog({ users }: { users: UserRow[] }) {
  const [action, setAction] = useState("");
  const [items, setItems] = useState<AuditRow[]>([]);
  const [more, setMore] = useState(false);
  const reqSeq = useRef(0);
  const load = async (before?: number) => {
    const q = new URLSearchParams({ limit: "100" });
    if (action) q.set("action", action);
    if (before) q.set("before_id", String(before));
    const my = ++reqSeq.current;
    const r = await api<{ items: AuditRow[] }>(`/audit?${q}`);
    if (my !== reqSeq.current) return r.items;    // 3.2.0: filtr tez almashsa — eski javob yangisining ustiga yozmasin
    setItems((xs) => (before ? [...xs, ...r.items] : r.items));
    setMore(r.items.length === 100);
    return r.items;
  };
  const data = useData(() => load(), [action]);
  const uname = new Map(users.map((u) => [u.id, u.name || u.email]));
  const bad = new Set(["login_fail", "password_fail", "user_deactivate"]);
  return (
    <div className="section" id="jurnal">
      <div className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h2>Jurnal</h2>
          <p className="muted small">Kim, qachon, qayerdan kirdi va nima qildi: kirish, xodim va token o&apos;zgarishlari, tekshirish, klip ko&apos;rish.</p>
        </div>
        <select className="select" style={{ width: "auto" }} aria-label="Amal turi" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Hamma amallar</option>
          {Object.entries(AUDIT_UZ).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </div>
      {data.error ? <p className="error">{data.error}</p> : null}
      <div className="table-wrap">
        <table className="data audit" data-testid="audit">
          <thead>
            <tr>
              <th>Vaqt</th>
              <th>Kim</th>
              <th>Amal</th>
              <th>Nima</th>
              <th>Manzil</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td className="small">{when(r.t, true)}</td>
                <td className="small">{(r.user_id && uname.get(r.user_id)) || r.actor || "—"}</td>
                <td className={bad.has(r.action) ? "bad small" : "small"}>{AUDIT_UZ[r.action] ?? r.action}</td>
                <td className="small">{r.target}</td>
                <td className="ip">{r.ip}</td>
              </tr>
            ))}
            {!items.length && !data.loading ? (
              <tr>
                <td colSpan={5} className="muted small">
                  Yozuv yo&apos;q
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {more ? (
        <button className="btn small quiet" style={{ marginTop: 10 }} onClick={() => void load(items[items.length - 1]?.id)}>
          Yana ko&apos;rsatish
        </button>
      ) : null}
    </div>
  );
}

export default function UsersPage() {
  const { me } = useSession();
  const [adding, setAdding] = useState(false);
  const owner = me.user.role === "owner";
  const [editing, setEditing] = useState<number | null>(null);
  // Egasi bo'lmasa so'rov yuborilmaydi (403 bo'lardi)
  const users = useData(() => (owner ? api<{ users: UserRow[] }>("/users").then((r) => r.users) : Promise.resolve([] as UserRow[])));
  const branches = useData(() => (owner ? api<{ branches: Branch[] }>("/branches").then((r) => r.branches) : Promise.resolve([] as Branch[])));
  if (!owner) {
    return (
      <div className="empty">
        <strong>Bu bo&apos;lim faqat kompaniya egasi uchun</strong>
        Xodim qo&apos;shish yoki o&apos;chirish kerak bo&apos;lsa, egasiga murojaat qiling.
      </div>
    );
  }
  const bname = new Map((branches.data ?? []).map((b) => [b.id, b.name]));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Xodimlar</h1>
          <p className="muted">Kim panelga kira oladi va qaysi filiallarni ko&apos;radi.</p>
        </div>
        {!adding ? (
          <button className="btn primary" onClick={() => setAdding(true)}>
            Xodim qo&apos;shish
          </button>
        ) : null}
      </div>
      {adding ? (
        <div style={{ marginBottom: 16 }}>
          <CreateUser
            branches={branches.data ?? []}
            onDone={() => {
              setAdding(false);
              users.reload(0);
            }}
          />
        </div>
      ) : null}
      {users.error ? <p className="error">{users.error}</p> : null}
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Xodim</th>
              <th>Roli</th>
              <th>Filiallar</th>
              <th>Qo&apos;shilgan</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(users.data ?? []).flatMap((u) => [
              <tr key={u.id} style={u.active ? undefined : { opacity: 0.55 }} data-testid="user-row">
                <td>
                  {u.name || "—"}
                  <div className="small muted">{u.email}</div>
                </td>
                <td>{ROLE_UZ[u.role]}</td>
                <td className="small">{u.role === "owner" ? "Hammasi" : u.branch_ids.map((b) => bname.get(b) ?? `#${b}`).join(", ") || "—"}</td>
                <td className="small muted">{when(u.created_at)}</td>
                <td className="num">
                  <span className="row-actions">
                    {u.id === me.user.id ? <span className="small muted">Siz</span> : null}
                    {!u.active ? (
                      <>
                        <span className="small muted">O&apos;chirilgan</span>
                        <button
                          className="btn small quiet"
                          onClick={async () => {
                            await api(`/users/${u.id}/activate`, { method: "POST", json: {} }).catch(() => undefined);
                            users.reload(0);
                          }}
                        >
                          Qaytarish
                        </button>
                      </>
                    ) : (
                      <>
                        <button className="btn small quiet" onClick={() => setEditing(editing === u.id ? null : u.id)} aria-expanded={editing === u.id}>
                          O&apos;zgartirish
                        </button>
                        {u.id !== me.user.id ? <Deactivate u={u} onDone={() => users.reload(0)} /> : null}
                      </>
                    )}
                  </span>
                </td>
              </tr>,
              editing === u.id ? (
                <tr key={`${u.id}-edit`}>
                  <td colSpan={5}>
                    <EditUser
                      u={u}
                      self={u.id === me.user.id}
                      branches={branches.data ?? []}
                      onCancel={() => setEditing(null)}
                      onDone={() => {
                        setEditing(null);
                        users.reload(0);
                      }}
                    />
                  </td>
                </tr>
              ) : null,
            ])}
          </tbody>
        </table>
      </div>
      <AuditLog users={users.data ?? []} />
    </>
  );
}
