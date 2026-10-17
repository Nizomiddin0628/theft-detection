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
};

export const STATUS_UZ: Record<Status, string> = {
  yangi: "Tekshirilmagan",
  tasdiqlangan: "Tasdiqlangan",
  rad_etilgan: "Xato signal",
};
