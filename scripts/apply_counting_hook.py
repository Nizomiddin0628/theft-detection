"""1.9.0: odam sanashni kamera jarayoniga (app/ai/worker.py) ulash.

Nega alohida skript: worker.py ning asl 1.4.1 nusxasi faqat do'kon kompyuterida. Yangilanish ZIP'i uni
almashtirmaydi (1.4.1 tuzatishlari yo'qolmasin) — shu skript sizdagi faylga kichik qo'shimchalar kiritadi:

  1. `from . import counting`
  2. Processor maydoni `counter`
  3. __post_init__ — LineCounter yaratish (jonli kamera — bazaga yozadi, video tahlili — faqat xotirada)
  4. interval() — sanash chizig'i bor kamerada odam bo'lsa to'liq AI tezligi (izlar uzilmasin)
  5–6. step() — analyzer.update dan keyin counter.update (ikkala yo'lda: jim kadr va to'liq tahlil)
  7. (ixtiyoriy) video fayl qaytadan aylansa — o'tishlar takror sanalmaydi (signallar kabi)

Xavfsiz: 1–6 joylari aynan bir marta topilishi shart, aks holda fayl O'ZGARTIRILMAYDI (7 topilmasa — faqat
ogohlantirish). Natija sintaksis va import bilan tekshiriladi (Processor yaratiladi va sanash chaqiriladi); xato
bo'lsa — eski nusxa qaytariladi. Fayl almashtirish atomar. Qayta ishga tushirish zararsiz (allaqachon ulangan).
Zaxira: data/backups/worker_<vaqt>.py

    python scripts/apply_counting_hook.py            # ulash
    python scripts/apply_counting_hook.py --check    # faqat tekshirish (hech narsa yozilmaydi)
Chiqish kodi: 0 — ulandi yoki allaqachon ulangan, 1 — ulab bo'lmadi (fayl o'zgarmadi).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = "counting.LineCounter("

# (nom, regex — aynan 1 marta topilishi shart, almashtirish funksiyasi[, majburiy=True])
EDITS = [
    ("import",
     re.compile(r"^(?P<line>from \. import [^\n]*\bzones as zones_mod\b[^\n]*)\n", re.M),
     lambda m: m["line"] + "\nfrom . import counting  # 1.9.0: sanash chizig'i\n"),
    ("maydon",
     re.compile(r"^(?P<i>[ \t]+)(?P<line>zones: zones_mod\.ZoneSet = field\([^\n]*\)[^\n]*)\n", re.M),
     lambda m: f"{m['i']}{m['line']}\n{m['i']}counter: \"counting.LineCounter\" = field(default=None)"
               f"  # type: ignore[assignment]   # 1.9.0: sanash chizig'i\n"),
    ("__post_init__",
     re.compile(r"^(?P<i>[ \t]+)if self\.analyzer is None:\n(?P<j>[ \t]+)self\.analyzer = Analyzer\(self\.source_name\)"
                r"[ \t]*\n", re.M),
     lambda m: (m[0] + f"{m['i']}if getattr(self, \"counter\", None) is None:\n"
                f"{m['j']}# 1.9.0: jonli kamera — bazaga yoziladi; video tahlili va aniqlik sinovi (background) — xotirada\n"
                f"{m['j']}self.counter = counting.LineCounter(self.source_id, self.source_name,"
                f" persist=not self.background)\n")),
    ("interval",
     re.compile(r"^(?P<i>[ \t]+)if self\.mode == CALM:\n(?P<j>[ \t]+)(?=return 1\.0 / max\(1\.0, min\(ai_fps, calm_fps\)\))",
                re.M),
     lambda m: (m[0] + f"if counting.lines_from(self.zones):\n{m['j']}    return 1.0 / ai_fps"
                f"  # 1.9.0: sanash chizig'i bor kamera — izlar uzilmasin\n{m['j']}")),
    ("step: jim kadr",
     re.compile(r"^(?P<i>[ \t]+)alerts, status = self\.analyzer\.update\(\[\], \[\], t, w, h[^\n]*\)[ \t]*\n", re.M),
     lambda m: m[0] + f"{m['i']}self.counter.update([], t, w, h, self.zones, status)  # 1.9.0\n"),
    ("step: to'liq tahlil",
     re.compile(r"^(?P<i>[ \t]+)alerts, status = self\.analyzer\.update\(tracked, [^\n]*\)[ \t]*\n", re.M),
     lambda m: m[0] + f"{m['i']}self.counter.update(tracked, t, w, h, self.zones, status)  # 1.9.0\n"),
    ("video qaytadan aylanishi",
     re.compile(r"^(?P<i>[ \t]+)self\.loop_no \+= 1[^\n]*\n", re.M),
     lambda m: (m[0] + f"{m['i']}if getattr(self.proc, \"counter\", None) is not None:\n"
                f"{m['i']}    self.proc.counter.persist = False  # 1.9.0: video qaytadan aylanmoqda — takror sanalmaydi\n"),
     False),
]

CHECK = """import sys
sys.path.insert(0, sys.argv[1])
from app.ai import worker
assert "counter" in worker.Processor.__dataclass_fields__, "counter maydoni yo'q"
p = worker.Processor("ulash-tekshiruvi", source_id=None, background=True)
assert p.counter is not None and p.counter.persist is False, "LineCounter yaratilmadi"
assert p.counter.update([], 1.0, 640, 480, p.zones, {}) == []
assert p.interval(8.0, 4.0) > 0
print("ok")
"""


def patch_text(text: str) -> tuple[str | None, list[str]]:
    """(yangi matn yoki None, xabarlar). None — biror joy topilmadi yoki bir necha marta topildi."""
    msgs = []
    if "def interval(self, ai_fps" not in text:
        return None, ["interval(self, ai_fps, ...) topilmadi — worker.py tuzilishi kutilganidan farq qiladi"]
    out = text
    for name, rx, repl, *opt in EDITS:
        required = opt[0] if opt else True
        found = list(rx.finditer(out))
        if len(found) != 1:
            if required:
                return None, [f"'{name}' joyi {len(found)} marta topildi (1 bo'lishi kerak)"]
            msgs.append(f"!   {name}: joyi topilmadi — o'tkazib yuborildi (sanash ishlaydi; aylanuvchi video faylda"
                        f" o'tishlar har aylanishda qayta sanaladi)")
            continue
        out = rx.sub(repl, out, count=1)
        msgs.append(f"OK  {name}")
    return out, msgs


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Odam sanashni worker.py ga ulash (1.9.0)")
    ap.add_argument("--check", action="store_true", help="faqat tekshirish, yozmaslik")
    ap.add_argument("--file", default=str(ROOT / "app" / "ai" / "worker.py"))
    ap.add_argument("--no-import-check", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    path = Path(a.file)
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")              # Windows Notepad saqlagan fayl
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if MARK in text:
        print("Sanash allaqachon ulangan — hech narsa o'zgartirilmadi.")
        return 0
    new, msgs = patch_text(text)
    for m in msgs:
        print("  " + m)
    if new is None:
        print("ULANMADI: fayl o'zgartirilmadi. app/ai/worker.py ni dasturchiga yuboring.")
        return 1
    try:
        compile(new, str(path), "exec")
    except SyntaxError as e:
        print(f"ULANMADI: natijada sintaksis xatosi ({e}); fayl o'zgartirilmadi.")
        return 1
    if a.check:
        print("Tekshiruv: ulash mumkin (--check — hech narsa yozilmadi).")
        return 0
    root = path.resolve().parent.parent.parent          # <loyiha>/app/ai/worker.py → <loyiha>
    bak_dir = root / "data" / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    bak = bak_dir / f"worker_{time.strftime('%Y%m%d_%H%M%S')}.py"
    shutil.copy2(path, bak)
    data = (new.replace("\n", "\r\n") if crlf else new).encode("utf-8")
    tmp = path.with_name(path.name + ".yangi")
    tmp.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    os.replace(tmp, path)                                # atomar: yarim yozilgan fayl qolmaydi

    def rollback(why: str) -> int:
        shutil.copy2(bak, path)
        print(f"ULANMADI: {why} — eski nusxa qaytarildi (fayl o'zgarmadi).")
        return 1

    if not a.no_import_check:
        try:
            r = subprocess.run([sys.executable, "-c", CHECK, str(root)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
        except (subprocess.TimeoutExpired, OSError) as e:
            return rollback(f"import tekshiruvi tugamadi ({e.__class__.__name__})")
        if r.returncode != 0 or "ok" not in r.stdout:
            return rollback("import tekshiruvi o'tmadi\n" + (r.stderr or r.stdout)[-1500:])
    print(f"Ulandi. Zaxira: {bak}\nDasturni qayta ishga tushiring (Stop/Start-ScheduledTask HushyorAI).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
