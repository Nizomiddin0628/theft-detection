// Bulut API v1 bilan ishlash: cookie sessiyasi (HttpOnly hc_session), bir xil manba — token JS'da saqlanmaydi.

export type Role = "owner" | "manager" | "guard";
export type Status = "yangi" | "tasdiqlangan" | "rad_etilgan";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  company_id: number;
}

export interface Me {
  user: User;
  company: { id: number; name: string } | null;
  branch_ids: number[];
  demo?: boolean;
}

export interface Camera {
  id: number;
  name: string;
  kind?: string;
  state?: string;
  running?: boolean;
}

export interface Branch {
  id: number;
  name: string;
  address?: string;
  device_id: string;
  created_at: number;
  last_seen: number | null;
  version?: string;
  cameras: Camera[];
  pending?: number;
  clock_skew?: number | null;
  online: boolean;
  events_24h: number;
  pending_review: number;
  visits_today?: { in: number; out: number; inside: number } | null;
  // 2.5.0: do'kon kompyuteridagi tayyorlik ro'yxati va pilot (hello bilan keladi)
  readiness?: {
    ok: number;
    total: number;
    bad: string[];
    warn: string[];
    at: number;
    pilot: { since: number; until: number | null; days: number } | null;
  } | null;
}

// 1.9.0: odamlarni sanash (edge sanash chizig'i → daqiqalik kirdi/chiqdi)
export interface VisitDay {
  day: number;
  in: number;
  out: number;
  branches: Record<string, { in: number; out: number; staff: number }>;
}
export interface VisitBranch {
  id: number;
  name: string;
  today_in: number;
  today_out: number;
  inside: number;
  hourly: number[];
  busiest_hour: number | null;
  total_in: number;
}
export interface Visits {
  days: VisitDay[];
  branches: VisitBranch[];
  tz_offset: number;
  day0: number;
}

// 2.1.0: oqim va heatmap (edge reja va bo'limlar → hello; soatlik yig'indi → flow_hour)
export interface FlowSection {
  id: number;
  name: string;
  color: string;
  pts: [number, number][];
}
export interface FlowPlan {
  id: number;
  name: string;
  w_m: number;
  h_m: number;
  cell: number;
  nx: number;
  ny: number;
  grid: string;
  sections: FlowSection[];
  branch_id: number;
  branch_name: string;
}
export interface FlowHeat {
  max: number;
  total: number;
  data: string;
}
export interface Flow {
  plans: { branch_id: number; branch_name: string; plan_id: number; plan_name: string }[];
  plan: FlowPlan | null;
  days: number;
  day0: number;
  t0: number;
  tz_offset: number;
  total: number;
  sections: { id: number; name: string; visits: number; share: number }[];
  moves: { from: number; to: number; n: number; from_name: string; to_name: string }[];
  today_hours: Record<string, number>[];
  per_day: number[];
  obs: number;
  heat: { occ: FlowHeat; pas: FlowHeat; nx: number; ny: number; cell: number };
}

// 2.0.0: kassa navbati (edge navbat zonasi → hello holati, daqiqalik yig'indi, qoida signallari)
export type QueueState = "navbat" | "band" | "bosh" | "yopiq" | "kassir_yoq" | "nomalum";
export interface QueueLive {
  zone_id: number;
  name: string;
  source_name: string;
  length: number;
  max_wait: number;
  cashier: boolean | null;
  state: QueueState;
  live: boolean;
}
export interface QueueBranch {
  id: number;
  name: string;
  live: QueueLive[] | null;
  stale: boolean;
  today: { served: number; avg_wait: number | null; max_wait: number; max_len: number; long_min: number; avg_len: number | null };
  hours_len: number[];
  hours_served: number[];
}
export interface QueueDay {
  day: number;
  served: number;
  avg_wait: number | null;
  wait_max: number;
  len_max: number;
  long_min: number;
  branches: Record<string, { served: number; max_len: number; avg_wait: number | null }>;
}
export interface QueueAlert {
  id: number;
  branch_id: number | null;
  branch_name: string | null;
  t: number;
  rule: "R1" | "R2" | "R3" | "R4" | "R5";
  level: string;
  zone_name: string;
  title: string;
  text: string;
}
export interface Queues {
  branches: QueueBranch[];
  days: QueueDay[];
  alerts: QueueAlert[];
  rules: Record<string, string>;
  tz_offset: number;
  day0: number;
}

export interface EventRow {
  uid: string;
  branch_id: number;
  branch_name: string;
  device_id: string;
  local_id: number;
  created_at: number;
  updated_at: number;
  received_at: number | null;
  kind: string;
  score: number;
  status: Status;
  source_name: string;
  clip_file: string | null;
  thumb_file: string | null;
  clip_clean?: string | null;
  loss_sum?: number | null;
  level?: string | null;
  reasons: string[];
  reviewed_by?: string | null;
  video?: boolean;
}

export interface EventFull {
  uid: string;
  branch_id: number;
  device_id: string;
  local_id: number;
  created_at: number;
  updated_at: number;
  kind: string;
  score: number;
  status: Status;
  source_name: string;
  clip_file: string | null;
  thumb_file: string | null;
  clip_clean?: string | null;
  loss_sum?: number | null;
  data: Record<string, unknown> | null;
}

export interface HealthRow {
  key: string;
  t: number;
  source_id: number | null;
  source_name: string;
  state: string;
  reason: string;
}

export interface Prefs {
  event: boolean;
  min_score: number;
  camera: boolean;
  offline: boolean;
  escalation: boolean;
  queue: boolean;
  queue_low: boolean;
  digest: boolean;
}

// 2.4.0 Taqqoslash (rahbariyat)
export interface CompareBranch {
  id: number;
  name: string;
  version: string | null;
  online: boolean;
  visits: { in: number; prev_in: number; change_pct: number | null };
  queue: { served: number; avg_wait: number | null; max_wait: number; long_sec: number } | null;
  theft: { total: number; confirmed: number; rejected: number; unreviewed: number; false_share: number | null; loss?: number; loss_n?: number };
  safety: { total: number; unreviewed: number };
  uptime: number | null;
}

export interface Compare {
  period: "week" | "day";
  t0: number;
  t1: number;
  label: string;
  partial: boolean;
  branches: CompareBranch[];
  total: { visits: number; prev_visits: number; change_pct: number | null; served: number; avg_wait: number | null; theft: number; safety: number; unreviewed: number; loss?: number; loss_n?: number };
  recs: { key: string; level: "warn" | "info"; text: string }[];
}

export interface Target {
  id: number;
  channel: "telegram" | "email" | "push";
  address: string;
  enabled: boolean;
  last_ok: number | null;
  last_error: string | null;
  created_at: number | null;
}

export interface Channels {
  telegram: { enabled: boolean; bot?: string | null; error?: string };
  email: { enabled: boolean };
  push: { enabled: boolean; error?: string };
}

export interface ChannelStats {
  sent: number;
  failed: number;
  pending: number;
  skipped: number;
  queue_p50: number | null;
  queue_p95: number | null;
  event_p50: number | null;
  event_p95: number | null;
}

export interface MetricRow {
  hour: number;
  source_id: number;
  source_name: string | null;
  persons_avg: number | null;
  persons_max: number;
  fps_ai: number | null;
  active_pct: number | null;
  up_pct: number | null;
  alerts: number;
  cpu: number | null;
  ram: number | null;
  in?: number;
  out?: number;
}

export interface AuditRow {
  id: number;
  t: number;
  user_id: number | null;
  actor: string;
  action: string;
  target: string;
  ip: string;
  detail: Record<string, unknown> | null;
}

export const AUDIT_UZ: Record<string, string> = {
  login_ok: "Kirdi",
  login_fail: "Kirish xato (parol)",
  logout: "Chiqdi",
  logout_all: "Hamma qurilmalardan chiqdi",
  password_change: "Parolini almashtirdi",
  password_fail: "Parol almashtirish: joriy parol xato",
  password_reset: "Parol tiklandi",
  user_create: "Xodim qo'shildi",
  user_update: "Xodim o'zgartirildi",
  user_deactivate: "Xodim o'chirildi",
  user_activate: "Xodim qaytarildi",
  branch_create: "Filial yaratildi",
  token_rotate: "Filial tokeni yangilandi",
  review: "Hodisa tekshirildi",
  clip_view: "Klip ko'rildi",
  telegram_link: "Telegram ulandi",
};

export interface SystemStatus {
  version: string;
  db: string;
  sse: number;
  clips: number;
  clips_mb: number;
  free_gb: number;
  min_free_gb: number;
  clip_days: number;
  confirmed_days: number;
  backup_hours: number;
  last_backup: string | null;
  last_backup_age_h: number | null;
}

