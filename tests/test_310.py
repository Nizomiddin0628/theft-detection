"""3.1.0 sinovi: toza (chizmalarsiz) klip, o'g'irlik tasdiqlanganda zarar summasi (so'm, qo'lda), Video sinovi —
Telegram'ga signal va yakuniy natija, video kadri ustiga chizilgan zonalar va o'lchovlar (navbat, sanash, bo'limlar).

Bulut — haqiqiy (uvicorn), edge — haqiqiy SyncWorker, Telegram — soxta API (tests/mock_telegram.py; do'kon boti va
bulut boti alohida tokenlar bilan). "real" bo'limi — haqiqiy model bilan FileJob (samples/ videolari, qo'lda sanalgan
natijalar bilan). Brauzer bo'limi — Playwright bo'lsa (zona chizish, klip almashtirgich, summa).

Ishga tushirish:  python -m tests.test_310 [--tez]     (--tez — haqiqiy video va brauzersiz)
"""
from __future__ import annotations

import json
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
TG_PORT = _free_port()
TOKEN = "t310-sinov-token-1234567890"
DEV = "t310dokon"
EDGE_TG = "777:EDGE"
CLOUD_TG = "888:CLOUD"
os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_310_edge_")
os.environ["CLOUD_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_310_cloud_")
os.environ["CLOUD_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["EDGE_TOKEN"] = TOKEN
os.environ["TELEGRAM_BOT_TOKEN"] = EDGE_TG
os.environ["TELEGRAM_API_BASE"] = f"http://127.0.0.1:{TG_PORT}"
os.environ["CLOUD_TELEGRAM_BOT_TOKEN"] = ""
os.environ["CLOUD_VIDEO_MAX_MB"] = "20"
os.environ.pop("CLOUD_DB_URL", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import mock_telegram as tg  # noqa: E402

tg.serve(TG_PORT)

import cv2  # noqa: E402
import httpx  # noqa: E402
import numpy as np  # noqa: E402

from app import config, db, losses, reports, sync  # noqa: E402
from app.ai import clipper, video_metrics, zones as zones_mod  # noqa: E402
from app.config import CLIPS_DIR  # noqa: E402
from cloud import notify, service  # noqa: E402
from cloud import server as cloud  # noqa: E402
from cloud import video_tests as vt  # noqa: E402

results: list[bool] = []
FAST = "--tez" in sys.argv


def ok(name: str, cond, info: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""), flush=True)


def wait(cond, timeout=20.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def tg_calls(method: str, token: str) -> list[dict]:
    return [c for c in tg.CALLS if c["method"] == method and c["token"] == token]


# ============================================================================= 1. birliklar
def unit_suite() -> None:
    print("\n--- Birliklar: summa, zonalar, o'lchovlar ---")
    cases = {"150000": 150000, "150 000": 150000, "150,000": 150000, "1.250.000": 1250000, "150 ming": 150000,
             "1,5 mln": 1500000, "150k": 150000, "200000 so'm": 200000, "": None, "0": None, 7500: 7500, None: None}
    bad = ["abc", "-5", "150.5", "12 34", "999999999999999"]
    e_ok = all(losses.parse_sum(k) == v for k, v in cases.items())
    c_ok = all(service.parse_sum(k) == v for k, v in cases.items())

    def raises(fn, x):
        try:
            fn(x)
        except ValueError:
            return True
        return False
    ok("Summa: edge va bulut bir xil o'qiydi (bo'shliq, vergul, nuqta, ming, mln, k)", e_ok and c_ok,
       str({k: (losses.parse_sum(k), service.parse_sum(k)) for k in cases if losses.parse_sum(k) != cases[k]}))
    ok("Summa: noto'g'ri yozuv — xato (edge ham, bulut ham)", all(raises(losses.parse_sum, b) and raises(service.parse_sum, b)
                                                                for b in bad))
    ok("Summa ko'rinishi: 1500000 → '1 500 000 so'm'", losses.fmt_sum(1500000) == "1 500 000 so'm"
       and notify.fmt_sum(150000) == "150 000 so'm" and service.fmt_sum(0) == "0 so'm")
    good = [{"kind": "navbat", "name": "1-kassa", "pts": [[0, 0.7], [1, 0.7], [1, 1], [0, 1]]},
            {"kind": "chiziq", "name": "Eshik", "pts": [[1, 0.72], [0, 0.72]]},
            {"kind": "polka", "name": "", "pts": [[0.1, 0.1], [0.3, 0.1], [0.3, 0.4]]}]
    zl = vt.parse_zones(json.dumps(good))
    ok("Bulut: zonalar tekshiruvi — to'g'ri ro'yxat, bo'sh nom → tur nomi", zl and len(zl) == 3
       and zl[2]["name"] == "Polka / bo'lim" and zl[1]["pts"] == [[1.0, 0.72], [0.0, 0.72]])
    bads = {"tur": [{"kind": "bomba", "pts": [[0, 0], [1, 0], [1, 1]]}],
            "chiziq qisqa": [{"kind": "chiziq", "pts": [[0.5, 0.5], [0.51, 0.5]]}],
            "2 nuqtali zona": [{"kind": "polka", "pts": [[0, 0], [1, 1]]}],
            "17 nuqta": [{"kind": "polka", "pts": [[i / 20, (i % 2) / 2] for i in range(17)]}],
            "13 zona": [{"kind": "kassa", "pts": [[0, 0], [1, 0], [1, 1]]}] * 13,
            "NaN": [{"kind": "kassa", "pts": [[0, 0], [1, 0], [1, float("inf")]]}]}
    bad_ok = []
    for k, v in bads.items():
        try:
            vt.parse_zones(json.dumps(v))
            bad_ok.append(k)
        except ValueError:
            pass
    ok("Bulut: noto'g'ri zonalar rad etiladi (tur, qisqa chiziq, nuqtalar, soni, NaN)", not bad_ok, str(bad_ok))
    ok("Bulut: juda uzun parametr va JSON emas — rad", raises(vt.parse_zones, "x" * 5000) and raises(vt.parse_zones, "{")
       and vt.parse_zones("") is None)
    zs = zones_mod.from_list(good + [{"kind": "favqulodda", "pts": [[0, 0], [1, 0], [1, 1]]}, "x"])
    ok("Edge: zonalar ro'yxatdan (yaroqsizlari tashlanadi, raqamlar 1..N)", zs and [z.kind for z in zs.zones]
       == ["navbat", "chiziq", "polka"] and [z.id for z in zs.zones] == [1, 2, 3]
       and zones_mod.from_list([]) is None and zones_mod.from_list("x") is None)

    class P:
        def __init__(self, tid, box, wrist=None):
            self.track_id, self.box = tid, np.array(box, dtype=np.float32)
            self.kpts = np.zeros((17, 3), dtype=np.float32)
            if wrist:
                self.kpts[9] = (wrist[0], wrist[1], 0.9)
    shelf = zones_mod.from_list([{"kind": "polka", "name": "Ichimlik", "pts": [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]},
                                 {"kind": "kassa", "name": "Kassa", "pts": [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]]}])
    av = video_metrics.AreaVisits()
    for i in range(40):                               # 4 s: 1-odam (oyog'i zonalardan tashqarida) qo'li polkada — 2 marta
        t = i * 0.1                                   # uzatadi (3 s + 0.5 s); 2-odam kassada turibdi
        hand = (50, 50) if i < 30 or i >= 35 else (300, 100)
        av.update([P(1, [200, 100, 260, 190], hand), P(2, [300, 250, 360, 390])], t, 400, 400, shelf)
    res = {a["name"]: a for a in av.result()}
    ok("Bo'limlar: polkada to'xtagan va qo'l uzatishlar, kassada turgan", res.get("Ichimlik", {}).get("visitors") == 1
       and res["Ichimlik"]["reaches"] == 2 and res.get("Kassa", {}).get("visitors") == 1
       and res["Kassa"]["reaches"] is None and av.persons_max == 2, str(res))
    m = {"queue": [{"name": "1-kassa", "max_len": 3, "served": 4, "avg_wait": 21.0}],
         "lines": [{"name": "Eshik", "in": 7, "out": 1, "staff": 0}],
         "areas": [{"name": "Ichimlik", "kind": "polka", "visitors": 5}]}
    b1, b2 = video_metrics.brief(m), notify.metrics_brief(m)
    ok("Qisqa natija matni: edge va bulut bir xil", b1 == b2 and len(b1) == 3 and "eng uzun navbat 3" in b1[0], str(b1))


# ============================================================================= 2. toza klip
def _frames(n: int = 24) -> list:
    out = []
    for i in range(n):
        img = np.zeros((240, 320, 3), np.uint8)
        img[:, :, 1] = 90
        img[:, (i * 6) % 300:(i * 6) % 300 + 20] = (200, 180, 40)
        ok_, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        kp = np.zeros((17, 3), np.float32)
        for j in range(17):
            kp[j] = (140 + j * 2, 60 + j * 8, 0.9)
        ann = ([(1, np.array([120, 40, 200, 220], np.float32), kp)], [], {1: {"risk": 80, "held": None, "staff": False}})
        out.append(clipper.BufFrame(i / 12, time.time(), buf.tobytes(), 1.0, ann))
    return out


def clip_suite() -> None:
    print("\n--- Toza klip ---")
    frames = _frames()
    out = Path(tempfile.mkdtemp(prefix="t310_clip_"))
    res = clipper.make_clip(frames, 1.0, 1, "SINOV", False, out, "ev00001_t", [120, 40, 200, 220], (0.5, 1.0))
    ok("Klip: belgili va toza nusxa (mp4 + rasm) yozildi", all(res.get(k) and (out / res[k]).is_file()
                                                              for k in ("clip", "clip_clean", "thumb", "thumb_clean")), str(res))
    raw = clipper._decode(frames[int(np.argmin([abs(f.t - 1.0) for f in frames]))]).astype(np.float32)
    tc = cv2.imread(str(out / res["thumb_clean"])).astype(np.float32)
    tm = cv2.imread(str(out / res["thumb"])).astype(np.float32)
    d_clean, d_mark = float(np.abs(tc - raw).mean()), float(np.abs(tm - raw).mean())
    ok("Toza rasm — asl kadrning o'zi, belgili rasmda chizmalar bor", d_clean < 3.0 and d_mark > d_clean + 2.0,
       f"toza {d_clean:.2f}, belgili {d_mark:.2f}")
    cap = cv2.VideoCapture(str(out / res["clip_clean"]))
    okv, f0 = cap.read()
    cap.release()
    d0 = float(np.abs(f0.astype(np.float32) - clipper._decode(frames[0]).astype(np.float32)).mean()) if okv else 99
    ok("Toza video — birinchi kadr asl kadrga mos (yozuv, skelet, vaqt chizig'i yo'q)", okv and d0 < 6.0, f"{d0:.2f}")
    res2 = clipper.make_clip(frames, 1.0, 1, "SINOV", False, out, "ev00002_t", None, None, clean=False)
    ok("clean=False — faqat belgili nusxa (eski usul)", res2.get("clip") and not res2.get("clip_clean"))
    snap, snapc = out / "s.jpg", out / "s_clean.jpg"
    clipper.make_snapshot(clipper._decode(frames[3]), frames[3].ann, 1, "X", "t", snap, clean_path=snapc)
    ok("Signal lahzasidagi rasm ham ikki nusxa", snap.is_file() and snapc.is_file()
       and float(np.abs(cv2.imread(str(snapc)).astype(np.float32) - clipper._decode(frames[3]).astype(np.float32)).mean()) < 3)
    # belgili nusxani yozishda (chizishda) xato — toza nusxa yo'qolmaydi, u asosiy klip bo'ladi
    orig = clipper.write_mp4

    def bad(gen, path, fps, only_clean=True):
        if not only_clean or not str(path).endswith("_clean.mp4"):
            Path(path).write_bytes(b"chala")
            raise RuntimeError("chizishda xato (sinov)")
        return orig(gen, path, fps)
    clipper.write_mp4 = bad
    try:
        res3 = clipper.make_clip(frames, 1.0, 1, "SINOV", False, out, "ev00003_t", None, None)
        ok("Belgili klip xato bilan — toza nusxa qaytadi, chala fayl o'chirildi, hodisa lahzasigacha yetmagan rasm yozilmadi",
           not res3.get("clip") and res3.get("clip_clean") and (out / res3["clip_clean"]).is_file()
           and not (out / "ev00003_t.mp4").exists() and not res3.get("thumb"), str(res3))
        from app.ai.manager import Manager
        from app.ai.worker import PendingClip

        evc = db.insert_event({"source_id": 3, "source_name": "Kassa", "created_at": time.time(), "score": 80,
                               "kind": "pocket", "reasons": [], "breakdown": {}, "track_id": 1, "status": "yangi"})
        m = Manager.__new__(Manager)
        m.notifier, m.emit, m._cleanup = None, (lambda *a, **k: None), (lambda: None)
        m.finish_clip(PendingClip(evc, 1.0, 0.0, 2.0, 1, "SINOV", [120, 40, 200, 220], False, 0.5, 1.0), frames)
        e = db.get_event(evc) or {}
        ok("Manager: toza nusxa asosiy klip bo'ldi (bitta fayl ikki maydonda emas), klip xatosi yo'q",
           str(e.get("clip_path") or "").endswith("_clean.mp4") and not e.get("clip_clean") and not e.get("clip_error")
           and (CLIPS_DIR / e["clip_path"]).is_file(), str({k: e.get(k) for k in ("clip_path", "clip_clean", "clip_error")}))
        clipper.write_mp4 = lambda g, p, f: bad(g, p, f, only_clean=False)
        evd = db.insert_event({"source_id": 3, "source_name": "Kassa", "created_at": time.time(), "score": 80,
                               "kind": "pocket", "reasons": [], "breakdown": {}, "track_id": 1, "status": "yangi"})
        m.finish_clip(PendingClip(evd, 1.0, 0.0, 2.0, 1, "SINOV", [120, 40, 200, 220], False, 0.5, 1.0), frames)
        e = db.get_event(evd) or {}
        ok("Ikkala nusxa ham yozilmadi — 'klip yaratilmadi' (avvalgidek)", e.get("clip_error") == 1 and not e.get("clip_path"))
        for x in (evc, evd):                         # keyingi bo'limlar (bulut hisoblari) ularni ko'rmasin
            db.execute("DELETE FROM outbox WHERE key IN (?,?)", (f"ev:{x}", f"clip:{x}"))
            db.execute("DELETE FROM events WHERE id=?", (x,))
    finally:
        clipper.write_mp4 = orig


# ============================================================================= 3. do'kon boti
def _jpg(seed: int, size: tuple[int, int]) -> bytes:
    rng = np.random.default_rng(seed)
    return cv2.imencode(".jpg", rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))[1].tobytes()


JPG_MARK = _jpg(1, (160, 120))          # belgili (katta) va toza (kichik) rasm — hajmi bo'yicha farqlanadi
JPG_CLEAN = _jpg(2, (96, 72))


def _event(kind="pocket", score=82.0, job_id=None, clean=True, status="yangi") -> int:
    ev = db.insert_event({"source_id": 3, "source_name": "Kassa kamerasi", "job_id": job_id, "created_at": time.time(),
                          "video_time": 4.5 if job_id else None, "score": score, "level": "yuqori", "kind": kind,
                          "reasons": ["qo'l cho'ntakka"], "breakdown": {}, "track_id": 1, "status": status,
                          "t_detect": time.time()})
    stem = f"ev{ev:05d}_t310"
    (CLIPS_DIR / f"{stem}.jpg").write_bytes(JPG_MARK)
    (CLIPS_DIR / f"{stem}.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"M" * 5000)
    upd = {"thumb_path": f"{stem}.jpg", "clip_path": f"{stem}.mp4"}
    if clean:
        (CLIPS_DIR / f"{stem}_clean.jpg").write_bytes(JPG_CLEAN)
        (CLIPS_DIR / f"{stem}_clean.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"C" * 3333)
        upd.update(thumb_clean=f"{stem}_clean.jpg", clip_clean=f"{stem}_clean.mp4")
    db.update_event(ev, **upd)
    return ev


def edge_bot_suite():
    from app import telegram_bot as tb

    print("\n--- Do'kon boti: toza rasm/klip, video sinovi, zarar summasi ---")
    db.set_settings({"telegram_enabled": "1", "telegram_send_video": "1", "telegram_min_score": "0",
                     "store_name": "Sinov do'koni"})
    chat = 7001
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active) VALUES(?,?,?,?,1)",
               (chat, "Qorovul", "qorovul1", time.time()))
    bot = tb.get_bot()
    bot.start()
    wait(lambda: bot.me, 10)
    ev = _event()
    bot.notify_alert(ev)
    ph = wait(lambda: [c for c in tg_calls("sendPhoto", EDGE_TG) if str(c["data"].get("chat_id")) == str(chat)], 15)
    ok("Birinchi xabar — toza rasm (chizmalarsiz nusxa)", ph and ph[-1]["files"]["photo"]["size"] == len(JPG_CLEAN),
       str(ph[-1]["files"] if ph else None))
    db.update_event(ev, clip_error=0)
    bot.notify_clip(ev)
    em = wait(lambda: tg_calls("editMessageMedia", EDGE_TG), 15)
    ok("Klip — toza video (Telegram'ga chizmalarsiz)", em and em[-1]["files"]["clip"]["size"] == 3345
       and em[-1]["files"].get("thumb", {}).get("size", 0) > 0, str(em[-1]["files"] if em else None))
    ev_old = _event(clean=False)
    n0 = len(tg_calls("sendPhoto", EDGE_TG))
    bot.notify_alert(ev_old)
    ph2 = wait(lambda: tg_calls("sendPhoto", EDGE_TG)[n0:], 15)
    ok("Toza nusxa yo'q (eski hodisa) — oddiy rasm yuboriladi", ph2 and ph2[-1]["files"]["photo"]["size"] == len(JPG_MARK))
    # video sinovi hodisasi: Telegram'ga boradi, belgisi bilan
    jid = db.execute("INSERT INTO jobs(filename, path, status, created_at, cloud_test_id, cloud_meta) VALUES(?,?,?,?,?,?)",
                     ("kassa_sinov.mp4", "/x/k.mp4", "ishlanmoqda", time.time(), 77, "{}"))
    evj = _event(job_id=jid)
    from app.ai import manager as mg

    m = mg.get_manager()
    m.notifier = bot
    m.create_event({"score": 90, "level": "yuqori", "kind": "pocket", "reasons": ["sinov"], "breakdown": {},
                    "track_id": 1, "t": time.time(), "kind_label": "Cho'ntakka solish"},
                   {"id": None, "name": "Video: kassa_sinov.mp4"}, jid, 4.5)
    def test_caps():
        return [str(c["data"].get("caption") or c["data"].get("text")) for c in tg.CALLS
                if c["token"] == EDGE_TG and "Video sinovi" in str(c["data"].get("caption") or c["data"].get("text") or "")]
    cap = wait(test_caps, 15)
    ok("Bulut Video sinovi signali do'kon botiga ham ketadi — '🧪 Video sinovi (bulut paneli): fayl'", bool(cap)
       and "🧪 <b>Video sinovi</b> (bulut paneli): kassa_sinov.mp4" in cap[-1], cap[-1][:120] if cap else "")
    m.notifier = None
    # ✅ → summa so'raladi
    tg.callback(chat, f"ok:{ev}")
    got = wait(lambda: db.get_event(ev)["status"] == "tasdiqlangan", 10)
    ask = wait(lambda: [c for c in tg_calls("sendMessage", EDGE_TG) if (c["data"].get("reply_markup") or {}).get("force_reply")
                        and f"#{ev}" in c["data"].get("text", "")], 10)
    ok("✅ Tasdiq — bot zarar summasini so'raydi (force_reply)", got and ask, str(ask[-1]["data"]["text"][:80] if ask else ""))
    tg.text_message(chat, "12 34")
    err = wait(lambda: [c for c in tg_calls("sendMessage", EDGE_TG) if "raqam bilan yozing" in c["data"].get("text", "")], 10)
    ok("Noto'g'ri javob — tushuntirish, summa yozilmaydi", err and not db.get_event(ev).get("loss_sum"))
    tg.text_message(chat, "rahmat")                 # raqamsiz oddiy xabar — summa emas (oddiy javob), kutish davom etadi
    time.sleep(1.0)
    ok("Raqamsiz oddiy xabar summa deb olinmaydi", not db.get_event(ev).get("loss_sum"))
    tg.text_message(chat, "150 ming")
    got = wait(lambda: db.get_event(ev).get("loss_sum") == 150000, 10)
    e1 = db.get_event(ev)
    ok("Javob '150 ming' — 150 000 so'm saqlandi, kim kiritgani bilan", got and str(e1.get("loss_by", "")).startswith(
        "Telegram: ") and e1.get("loss_at"), str({k: e1.get(k) for k in ("loss_sum", "loss_by")}))
    ed = wait(lambda: [c for c in tg_calls("editMessageCaption", EDGE_TG) + tg_calls("editMessageMedia", EDGE_TG)
                       if "150 000 so'm" in json.dumps(c["data"], ensure_ascii=False)], 10)
    ok("Xabar yangilandi: '💰 Taxminiy zarar: 150 000 so'm' va '💰 Summani o'zgartirish' tugmasi", bool(ed)
       and "loss:" in json.dumps(ed[-1]["data"], ensure_ascii=False))
    tg.callback(chat, f"loss:{ev}")
    wait(lambda: len([c for c in tg_calls("sendMessage", EDGE_TG) if (c["data"].get("reply_markup") or {}).get("force_reply")])
         >= 2, 10)
    tg.text_message(chat, "/otkaz")
    wait(lambda: [c for c in tg_calls("sendMessage", EDGE_TG) if "summa yozilmadi" in c["data"].get("text", "")], 10)
    ok("💰 tugmasi qayta so'raydi, /otkaz — summa o'zgarmaydi", db.get_event(ev).get("loss_sum") == 150000)
    n_ask = len([c for c in tg_calls("sendMessage", EDGE_TG) if (c["data"].get("reply_markup") or {}).get("force_reply")])
    evs = _event(kind="fall", score=0)
    tg.callback(chat, f"ok:{evs}")
    tg.callback(chat, f"ok:{evj}")
    wait(lambda: db.get_event(evs)["status"] == "tasdiqlangan" and db.get_event(evj)["status"] == "tasdiqlangan", 10)
    time.sleep(1.0)
    n_ask2 = len([c for c in tg_calls("sendMessage", EDGE_TG) if (c["data"].get("reply_markup") or {}).get("force_reply")])
    ok("Xavfsizlik signali va video sinovi tasdiqlanganda summa so'ralmaydi", n_ask2 == n_ask, f"{n_ask} → {n_ask2}")
    # guruh: so'rov tugmani bosganga (eslatma, selective); javob — so'rov xabariga reply; boshqa a'zoning javobi olinmaydi
    grp = -100777
    db.execute("INSERT OR REPLACE INTO subscribers(chat_id, name, username, created_at, active) VALUES(?,?,?,?,1)",
               (grp, "Qo'riqchilar guruhi", None, time.time()))
    evg = _event()
    tg.push_update({"callback_query": {"id": "cbg1", "data": f"ok:{evg}", "from": {"id": 501, "first_name": "Ali"},
                                       "message": {"message_id": 1, "chat": {"id": grp, "type": "supergroup"}}}})
    gq = wait(lambda: [c for c in tg_calls("sendMessage", EDGE_TG) if str(c["data"].get("chat_id")) == str(grp)
                       and (c["data"].get("reply_markup") or {}).get("force_reply")], 10)
    ok("Guruh: so'rov tugmani bosganga (eslatma va selective)", gq and gq[-1]["data"]["reply_markup"].get("selective") is True
       and "tg://user?id=501" in gq[-1]["data"]["text"], str(gq[-1]["data"] if gq else "")[:160])
    # soxta API message_id'ni chaqiruvlar tartibida beradi — so'rov xabarining raqami bot xotirasida
    pmid = max(bot._loss_wait.get(grp, {}) or [0])

    def gmsg(uid_, text_, reply=None):
        m = {"message_id": 9000 + uid_, "date": int(time.time()), "text": text_,
             "chat": {"id": grp, "type": "supergroup"}, "from": {"id": uid_, "first_name": f"A{uid_}"}}
        if reply:
            m["reply_to_message"] = {"message_id": reply}
        tg.push_update({"message": m})
    gmsg(502, "999999", reply=pmid)                 # boshqa a'zo
    gmsg(501, "450000")                             # reply'siz oddiy xabar guruhda — summa emas
    time.sleep(1.5)
    ok("Guruh: boshqa a'zoning javobi va reply'siz xabar summa sifatida olinmaydi", not db.get_event(evg).get("loss_sum"))
    gmsg(501, "75 000", reply=pmid)
    got = wait(lambda: db.get_event(evg).get("loss_sum") == 75000, 10)
    ok("Guruh: tugmani bosgan odam so'rovga javob qildi — 75 000, kim kiritgani uning ismi", got
       and db.get_event(evg).get("loss_by") == "Telegram: A501", str(db.get_event(evg).get("loss_by")))
    # bulut sinovidan Telegram'ga ko'pi bilan 20 ta signal (do'kon boti)
    from app.ai.manager import _tg_allowed

    jc = db.execute("INSERT INTO jobs(filename, path, status, created_at, cloud_test_id, cloud_meta) VALUES(?,?,?,?,?,?)",
                    ("uzun.mp4", "/x/u.mp4", "ishlanmoqda", time.time(), 78, "{}"))
    ids = [db.insert_event({"job_id": jc, "created_at": time.time(), "kind": "pocket", "score": 70, "status": "yangi",
                            "reasons": [], "breakdown": {}}) for _ in range(22)]
    jl = db.execute("INSERT INTO jobs(filename, path, status, created_at) VALUES(?,?,?,?)",
                    ("lokal.mp4", "/x/l.mp4", "ishlanmoqda", time.time()))
    ok("Bulut sinovi: 20-signalgacha Telegram'ga, 21-dan — yo'q; jonli va do'kondagi yuklangan video — cheksiz",
       _tg_allowed(jc, True, ids[19]) and not _tg_allowed(jc, True, ids[20]) and _tg_allowed(None) and _tg_allowed(jl))
    # dastur qayta ishga tushib sinov qaytadan tahlil qilinsa — oldingi urinish signallari ham sanaladi
    from app import cloud_tests

    db.execute("UPDATE jobs SET status='xato' WHERE id=?", (jc,))
    jr = db.execute("INSERT INTO jobs(filename, path, status, created_at, cloud_test_id, cloud_meta) VALUES(?,?,?,?,?,?)",
                    ("uzun2.mp4", "/x/u2.mp4", "ishlanmoqda", time.time(), 79, "{}"))
    for _ in range(15):
        db.insert_event({"job_id": jr, "created_at": time.time(), "kind": "pocket", "score": 70, "status": "yangi",
                         "reasons": [], "breakdown": {}})
    others = [r_["id"] for r_ in db.rows("SELECT id FROM jobs WHERE cloud_test_id IS NOT NULL AND status='ishlanmoqda' "
                                         "AND id<>?", (jr,))]
    for o in others:                                 # boshqa bo'limlarning sinovlari (masalan 77) qayta navbatga tushmasin
        db.execute("UPDATE jobs SET status='kutish_t310' WHERE id=?", (o,))
    cloud_tests.recover()
    for o in others:
        db.execute("UPDATE jobs SET status='ishlanmoqda' WHERE id=?", (o,))
    jn = (db.row("SELECT id FROM jobs WHERE cloud_test_id=79 AND status='navbatda'") or {}).get("id")
    ids2 = [db.insert_event({"job_id": jn, "created_at": time.time(), "kind": "pocket", "score": 70, "status": "yangi",
                             "reasons": [], "breakdown": {}}) for _ in range(8)]
    ok("Qayta tahlil qilingan sinov: cheklov sinov bo'yicha (15 oldin + 5 = 20, keyingisi yo'q)",
       jn and _tg_allowed(jn, True, ids2[4]) and not _tg_allowed(jn, True, ids2[5]), str(jn))
    db.execute("UPDATE jobs SET status='xato' WHERE cloud_test_id=79")
    for r_ in db.rows("SELECT id FROM events WHERE job_id IN (?,?)", (jr, jn)):
        db.execute("DELETE FROM outbox WHERE key IN (?,?)", (f"ev:{r_['id']}", f"clip:{r_['id']}"))
    db.execute("DELETE FROM events WHERE job_id IN (?,?)", (jr, jn))
    bot.stop()
    return ev


# ============================================================================= 4. do'kon paneli API
def edge_web_suite(ev_tg: int) -> None:
    from fastapi.testclient import TestClient

    from app import config, web

    print("\n--- Do'kon paneli: summa API, karta, CSV, hisobot ---")
    c = TestClient(web.app)
    c.post("/login", data={"username": config.ADMIN_USER, "password": config.ADMIN_PASSWORD, "next": "/"})
    ev = _event()
    r = c.post(f"/api/events/{ev}/loss", json={"loss_sum": "100000"})
    ok("Tasdiqlanmagan signalga summa — 409", r.status_code == 409, r.text[:100])
    evs = _event(kind="fall", score=0, status="tasdiqlangan")
    ok("Xavfsizlik signaliga summa — 400", c.post(f"/api/events/{evs}/loss", json={"loss_sum": "5"}).status_code == 400)
    r = c.post(f"/api/events/{ev}/review", json={"status": "tasdiqlangan", "loss_sum": "1,5 mln"})
    e = r.json().get("event", {}) if r.status_code == 200 else {}
    ok("Tasdiqlash bilan birga summa (1,5 mln → 1 500 000)", e.get("loss_sum") == 1500000
       and str(e.get("loss_by", "")).startswith("Web: ") and e.get("loss_allowed"), str(e.get("loss_sum")))
    ok("Hodisa ma'lumotida toza klip va rasm havolalari", e.get("clip_clean_url", "").endswith("_clean.mp4")
       and e.get("thumb_clean_url", "").endswith("_clean.jpg"))
    ok("Noto'g'ri summa — 400 (tushunarli xabar)", c.post(f"/api/events/{ev}/loss", json={"loss_sum": "ko'p"}).status_code == 400)
    r = c.post(f"/api/events/{ev}/loss", json={"loss_sum": "250 000"})
    ok("Summani o'zgartirish", r.status_code == 200 and r.json()["event"]["loss_sum"] == 250000)
    s = c.get("/api/summary").json().get("losses") or {}
    ok("Boshqaruv paneli: jami 475 000 so'm (Telegram 150 000 + guruh 75 000 + panel 250 000)", s.get("total") == 475000,
       str(s))
    csv = c.get("/api/export/events.csv").text
    ok("CSV — 'Taxminiy zarar (so'm)' ustuni va qiymat", "Taxminiy zarar (so'm)" in csv and ";250000" in csv)
    t0 = time.time() - 3600
    d = reports.compute("daily", t0, time.time() + 60)
    txt = reports.text(d)
    ok("Hisobot: 'Oldini olingan zarar: 475 000 so'm (3 ta holat)'", d["theft"]["loss"]["sum"] == 475000
       and "475 000 so'm" in txt and "3 ta holat" in txt, str(d["theft"]["loss"]))
    pdfb = reports.pdf(d)
    import re
    import zlib

    plain = b"".join(zlib.decompress(m.group(1)) for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdfb, re.S)
                     if m.group(1)[:1] == b"x")
    ok("Hisobot PDF yaratiladi (summa bilan)", pdfb[:4] == b"%PDF" and b"Oldini olingan zarar" in plain
       and b"475 000 so'm" in plain.replace(b"\\'", b"'"))
    c.post(f"/api/events/{ev}/review", json={"status": "rad_etilgan"})
    ok("Xato deb belgilangan hodisa summasi hisobga kirmaydi", losses.totals()["sum"] == 225000)
    page = c.get(f"/events/{ev_tg}").text
    ok("Hodisa sahifasi: 'Belgili / Toza' tugmalari va summa maydoni", 'id="clip-mode"' in page and 'id="loss"' in page)
    c.post(f"/api/events/{ev}/loss", json={"loss_sum": ""})


