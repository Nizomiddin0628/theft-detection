"""Aniqlikni haqiqiy videolarda o'lchash.

Foydalanuvchi o'z do'konida yozilgan videolarni yuklaydi va undagi haqiqiy hodisalarni (o'g'irlik yoki uning
sahnalashtirilgan namunasi) vaqt oralig'i bilan belgilaydi. Baholashda tizim har videoni jonli kameradagi
kabi tahlil qiladi (Telegram va hodisalar yaratilmaydi) va signallarni belgilar bilan solishtiradi:

  to'g'ri (TP)    — belgilangan hodisa vaqtida signal bo'ldi;
  xato (FP)       — hech qanday belgi bo'lmagan joyda signal;
  o'tkazilgan (FN) — belgilangan hodisaga signal bo'lmadi.

  Aniqlik (precision) = TP / (TP + FP)  — signallarning qanchasi haqiqiy
  Topish (recall)     = TP / (TP + FN)  — haqiqiy hodisalarning qanchasi topildi

Faqat "belgilash tugallangan" videolar baholanadi: belgisi chala video yolg'on "xato signal" ko'rsatadi.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import db
from .ai.clipper import ffmpeg_exe
from .config import APP_VERSION
from .logutil import get_logger

log = get_logger("eval")

TOL_BEFORE = 2.0      # signal belgi boshlanishidan shuncha oldin kelsa ham to'g'ri (shubha boshlanishi)
TOL_AFTER = 6.0       # belgi tugaganidan keyin shuncha soniya ichida kelgan signal ham to'g'ri
LABEL_KINDS = {
    "pocket": "Cho'ntakka", "torso": "Kiyim ichiga", "bag": "Sumkaga", "offscreen": "Kadrdan pastga",
    "exit": "Chiqib ketish", "other": "Boshqa",
}
PLAYABLE_EXT = {".mp4", ".m4v", ".mov", ".webm"}
H264_FOURCC = {"avc1", "h264", "H264", "x264", "X264", "AVC1"}
SETTING_KEYS = ("sensitivity", "threshold", "dwell_sec", "ai_fps", "calm_fps", "use_ml", "ml_weight",
                "infer_size", "person_conf", "object_conf", "personal_items")


# ----------------------------------------------------------------------------- video
def probe(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return {}
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps <= 1 or fps > 240:
            fps = 25.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        fcc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        fourcc = "".join(chr((fcc >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")
        ok = cap.read()[0]
        return {"fps": float(fps), "duration": float(n / fps) if n else 0.0, "ok": bool(ok),
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fourcc": fourcc}
    finally:
        cap.release()


def needs_transcode(path: Path, info: dict) -> bool:
    """Brauzer o'ynata olmaydigan video (HEVC iPhone videosi, AVI, MKV) — ko'rish uchun H.264 nusxa kerak."""
    return path.suffix.lower() not in PLAYABLE_EXT or info.get("fourcc") not in H264_FOURCC


def make_playable(vid: int, src: Path) -> None:
    """Fon oqimida: brauzerda belgilash uchun H.264 MP4 nusxa (eni 1280 dan oshmaydi)."""
    exe = ffmpeg_exe()
    dst = src.with_name(src.stem + "_play.mp4")
    if not exe:
        db.execute("UPDATE eval_videos SET play_status='xato' WHERE id=?", (vid,))
        return
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale='min(1280,iw)':-2", "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(dst)]
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, timeout=3600, creationflags=flags)
        ok = r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:  # noqa: BLE001
        log.exception("Videoni H.264 ga o'tkazishda xato")
        ok = False
    db.execute("UPDATE eval_videos SET play_path=?, play_status=? WHERE id=?",
               (str(dst) if ok else None, "tayyor" if ok else "xato", vid))


def clean_labels(raw) -> list[dict]:
    """Belgilarni tekshirish va tartiblash."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    out = []
    for lb in raw or []:
        try:
            a, b = float(lb.get("start")), float(lb.get("end"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        a, b = max(0.0, min(a, b)), max(a, b)
        if b - a < 0.2:
            b = a + 0.2
        kind = lb.get("kind") if lb.get("kind") in LABEL_KINDS else "other"
        out.append({"start": round(a, 2), "end": round(b, 2), "kind": kind,
                    "note": str(lb.get("note") or "")[:200]})
    return sorted(out, key=lambda x: x["start"])


# ----------------------------------------------------------------------------- tahlil va solishtirish
def analyze(path: str, zone_source_id: int | None = None, stop_evt: threading.Event | None = None,
            progress=None) -> dict:
    """Videoni jonli kameradagi kabi tahlil qiladi; signallar ro'yxatini qaytaradi (hodisa yaratilmaydi)."""
    from .ai.worker import Processor

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("Videoni ochib bo'lmadi")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 1 or fps > 240:
        fps = 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ai_fps = max(1.0, db.fnum("ai_fps", 8))
    calm_fps = db.fnum("calm_fps", 4)
    proc = Processor("Baholash", source_id=zone_source_id, background=True)
    alerts: list[dict] = []
    idx = 0
    last_ai = -1e9
    t_cpu = time.time()
    try:
        while not (stop_evt and stop_evt.is_set()):
            if not cap.grab():
                break
            t = idx / fps
            idx += 1
            if t - last_ai < proc.interval(ai_fps, calm_fps) - 0.5 / fps:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            last_ai = t
            _, _, al, _ = proc.step(frame, t)
            for a in al:
                alerts.append({"t": round(float(t), 2), "kind": a["kind"], "kind_label": a["kind_label"],
                               "score": round(float(a["score"]), 1),
                               "susp_start": round(float(a.get("susp_start", t)), 2),
                               "reasons": a["reasons"][:4]})
            if progress and total and idx % 15 == 0:
                progress(min(1.0, idx / total))
    finally:
        cap.release()
    return {"alerts": alerts, "duration": round(idx / fps, 2), "steps": dict(proc.counts),
            "seconds": round(time.time() - t_cpu, 1)}


def match(labels: list[dict], alerts: list[dict]) -> dict:
    """Belgilar va signallarni vaqt bo'yicha solishtirish.

    1) Har belgiga o'z oynasidagi eng birinchi bo'sh signal (TP). 2) Qolgan signallar biror belgi oynasiga
    tushsa — takroriy (xato emas), aks holda — xato signal (FP).
    """
    alerts = sorted(alerts, key=lambda a: a["t"])
    win = lambda lb: (lb["start"] - TOL_BEFORE, lb["end"] + TOL_AFTER)  # noqa: E731
    used: set[int] = set()
    tp, fn = [], []
    for lb in sorted(labels, key=lambda x: x["start"]):
        lo, hi = win(lb)
        cand = [i for i, a in enumerate(alerts) if i not in used and lo <= a["t"] <= hi]
        if cand:
            a = alerts[cand[0]]
            used.add(cand[0])
            tp.append({"label": lb, "alert": a, "delay": round(a["t"] - lb["start"], 2),
                       "kind_ok": a["kind"] == lb["kind"] or lb["kind"] == "other"})
        else:
            fn.append({"label": lb})
    fp, dup = [], 0
    for i, a in enumerate(alerts):
        if i in used:
            continue
        if any(win(lb)[0] <= a["t"] <= win(lb)[1] for lb in labels):
            dup += 1
        else:
            fp.append({"alert": a})
    return {"tp": tp, "fp": fp, "fn": fn, "dup": dup}


def summarize(per_video: list[dict]) -> dict:
    tp = sum(len(v["tp"]) for v in per_video)
    fp = sum(len(v["fp"]) for v in per_video)
    fn = sum(len(v["fn"]) for v in per_video)
    dup = sum(v["dup"] for v in per_video)
    hours = sum(v["duration"] for v in per_video) / 3600.0
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    delays = [x["delay"] for v in per_video for x in v["tp"]]
    return {"tp": tp, "fp": fp, "fn": fn, "dup": dup, "hours": round(hours, 3),
            "precision": precision, "recall": recall, "f1": f1,
            "fp_per_hour": round(fp / hours, 2) if hours > 0 else None,
            "latency_med": round(float(np.median(delays)), 2) if delays else None}


# ----------------------------------------------------------------------------- fon ishchisi
class EvalRunner:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.state = {"running": False, "run_id": None, "progress": 0.0, "current": "", "done": 0, "total": 0}

    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, video_ids: list[int] | None = None) -> int:
        if self.busy():
            raise RuntimeError("Baholash allaqachon ishlayapti")
        q = "SELECT * FROM eval_videos WHERE reviewed=1"
        vids = db.rows(q + " ORDER BY id")
        if video_ids:
            want = {int(x) for x in video_ids}
            vids = [v for v in vids if v["id"] in want]
        if not vids:
            raise ValueError("Baholash uchun video yo'q: videoni yuklang, hodisalarni belgilang va "
                             "\"Belgilash tugallandi\" ni yoqing")
        s = db.get_settings()
        run_id = db.execute(
            "INSERT INTO eval_runs(created_at, status, progress, version, settings, videos) VALUES(?,?,?,?,?,?)",
            (time.time(), "ishlanmoqda", 0.0, APP_VERSION,
             json.dumps({k: s.get(k) for k in SETTING_KEYS}, ensure_ascii=False), len(vids)))
        self.stop_evt.clear()
        self.state = {"running": True, "run_id": run_id, "progress": 0.0, "current": "", "done": 0,
                      "total": len(vids)}
        self.thread = threading.Thread(target=self._run, args=(run_id, vids), daemon=True, name="eval")
        self.thread.start()
        return run_id

    def stop(self) -> None:
        self.stop_evt.set()

    def _run(self, run_id: int, vids: list[dict]) -> None:
        per_video, err = [], None
        total_dur = sum(max(v.get("duration") or 1.0, 1.0) for v in vids)
        done_dur = 0.0
        try:
            for v in vids:
                if self.stop_evt.is_set():
                    break
                self.state["current"] = v["filename"]
                dur = max(v.get("duration") or 1.0, 1.0)

                def prog(x, base=done_dur, d=dur):
                    self.state["progress"] = min(0.999, (base + x * d) / total_dur)

                res = analyze(v["path"], v.get("zone_source_id"), self.stop_evt, prog)
                if self.stop_evt.is_set():
                    break
                m = match(clean_labels(v["labels"]), res["alerts"])
                per_video.append({"video_id": v["id"], "filename": v["filename"], "duration": res["duration"],
                                  "labels": len(clean_labels(v["labels"])), "alerts": res["alerts"],
                                  "steps": res["steps"], "seconds": res["seconds"], **m})
                done_dur += dur
                self.state["done"] += 1
                self.state["progress"] = min(0.999, done_dur / total_dur)
                db.execute("UPDATE eval_runs SET progress=? WHERE id=?", (round(self.state["progress"] * 100, 1), run_id))
        except Exception as e:  # noqa: BLE001
            err = str(e)
            log.exception("Baholashda xato")
        sm = summarize(per_video)
        status = "xato" if err else ("toxtatildi" if self.stop_evt.is_set() else "tayyor")
        db.execute("UPDATE eval_runs SET finished_at=?, status=?, progress=?, hours=?, tp=?, fp=?, fn=?, dup=?, "
                   "precision=?, recall=?, f1=?, fp_per_hour=?, latency_med=?, details=?, error=?, videos=? "
                   "WHERE id=?",
                   (time.time(), status, 100.0 if status == "tayyor" else round(self.state["progress"] * 100, 1),
                    sm["hours"], sm["tp"], sm["fp"], sm["fn"], sm["dup"], sm["precision"], sm["recall"], sm["f1"],
                    sm["fp_per_hour"], sm["latency_med"], json.dumps(per_video, ensure_ascii=False), err,
                    len(per_video), run_id))
        self.state.update({"running": False, "progress": 1.0, "current": ""})
        log.info("Baholash #%d tugadi: %s, TP %d FP %d FN %d", run_id, status, sm["tp"], sm["fp"], sm["fn"])


_runner: EvalRunner | None = None


def get_runner() -> EvalRunner:
    global _runner
    if _runner is None:
        _runner = EvalRunner()
    return _runner
