"use client";
// 3.0.0: mavzu tugmasi — tizim bo'yicha → yorug' → tungi → tizim bo'yicha. Tanlov brauzerda saqlanadi
// (public/theme.js uni sahifa chizilishidan oldin qo'llaydi; sayt bilan bir xil kalit).
import { useEffect, useState } from "react";

import { IconAuto, IconMoon, IconSun } from "./Icons";

export type Theme = "system" | "light" | "dark";

const KEY = "hushyor-theme";
const ORDER: Theme[] = ["system", "light", "dark"];
const NAME: Record<Theme, string> = { system: "Tizim", light: "Yorug'", dark: "Tungi" };
const LONG: Record<Theme, string> = { system: "tizim bo'yicha", light: "yorug'", dark: "tungi" };

function current(): Theme {
  if (typeof document === "undefined") return "system";
  const t = document.documentElement.getAttribute("data-theme");
  return t === "light" || t === "dark" ? t : "system";
}

const EVT = "hushyor-theme";            // shu tabdagi boshqa tugmalar ham ergashsin (storage hodisasi faqat boshqa tabda)

export function applyTheme(t: Theme): void {
  const root = document.documentElement;
  if (t === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", t);
  try {
    if (t === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, t);
  } catch {
    /* saqlab bo'lmadi — faqat shu oynada amal qiladi */
  }
  window.dispatchEvent(new CustomEvent<Theme>(EVT, { detail: t }));
}

/** variant "rail" — chap menyu pastida (belgi + yozuv), "chip" — kichik dumaloq tugma (telefon sarlavhasi, kirish sahifasi). */
export default function ThemeToggle({ variant = "chip", className = "" }: { variant?: "rail" | "chip"; className?: string }) {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    setTheme(current());
    // Boshqa tabda o'zgartirilsa — shu tab ham ergashadi
    const on = (e: StorageEvent) => {
      if (e.key !== KEY) return;
      const v = e.newValue === "light" || e.newValue === "dark" ? e.newValue : "system";
      applyTheme(v);
      setTheme(v);
    };
    const same = (e: Event) => setTheme((e as CustomEvent<Theme>).detail);
    window.addEventListener("storage", on);
    window.addEventListener(EVT, same);
    return () => {
      window.removeEventListener("storage", on);
      window.removeEventListener(EVT, same);
    };
  }, []);
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
  const label = `Mavzu: ${LONG[theme]}. Bosing — ${LONG[next]}`;
  const Icon = theme === "light" ? IconSun : theme === "dark" ? IconMoon : IconAuto;
  const click = () => {
    applyTheme(next);
    setTheme(next);
  };
  if (variant === "rail") {
    return (
      <button type="button" className={`rail-link theme-toggle ${className}`.trim()} onClick={click} aria-label={label} title={label} data-theme-mode={theme}>
        <Icon />
        <span>{NAME[theme]}</span>
      </button>
    );
  }
  return (
    <button type="button" className={`theme-chip ${className}`.trim()} onClick={click} aria-label={label} title={label} data-theme-mode={theme}>
      <Icon width={18} height={18} />
    </button>
  );
}