# ============================================================================= 5. bulut
class FakeMgr:
    """FileJob o'rnida: zonalarni o'qiydi, hodisa (toza klip bilan) va o'lchovlarni yozadi."""

    def __init__(self):
        self.notifier = None
        self.queued: list[int] = []
        self.zones_seen = None

    def source_info(self):
        return [{"id": 3, "name": "Kassa kamerasi", "kind": "rtsp", "state": "ishlayapti", "running": True}]

    def emit(self, *_a):
        pass

    def queue_job(self, jid: int) -> None:
        self.queued.append(jid)
        threading.Thread(target=self._run, args=(jid,), daemon=True).start()

    def stop_job(self, jid: int) -> None:
        pass

    def _run(self, jid: int) -> None:
        job = db.row("SELECT * FROM jobs WHERE id=?", (jid,))
        self.zones_seen = json.loads(job.get("cloud_meta") or "{}").get("zones")
        db.execute("UPDATE jobs SET status='ishlanmoqda', progress=30 WHERE id=?", (jid,))
        sync.hello_soon()
        _event(job_id=jid)
        m = {"duration": 12.5, "zones": 2, "persons_max": 4,
             "queue": [{"name": "1-kassa", "max_len": 3, "avg_len": 1.2, "busy_pct": 0.5, "served": 4, "avg_wait": 20.0,
                        "max_wait": 41.0, "series": [[1.0, 0], [3.0, 3]]}],
             "lines": [{"name": "Eshik", "in": 7, "out": 1, "staff": 0}], "areas": []}
        db.execute("UPDATE jobs SET metrics=? WHERE id=?", (json.dumps(m), jid))
        db.execute("UPDATE jobs SET status='tayyor', progress=100, events_count=1, duration=12.5, finished_at=? WHERE id=?",
                   (time.time(), jid))
        sync.hello_soon()


