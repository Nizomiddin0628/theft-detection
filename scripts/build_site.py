"""Sayt sahifalarini yasash (2.9.0): scripts/site_template.html + scripts/site_text.py → site/index.html (o'zbekcha),
site/ru/index.html, site/en/index.html. Natija repoga commit qilinadi (bulut serveri tayyor fayllarni beradi).

    python scripts/build_site.py            # yozadi
    python scripts/build_site.py --check    # faqat tekshiradi: fayllar shablonga mosmi (sinovda ishlatiladi)

Tomir tizimi sxemasining koordinatalari shu yerda hisoblanadi (shablonda qo'lda hisoblanmaydi).
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import site_text  # noqa: E402

PAGES = {"uz": ("", SITE / "index.html"), "ru": ("../", SITE / "ru" / "index.html"),
         "en": ("../", SITE / "en" / "index.html")}
OUT_KEYS = ("tg", "panel", "cloud", "report", "sms")
ICONS = {
    "cam": "M3 7h12v10H3z M15 10.5l6-3v9l-6-3z",
    "tg": "M3 11.5 21 4l-3.2 16-5.6-4.6-3 3.1v-5.4L17.4 7 7.6 13.2z",
    "panel": "M3 5h18v11H3z M9 20h6 M12 16v4",
    "cloud": "M7 18h10.5a4 4 0 0 0 .4-8A6 6 0 0 0 6.3 11.3 3.4 3.4 0 0 0 7 18z",
    "report": "M6 3h9l4 4v14H6z M15 3v4h4 M9.5 17v-3 M12.5 17v-6 M15.5 17v-4",
    "sms": "M4 5h16v11H9.5L4 20z M8 9.5h8 M8 12.5h5",
}


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def vascular() -> dict:
    """Ikki sxema: gorizontal (kompyuter, 1120×520) va vertikal (telefon, 360×720)."""
    # ---- gorizontal
    hx, hy = 560, 260
    cams = []
    for i, y in enumerate((80, 200, 320, 440)):
        ye = hy + (y - hy) * 0.32
        cams.append({"i": i, "x": 190, "y": y, "d": f"M214,{y} C340,{y} 380,{_f(ye)} 492,{_f(ye)}"})
    outs = []
    for k, y in zip(OUT_KEYS, (70, 165, 260, 355, 450)):
        ys = hy + (y - hy) * 0.32
        outs.append({"k": k, "x": 930, "y": y, "d": f"M628,{_f(ys)} C740,{_f(ys)} 800,{y} 906,{y}"})
    h = {"w": 1120, "h": 520, "hx": hx, "hy": hy, "r": 62, "orbit": 98, "orbit_len": _f(2 * math.pi * 98 - 4),
         "cams": cams, "outs": outs,
         "vein": "M906,52 C800,-6 650,34 590,204", "vein_label": "M590,204 C650,34 800,-6 906,52"}
    # ---- vertikal
    vx, vy = 180, 290
    vcams = []
    for i, x in enumerate((54, 138, 222, 306)):
        xe = vx + (x - vx) * 0.3
        vcams.append({"i": i, "x": x, "y": 56, "d": f"M{x},78 C{x},160 {_f(xe)},160 {_f(xe)},232"})
    vpos = {"tg": (60, 500), "panel": (180, 500), "cloud": (300, 500), "report": (110, 620), "sms": (250, 620)}
    vouts = []
    for k in OUT_KEYS:
        x, y = vpos[k]
        xs = vx + (x - vx) * 0.3
        mid = 420 if y == 500 else 470
        vouts.append({"k": k, "x": x, "y": y, "d": f"M{_f(xs)},350 C{_f(xs)},{mid} {x},{mid + (0 if y == 500 else 50)} {x},{y - 22}"})
    v = {"w": 360, "h": 720, "hx": vx, "hy": vy, "r": 56, "orbit": 86, "orbit_len": _f(2 * math.pi * 86 - 4),
         "cams": vcams, "outs": vouts,
         "vein": "M40,486 C6,420 18,330 122,304"}
    return {"h": h, "v": v, "icons": ICONS}


def render(lang: str) -> str:
    import jinja2

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).resolve().parent)),
                             autoescape=True, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
    tpl = env.get_template("site_template.html")
    base = PAGES[lang][0]
    up = "" if lang == "uz" else "../"
    alt = {lg: "./" if lg == lang else up + ("" if lg == "uz" else lg + "/") for lg in site_text.LANGS}
    return tpl.render(t=site_text.T[lang], lang=lang, base=base, alt=alt, langs=site_text.LANGS, T=site_text.T,
                      vas=vascular(), out_keys=OUT_KEYS, branch_values=site_text.BRANCH_VALUES, version=version())


def version() -> str:
    """Bulut serveri versiyasi (cloud/server.py: VERSION) — style.css/app.js ?v= keshni yangilaydi."""
    import re

    m = re.search(r'^VERSION = "([^"]+)"', (ROOT / "cloud" / "server.py").read_text(encoding="utf-8"), re.M)
    return m.group(1)


def check_keys() -> list[str]:
    """Har uch tilda bir xil kalitlar va bir xil uzunlikdagi ro'yxatlar."""
    errs = []
    base = site_text.T["uz"]
    for lg in site_text.LANGS[1:]:
        t = site_text.T[lg]
        for k in base:
            if k not in t:
                errs.append(f"{lg}: {k} yo'q")
            elif isinstance(base[k], list) and len(base[k]) != len(t[k]):
                errs.append(f"{lg}: {k} — {len(t[k])} ta (uz: {len(base[k])})")
            elif isinstance(base[k], dict) and set(base[k]) != set(t[k]):
                errs.append(f"{lg}: {k} kalitlari farq qiladi")
        errs += [f"{lg}: ortiqcha {k}" for k in t if k not in base]
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="Sayt sahifalarini yasash")
    ap.add_argument("--check", action="store_true", help="faqat tekshirish (fayllar eskirgan bo'lsa — 1)")
    a = ap.parse_args()
    errs = check_keys()
    if errs:
        print("Tarjima kalitlari mos emas:\n  " + "\n  ".join(errs))
        return 1
    stale = []
    for lang, (_, path) in PAGES.items():
        html = render(lang)
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != html:
            stale.append(str(path.relative_to(ROOT)))
            if not a.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(html, encoding="utf-8", newline="\n")
    if a.check:
        if stale:
            print("Eskirgan (python scripts/build_site.py): " + ", ".join(stale))
            return 1
        print("Sayt sahifalari shablonga mos")
        return 0
    print("Yozildi: " + (", ".join(stale) if stale else "o'zgarish yo'q"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
