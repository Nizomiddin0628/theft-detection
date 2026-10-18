"""Sayt rasmlarini (site/img/*.jpg) demodan qayta olish (2.6.0). Panel ko'rinishi o'zgarganda ishlating.

Avval ikkala demo ochiq bo'lsin:
    demo_windows.bat                 (yoki python scripts/demo.py)      → http://127.0.0.1:8090
    .venv\\Scripts\\python -m cloud.demo                                 → http://127.0.0.1:9090
Keyin:
    python scripts/site_shots.py     (kerak: pip install playwright && playwright install chromium)

Rasmlarni kunduzi (ish vaqtida) oling — bugungi raqamlar to'la bo'ladi.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "img"
EDGE = [("panel", "/"), ("hodisalar", "/events"), ("navbat", "/navbat"), ("oqim", "/oqim"), ("hisobot", "/reports")]


def main() -> int:
    ap = argparse.ArgumentParser(description="Sayt rasmlarini demodan olish")
    ap.add_argument("--edge", default="http://127.0.0.1:8090")
    ap.add_argument("--cloud", default="http://127.0.0.1:9090")
    a = ap.parse_args()
    try:
        import cv2
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print("Kerak: pip install playwright opencv-python && playwright install chromium —", e)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="hushyor_shots_"))

    def save(png: Path, name: str, crop_h: int | None = None) -> None:
        im = cv2.imread(str(png))
        if crop_h:
            im = im[:crop_h]
        cv2.imwrite(str(OUT / f"{name}.jpg"), im, [cv2.IMWRITE_JPEG_QUALITY, 80, cv2.IMWRITE_JPEG_PROGRESSIVE, 1])
        print("  ", OUT / f"{name}.jpg")

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ru-RU")
        pg = ctx.new_page()
        pg.goto(a.edge + "/login")
        pg.fill("input[name=username]", "demo")
        pg.fill("input[name=password]", "demo12345")
        pg.click("form.login button")
        pg.wait_for_timeout(800)
        for name, path in EDGE:
            pg.goto(a.edge + path)
            pg.wait_for_timeout(3500)
            pg.screenshot(path=str(tmp / f"{name}.png"))
            save(tmp / f"{name}.png", name)
        for _label, vp, mob, path, name, crop in (
                ("kompyuter", {"width": 1280, "height": 800}, False, "/compare/", "taqqoslash", None),
                ("telefon", {"width": 390, "height": 844}, True, "/", "telefon", 1520)):
            c2 = b.new_context(viewport=vp, locale="ru-RU", is_mobile=mob, has_touch=mob, device_scale_factor=2 if mob else 1)
            p2 = c2.new_page()
            p2.goto(a.cloud + "/login/")
            p2.wait_for_timeout(1200)               # demo serverda login/parol yozilgan
            p2.click("form.login-form button[type=submit]")
            p2.wait_for_timeout(1500)
            p2.goto(a.cloud + path)
            p2.wait_for_timeout(3000)
            p2.screenshot(path=str(tmp / f"{name}.png"))
            save(tmp / f"{name}.png", name, crop)
            c2.close()
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
