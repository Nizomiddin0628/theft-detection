"use client";
// Jonli oqim (SSE /api/v1/stream): Shell bitta EventSource ochadi, sahifalar useLive orqali tinglaydi.
import { useEffect, useRef } from "react";

export type LiveKind = "event_new" | "event_update" | "branch" | "health" | "logout" | "evicted";
export interface LiveMsg {
  kind: LiveKind;
  data: Record<string, unknown>;
}

const NAME = "hushyor:live";

export function emitLive(msg: LiveMsg) {
  window.dispatchEvent(new CustomEvent<LiveMsg>(NAME, { detail: msg }));
}

export function useLive(handler: (msg: LiveMsg) => void) {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => {
    const fn = (e: Event) => ref.current((e as CustomEvent<LiveMsg>).detail);
    window.addEventListener(NAME, fn);
    return () => window.removeEventListener(NAME, fn);
  }, []);
}

// Qisqa ikki tonli signal (brauzer ovozni faqat foydalanuvchi sahifaga bir marta bosgandan keyin ruxsat etadi)
let ctx: AudioContext | null = null;
export function unlockAudio() {
  try {
    if (!ctx) ctx = new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
  }
}

export function beep() {
  try {
    if (!ctx || ctx.state !== "running") return;
    const t = ctx.currentTime;
    [880, 1175].forEach((f, i) => {
      const o = ctx!.createOscillator();
      const g = ctx!.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + i * 0.16);
      g.gain.exponentialRampToValueAtTime(0.18, t + i * 0.16 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.16 + 0.14);
      o.connect(g).connect(ctx!.destination);
      o.start(t + i * 0.16);
      o.stop(t + i * 0.16 + 0.15);
    });
  } catch {
    /* ovozsiz davom etadi */
  }
}

export function getPref(key: string, dflt: string): string {
  try {
    return window.localStorage.getItem("hushyor." + key) ?? dflt;
  } catch {
    return dflt;
  }
}

export function setPref(key: string, value: string) {
  try {
    window.localStorage.setItem("hushyor." + key, value);
  } catch {
    /* saqlanmasa ham ishlaydi */
  }
}