def serve():
    import uvicorn

    srv = uvicorn.Server(uvicorn.Config(cloud.app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    wait(lambda: srv.started, 10)
    return srv


def cloud_suite() -> None:
    print("\n--- Bulut: toza klip, summa (panel, bot, edge), Video sinovi — zonalar, Telegram ---")
    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    for t in ("jobs", "outbox", "events"):
        db.execute(f"DELETE FROM {t}")
    db.execute("INSERT OR IGNORE INTO sources(id, name, kind, uri, enabled, loop, created_at) VALUES(3,?,?,?,0,0,?)",
               ("Kassa kamerasi", "rtsp", "rtsp://127.0.0.1/sinov", time.time()))
    db.set_settings({"device_id": DEV})
    comp = cdb.create_company("3.1 MChJ")
    owner_id = cdb.create_user(comp, "egasi@t310.uz", "T310Parol-2026x", "Egasi", "owner")
    bid, _tok = cdb.create_branch(comp, "Chilonzor", DEV)
    cdb.execute("UPDATE branches SET token_hash=:h WHERE id=:b", {"h": cdb.sha256(TOKEN), "b": bid})
    guard_id = cdb.create_user(comp, "qorovul@t310.uz", "T310Parol-2026y", "Qo'riqchi", "guard", [bid])
    notify.add_target(owner_id, "telegram", "8001", {"name": "Egasi"})
    notify.add_target(guard_id, "telegram", "8002", {"name": "Qo'riqchi"})
    notify.set_prefs(cdb.row("SELECT * FROM users WHERE id=:u", {"u": owner_id}), {"min_score": 0})
    base = f"http://127.0.0.1:{PORT}"
    owner = httpx.Client(base_url=base, timeout=30)
    owner.post("/api/v1/auth/login", json={"email": "egasi@t310.uz", "password": "T310Parol-2026x"})
    mgr = FakeMgr()
    w = sync.SyncWorker(manager=mgr)
    sync._worker = w
    w.start()
    wait(lambda: (cdb.row("SELECT version FROM branches WHERE id=:b", {"b": bid}) or {}).get("version"), 15)
    ok(f"Edge {config.APP_VERSION} bulutga ulandi", (cdb.row("SELECT version FROM branches WHERE id=:b", {"b": bid}) or {}).get("version")
       == config.APP_VERSION)

    # --- jonli hodisa: toza klip bulutga, bulut boti toza videoni yuboradi
    ev = _event()
    uid = f"{DEV}:{ev}"
    got = wait(lambda: (cdb.row("SELECT clip_file, clip_clean FROM events WHERE uid=:u", {"u": uid}) or {}).get("clip_clean"), 20)
    row = cdb.row("SELECT * FROM events WHERE uid=:u", {"u": uid}) or {}
    ok("Klip yuklashda toza nusxa ham bulutga keldi (alohida fayl)", got and (cloud.CLIPS_DIR / row["clip_clean"]).stat().st_size
       == 3345 and (cloud.CLIPS_DIR / row["clip_file"]).stat().st_size == 5012, str(row.get("clip_clean")))
    def _ev_vids():
        return [c for c in tg_calls("sendVideo", CLOUD_TG) if str(c["data"].get("caption") or "").endswith(f"hodisa #{ev}")]
    vids = wait(lambda: [c for c in _ev_vids() if str(c["data"].get("chat_id")) == "8001"], 25)
    # birinchi yuborishda fayl yuklanadi, keyingi chatlarga Telegram file_id bilan (qaysi chat birinchi — tasodifiy)
    ups = [c for c in _ev_vids() if "video" in (c.get("files") or {})]
    ok("Bulut boti egasiga toza videoni yubordi", vids and ups and all(u["files"]["video"]["size"] == 3345 for u in ups),
       str([u.get("files") for u in ups]))
    lst = owner.get("/api/v1/events?limit=10").json()["events"]
    ok("Hodisalar ro'yxatida clip_clean va loss_sum maydonlari", any(e["uid"] == uid and e.get("clip_clean") for e in lst)
       and all("loss_sum" in e for e in lst))

    # --- summa: bulut paneli
    r = owner.post(f"/api/v1/events/{uid}/loss", json={"loss_sum": "100000"})
    ok("Bulut: tasdiqlanmagan signalga summa — 409", r.status_code == 409, r.text[:100])
    r = owner.post(f"/api/v1/events/{uid}/review", json={"status": "tasdiqlangan", "loss_sum": "120 000"})
    ok("Bulut: tasdiqlash bilan summa — 120 000", r.status_code == 200 and r.json()["event"].get("loss_sum") == 120000,
       r.text[:160])
    got = wait(lambda: db.get_event(ev).get("loss_sum") == 120000 and db.get_event(ev)["status"] == "tasdiqlangan", 25)
    ok("Edge'ga yetdi: holat va summa ('Bulut: ...' kiritgan)", got and str(db.get_event(ev).get("loss_by", "")).startswith("Bulut: "),
       str({k: db.get_event(ev).get(k) for k in ("status", "loss_sum", "loss_by")}))
    ok("Bulut: noto'g'ri summa — 400", owner.post(f"/api/v1/events/{uid}/loss", json={"loss_sum": "ko'p"}).status_code == 400)
    # --- summa: do'konda (Telegram/panel) kiritildi → bulutga
    losses.set_loss(ev, 175000, "Telegram: Qorovul")
    got = wait(lambda: (cdb.row("SELECT loss_sum FROM events WHERE uid=:u", {"u": uid}) or {}).get("loss_sum") == 175000, 20)
    ok("Do'konda o'zgartirilgan summa bulutga yetdi", got)
    edt = wait(lambda: [c for c in tg_calls("editMessageText", CLOUD_TG) if "175 000 so'm" in c["data"].get("text", "")], 20)
    ok("Bulut botidagi xabar yangilandi: summa va 💰 tugmasi", edt and edt[-1]["data"]["reply_markup"]["inline_keyboard"][0][0]
       ["callback_data"].startswith("m:"))
    # 3.0.0 edge (loss_sum maydoni yo'q) yuborgan yangiroq hodisa bulutdagi summani o'chirmaydi
    old_item = {k: v for k, v in sync.event_payload(db.get_event(ev)).items() if not k.startswith("loss_")}
    old_item["updated_at"] = time.time() + 5
    with httpx.Client(base_url=base, timeout=20) as ec:
        r = ec.post("/api/v1/edge/events", json={"items": [{**old_item, "key": f"ev:{ev}"}]},
                    headers={"Authorization": f"Bearer {TOKEN}", "X-Device-Id": DEV})
    ok("3.0.0 edge yangilanishi (summasiz) bulutdagi summani o'chirmaydi", r.status_code == 200
       and (cdb.row("SELECT loss_sum FROM events WHERE uid=:u", {"u": uid}) or {}).get("loss_sum") == 175000, r.text[:120])
    cdb.execute("UPDATE branches SET version='3.0.0' WHERE id=:b", {"b": bid})
    r = owner.post(f"/api/v1/events/{uid}/loss", json={"loss_sum": "1000"})
    ok("Filial kompyuteri 3.0.0 — bulutda summa kiritib bo'lmaydi (409, yangilash kerak)", r.status_code == 409
       and "3.1.0" in r.text, r.text[:120])
    cdb.execute("UPDATE branches SET version='3.1.0' WHERE id=:b", {"b": bid})
    # eskirgan bulut buyrug'i lokal yangi summani buzmaydi
    w._apply_loss({"local_id": ev, "loss_sum": 1, "by": "Bulut: eski", "t": time.time() - 3600})
    ok("Eskirgan bulut summasi lokal yangisini buzmaydi", db.get_event(ev).get("loss_sum") == 175000)
    # --- bulut boti: ✅ → summa so'raladi → javob
    ev2 = _event()
    uid2 = f"{DEV}:{ev2}"
    msg = wait(lambda: cdb.row("SELECT * FROM tg_sent WHERE uid=:u AND kind='event' AND message_id IS NOT NULL AND chat_id='8001'",
                               {"u": uid2}), 25)
    tg.callback(8001, f"r:{msg['id']}:ok" if msg else "x")
    ask = wait(lambda: [c for c in tg_calls("sendMessage", CLOUD_TG) if str(c["data"].get("chat_id")) == "8001"
                        and (c["data"].get("reply_markup") or {}).get("force_reply")], 15)
    ok("Bulut boti: ✅ dan keyin zarar summasini so'raydi", bool(msg) and ask)
    tg.text_message(8001, "90000")
    got = wait(lambda: (cdb.row("SELECT loss_sum FROM events WHERE uid=:u", {"u": uid2}) or {}).get("loss_sum") == 90000, 15)
    ok("Bulut botiga javob '90000' — saqlandi va edge'ga yetdi", got and wait(lambda: db.get_event(ev2).get("loss_sum") == 90000, 25))
    # xavfsizlik va video sinovi — summa yozilmaydi
    evs = _event(kind="fall", score=0)
    uids = f"{DEV}:{evs}"
    wait(lambda: cdb.row("SELECT uid FROM events WHERE uid=:u", {"u": uids}), 20)
    owner.post(f"/api/v1/events/{uids}/review", json={"status": "tasdiqlangan"})
    ok("Bulut: xavfsizlik signaliga summa — 409", owner.post(f"/api/v1/events/{uids}/loss", json={"loss_sum": 5}).status_code == 409)
    s = owner.get("/api/v1/losses").json()
    ok("/losses: jami 265 000 so'm (2 holat), filial bo'yicha", s.get("total", {}).get("sum") == 265000
       and s["total"]["n"] == 2 and s["branches"][0]["id"] == bid, str(s.get("total")))
    off = notify.cfg.tz_offset * 3600
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + off))
    cmpd = owner.get("/api/v1/compare", params={"period": "day", "date": day}).json()
    ok("Taqqoslash: oldini olingan zarar (jami va filial)", cmpd.get("total", {}).get("loss") == 265000
       and cmpd["branches"][0]["theft"]["loss"] == 265000, str(cmpd.get("total")))

    # --- Video sinovi: zonalar
    tmp = Path(tempfile.mkdtemp(prefix="t310_vid_"))
    vid = tmp / "kassa.mp4"
    wv = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 10, (96, 64))
    for i in range(20):
        wv.write(np.full((64, 96, 3), i * 10, np.uint8))
    wv.release()
    data = vid.read_bytes()
    zones = [{"kind": "navbat", "name": "1-kassa", "pts": [[0, 0.7], [1, 0.7], [1, 1], [0, 1]]},
             {"kind": "chiziq", "name": "Eshik", "pts": [[1, 0.5], [0, 0.5]]}]

    def up(params):
        return owner.post("/api/v1/video-tests", params={"branch_id": bid, "name": "kassa.mp4", **params}, content=data,
                          headers={"Content-Type": "application/octet-stream"})
    r = up({"zones": json.dumps([{"kind": "bomba", "pts": [[0, 0], [1, 0], [1, 1]]}])})
    ok("Noto'g'ri zona — 400 (fayl saqlanmaydi)", r.status_code == 400 and "Zonalar" in r.text and not any(vt.VIDEO_DIR.glob("*")))
    cdb.execute("UPDATE branches SET version='3.0.0' WHERE id=:b", {"b": bid})
    r = up({"zones": json.dumps(zones)})
    ok("Edge 3.0.0 — chizilgan zonalar bilan yuklab bo'lmaydi (409, tushuntirish)", r.status_code == 409 and "3.1.0" in r.text)
    cdb.execute("UPDATE branches SET version='3.1.0' WHERE id=:b", {"b": bid})
    n_owner0 = len([c for c in tg_calls("sendMessage", CLOUD_TG) if str(c["data"].get("chat_id")) == "8001"])
    n_guard0 = len([c for c in tg_calls("sendMessage", CLOUD_TG) if str(c["data"].get("chat_id")) == "8002"])
    r = up({"zones": json.dumps(zones), "zone_source_id": "3"})
    t1 = r.json().get("test", {}) if r.status_code == 200 else {}
    cmd = cdb.row("SELECT * FROM commands WHERE type='video_test' ORDER BY id DESC LIMIT 1") or {}
    ok("Yuklandi: zonalar saqlandi, kamera zonalari o'rniga (zone_source_id bekor), buyruqda zonalar",
       len(t1.get("zones") or []) == 2 and t1.get("zone_source_id") is None
       and (cdb.json_load(cmd.get("payload")) or {}).get("zones") == t1.get("zones"), r.text[:200])
    st = wait(lambda: (vt.get(t1["id"]) or {}).get("status") == "tayyor", 40)
    job = db.row("SELECT * FROM jobs WHERE cloud_test_id=?", (t1.get("id"),)) or {}
    ok("Edge zonalarni oldi (tahlil shu zonalar bilan, kamera zonasiz)", mgr.zones_seen and [z["kind"] for z in mgr.zones_seen]
       == ["navbat", "chiziq"] and job.get("zone_source_id") is None, str(mgr.zones_seen)[:120])
    t1d = owner.get(f"/api/v1/video-tests/{t1.get('id')}").json().get("test", {})
    ok("Natija: o'lchovlar bulutda (navbat 3 kishi, kirdi 7)", st and (t1d.get("metrics") or {}).get("queue", [{}])[0].get("max_len")
       == 3 and t1d["metrics"]["lines"][0]["in"] == 7, str(t1d.get("metrics"))[:160])
    test_msg = wait(lambda: [c for c in tg_calls("sendMessage", CLOUD_TG) if str(c["data"].get("chat_id")) == "8001"
                             and "Video sinovi</b>: kassa.mp4" in c["data"].get("text", "")], 25)
    ok("Sinov signali yuklagan egasiga Telegram'da ('🧪 Video sinovi: fayl — videoning 4.5-soniyasi')",
       test_msg and "videoning 4.5-soniyasi" in test_msg[-1]["data"]["text"], test_msg[-1]["data"]["text"][:120] if test_msg else "")
    done = wait(lambda: [c for c in tg_calls("sendMessage", CLOUD_TG) if str(c["data"].get("chat_id")) == "8001"
                         and "Video sinovi tayyor" in c["data"].get("text", "")], 25)
    ok("Yakuniy natija Telegram'da: signallar va o'lchovlar", done and "eng uzun navbat 3 kishi" in done[-1]["data"]["text"]
       and "kirdi 7" in done[-1]["data"]["text"], done[-1]["data"]["text"][:200] if done else "")
    time.sleep(2.5)
    n_done = len([c for c in tg_calls("sendMessage", CLOUD_TG) if "Video sinovi tayyor" in c["data"].get("text", "")])
    n_guard = len([c for c in tg_calls("sendMessage", CLOUD_TG) if str(c["data"].get("chat_id")) == "8002"])
    ok("Yakuniy xabar bir marta; qo'riqchiga (sinovni yuklamagan) sinov xabarlari yo'q", n_done == 1 and n_guard == n_guard0,
       f"tayyor {n_done}, qo'riqchi {n_guard0}→{n_guard}")
    jm = db.row("SELECT cloud_meta FROM jobs WHERE cloud_test_id=?", (t1.get("id"),)) or {}
    ok("O'lchovlar bulutda saqlangach edge ularni qayta yubormaydi (metrics_sent)",
       wait(lambda: json.loads((db.row("SELECT cloud_meta FROM jobs WHERE cloud_test_id=?", (t1.get("id"),)) or {})
                               .get("cloud_meta") or "{}").get("metrics_sent"), 20)
       and not any("metrics" in x for x in __import__("app.cloud_tests", fromlist=["x"]).report()), str(jm)[:120])
    evj_uid = (cdb.row("SELECT uid FROM events WHERE uid LIKE :p ORDER BY created_at DESC LIMIT 1", {"p": f"{DEV}:%"}) or {})
    owner.post(f"/api/v1/events/{evj_uid.get('uid')}/review", json={"status": "tasdiqlangan"})
    ok("Video sinovi hodisasiga summa yozilmaydi (409)", owner.post(f"/api/v1/events/{evj_uid.get('uid')}/loss",
                                                                     json={"loss_sum": 5}).status_code == 409)
    # kechikib kelgan o'lchovlar: bulutda "tayyor", o'lchov yo'q → keyingi hisobot bilan yoziladi
    with cdb.engine().begin() as c:
        c.execute(cdb.video_tests.update().where(cdb.video_tests.c.id == t1["id"]).values(metrics=None))
        vt.apply_edge_report(c, {"id": bid}, [{"id": t1["id"], "token": (vt.get(t1["id"]) or {}).get("token"),
                                               "status": "tayyor", "metrics": {"queue": [], "lines": [], "areas": []}}])
    lm = cdb.json_load((vt.get(t1["id"]) or {}).get("metrics")) or {}
    ok("Kechikkan o'lchovlar yakuniy sinovga yoziladi (holat o'zgarmaydi, shakli tozalangan)",
       lm.get("queue") == [] and lm.get("lines") == [] and lm.get("areas") == [] and "duration" in lm
       and (vt.get(t1["id"]) or {}).get("status") == "tayyor", str(lm))
    st_: list = []
    with cdb.engine().begin() as c:
        vt.apply_edge_report(c, {"id": bid}, [{"id": 987654, "token": "x", "status": "tayyor", "metrics": {"queue": []}},
                                              {"id": t1["id"], "token": "boshqa", "status": "tayyor",
                                               "metrics": {"queue": []}},
                                              {"id": 987655, "token": "x", "status": "tayyor"}], stored=st_)
    ok("Bulut: o'chirilgan / boshqa sinov o'lchovlari ham 'qabul qilindi' (edge har hello'da qayta yubormaydi)",
       sorted(st_) == sorted([987654, t1["id"]]), str(st_))
    pp = notify.TelegramPoller()
    pp.stop()
    ok("Bulut boti to'xtatiladi (server o'chishida xato yo'q)", pp.stop_evt.is_set())
    # toza nusxa sig'masa (bulut tana chegarasi) — klip toza nusxasiz yuboriladi, dalil yo'qolmaydi
    old_lim, old_extra = sync.CLIP_LIMIT, sync.CLEAN_EXTRA
    sync.CLIP_LIMIT, sync.CLEAN_EXTRA = 6000, 0
    try:
        evb = _event()
        uidb = f"{DEV}:{evb}"
        got = wait(lambda: (cdb.row("SELECT clip_file FROM events WHERE uid=:u", {"u": uidb}) or {}).get("clip_file"), 25)
        rowb = cdb.row("SELECT clip_file, clip_clean FROM events WHERE uid=:u", {"u": uidb}) or {}
        ok("Klip + toza nusxa chegaradan katta — asosiy klip yetdi, toza nusxasiz", got and not rowb.get("clip_clean"),
           str(rowb))
    finally:
        sync.CLIP_LIMIT, sync.CLEAN_EXTRA = old_lim, old_extra
    # bulut: bitta sinovdan ko'pi bilan 20 ta signal xabari (faqat Telegram)
    t_cap = vt.get(t1["id"]) or {}
    n0 = cdb.scalar("SELECT COUNT(*) FROM notify_outbox WHERE kind='event' AND user_id=:u", {"u": owner_id}) or 0
    for i in range(25):
        luid = f"{DEV}:{900000 + i}"
        cdb.upsert(cdb.events, {"uid": luid, "branch_id": bid, "device_id": DEV, "local_id": 900000 + i,
                                "created_at": time.time(), "updated_at": time.time(), "kind": "pocket", "score": 80,
                                "status": "yangi", "source_name": "Video: kassa.mp4", "received_at": time.time(),
                                "data": {"job_id": t_cap.get("job_id"), "cloud_test_id": t1["id"], "video_time": i,
                                         "reasons": []}}, ["uid"], ["status"])
        notify.on_new_event(luid)
    n1 = cdb.scalar("SELECT COUNT(*) FROM notify_outbox WHERE kind='event' AND user_id=:u", {"u": owner_id}) or 0
    ok("Bulut: bitta Video sinovidan ko'pi bilan 20 ta signal xabari (birinchi signal bilan birga)", n1 - n0 == 19,
       f"{n1 - n0}")
    w.stop()
    w.join(10)
    sync._worker = None
    from app import cloud_tests as ct

    # (sync to'xtatilgan: hello bu qatorlarni bulutga olib ketmasin)
    now_ = time.time()
    # 4242: bulutga 7 soat oldin yetgan, "saqlandi" kelmagan; 4243: 7 soat oldin tugagan, lekin aloqa endi tiklandi
    for tid_, meta_ in ((4242, json.dumps({"metrics_offered_at": now_ - 7 * 3600})), (4243, "{}")):
        db.execute("INSERT INTO jobs(filename, path, status, created_at, finished_at, cloud_test_id, cloud_meta, metrics) "
                   "VALUES(?,?,?,?,?,?,?,?)", ("e.mp4", "/x/e.mp4", "tayyor", now_ - 7 * 3600, now_ - 7 * 3600, tid_, meta_,
                                               '{"queue": []}'))
    rep_ = {x["id"]: x for x in ct.report()}
    ct.metrics_offered([4243])
    m43 = json.loads((db.row("SELECT cloud_meta FROM jobs WHERE cloud_test_id=4243") or {}).get("cloud_meta") or "{}")
    ok("Edge: bulut tasdiqlamasa — birinchi yetkazilgandan 6 soat o'tgach qayta yuborilmaydi (aloqasiz vaqt hisobga kirmaydi)",
       "metrics" not in rep_.get(4242, {"metrics": 1}) and "metrics" in rep_.get(4243, {})
       and isinstance(m43.get("metrics_offered_at"), (int, float)), str(list(rep_)[:6]))
    db.execute("DELETE FROM jobs WHERE cloud_test_id IN (4242, 4243)")


