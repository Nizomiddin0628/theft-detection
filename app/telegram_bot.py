"""Telegram bot: shubhali hodisa klipini yuboradi, qo'riqchi Tasdiq / Rad tugmasini bosadi.

Long polling ishlatiladi — tashqi IP yoki webhook kerak emas, oddiy kompyuterda ishlaydi.
Ulanish: web paneldagi 6 xonali kodni botga yuboring (yoki /start KOD).
"""
from __future__ import annotations

import html
import json
import queue
import threading
import time
from pathlib import Path

import httpx

from . import db
from .config import CLIPS_DIR, PUBLIC_URL, TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN
from .logutil import get_logger

log = get_logger("telegram")

KIND_UZ = {"pocket": "Cho'ntakka solish", "torso": "Kiyim ichiga yashirish", "bag": "Sumkaga solish",
           "offscreen": "Qo'l kadrdan yashirildi"}
STATUS_UZ = {"yangi": "🟡 Tekshirilmagan", "tasdiqlangan": "✅ Tasdiqlangan",
             "rad_etilgan": "❌ Xato signal"}

HELP = (
    "🛡 <b>Hushyor AI — do'kon qo'riqchisi boti</b>\n\n"
    "Kamerada shubhali harakat aniqlansa, men shu yerga qisqa video klip va sabablarini yuboraman. "
    "Siz videoni ko'rib <b>✅ Tasdiqlash</b> yoki <b>❌ Xato signal</b> tugmasini bosasiz.\n\n"
    "Buyruqlar:\n"
    "/holat — kameralar va bugungi statistika\n"
    "/oxirgi — oxirgi 5 ta hodisa\n"
    "/tekshirilmagan — javob kutayotgan hodisalar\n"
    "/stop — xabarlarni to'xtatish\n"
    "/yordam — shu yordam\n\n"
    "⚠️ Bot hech kimni ayblamaydi: u faqat \"tekshirib ko'ring\" deydi. Qaror har doim odamniki."
)


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
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.client = httpx.Client(timeout=httpx.Timeout(40.0, connect=10.0))
        self._threads: list[threading.Thread] = []
        self.manager = None

    # ------------------------------------------------------------------ API
    def api(self, method: str, data: dict | None = None, files: dict | None = None,
            timeout: float = 30.0, retries: int = 2) -> dict:
        url = f"{self.api_base}/bot{self.token}/{method}"
        for attempt in range(retries + 1):
            try:
                if files:
                    r = self.client.post(url, data=data or {}, files=files, timeout=timeout)
                else:
                    r = self.client.post(url, json=data or {}, timeout=timeout)
                res = r.json()
            except (httpx.HTTPError, ValueError) as e:
                self.last_error = f"Tarmoq xatosi: {e.__class__.__name__}"
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            if res.get("ok"):
                self.last_ok = time.time()
                return res
            if r.status_code == 429:
                wait = float(res.get("parameters", {}).get("retry_after", 3))
                time.sleep(min(wait, 30))
                continue
            self.last_error = res.get("description", "noma'lum xato")
            if r.status_code in (400, 401, 403, 404):
                return res
            if attempt < retries:
                time.sleep(1.0)
        return {"ok": False, "description": self.last_error}

    # ---------------------------------------------------------------- start
    def start(self) -> None:
        if not self.token:
            self.status = "o'chirilgan"
            log.warning("TELEGRAM_BOT_TOKEN berilmagan — bot ishlamaydi")
            return
        t1 = threading.Thread(target=self._poll_loop, daemon=True, name="tg-poll")
        t2 = threading.Thread(target=self._send_loop, daemon=True, name="tg-send")
        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def stop(self) -> None:
        self.stop_evt.set()

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
            self.api("setMyCommands", {"commands": [
                {"command": "holat", "description": "Kameralar va bugungi statistika"},
                {"command": "oxirgi", "description": "Oxirgi 5 ta hodisa"},
                {"command": "tekshirilmagan", "description": "Javob kutayotgan hodisalar"},
                {"command": "stop", "description": "Xabarlarni to'xtatish"},
                {"command": "yordam", "description": "Yordam"},
            ]})
        except httpx.HTTPError:
            pass
        log.info("Telegram bot ulandi: @%s", self.me.get("username"))
        return True

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
            for upd in r.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                try:
                    if "message" in upd:
                        self._on_message(upd["message"])
                    elif "callback_query" in upd:
                        self._on_callback(upd["callback_query"])
                except Exception:
                    log.exception("Telegram yangilanishini qayta ishlashda xato")

    # ------------------------------------------------------------- handlers
    def _reply(self, chat_id: int, text: str, markup: dict | None = None) -> dict:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            data["reply_markup"] = markup
        return self.api("sendMessage", data)

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

        if cmd in ("/start", "/ulash") and arg:
            if arg == code:
                self._subscribe(msg)
            else:
                self._reply(chat_id, "❌ Kod noto'g'ri. Kodni web paneldagi <b>Telegram</b> bo'limidan oling.")
            return
        if text == code:
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
        else:
            self._reply(chat_id, "Buyruqni tushunmadim. /yordam ni bosing.")

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
        return "\n".join(lines)

    def _last_text(self, sql: str, title: str) -> str:
        evs = db.rows(sql)
        if not evs:
            return title + "\n\nHozircha hodisa yo'q."
        lines = [title, ""]
        for e in evs:
            lines.append(f"#{e['id']} · {fmt_time(e['created_at'])} · {esc(KIND_UZ.get(e['kind'], e['kind']))} · "
                         f"ball {e['score']:.0f} · {STATUS_UZ.get(e['status'], e['status'])}")
        return "\n".join(lines)

    def _on_callback(self, cb: dict) -> None:
        data = cb.get("data", "")
        user = cb.get("from", {})
        who = user.get("first_name") or user.get("username") or str(user.get("id"))
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        if not chat_id or not self._is_sub(chat_id):
            self.api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Ruxsat yo'q"})
            return
        try:
            action, ev_id = data.split(":")
            ev_id = int(ev_id)
        except ValueError:
            self.api("answerCallbackQuery", {"callback_query_id": cb["id"]})
            return
        status = {"ok": "tasdiqlangan", "no": "rad_etilgan"}.get(action)
        if not status:
            return
        ev = db.get_event(ev_id)
        if not ev:
            self.api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Hodisa topilmadi"})
            return
        db.review_event(ev_id, status, f"Telegram: {who}")
        txt = "✅ Tasdiqlandi. Rahmat!" if status == "tasdiqlangan" else "❌ Xato signal deb belgilandi. Rahmat!"
        self.api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": txt})
        self.refresh_event(ev_id)
        log.info("Hodisa #%d: %s (%s)", ev_id, status, who)

    # --------------------------------------------------------------- sending
    def _caption(self, ev: dict) -> str:
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
        lines = [
            "⚠️ <b>Shubhali harakat — tekshirib ko'ring</b>",
            f"🏪 {esc(store)}",
            f"📷 Kamera: {esc(ev.get('source_name'))}",
            f"🕒 {esc(when)}",
            *([susp_line] if susp_line else []),
            f"🎯 Turi: <b>{esc(KIND_UZ.get(ev.get('kind'), ev.get('kind')))}</b>",
            f"📊 Ball: <b>{ev['score']:.0f}/100</b> ({esc(ev.get('level'))})",
            "",
            "<b>Sabablar:</b>",
        ]
        for r in reasons[:6]:
            lines.append(f"• {esc(r)}")
        lines += ["", f"🆔 Hodisa #{ev['id']}", "<i>Bu ayblov emas — videoni ko'rib, o'zingiz qaror qiling.</i>"]
        if ev.get("status") and ev["status"] != "yangi":
            mark = "✅ TASDIQLANDI" if ev["status"] == "tasdiqlangan" else "❌ XATO SIGNAL"
            lines += ["", f"<b>{mark}</b> — {esc(ev.get('reviewed_by'))}, {fmt_time(ev.get('reviewed_at'))}"]
        cap = "\n".join(lines)
        return cap[:1020]

    def _markup(self, ev: dict) -> dict:
        rows = []
        if ev.get("status", "yangi") == "yangi":
            rows.append([{"text": "✅ Tasdiqlash", "callback_data": f"ok:{ev['id']}"},
                         {"text": "❌ Xato signal", "callback_data": f"no:{ev['id']}"}])
        if PUBLIC_URL and "localhost" not in PUBLIC_URL and "127.0.0.1" not in PUBLIC_URL:
            rows.append([{"text": "🔗 Panelda ochish", "url": f"{PUBLIC_URL}/events/{ev['id']}"}])
        return {"inline_keyboard": rows}

    def notify_event(self, event_id: int) -> None:
        if not self.token:
            return
        if not db.flag("telegram_enabled"):
            return
        ev = db.get_event(event_id)
        if not ev or ev["score"] < db.fnum("telegram_min_score", 0):
            return
        self.q.put(("event", event_id, None))

    def send_test(self) -> int:
        subs = db.rows("SELECT chat_id FROM subscribers WHERE active=1")
        for s in subs:
            self.q.put(("text", s["chat_id"], "🧪 Sinov xabari: Hushyor AI ↔ Telegram aloqasi ishlayapti ✅"))
        return len(subs)

    def refresh_event(self, event_id: int) -> None:
        """Hodisa holati o'zgarganda (Telegram yoki web orqali) barcha xabarlarni yangilash."""
        self.q.put(("refresh", event_id, None))

    def _send_loop(self) -> None:
        while not self.stop_evt.is_set():
            try:
                kind, a, b = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if kind == "event":
                    self._send_event(a)
                elif kind == "text":
                    self._reply(a, b)
                elif kind == "refresh":
                    self._refresh(a)
            except Exception:
                log.exception("Telegram yuborishda xato")

    def _send_event(self, event_id: int) -> None:
        ev = db.get_event(event_id)
        if not ev:
            return
        subs = db.rows("SELECT chat_id FROM subscribers WHERE active=1")
        if not subs:
            log.info("Telegram obunachisi yo'q — hodisa #%d yuborilmadi", event_id)
            return
        cap = self._caption(ev)
        markup = json.dumps(self._markup(ev))
        clip = CLIPS_DIR / ev["clip_path"] if ev.get("clip_path") else None
        thumb = CLIPS_DIR / ev["thumb_path"] if ev.get("thumb_path") else None
        send_video = db.flag("telegram_send_video") and clip and clip.exists() and clip.stat().st_size < 49_000_000
        small_thumb = _small_jpeg(thumb) if thumb and thumb.exists() else None
        for s in subs:
            res, kind = None, None
            if send_video:
                with open(clip, "rb") as fv:
                    files = {"video": (clip.name, fv, "video/mp4")}
                    if small_thumb:
                        files["thumbnail"] = ("thumb.jpg", small_thumb, "image/jpeg")
                    res = self.api("sendVideo", {"chat_id": str(s["chat_id"]), "caption": cap, "parse_mode": "HTML",
                                                 "supports_streaming": "true", "reply_markup": markup},
                                   files=files, timeout=120)
                kind = "video"
            if (not res or not res.get("ok")) and thumb and thumb.exists():
                with open(thumb, "rb") as fp:
                    res = self.api("sendPhoto", {"chat_id": str(s["chat_id"]), "caption": cap, "parse_mode": "HTML",
                                                 "reply_markup": markup},
                                   files={"photo": (thumb.name, fp, "image/jpeg")}, timeout=60)
                kind = "photo"
            if not res or not res.get("ok"):
                res = self.api("sendMessage", {"chat_id": s["chat_id"], "text": cap, "parse_mode": "HTML",
                                               "reply_markup": json.loads(markup)})
                kind = "text"
            if res and res.get("ok"):
                mid = res["result"]["message_id"]
                db.execute("INSERT OR REPLACE INTO tg_messages(event_id, chat_id, message_id, kind) VALUES(?,?,?,?)",
                           (event_id, s["chat_id"], mid, kind))
                self.sent += 1
            else:
                log.warning("Hodisa #%d yuborilmadi (%s): %s", event_id, s["chat_id"], self.last_error)
        db.update_event(event_id, telegram_sent=1)

    def _refresh(self, event_id: int) -> None:
        ev = db.get_event(event_id)
        if not ev:
            return
        cap = self._caption(ev)
        markup = self._markup(ev)
        for m in db.rows("SELECT * FROM tg_messages WHERE event_id=?", (event_id,)):
            if m["kind"] == "text":
                self.api("editMessageText", {"chat_id": m["chat_id"], "message_id": m["message_id"], "text": cap,
                                             "parse_mode": "HTML", "reply_markup": markup})
            else:
                self.api("editMessageCaption", {"chat_id": m["chat_id"], "message_id": m["message_id"],
                                                "caption": cap, "parse_mode": "HTML", "reply_markup": markup})

    def info(self) -> dict:
        return {
            "enabled": bool(self.token), "status": self.status, "error": self.last_error,
            "username": (self.me or {}).get("username"), "name": (self.me or {}).get("first_name"),
            "last_ok": self.last_ok, "sent": self.sent, "queue": self.q.qsize(),
        }


_bot: TelegramBot | None = None


def get_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot
