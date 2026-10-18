"""Telegram bot: shubhali hodisa haqida darhol xabar, keyin shu xabarga klip qo'shiladi.

Ish tartibi (signal tezligi uchun):
  1. Hodisa aniqlangan zahoti — o'sha kadrning rasmi, sabablar va tugmalar (bir necha soniyada).
  2. Klip tayyor bo'lgach — o'sha xabardagi rasm `editMessageMedia` bilan videoga almashadi.
  3. Qo'riqchi ✅ / ❌ / 👀 bosadi — barcha obunachilardagi xabar yangilanadi.

Har bir yuborish `notify_log` jadvaliga vaqtlari bilan yoziladi — panelda kechikish (p50/p95) ko'rinadi.
Long polling ishlatiladi — tashqi IP yoki webhook kerak emas. Ulanish: web paneldagi 6 xonali kod.
"""
from __future__ import annotations

import hmac
import html
import itertools
import json
import queue
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, wait
from pathlib import Path

import httpx

from . import db
from .config import CLIPS_DIR, PUBLIC_URL, TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN
from .logutil import get_logger
from .ai.safety import HEADLINES as SAFETY_HEADLINES, KIND_LABELS as SAFETY_LABELS, SAFETY_KINDS

log = get_logger("telegram")

KIND_UZ = {"pocket": "Cho'ntakka solish", "torso": "Kiyim ichiga yashirish", "bag": "Sumkaga solish",
           "offscreen": "Qo'l kadrdan yashirildi", "exit": "Chiqish tomon ketmoqda", **SAFETY_LABELS}
STATUS_UZ = {"yangi": "🟡 Tekshirilmagan", "tasdiqlangan": "✅ Tasdiqlangan",
             "rad_etilgan": "❌ Xato signal"}

# Navbat ustuvorligi: kichik raqam — birinchi
PRIO_ALERT, PRIO_CLIP, PRIO_REFRESH, PRIO_TEXT = 0, 1, 2, 3
# Bitta chatga ketma-ket so'rovlar orasidagi eng kam vaqt (Telegram: shaxsiy chat ~1/s, guruh 20/daqiqa)
MIN_GAP_PRIVATE = 0.05
MIN_GAP_GROUP = 1.0
VIDEO_LIMIT = 49_000_000     # Bot API orqali video 50 MB gacha
PHOTO_LIMIT = 9_500_000      # rasm 10 MB gacha
WORKERS = 6                  # navbatdan o'zi oladigan bajaruvchilar (ustuvorlik haqiqatan ishlaydi)
CLIP_RETRY_SEC = 15.0        # klip tarmoq xatosi bilan yetmasa — shuncha soniyadan keyin qayta
CLIP_RETRIES = 2
# 2.3.1: signal xabari (rasm) Telegram ishlamay turgan paytda yetmasa — keyinroq qayta (avval yo'qolib ketardi).
# Jami ~7,5 daqiqa; hodisa ALERT_MAX_AGE dan eski bo'lsa endi yuborilmaydi (kech xabar chalg'itadi)
ALERT_RETRY = (10.0, 30.0, 60.0, 120.0, 240.0)
ALERT_MAX_AGE = 900.0
TRANSIENT = (0, 429, 500, 502, 503, 504)


def _transient(res) -> bool:
    """Tarmoq yoki Telegram serveri xatosi (keyinroq o'tishi mumkin). 400/403 (bloklangan, noto'g'ri so'rov) — yo'q."""
    return not res or (not res.get("ok") and int(res.get("error_code") or 0) in TRANSIENT)
CODE_TRIES = 5               # ulanish kodi: bitta chatdan 10 daqiqada shuncha xato urinish — blok
CODE_ROTATE_AFTER = 30       # hamma chatlardan 10 daqiqada shuncha xato — kod avtomatik almashadi

HELP = (
    "🛡 <b>Hushyor AI — do'kon qo'riqchisi boti</b>\n\n"
    "Kamerada shubhali harakat aniqlansa, darhol shu yerga rasm va sabablar keladi, bir necha soniyadan "
    "keyin o'sha xabarga video klip qo'shiladi. Videoni ko'rib <b>✅ Tasdiqlash</b> yoki "
    "<b>❌ Xato signal</b> tugmasini bosing. <b>👀 Ko'ryapman</b> — boshqalarga siz shug'ullanayotganingizni "
    "bildiradi.\n\n"
    "Buyruqlar:\n"
    "/holat — kameralar va bugungi statistika\n"
    "/oxirgi — oxirgi 5 ta hodisa\n"
    "/tekshirilmagan — javob kutayotgan hodisalar\n"
    "/navbat — kassalardagi navbat hozir (navbat signallarini o'chirish/yoqish ham shu yerda)\n"
    "/oqim — bugun qaysi bo'limlarga ko'p kirildi va yo'nalishlar\n"
    "/xavfsizlik — bugungi xavfsizlik signallari (yiqilish, tungi kirish, chiqish to'silgan...)\n"
    "/hisobot — bugungi holat; kechagi va o'tgan haftaning to'liq hisoboti (PDF). Har kuni ertalab o'zi keladi\n"
    "/stop — xabarlarni to'xtatish\n"
    "/yordam — shu yordam\n\n"
    "⚠️ Bot hech kimni ayblamaydi: u faqat \"tekshirib ko'ring\" deydi. Qaror har doim odamniki."
)


class DaemonPool:
    """Oddiy oqimlar havzasi (daemon). concurrent.futures.ThreadPoolExecutor dastur yopilishida barcha ishlarini
    kutadi (atexit) — 2 daqiqalik video yuklash dasturni to'xtatmay qo'yardi. Bu havza kutmaydi."""

    def __init__(self, workers: int, name: str):
        self._q: "queue.Queue" = queue.Queue()
        self._stop = False
        self._threads = [threading.Thread(target=self._run, daemon=True, name=f"{name}-{i}") for i in range(workers)]
        for t in self._threads:
            t.start()

    def submit(self, fn, *args) -> Future:
        fut: Future = Future()
        if self._stop:
            fut.cancel()
            return fut
        self._q.put((fn, args, fut))
        return fut

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, args, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        self._stop = True
        if cancel_futures:
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    item[2].cancel()
        for _ in self._threads:
            self._q.put(None)


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def _small_jpeg(path: Path) -> bytes | None:
    """Telegram video eskizi: JPEG, 320 px dan oshmasin, 200 KB dan kichik."""
    try:
        import cv2

        img = cv2.imread(str(path))
        if img is None:
            return None
        h, w = img.shape[:2]
        s = 320 / max(h, w)
        if s < 1:
            img = cv2.resize(img, (int(w * s), int(h * s)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def fmt_time(ts: float | None) -> str:
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ts)) if ts else "—"


def susp_info(ev: dict) -> dict:
    """Shubhali lahza: qachon bo'lgani va klipning nechanchi soniyasida ekani."""
    s0 = ev.get("susp_start")
    live = ev.get("job_id") is None
    label, offset = None, None
    if s0 is not None:
        if live:
            label = time.strftime("%H:%M:%S", time.localtime(s0))
        else:
            m, sec = divmod(max(float(s0), 0.0), 60)
            label = f"videoning {int(m)}:{sec:04.1f} soniyasi"
        if ev.get("clip_start") is not None:
            offset = round(max(float(s0) - float(ev["clip_start"]), 0.0), 1)
    dur = None
    if s0 is not None and ev.get("susp_end") is not None:
        dur = round(max(float(ev["susp_end"]) - float(s0), 0.0), 1)
    return {"susp_label": label, "susp_offset": offset, "susp_dur": dur}


