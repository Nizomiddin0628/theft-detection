"""3.0.0 "Video sinovi" sinovi: bulut paneli → bulut → edge (yuklab olish, tekshirish, navbat) → natija qaytadi.

Bulut haqiqiy (uvicorn), edge — haqiqiy SyncWorker va app/cloud_tests.py; tahlil (FileJob) o'rnida soxta menejer:
hodisalarni job_id bilan yozadi, odatdagi navbat ularni bulutga yetkazadi. Shuning uchun model kerak emas.
Tekshiriladi: cheklovlar (kengaytma, imzo, hajm, kvota, eski edge, rol), to'liq oqim, sha256 buzilsa qayta urinish
va xato, bekor qilish (edge olguncha va olgandan keyin), dastur qayta ishga tushganda qaytadan tahlil, eski sinovlarni
tozalash, panel sahifasi (brauzerda: yuklash, holat, natija). SQLite; CLOUD_TEST_PG_URL bilan PostgreSQL ham.

Ishga tushirish:  python -m tests.test_video_tests
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
TOKEN = "vt-sinov-token-1234567890"
DEV = "vtdokon01"
os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_vt_edge_")
os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_vt_cloud_")
os.environ["CLOUD_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["EDGE_TOKEN"] = TOKEN
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["CLOUD_TELEGRAM_BOT_TOKEN"] = ""
os.environ["CLOUD_VIDEO_MAX_MB"] = "5"
os.environ.pop("CLOUD_DB_URL", None)

import httpx  # noqa: E402

from app import cloud_tests, db, sync  # noqa: E402
from app.config import CLIPS_DIR  # noqa: E402
from cloud import server as cloud  # noqa: E402
from cloud import video_tests as vt  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))


def wait(cond, timeout=20.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def make_video(path: Path, seconds: float = 2.0) -> Path:
    """Haqiqiy (OpenCV o'qiy oladigan) kichik MP4 — edge tekshiruvidan o'tishi kerak."""
    import cv2
    import numpy as np

    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (96, 64))
    for i in range(int(seconds * 10)):
        f = np.zeros((64, 96, 3), np.uint8)
        f[:, (i * 4) % 96:(i * 4) % 96 + 8] = (40, 200, 160)
        w.write(f)
    w.release()
    return path


class FakeManager:
    """FileJob o'rnida: queue_job → 'ishlanmoqda' → hodisalar (job_id bilan) → 'tayyor'. hold=True — to'xtatilguncha."""

    def __init__(self):
        self.notifier = None
        self.queued: list[int] = []
        self.hold = False
        self.stopped: set[int] = set()

    def source_info(self):
        return [{"id": 3, "name": "Kassa kamerasi", "kind": "rtsp", "state": "ishlayapti", "running": True},
                {"id": 9, "name": "Video: eski.mp4", "kind": "file", "state": "tayyor", "running": False}]

    def emit(self, *_a):
        pass

    def queue_job(self, jid: int) -> None:
        self.queued.append(jid)
        threading.Thread(target=self._run, args=(jid,), daemon=True).start()

    def stop_job(self, jid: int) -> None:
        self.stopped.add(jid)

    def _run(self, jid: int) -> None:
        db.execute("UPDATE jobs SET status='ishlanmoqda', progress=40 WHERE id=?", (jid,))
        sync.hello_soon()                       # haqiqiy Manager ham shunday qiladi (_job_loop)
        t_end = time.time() + 30
        while self.hold and jid not in self.stopped and time.time() < t_end:
            time.sleep(0.05)
        if jid in self.stopped:
            db.execute("UPDATE jobs SET status='toxtatildi', finished_at=? WHERE id=?", (time.time(), jid))
            sync.hello_soon()
            return
        evs = []
        for i, (kind, score) in enumerate((("pocket", 82), ("fall", 0))):
            ev = db.insert_event({"source_id": None, "source_name": "Video: sinov.mp4", "job_id": jid,
                                  "created_at": time.time(), "video_time": 3.0 + i * 4, "t_detect": time.time(),
                                  "score": score, "level": "yuqori", "kind": kind, "reasons": ["sinov sababi"],
                                  "breakdown": {}, "track_id": i + 1, "status": "yangi"})
            evs.append(ev)
        clip = f"ev{evs[0]:05d}_vt.mp4"
        (CLIPS_DIR / clip).write_bytes(os.urandom(4000))
        thumb = f"ev{evs[0]:05d}_vt.jpg"
        (CLIPS_DIR / thumb).write_bytes(b"\xff\xd8\xff" + os.urandom(500))
        db.update_event(evs[0], clip_path=clip, thumb_path=thumb)
        db.execute("UPDATE jobs SET status='tayyor', progress=100, events_count=?, duration=?, frames=?, finished_at=? "
                   "WHERE id=?", (len(evs), 12.5, 125, time.time(), jid))
        sync.hello_soon()


def reset_edge() -> None:
    """Har to'plam yangi bulut bazasi bilan — edge'dagi eski sinov/hodisa/navbat qatorlari aralashmasin."""
    for t in ("jobs", "outbox", "events"):
        db.execute(f"DELETE FROM {t}")
    db.execute("INSERT OR IGNORE INTO sources(id, name, kind, uri, enabled, loop, created_at) VALUES(3,?,?,?,0,0,?)",
               ("Kassa kamerasi", "rtsp", "rtsp://127.0.0.1/sinov", time.time()))
    db.set_settings({"device_id": DEV})


def serve():
    import uvicorn

    srv = uvicorn.Server(uvicorn.Config(cloud.app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    return srv


def suite(label: str) -> None:
    print(f"\n--- {label} ---")
    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    cloud._state.update(tokens=[], admin="", fail=False, fail_paths=())
    cloud._login_fails.clear()
    for f in vt.VIDEO_DIR.glob("*") if vt.VIDEO_DIR.is_dir() else []:
        f.unlink()
    reset_edge()
    comp = cdb.create_company("Video Sinov MChJ")
    cdb.create_user(comp, "egasi@vt.uz", "VtParol-2026x", "Egasi", "owner")
    bid, _tok = cdb.create_branch(comp, "Chilonzor", DEV)
    cdb.execute("UPDATE branches SET token_hash=:h WHERE id=:b", {"h": cdb.sha256(TOKEN), "b": bid})
    cdb.create_user(comp, "qorovul@vt.uz", "VtParol-2026y", "Qo'riqchi", "guard", [bid])
    base = f"http://127.0.0.1:{PORT}"
    owner = httpx.Client(base_url=base, timeout=30)
    r = owner.post("/api/v1/auth/login", json={"email": "egasi@vt.uz", "password": "VtParol-2026x"})
    ok(f"{label}: egasi panelga kirdi", r.status_code == 200, r.text[:120])
    guard = httpx.Client(base_url=base, timeout=30)
    guard.post("/api/v1/auth/login", json={"email": "qorovul@vt.uz", "password": "VtParol-2026y"})
    comp_b = cdb.create_company("Boshqa MChJ")
    cdb.create_user(comp_b, "begona@vt.uz", "VtParol-2026z", "Begona", "owner")
    other = httpx.Client(base_url=base, timeout=30)
    other.post("/api/v1/auth/login", json={"email": "begona@vt.uz", "password": "VtParol-2026z"})

    tmp = Path(tempfile.mkdtemp(prefix="vt_files_"))
    video = make_video(tmp / "kassa sinov.mp4")
    data = video.read_bytes()

    def up(client, body, name="kassa sinov.mp4", **q):
        params = {"branch_id": bid, "name": name, **q}
        return client.post("/api/v1/video-tests", params=params, content=body,
                           headers={"Content-Type": "application/octet-stream"})

    # --- cheklovlar (edge hali ulanmagan: versiya yo'q)
    r = up(owner, data)
    ok(f"{label}: do'kon kompyuteri ulanmagan/eski — 409", r.status_code == 409, r.text[:120])
    cdb.execute("UPDATE branches SET version='2.9.0' WHERE id=:b", {"b": bid})
    ok(f"{label}: eski edge (2.9.0) — 409", up(owner, data).status_code == 409)
    with cdb.engine().begin() as c:                 # oldingi hello'dan qolgan holat: versiya va kameralar
        c.execute(cdb.branches.update().where(cdb.branches.c.id == bid).values(
            version="3.0.0", last_seen=time.time(),
            cameras=[{"id": 3, "name": "Kassa kamerasi", "kind": "rtsp"}, {"id": 9, "name": "Video: x", "kind": "file"}]))
    ok(f"{label}: video emas (kengaytma) — 400", up(owner, b"salom", name="hujjat.pdf").status_code == 400)
    ok(f"{label}: imzo video emas (.mp4 nomli matn) — 400", up(owner, b"bu video emas" * 50).status_code == 400)
    ok(f"{label}: qo'riqchi yuklay olmaydi — 403", up(guard, data).status_code == 403)
    big = up(owner, b"\x00\x00\x00\x18ftypmp42" + b"0" * (6 * 1024 * 1024))
    ok(f"{label}: CLOUD_VIDEO_MAX_MB dan katta — 413", big.status_code == 413, str(big.status_code))
    ok(f"{label}: rad etilganlardan keyin bulutda fayl qolmadi", not any(vt.VIDEO_DIR.glob("*")))
    lst = owner.get("/api/v1/video-tests").json()
    br = next((b for b in lst["branches"] if b["id"] == bid), {})
    ok(f"{label}: ro'yxat — filial tayyor, kamera (fayl emas) zonalar uchun", br.get("ok") and br.get("online")
       and br.get("cameras") == [{"id": 3, "name": "Kassa kamerasi"}] and lst["can_upload"]
       and lst["limits"]["max_mb"] == 5, str(br)[:160])

    # --- to'liq oqim: yuklash → edge oladi → tahlil (soxta) → natija
    r = up(owner, data, zone_source_id=3)
    t1 = r.json().get("test", {}) if r.status_code == 200 else {}
    ok(f"{label}: yuklandi — holat 'kutilmoqda', zona nomi saqlandi", r.status_code == 200 and t1.get("status") == "kutilmoqda"
       and t1.get("zone_source_name") == "Kassa kamerasi", r.text[:160])
    f1 = vt.VIDEO_DIR / f"{t1.get('id')}.mp4"
    ok(f"{label}: video bulutda (hajm mos), edge buyrug'i navbatda", f1.is_file() and f1.stat().st_size == len(data)
       and cdb.scalar("SELECT COUNT(*) FROM commands WHERE type='video_test' AND acked_at IS NULL") == 1)
    mgr = FakeManager()
    w = sync.SyncWorker(manager=mgr)
    sync._worker = w
    w.start()
    st = wait(lambda: (vt.get(t1["id"]) or {}).get("status") == "tayyor", 30)
    t1d = vt.get(t1["id"]) or {}
    ok(f"{label}: edge oldi, tekshirdi, tahlil qildi — 'tayyor', 2 signal, davomiylik", st and t1d.get("events") == 2
       and abs(float(t1d.get("duration") or 0) - 12.5) < 0.01 and t1d.get("job_id"), str({k: t1d.get(k) for k in
                                                                                         ("status", "events", "error")}))
    job = db.row("SELECT * FROM jobs WHERE cloud_test_id=?", (t1["id"],)) or {}
    ok(f"{label}: edge vazifasi — zona kamerasi (3) va sha256 bilan", job.get("zone_source_id") == 3
       and Path(job.get("path") or "x").is_file() and mgr.queued == [job.get("id")])
    ok(f"{label}: tugagach video bulutdan o'chirildi", not f1.exists() and t1d.get("file_deleted"))
    det = wait(lambda: (lambda d: d if len(d.get("events") or []) == 2 and any(e.get("clip_file") for e in d["events"])
                        else None)(owner.get(f"/api/v1/video-tests/{t1['id']}").json()), 20) or {}
    evs = det.get("events") or []
    ok(f"{label}: natija — 2 hodisa (klip va rasm bilan), video vaqti", len(evs) == 2 and evs[0].get("thumb_file")
       and evs[0].get("video_time") == 3.0, str(evs)[:200])
    ok(f"{label}: video tahlili uchun bildirishnoma yo'q (jonli emas)",
       (cdb.scalar("SELECT COUNT(*) FROM notify_outbox") or 0) == 0)
    ok(f"{label}: boshqa kompaniya sinovni ko'ra/bekor/o'chira olmaydi (404), uning filialiga yuklay olmaydi (403)",
       other.get(f"/api/v1/video-tests/{t1['id']}").status_code == 404
       and other.post(f"/api/v1/video-tests/{t1['id']}/cancel", json={}).status_code == 404
       and other.delete(f"/api/v1/video-tests/{t1['id']}").status_code == 404
       and up(other, data).status_code == 403
       and all(t["id"] != t1["id"] for t in other.get("/api/v1/video-tests").json()["tests"]))
    ok(f"{label}: sessiyasiz — 401", httpx.get(f"{base}/api/v1/video-tests").status_code == 401
       and httpx.post(f"{base}/api/v1/video-tests", content=b"x").status_code == 401)
    ok(f"{label}: qo'riqchi natijani ko'radi, lekin bekor/o'chira olmaydi",
       guard.get(f"/api/v1/video-tests/{t1['id']}").status_code == 200
       and guard.delete(f"/api/v1/video-tests/{t1['id']}").status_code == 403)

    # --- bekor qilish: edge olguncha (worker to'xtatilgan)
    w.stop()
    w.join(10)
    r = up(owner, data, name="ikkinchi.mov")
    t2 = r.json()["test"]
    c2 = owner.post(f"/api/v1/video-tests/{t2['id']}/cancel", json={}).json()
    ok(f"{label}: edge olmasdan bekor — buyruq olib tashlandi, fayl o'chdi", c2.get("status") == "bekor"
       and not (vt.VIDEO_DIR / f"{t2['id']}.mov").exists()
       and cdb.scalar("SELECT COUNT(*) FROM commands WHERE acked_at IS NULL") == 0)

    # --- bekor qilish: edge olgandan keyin (tahlil davom etmoqda)
    mgr.hold = True
    w = sync.SyncWorker(manager=mgr)
    sync._worker = w
    w.start()
    t3 = up(owner, data, name="uchinchi.mp4").json()["test"]
    wait(lambda: (vt.get(t3["id"]) or {}).get("status") == "ishlanmoqda", 30)
    ok(f"{label}: tahlil paytida panelda 'ishlanmoqda' va foiz", (vt.get(t3["id"]) or {}).get("status") == "ishlanmoqda"
       and float((vt.get(t3["id"]) or {}).get("progress") or 0) == 40)
    owner.post(f"/api/v1/video-tests/{t3['id']}/cancel", json={})
    j3 = wait(lambda: (lambda j: j if j and j["status"] == "toxtatildi" else None)(
        db.row("SELECT * FROM jobs WHERE cloud_test_id=?", (t3["id"],))), 30)
    ok(f"{label}: edge'ga bekor buyrug'i yetdi — tahlil to'xtatildi", bool(j3) and j3["id"] in mgr.stopped)
    time.sleep(1.0)
    ok(f"{label}: bulutda 'bekor' qoladi", (vt.get(t3["id"]) or {}).get("status") == "bekor")
    mgr.hold = False

    # --- buzilgan fayl: sha256 mos emas → qayta urinish → xato
    cloud_tests.RETRY_DELAYS = (0.2, 0.2)
    w.stop()
    w.join(10)
    t4 = up(owner, data, name="buzilgan.mp4").json()["test"]
    p4 = vt.VIDEO_DIR / f"{t4['id']}.mp4"
    raw = bytearray(p4.read_bytes())
    raw[-10] ^= 0xFF
    p4.write_bytes(bytes(raw))
    w = sync.SyncWorker(manager=mgr)
    sync._worker = w
    w.start()
    st4 = wait(lambda: (vt.get(t4["id"]) or {}).get("status") == "xato", 30)
    t4d = vt.get(t4["id"]) or {}
    ok(f"{label}: sha256 mos emas — 2 marta qayta urinib, 'xato' (sababi bilan)", st4 and "yuklab bo'lmadi" in
       str(t4d.get("error")), str(t4d.get("error"))[:120])
    ok(f"{label}: edge'da chala fayl qolmadi", not list(Path(os.environ["HUSHYOR_DATA_DIR"], "uploads").glob("*.part")))

    # --- dastur qayta ishga tushdi (tahlil paytida) → yangi qator bilan qaytadan navbat (faqat recover(), init_db emas)
    w.stop()
    w.join(10)
    t5 = up(owner, data, name="qayta.mp4").json()["test"]
    jid_old = db.execute("INSERT INTO jobs(filename, path, status, progress, created_at, cloud_test_id, cloud_meta) "
                         "VALUES(?,?,?,?,?,?,?)", ("qayta.mp4", str(video), "ishlanmoqda", 55, time.time(), t5["id"], "{}"))
    db.init_db()                                    # boshqa buyruq (masalan, model o'qitish) — bulut sinoviga tegmaydi
    ok(f"{label}: init_db (boshqa buyruqlar) bulut sinoviga tegmaydi",
       (db.row("SELECT status FROM jobs WHERE id=?", (jid_old,)) or {}).get("status") == "ishlanmoqda")
    cloud_tests.recover()
    old = db.row("SELECT * FROM jobs WHERE id=?", (jid_old,)) or {}
    new = db.row("SELECT * FROM jobs WHERE cloud_test_id=?", (t5["id"],)) or {}
    ok(f"{label}: qayta ishga tushish — eski qator yopildi, yangisi 'navbatda'", old.get("status") == "toxtatildi"
       and old.get("cloud_test_id") is None and new.get("status") == "navbatda" and new.get("id") != jid_old)
    rep = {x["id"]: x for x in cloud_tests.report()}
    ok(f"{label}: hello hisobotida yangi vazifa", rep.get(t5["id"], {}).get("job_id") == new.get("id"))
    for _ in range(cloud_tests.MAX_RESTARTS):          # video har safar dasturni yiqitsa — cheksiz aylanmasin
        db.execute("UPDATE jobs SET status='ishlanmoqda' WHERE cloud_test_id=?", (t5["id"],))
        cloud_tests.recover()
    last = db.row("SELECT * FROM jobs WHERE cloud_test_id=? ORDER BY id DESC LIMIT 1", (t5["id"],)) or {}
    ok(f"{label}: {cloud_tests.MAX_RESTARTS} martadan ko'p chala qolsa — 'xato', qaytadan navbatga qo'yilmaydi",
       last.get("status") == "xato" and "chala qoldi" in str(last.get("error")), str(last.get("error"))[:80])

    # --- dastur to'xtayotganda yuklab olish uzilsa — "xato" emas, keyingi ishga tushishda davom etadi
    import types

    jdl = db.execute("INSERT INTO jobs(filename, path, status, progress, created_at, cloud_test_id, cloud_meta) "
                     "VALUES(?,?,?,?,?,?,?)", ("dl.mp4", str(Path(os.environ["HUSHYOR_DATA_DIR"]) / "uploads" / "dl.mp4"),
                                               "yuklanmoqda", 0, time.time(), 999999,
                                               '{"size": 10, "sha256": "' + "0" * 64 + '", "tries": 0}'))
    closed = httpx.Client()
    closed.close()
    stopped = threading.Event()
    stopped.set()
    fake = types.SimpleNamespace(url=f"http://127.0.0.1:{PORT}", client=closed, stop_evt=stopped, manager=None)
    res = cloud_tests.download(jdl, fake)
    ok(f"{label}: to'xtash paytida yuklab olish uzildi — holat 'yuklanmoqda' qoladi (resume)", res == "yuklanmoqda"
       and (db.row("SELECT status FROM jobs WHERE id=?", (jdl,)) or {}).get("status") == "yuklanmoqda", res)
    db.execute("DELETE FROM jobs WHERE id=?", (jdl,))

    # --- boshqa sinov haqidagi hisobot (token mos emas) va tugagan sinovga kech kelgan hisobot e'tiborsiz
    with cdb.engine().begin() as c:
        n_bad = vt.apply_edge_report(c, {"id": bid}, [{"id": t5["id"], "token": "boshqa", "status": "tayyor", "events": 9}])
    t1_before = vt.get(t1["id"]) or {}
    with cdb.engine().begin() as c:
        n_late = vt.apply_edge_report(c, {"id": bid}, [{"id": t1["id"], "token": t1_before.get("token"),
                                                        "status": "xato", "error": "kech"}])
    ok(f"{label}: token mos emas yoki sinov tugagan — hisobot qo'llanmaydi", n_bad == 0 and n_late == 0
       and (vt.get(t1["id"]) or {}).get("status") == "tayyor" and (vt.get(t5["id"]) or {}).get("status") != "tayyor")
    ok(f"{label}: tugagan sinovni bekor qilib bo'lmaydi (holat saqlanadi)",
       owner.post(f"/api/v1/video-tests/{t1['id']}/cancel", json={}).json().get("status") == "tayyor")

    # --- tozalash: 2 kunda olinmagan sinov → xato, fayl o'chadi
    t6 = up(owner, data, name="eski.mp4").json()["test"]
    cdb.execute("UPDATE video_tests SET created_at=:t WHERE id=:i", {"t": time.time() - 3 * 86400, "i": t6["id"]})
    with cdb.engine().begin() as c:
        vt.sweep(c)
    t6d = vt.get(t6["id"]) or {}
    left = [r for r in cdb.rows("SELECT payload FROM commands WHERE type='video_test' AND acked_at IS NULL")
            if (cdb.json_load(r["payload"]) or {}).get("test_id") == t6["id"]]
    ok(f"{label}: 2 kunda olinmagan sinov — 'xato', fayl va yetkazilmagan buyruq o'chirildi", t6d.get("status") == "xato"
       and not (vt.VIDEO_DIR / f"{t6['id']}.mp4").exists() and not left, str(t6d.get("error"))[:80])

    # --- bir vaqtdagi yuklashlar: zahira chegarani aylanib o'tmasin (faol sinovlar soni)
    active_now = vt.active_count(comp)
    got = [vt.reserve(comp, 1000) for _ in range(vt.ACTIVE_MAX + 2)]
    ok(f"{label}: zahira — {vt.ACTIVE_MAX} tadan ortig'i 429", sum(1 for g in got if g is None) == vt.ACTIVE_MAX - active_now
       and any(g and g[0] == 429 for g in got), str([g[0] if g else None for g in got]))
    for g in got:
        if g is None:
            vt.release(comp, 1000)
    ok(f"{label}: zahira qaytarildi", not vt._reserved)

    # --- kvota va faol sinovlar chegarasi
    os.environ["CLOUD_VIDEO_QUOTA_MB"] = "1"
    keep = up(owner, data, name="kvota1.mp4")
    big2 = up(owner, b"\x00\x00\x00\x18ftypmp42" + os.urandom(1024 * 1024))
    ok(f"{label}: kvota (CLOUD_VIDEO_QUOTA_MB) — 413", keep.status_code == 200 and big2.status_code == 413,
       f"{keep.status_code} {big2.status_code}")
    os.environ.pop("CLOUD_VIDEO_QUOTA_MB", None)

    # --- o'chirish: faol — 409, tugagan — ro'yxatdan
    ok(f"{label}: faol sinovni o'chirib bo'lmaydi — 409",
       owner.delete(f"/api/v1/video-tests/{keep.json()['test']['id']}").status_code == 409)
    ok(f"{label}: tugagan sinov ro'yxatdan o'chiriladi", owner.delete(f"/api/v1/video-tests/{t1['id']}").status_code == 200
       and vt.get(t1["id"]) is None)
    w.stop()
    w.join(10)
    sync._worker = None


def notify_suite() -> None:
    """3.0.0: bulut sinovi hodisalari uchun do'kon Telegram'i va ovozli signali yo'q (_is_cloud_job)."""
    from app.ai import manager as mg

    print("\n--- Do'kon bildirishnomalari ---")
    jc = db.execute("INSERT INTO jobs(filename, path, status, created_at, cloud_test_id) VALUES(?,?,?,?,?)",
                    ("b.mp4", "/x/b.mp4", "tayyor", time.time(), 424242))
    jl = db.execute("INSERT INTO jobs(filename, path, status, created_at) VALUES(?,?,?,?)",
                    ("l.mp4", "/x/l.mp4", "tayyor", time.time()))
    ok("bulut sinovi vazifasi — Telegram/ovoz yo'q; oddiy yuklangan video va jonli kamera — bor",
       mg._is_cloud_job(jc) and not mg._is_cloud_job(jl) and not mg._is_cloud_job(None))
    db.execute("DELETE FROM jobs WHERE id IN (?, ?)", (jc, jl))


def cli_suite() -> None:
    """3.0.0: python -m app.sync --device-id / --connect (ulanishni tekshirish, .env ga yozish)."""
    import contextlib
    import io

    print("\n--- Buyruq qatori (ulash) ---")
    base = f"http://127.0.0.1:{PORT}"
    good = sync.check_connection(base, TOKEN)
    bad = sync.check_connection(base, "notogri-token-1234567890")
    off = sync.check_connection("http://127.0.0.1:9", TOKEN)
    ok("check_connection: to'g'ri token — OK; xato token va yopiq manzil — tushunarli xabar",
       good[0] and not bad[0] and "token xato" in bad[1] and not off[0] and "aloqa yo'q" in off[1], f"{good} {bad} {off}")
    env = Path(tempfile.mkdtemp(prefix="vt_env_")) / ".env"
    env.write_text("\ufeffTELEGRAM_BOT_TOKEN=abc\nCLOUD_URL=\n# izoh\nEDGE_TOKEN=eski\nPORT=8010\n", encoding="utf-8")
    sync.write_env({"CLOUD_URL": base, "EDGE_TOKEN": TOKEN}, env)
    txt = env.read_text(encoding="utf-8")
    ok("write_env: kalitlar almashdi, qolgan qatorlar va izoh joyida, BOM olib tashlandi",
       txt == f"TELEGRAM_BOT_TOKEN=abc\nCLOUD_URL={base}\n# izoh\nEDGE_TOKEN={TOKEN}\nPORT=8010\n", repr(txt))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sync.main(["--device-id"])
    ok("python -m app.sync --device-id — qurilma identifikatori", code == 0 and buf.getvalue().strip() == DEV,
       buf.getvalue().strip())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sync.main(["--connect", "ftp://x", TOKEN])
    ok("--connect: noto'g'ri manzil — rad etiladi (.env ga tegmaydi)", code == 1 and "http" in buf.getvalue())


def browser_suite() -> None:
    """Panel sahifasi brauzerda: yuklash (XHR), holat yangilanishi, natija kartochkalari, telefon kengligi."""
    print("\n--- Panel brauzerda ---")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ok("Playwright yo'q — brauzer sinovi o'tkazilmadi", True)
        return
    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    reset_edge()
    comp = cdb.create_company("Brauzer MChJ")
    cdb.create_user(comp, "egasi@vt.uz", "VtParol-2026x", "Egasi", "owner")
    bid, _tok = cdb.create_branch(comp, "Yunusobod", DEV)
    cdb.execute("UPDATE branches SET token_hash=:h WHERE id=:b", {"h": cdb.sha256(TOKEN), "b": bid})
    mgr = FakeManager()
    w = sync.SyncWorker(manager=mgr)
    sync._worker = w
    w.start()
    wait(lambda: (cdb.row("SELECT version FROM branches WHERE id=:b", {"b": bid}) or {}).get("version"), 15)
    video = make_video(Path(tempfile.mkdtemp(prefix="vt_br_")) / "brauzer_sinov.mp4")
    base = f"http://127.0.0.1:{PORT}"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, vp in (("kompyuter", {"width": 1366, "height": 900}), ("telefon", {"width": 390, "height": 844})):
            ctx = b.new_context(viewport=vp)
            pg = ctx.new_page()
            errs: list[str] = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            pg.goto(f"{base}/login/")
            pg.fill('input[type="email"]', "egasi@vt.uz")
            pg.fill('input[type="password"]', "VtParol-2026x")
            pg.keyboard.press("Enter")
            pg.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            pg.goto(f"{base}/videos/")
            pg.locator("form.vt-upload").wait_for(timeout=15000)
            ok(f"{name}: 'Video sinovi' menyuda va sahifa ochildi", pg.locator('a[href="/videos/"]').count() > 0)
            pg.set_input_files('input[type="file"]', str(video))
            pg.click('[data-testid="vt-submit"]')
            pg.locator('[data-testid="vt-ok"]').wait_for(timeout=20000)
            pg.locator('tr[data-testid="vt-row"][data-status="tayyor"]').first.wait_for(timeout=30000)
            ok(f"{name}: yuklandi va do'kon kompyuteri tahlil qildi — 'Tayyor'", True)
            pg.locator('tr[data-testid="vt-row"][data-status="tayyor"] button:has-text("Natija")').first.click()
            pg.locator('[data-testid="vt-events"] .vt-ev').nth(1).wait_for(timeout=20000)
            ok(f"{name}: natija — 2 ta signal kartochkasi, hodisaga havola",
               pg.locator('[data-testid="vt-events"] .vt-ev').count() == 2
               and "/events/?uid=" in (pg.locator('[data-testid="vt-events"] .vt-ev').first.get_attribute("href") or ""))
            sw = pg.evaluate("document.documentElement.scrollWidth")
            ok(f"{name}: gorizontal siljish yo'q, konsolda xato yo'q", sw <= vp["width"] and not errs, f"{sw} {errs[:2]}")
            if name == "kompyuter":
                pg.screenshot(path=str(Path(tempfile.gettempdir()) / "vt_panel.png"), full_page=True)
            ctx.close()
        b.close()
    w.stop()
    w.join(10)
    sync._worker = None


def main() -> int:
    t = time.time()
    db.init_db()
    serve()
    suite("SQLite")
    cli_suite()
    notify_suite()
    pg = os.environ.get("CLOUD_TEST_PG_URL")
    if pg:
        os.environ["CLOUD_DB_URL"] = pg
        suite("PostgreSQL")
        os.environ.pop("CLOUD_DB_URL", None)
    else:
        print("\n  (PostgreSQL sinovi o'tkazilmadi: CLOUD_TEST_PG_URL berilmagan)")
    browser_suite()
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
