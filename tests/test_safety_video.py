"""2.2.0 yiqilish — haqiqiy detektor bilan: ochiq yiqilish videolari (ambianic/fall-detection, Apache-2.0) va
do'kon namunalari (samples/*.mp4 — xato signal 0 bo'lishi kerak).

    python -m tests.test_safety_video

Videolar repoga qo'shilmagan: sinov ularni GitHub'dan o'zi yuklab oladi (SHA-256 tekshiriladi) va vaqtinchalik papkada
saqlaydi. Internet bo'lmasa — bu qism "o'tkazib yuborildi" deb chiqadi (xato emas).

Rostini aytsam:
  * videolar 640×360, 25 kadr/s, 5–15 s, har birida bitta yiqilish; video yiqilishdan keyin darhol tugaydi —
    shuning uchun oxirgi kadr 3,5 s takrorlanadi (odam yerda qoladi). Bu sun'iy qo'shimcha;
  * YOLO11n-pose yerda yotgan odamni ko'pincha topmaydi (yoki 0,25–0,4 ishonch bilan topadi);
  * kutilgan natija (oddiy sezgirlik): 5 tadan 3 tasi (1, 2, 3). 4-video oxiri g'ijimlangan (to'liq yotmaydi),
    5-videoda detektor odamni yiqilish paytida yo'qotadi. Bu do'koningizdagi natija emas — darvoza do'konda
    (gilam ustida sahnalashtirilgan yiqilishlar) o'tadi.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HUSHYOR_DATA_DIR"] = tempfile.mkdtemp(prefix="hushyor_safety_video_")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, evaluation, safety_eval as se  # noqa: E402

COMMIT = "30a050f9761ba5f019e7f83b497e9db8b80ff2f0"
URL = "https://raw.githubusercontent.com/ambianic/fall-detection/{c}/videos/video_{i}.mp4"
SHA = {1: "2de80698736ce82f2441e06218eaa05a26089336c1539ebda6e9a0fa79f5c445",
       2: "ae977072d192665e2e83a648de9ba23cd7b2c0c165bd7cbc56ef9484dc2db5fb",
       3: "d6052e4a6c54a275580eba18224e8559c24a11a6ee352359613a42969cd51289",
       4: "c5ff9f4f35d531541d7a90d60d8c85b4ba4f6a8f566bcc046434f513a8d471f6",
       5: "1c41622c64fe3ce7328ca1c7f671ef3613cf5cef8ace41afacb6accb64646597"}
# Yiqilish lahzasi (odam yerga tekkan payt, soniya) — videolarni kadrma-kadr ko'rib belgilangan
FALLS = {1: 3.2, 2: 4.2, 3: 4.6, 4: 14.3, 5: 6.0}
EXPECT_MIN = 3
CACHE = Path(tempfile.gettempdir()) / "hushyor_fall_videos"
results: list[bool] = []


def ok(name: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def fetch(i: int) -> Path | None:
    import httpx

    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"video_{i}.mp4"
    if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == SHA[i]:
        return p
    try:
        r = httpx.get(URL.format(c=COMMIT, i=i), timeout=60, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"  !    video_{i} yuklab olinmadi: {e}")
        return None
    if hashlib.sha256(r.content).hexdigest() != SHA[i]:
        print(f"  !    video_{i}: SHA-256 mos emas — ishlatilmaydi")
        return None
    p.write_bytes(r.content)
    return p


def main() -> int:
    t_all = time.time()
    db.init_db()
    db.set_settings({"safety_fall_sens": "normal"})
    print("\n--- Ochiq yiqilish videolari (ambianic, oxirgi kadr 3,5 s takrorlanadi) ---")
    paths = {i: fetch(i) for i in FALLS}
    found = 0
    if not all(paths.values()):
        print("  !    Internet yo'q yoki fayllar yuklanmadi — bu qism o'tkazib yuborildi")
    else:
        vids = []
        for i, p in paths.items():
            t0 = time.time()
            r = evaluation.analyze(str(p), hold_last=se.HOLD_SEC)
            m = se.match([FALLS[i]], r["safety"])
            hit = len(m["tp"]) == 1
            found += hit
            vids.append({"id": i, "filename": p.name, "falls": [FALLS[i]], "result": {"safety": r["safety"]}})
            sig = ", ".join(f"{a['kind']}@{a['t']:.1f}" for a in r["safety"]) or "—"
            print(f"       video_{i}: yiqilish {FALLS[i]} s → {'TOPILDI' if hit else 'topilmadi'} (signallar: {sig}; "
                  f"o'g'irlik signali {len(r['alerts'])}; {time.time() - t0:.1f} s)")
            ok(f"video_{i}: xato signal yo'q (yiqilishdan oldin signal yo'q)", not m["fp"], str(m["fp"]))
        g = se.recall_gate(vids)
        ok(f"Recall: {found}/5 (kutilgan ≥ {EXPECT_MIN}; reja darvozasi do'konda ≥ 85%)", found >= EXPECT_MIN,
           f"recall {g['recall']:.0%}")
        ok("Signal yerga tushgandan ≤ 4 s ichida (2 s tasdiq bilan)", all(0 <= x["delay"] <= 4.0 for v in vids
                                                                         for x in se.match(v["falls"], v["result"]["safety"])["tp"]))
    print("\n--- Do'kon namunalari (yiqilish yo'q — xato signal 0 bo'lishi kerak) ---")
    for p in sorted((ROOT / "samples").glob("*.mp4")):
        t0 = time.time()
        r = evaluation.analyze(str(p))
        bad = [a for a in r["safety"] if a["kind"] in ("fall", "lying")]
        ok(f"{p.name} ({r['duration']:.0f} s): yiqilish/yotish signali yo'q", not bad,
           ", ".join(f"{a['kind']}@{a['t']}" for a in bad) + f" ({time.time() - t0:.0f} s)")
    n = len(results)
    print(f"\nNatija: {sum(results)}/{n} sinov o'tdi  ({time.time() - t_all:.0f} s)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
