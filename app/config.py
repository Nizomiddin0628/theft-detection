"""Loyiha sozlamalari: yo'llar, .env qiymatlari va standart parametrlar."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

# scikit-learn (OpenMP) bitta odamning bashorati uchun barcha yadrolarni ishga tushirib, ularni
# "aylantirib" CPU ni band qiladi. Bitta oqim yetarli va kompyuterni qotirmaydi.
os.environ.setdefault("OMP_NUM_THREADS", "1")

BASE_DIR = Path(__file__).resolve().parent.parent
# HUSHYOR_DATA_DIR — boshqa ma'lumot papkasi (masalan, 72 soatlik sinov jonli ma'lumotga tegmasin: tests/soak.py)
DATA_DIR = Path(os.environ.get("HUSHYOR_DATA_DIR") or BASE_DIR / "data")
CLIPS_DIR = DATA_DIR / "clips"
UPLOADS_DIR = DATA_DIR / "uploads"
POSES_DIR = DATA_DIR / "poses"
FEEDBACK_DIR = DATA_DIR / "feedback"   # o'chirilgan, lekin tekshirilgan hodisalarning o'qitish ma'lumoti
LOGS_DIR = DATA_DIR / "logs"
SNAP_DIR = DATA_DIR / "snapshots"      # zona chizish uchun kameralarning oxirgi toza kadri
EVAL_DIR = DATA_DIR / "eval"           # aniqlikni o'lchash uchun belgilangan videolar
MODELS_DIR = BASE_DIR / "models"
DB_PATH = DATA_DIR / "hushyor.db"

for _d in (DATA_DIR, CLIPS_DIR, UPLOADS_DIR, POSES_DIR, FEEDBACK_DIR, LOGS_DIR, MODELS_DIR, SNAP_DIR, EVAL_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv(env_file: Path | None = None) -> None:
    """python-dotenv bo'lmasa ham ishlaydigan oddiy .env o'quvchi.

    utf-8-sig: Notepad "UTF-8 with BOM" bilan saqlasa, birinchi kalit (odatda TELEGRAM_BOT_TOKEN) oldidagi
    ko'rinmas BOM belgisi olib tashlanadi — aks holda token "yo'q" deb o'qilardi.
    """
    env_file = env_file or BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip().lstrip("﻿")
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_BASE = env("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
ADMIN_USER = env("ADMIN_USER", "admin")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "admin123")
HOST = env("HOST", "0.0.0.0")
# 2.6.0: demo rejimi (scripts/demo.py) — namuna ma'lumot, faqat alohida "demo" papkasida (haqiqiy data/ da hech qachon)
DEMO = env("HUSHYOR_DEMO") == "1" and DATA_DIR.name == "demo"
PORT = int(env("PORT", "8000"))
PUBLIC_URL = env("PUBLIC_URL", "").rstrip("/")
# Bulut (markaziy server): bo'sh bo'lsa — hamma narsa faqat shu kompyuterda, yuborilmaydi
CLOUD_URL = env("CLOUD_URL", "").rstrip("/")
EDGE_TOKEN = env("EDGE_TOKEN", "").strip()
# 2.2.0: SMS shlyuzi (app/sms.py) — maxfiy qiymatlar bazada emas, faqat shu yerda
SMS_ESKIZ_EMAIL = env("SMS_ESKIZ_EMAIL", "").strip()
SMS_ESKIZ_PASSWORD = env("SMS_ESKIZ_PASSWORD", "").strip()
SMS_FROM = env("SMS_FROM", "4546").strip() or "4546"
SMS_ESKIZ_BASE = env("SMS_ESKIZ_BASE", "https://notify.eskiz.uz/api").rstrip("/")
SMS_HTTP_URL = env("SMS_HTTP_URL", "").strip()
SMS_HTTP_TOKEN = env("SMS_HTTP_TOKEN", "").strip()


def _secret_key() -> str:
    key = env("SECRET_KEY")
    if key:
        return key
    key_file = DATA_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


SECRET_KEY = _secret_key()

POSE_MODEL = MODELS_DIR / "yolo11n-pose.onnx"
DET_MODEL = MODELS_DIR / "yolo11n.onnx"

# Sezgirlik rejimlari: chegara (0-100) va yashirish harakati davomiyligi (soniya)
SENSITIVITY_PRESETS = {
    "demo": {"threshold": 35, "dwell": 0.30, "label": "Sinov (demo) — eng sezgir"},
    "normal": {"threshold": 60, "dwell": 0.45, "label": "Oddiy — do'kon uchun"},
    "strict": {"threshold": 75, "dwell": 0.60, "label": "Qattiq — kam signal"},
}

DEFAULT_SETTINGS = {
    "sensitivity": "normal",
    "threshold": "60",
    "dwell_sec": "0.45",
    "pre_sec": "10",
    "post_sec": "10",
    "cooldown_sec": "15",
    "ai_fps": "8",
    "infer_size": "640",
    "person_conf": "0.40",
    "object_conf": "0.30",
    "use_ml": "1",
    "ml_weight": "10",
    "personal_items": "1",
    "calm_fps": "4",
    "model_set": "yolo11",         # 2.7.0: ochiq to'plam (open-m) panelda tavsiya — yiqilishda yomonroq (docs/MODELS.md)
    "ai_runtime": "auto",
    "runtime_auto": "",
    "show_objects": "0",
    "signal_pocket": "1",
    "signal_torso": "1",
    "signal_bag": "1",
    "signal_vanish": "1",
    "signal_looking": "1",
    "signal_offscreen": "1",
    "signal_exit": "1",
    "telegram_enabled": "1",
    "telegram_send_video": "1",
    "telegram_min_score": "0",
    "retention_days": "30",
    "camera_alerts": "1",
    "camera_alert_sec": "60",
    "store_name": "Mening do'konim",
    # 2.0.0: kassa navbati qoidalari (app/ai/queues.py)
    "queue_max_len": "4",          # 1-qoida: navbatda shuncha kishi ...
    "queue_long_sec": "30",        # ... shuncha soniya davomida
    "queue_max_wait_min": "3",     # ... yoki kimdir shuncha daqiqa kutmoqda
    "queue_cashier_sec": "45",     # 2-qoida: navbat bor, kassir joyi shuncha soniya bo'sh
    "queue_cooldown_min": "10",    # bitta kassa uchun bir xil signal shuncha daqiqada bir marta
    "queue_tg": "1",               # 1-2-qoida Telegram'ga
    "queue_tg_low": "0",           # 3-qoida ("kassir kerak emas") Telegram'ga — sukut bo'yicha faqat panelda
    "queue_tg_schedule": "1",      # 4-qoida (haftalik kassir jadvali) Telegram'ga
    # 2.2.0: xavfsizlik va salomatlik (app/ai/safety.py)
    "safety_fall": "1",            # yiqilish
    "safety_lying": "1",           # odam yotibdi (≥ safety_lying_sec)
    "safety_night": "1",           # tungi kirish (ish vaqtidan tashqari yoki o'z jadvali)
    "safety_crowd": "1",           # gavjumlik (chegara berilsa)
    "safety_blocked": "1",         # favqulodda chiqish to'silgan (zona chizilsa)
    "safety_fire": "0",            # olov (tajriba) — sukut bo'yicha o'chiq
    "safety_fight": "0",           # 2.3.0: janjal (tajriba) — sukut bo'yicha o'chiq
    # 2.4.0: kunlik va haftalik hisobot (app/reports.py) — Telegram'ga matn + PDF
    "report_daily": "1",           # har kuni report_time da kechagi kun
    "report_weekly": "1",          # har dushanba report_time da o'tgan hafta
    "report_time": "09:00",
    "safety_fall_sens": "normal",  # normal | high
    "safety_lying_sec": "20",
    "safety_night_hours": "",      # o'z jadvali "22:30-07:30" (bo'sh — ish vaqtidan tashqari)
    "safety_night_grace_min": "20",
    "safety_crowd_inside": "0",    # do'kon bo'yicha "hozir ichkarida" chegarasi (0 — o'chiq)
    "safety_crowd_cam": "0",       # bitta kadrdagi odamlar chegarasi (0 — o'chiq)
    "safety_crowd_sec": "60",
    "safety_blocked_min": "10",
    "safety_blocked_frac": "0.35",
    "safety_fire_sec": "10",
    "safety_cooldown_min": "10",
    # 2.2.0: kritik SMS (app/sms.py). Login/parol — faqat .env da (SMS_ESKIZ_EMAIL, SMS_ESKIZ_PASSWORD)
    "sms_enabled": "0",
    "sms_provider": "eskiz",       # eskiz | http
    "sms_phones": "",              # 998901234567, 998911234567
    "sms_kinds": "fall,lying,night,fire",
    "sms_max_per_hour": "6",
}

# Eski versiyadan qolgan sozlamalarni yangilash: {kalit: (eski standart, yangi standart)}.
# Foydalanuvchi o'zi o'zgartirgan qiymatlarga tegilmaydi.
SETTINGS_MIGRATIONS = {
    2: {"pre_sec": ("6", "10"), "post_sec": ("4", "10"), "ml_weight": ("20", "10")},
}
SETTINGS_VERSION = max(SETTINGS_MIGRATIONS)

APP_NAME = "Hushyor AI"
APP_VERSION = "2.8.0"
