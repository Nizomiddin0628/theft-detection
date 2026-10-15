"use client";
// Panel ramkasi: kirishni tekshirish, chap menyu (telefonda pastki menyu), jonli oqim, xabarchalar (toast).
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, KIND_UZ, ROLE_UZ, type Me } from "@/lib/api";
import { beep, emitLive, getPref, unlockAudio, type LiveMsg } from "@/lib/live";
import { IconBell, IconChart, IconDoor, IconExit, IconList, IconPeople, IconShield, IconStore, IconWall } from "./Icons";

interface Toast {
  id: number;
  title: string;
  body?: string;
  href?: string;
  action?: string;
  tone?: "alert" | "ok" | "info" | "error";
}

interface SessionCtx {
  me: Me;
  toast: (t: Omit<Toast, "id">) => void;
  live: boolean;
}

const Ctx = createContext<SessionCtx | null>(null);

export function useSession(): SessionCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession — Shell ichida");
  return c;
}

// short — telefon ekranidagi pastki menyu uchun
const NAV = [
  { href: "/", label: "Bosh sahifa", short: "Bosh", icon: IconWall },
  { href: "/events/", label: "Hodisalar", short: "Hodisalar", icon: IconList },
  { href: "/branches/", label: "Filiallar", short: "Filiallar", icon: IconStore },
  { href: "/visits/", label: "Tashriflar", short: "Tashrif", icon: IconDoor },
  { href: "/metrics/", label: "Ko'rsatkichlar", short: "Grafiklar", icon: IconChart },
  { href: "/users/", label: "Xodimlar", short: "Xodimlar", icon: IconPeople, owner: true },
  { href: "/settings/", label: "Sozlamalar", short: "Sozlama", icon: IconBell },
];

