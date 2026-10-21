"""Sayt sahifalarini yasash (2.9.0): scripts/site_template.html + scripts/site_text.py → site/index.html (o'zbekcha),
site/ru/index.html, site/en/index.html. Natija repoga commit qilinadi (bulut serveri tayyor fayllarni beradi).

    python scripts/build_site.py            # yozadi
    python scripts/build_site.py --check    # faqat tekshiradi: fayllar shablonga mosmi (sinovda ishlatiladi)

«Qanday ishlaydi» sahnasidagi egri chiziqlar shu yerda hisoblanadi (shablonda qo'lda yozilmaydi).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import site_text  # noqa: E402

PAGES = {"uz": ("", SITE / "index.html"), "ru": ("../", SITE / "ru" / "index.html"),
         "en": ("../", SITE / "en" / "index.html")}
# 3.3.0: bo'limlar (menyu, pastki qism), "Kimlar uchun" belgilari va oldindan yuklanadigan shriftlar
NAV_IDS = ("qanday", "imkoniyatlar", "ekranlar", "pilot", "savollar")
WHO_ICONS = (
    "M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 8H6 M8 20a1.2 1.2 0 1 0 2.4 0a1.2 1.2 0 1 0-2.4 0 "
    "M15.5 20a1.2 1.2 0 1 0 2.4 0a1.2 1.2 0 1 0-2.4 0",
    "M10 5.5a2 2 0 1 1 2 2v1L3.4 14.9a1.2 1.2 0 0 0 .7 2.1h15.8a1.2 1.2 0 0 0 .7-2.1L12 8.5",
    "M9.5 3.5h5v6h6v5h-6v6h-5v-6h-6v-5h6z",
    "M7.5 2.5h9a1 1 0 0 1 1 1v17a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1z M11 18h2",
    "M9.5 3h5v4.5h-5z M12 7.5V11 M5 14v-3h14v3 M3 14h4v6H3z M10 14h4v6h-4z M17 14h4v6h-4z",
)
FONTS = {"uz": ("onest-latin", "unbounded-latin"), "en": ("onest-latin", "unbounded-latin"),
         "ru": ("onest-cyrillic", "unbounded-cyrillic", "onest-latin")}


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _smooth(pts: list[tuple[float, float]]) -> str:
    """Nuqtalardan silliq egri chiziq (Catmull-Rom → kubik Bezye), SVG path."""
    d = f"M{_f(pts[0][0])} {_f(pts[0][1])}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{_f(c1[0])} {_f(c1[1])} {_f(c2[0])} {_f(c2[1])} {_f(p2[0])} {_f(p2[1])}"
    return d


def how() -> dict:
    """3.3.0: «Qanday ishlaydi» sahnasi uchun egri chiziqlar va qadamlar zonasi (do'kon ichida / tashqariga).
    Xato signal ulushi 8 hafta davomida (24% → 7%) va bugungi tashriflar soatlar bo'yicha (09:00 dan hozirgacha)."""
    share = (24, 21, 17, 15, 12, 10, 8, 7)
    learn = [(12 + i * 28, 84 - v * 2.9) for i, v in enumerate(share)]
    visits = (3, 8, 14, 21, 17, 13, 16)                      # 09, 10, ... 14, 14:30
    hours = (0, 1, 2, 3, 4, 5, 5.5)
    vis = [(6 + hx * 24, 104 - v * 4) for hx, v in zip(hours, visits)]
    lp, vp = _smooth(learn), _smooth(vis)
    return {
        "learn": lp, "learn_area": f"{lp} L{_f(learn[-1][0])} 84 L{_f(learn[0][0])} 84 Z",
        "learn_start": tuple(round(c, 1) for c in learn[0]), "learn_end": tuple(round(c, 1) for c in learn[-1]),
        "visits": vp, "visits_area": f"{vp} L{_f(vis[-1][0])} 104 L{_f(vis[0][0])} 104 Z",
        "visits_end": tuple(round(c, 1) for c in vis[-1]),
        "zones": {"cams": "in", "heart": "in", "tg": "out", "back": "out", "panel": "in", "cloud": "out",
                  "report": "out"},
    }


def render(lang: str) -> str:
    import jinja2

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).resolve().parent)),
                             autoescape=True, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
    tpl = env.get_template("site_template.html")
    base = PAGES[lang][0]
    up = "" if lang == "uz" else "../"
    alt = {lg: "./" if lg == lang else up + ("" if lg == "uz" else lg + "/") for lg in site_text.LANGS}
    # 3.3.0: sarlavhalarda tire (—) yangi qator boshida qolmasin — oldidagi so'zga bog'lanadi
    t = {k: (v.replace(" —", "\u00a0—") if isinstance(v, str) and (k.endswith("_h2") or k == "h1") else v)
         for k, v in site_text.T[lang].items()}
    return tpl.render(t=t, lang=lang, base=base, alt=alt, langs=site_text.LANGS, T=site_text.T,
                      how=how(), branch_values=site_text.BRANCH_VALUES, version=version(),
                      nav_ids=NAV_IDS, who_icons=WHO_ICONS, fonts=FONTS[lang])


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
            elif isinstance(base[k], dict):
                # 3.3.0: ichki ro'yxatlar ham bir xil uzunlikda (shablon tartib raqamiga tayanadi: r1–r4, k1–k4, b1–b3)
                errs += [f"{lg}: {k}.{sk} — {len(t[k][sk])} ta (uz: {len(v)})" for sk, v in base[k].items()
                         if isinstance(v, list) and len(v) != len(t[k][sk])]
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