def event_time(ev: dict) -> float:
    """Hodisa aniqlangan devor vaqti (kechikish shu vaqtdan o'lchanadi)."""
    return float(ev.get("t_detect") or ev.get("created_at") or time.time())


class TelegramBot:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, api_base: str = TELEGRAM_API_BASE):
        self.token = token.strip()
        self.api_base = api_base.rstrip("/")
        self.me: dict | None = None
        self.status = "o'chirilgan" if not self.token else "ulanmoqda"
        self.last_error = ""
        self.last_ok = 0.0
        self.sent = 0
        self.stop_evt = threading.Event()
        self.q: "queue.PriorityQueue[tuple]" = queue.PriorityQueue()
        self._seq = itertools.count()
        # Doimiy ulanishlar (keep-alive): har xabar uchun qaytadan TLS ulanish ochilmaydi
        self.client = httpx.Client(timeout=httpx.Timeout(40.0, connect=10.0),
                                   limits=httpx.Limits(max_connections=32, max_keepalive_connections=16))
        # Vazifalar (hodisa, klip, yangilash) — WORKERS ta bajaruvchi oqim navbatdan O'ZI oladi: hammasi katta
        # video yuklayotgan bo'lsa ham, bo'shagan birinchi oqim eng ustuvor vazifani (yangi signal rasmi) oladi.
        # (Avval navbat darhol pool'ga to'kilardi va pool o'z FIFO tartibida bajarardi — ustuvorlik yo'qolardi.)
        # Har chatga yuborish — alohida io_pool; tugma bosilishi — o'z oqimida (yuklashlar uni kutdirmaydi).
        self.io_pool = DaemonPool(16, "tg-io")
        self.cb_pool = DaemonPool(2, "tg-cb")
        self._code_fails: dict[int, list[float]] = defaultdict(list)
        self._code_fails_all: list[float] = []
        self._chat_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        # Hodisa bo'yicha qulf (signal qayta urinishi va klip bosqichi bir-birini takrorlamasin): doimiy 64 ta qulf
        self._ev_locks = [threading.Lock() for _ in range(64)]
        self._timers: set = set()
        self._chat_next: dict[int, float] = {}
        self._alert_done: dict[int, threading.Event] = {}
        self._kb_done: set[int] = set()   # eski pastki tugmalar (reply keyboard) tozalangan chatlar
        self._menus_ok = True             # menyu tozalash muvaffaqiyatli tugaganmi (aks holda qayta uriniladi)
        self._menus_t = 0.0
        self._threads: list[threading.Thread] = []
        self.manager = None

    # ------------------------------------------------------------------ API
    def api(self, method: str, data: dict | None = None, files: dict | None = None,
            timeout: float = 30.0, retries: int = 2, chat_id: int | None = None) -> dict:
        """Bot API chaqiruvi. chat_id berilsa — shu chat uchun navbat va tezlik cheklovi qo'llanadi."""
        if chat_id is None:
            return self._call(method, data, files, timeout, retries)
        # Qulf faqat navbatdagi vaqt oralig'ini band qilish uchun: so'rovning o'zi qulfsiz ketadi,
        # shuning uchun katta video yuklanayotganda ham shu chatga yangi signal rasmi kutib qolmaydi
        cid = int(chat_id)
        gap = MIN_GAP_GROUP if cid < 0 else MIN_GAP_PRIVATE
        with self._chat_locks[cid]:
            start = max(time.time(), self._chat_next.get(cid, 0.0))
            self._chat_next[cid] = start + gap
        wait_s = start - time.time()
        if wait_s > 0:
            time.sleep(wait_s)
        return self._call(method, data, files, timeout, retries)

    def _call(self, method: str, data: dict | None, files: dict | None, timeout: float, retries: int) -> dict:
        url = f"{self.api_base}/bot{self.token}/{method}"
        attempt, waits_429 = 0, 0
        while True:
            try:
                if files:
                    for f in files.values():   # qayta urinishda fayl boshidan o'qilsin
                        if len(f) > 1 and hasattr(f[1], "seek"):
                            f[1].seek(0)
                    r = self.client.post(url, data=data or {}, files=files, timeout=timeout)
                else:
                    r = self.client.post(url, json=data or {}, timeout=timeout)
                res = r.json()
            except (httpx.HTTPError, ValueError) as e:
                self.last_error = f"Tarmoq xatosi: {e.__class__.__name__}"
                if attempt < retries:
                    attempt += 1
                    time.sleep(0.8 * attempt)
                    continue
                raise
            if res.get("ok"):
                self.last_ok = time.time()
                return res
            if r.status_code == 429 and waits_429 < 3:
                waits_429 += 1
                wait_s = float(res.get("parameters", {}).get("retry_after", 2))
                time.sleep(min(wait_s, 30))
                continue
            self.last_error = res.get("description", "noma'lum xato")
            if r.status_code in (400, 401, 403, 404) or attempt >= retries:
                return res
            attempt += 1
            time.sleep(0.8)

    # ---------------------------------------------------------------- start
    def start(self) -> None:
        if not self.token:
            self.status = "o'chirilgan"
            log.warning("TELEGRAM_BOT_TOKEN berilmagan — bot ishlamaydi")
            return
        self._threads = [threading.Thread(target=self._poll_loop, daemon=True, name="tg-poll")]
        self._threads += [threading.Thread(target=self._worker_loop, daemon=True, name=f"tg-send-{i}")
                          for i in range(WORKERS)]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        """Toza to'xtash: navbat kutilmaydi, davom etayotgan yuklashlar (2 daqiqalik video ham) uziladi."""
        self.stop_evt.set()
        for tm in list(self._timers):
            tm.cancel()
        for p in (self.io_pool, self.cb_pool):
            p.shutdown(wait=False, cancel_futures=True)
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

    def _init(self) -> bool:
        try:
            r = self.api("getMe", timeout=15)
        except httpx.HTTPError:
            self.status = "tarmoq xatosi"
            return False
        if not r.get("ok"):
            self.status = "token xato"
            log.error("Telegram token xato: %s", r.get("description"))
            return False
        self.me = r["result"]
        self.status = "ishlayapti"
        try:
            self.api("deleteWebhook", {"drop_pending_updates": False})
        except httpx.HTTPError:
            pass
        threading.Thread(target=self._safe, args=(self._reset_menus,), daemon=True, name="tg-menu").start()
        log.info("Telegram bot ulandi: @%s", self.me.get("username"))
        return True

    COMMANDS = [
        {"command": "holat", "description": "Kameralar va bugungi statistika"},
        {"command": "oxirgi", "description": "Oxirgi 5 ta hodisa"},
        {"command": "tekshirilmagan", "description": "Javob kutayotgan hodisalar"},
        {"command": "navbat", "description": "Kassalardagi navbat"},
        {"command": "oqim", "description": "Bugungi bo'lim tashriflari"},
        {"command": "hisobot", "description": "Bugungi holat va hisobotlar (PDF)"},
        {"command": "stop", "description": "Xabarlarni to'xtatish"},
        {"command": "yordam", "description": "Yordam"},
    ]

    def _try(self, method: str, data: dict, **kw) -> dict:
        """Menyu sozlash chaqiruvi: tarmoq xatosi butun jarayonni to'xtatmasin."""
        try:
            return self.api(method, data, **kw)
        except httpx.HTTPError as e:
            log.warning("Telegram %s: tarmoq xatosi (%s)", method, e)
            return {"ok": False}

    def _reset_menus(self) -> None:
        """Shu token avval boshqa botda ishlatilgan bo'lsa, undan qolgan menyularni tozalash.

        * boshqa til va doiralar uchun qo'yilgan eski buyruqlar o'chiriladi, faqat bizniki qoladi;
        * chap pastdagi menyu tugmasi oddiy "buyruqlar" menyusiga qaytariladi;
        * obunachilardagi eski pastki tugmalar (reply keyboard) bir martalik xabar bilan olib tashlanadi.
        Biror qadam muvaffaqiyatsiz bo'lsa, 5 daqiqadan keyin qayta uriniladi (_poll_loop).
        """
        self._menus_t = time.time()
        self._kb_done |= {int(r["chat_id"]) for r in
                          db.rows("SELECT chat_id FROM subscribers WHERE COALESCE(kb_cleared, 0)=1")}
        scopes = [{"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"},
                  {"type": "all_chat_administrators"}]
        try:
            for scope in scopes:
                for lang in ("", "uz", "ru", "en"):
                    data = {"scope": scope}
                    if lang:
                        data["language_code"] = lang
                    self._try("deleteMyCommands", data, retries=0)
        finally:
            # O'chirish chala qolsa ham buyruqlar albatta qayta o'rnatiladi (menyu bo'sh qolmasin)
            ok = self._try("setMyCommands", {"commands": self.COMMANDS}).get("ok", False)
            ok = self._try("setChatMenuButton", {"menu_button": {"type": "commands"}}).get("ok", False) and ok
        for s in db.rows("SELECT chat_id FROM subscribers WHERE active=1 AND COALESCE(kb_cleared, 0)=0"):
            try:
                self._clear_keyboard(s["chat_id"])
            except Exception:  # noqa: BLE001 — bitta chat xatosi qolganlarini to'xtatmasin
                log.warning("Chat %s menyusini tozalab bo'lmadi", s["chat_id"])
                ok = False
        self._menus_ok = ok

    def _clear_keyboard(self, chat_id: int) -> None:
        # Eski bot shu chat uchun alohida menyu tugmasi (masalan, veb-ilova) qo'ygan bo'lishi mumkin
        self._try("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "commands"}}, retries=0)
        res = self.api("sendMessage", {"chat_id": chat_id, "text": "🔧 Bot menyusi yangilandi: eski pastki tugmalar "
                                                                     "olib tashlandi. Buyruqlar — chap pastdagi ☰ menyuda.",
                                       "reply_markup": {"remove_keyboard": True}}, chat_id=chat_id)
        if res.get("ok"):
            self._kb_done.add(int(chat_id))
            db.execute("UPDATE subscribers SET kb_cleared=1 WHERE chat_id=?", (chat_id,))

    def _poll_loop(self) -> None:
        offset = 0
        backoff = 2.0
        while not self.stop_evt.is_set():
            if self.me is None and not self._init():
                if self.stop_evt.wait(min(backoff, 60)):
                    break
                backoff *= 1.6
                continue
            backoff = 2.0
            try:
                r = self.api("getUpdates", {"offset": offset, "timeout": 25,
                                            "allowed_updates": ["message", "callback_query"]}, timeout=35, retries=0)
            except httpx.HTTPError:
                self.status = "tarmoq xatosi"
                self.stop_evt.wait(3)
                continue
            if not r.get("ok"):
                desc = r.get("description", "")
                if "Conflict" in desc:
                    self.status = "boshqa joyda ishlayapti"
                    self.last_error = "Bu token bilan boshqa dastur ham ishlayapti (Conflict)"
                self.stop_evt.wait(3)
                continue
            self.status = "ishlayapti"
            if not self._menus_ok and time.time() - self._menus_t > 300:
                self._menus_t = time.time()
                threading.Thread(target=self._safe, args=(self._reset_menus,), daemon=True, name="tg-menu").start()
            for upd in r.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                try:
                    if "message" in upd:
                        self._on_message(upd["message"])
                    elif "callback_query" in upd:
                        # Tugma bosilishi alohida oqimda: video yuklashlar ham, sekin tarmoq ham uni kutdirmaydi
                        self.cb_pool.submit(self._safe, self._on_callback, upd["callback_query"])
                except Exception:
                    log.exception("Telegram yangilanishini qayta ishlashda xato")

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            log.exception("Telegram vazifasida xato")
            return None

    # ------------------------------------------------------------- handlers
    def _reply(self, chat_id: int, text: str, markup: dict | None = None) -> dict:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            data["reply_markup"] = markup
        elif int(chat_id) not in self._kb_done:
            # Shu chatga birinchi oddiy javobda eski botdan qolgan pastki tugmalar olib tashlanadi
            data["reply_markup"] = {"remove_keyboard": True}
        res = self.api("sendMessage", data, chat_id=chat_id)
        if res.get("ok") and not markup and int(chat_id) not in self._kb_done:
            self._kb_done.add(int(chat_id))
            db.execute("UPDATE subscribers SET kb_cleared=1 WHERE chat_id=?", (chat_id,))
        return res

    def _is_sub(self, chat_id: int) -> bool:
        return bool(db.scalar("SELECT 1 FROM subscribers WHERE chat_id=? AND active=1", (chat_id,)))

    def _subscribe(self, msg: dict) -> None:
        chat = msg["chat"]
        user = msg.get("from", {})
        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or chat.get("title", "")
        db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active) VALUES(?,?,?,?,1) "
                   "ON CONFLICT(chat_id) DO UPDATE SET active=1, name=excluded.name, username=excluded.username",
                   (chat["id"], name, user.get("username", ""), time.time()))
        store = db.get_setting("store_name", "")
        self._reply(chat["id"], f"✅ Ulandingiz! Endi <b>{esc(store)}</b> kameralaridagi shubhali "
                                f"hodisalar shu yerga keladi.\n\n" + HELP)
        log.info("Yangi obunachi: %s (%s)", name, chat["id"])

    def _on_message(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        code = db.get_setting("connect_code", "")
        parts = text.split()
        cmd = parts[0].split("@")[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        guess = arg if cmd in ("/start", "/ulash") and arg else (text if text.isdigit() and len(text) == 6 else "")
        if guess and not self._is_sub(chat_id):
            if self._code_blocked(chat_id):
                self._reply(chat_id, "⛔ Ko'p noto'g'ri urinish. 10 daqiqadan keyin qayta urining.")
                return
            if hmac.compare_digest(guess.encode(), code.encode()):
                self._code_fails.pop(chat_id, None)
                self._subscribe(msg)
            else:
                self._code_fail(chat_id)
                self._reply(chat_id, "❌ Kod noto'g'ri. Kodni web paneldagi <b>Telegram</b> bo'limidan oling.")
            return
        if guess and guess == code:
            self._subscribe(msg)
            return
        if not self._is_sub(chat_id):
            self._reply(chat_id, "👋 Assalomu alaykum! Bu do'kon qo'riqlash boti.\n\n"
                                 "Ulanish uchun web paneldagi <b>Telegram</b> bo'limida ko'rsatilgan "
                                 "6 xonali kodni shu yerga yuboring.")
            return
        if cmd in ("/start", "/yordam", "/help"):
            self._reply(chat_id, HELP)
        elif cmd == "/stop":
            db.execute("UPDATE subscribers SET active=0 WHERE chat_id=?", (chat_id,))
            self._reply(chat_id, "🔕 Xabarlar to'xtatildi. Qayta ulanish uchun kodni yuboring.")
        elif cmd == "/holat":
            self._reply(chat_id, self._status_text())
        elif cmd == "/oxirgi":
            self._reply(chat_id, self._last_text("SELECT * FROM events ORDER BY id DESC LIMIT 5",
                                                 "🕘 <b>Oxirgi hodisalar</b>"))
        elif cmd == "/tekshirilmagan":
            self._reply(chat_id, self._last_text(
                "SELECT * FROM events WHERE status='yangi' ORDER BY id DESC LIMIT 10",
                "🟡 <b>Javob kutayotgan hodisalar</b>"))
        elif cmd == "/navbat":
            from .ai import queues

            self._reply(chat_id, queues.status_text(), self._queue_markup(chat_id))
        elif cmd == "/oqim":
            from .ai import flow

            self._reply(chat_id, flow.status_text())
        elif cmd == "/xavfsizlik":
            from .ai import safety

            self._reply(chat_id, safety.status_text())
        elif cmd == "/hisobot":
            from . import reports

            on = db.scalar("SELECT COALESCE(reports, 1) FROM subscribers WHERE chat_id=?", (chat_id,))
            kb = {"inline_keyboard": [
                [{"text": "📄 Kechagi hisobot (PDF)", "callback_data": "rp:daily"},
                 {"text": "📄 O'tgan hafta", "callback_data": "rp:weekly"}],
                [{"text": "🔕 Har kungi hisobotni o'chirish" if on else "🔔 Har kungi hisobotni yoqish",
                  "callback_data": "rp:off" if on else "rp:on"}]]}
            self._reply(chat_id, reports.today_text(), kb)
        else:
            self._reply(chat_id, "Buyruqni tushunmadim. /yordam ni bosing.")

    def _code_blocked(self, chat_id: int) -> bool:
        now = time.time()
        f = [t for t in self._code_fails.get(chat_id, []) if now - t < 600]
        self._code_fails[chat_id] = f
        return len(f) >= CODE_TRIES

    def _code_fail(self, chat_id: int) -> None:
        """Ulanish kodini tavakkal qilish: chat bo'yicha blok; ko'p chatdan urinish bo'lsa kod o'zi almashadi."""
        now = time.time()
        self._code_fails[chat_id].append(now)
        self._code_fails_all = [t for t in self._code_fails_all if now - t < 600] + [now]
        if len(self._code_fails) > 2000:
            for k in [k for k, v in self._code_fails.items() if not v or now - v[-1] > 600]:
                self._code_fails.pop(k, None)
        if len(self._code_fails_all) >= CODE_ROTATE_AFTER:
            import secrets

            db.set_settings({"connect_code": f"{secrets.randbelow(900000) + 100000}"})
            self._code_fails_all = []
            log.warning("Telegram ulanish kodi ko'p marta noto'g'ri terildi — kod avtomatik almashtirildi")

    def _status_text(self) -> str:
        day0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        tot = db.scalar("SELECT COUNT(*) FROM events WHERE created_at>=?", (day0,)) or 0
        ok = db.scalar("SELECT COUNT(*) FROM events WHERE created_at>=? AND status='tasdiqlangan'", (day0,)) or 0
        no = db.scalar("SELECT COUNT(*) FROM events WHERE created_at>=? AND status='rad_etilgan'", (day0,)) or 0
        lines = [f"📊 <b>Bugun</b>: {tot} ta hodisa (✅ {ok}, ❌ {no}, 🟡 {tot - ok - no})", "", "📷 <b>Kameralar</b>:"]
        srcs = self.manager.source_info() if self.manager else []
        if not srcs:
            lines.append("— hali kamera qo'shilmagan")
        for s in srcs:
            icon = "🟢" if s.get("running") else "⚪️"
            extra = f" · {s['persons']} odam · AI {s['fps_ai']} kadr/s" if s.get("running") else ""
            lines.append(f"{icon} {esc(s['name'])} — {esc(s['state'])}{extra}")
        lat = latency_stats(hours=24)
        a = lat.get("telegram/alert")
        if a and a["n"]:
            lines += ["", f"⏱ Signal tezligi (24 soat): o'rtacha {a['p50']:.1f} s, 95% holatda ≤ {a['p95']:.1f} s"]
        return "\n".join(lines)

    def _last_text(self, sql: str, title: str) -> str:
        evs = db.rows(sql)
        if not evs:
            return title + "\n\nHozircha hodisa yo'q."
        lines = [title, ""]
        for e in evs:
            lines.append(f"#{e['id']} · {fmt_time(e['created_at'])} · {esc(KIND_UZ.get(e['kind'], e['kind']))} · "
                         + ("" if e.get("kind") in SAFETY_KINDS else f"ball {e['score']:.0f} · ")
                         + f"{STATUS_UZ.get(e['status'], e['status'])}")
        return "\n".join(lines)

    def _on_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        user = cb.get("from", {})
        who = user.get("first_name") or user.get("username") or str(user.get("id"))
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        def answer(text: str = "", alert: bool = False):
            payload = {"callback_query_id": cb["id"]}
            if text:
                payload["text"] = text
                payload["show_alert"] = alert
            self.api("answerCallbackQuery", payload)

        if not chat_id or not self._is_sub(chat_id):
            answer("Ruxsat yo'q")
            return
        if data.startswith("rf:") or data.startswith("rp:"):
            self._report_callback(data, chat_id, who, answer, cb)
            return
        if data in ("qa:on", "qa:off"):
            # 2.0.0: navbat signallari shu chat uchun (boshqa obunachilarga ta'sir qilmaydi)
            on = data == "qa:on"
            db.execute("UPDATE subscribers SET queue_alerts=? WHERE chat_id=?", (int(on), chat_id))
            answer("🔔 Navbat signallari yoqildi" if on else "🔕 Navbat signallari o'chirildi (hodisalar kelaveradi)")
            mid = (cb.get("message") or {}).get("message_id")
            if mid:
                self.api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": mid,
                                                    "reply_markup": self._queue_markup(chat_id)}, chat_id=chat_id)
            log.info("Navbat signallari %s: %s", "yoqildi" if on else "o'chirildi", who)
            return
        try:
            action, ev_id = data.split(":")
            ev_id = int(ev_id)
        except ValueError:
            answer()
            return
        ev = db.get_event(ev_id)
        if not ev:
            answer("Hodisa topilmadi")
            return
        if action in ("ok", "no"):
            status = "tasdiqlangan" if action == "ok" else "rad_etilgan"
            db.review_event(ev_id, status, f"Telegram: {who}")
            answer("✅ Tasdiqlandi. Rahmat!" if status == "tasdiqlangan" else "❌ Xato signal deb belgilandi. Rahmat!")
            log.info("Hodisa #%d: %s (%s)", ev_id, status, who)
        elif action == "see":
            if ev.get("claimed_by") and ev["claimed_by"] != who:
                answer(f"{ev['claimed_by']} allaqachon ko'ryapti")
                return
            db.update_event(ev_id, claimed_by=who, claimed_at=time.time())
            answer("👀 Boshqalarga siz ko'rayotganingiz ko'rsatildi")
        elif action == "undo":
            db.update_event(ev_id, status="yangi", reviewed_by=None, reviewed_at=None)
            answer("↩️ Qaror bekor qilindi — qaytadan tanlang")
            log.info("Hodisa #%d: qaror bekor qilindi (%s)", ev_id, who)
        else:
            answer()
            return
        self.refresh_event(ev_id)
        if self.manager is not None:
            self.manager.emit("update", ev_id)

    def _report_callback(self, data: str, chat_id: int, who: str, answer, cb: dict) -> None:
        """2.4.0: hisobot tugmalari — rf:<hisobot>:<tavsiya xeshi>:<1|0> (baho), rp:daily|weekly (PDF), rp:on|off."""
        from . import reports

        parts = data.split(":")
        if parts[0] == "rf" and len(parts) == 4:
            try:
                rid, ref, val = int(parts[1]), parts[2], parts[3] == "1"
                ref = int(ref) if ref.isdigit() and len(ref) <= 2 else ref     # eski tugma: tartib raqami (xesh — 10 belgi)
            except ValueError:
                answer()
                return
            rec = reports.feedback(rid, ref, val, f"tg:{chat_id}:{who}")
            answer(("👍 Rahmat — foydali deb belgilandi" if val else "👎 Rahmat — bunday tavsiyalar kamaytiriladi")
                   if rec else "Hisobot topilmadi")
            return
        if parts[0] == "rp" and len(parts) == 2 and parts[1] in ("on", "off"):
            on = parts[1] == "on"
            db.execute("UPDATE subscribers SET reports=? WHERE chat_id=?", (int(on), chat_id))
            answer("🔔 Har kungi hisobot yoqildi" if on else "🔕 Har kungi hisobot o'chirildi (signallar kelaveradi)")
            return
        if parts[0] == "rp" and len(parts) == 2 and parts[1] in reports.KINDS:
            answer("⏳ Hisobot tayyorlanmoqda...")
            kind = parts[1]

            def work():
                t0, t1 = reports.period(kind)
                r = db.row("SELECT id FROM reports WHERE kind=? AND t0=?", (kind, t0))
                rid = r["id"] if r else reports.generate(kind, t0, t1)
                reports.send(rid, self, [chat_id], mark=False)
            self.io_pool.submit(self._safe, work)
            return
        answer()

    # --------------------------------------------------------------- matnlar
    def _caption(self, ev: dict, clip_pending: bool = False) -> str:
        store = db.get_setting("store_name", "")
        when = fmt_time(ev["created_at"])
        if ev.get("video_time") is not None:
            when += f" (videoda {ev['video_time']:.1f}-soniya)"
        si = susp_info(ev)
        susp_line = None
        if si["susp_label"]:
            susp_line = f"⏱ Shubhali lahza: <b>{esc(si['susp_label'])}</b>"
            if si["susp_offset"] is not None:
                susp_line += f" — klipning <b>{si['susp_offset']:.0f}-soniyasida</b> (qizil belgi)"
        reasons = ev.get("reasons") or []
        safe = ev.get("kind") in SAFETY_KINDS       # 2.2.0: xavfsizlik signali — ayblov emas, yordam/chora kerak
        if safe:
            susp_line = None
            if si["susp_offset"] is not None:
                susp_line = f"⏱ Klipning <b>{si['susp_offset']:.0f}-soniyasida</b> (qizil belgi)"
        lines = [
            f"<b>{esc(SAFETY_HEADLINES[ev['kind']])}</b>" if safe else "⚠️ <b>Shubhali harakat — tekshirib ko'ring</b>",
            f"🏪 {esc(store)}",
            f"📷 Kamera: {esc(ev.get('source_name'))}",
            f"🕒 {esc(when)}",
            *([susp_line] if susp_line else []),
            f"🎯 Turi: <b>{esc(KIND_UZ.get(ev.get('kind'), ev.get('kind')))}</b>",
            *([] if safe else [f"📊 Ball: <b>{ev['score']:.0f}/100</b> ({esc(ev.get('level'))})"]),
            "",
            "<b>Nima bo'ldi:</b>" if safe else "<b>Sabablar:</b>",
        ]
        for r in reasons[:6]:
            lines.append(f"• {esc(r)}")
        lines += ["", f"🆔 Hodisa #{ev['id']}"]
        if clip_pending and ev.get("clip_error"):
            lines.append("⚠️ <i>Video klip yaratilmadi — rasm va sabablarga qarab tekshiring</i>")
        elif clip_pending:
            lines.append("⏳ <i>Video klip tayyorlanmoqda — bir necha soniyada shu xabarga qo'shiladi</i>")
        if safe:
            lines.append("<i>✅ — haqiqatan shunday, ❌ — xato signal (javobingiz aniqlikni o'lchash uchun kerak).</i>")
        else:
            lines.append("<i>Bu ayblov emas — videoni ko'rib, o'zingiz qaror qiling.</i>")
        if ev.get("status") == "yangi" and ev.get("claimed_by"):
            lines += ["", f"👀 <b>Ko'ryapti:</b> {esc(ev['claimed_by'])}, {fmt_time(ev.get('claimed_at'))[-8:]}"]
        if ev.get("status") and ev["status"] != "yangi":
            mark = "✅ TASDIQLANDI" if ev["status"] == "tasdiqlangan" else "❌ XATO SIGNAL"
            lines += ["", f"<b>{mark}</b> — {esc(ev.get('reviewed_by'))}, {fmt_time(ev.get('reviewed_at'))}"]
        cap = "\n".join(lines)
        if len(cap) > 1024:
            # HTML teglarni buzmaslik uchun sabablar qisqartiriladi, matn o'rtasidan kesilmaydi
            lines = [ln for ln in lines if not ln.startswith("• ")][:]
            cap = "\n".join(lines)[:1024]
        return cap

    def _markup(self, ev: dict) -> dict:
        rows = []
        if ev.get("status", "yangi") == "yangi":
            rows.append([{"text": "✅ Tasdiqlash", "callback_data": f"ok:{ev['id']}"},
                         {"text": "❌ Xato signal", "callback_data": f"no:{ev['id']}"}])
            if not ev.get("claimed_by"):
                rows.append([{"text": "👀 Ko'ryapman", "callback_data": f"see:{ev['id']}"}])
        else:
            rows.append([{"text": "↩️ Qarorni o'zgartirish", "callback_data": f"undo:{ev['id']}"}])
        if PUBLIC_URL and "localhost" not in PUBLIC_URL and "127.0.0.1" not in PUBLIC_URL:
            rows.append([{"text": "🔗 Panelda ochish", "url": f"{PUBLIC_URL}/events/{ev['id']}"}])
        return {"inline_keyboard": rows}

    # ---------------------------------------------------------- navbatga qo'yish
    def _enqueue(self, prio: int, kind: str, a, b=None) -> None:
        self.q.put((prio, next(self._seq), kind, a, b, time.time()))

    def _enabled_for(self, event_id: int) -> dict | None:
        if not self.token or not db.flag("telegram_enabled"):
            return None
        ev = db.get_event(event_id)
        if not ev or ev["score"] < db.fnum("telegram_min_score", 0):
            return None
        return ev

    def notify_alert(self, event_id: int) -> None:
        """Hodisa aniqlangan zahoti: rasm + sabablar + tugmalar (klipni kutmasdan)."""
        if self._enabled_for(event_id) is None:
            return
        if len(self._alert_done) > 500:   # klipi kelmay qolgan eski hodisalar xotiradan tozalanadi
            for k in [k for k, e in self._alert_done.items() if e.is_set()]:
                self._alert_done.pop(k, None)
        self._alert_done[event_id] = threading.Event()
        self._enqueue(PRIO_ALERT, "alert", event_id)

    def notify_clip(self, event_id: int) -> None:
        """Klip tayyor: avval yuborilgan xabarlardagi rasm videoga almashadi."""
        if self._enabled_for(event_id) is None:
            return
        self._enqueue(PRIO_CLIP, "clip", event_id)

    def notify_event(self, event_id: int) -> None:
        """Eski usul (qayta yuborish): bor narsa bilan to'liq xabar."""
        if self._enabled_for(event_id) is None:
            return
        self._enqueue(PRIO_ALERT, "full", event_id)

    def resend_event(self, event_id: int) -> None:
        if self.token:
            self._enqueue(PRIO_ALERT, "full", event_id)

    def _queue_markup(self, chat_id: int) -> dict:
        on = db.scalar("SELECT COALESCE(queue_alerts, 1) FROM subscribers WHERE chat_id=?", (chat_id,))
        return {"inline_keyboard": [[{"text": "🔕 Navbat signallarini o'chirish" if on else "🔔 Navbat signallarini yoqish",
                                      "callback_data": "qa:off" if on else "qa:on"}]]}

    def send_queue_alert(self, text: str) -> int:
        """2.0.0: navbat qoidasi signali — navbat signallarini o'chirmagan obunachilarga."""
        subs = (db.rows("SELECT chat_id FROM subscribers WHERE active=1 AND COALESCE(queue_alerts, 1)=1")
                if self.token else [])
        for s in subs:
            self._enqueue(PRIO_ALERT, "text", s["chat_id"], text)
        return len(subs)

    def send_text_all(self, text: str) -> int:
        subs = db.rows("SELECT chat_id FROM subscribers WHERE active=1") if self.token else []
        for s in subs:
            self._enqueue(PRIO_ALERT, "text", s["chat_id"], text)
        return len(subs)

    def send_test(self) -> int:
        subs = db.rows("SELECT chat_id FROM subscribers WHERE active=1")
        for s in subs:
            self._enqueue(PRIO_TEXT, "text", s["chat_id"], "🧪 Sinov xabari: Hushyor AI ↔ Telegram aloqasi ishlayapti ✅")
        return len(subs)

    def refresh_event(self, event_id: int) -> None:
        """Hodisa holati o'zgarganda (Telegram yoki web orqali) barcha xabarlarni yangilash."""
        self._enqueue(PRIO_REFRESH, "refresh", event_id)

    def _worker_loop(self) -> None:
        """Bajaruvchi: navbatdan eng ustuvor vazifani o'zi oladi va bajaradi (WORKERS ta parallel)."""
        while not self.stop_evt.is_set():
            try:
                prio, _, kind, a, b, t_q = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            fn = {"alert": self._send_alert, "clip": self._send_clip, "full": self._send_full,
                  "refresh": self._refresh, "text": self._send_text}.get(kind)
            if fn and not self.stop_evt.is_set():
                self._safe(fn, a, b, t_q)

    # --------------------------------------------------------------- yuborish
    def _log(self, event_id, stage, chat_id, t_event, t_queued, t_sent, res, err="") -> None:
        ok = bool(res and res.get("ok"))
        tg_date = None
        if ok and isinstance(res.get("result"), dict):
            tg_date = res["result"].get("date")
        try:
            db.execute("INSERT INTO notify_log(event_id, channel, stage, chat_id, t_event, t_queued, t_sent, tg_date, "
                       "ok, error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (event_id, "telegram", stage, chat_id, t_event, t_queued, t_sent, tg_date, int(ok),
                        (err or (res or {}).get("description") or "")[:300] if not ok else None))
        except Exception:
            log.exception("notify_log yozilmadi")

    def _subs(self) -> list[int]:
        return [s["chat_id"] for s in db.rows("SELECT chat_id FROM subscribers WHERE active=1")]

    def _photo_bytes(self, ev: dict) -> bytes | None:
        thumb = CLIPS_DIR / ev["thumb_path"] if ev.get("thumb_path") else None
        if thumb and thumb.exists() and thumb.stat().st_size < PHOTO_LIMIT:
            return thumb.read_bytes()
        return None

    def _send_alert(self, event_id: int, opts, t_q: float) -> None:
        if opts and opts.get("retry"):
            self._retry_alert(event_id, opts, t_q)
            return
        done = self._alert_done.setdefault(event_id, threading.Event())
        try:
            ev = db.get_event(event_id)
            subs = self._subs()
            if not ev or not subs:
                if ev and not subs:
                    log.info("Telegram obunachisi yo'q — hodisa #%d yuborilmadi", event_id)
                return
            cap = self._caption(ev, clip_pending=bool(db.flag("telegram_send_video")))
            markup = json.dumps(self._markup(ev))
            photo = self._photo_bytes(ev)
            t_ev = event_time(ev)

            def one(chat_id: int):
                res, kind = None, "photo"
                try:
                    if photo:
                        res = self.api("sendPhoto", {"chat_id": str(chat_id), "caption": cap, "parse_mode": "HTML",
                                                     "reply_markup": markup},
                                       files={"photo": (f"hodisa_{event_id}.jpg", photo, "image/jpeg")},
                                       timeout=30, chat_id=chat_id)
                    if not res or not res.get("ok"):
                        kind = "text"
                        res = self.api("sendMessage", {"chat_id": chat_id, "text": cap, "parse_mode": "HTML",
                                                       "reply_markup": json.loads(markup)}, chat_id=chat_id)
                except Exception as e:
                    self._log(event_id, "alert", chat_id, t_ev, t_q, time.time(), None, str(e))
                    self._schedule_alert_retry(event_id, 0, [chat_id])
                    return
                t_sent = time.time()
                self._log(event_id, "alert", chat_id, t_ev, t_q, t_sent, res)
                if res and res.get("ok"):
                    db.execute("INSERT OR REPLACE INTO tg_messages(event_id, chat_id, message_id, kind) "
                               "VALUES(?,?,?,?)", (event_id, chat_id, res["result"]["message_id"], kind))
                    self.sent += 1
                else:
                    log.warning("Hodisa #%d yuborilmadi (%s): %s", event_id, chat_id, self.last_error)
                    if _transient(res):
                        # har chat o'z natijasi ma'lum bo'lganda (60 s dan keyin ham) — qayta urinish rejalashtiriladi
                        self._schedule_alert_retry(event_id, 0, [chat_id])

            # Rasm kichik (~100 KB) — barcha chatlarga parallel yuboriladi
            futs = [self.io_pool.submit(self._safe, one, c) for c in subs]
            # Telegram osilib qolsa bitta chat ~3 daqiqa (3 × 30 s rasm + 3 × 30 s matn) — klip bosqichi shuni kutadi
            wait(futs, timeout=240)
            db.update_event(event_id, telegram_sent=1)
        finally:
            done.set()

    def _schedule_alert_retry(self, event_id: int, n: int, chats: list[int]) -> None:
        if self.stop_evt.is_set():
            return
        if n >= len(ALERT_RETRY):
            log.error("Hodisa #%d xabari %d ta chatga yetmadi (%d urinish) — Telegram uzoq ishlamadi", event_id,
                      len(chats), n + 1)
            return
        log.warning("Hodisa #%d xabari %d ta chatga yetmadi — %.0f s dan keyin qayta (%d/%d)", event_id, len(chats),
                    ALERT_RETRY[n], n + 1, len(ALERT_RETRY))

        def fire():
            self._timers.discard(t)
            if not self.stop_evt.is_set():
                # yangi signallardan keyin (PRIO_CLIP): eski hodisaning qayta urinishi yangi signalni kechiktirmasin
                self._enqueue(PRIO_CLIP, "alert", event_id, {"retry": n + 1, "chats": list(chats)})
        t = threading.Timer(ALERT_RETRY[n], fire)
        t.daemon = True
        self._timers.add(t)
        t.start()

    def _retry_alert(self, event_id: int, opts: dict, t_q: float) -> None:
        """Yetmagan chatlarga qayta: klip tayyor bo'lsa — video bilan, bo'lmasa rasm (keyin klip o'sha xabarga
        qo'shiladi). Shu orada boshqa yo'l bilan (klip bosqichi) yetib bo'lgan chatlar o'tkazib yuboriladi."""
        ev = self._enabled_for(event_id)         # Telegram o'chirilgan / chegara o'zgargan bo'lsa — yubormaymiz
        if not ev:
            return
        if time.time() - float(ev.get("created_at") or 0) > ALERT_MAX_AGE:
            log.error("Hodisa #%d xabari yuborilmadi: %.0f daqiqadan eski", event_id, ALERT_MAX_AGE / 60)
            return
        chats = [int(c) for c in opts.get("chats") or []]
        # retries=0: tashqi jadval (ALERT_RETRY) o'zi qayta uradi — osilgan Telegram bajaruvchilarni band qilmasin
        failed = self._send_full(event_id, {"chats": chats, "only_missing": True, "retries": 0}, t_q)
        if failed:
            self._schedule_alert_retry(event_id, int(opts["retry"]), failed)

    def _send_clip(self, event_id: int, opts, t_q: float) -> None:
        """opts: None (birinchi urinish) yoki {"retry": n, "chats": [...]} — tarmoq xatosi bo'lgan chatlarga qayta."""
        opts = opts or {}
        retry = int(opts.get("retry", 0))
        done = self._alert_done.get(event_id)
        if done is not None and not done.wait(60):   # rasm xabarlari yuborilib bo'lishini kutish (message_id kerak)
            # Signal bosqichi hali tugamagan (Telegram osilgan): keyinroq — aks holda o'sha chatga ikkinchi xabar ketardi
            tm = threading.Timer(CLIP_RETRY_SEC, self._enqueue, (PRIO_CLIP, "clip", event_id, opts or None))
            tm.daemon = True
            tm.start()
            return
        ev = db.get_event(event_id)
        if not ev:
            return
        msgs = db.rows("SELECT * FROM tg_messages WHERE event_id=?", (event_id,))
        if not msgs and not retry:
            self._send_full(event_id, {"only_missing": True}, t_q)
            # Shu orada signal qayta urinishi rasm yuborgan bo'lishi mumkin — unga video qo'shiladi (pastda)
            msgs = db.rows("SELECT * FROM tg_messages WHERE event_id=?", (event_id,))
            if not any(m["kind"] in ("photo", "text") for m in msgs):
                return
        # Video allaqachon qo'shilgan xabarlar o'tkazib yuboriladi (takroriy chaqiruv yoki qayta urinish)
        msgs = [m for m in msgs if m["kind"] in ("photo", "text")]
        if opts.get("chats") is not None:
            want = {int(c) for c in opts["chats"]}
            msgs = [m for m in msgs if int(m["chat_id"]) in want]
        if not msgs:
            return
        clip = CLIPS_DIR / ev["clip_path"] if ev.get("clip_path") else None
        use_video = (db.flag("telegram_send_video") and clip is not None and clip.exists()
                     and clip.stat().st_size < VIDEO_LIMIT)
        if not use_video:
            self._refresh(event_id, None, t_q)   # "klip tayyorlanmoqda" yozuvi olib tashlanadi
            return
        cap = self._caption(ev)
        markup = self._markup(ev)
        thumb = CLIPS_DIR / ev["thumb_path"] if ev.get("thumb_path") else None
        small = _small_jpeg(thumb) if thumb and thumb.exists() else None
        t_ev = event_time(ev)
        file_id: list[str] = []
        failed: list[int] = []

        def media(ref: str) -> str:
            m = {"type": "video", "media": ref, "caption": cap, "parse_mode": "HTML", "supports_streaming": True}
            if small and ref.startswith("attach://"):
                m["thumbnail"] = "attach://thumb"
            return json.dumps(m)

        def edit(m: dict, upload: bool):
            chat_id = m["chat_id"]
            try:
                if upload:
                    with open(clip, "rb") as fv:
                        files = {"clip": (clip.name, fv, "video/mp4")}
                        if small:
                            files["thumb"] = ("thumb.jpg", small, "image/jpeg")
                        res = self.api("editMessageMedia", {"chat_id": str(chat_id), "message_id": str(m["message_id"]),
                                                            "media": media("attach://clip"),
                                                            "reply_markup": json.dumps(markup)},
                                       files=files, timeout=120, chat_id=chat_id)
                else:
                    res = self.api("editMessageMedia", {"chat_id": chat_id, "message_id": m["message_id"],
                                                        "media": json.loads(media(file_id[0])),
                                                        "reply_markup": markup}, chat_id=chat_id)
                if not res or not res.get("ok"):
                    # Matnli xabarga media qo'shib bo'lmasa — klip javob sifatida alohida yuboriladi
                    reply_kind = f"{m['kind']}_video"
                    if file_id:
                        res = self.api("sendVideo", {"chat_id": chat_id, "video": file_id[0],
                                                     "caption": f"🎬 Hodisa #{event_id} klipi",
                                                     "reply_to_message_id": m["message_id"]}, chat_id=chat_id)
                    else:
                        with open(clip, "rb") as fv:
                            res = self.api("sendVideo", {"chat_id": str(chat_id), "caption": f"🎬 Hodisa #{event_id} klipi",
                                                         "reply_to_message_id": str(m["message_id"]),
                                                         "supports_streaming": "true"},
                                           files={"video": (clip.name, fv, "video/mp4")}, timeout=120, chat_id=chat_id)
                    if res and res.get("ok"):
                        # Rasm/matn xabari joyida qoldi, video — alohida javob: yangilashda "tayyorlanmoqda" yozilmaydi
                        db.execute("UPDATE tg_messages SET kind=? WHERE event_id=? AND chat_id=?",
                                   (reply_kind, event_id, chat_id))
                else:
                    db.execute("UPDATE tg_messages SET kind='video' WHERE event_id=? AND chat_id=?", (event_id, chat_id))
                if not res or (not res.get("ok") and int(res.get("error_code") or 0) in (0, 429, 500, 502, 503, 504)):
                    failed.append(chat_id)
                self._log(event_id, "clip", chat_id, t_ev, t_q, time.time(), res)
                if res and res.get("ok") and not file_id:
                    v = (res.get("result") or {}).get("video") if isinstance(res.get("result"), dict) else None
                    if v and v.get("file_id"):
                        file_id.append(v["file_id"])
            except Exception as e:
                failed.append(chat_id)
                self._log(event_id, "clip", chat_id, t_ev, t_q, time.time(), None, str(e))

        # Video katta — bir marta yuklanadi, qolgan chatlarga file_id bilan (parallel)
        edit(msgs[0], upload=True)
        rest = msgs[1:]
        if rest:
            futs = [self.io_pool.submit(self._safe, edit, m, not file_id) for m in rest]
            wait(futs, timeout=180)
        self._alert_done.pop(event_id, None)
        if failed and not self.stop_evt.is_set():
            if retry < CLIP_RETRIES:
                log.warning("Hodisa #%d klipi %d ta chatga yetmadi — %.0f s dan keyin qayta (%d/%d)", event_id,
                            len(failed), CLIP_RETRY_SEC, retry + 1, CLIP_RETRIES)
                t = threading.Timer(CLIP_RETRY_SEC, self._enqueue,
                                    (PRIO_CLIP, "clip", event_id, {"retry": retry + 1, "chats": failed}))
                t.daemon = True
                t.start()
            else:
                log.error("Hodisa #%d klipi %d ta chatga yuborilmadi (%d urinish)", event_id, len(failed), retry + 1)

    def _send_full(self, event_id: int, opts, t_q: float) -> list[int]:
        """Bor narsa bilan bitta xabar (qayta yuborish yoki rasm bosqichi o'tkazib yuborilgan bo'lsa).
        opts: {"chats": [...]} — faqat shu chatlarga; {"only_missing": True} — xabari yetib bo'lgan chatlar o'tkazib
        yuboriladi (signal qayta urinishi va klip bosqichi bir-birini takrorlamasin). Qaytaradi: vaqtinchalik xato bilan
        yetmagan chatlar."""
        opts = opts or {}
        with self._ev_locks[int(event_id) % len(self._ev_locks)]:
            return self._send_full_locked(event_id, opts, t_q)

    def _send_full_locked(self, event_id: int, opts: dict, t_q: float) -> list[int]:
        ev = db.get_event(event_id)
        subs = self._subs()
        if opts.get("chats") is not None:
            want = {int(c) for c in opts["chats"]}
            subs = [c for c in subs if int(c) in want]
        if opts.get("only_missing"):
            have = {int(r["chat_id"]) for r in db.rows("SELECT chat_id FROM tg_messages WHERE event_id=?", (event_id,))}
            subs = [c for c in subs if int(c) not in have]
        failed: list[int] = []
        if not ev or not subs:
            return failed
        cap = self._caption(ev)
        markup = json.dumps(self._markup(ev))
        clip = CLIPS_DIR / ev["clip_path"] if ev.get("clip_path") else None
        send_video = db.flag("telegram_send_video") and clip and clip.exists() and clip.stat().st_size < VIDEO_LIMIT
        rt = int(opts.get("retries", 2))
        # Klip hali tayyor emas — rasm/matn xabarida "klip tayyorlanmoqda" (keyin video shu xabarga qo'shiladi)
        cap_wait = (self._caption(ev, clip_pending=True)
                    if db.flag("telegram_send_video") and not ev.get("clip_path") and not ev.get("clip_error") else cap)
        photo = self._photo_bytes(ev)
        thumb = CLIPS_DIR / ev["thumb_path"] if ev.get("thumb_path") else None
        small = _small_jpeg(thumb) if thumb and thumb.exists() else None
        t_ev = event_time(ev)
        file_id: list[str] = []
        for chat_id in subs:
            res, kind = None, None
            try:
                if send_video:
                    kind = "video"
                    if file_id:
                        res = self.api("sendVideo", {"chat_id": chat_id, "video": file_id[0], "caption": cap,
                                                     "parse_mode": "HTML", "reply_markup": json.loads(markup)},
                                       chat_id=chat_id, retries=rt)
                    else:
                        with open(clip, "rb") as fv:
                            files = {"video": (clip.name, fv, "video/mp4")}
                            if small:
                                files["thumbnail"] = ("thumb.jpg", small, "image/jpeg")
                            res = self.api("sendVideo", {"chat_id": str(chat_id), "caption": cap, "parse_mode": "HTML",
                                                         "supports_streaming": "true", "reply_markup": markup},
                                           files=files, timeout=120, chat_id=chat_id, retries=rt)
                        v = (res or {}).get("result", {}).get("video") if res and res.get("ok") else None
                        if v:
                            file_id.append(v["file_id"])
                if (not res or not res.get("ok")) and photo:
                    kind = "photo"
                    res = self.api("sendPhoto", {"chat_id": str(chat_id), "caption": cap_wait, "parse_mode": "HTML",
                                                 "reply_markup": markup},
                                   files={"photo": (f"hodisa_{event_id}.jpg", photo, "image/jpeg")}, chat_id=chat_id,
                                   retries=rt)
                if not res or not res.get("ok"):
                    kind = "text"
                    res = self.api("sendMessage", {"chat_id": chat_id, "text": cap_wait, "parse_mode": "HTML",
                                                   "reply_markup": json.loads(markup)}, chat_id=chat_id, retries=rt)
            except Exception as e:
                self._log(event_id, "full", chat_id, t_ev, t_q, time.time(), None, str(e))
                failed.append(chat_id)
                continue
            self._log(event_id, "full", chat_id, t_ev, t_q, time.time(), res)
            if res and res.get("ok"):
                db.execute("INSERT OR REPLACE INTO tg_messages(event_id, chat_id, message_id, kind) VALUES(?,?,?,?)",
                           (event_id, chat_id, res["result"]["message_id"], kind))
                self.sent += 1
            elif _transient(res):
                failed.append(chat_id)
        db.update_event(event_id, telegram_sent=1)
        return failed

    def _send_text(self, chat_id: int, text: str, _t_q: float) -> None:
        self._reply(chat_id, text)

    def _refresh(self, event_id: int, _b=None, _t_q: float = 0.0) -> None:
        ev = db.get_event(event_id)
        if not ev:
            return
        markup = self._markup(ev)
        for m in db.rows("SELECT * FROM tg_messages WHERE event_id=?", (event_id,)):
            pending = m["kind"] == "photo" and bool(db.flag("telegram_send_video")) and not ev.get("clip_path")
            cap = self._caption(ev, clip_pending=pending)
            if m["kind"] in ("text", "text_video"):
                self.api("editMessageText", {"chat_id": m["chat_id"], "message_id": m["message_id"], "text": cap,
                                             "parse_mode": "HTML", "reply_markup": markup}, chat_id=m["chat_id"])
            else:
                self.api("editMessageCaption", {"chat_id": m["chat_id"], "message_id": m["message_id"],
                                                "caption": cap, "parse_mode": "HTML", "reply_markup": markup},
                         chat_id=m["chat_id"])

    def info(self) -> dict:
        return {
            "enabled": bool(self.token), "status": self.status, "error": self.last_error,
            "username": (self.me or {}).get("username"), "name": (self.me or {}).get("first_name"),
            "last_ok": self.last_ok, "sent": self.sent, "queue": self.q.qsize(),
        }


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1)))))
    return vals[k]


