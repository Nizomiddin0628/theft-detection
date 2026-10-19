"use client";
// 3.1.0: Video sinovi natijasidagi o'lchovlar — navbat, sanash chizig'i, zonalar (bo'limlar). Hisobni edge qiladi.
import type { VideoMetrics } from "@/lib/api";

const dur = (s: number | null | undefined) => {
  if (s === null || s === undefined) return "—";
  if (s < 90) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} daq ${Math.round(s % 60)} s`;
};

function Spark({ series, max }: { series: [number, number][]; max: number }) {
  if (!series.length) return null;
  const top = Math.max(1, max);
  const w = 100 / series.length;
  return (
    <svg className="vt-spark" viewBox="0 0 100 30" preserveAspectRatio="none" role="img" aria-label="Navbat uzunligi video davomida">
      {series.map(([, v], i) => (
        <rect key={i} x={i * w + w * 0.1} width={w * 0.8} y={30 - (v / top) * 28} height={(v / top) * 28} rx={0.6} />
      ))}
    </svg>
  );
}

export default function Metrics({ m: raw, zones }: { m: VideoMetrics | null | undefined; zones: number }) {
  // bulut shaklini tekshirib beradi; baribir himoya — eski yoki chala yozuv sahifani buzmasin
  const m = raw
    ? {
        ...raw,
        queue: Array.isArray(raw.queue) ? raw.queue : [],
        lines: Array.isArray(raw.lines) ? raw.lines : [],
        areas: Array.isArray(raw.areas) ? raw.areas : [],
      }
    : null;
  if (!m) {
    return zones ? (
      <p className="muted small">Zonalar bo&apos;yicha o&apos;lchov hali kelmagan (do&apos;kon kompyuteri tahlilni tugatgach chiqadi).</p>
    ) : null;
  }
  const hasAny = m.queue.length || m.lines.length || m.areas.length;
  if (!hasAny) {
    return zones ? <p className="muted small">Chizilgan zonalarda odam aniqlanmadi.</p> : null;
  }
  return (
    <div className="vt-metrics" data-testid="vt-metrics">
      {m.queue.map((q, i) => (
        <section key={`q${i}`} className="vt-mcard" aria-label={`Navbat: ${q.name}`}>
          <h3>🧾 {q.name}</h3>
          <div className="vt-mnums">
            <span>
              <b data-testid="vt-q-max">{q.max_len}</b> eng uzun navbat
            </span>
            <span>
              <b>{Number(q.avg_len || 0).toFixed(1)}</b> o&apos;rtacha
            </span>
            <span>
              <b>{Math.round(Number(q.busy_pct || 0) * 100)}%</b> vaqt navbat bor
            </span>
            <span>
              <b>{q.served}</b> xaridor o&apos;tdi
            </span>
            <span>
              <b>{dur(q.avg_wait)}</b> o&apos;rtacha kutish
            </span>
            <span>
              <b>{dur(q.max_wait)}</b> eng uzoq
            </span>
          </div>
          <Spark series={Array.isArray(q.series) ? q.series : []} max={q.max_len} />
        </section>
      ))}
      {m.lines.map((l, i) => (
        <section key={`l${i}`} className="vt-mcard" aria-label={`Sanash: ${l.name}`}>
          <h3>🚪 {l.name}</h3>
          <div className="vt-mnums">
            <span>
              <b data-testid="vt-in">{l.in}</b> kirdi
            </span>
            <span>
              <b>{l.out}</b> chiqdi
            </span>
            {l.staff ? (
              <span>
                <b>{l.staff}</b> xodim o&apos;tishi
              </span>
            ) : null}
          </div>
        </section>
      ))}
      {m.areas.length ? (
        <section className="vt-mcard wide" aria-label="Zonalar bo'yicha">
          <h3>🗺 Zonalar (bo&apos;limlar)</h3>
          <table className="data">
            <thead>
              <tr>
                <th>Zona</th>
                <th className="num">To&apos;xtaganlar</th>
                <th className="num">O&apos;rtacha turish</th>
                <th className="num">Qo&apos;l uzatish</th>
              </tr>
            </thead>
            <tbody>
              {m.areas.map((a, i) => (
                <tr key={i}>
                  <td>{a.name}</td>
                  <td className="num">
                    <b>{a.visitors}</b>
                  </td>
                  <td className="num">{dur(a.avg_dwell)}</td>
                  <td className="num">{a.reaches ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">To&apos;xtaganlar — zonada kamida 2 soniya turgan odamlar (polkada — qo&apos;li polkada bo&apos;lgan).</p>
        </section>
      ) : null}
      {m.persons_max !== undefined ? (
        <p className="muted small vt-mfoot">Kadrda bir vaqtda eng ko&apos;pi bilan {m.persons_max} kishi ko&apos;rindi.</p>
      ) : null}
    </div>
  );
}
