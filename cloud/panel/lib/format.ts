// Vaqt va sonlar (o'zbekcha oy nomlari). 3.2.0: kompaniya vaqt mintaqasi bo'yicha (serverdagi CLOUD_TZ_OFFSET) —
// panel boshqa mintaqadagi brauzerda ochilsa ham do'kon vaqti ko'rsatiladi; noma'lum bo'lsa — brauzer vaqti

const MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];

const pad = (n: number) => String(n).padStart(2, "0");

let TZ_SEC: number | null = null;

export function setCompanyTz(hours: number | null | undefined): void {
  TZ_SEC = typeof hours === "number" && Number.isFinite(hours) ? Math.round(hours * 3600) : null;
}

/** Kompaniya (yoki brauzer) vaqtidagi qismlar. */
export function parts(ts: number): { y: number; mo: number; d: number; h: number; mi: number; s: number } {
  if (TZ_SEC === null) {
    const d = new Date(ts * 1000);
    return { y: d.getFullYear(), mo: d.getMonth(), d: d.getDate(), h: d.getHours(), mi: d.getMinutes(), s: d.getSeconds() };
  }
  const d = new Date((ts + TZ_SEC) * 1000);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth(), d: d.getUTCDate(), h: d.getUTCHours(), mi: d.getUTCMinutes(), s: d.getUTCSeconds() };
}

/** Kompaniya vaqti bo'yicha shu kun boshlanishi (unix soniya). */
export function dayStart(ts = Date.now() / 1000): number {
  if (TZ_SEC === null) {
    const d = new Date(ts * 1000);
    d.setHours(0, 0, 0, 0);
    return d.getTime() / 1000;
  }
  return Math.floor((ts + TZ_SEC) / 86400) * 86400 - TZ_SEC;
}

/** Vaqt o'qidagi yaxlit soatlar uchun siljish (soniya): mahalliy soat = (ts + tzShift) / 3600. */
export function tzShift(): number {
  return TZ_SEC === null ? -new Date().getTimezoneOffset() * 60 : TZ_SEC;
}

export function clock(ts: number | null | undefined, seconds = false): string {
  if (!ts) return "—";
  const p = parts(ts);
  return `${pad(p.h)}:${pad(p.mi)}${seconds ? ":" + pad(p.s) : ""}`;
}

export function dayLabel(ts: number): string {
  const p = parts(ts);
  const t0 = dayStart();
  const now = parts(Date.now() / 1000);
  if (ts >= t0 && ts < t0 + 86400) return "Bugun";
  if (ts >= t0 - 86400 && ts < t0) return "Kecha";
  return `${p.d}-${MONTHS[p.mo]}${p.y !== now.y ? " " + p.y : ""}`;
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