def latency_stats(hours: float = 24, channel: str | None = None) -> dict:
    """Kanal va bosqich bo'yicha kechikish: hodisa aniqlangandan xabar yetkazilguncha (soniya)."""
    since = time.time() - hours * 3600
    sql = "SELECT channel, stage, ok, t_event, t_queued, t_sent, tg_date FROM notify_log WHERE t_sent>=?"
    params: list = [since]
    if channel:
        sql += " AND channel=?"
        params.append(channel)
    out: dict = {}
    groups: dict = defaultdict(list)
    for r in db.rows(sql, params):
        groups[f"{r['channel']}/{r['stage']}"].append(r)
    for key, rs in groups.items():
        oks = [r for r in rs if r["ok"] and r["t_event"]]
        lat = [r["t_sent"] - r["t_event"] for r in oks]
        queue_wait = [r["t_sent"] - r["t_queued"] for r in oks if r["t_queued"]]
        # Telegram server vaqti bilan farq (kompyuter soati noto'g'ri bo'lsa ko'rinadi)
        skew = [r["tg_date"] - r["t_sent"] for r in oks if r["tg_date"]]
        out[key] = {"n": len(rs), "ok": len(oks), "p50": _pct(lat, 50), "p95": _pct(lat, 95),
                    "max": max(lat) if lat else 0.0, "send_p95": _pct(queue_wait, 95),
                    "within_5s": (sum(1 for x in lat if x <= 5.0) / len(lat)) if lat else None,
                    "clock_skew": (sum(skew) / len(skew)) if skew else None}
    return out


_bot: TelegramBot | None = None


def get_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot
