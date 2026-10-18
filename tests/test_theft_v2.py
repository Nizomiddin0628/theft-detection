"""2.3.0 (7-bosqich) sinovi: o'g'irlik v2 — qayta o'qitish, ko'rmagan baholash (oof), champion/challenger (soya model,
solishtiruv, faollashtirish, bekor qilish), faol model ko'rsatkichi data/ da, janjal (tajriba), dalil paketi (ZIP + PDF +
SHA-256), jonli xato darvozasi (≤ 3 / kamera / kun) va buyruq qatori, start_windows.bat porti.

    python -m tests.test_theft_v2

Eslatma: sintetik skelet va soxta javoblar — mantiq sinovi. Haqiqiy natija: AI model sahifasidagi solishtiruv
(sizning ✅/❌ javoblaringiz) va Aniqlik sinovidagi darvoza.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_v2_")   # hech qachon jonli data/ emas
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app import config, db, theft_eval as te  # noqa: E402
from app.ai import safety  # noqa: E402
from app.ml import champion, synthetic  # noqa: E402
from tests import safety_sim as sim  # noqa: E402

results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def login(client) -> bool:
    r = client.post("/login", data={"username": "admin", "password": config.ADMIN_PASSWORD, "next": "/"},
                    follow_redirects=False)
    return r.status_code in (302, 303)


# ============================================================================ 1. champion/challenger hisobi
def math_suite() -> None:
    print("\n--- Champion / challenger hisobi ---")
    ok("Champion hissasi ball tarkibidan", champion.ml_contrib([["Cho'ntak", 35], ["AI model ehtimoli 80%", 6]]) == 6
       and champion.ml_contrib("[]") == 0)
    ok("Hissa formulasi (analyzer bilan bir xil)", champion.contrib(0.9, 10) == 8 and champion.contrib(0.4, 10) == 0)
    ok("AUC: mukammal 1.0, teskari 0.0, bir sinf — None",
       champion.auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == 1.0 and champion.auc([1, 0], [0.1, 0.9]) == 0.0
       and champion.auc([1, 1], [0.5, 0.6]) is None)
    evs = []
    for i in range(30):                                  # 15 ta ✅ (ball 66), 15 ta ❌ (ball 62); champion hissasi 6
        y = i % 2 == 0
        evs.append({"id": i, "status": "tasdiqlangan" if y else "rad_etilgan", "score": 66 if y else 62,
                    "breakdown": [["AI model ehtimoli 80%", 6]], "ml_prob": 0.8, "p": 0.9 if y else 0.2})
    s = champion.simulate(evs, "p", 10, 60)
    ok("Simulyatsiya: challenger ❌ larni chiqarmaydi (62 − 6 + 0 = 56 < 60), ✅ lar qoladi (66 − 6 + 8 = 68)",
       s["champ_fp"] == 15 and s["fp"] == 0 and s["tp"] == 15 and not s["lost_tp"] and len(s["saved_fp"]) == 15, str(s)[:200])
    v = champion.verdict(s)
    ok("Tavsiya: challenger yaxshiroq", v["enough"] and v["better"], v["text"])
    for e in evs:
        if e["status"] == "tasdiqlangan":
            e["p"], e["score"] = 0.3, 63                  # 63 − 6 + 0 = 57 < 60: challenger haqiqiy signalni yo'qotadi
    v = champion.verdict(champion.simulate(evs, "p", 10, 60))
    ok("Tavsiya: ✅ yo'qolsa — faollashtirish tavsiya etilmaydi", v["enough"] and not v["better"] and "o'tkazib" in v["text"],
       v["text"])
    v = champion.verdict(champion.simulate(evs[:8], "p", 10, 60))
    ok("Ma'lumot kam (8 ta) — 'ma'lumot kam'", not v["enough"] and "kam" in v["text"])
    z = {"id": 1, "status": "tasdiqlangan", "score": 72, "score_raw": 72, "thr": 70, "ml_w": 10,
         "breakdown": [["AI model ehtimoli 80%", 6]], "ml_prob": 0.8, "p": 0.5}
    s = champion.simulate([z], "p", 10, 60)
    ok("Har signalning o'z chegarasi (zona: 70): 72 − 6 + 0 = 66 < 70 — ✅ yo'qolardi (umumiy 60 da ko'rinmasdi)",
       s["lost_tp"] == [1] and s["approx"] == 0, str(s)[:160])
    s = champion.simulate([{**z, "ml_w": None}], "p", 10, 60)
    ok("Signal paytida AI model o'chiq bo'lgan (ml_w yo'q) — solishtirilmaydi", s["n"] == 0)
    s = champion.simulate([{**z, "thr": None, "ml_w": None, "score_raw": None}], "p", 10, 60)
    ok("Eski hodisa (chegara yozilmagan) — umumiy chegara, 'taxminiy' deb sanaladi", s["n"] == 1 and s["approx"] == 1
       and not s["lost_tp"])
    s = champion.simulate([{**z, "score": 100, "score_raw": 104, "thr": 100}], "p", 10, 60)
    ok("Ball 100 ga qirqilgan bo'lsa — qirqilmagan ball (score_raw) bilan: 104 − 6 = 98 < 100", s["lost_tp"] == [1])


# ============================================================================ 2. o'qitish va soya model
def make_feedback(n: int = 36, seed: int = 3) -> list[int]:
    from app.config import POSES_DIR

    wins = synthetic.sample_windows(n, seed=seed)
    ids = []
    rng = np.random.default_rng(seed)
    for w in wins:
        y = int(w["label"])
        t_ev = float(w["frames"][-1][0])
        score = float(rng.uniform(62, 80) if y else rng.uniform(60, 70))
        p = float(rng.uniform(0.6, 0.9) if y else rng.uniform(0.4, 0.8))
        bd = [["Cho'ntakka solish", 35]] + ([[f"AI model ehtimoli {p:.0%}", champion.contrib(p, 10)]] if p > 0.5 else [])
        ev = db.insert_event({"source_id": None, "source_name": "Zal", "created_at": time.time() - 86400, "score": score,
                              "level": "o'rta", "kind": "pocket", "reasons": ["sintetik"], "breakdown": bd,
                              "track_id": 1, "ml_prob": p, "status": "tasdiqlangan" if y else "rad_etilgan"})
        name = f"event_{ev:05d}.json"
        (POSES_DIR / name).write_text(json.dumps({"event_id": ev, "t_event": t_ev, "kind": "pocket",
                                                  "seq": [[float(t), k.tolist(), b.tolist()] for t, k, b in w["frames"]]}),
                                      encoding="utf-8")
        db.update_event(ev, pose_path=name)
        ids.append(ev)
    return ids


def train_suite() -> None:
    print("\n--- Qayta o'qitish, ko'rmagan baholash, soya model ---")
    from app.ml import train
    from app.ml.model import ACTIVE_POINTER, BUNDLED_POINTER, BehaviorModel

    import shutil
    from unittest import mock

    from app.ml import model as model_mod

    bundled = BUNDLED_POINTER.read_bytes() if BUNDLED_POINTER.exists() else b""
    champ0 = BehaviorModel.active_path()
    ok("Boshlang'ich (dastur bilan kelgan) model faol", champ0 is not None and champ0.exists(), str(champ0))
    # Ko'rsatkichni ko'chirish (2.2.0 da tanlov git faylida edi)
    tmpd = Path(tempfile.mkdtemp(prefix="hs_ptr_"))
    other = model_mod.MODELS_DIR / "behavior_20200101_000000.joblib"
    shutil.copyfile(champ0, other)
    fake_b, fake_a = tmpd / "bundled.json", tmpd / "active.json"
    with mock.patch.object(model_mod, "BUNDLED_POINTER", fake_b), mock.patch.object(model_mod, "ACTIVE_POINTER", fake_a):
        fake_b.write_text(json.dumps({"path": model_mod.BUNDLED_DEFAULT}), encoding="utf-8")
        m1 = model_mod.migrate_pointer()
        fake_b.write_text(json.dumps({"path": other.name}), encoding="utf-8")
        model_mod._ptr_cache.clear()                  # tashqaridan tez qayta yozildi (mtime bir xil bo'lishi mumkin)
        m2 = model_mod.migrate_pointer()
        a2 = json.loads(fake_a.read_text(encoding="utf-8")) if fake_a.exists() else None
        fake_b.write_text(json.dumps({"path": model_mod.BUNDLED_DEFAULT}), encoding="utf-8")
        model_mod._ptr_cache.clear()
        m3 = model_mod.migrate_pointer()
    ok("Ko'rsatkich ko'chirish: asl holatda — yo'q; boshqa model tanlangan — data/ ga; data/ da bor — tegmaydi",
       not m1 and m2 and a2 == {"path": other.name} and not m3, str((m1, m2, a2, m3)))
    other.unlink()
    ok("models/behavior_active.json git'dagi asl holatda (2.3.0 standart fayl nomi bilan)",
       json.loads(bundled or b"{}").get("path") == model_mod.BUNDLED_DEFAULT)
    # Buzuk model fayli: har kadrda qayta yuklashga urinmaydi
    import joblib

    bad = model_mod.MODELS_DIR / "behavior_19990101_000000.joblib"
    bad.write_bytes(b"buzuk fayl")
    BehaviorModel.set_challenger(bad)
    with mock.patch.object(joblib, "load", side_effect=joblib.load) as jl:
        r1, r2, r3 = BehaviorModel.get_challenger(), BehaviorModel.get_challenger(), BehaviorModel.get_challenger()
    ok("Buzuk challenger — bir marta urinadi, keyin eslab qoladi (kamera oqimini sekinlatmaydi)",
       r1 is None and r2 is None and r3 is None and jl.call_count == 1, str(jl.call_count))
    BehaviorModel.set_active(bad)
    with mock.patch.object(joblib, "load", side_effect=joblib.load) as jl:
        r1, r2 = BehaviorModel.get(), BehaviorModel.get()
    ok("Buzuk champion — bir marta urinadi", r1 is None and r2 is None and jl.call_count == 1, str(jl.call_count))
    BehaviorModel.set_challenger(None)
    model_mod.ACTIVE_POINTER.unlink()
    bad.unlink()
    ok("Ko'rsatkich atomar yoziladi, bo'sh challenger — {\"path\": null}",
       json.loads(model_mod.CHALLENGER_POINTER.read_text(encoding="utf-8")) == {"path": None}
       and BehaviorModel.challenger_path() is None and BehaviorModel.active_path() == champ0
       and not list(model_mod.DATA_DIR.glob("*.tmp")))
    ids = make_feedback()
    fb = train.feedback_windows()
    ok("Qo'riqchi javoblari o'qitishga olinadi", len(fb) >= 30, str(len(fb)))
    t0 = time.time()
    res = train.train(n_synthetic=400, use_feedback=True, seed=5, challenger=True)
    oof = res and db.row("SELECT * FROM model_versions WHERE id=?", (res["version_id"],))
    oof_d = json.loads(oof["oof"] or "{}") if oof else {}
    ok(f"Challenger o'qitildi ({time.time() - t0:.0f} s), har javob uchun ko'rmagan ehtimol",
       oof and oof["role"] == "challenger" and len(oof_d) >= 30 and set(int(k) for k in oof_d) <= set(ids), str(len(oof_d)))
    ok("Faol model o'zgarmadi (challenger — soyada)", BehaviorModel.active_path() == champ0)
    ch = BehaviorModel.get_challenger()
    ok("Challenger yuklanadi va bashorat qiladi", ch is not None and
       ch.predict([(float(t), k, b) for t, k, b in fb[0]["frames"]]) is not None)
    ok("Ko'rsatkichlar data/ da, models/behavior_active.json o'zgarmagan (git toza qoladi)",
       BehaviorModel.challenger_path() is not None and (BUNDLED_POINTER.read_bytes() if BUNDLED_POINTER.exists() else b"") == bundled
       and not ACTIVE_POINTER.exists())
    cmp_ = champion.compare()
    ok("Solishtiruv: ko'rmagan baholash ≥ 30 javob, tavsiya matni bor", cmp_["cv"]["n"] >= 30 and cmp_["verdict"]["text"]
       and cmp_["shadow"]["n"] == 0, json.dumps({k: cmp_["cv"][k] for k in ("n", "fp", "champ_fp", "tp", "champ_tp")}))
    vd = cmp_["verdict"]
    ok("Tavsiya faqat soyadan: soya bo'sh — 'faollashtiring' yo'q, ko'rmagan baho faqat ma'lumot",
       not vd["better"] and vd["source"] == "soya (jonli)" and "soyada" in vd["text"]
       and (not champion.verdict(cmp_["cv"])["enough"] or "faqat ma'lumot" in vd["text"]), vd["text"])
    # Soya: analyzer jonli signalda challenger ehtimolini yozadi (ballga qo'shmaydi)
    from app.ai.analyzer import Analyzer
    from app.ai.engine import Obj, Person

    db.set_settings({"threshold": 35, "dwell_sec": 0.3, "use_ml": "1", "cooldown_sec": 15})
    rng = np.random.default_rng(1)

    def run_seq(seq):
        an = Analyzer("t")
        items = {round(t, 3): box for t, box in seq["items"]}
        for t, k, box in seq["frames"]:
            objs = [Obj(items[round(t, 3)], 0.8, 39, "shisha", "item")] if items.get(round(t, 3)) is not None else []
            al, _ = an.update([Person(box=box, score=0.9, kpts=k, track_id=1)], objs, t, synthetic.W, synthetic.H)
            if al:
                return al[0]
        return None

    got = seq_hit = None
    for _ in range(20):
        seq_hit = synthetic.generate("pocket", rng, with_item=True)
        got = run_seq(seq_hit)
        if got:
            break
    ch_saved = BehaviorModel.challenger_path()
    BehaviorModel.set_challenger(None)
    alone = run_seq(seq_hit) if got else None
    BehaviorModel.set_challenger(ch_saved)
    again = run_seq(seq_hit) if got else None
    ok("Challenger ball va signalga ta'sir qilmaydi (u bilan va usiz — bir xil ball, ball tarkibi)",
       got and alone and again and alone["score"] == again["score"] == got["score"]
       and alone["breakdown"] == again["breakdown"] and alone.get("ml_ch") is None and again.get("ml_ch") is not None,
       str((got and got["score"], alone and alone["score"])))
    ok("Signalda haqiqiy chegara, ML og'irligi va qirqilmagan ball", got and got.get("thr") == 35.0
       and got.get("ml_w") == float(db.get_settings().get("ml_weight", 10) or 10) and got.get("score_raw") is not None,
       str(got and {k: got.get(k) for k in ("thr", "ml_w", "score_raw", "score")}))
    ok("Soya: signalda challenger ehtimoli (ml_ch) va fayl nomi, champion ehtimoli alohida",
       got is not None and got.get("ml_ch") is not None and got.get("ml_ch_ver") == BehaviorModel.challenger_path().name
       and not any(str(x[0]).startswith("Challenger") for x in got["breakdown"]), str(got and got.get("ml_ch")))
    from app.ai.manager import Manager

    mgr = Manager.__new__(Manager)
    mgr.listeners, mgr.notifier = [], None
    eid = Manager.create_event(mgr, got, source={"id": None, "name": "Zal"}, job_id=None, video_time=None)
    e = db.get_event(eid)
    ok("Hodisaga ml_ch, chegara va ML og'irligi yoziladi", e["ml_ch"] is not None and e["ml_ch_ver"] == got["ml_ch_ver"]
       and e["thr"] == 35.0 and e["ml_w"] is not None and e["score_raw"] is not None)
    db.review_event(eid, "rad_etilgan", "sinov")
    cmp_ = champion.compare()
    ok("Soya natijasida yangi javob ko'rinadi", cmp_["shadow"]["n"] == 1 and cmp_["shadow"]["approx"] == 0,
       str(cmp_["shadow"]["n"]))
    xid = db.insert_event({"source_id": None, "source_name": "Zal", "created_at": time.time(), "score": 70, "level": "o'rta",
                           "kind": "exit", "reasons": [], "breakdown": [], "track_id": 1, "ml_prob": 0.7,
                           "ml_ch": 0.2, "ml_ch_ver": got["ml_ch_ver"], "thr": 35.0, "ml_w": 10.0, "score_raw": 70.0,
                           "status": "rad_etilgan"})
    ok("'Chiqish' signali solishtiruvga kirmaydi (o'sha hodisani ikkinchi marta sanardi)",
       champion.compare()["shadow"]["n"] == 1 and xid)
    db.set_settings({"threshold": 60, "dwell_sec": 0.45})
    # Faollashtirish va qaytarish
    newp = BehaviorModel.challenger_path()
    r = champion.promote()
    ok("Challenger → champion; challenger bo'shadi; oldingisi ko'rsatildi",
       BehaviorModel.active_path() == newp and BehaviorModel.challenger_path() is None and r["previous"] == champ0.name)
    ok("Faol ko'rsatkich data/behavior_active.json da, dastur bilan kelgani o'zgarmagan",
       ACTIVE_POINTER.exists() and (BUNDLED_POINTER.read_bytes() if BUNDLED_POINTER.exists() else b"") == bundled)
    row = db.row("SELECT * FROM model_versions WHERE path=?", (newp.name,))
    ok("Versiyalar jadvalida: faol, champion, faollashtirilgan vaqt", row["active"] == 1 and row["role"] == "champion"
       and row["promoted_at"])
    try:
        champion.promote()
        ok("Challenger yo'q — xato", False)
    except ValueError:
        ok("Challenger yo'q — xato", True)
    role = lambda vid: db.row("SELECT role FROM model_versions WHERE id=?", (vid,))["role"]  # noqa: E731
    res1 = train.train(n_synthetic=300, use_feedback=True, seed=8, challenger=True)
    res2 = train.train(n_synthetic=300, use_feedback=True, seed=6, challenger=True)
    ok("Yangi challenger — oldingisi 'eskirgan'", role(res1["version_id"]) == "eskirgan"
       and BehaviorModel.challenger_path() == Path(res2["path"]))
    champion.drop()
    ok("Challenger bekor qilindi (faol model o'zgarmadi)", BehaviorModel.challenger_path() is None and
       BehaviorModel.active_path() == newp and role(res2["version_id"]) == "rad etilgan")
    res3 = train.train(n_synthetic=300, use_feedback=False, seed=4, challenger=True)
    res4 = train.train(n_synthetic=300, use_feedback=True, seed=3)
    r4 = db.row("SELECT oof FROM model_versions WHERE id=?", (res4["version_id"],))
    ok("Champion almashdi (o'qitib faollashtirildi) — eski challenger 'eskirgan', soya bo'shadi; oddiy o'qitishda oof yo'q",
       BehaviorModel.challenger_path() is None and role(res3["version_id"]) == "eskirgan"
       and BehaviorModel.active_path() == Path(res4["path"]) and json.loads(r4["oof"] or "{}") == {})
    res5 = train.train(n_synthetic=300, use_feedback=True, seed=2, challenger=True)
    from app import web

    web.api_model_activate(res4["version_id"])
    ok("Hozirgi champion'ni qayta 'faollashtirish' — challenger soyasi davom etadi",
       BehaviorModel.challenger_path() == Path(res5["path"]) and role(res5["version_id"]) == "challenger")
    from app.ml import model as model_mod

    ptr = model_mod.CHALLENGER_POINTER
    model_mod.pointer_name(ptr)
    os.utime(ptr, ns=(time.time_ns(), time.time_ns() + 5_000_000))
    with mock.patch.object(Path, "read_text", side_effect=PermissionError("band")):
        during = BehaviorModel.challenger_path()
    ok("Ko'rsatkich o'qilmay qolsa (Windows: yozish paytida) — oxirgi qiymat, standart modelga sakramaydi",
       during == Path(res5["path"]))
    nid = db.row("SELECT id FROM model_versions WHERE path=? ORDER BY id DESC LIMIT 1", (newp.name,))["id"]
    web.api_model_activate(nid)
    ok("Boshqa versiya qo'lda faollashtirildi — challenger 'eskirgan'", BehaviorModel.challenger_path() is None
       and role(res5["version_id"]) == "eskirgan" and BehaviorModel.active_path() == newp)
    res6 = train.train(n_synthetic=200, use_feedback=False, seed=1, challenger=True)
    Path(res6["path"]).unlink()
    champion.drop()
    ok("Challenger fayli o'chirilgan bo'lsa ham bekor qilish jadvaldagi rolni yangilaydi",
       role(res6["version_id"]) == "rad etilgan" and BehaviorModel.challenger_path() is None)
    excl = make_feedback(4, seed=9)
    db.execute(f"UPDATE events SET kind='fall' WHERE id IN ({','.join(str(i) for i in excl)})")
    fb2 = {w["group"] for w in train.feedback_windows()}
    ok("Xavfsizlik hodisalari o'qitishga kirmaydi", not any(f"ev{i}" in fb2 for i in excl))


def api_suite() -> None:
    print("\n--- Panel API: model, champion/challenger ---")
    from fastapi.testclient import TestClient

    from app import web
    from app.ml.model import BehaviorModel

    c = TestClient(web.app)
    ok("Panelga kirish", login(c))
    r = c.get("/model")
    ok("AI model sahifasi: champion/challenger kartasi", r.status_code == 200 and "Champion / challenger" in r.text)
    j = c.get("/api/model/champion").json()
    ok("/api/model/champion — challenger yo'q", j["challenger"] is None and j["champion"])
    r = c.post("/api/model/train", json={"synthetic": 300, "feedback": "1", "challenger": "1"})
    ok("Panel: challenger o'qitish boshlandi", r.status_code == 200)
    t0 = time.time()
    while time.time() - t0 < 300:
        st = c.get("/api/model/train/status").json()
        if not st["running"]:
            break
        time.sleep(0.5)
    ok("O'qitish tugadi", st.get("result") is not None, (st.get("error") or "") + (st["logs"][-1] if st.get("logs") else ""))
    j = c.get("/api/model/champion").json()
    m = c.get("/api/model").json()
    ok("Challenger paydo bo'ldi, versiyalar ro'yxatida belgilangan (oof ro'yxati katta javobga qo'shilmaydi)",
       j["challenger"] and any(v["is_challenger"] for v in m["versions"]) and all("oof" not in v for v in m["versions"]))
    ok("Challenger faollashtirildi (API)", c.post("/api/model/challenger/promote").status_code == 200
       and BehaviorModel.challenger_path() is None)
    ok("Challenger yo'q — promote 400", c.post("/api/model/challenger/promote").status_code == 400)
    ok("Bekor qilish — doim 200", c.post("/api/model/challenger/drop").status_code == 200)
    vid = m["versions"][-1]["id"]
    ok("Oldingi versiyaga qaytish (mavjud tugma)", c.post(f"/api/model/{vid}/activate").status_code == 200)


# ============================================================================ 3. janjal
def fight_suite() -> None:
    print("\n--- Janjal (tajriba) ---")
    cases = {
        "zarba (2 Hz)": (sim.punching(2.0), sim.standing, None, True),
        "ikkalasi zarba beradi": (sim.punching(1.5), sim.punching(1.7), None, True),
        "gaplashib qo'l silkitish": (sim.gesturing(1.0), sim.gesturing(0.8), None, False),
        "tez, kichik imo-ishora": (sim.gesturing(2.0, 0.07), sim.standing, None, False),
        "qo'l berib ko'rishish": (sim.handshake(), sim.handshake(), None, False),
        "qo'l silkitib yonidan yurib o'tish": (sim.gesturing(1.0, 0.15), sim.standing, (lambda t: -300 + 180 * t), False),
        "uzoqda havoga zarba (nishon yo'q)": (sim.punching(2.0), sim.standing, (lambda t: -350.0), False),
    }
    for name, (fa, fb, ma, exp) in cases.items():
        bad = []
        for fps in (4, 8, 15, 30):
            for noise in (0.0, 0.01, 0.02):
                fd = safety.FightDetector()
                got = []
                for t, ps in sim.two_people(fa, fb, 8.0, fps=fps, noise=noise, move_a=ma, seed=2):
                    got += fd.update(ps, t)
                if bool(got) != exp:
                    bad.append(f"{fps} kadr/s, shovqin {noise}: {len(got)}")
        ok(f"{name} → {'signal' if exp else 'signal yo`q'}", not bad, "; ".join(bad))

    def run(specs, fps, noise, dur=8.0):
        fd = safety.FightDetector()
        n = 0
        for t, ps in sim.people(specs, dur, fps=fps, noise=noise, seed=3):
            n += len(fd.update(ps, t))
        return n

    P = sim.punching(2.0)
    grid = [(f, n) for f in (8, 15, 30) for n in (0.0, 0.02)]
    two_pairs = [(1, sim.burst(P, 0, 1.0), 200, False, None), (2, sim.standing, 440, True, None),
                 (3, sim.burst(P, 1.0, 2.0), 900, False, None), (4, sim.standing, 1140, True, None)]
    r = [run(two_pairs, f, n) for f, n in grid]
    ok("Ikki alohida juftlik, har birida 2 zarba — signal yo'q (zarbalar juftlik bo'yicha sanaladi)", not any(r), str(r))
    r = [run([(1, sim.burst(P, 0, 2.0), 200, False, None), (2, sim.standing, 440, True, None)], f, n) for f, n in grid]
    ok("Bitta juftlik, 4 zarba — bitta signal (har kadr tezligida)", r == [1] * len(r), str(r))
    r = [run([(1, sim.burst(P, 0, 1.5), 200, False, None), (2, sim.standing, 440, True, None)], f, n) for f, n in grid]
    ok("3 zarba — signal yo'q (bitta cho'zilish ko'p kadrda — bitta zarba)", not any(r), str(r))
    g = [(1, sim.gesturing(1.0), 520, False, None), (2, sim.gesturing(0.8), 760, True, None)]
    r = [run(g, f, n, 30.0) for f in (15, 30) for n in (0.02, 0.03)]
    ok("30 s qo'l silkitib gaplashish, kuchli titrash (15/30 kadr/s) — signal yo'q", not any(r), str(r))

    db.set_settings({"safety_fight": "0"})
    mon = safety.SafetyMonitor(1, "Zal")
    out = []
    for t, ps in sim.two_people(sim.punching(2.0), sim.standing, 8.0, fps=8):
        out += mon.update(ps, 1000 + t, 1280, 720, None, None)
    ok("Sukut bo'yicha o'chiq — signal yo'q", not out and config.DEFAULT_SETTINGS["safety_fight"] == "0")
    db.set_settings({"safety_fight": "1", "safety_cooldown_min": "10"})
    mon = safety.SafetyMonitor(1, "Zal")
    out = []
    for t, ps in sim.two_people(sim.punching(2.0), sim.standing, 20.0, fps=8):
        out += mon.update(ps, 1000 + t, 1280, 720, None, None)
    ok("Yoqilgan: 20 s janjal — bitta signal (takror 10 daqiqada bir), formati xavfsizlik signali",
       [a["kind"] for a in out] == ["fight"] and out[0]["score"] == 100 and out[0]["box"] and out[0]["kind_label"] ==
       "Janjal (tajriba)", str([a["kind"] for a in out]))
    ok("Janjal — xavfsizlik turi (sarlavha, SMS tanlovi, bulut)", "fight" in safety.SAFETY_KINDS and "fight" in safety.HEADLINES
       and "fight" in safety.CRITICAL)
    from cloud import notify

    ok("Bulut: janjal turi va sarlavhasi", "fight" in notify.SAFETY_KINDS and "fight" in notify.SAFETY_HEAD)
    db.set_settings({"safety_fight": "0"})


# ============================================================================ 4. dalil paketi
def tiny_clip(path: Path) -> None:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for i in range(20):
        img = np.full((180, 320, 3), 60 + i * 5, np.uint8)
        w.write(img)
    w.release()


def pdf_ok(b: bytes) -> tuple[bool, str]:
    """PDF tuzilishi: sarlavha, xref jadvalidagi har ofset "N 0 obj" ga ko'rsatadi, oxirida %%EOF."""
    if not b.startswith(b"%PDF-1.") or not b.rstrip().endswith(b"%%EOF"):
        return False, "sarlavha/oxiri"
    m = re.search(rb"startxref\s+(\d+)", b[-200:])
    if not m:
        return False, "startxref"
    x = int(m.group(1))
    if not b[x:x + 4] == b"xref":
        return False, "xref ofseti"
    lines = b[x:].split(b"\n")
    n = int(lines[1].split()[1])
    for i in range(1, n):
        off = int(lines[2 + i][:10])
        if not b[off:].startswith(f"{i} 0 obj".encode()):
            return False, f"obyekt {i}"
    return True, f"{n - 1} obyekt"