// 3.0.0: Video sinovi — panelda yuklanadi, filialning do'kon kompyuteri (edge) tahlil qiladi
export type VideoTestStatus = "kutilmoqda" | "yuklanmoqda" | "navbatda" | "ishlanmoqda" | "tayyor" | "xato" | "bekor";
export interface VideoTest {
  id: number;
  branch_id: number;
  branch_name: string;
  filename: string;
  size: number;
  status: VideoTestStatus;
  status_uz: string;
  progress: number;
  events: number;
  duration: number | null;
  error: string | null;
  created_at: number;
  updated_at: number | null;
  started_at: number | null;
  done_at: number | null;
  zone_source_id: number | null;
  zone_source_name: string | null;
  job_id: number | null;
  active: boolean;
  zones?: DrawnZone[];
  metrics?: VideoMetrics | null;
}
// 3.1.0: video kadri ustiga chizilgan zona (nuqtalar 0..1)
export type ZoneKind = "navbat" | "kassir" | "chiziq" | "polka" | "kassa" | "eshik" | "xodim" | "etiborsiz";
export interface DrawnZone {
  kind: ZoneKind;
  name: string;
  pts: [number, number][];
}
export interface VideoMetrics {
  duration: number;
  zones: number;
  persons_max?: number;
  queue: { name: string; max_len: number; avg_len: number; busy_pct: number; served: number; avg_wait: number | null; max_wait: number | null; series: [number, number][] }[];
  lines: { name: string; in: number; out: number; staff: number }[];
  areas: { name: string; kind: string; visitors: number; avg_dwell: number | null; reaches: number | null }[];
}
export interface Losses {
  today: { sum: number; n: number };
  month: { sum: number; n: number };
  total: { sum: number; n: number };
  missing: number;
  branches: { id: number; name: string; month_sum: number; month_n: number }[];
}
export interface VideoTestBranch {
  id: number;
  name: string;
  version: string;
  online: boolean;
  ok: boolean;
  cameras: { id: number; name: string }[];
}
export interface VideoTestsResp {
  tests: VideoTest[];
  branches: VideoTestBranch[];
  limits: { max_mb: number; quota_mb: number; exts: string[]; active_max: number };
  can_upload: boolean;
}
export interface VideoTestEvent {
  uid: string;
  kind: string;
  score: number;
  status: Status;
  level: string | null;
  reasons: string[];
  video_time: number | null;
  clip_file: string | null;
  thumb_file: string | null;
  clip_clean?: string | null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/api/v1";

export async function api<T = Record<string, unknown>>(
  path: string,
  init: RequestInit & { json?: unknown; noRedirect?: boolean } = {},
): Promise<T> {
  const { json, noRedirect, ...rest } = init;
  const headers: Record<string, string> = { Accept: "application/json", ...(rest.headers as Record<string, string>) };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...rest,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw new ApiError(0, "Server bilan aloqa yo'q. Internetni tekshiring.");
  }
  let body: Record<string, unknown> = {};
  try {
    body = await res.json();
  } catch {
    body = {};
  }
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `xato ${res.status}`;
    if (res.status === 401 && !noRedirect && typeof window !== "undefined" && !path.startsWith("/auth/")) {
      const here = window.location.pathname + window.location.search;
      window.location.replace("/login/?next=" + encodeURIComponent(here));
    }
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export const clipUrl = (file: string | null | undefined) => (file ? `${BASE}/clips/${file}` : null);

export const ROLE_UZ: Record<Role, string> = { owner: "Egasi", manager: "Menejer", guard: "Qo'riqchi" };

export const KIND_UZ: Record<string, string> = {
  pocket: "Cho'ntakka solish",
  torso: "Kiyim ichiga yashirish",
  bag: "Sumkaga solish",
  offscreen: "Qo'l kadrdan yashirildi",
  exit: "Chiqish tomon ketmoqda",
  // 2.2.0: xavfsizlik va salomatlik
  fall: "Yiqilish",
  lying: "Odam yotibdi",
  night: "Tungi kirish",
  crowd: "Gavjumlik",
  blocked: "Chiqish to'silgan",
  fire: "Olov (tajriba)",
  fight: "Janjal (tajriba)", // 2.3.0
};

/** 2.2.0: xavfsizlik signali — ball yo'q, "ayblov emas" yozuvi kerak emas, yordam/chora kerak */
export const SAFETY_KINDS = ["fall", "lying", "night", "crowd", "blocked", "fire", "fight"];
export const isSafety = (kind: string | null | undefined) => SAFETY_KINDS.includes(String(kind || ""));

export const STATUS_UZ: Record<Status, string> = {
  yangi: "Tekshirilmagan",
  tasdiqlangan: "Tasdiqlangan",
  rad_etilgan: "Xato signal",
};
