"""Dalil paketi (2.3.0, 7-bosqich): bitta hodisa uchun ZIP — klip, rasmlar, PDF hisobot va SHA-256 ro'yxati.

Paket ichida:
  hodisa_<id>.pdf        hisobot: do'kon, kamera, vaqt, turi, ball, sabablar, ball tarkibi, kim qachon tekshirdi, izoh,
                         rasm (signal lahzasi) va odam qirqimi, har faylning SHA-256 xeshi
  klip.mp4, rasm.jpg, odam.jpg   asl fayllar (o'zgartirilmagan)
  manifest.json          hodisa ma'lumoti va har faylning SHA-256 / hajmi
  TEKSHIRISH.txt         xeshni Windows'da tekshirish: certutil -hashfile klip.mp4 SHA256

Paket hech kimni ayblamaydi: u "tizim nima ko'rdi va odam qanday qaror qildi" ni bir joyga yig'adi. Shaxsiy ma'lumot
(video) — faqat qonun doirasida, vakolatli shaxslarga berilsin.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile

from . import db, pdfmini
from .config import APP_VERSION, CLIPS_DIR

STATUS_UZ = {"yangi": "Tekshirilmagan", "tasdiqlangan": "Tasdiqlangan (haqiqatan shunday)", "rad_etilgan": "Xato signal"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _fmt(ts) -> str:
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(float(ts))) if ts else "-"


def _jpeg(path) -> bytes | None:
    """PDF uchun: rasmni 3 kanalli oddiy JPEG ga (asl fayl ZIP'da o'zgarmasdan qoladi)."""
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        if w > 1280:
            img = cv2.resize(img, (1280, int(h * 1280 / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None
    except Exception:  # noqa: BLE001
        return None


def build(event_id: int) -> tuple[bytes, str]:
    """(ZIP baytlari, fayl nomi). Hodisa yo'q bo'lsa — KeyError."""
    from .ai.safety import KIND_LABELS as SAFE_LABELS, SAFETY_KINDS
    from .telegram_bot import KIND_UZ, susp_info

    ev = db.get_event(event_id)
    if not ev:
        raise KeyError(event_id)
    safe = ev.get("kind") in SAFETY_KINDS
    kind = KIND_UZ.get(ev.get("kind"), SAFE_LABELS.get(ev.get("kind"), ev.get("kind")))
    store = db.get_setting("store_name", "")
    files: list[tuple[str, bytes]] = []
    for key, name in (("clip_path", "klip.mp4"), ("thumb_path", "rasm.jpg"), ("crop_path", "odam.jpg")):
        fn = ev.get(key)
        p = CLIPS_DIR / fn if fn else None
        if p is not None and p.is_file():
            files.append((name, p.read_bytes()))
    hashes = {n: {"sha256": _sha(b), "bytes": len(b)} for n, b in files}
    si = susp_info(ev)

    doc = pdfmini.Doc(f"Hushyor AI - hodisa #{ev['id']}")
    doc.text(f"Hodisa #{ev['id']}: {kind}", size=16, bold=True)
    doc.text("Xavfsizlik signali" if safe else "Shubhali harakat signali (ayblov emas)", size=10,
             color=(0.45, 0.45, 0.5))
    doc.rule()
    doc.kv("Do'kon", store or "-")
    doc.kv("Kamera", ev.get("source_name") or "-")
    doc.kv("Vaqt", _fmt(ev.get("created_at")) + (f" (videoda {ev['video_time']:.1f}-soniya)"
                                                  if ev.get("video_time") is not None else ""))
    if si.get("susp_label"):
        doc.kv("Lahza", si["susp_label"] + (f", klipning {si['susp_offset']:.0f}-soniyasida"
                                            if si.get("susp_offset") is not None else ""))
    if not safe:
        doc.kv("Ball", f"{float(ev.get('score') or 0):.0f}/100 ({ev.get('level') or '-'})")
        if ev.get("ml_prob") is not None:
            doc.kv("AI model", f"{float(ev['ml_prob']) * 100:.0f}% ehtimollik")
    doc.kv("Holat", STATUS_UZ.get(ev.get("status"), ev.get("status") or "-"))
    if ev.get("reviewed_by"):
        doc.kv("Tekshirdi", f"{ev['reviewed_by']}, {_fmt(ev.get('reviewed_at'))}")
    if ev.get("note"):
        doc.kv("Izoh", ev["note"])
    doc.space(6)
    doc.text("Nima aniqlandi" if safe else "Sabablar", size=12, bold=True)
    for r in ev.get("reasons") or []:
        doc.text(f"\x95 {r}", indent=8)
    bd = ev.get("breakdown") or []
    if bd and not safe:
        doc.space(4)
        doc.text("Ball tarkibi", size=12, bold=True)
        for item in bd:
            try:
                doc.text(f"{item[0]}: {'+' if float(item[1]) > 0 else ''}{float(item[1]):.0f}", indent=8)
            except (TypeError, ValueError, IndexError):
                continue
    for name, cap in (("rasm.jpg", "Signal lahzasi (klipdan)"), ("odam.jpg", "Odam (hodisa lahzasi)")):
        b = next((b for n, b in files if n == name), None)
        p = CLIPS_DIR / ev["thumb_path" if name == "rasm.jpg" else "crop_path"] if b is not None else None
        jp = _jpeg(p) if p is not None else None
        if jp:
            doc.space(6)
            doc.image(jp, max_h=280 if name == "rasm.jpg" else 220, caption=cap)
    doc.space(6)
    doc.text("Fayllar va SHA-256 (o'zgarmaganini tekshirish uchun)", size=12, bold=True)
    if not files:
        doc.text("Fayl yo'q (klip o'chirilgan yoki yaratilmagan)", indent=8)
    for n, h in hashes.items():
        doc.text(f"{n} ({h['bytes']:,} bayt)".replace(",", " "), indent=8, bold=True, size=9)
        doc.text(h["sha256"], indent=8, size=8)
    doc.space(8)
    doc.rule()
    doc.text(f"Yaratildi: {_fmt(time.time())}, Hushyor AI {APP_VERSION}, qurilma {db.get_setting('device_id', '-')}.",
             size=8, color=(0.4, 0.4, 0.45))
    doc.text("Bu hujjat hech kimni ayblamaydi: u tizim nimani ko'rgani va odam qanday qaror qilganini qayd etadi. Video "
             "shaxsiy ma'lumot — faqat qonun doirasida, vakolatli shaxslarga bering.", size=8, color=(0.4, 0.4, 0.45))
    pdf = doc.render()
    pdf_name = f"hodisa_{ev['id']:05d}.pdf"
    files.insert(0, (pdf_name, pdf))
    hashes = {n: {"sha256": _sha(b), "bytes": len(b)} for n, b in files}
    manifest = {"event_id": ev["id"], "kind": ev.get("kind"), "kind_label": kind, "store": store,
                "camera": ev.get("source_name"), "created_at": ev.get("created_at"), "created": _fmt(ev.get("created_at")),
                "status": ev.get("status"), "reviewed_by": ev.get("reviewed_by"), "reviewed_at": ev.get("reviewed_at"),
                "score": ev.get("score"), "reasons": ev.get("reasons"), "note": ev.get("note"),
                "app_version": APP_VERSION, "device_id": db.get_setting("device_id", ""),
                "generated_at": time.time(), "files": hashes}
    readme = ("Hushyor AI - dalil paketi\r\n\r\n"
              f"Hodisa #{ev['id']}. Fayllar o'zgarmaganini tekshirish (Windows PowerShell):\r\n"
              + "".join(f"  certutil -hashfile {n} SHA256    -> {h['sha256']}\r\n" for n, h in hashes.items())
              + "\r\nXesh manifest.json dagi bilan bir xil bo'lishi kerak.\r\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files:
            z.writestr(n, b, compress_type=zipfile.ZIP_STORED if n.endswith((".mp4", ".jpg")) else zipfile.ZIP_DEFLATED)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("TEKSHIRISH.txt", readme.encode("ascii", "replace"))
    return buf.getvalue(), f"hodisa_{ev['id']:05d}_dalil.zip"