def evidence_suite() -> None:
    print("\n--- Dalil paketi (ZIP + PDF + SHA-256) ---")
    import hashlib

    from fastapi.testclient import TestClient

    from app import evidence, pdfmini, web
    from app.config import CLIPS_DIR

    tiny_clip(CLIPS_DIR / "ev_t.mp4")
    img = np.full((360, 640, 3), 140, np.uint8)
    cv2.rectangle(img, (200, 80), (300, 330), (40, 40, 200), -1)
    cv2.imwrite(str(CLIPS_DIR / "ev_t.jpg"), img)
    cv2.imwrite(str(CLIPS_DIR / "ev_t_crop.jpg"), img[60:340, 180:320])
    eid = db.insert_event({"source_id": None, "source_name": "Kassa — 1", "created_at": time.time(), "score": 72,
                           "level": "o'rta", "kind": "pocket", "reasons": ["Qo'l cho'ntakka kirdi (0,6 s)", "Buyum ko'rinmay qoldi"],
                           "breakdown": [["Cho'ntakka solish", 35], ["AI model ehtimoli 80%", 6]], "track_id": 3,
                           "ml_prob": 0.8, "status": "tasdiqlangan", "reviewed_by": "Telegram: Ali", "reviewed_at": time.time(),
                           "note": "Мижоз кассага қайтди — kirill izoh", "clip_path": "ev_t.mp4", "thumb_path": "ev_t.jpg",
                           "crop_path": "ev_t_crop.jpg", "susp_start": time.time() - 4, "clip_start": time.time() - 14})
    data, name = evidence.build(eid)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    ok("ZIP: PDF, klip, rasm, odam, manifest, tekshirish yo'riqnomasi",
       names[:1] == [f"hodisa_{eid:05d}.pdf"] and {"klip.mp4", "rasm.jpg", "odam.jpg", "manifest.json", "TEKSHIRISH.txt"} <= set(names),
       str(names))
    man = json.loads(z.read("manifest.json"))
    good = all(hashlib.sha256(z.read(n)).hexdigest() == h["sha256"] for n, h in man["files"].items())
    ok("manifest.json: har fayl SHA-256 si mos, asl klip o'zgarmagan",
       good and hashlib.sha256(z.read("klip.mp4")).hexdigest() == hashlib.sha256((CLIPS_DIR / "ev_t.mp4").read_bytes()).hexdigest())
    pdf = z.read(names[0])
    good, info = pdf_ok(pdf)
    ok("PDF tuzilishi to'g'ri (xref ofsetlari, %%EOF)", good, info)
    ok("PDF ichida rasm (JPEG) va klip xeshi", b"/DCTDecode" in pdf and man["files"]["klip.mp4"]["sha256"][:16].encode() in
       __import__("zlib").decompress(re.search(rb"/FlateDecode >>\nstream\n(.*?)\nendstream", pdf, re.S).group(1))
       + b"".join(__import__("zlib").decompress(x) for x in re.findall(rb"/FlateDecode >>\nstream\n(.*?)\nendstream", pdf, re.S)[1:]))
    ok("Kirill matn lotinga, o'zbekcha belgilar WinAnsi'ga", pdfmini.to_winansi("Мижоз қайтди") == "Mijoz qaytdi"
       and pdfmini.to_winansi("do‘kon — ✅") == "do'kon \x97 ")
    ok("Juda uzun so'z (xesh) qatorlarga bo'linadi", len(pdfmini.wrap("a" * 300, 8, 200)) > 1)
    ok("Izohdagi yangi qatorlar saqlanadi", pdfmini.wrap("satr1\nsatr2\r\n\tsatr3", 10, 300) == ["satr1", "satr2", "satr3"],
       str(pdfmini.wrap("satr1\nsatr2\r\n\tsatr3", 10, 300)))
    d = pdfmini.Doc("t")
    d.text("Sarlavha")
    d.kv("Izoh", "uzun izoh " * 3000)
    ys = [float(re.search(r"([\d.]+) Td", x).group(1)) for pg in d.pages for x in pg if " Td " in x]
    ok("Juda uzun izoh sahifadan chiqmaydi (keyingi sahifalarga o'tadi)", len(d.pages) > 2 and min(ys) >= pdfmini.MARGIN
       and pdf_ok(d.render())[0], f"{len(d.pages)} sahifa, min y {min(ys):.0f}")
    sid = db.insert_event({"source_name": "Zal", "created_at": time.time(), "score": 100, "level": "yuqori", "kind": "fall",
                           "reasons": ["Odam yiqildi"], "breakdown": [["Yiqilish", 100]], "track_id": 1, "status": "yangi"})
    d2, _ = evidence.build(sid)
    p2 = zipfile.ZipFile(io.BytesIO(d2)).read(f"hodisa_{sid:05d}.pdf")
    body = b"".join(__import__("zlib").decompress(x) for x in re.findall(rb"/FlateDecode >>\nstream\n(.*?)\nendstream", p2, re.S))
    ok("Xavfsizlik hodisasi: 'Xavfsizlik signali', ball yo'q, fayl yo'q bo'lsa ham PDF bor",
       b"Xavfsizlik signali" in body and b"Ball tarkibi" not in body and pdf_ok(p2)[0])
    c = TestClient(web.app)
    login(c)
    r = c.get(f"/api/events/{eid}/evidence.zip")
    ok("API: ZIP yuklab olinadi (Content-Disposition)", r.status_code == 200 and r.headers["content-type"] == "application/zip"
       and f"hodisa_{eid:05d}_dalil.zip" in r.headers.get("content-disposition", ""))
    ok("Yo'q hodisa — 404", c.get("/api/events/99999/evidence.zip").status_code == 404)
    ok("Hodisa sahifasida 'Dalil paketi' tugmasi", "evidence.zip" in c.get(f"/events/{eid}").text)
    from fastapi.testclient import TestClient as TC

    ok("Kirmagan foydalanuvchi — 401", TC(web.app).get(f"/api/events/{eid}/evidence.zip").status_code == 401)


# ============================================================================ 5. jonli darvoza
def gate_suite() -> None:
    print("\n--- Jonli xato darvozasi (≤ 3 / kamera / kun) ---")
    g = te.gate([{"source_id": 1, "name": "A", "minutes": 7 * 1440, "rejected": 14, "confirmed": 5, "unreviewed": 1},
                 {"source_id": 2, "name": "B", "minutes": 10 * 1440, "rejected": 30, "confirmed": 2, "unreviewed": 0},
                 {"source_id": 3, "name": "C", "minutes": 3 * 1440, "rejected": 1, "confirmed": 0, "unreviewed": 0}])
    ok("A: 2/kun, B: 3/kun — o'tdi; C: 3 kun — hisobga olinmaydi", g["ok"] and g["ready"] == 2 and not g["cams"][2]["enough"],
       str([(c["name"], c["rate"]) for c in g["cams"]]))
    ok("7 kunda 28 ta ❌ (4/kun) — o'tmadi", te.gate([{"name": "A", "minutes": 7 * 1440, "rejected": 28}])["state"] == "otmadi")
    ok("Javobsizlar 20% — o'tmadi (xato soni noma'lum)",
       not te.gate([{"name": "A", "minutes": 7 * 1440, "rejected": 8, "confirmed": 0, "unreviewed": 2}])["ok"])
    ok("Ma'lumot yo'q — 'yetarli emas'", te.gate([])["state"] == "yetarli emas")
    g = te.gate([{"name": "A", "minutes": 2 * 1440, "rejected": 22}])
    ok("2 kunda 22 ta ❌ (7 kunlik chegara 21 dan oshdi) — 7 kun kutmasdan o'tmadi", g["state"] == "otmadi" and not g["ok"])
    g = te.gate([{"name": "A", "minutes": 1000 * 1440, "rejected": 3004}])
    ok("Kuniga 3,004 — o'tmadi (yaxlitlangan 3,0 emas)", g["state"] == "otmadi", str(g["cams"][0]["rate"]))
    ok("Oyna: retention 30 kun / 0 → 90 kun",
       abs(te.window(30, 1e9) - (1e9 - 30 * 86400)) < 1 and abs(te.window(0, 1e9) - (1e9 - 90 * 86400)) < 1)
    sid = db.execute("INSERT INTO sources(name, kind, uri, enabled, loop, created_at) VALUES(?,?,?,?,?,?)",
                     ("Kamera D", "rtsp", "rtsp://d", 0, 1, time.time()))
    m0 = int(time.time() // 60 * 60)
    with db.tx() as con:
        con.executemany("INSERT OR IGNORE INTO metrics_minute(minute, source_id, source_name, up_pct) VALUES(?,?,?,?)",
                        [(m0 - i * 60, sid, "Kamera D", 1.0) for i in range(8 * 1440)])
    for st in ["rad_etilgan"] * 10 + ["tasdiqlangan"] * 3:
        db.insert_event({"source_id": sid, "source_name": "Kamera D", "created_at": time.time() - 3600, "score": 70,
                         "level": "o'rta", "kind": "bag", "reasons": [], "breakdown": [], "track_id": 1, "status": st})
    db.insert_event({"source_id": sid, "source_name": "Kamera D", "created_at": time.time() - 3600, "score": 100,
                     "level": "yuqori", "kind": "fall", "reasons": [], "breakdown": [], "track_id": 1, "status": "rad_etilgan"})
    from fastapi.testclient import TestClient

    from app import web

    c = TestClient(web.app)
    login(c)
    j = c.get("/api/eval/live_gate").json()
    cam = next(x for x in j["cams"] if x["source_id"] == sid)
    ok("API: 8 kun, 10 ta ❌ (xavfsizlik ❌ hisoblanmaydi) → 1,25/kun, o'tdi", cam["rejected"] == 10 and cam["ok"]
       and abs(cam["rate"] - 1.25) < 0.01, str(cam))
    ok("Aniqlik sinovi sahifasida jonli darvoza kartasi", "Jonli kameralarda xato signal" in c.get("/eval").text)
    dels = [db.insert_event({"source_id": sid, "source_name": "Kamera D", "created_at": time.time() - 1800, "score": 70,
                             "level": "o'rta", "kind": "pocket", "reasons": [], "breakdown": [], "track_id": 1,
                             "status": "rad_etilgan"}) for _ in range(4)]
    for i in dels[:3]:
        c.post(f"/api/events/{i}/delete")
    db.execute("UPDATE events SET status='yangi' WHERE id=?", (dels[3],))
    c.post(f"/api/events/{dels[3]}/delete")                   # javobsiz hodisa o'chirildi — "javobsiz" bo'lib qoladi
    cam = next(x for x in c.get("/api/eval/live_gate").json()["cams"] if x["source_id"] == sid)
    ok("O'chirilgan ❌ hodisalar ham sanaladi (o'chirib darvozani aldab bo'lmaydi) — 13 ta; o'chirilgan javobsiz — javobsiz",
       cam["rejected"] == 13 and cam["unreviewed"] == 1, str(cam))
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "tests" / "theft_gate.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("tests/theft_gate.py: HUSHYOR_DATA_DIR dagi baza, o'chirilganlar bilan 13 ta ❌, o'tdi — chiqish kodi 0",
       r.returncode == 0 and "Kamera D" in r.stdout and "❌ 13" in r.stdout, f"kod {r.returncode}: " + (r.stdout + r.stderr)[-300:])
    with db.tx() as con:
        con.executemany("INSERT OR IGNORE INTO metrics_minute(minute, source_id, source_name, up_pct) VALUES(?,?,?,?)",
                        [(m0 - i * 60, sid + 100, "Kamera E", 1.0) for i in range(2 * 1440)])
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "theft_gate.py"), "--db", str(db.DB_PATH)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("Darvoza CLI: 2 kunlik kamera qo'shildi — baribir o'tdi (kam kunli kamera kutiladi), kod 0",
       r.returncode == 0 and "Kamera E" in r.stdout, (r.stdout + r.stderr)[-200:])
    for _ in range(25):
        db.insert_event({"source_id": sid + 100, "source_name": "Kamera E", "created_at": time.time() - 600, "score": 70,
                         "level": "o'rta", "kind": "bag", "reasons": [], "breakdown": [], "track_id": 1,
                         "status": "rad_etilgan"})
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "theft_gate.py"), "--db", str(db.DB_PATH)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("Darvoza CLI: 2 kunda 25 ta ❌ — o'tmadi, kod 1", r.returncode == 1, f"kod {r.returncode}")


def misc_suite() -> None:
    print("\n--- Boshqa ---")
    bat = (ROOT / "start_windows.bat").read_bytes()
    ok("start_windows.bat: brauzer manzili .env dagi PORT dan (8000 qattiq yozilmagan)", b"%HPORT%" in bat
       and b"http://localhost:8000" not in bat and b"\r\n" in bat and bat.isascii())
    ok("start_windows.bat: birinchi PORT qatori, qo'shtirnoq/bo'shliqsiz, tizim muhitidagi PORT ustun",
       b"if not defined HPORT set \"HPORT=%%b\"" in bat and b'%HPORT:"=%' in bat and b"%HPORT: =%" in bat
       and b'set "HPORT=%PORT%"' in bat)
    ok("Versiya 2.3.1 (edge va bulut)", config.APP_VERSION == "2.3.1" and
       __import__("cloud.server", fromlist=["VERSION"]).VERSION == "2.3.1")


def main() -> int:
    t = time.time()
    db.init_db()
    before = set(config.MODELS_DIR.glob("behavior_*.joblib"))
    try:
        math_suite()
        train_suite()
        api_suite()
        fight_suite()
        evidence_suite()
        gate_suite()
        misc_suite()
    finally:
        for p in set(config.MODELS_DIR.glob("behavior_*.joblib")) - before:   # sinov o'qitgan modellar qolmasin
            p.unlink(missing_ok=True)
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