export default function Shell({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [live, setLive] = useState(false);
  const seq = useRef(0);
  const pathname = usePathname();
  const router = useRouter();

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = ++seq.current;
    setToasts((xs) => [...xs.slice(-3), { ...t, id }]);
    window.setTimeout(() => setToasts((xs) => xs.filter((x) => x.id !== id)), t.tone === "alert" ? 12000 : 5000);
  }, []);

  useEffect(() => {
    api<Me & { authenticated: boolean }>("/auth/status")
      .then((r) => {
        if (r.authenticated) setMe(r);
        else window.location.replace("/login/?next=" + encodeURIComponent(window.location.pathname + window.location.search));
      })
      .catch((e: Error) => setFailed(e.message));
  }, []);

  // Ovoz: brauzer birinchi bosishdan keyin ruxsat beradi
  useEffect(() => {
    const on = () => unlockAudio();
    window.addEventListener("pointerdown", on, { once: true });
    window.addEventListener("keydown", on, { once: true });
    return () => {
      window.removeEventListener("pointerdown", on);
      window.removeEventListener("keydown", on);
    };
  }, []);

  // Jonli oqim
  useEffect(() => {
    if (!me) return;
    let es: EventSource | null = null;
    let timer: number | undefined;
    let stopped = false;
    let evicted = false;
    // Server shu tabni boshqa (yangiroq) tab foydasiga yopdi: brauzer o'zi qayta ulanmasin — tab yana ko'ringanda ulanadi
    const onVisible = () => {
      if (evicted && document.visibilityState === "visible" && !stopped) {
        evicted = false;
        open();
      }
    };
    const open = () => {
      es = new EventSource("/api/v1/stream");
      es.onopen = () => setLive(true);
      es.onerror = () => {
        setLive(false);
        if (es && es.readyState === EventSource.CLOSED && !stopped) {
          // Server rad etdi (masalan, sessiya tugagan): kirish sahifasiga
          api<{ authenticated: boolean }>("/auth/status")
            .then((r) => {
              if (!r.authenticated) window.location.replace("/login/");
            })
            .catch(() => undefined);
          timer = window.setTimeout(open, 5000);
        }
      };
      const handle = (kind: LiveMsg["kind"]) => (ev: MessageEvent) => {
        let data: Record<string, unknown> = {};
        try {
          data = JSON.parse(ev.data || "{}");
        } catch {
          data = {};
        }
        if (kind === "logout") {
          window.location.replace("/login/");
          return;
        }
        if (kind === "evicted") {
          evicted = true;
          es?.close();
          setLive(false);
          if (document.visibilityState === "visible") window.setTimeout(onVisible, 30000);
          return;
        }
        // Faqat jonli hodisa (server belgilaydi): uzilishdan keyin kelgan eski hodisalar ovoz chiqarmaydi
        if (kind === "event_new" && data.alert === true) {
          const score = Number(data.score || 0);
          const cam = String(data.source_name || "kamera");
          toast({
            tone: "alert",
            title: `Shubhali harakat: ${String(data.branch_name || "filial")}`,
            body: `${cam}, ${KIND_UZ[String(data.kind)] ?? "harakat"}, ball ${Math.round(score)}`,
            href: `/events/?uid=${encodeURIComponent(String(data.uid))}`,
            action: "Ko'rish",
          });
          if (getPref("sound", "1") === "1") beep();
        }
        if (kind === "branch") {
          toast({
            tone: data.online ? "ok" : "error",
            title: data.online ? "Do'kon bilan aloqa tiklandi" : "Do'kon bilan aloqa uzildi",
            href: "/branches/",
            action: "Filiallar",
          });
        }
        if (kind === "health" && data.state === "down") {
          toast({ tone: "error", title: `Kamera ishlamayapti: ${String(data.source_name || "")}`, href: "/branches/" });
        }
        emitLive({ kind, data });
      };
      (["event_new", "event_update", "branch", "health", "logout", "evicted"] as const).forEach((k) =>
        es!.addEventListener(k, handle(k) as EventListener),
      );
    };
    open();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      es?.close();
    };
  }, [me, toast]);

  const logout = async () => {
    // Umumiy kompyuterda: shu brauzerdagi push obunasi ham uziladi (keyingi xodimga eski xabarlar kelmasin)
    let endpoint: string | undefined;
    try {
      const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration("/") : undefined;
      const sub = reg ? await reg.pushManager.getSubscription() : null;
      if (sub) {
        endpoint = sub.endpoint;
        await sub.unsubscribe();
      }
    } catch {
      endpoint = undefined;
    }
    try {
      await api("/auth/logout", { method: "POST", json: endpoint ? { push_endpoint: endpoint } : {} });
    } finally {
      window.location.replace("/login/");
    }
  };

  const ctx = useMemo(() => (me ? { me, toast, live } : null), [me, toast, live]);

  if (failed) {
    return (
      <main className="boot">
        <p className="boot-msg">{failed}</p>
        <button className="btn" onClick={() => window.location.reload()}>
          Qayta urinish
        </button>
      </main>
    );
  }
  if (!ctx) {
    return (
      <main className="boot" aria-busy="true">
        <IconShield className="boot-mark" />
      </main>
    );
  }

  const isOwner = ctx.me.user.role === "owner";
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <Ctx.Provider value={ctx}>
      <div className="frame">
        <nav className="rail" aria-label="Asosiy menyu">
          <Link href="/" className="rail-mark" aria-label="Hushyor AI">
            <IconShield />
          </Link>
          <ul className="rail-nav">
            {NAV.filter((n) => !n.owner || isOwner).map((n) => (
              <li key={n.href}>
                <Link href={n.href} className={active(n.href) ? "rail-link on" : "rail-link"} aria-current={active(n.href) ? "page" : undefined}>
                  <n.icon />
                  <span className="long">{n.label}</span>
                  <span className="short">{n.short}</span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="rail-foot">
            <span className={live ? "live on" : "live"} title={live ? "Jonli ulanish bor" : "Jonli ulanish yo'q — qayta ulanmoqda"}>
              {live ? "Jonli" : "Uzilgan"}
            </span>
            <button className="rail-link" onClick={logout} title="Chiqish">
              <IconExit />
              <span>Chiqish</span>
            </button>
          </div>
        </nav>
        <div className="main">
          <header className="topbar">
            <span className="company">{ctx.me.company?.name ?? "Hushyor AI"}</span>
            <span className="who">
              {ctx.me.user.name && ctx.me.user.name !== ROLE_UZ[ctx.me.user.role] ? ctx.me.user.name : ctx.me.user.email}
              <span className="role">{ROLE_UZ[ctx.me.user.role]}</span>
              <button className="btn small quiet top-exit" onClick={logout}>
                Chiqish
              </button>
            </span>
          </header>
          {children}
        </div>
      </div>
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone ?? "info"}`}>
            <div className="toast-text">
              <strong>{t.title}</strong>
              {t.body ? <span>{t.body}</span> : null}
            </div>
            {t.href ? (
              <button
                className="toast-act"
                onClick={() => {
                  setToasts((xs) => xs.filter((x) => x.id !== t.id));
                  router.push(t.href!);
                }}
              >
                {t.action ?? "Ochish"}
              </button>
            ) : null}
            <button className="toast-x" aria-label="Yopish" onClick={() => setToasts((xs) => xs.filter((x) => x.id !== t.id))}>
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
