"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";

import { IconShield } from "@/components/Icons";
import ThemeToggle from "@/components/ThemeToggle";
import { api } from "@/lib/api";

function safeNext(v: string | null): string {
  // Faqat o'z panelimiz (ochiq yo'naltirish bo'lmasin). Brauzer URL'ni qanday tushunsa, shunday tekshiriladi va
  // TO'LIQ manzil qaytariladi: "/\t/evil.com", "/.//evil.com" kabi hiylalar boshqa saytga olib chiqmaydi.
  if (!v || !v.startsWith("/")) return "/";
  try {
    const u = new URL(v, window.location.origin);
    // "//evil.com" yo'li ham rad etiladi (ba'zi joylarda protokolsiz manzil deb o'qiladi)
    if (u.origin !== window.location.origin || u.pathname.startsWith("//") || u.pathname.startsWith("/login")) return "/";
    return u.href;
  } catch {
    return "/";
  }
}

function LoginForm() {
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    // Sessiya bor bo'lsa — to'g'ridan-to'g'ri panelga. Demo serverda (python -m cloud.demo) login/parol tayyor turadi
    api<{ authenticated: boolean; demo?: boolean; demo_login?: { email: string; password: string } }>("/auth/status")
      .then((r) => {
        if (r.authenticated) window.location.replace(next);
        else if (r.demo && r.demo_login) {
          setDemo(true);
          setEmail(r.demo_login.email);
          setPassword(r.demo_login.password);
        }
      })
      .catch(() => undefined);
  }, [next]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", { method: "POST", json: { email, password, session: "cookie" } });
      window.location.replace(next);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="login-form" onSubmit={submit} noValidate>
      <h2>Panelga kirish</h2>
      {demo ? (
        <p className="demo-note" data-testid="demo-login">
          <strong>DEMO</strong> — login va parol yozib qo&apos;yilgan, &quot;Kirish&quot; ni bosing.
        </p>
      ) : null}
      <label className="field">
        <span>Email</span>
        <input
          className="input"
          type="email"
          name="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
        />
      </label>
      <label className="field">
        <span>Parol</span>
        <input
          className="input"
          type="password"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <button className="btn primary" type="submit" disabled={busy || !email || !password}>
        {busy ? "Tekshirilmoqda…" : "Kirish"}
      </button>
      <p className="muted small">Parolni unutdingizmi? Kompaniya egasi <b>Xodimlar</b> sahifasida sizga yangi parol berib qo&apos;yadi.</p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="login">
      <section className="login-side">
        <div className="login-brand">
          <IconShield />
          Hushyor AI
        </div>
        <div>
          <h1>Har bir signalni odam tekshiradi</h1>
          <p>
            Kameralar shubhali harakatni belgilaydi, siz videoni ko&apos;rib qaror qilasiz. Barcha filiallar, kameralar va
            javobsiz hodisalar bir joyda.
          </p>
        </div>
        <div className="login-wall" aria-hidden="true">
          <span />
          <span className="hot" />
          <span />
          <span />
          <span />
          <span className="hot" />
        </div>
      </section>
      <section className="login-form-wrap">
        <ThemeToggle className="login-theme" />
        <Suspense>
          <LoginForm />
        </Suspense>
      </section>
    </main>
  );
}
