"use client";
// 3.1.0: Video sinovi — tanlangan videoning kadri ustiga zonalar chizish (kassa navbati, sanash chizig'i, bo'limlar...).
// Kadr brauzerning o'zida olinadi (video serverga yuklanmasdan oldin). Nuqtalar 0..1 — videoning o'lchamiga bog'liq emas.
import { useEffect, useMemo, useRef, useState } from "react";

import type { DrawnZone, ZoneKind } from "@/lib/api";

export const ZONES_MAX = 12;
export const POINTS_MAX = 16;

export const KINDS: { kind: ZoneKind; label: string; color: string; hint: string; line?: boolean }[] = [
  { kind: "navbat", label: "Kassa navbati", color: "#db2777", hint: "Kassa oldida xaridorlar navbat kutadigan joy (to'lov joyi bilan birga). Navbat uzunligi va kutish vaqti o'lchanadi." },
  { kind: "kassir", label: "Kassir joyi", color: "#4f46e5", hint: "Peshtaxta orti — kassir turadigan joy. Bu yerdagi odam navbatga qo'shilmaydi." },
  { kind: "chiziq", label: "Sanash chizig'i", color: "#0891b2", hint: "Eshik ostonasiga 2 nuqta. Strelka — ichkari: shu tomonga o'tgan odam «kirdi», teskarisi «chiqdi».", line: true },
  { kind: "polka", label: "Polka / bo'lim", color: "#2563eb", hint: "Mahsulot turgan joy. Necha odam to'xtadi va necha marta qo'l uzatildi — «qaysi bo'limga ko'p qarashdi»." },
  { kind: "kassa", label: "Kassa", color: "#16a34a", hint: "To'lov joyi: sumkaga solish signallari bu yerda o'chadi." },
  { kind: "eshik", label: "Kirish / chiqish", color: "#d97706", hint: "Shubhali odam shu yerga kelsa — «chiqish tomon ketmoqda» signali." },
  { kind: "xodim", label: "Xodimlar joyi", color: "#7c3aed", hint: "Shu yerda 2 s turgan odam xodim deb olinadi — unga signal berilmaydi." },
  { kind: "etiborsiz", label: "E'tiborsiz", color: "#64748b", hint: "Televizor, oyna, deraza: bu yerdagi odam va harakat tahlil qilinmaydi." },
];
const META = Object.fromEntries(KINDS.map((k) => [k.kind, k])) as Record<ZoneKind, (typeof KINDS)[number]>;

/** Videoning bitta kadri (≈1-soniya) — brauzerda, serverga yuklashdan oldin. */
function useFrame(file: File | null) {
  const [frame, setFrame] = useState<{ src: string; w: number; h: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setFrame(null);
    setErr(null);
    if (!file) return;
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.playsInline = true;
    let done = false;
    const fail = () => {
      if (done) return;
      done = true;
      setErr("Bu videoning kadrini brauzer ko'rsata olmadi (format). Zonalarni chizish uchun MP4 (H.264) faylni tanlang — tahlil esa baribir ishlaydi.");
    };
    const timer = window.setTimeout(fail, 15000);
    v.onerror = fail;
    v.onloadedmetadata = () => {
      const d = Number.isFinite(v.duration) ? v.duration : 0;
      v.currentTime = Math.min(1, d * 0.1);
    };
    v.onseeked = () => {
      if (done) return;
      try {
        const w = v.videoWidth;
        const h = v.videoHeight;
        if (!w || !h) return fail();
        const scale = Math.min(1, 1280 / w);
        const c = document.createElement("canvas");
        c.width = Math.round(w * scale);
        c.height = Math.round(h * scale);
        const ctx = c.getContext("2d");
        if (!ctx) return fail();
        ctx.drawImage(v, 0, 0, c.width, c.height);
        done = true;
        setFrame({ src: c.toDataURL("image/jpeg", 0.85), w, h });
      } catch {
        fail();
      }
    };
    v.src = url;
    return () => {
      done = true;
      window.clearTimeout(timer);
      v.removeAttribute("src");
      v.load();
      URL.revokeObjectURL(url);
    };
  }, [file]);
  return { frame, err };
}

const r3 = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;

export default function ZoneEditor({
  file,
  zones,
  onChange,
  disabled,
}: {
  file: File | null;
  zones: DrawnZone[];
  onChange: (z: DrawnZone[]) => void;
  disabled?: boolean;
}) {
  const { frame, err } = useFrame(file);
  const [kind, setKind] = useState<ZoneKind>("navbat");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<[number, number][]>([]);
  const [hover, setHover] = useState<[number, number] | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const W = 1000;
  const H = frame ? Math.round((W * frame.h) / frame.w) : 562;
  const meta = META[kind];
  const full = zones.length >= ZONES_MAX;

  useEffect(() => setDraft([]), [kind, file]);

  const add = (z: DrawnZone) => {
    onChange([...zones, z]);
    setDraft([]);
    setName("");
  };
  const finish = () => {
    if (meta.line || draft.length < 3) return;
    add({ kind, name: name.trim() || defaultName(kind, zones), pts: draft });
  };
  const click = (e: React.MouseEvent<SVGSVGElement>) => {
    if (disabled || full || !svgRef.current) return;
    const r = svgRef.current.getBoundingClientRect();
    const p: [number, number] = [r3((e.clientX - r.left) / r.width), r3((e.clientY - r.top) / r.height)];
    if (meta.line) {
      const next = [...draft, p];
      if (next.length === 2) {
        if (Math.hypot(next[1][0] - next[0][0], next[1][1] - next[0][1]) < 0.03) return;
        add({ kind, name: name.trim() || defaultName(kind, zones), pts: next });
      } else setDraft(next);
      return;
    }
    // birinchi nuqta yonida bosilsa — ko'pburchak yopiladi
    if (draft.length >= 3 && Math.hypot(p[0] - draft[0][0], (p[1] - draft[0][1]) * (H / W)) < 0.02) {
      finish();
      return;
    }
    const next = [...draft, p];
    if (next.length >= POINTS_MAX) add({ kind, name: name.trim() || defaultName(kind, zones), pts: next });
    else setDraft(next);
  };
  const move = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current || !draft.length) return;
    const r = svgRef.current.getBoundingClientRect();
    setHover([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
  };

  const toPx = (p: [number, number]) => `${(p[0] * W).toFixed(1)},${(p[1] * H).toFixed(1)}`;
  const shapes = useMemo(
    () =>
      zones.map((z, i) => {
        const c = META[z.kind]?.color ?? "#64748b";
        if (z.kind === "chiziq") {
          const [a, b] = z.pts;
          const ax = a[0] * W, ay = a[1] * H, bx = b[0] * W, by = b[1] * H;
          const dx = bx - ax, dy = by - ay;
          const len = Math.hypot(dx, dy) || 1;
          const nx = -dy / len, ny = dx / len;            // "ichkari" — a→b ning o'ng tomoni (rasmda y pastga)
          const mx = (ax + bx) / 2, my = (ay + by) / 2;
          const tx = mx + nx * 40, ty = my + ny * 40;
          return (
            <g key={i}>
              <line x1={ax} y1={ay} x2={bx} y2={by} stroke={c} strokeWidth={5} strokeLinecap="round" />
              <line x1={mx} y1={my} x2={tx} y2={ty} stroke={c} strokeWidth={4} markerEnd="url(#vt-arrow)" />
              {/* nom — chiziq o'rtasida, strelkaning teskari tomonida (kadrdan chiqib ketmasin) */}
              <text
                x={Math.min(W - 10, Math.max(10, mx - nx * 22))}
                y={Math.min(H - 8, Math.max(24, my - ny * 22 + 8))}
                textAnchor="middle"
                className="vt-zlabel"
                fill={c}
              >
                {z.name}
              </text>
            </g>
          );
        }
        const [x0, y0] = z.pts[0];
        return (
          <g key={i}>
            <polygon points={z.pts.map(toPx).join(" ")} fill={c} fillOpacity={0.18} stroke={c} strokeWidth={3} />
            <text x={x0 * W + 6} y={y0 * H + 22} className="vt-zlabel" fill={c}>
              {z.name}
            </text>
          </g>
        );
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [zones, H],
  );

  if (!file) {
    return <p className="muted small vt-zone-note">Avval video faylni tanlang — uning kadri shu yerda chiqadi va ustiga zonalar chiziladi.</p>;
  }
  if (err) return <p className="error vt-zone-note">{err}</p>;
  if (!frame) return <p className="muted small vt-zone-note">Video kadri tayyorlanmoqda…</p>;

  return (
    <div className="vt-zones" data-testid="vt-zones">
      <div className="vt-ztools">
        <label className="field">
          <span>Zona turi</span>
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)} disabled={disabled} data-testid="vt-zkind">
            {KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>
            Nomi <span className="hint">(ixtiyoriy, masalan «1-kassa»)</span>
          </span>
          <input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} disabled={disabled} />
        </label>
        <div className="vt-zbtns">
          {!meta.line ? (
            <button type="button" className="btn small primary" onClick={finish} disabled={disabled || draft.length < 3} data-testid="vt-zclose">
              Zonani yopish
            </button>
          ) : null}
          <button type="button" className="btn small" onClick={() => setDraft(draft.slice(0, -1))} disabled={disabled || !draft.length}>
            Oxirgi nuqtani olish
          </button>
          <button type="button" className="btn small quiet" onClick={() => setDraft([])} disabled={disabled || !draft.length}>
            Bekor qilish
          </button>
        </div>
      </div>
      <p className="muted small vt-zhint">
        <b style={{ color: meta.color }}>{meta.label}:</b> {meta.hint}{" "}
        {meta.line
          ? "Kadrda ikki nuqtani bosing."
          : `Kadrda burchaklarni ketma-ket bosing (3–${POINTS_MAX} nuqta), keyin «Zonani yopish» yoki birinchi nuqtani bosing.`}
        {full ? ` Ko'pi bilan ${ZONES_MAX} ta zona.` : ""}
      </p>
      <div className="vt-frame" style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={frame.src} alt="Video kadri" />
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          onClick={click}
          onMouseMove={move}
          onMouseLeave={() => setHover(null)}
          className={disabled || full ? "" : "drawing"}
          role="img"
          aria-label="Zonalar chizilgan video kadri"
          data-testid="vt-canvas"
        >
          <defs>
            <marker id="vt-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#0891b2" />
            </marker>
          </defs>
          {shapes}
          {draft.length ? (
            <g>
              <polyline
                points={[...draft, ...(hover ? [hover] : [])].map(toPx).join(" ")}
                fill="none"
                stroke={meta.color}
                strokeWidth={3}
                strokeDasharray="10 6"
              />
              {draft.map((p, i) => (
                <circle key={i} cx={p[0] * W} cy={p[1] * H} r={i === 0 ? 9 : 6} fill={i === 0 ? "#fff" : meta.color} stroke={meta.color} strokeWidth={3} />
              ))}
            </g>
          ) : null}
        </svg>
      </div>
      {zones.length ? (
        <ul className="vt-zlist" aria-label="Chizilgan zonalar">
          {zones.map((z, i) => (
            <li key={i}>
              <span className="vt-zdot" style={{ background: META[z.kind]?.color }} aria-hidden />
              <b>{z.name}</b>
              <span className="muted small">{META[z.kind]?.label}</span>
              <span className="vt-zgrow" />
              {z.kind === "chiziq" ? (
                <button
                  type="button"
                  className="btn small quiet"
                  disabled={disabled}
                  onClick={() => onChange(zones.map((x, j) => (j === i ? { ...x, pts: [x.pts[1], x.pts[0]] } : x)))}
                >
                  Yo&apos;nalishni almashtirish
                </button>
              ) : null}
              <button
                type="button"
                className="btn small quiet"
                disabled={disabled}
                onClick={() => onChange(zones.filter((_, j) => j !== i))}
                aria-label={`${z.name} zonasini o'chirish`}
              >
                O&apos;chirish
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">Hali zona chizilmagan — zonasiz ham tahlil qilinadi (faqat o&apos;g&apos;irlik va xavfsizlik signallari).</p>
      )}
    </div>
  );
}

function defaultName(kind: ZoneKind, zones: DrawnZone[]): string {
  const n = zones.filter((z) => z.kind === kind).length + 1;
  const base = META[kind].label;
  return n > 1 ? `${base} ${n}` : base;
}
