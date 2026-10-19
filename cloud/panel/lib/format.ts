// Vaqt va sonlar (brauzer mahalliy vaqti; o'zbekcha oy nomlari)

const MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];

const pad = (n: number) => String(n).padStart(2, "0");

export function clock(ts: number | null | undefined, seconds = false): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}${seconds ? ":" + pad(d.getSeconds()) : ""}`;
}

export function dayLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const y = new Date();
  y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Bugun";
  if (d.toDateString() === y.toDateString()) return "Kecha";
  return `${d.getDate()}-${MONTHS[d.getMonth()]}${d.getFullYear() !== today.getFullYear() ? " " + d.getFullYear() : ""}`;
}

export function when(ts: number | null | undefined, seconds = false): string {
  if (!ts) return "—";
  return `${dayLabel(ts)}, ${clock(ts, seconds)}`;
}

export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "hech qachon";
  const s = Math.max(0, now - ts);
  if (s < 60) return "hozirgina";
  if (s < 3600) return `${Math.floor(s / 60)} daqiqa oldin`;
  if (s < 86400) return `${Math.floor(s / 3600)} soat oldin`;
  return `${Math.floor(s / 86400)} kun oldin`;
}

export function duration(sec: number): string {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} soniya`;
  if (sec < 3600) return `${Math.round(sec / 60)} daqiqa`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m ? `${h} soat ${m} daqiqa` : `${h} soat`;
}

export function secs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v < 10 ? `${v.toFixed(1)} s` : `${Math.round(v)} s`;
}

export const scoreTone = (score: number) => (score >= 75 ? "high" : score >= 50 ? "mid" : "low");

// 3.1.0: 150000 → "150 000 so'm"
export function som(n: number | null | undefined, unit = true): string {
  const v = Math.round(Number(n) || 0);
  const s = String(v).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return unit ? `${s} so'm` : s;
}