# ============================================================================= 6. haqiqiy video
def real_suite() -> None:
    """Haqiqiy model bilan FileJob: video ustiga chizilgan zonalar → o'lchovlar (qo'lda sanalgan natijalar bilan)."""
    from app.ai.manager import get_manager
    from app.ai.worker import FileJob

    print("\n--- Haqiqiy video: chizilgan zonalar bilan o'lchovlar ---")
    db.set_settings({"ai_fps": "8", "calm_fps": "4", "sensitivity": "normal"})
    mgr = get_manager()
    mgr.notifier = None
    cases = [
        ("navbat_guruh.mp4", [{"kind": "navbat", "name": "Kassa", "pts": [[0, 0.75], [1, 0.75], [1, 1], [0, 1]]}],
         lambda m: m["queue"][0]["max_len"] == 3, "eng uzun navbat 3 kishi (1, 2, 3 kishilik guruhlar)"),
        ("navbat_namuna.mp4", [{"kind": "navbat", "name": "Stol",
                                "pts": [[0.40, 0.70], [0.78, 0.70], [0.84, 1.0], [0.34, 1.0]]}],
         lambda m: m["queue"][0]["max_len"] == 1 and 5 <= m["queue"][0]["served"] <= 7,
         "6 kishi birin-ketin (eng uzun navbat 1, xizmat 6 ±1)"),
        ("sanash_namuna.mp4", [{"kind": "chiziq", "name": "Eshik", "pts": [[1.0, 0.72], [0.0, 0.72]]}],
         lambda m: m["lines"][0]["in"] == 7 and m["lines"][0]["out"] == 0, "7 kishi kirdi (strelka yuqoriga)"),
    ]
    for name, zl, check, label in cases:
        path = ROOT / "samples" / name
        jid = db.execute("INSERT INTO jobs(filename, path, status, progress, created_at, cloud_test_id, cloud_meta) "
                         "VALUES(?,?,?,?,?,?,?)", (name, str(path), "navbatda", 0, time.time(), None,
                                                   json.dumps({"zones": zl})))
        t0 = time.time()
        FileJob(db.row("SELECT * FROM jobs WHERE id=?", (jid,)), mgr).run()
        j = db.row("SELECT * FROM jobs WHERE id=?", (jid,)) or {}
        m = json.loads(j.get("metrics") or "{}")
        good = False
        try:
            good = j.get("status") == "tayyor" and check(m)
        except (KeyError, IndexError, TypeError):
            good = False
        brief = video_metrics.brief(m)
        ok(f"{name}: {label}", good, f"{brief} ({time.time() - t0:.0f} s)")


