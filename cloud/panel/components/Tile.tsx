import Link from "next/link";

import { clipUrl, KIND_UZ, type EventRow } from "@/lib/api";
import { clock, dayLabel } from "@/lib/format";
import { IconCamera } from "./Icons";

export default function Tile({ ev, fresh }: { ev: EventRow; fresh?: boolean }) {
  const img = clipUrl(ev.thumb_file);
  const today = dayLabel(ev.created_at) === "Bugun";
  const cls = ["tile", ev.score >= 75 ? "high" : "", fresh ? "fresh" : ""].filter(Boolean).join(" ");
  return (
    <Link href={`/events/?uid=${encodeURIComponent(ev.uid)}`} className={cls} data-uid={ev.uid}>
      {img ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={img} alt="" loading="lazy" />
      ) : (
        <span className="tile-noimg">
          <IconCamera />
          {ev.clip_file ? "Kadr yo'q" : "Video yuklanmoqda"}
        </span>
      )}
      <span className="tile-cam">{ev.source_name || "Kamera"}</span>
      <span className="tile-time">{today ? clock(ev.created_at, true) : `${dayLabel(ev.created_at)}, ${clock(ev.created_at)}`}</span>
      <span className="tile-foot">
        <span className="tile-kind">
          {KIND_UZ[ev.kind] ?? (ev.kind || "Shubhali harakat")}
          <small>{ev.branch_name}</small>
        </span>
        <span className="tile-score" aria-label={`ball ${Math.round(ev.score)}`}>
          {Math.round(ev.score)}
        </span>
      </span>
    </Link>
  );
}