# ============================================================================= 7. brauzer
def browser_suite() -> None:
    print("\n--- Panel brauzerda: zona chizish, natija, klip almashtirgich, summa ---")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ok("Playwright yo'q — brauzer sinovi o'tkazilmadi", True)
        return
    import shutil
    import subprocess

    cdb = cloud.cdb
    cdb.reset_engine()
    cdb.meta.drop_all(cdb.engine())
    cloud.init_db()
    for t in ("jobs", "outbox", "events"):
        db.execute(f"DELETE FROM {t}")
    db.set_settings({"device_id": DEV})
    comp = cdb.create_company("Brauzer 3.1")
    cdb.create_user(comp, "egasi@br310.uz", "Br310Parol-2026", "Egasi", "owner")
    bid, _tok = cdb.create_branch(comp, "Yunusobod", DEV)
    cdb.execute("UPDATE branches SET token_hash=:h WHERE id=:b", {"h": cdb.sha256(TOKEN), "b": bid})
    mgr = FakeMgr()
    w = sync.SyncWorker(manager=mgr)
    sync._worker = w
    w.start()
    wait(lambda: (cdb.row("SELECT version FROM branches WHERE id=:b", {"b": bid}) or {}).get("version"), 15)
    # Playwright'dagi Chromium H.264 ni o'qimaydi — WebM (VP8) sinov videosi
    tmp = Path(tempfile.mkdtemp(prefix="t310_br_"))
    webm = tmp / "kassa_brauzer.webm"
    ff = shutil.which("ffmpeg")
    if ff:
        subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(ROOT / "samples" / "navbat_guruh.mp4"), "-t", "4",
                        "-an", "-c:v", "libvpx", "-b:v", "300k", str(webm)], check=False, timeout=120)
    if not webm.is_file():
        ok("ffmpeg yo'q — WebM sinov videosi yasalmadi (brauzer sinovi o'tkazilmadi)", True)
        w.stop()
        return
    ev = _event(status="tasdiqlangan")
    uid = f"{DEV}:{ev}"
    wait(lambda: (cdb.row("SELECT clip_clean FROM events WHERE uid=:u", {"u": uid}) or {}).get("clip_clean"), 20)
    base = f"http://127.0.0.1:{PORT}"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        pg = ctx.new_page()
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        reqs: list[str] = []
        pg.on("request", lambda r: reqs.append(r.url) if "/api/v1/video-tests?" in r.url else None)
        pg.goto(f"{base}/login/")
        pg.fill('input[type="email"]', "egasi@br310.uz")
        pg.fill('input[type="password"]', "Br310Parol-2026")
        pg.keyboard.press("Enter")
        pg.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        pg.goto(f"{base}/videos/")
        pg.locator("form.vt-upload").wait_for(timeout=15000)
        pg.set_input_files('input[type="file"]', str(webm))
        pg.select_option('[data-testid="vt-zone-select"]', "draw")
        cv = pg.locator('[data-testid="vt-canvas"]')
        cv.wait_for(timeout=20000)
        cv.scroll_into_view_if_needed()
        pg.wait_for_timeout(200)
        box = cv.bounding_box()
        ok("Video kadri chiqdi — zona chizish maydoni", bool(box) and box["width"] > 300)

        def at(x, y):
            pg.mouse.click(box["x"] + box["width"] * x, box["y"] + box["height"] * y)
        for x, y in ((0.02, 0.76), (0.98, 0.76), (0.98, 0.98), (0.02, 0.98)):
            at(x, y)
            pg.wait_for_timeout(80)
        pg.screenshot(path=str(Path(tempfile.gettempdir()) / "t310_draft.png"))
        pg.click('[data-testid="vt-zclose"]', timeout=5000)
        pg.select_option('[data-testid="vt-zkind"]', "chiziq")
        cv.scroll_into_view_if_needed()
        box = cv.bounding_box()
        at(0.9, 0.5)
        at(0.1, 0.5)
        items = pg.locator(".vt-zlist li")
        ok("Ikki zona chizildi: kassa navbati (4 nuqta) va sanash chizig'i", items.count() == 2
           and "Kassa navbati" in items.nth(0).inner_text() and "Sanash chizig'i" in items.nth(1).inner_text())
        pg.screenshot(path=str(Path(tempfile.gettempdir()) / "t310_zones.png"))
        pg.click('[data-testid="vt-submit"]')
        pg.locator('[data-testid="vt-ok"]').wait_for(timeout=20000)
        q = [u for u in reqs if "zones=" in u]
        sent = json.loads(httpx.QueryParams(q[-1].split("?", 1)[1]).get("zones")) if q else []
        ok("Yuklash so'rovida zonalar (normallangan nuqtalar)", len(sent) == 2 and sent[0]["kind"] == "navbat"
           and len(sent[0]["pts"]) == 4 and all(0 <= v <= 1 for p in sent[0]["pts"] for v in p)
           and sent[1]["kind"] == "chiziq", str(sent)[:160])
        pg.locator('tr[data-testid="vt-row"][data-status="tayyor"]').first.wait_for(timeout=40000)
        pg.locator('tr[data-testid="vt-row"][data-status="tayyor"] button:has-text("Natija")').first.click()
        pg.locator('[data-testid="vt-metrics"]').wait_for(timeout=20000)
        ok("Natijada o'lchovlar: navbat 3 kishi, kirdi 7", pg.locator('[data-testid="vt-q-max"]').first.inner_text() == "3"
           and pg.locator('[data-testid="vt-in"]').first.inner_text() == "7")
        pg.screenshot(path=str(Path(tempfile.gettempdir()) / "t310_metrics.png"), full_page=True)
        # hodisa: Belgili / Toza va summa
        pg.goto(f"{base}/events/?uid={uid}")
        pg.locator('[data-testid="clip"]').wait_for(timeout=15000)
        src0 = pg.locator('[data-testid="clip"]').get_attribute("src") or ""
        pg.click('[data-testid="clip-clean"]')
        pg.wait_for_function("() => (document.querySelector('[data-testid=clip]')||{}).src?.includes('_clean')", timeout=5000)
        ok("Klip almashtirgich: 'Toza' — chizmalarsiz video", "_clean" not in src0
           and "_clean" in (pg.locator('[data-testid="clip"]').get_attribute("src") or ""))
        pg.fill('[data-testid="loss-input"]', "320 000")
        pg.click('[data-testid="loss-save"]')
        pg.locator('[data-testid="loss-sum"]').wait_for(timeout=10000)
        ok("Summa panelda saqlandi: '320 000 so'm' (edge'ga ham)", "320 000 so'm" in pg.locator('[data-testid="loss-sum"]').inner_text()
           and wait(lambda: db.get_event(ev).get("loss_sum") == 320000, 25))
        pg.screenshot(path=str(Path(tempfile.gettempdir()) / "t310_event.png"))
        pg.goto(f"{base}/")
        pg.locator('[data-testid="loss-strip"]').wait_for(timeout=15000)
        ok("Bosh sahifa: 'Oldini olingan zarar' — 320 000 so'm",
           "320 000" in pg.locator('[data-testid="loss-strip"]').inner_text())
        ok("Konsolda xato yo'q", not errs, str(errs[:3]))
        ctx.close()
        b.close()
    w.stop()
    w.join(10)
    sync._worker = None


def main() -> int:
    t0 = time.time()
    db.init_db()
    if "--real" in sys.argv:                        # faqat haqiqiy videolar (model bilan)
        real_suite()
        n, t = sum(results), len(results)
        print(f"\nNatija: {n}/{t} sinov o'tdi  ({time.time() - t0:.0f} s)")
        return 0 if n == t else 1
    if "--brauzer" in sys.argv:                     # faqat brauzer bo'limi (tez tekshirish)
        notify.cfg.tg_token = ""
        cloud._state["notify_configured"] = True
        serve()
        browser_suite()
        n, t = sum(results), len(results)
        print(f"\nNatija: {n}/{t} sinov o'tdi  ({time.time() - t0:.0f} s)")
        return 0 if n == t else 1
    unit_suite()
    clip_suite()
    ev_tg = edge_bot_suite()
    edge_web_suite(ev_tg)
    notify.cfg.tg_token = CLOUD_TG
    notify.cfg.tg_api = f"http://127.0.0.1:{TG_PORT}"
    notify.cfg.public_url = f"http://127.0.0.1:{PORT}"
    notify.state["tg_conflict"] = False
    cloud._state["notify_configured"] = True
    serve()
    cloud_suite()
    if not FAST:
        browser_suite()
        real_suite()
    n, t = sum(results), len(results)
    print(f"\nNatija: {n}/{t} sinov o'tdi  ({time.time() - t0:.0f} s)")
    return 0 if n == t else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()                 # Windows'da chiqish quvurga ketganda oxirgi "Natija" qatori yo'qolmasin
    os._exit(code)
