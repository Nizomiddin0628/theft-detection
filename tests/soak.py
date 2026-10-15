"""Uzoq muddatli barqarorlik sinovi (72 soat): edge + bulut + soxta Telegram + soxta IP kamera, nosozliklar bilan.

Nima qiladi:
  * alohida papkada (jonli data/ ga TEGMAYDI) edge dasturini (tests/soak_edge.py = run.py + har N soniyada soxta
    signal), bulut serverini, ikkita soxta Telegram serverini (edge boti va bulut boti uchun) va namunaviy videoni
    MJPEG oqim qilib beradigan soxta IP kamerani ishga tushiradi; edge bulutga oraliq TCP-proksi orqali ulanadi;
  * davriy nosozliklar: internet uzilishi (proksi — "rad etish" yoki "javobsiz qora tuynuk"), Telegram ishlamasligi
    (5xx yoki osilib qolish), kamera uzilishi; har --restart-hours da edge va bulutni qayta ishga tushirish;
  * har --sample-sec da o'lchaydi: xotira (RSS), oqimlar, dastaklar, CPU, disk, bulut navbati, hodisalar, /healthz;
  * oxirida tekshiradi va hisobot yozadi: xotira oqishi yo'q, navbat bo'shadi, har hodisa bulutga va Telegram'ga
    aynan bir marta yetdi (yo'qolgan ham, takrorlangan ham yo'q), disk chegaralangan, kutilmagan xato yo'q;
  * 1.9.0: kamerada sanash chizig'i (--count-line, sukut videosida odamlar uni kesib o'tadi) — har o'tish daqiqalik
    yig'indiga va bulutga aynan yetgani (uzilish va qayta ishga tushishlarda ham) tekshiriladi.

Ishga tushirish (loyiha ildizidan; virtual muhit faollashtirilgan):
    python tests/soak.py --hours 72                       # to'liq sinov (ma'lumot: %TEMP%/hushyor_soak_<vaqt>)
    python tests/soak.py --hours 0.75 --cycle-min 12      # qisqa tekshiruv (taxminan 1 soat)
    python tests/soak.py --report-live data/hushyor.db --hours 72
                                                         # jonli tizimning o'z ko'rsatkichlaridan 72 soatlik hisobot
                                                         # (bazani faqat O'QIYDI; hech narsa ishga tushirmaydi)
Natija: <papka>/hisobot.md, hisobot.html (grafiklar), samples.csv, summary.json. Chiqish kodi: 0 — o'tdi, 1 — yiqildi.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
# Diqqat: orkestrator `app` paketini import QILMAYDI (app.config import paytida data/ papkalarini yaratadi).

H = 3600.0
NOTIFY_MAX_AGE = 600.0          # bulut: bundan eski hodisa uchun xabar yo'q (cloud/notify.py bilan bir xil)


def now() -> float:
    return time.time()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def fmt_dur(sec: float) -> str:
    sec = int(max(sec, 0))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h} soat {m} daq" if h else (f"{m} daq {s} s" if m else f"{s} s")


def dir_mb(p: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total / 2 ** 20


def slope_per_hour(pts: list[tuple[float, float]]) -> float | None:
    """Eng kichik kvadratlar: qiymatning soatiga o'zgarishi."""
    if len(pts) < 5:
        return None
    xs = [p[0] / H for p in pts]
    ys = [p[1] for p in pts]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[max(0, min(len(v) - 1, int(round(p / 100 * (len(v) - 1)))))]


# ============================================================================ soxta Telegram
class FakeTelegram:
    """Bot API taqlidi. mode: None — ishlaydi; "5xx" — 502 qaytaradi; "hang" — 40 s javobsiz, keyin 504 (yetkazilmagan);
    "slow" — xabar YETKAZILADI, lekin javob 35 s dan keyin keladi (mijoz kutib bo'lmay qayta yuboradi — haqiqiy
    Telegram'da takroriy xabar shunday paydo bo'ladi; bunday takror "ruxsat etilgan" deb alohida sanaladi)."""

    HANG_SEC = 40.0
    SLOW_SEC = 35.0

    def __init__(self, name: str):
        self.name = name
        self.calls: list[dict] = []
        self.polls = 0
        self.updates: list[dict] = []
        self.mode: str | None = None
        self._uid = 1000
        self._mid = 1
        self.server = None
        self.port = 0
        app = FastAPI()
        self.app = app

        @app.post("/bot{token}/{method}")
        async def method(token: str, method: str, request: Request):
            return await self._handle(method, request)

    def push_text(self, chat_id: int, text: str, first_name: str = "Sinov") -> None:
        self._uid += 1
        self.updates.append({"update_id": self._uid, "message": {
            "message_id": self._uid, "date": int(now()), "text": text, "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "first_name": first_name, "username": f"u{chat_id}"}}})

    async def _handle(self, method: str, request: Request):
        ctype = request.headers.get("content-type", "")
        data: dict = {}
        nfiles = 0
        if "multipart" in ctype:
            form = await request.form()
            for k, v in form.items():
                if hasattr(v, "read"):
                    await v.read()
                    nfiles += 1
                else:
                    data[k] = v
        else:
            try:
                data = await request.json()
            except Exception:  # noqa: BLE001
                data = {}
        mode = self.mode
        if method == "getUpdates":
            self.polls += 1
            if mode == "hang":
                await asyncio.sleep(self.HANG_SEC)
                return JSONResponse(status_code=504, content={"ok": False, "error_code": 504, "description": "Gateway Timeout"})
            if mode == "5xx":
                await asyncio.sleep(1.0)
                return JSONResponse(status_code=502, content={"ok": False, "error_code": 502, "description": "Bad Gateway"})
            if mode == "slow":
                await asyncio.sleep(self.SLOW_SEC)
            offset = int(data.get("offset", 0) or 0)
            for _ in range(20):
                res = [u for u in self.updates if u["update_id"] >= offset]
                if res:
                    return {"ok": True, "result": res}
                await asyncio.sleep(0.1)
            return {"ok": True, "result": []}
        text = data.get("text") or data.get("caption") or ""
        media = data.get("media")
        if isinstance(media, str):
            try:
                media = json.loads(media)
            except ValueError:
                media = None
        if isinstance(media, dict):
            text = media.get("caption") or text
        rec = {"t": now(), "method": method, "chat": str(data.get("chat_id", "")), "text": str(text)[:400],
               "files": nfiles, "ok": mode in (None, "slow"), "mode": mode, "mid": None}
        self.calls.append(rec)
        if mode == "hang":
            await asyncio.sleep(self.HANG_SEC)
            return JSONResponse(status_code=504, content={"ok": False, "error_code": 504, "description": "Gateway Timeout"})
        if mode == "5xx":
            return JSONResponse(status_code=502, content={"ok": False, "error_code": 502, "description": "Bad Gateway"})
        t = int(now())
        if method in ("sendMessage", "sendPhoto", "sendVideo"):
            self._mid += 1
            rec["mid"] = self._mid
        if mode == "slow":
            await asyncio.sleep(self.SLOW_SEC)
        if method == "getMe":
            return {"ok": True, "result": {"id": 42, "is_bot": True, "first_name": self.name, "username": f"{self.name}_bot"}}
        if method in ("sendMessage", "sendPhoto", "sendVideo"):
            mid = rec["mid"]
            res = {"message_id": mid, "date": t, "chat": {"id": int(data.get("chat_id", 0) or 0)}}
            if method == "sendPhoto":
                res["photo"] = [{"file_id": f"photo{mid}", "width": 320, "height": 180}]
            if method == "sendVideo":
                res["video"] = {"file_id": f"video{mid}"}
            return {"ok": True, "result": res}
        if method == "editMessageMedia":
            ref = (media or {}).get("media", "") if isinstance(media, dict) else ""
            fid = ref if ref and not ref.startswith("attach://") else f"video_edit{self._mid}"
            return {"ok": True, "result": {"message_id": int(data.get("message_id", 0) or 0), "date": t,
                                           "chat": {"id": int(data.get("chat_id", 0) or 0)}, "video": {"file_id": fid}}}
        if method in ("editMessageCaption", "editMessageText"):
            return {"ok": True, "result": {"message_id": int(data.get("message_id", 0) or 0), "date": t}}
        return {"ok": True, "result": True}

    def start(self) -> None:
        import uvicorn

        self.port = free_port()
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", access_log=False)
        self.server = uvicorn.Server(cfg)
        threading.Thread(target=self.server.run, daemon=True, name=f"tg-{self.name}").start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.1)
        raise RuntimeError("soxta Telegram ishga tushmadi")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def ok_calls(self, chat: int | str | None = None) -> list[dict]:
        return [c for c in list(self.calls) if c["ok"] and (chat is None or c["chat"] == str(chat))]


# ============================================================================ soxta IP kamera (MJPEG)
class FakeCamera:
    def __init__(self, video: Path, fps: float = 12.0, width: int = 640):
        import cv2

        cap = cv2.VideoCapture(str(video))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(round(src_fps / fps)))
        self.frames: list[bytes] = []
        i = 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if i % step == 0:
                h, w = f.shape[:2]
                if w > width:
                    f = cv2.resize(f, (width, int(h * width / w)))
                self.frames.append(cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes())
            i += 1
        cap.release()
        if not self.frames:
            raise RuntimeError(f"video o'qilmadi: {video}")
        self.fps = fps
        self.port = free_port()
        self.up = False
        self.gen = 0
        self.httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/cam.mjpg"

    def start(self) -> None:
        cam = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def do_GET(self):
                gen = cam.gen
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                i, t_next = 0, time.time()
                try:
                    while cam.up and cam.gen == gen:
                        b = cam.frames[i % len(cam.frames)]
                        i += 1
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(b) + b + b"\r\n")
                        t_next += 1.0 / cam.fps
                        time.sleep(max(0.0, t_next - time.time()))
                except OSError:
                    pass

        self.up = True
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fake-cam").start()

    def stop(self) -> None:
        self.up = False
        self.gen += 1
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


# ============================================================================ "internet" (TCP proksi)
class NetProxy:
    """edge → bulut ulanishi shu proksi orqali. down("refuse") — ulanish rad etiladi; down("blackhole") — ulanish
    qabul qilinadi, lekin javob kelmaydi (internet "osilib" qolganday). Uzilishda ochiq ulanishlar ham uziladi."""

    def __init__(self, target_port: int):
        self.target = ("127.0.0.1", target_port)
        self.port = free_port()
        self.mode: str | None = None
        self.gen = 0
        self.lsock: socket.socket | None = None
        self.conns: set = set()
        self.lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._listen()

    def _listen(self) -> None:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(64)
        self.lsock = s
        threading.Thread(target=self._accept, args=(s,), daemon=True, name="proxy-accept").start()

    def _accept(self, s: socket.socket) -> None:
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            if self.mode == "blackhole":
                with self.lock:
                    self.conns.add(c)       # qabul qilindi, lekin hech narsa uzatilmaydi
                continue
            threading.Thread(target=self._serve, args=(c, self.gen), daemon=True).start()

    def _serve(self, c: socket.socket, gen: int) -> None:
        try:
            u = socket.create_connection(self.target, timeout=10)
        except OSError:
            c.close()
            return
        u.settimeout(None)
        with self.lock:
            self.conns.update((c, u))

        def pipe(a: socket.socket, b: socket.socket) -> None:
            try:
                while self.gen == gen:
                    d = a.recv(65536)
                    if not d:
                        break
                    b.sendall(d)
            except OSError:
                pass
            for x in (a, b):
                try:
                    x.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    x.close()
                except OSError:
                    pass
            with self.lock:
                self.conns.discard(a)
                self.conns.discard(b)

        threading.Thread(target=pipe, args=(u, c), daemon=True).start()
        pipe(c, u)

    def _drop_all(self) -> None:
        with self.lock:
            conns, self.conns = list(self.conns), set()
        for x in conns:
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                x.close()
            except OSError:
                pass

    def down(self, mode: str) -> None:
        self.gen += 1
        self.mode = mode
        self._drop_all()
        if mode == "refuse" and self.lsock is not None:
            try:
                self.lsock.shutdown(socket.SHUT_RDWR)     # Linux: kutib turgan accept() ham uyg'onadi
            except OSError:
                pass
            self.lsock.close()
            self.lsock = None

    def up(self) -> None:
        self.gen += 1
        self.mode = None
        self._drop_all()           # "qora tuynuk"dagi osilgan ulanishlar uziladi (internet qaytganda TCP ham qayta ulanadi)
        if self.lsock is None:
            self._listen()


# ============================================================================ jarayonlar
class Child:
    def __init__(self, name: str, cmd: list[str], env: dict, log_path: Path):
        self.name, self.cmd, self.env, self.log_path = name, cmd, env, log_path
        self.p: subprocess.Popen | None = None
        self.ps = None
        self.starts = 0

    def start(self) -> None:
        import psutil

        log = open(self.log_path, "ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.p = subprocess.Popen(self.cmd, cwd=str(ROOT), env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, creationflags=flags)
        log.close()
        self.ps = psutil.Process(self.p.pid)
        try:
            self.ps.cpu_percent(None)
        except Exception:  # noqa: BLE001
            pass
        self.starts += 1

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def stop(self, timeout: float = 30.0) -> None:
        if not self.alive():
            return
        self.p.terminate()
        try:
            self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(10)

    def kill(self) -> None:
        if self.alive():
            self.p.kill()
            self.p.wait(15)

    def sample(self) -> dict:
        out = {"rss": None, "threads": None, "handles": None, "cpu": None}
        if not self.alive() or self.ps is None:
            return out
        try:
            with self.ps.oneshot():
                out["rss"] = round(self.ps.memory_info().rss / 2 ** 20, 1)
                out["threads"] = self.ps.num_threads()
                out["handles"] = self.ps.num_handles() if os.name == "nt" else self.ps.num_fds()
                out["cpu"] = round(self.ps.cpu_percent(None), 1)
        except Exception:  # noqa: BLE001
            pass
        return out


class LogTail:
    """Jurnal faylidan yangi qatorlarni o'qiydi (aylanma fayl qisqarsa — boshidan). Xato vaqti qatorning o'zidan
    ("2026-10-15 06:00:50,396 ...") olinadi — o'qish vaqti emas (nosozlik oynasiga noto'g'ri tushmasin)."""

    PAT = re.compile(r"\b(ERROR|CRITICAL|Traceback)\b|Exception in ")
    TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    def __init__(self, path: Path, name: str, pat: re.Pattern | None = None):
        self.path, self.name, self.pos = path, name, 0
        self.pat = pat or self.PAT
        self.last_ts = 0.0
        self.hits: list[tuple[float, str]] = []

    def poll(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.pos:
            self.pos = 0
        if size == self.pos:
            return
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read(min(size - self.pos, 8 * 2 ** 20))
            self.pos += len(data)
        t_read = now()
        for line in data.decode("utf-8", "replace").splitlines():
            m = self.TS.match(line)
            if m:
                try:
                    self.last_ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    pass
            if self.pat.search(line):
                t = self.last_ts if self.last_ts and t_read - self.last_ts < 3600 else t_read
                self.hits.append((t, line.strip()[:300]))


# ============================================================================ sinov
class Soak:
    EDGE_CHAT = 9001
    GUARD_CHAT = 7001
    OWNER_CHAT = 7000

    def __init__(self, a):
        self.a = a
        self.dir = Path(a.dir) if a.dir else Path(tempfile.gettempdir()) / f"hushyor_soak_{time.strftime('%Y%m%d_%H%M%S')}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.edge_data = self.dir / "edge_data"
        self.cloud_data = self.dir / "cloud_data"
        self.logs = self.dir / "logs"
        for d in (self.edge_data, self.cloud_data, self.logs):
            d.mkdir(exist_ok=True)
        self.total = a.hours * H
        self.cycle = a.cycle_min * 60
        self.samples: list[dict] = []
        self.faults: list[dict] = []          # bajarilgan nosozliklar
        self.notes: list[str] = []
        self.unexpected: list[tuple[float, str]] = []
        self.health_fail: list[float] = []
        self.restart_windows: list[list[float]] = []   # edge qayta ishga tushayotgan oraliqlar (healthz kutilmaydi)
        self.recoveries: list[dict] = []
        self.device = "soak" + secrets.token_hex(4)
        self.edge_pw = "Soak-" + secrets.token_hex(6)
        self.owner_pw = "Soak-" + secrets.token_hex(6) + "A1"
        self.shutdown_token = secrets.token_hex(16)
        self.t0 = 0.0
        self.measure_from = 0.0

    # ------------------------------------------------------------------ yordamchilar
    def say(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [{fmt_dur(now() - self.t0) if self.t0 else '—'}] {msg}"
        print(line, flush=True)
        with open(self.dir / "soak.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def wait(self, fn, timeout: float, step: float = 1.0):
        end = now() + timeout
        while now() < end:
            try:
                v = fn()
                if v:
                    return v
            except Exception:  # noqa: BLE001
                pass
            time.sleep(step)
        return None

    def edge_db(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.edge_data / "hushyor.db"), timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def cloud_rows(self, sql: str, params: dict | None = None) -> list[dict]:
        from sqlalchemy import text

        with self.cloud_engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), params or {})]

    # ------------------------------------------------------------------ tayyorlash
    def setup(self) -> None:
        import httpx
        from sqlalchemy import create_engine

        a = self.a
        self.say(f"Sinov papkasi: {self.dir}")
        self.tg_edge = FakeTelegram("edge")
        self.tg_cloud = FakeTelegram("bulut")
        self.tg_edge.start()
        self.tg_cloud.start()
        self.cam = FakeCamera(Path(a.video), fps=a.cam_fps)
        self.cam.start()
        self.say(f"Soxta kamera: {self.cam.url} ({len(self.cam.frames)} kadr, aylanma)")

        # --- bulut
        self.cloud_port = free_port()
        cloud_url = a.cloud_db_url or f"sqlite:///{(self.cloud_data / 'cloud.db').as_posix()}"
        self.cloud_engine = create_engine(cloud_url, pool_pre_ping=True)
        base_env = {k: v for k, v in os.environ.items()}
        cenv = dict(base_env, CLOUD_DATA_DIR=str(self.cloud_data), CLOUD_DB_URL=cloud_url, CLOUD_HOST="127.0.0.1",
                    CLOUD_PORT=str(self.cloud_port), CLOUD_TELEGRAM_BOT_TOKEN="555:SOAKCLOUD",
                    CLOUD_TELEGRAM_API_BASE=self.tg_cloud.url, CLOUD_PUBLIC_URL="", CLOUD_SMTP_HOST="",
                    CLOUD_SMTP_USER="", CLOUD_SMTP_PASSWORD="", CLOUD_SMTP_FROM="", CLOUD_VAPID_SUB="",
                    CLOUD_EDGE_TOKENS="", CLOUD_ADMIN_TOKEN="", CLOUD_TRUSTED_PROXIES="",
                    CLOUD_BACKUP_DIR=str(self.cloud_data / "backups"), CLOUD_BACKUP_HOURS=str(a.backup_hours),
                    CLOUD_BACKUP_KEEP="3", CLOUD_MIN_FREE_GB="0.5", CLOUD_OFFLINE_MIN=str(a.offline_min),
                    CLOUD_ESCALATE_MIN="0", PYTHONUNBUFFERED="1")
        r = subprocess.run([sys.executable, "-m", "cloud.server", "--init-owner", "egasi@soak.uz", self.owner_pw, "Soak savdo"],
                           cwd=str(ROOT), env=cenv, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"bulut egasini yaratib bo'lmadi: {r.stdout} {r.stderr}")
        self.cloud = Child("bulut", [sys.executable, "-m", "cloud.server", "--host", "127.0.0.1", "--port",
                                     str(self.cloud_port)], cenv, self.logs / "cloud_console.log")
        self.cloud.start()
        self.cbase = f"http://127.0.0.1:{self.cloud_port}"
        if not self.wait(lambda: httpx.get(f"{self.cbase}/healthz", timeout=3).status_code == 200, 90):
            raise RuntimeError("bulut ishga tushmadi (logs/cloud_console.log)")
        self.proxy = NetProxy(self.cloud_port)
        self.proxy.start()

        tok = httpx.post(f"{self.cbase}/api/v1/auth/login", json={"email": "egasi@soak.uz", "password": self.owner_pw},
                         timeout=15).json()["token"]
        self.owner_h = {"Authorization": f"Bearer {tok}"}
        br = httpx.post(f"{self.cbase}/api/v1/branches", json={"name": "Soak do'kon", "device_id": self.device},
                        headers=self.owner_h, timeout=15).json()
        self.edge_token = br["device_token"]
        bid = br["branch"]["id"]
        r = httpx.post(f"{self.cbase}/api/v1/users", json={"email": "qorovul@soak.uz", "password": self.owner_pw,
                                                            "name": "Qorovul", "role": "guard", "branch_ids": [bid]},
                       headers=self.owner_h, timeout=15)
        r.raise_for_status()
        gtok = httpx.post(f"{self.cbase}/api/v1/auth/login", json={"email": "qorovul@soak.uz", "password": self.owner_pw},
                          timeout=15).json()["token"]
        guard_h = {"Authorization": f"Bearer {gtok}"}
        for hdr, chat, nm in ((guard_h, self.GUARD_CHAT, "Qorovul"), (self.owner_h, self.OWNER_CHAT, "Egasi")):
            code = httpx.post(f"{self.cbase}/api/v1/me/telegram/link", headers=hdr, timeout=15).json()["code"]
            self.tg_cloud.push_text(chat, f"/start {code}", nm)
        if not self.wait(lambda: len(self.cloud_rows("SELECT id FROM notify_targets WHERE channel='telegram'")) >= 2, 60):
            raise RuntimeError("bulut boti Telegram'ni bog'lamadi")
        self.say("Bulut tayyor: egasi, qorovul, filial, ikkala Telegram bog'langan")

        # --- edge (alohida ma'lumot papkasi; qurilma identifikatori oldindan yoziladi — bulutdagi filial bilan bir xil)
        c = sqlite3.connect(str(self.edge_data / "hushyor.db"))
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('device_id', ?)", (self.device,))
        c.commit()
        c.close()
        self.edge_port = free_port()
        eenv = dict(base_env, HUSHYOR_DATA_DIR=str(self.edge_data), HOST="127.0.0.1", PORT=str(self.edge_port),
                    ADMIN_USER="admin", ADMIN_PASSWORD=self.edge_pw, TELEGRAM_BOT_TOKEN="444:SOAKEDGE",
                    TELEGRAM_API_BASE=self.tg_edge.url, CLOUD_URL=self.proxy.url, EDGE_TOKEN=self.edge_token,
                    PUBLIC_URL="", SECRET_KEY=secrets.token_hex(24), SOAK_EVENT_SEC=str(a.event_sec),
                    HUSHYOR_NO_BROWSER="1", HUSHYOR_CTL_TOKEN=self.shutdown_token,
                    HUSHYOR_SHUTDOWN_TOKEN=self.shutdown_token, SOAK_PARENT_PID=str(os.getpid()), PYTHONUNBUFFERED="1")
        self.edge = Child("edge", [sys.executable, str(ROOT / "tests" / "soak_edge.py"),
                                   "--host", "127.0.0.1", "--port", str(self.edge_port)], eenv, self.logs / "edge_console.log")
        self.edge.start()
        self.ebase = f"http://127.0.0.1:{self.edge_port}"
        if not self.wait(lambda: httpx.get(f"{self.ebase}/healthz", timeout=3).status_code == 200, 180):
            raise RuntimeError("edge ishga tushmadi (logs/edge_console.log)")
        self.ec = httpx.Client(base_url=self.ebase, timeout=20)
        r = self.ec.post("/login", data={"username": "admin", "password": self.edge_pw, "next": "/"})
        if r.status_code != 303 or "error" in r.headers.get("location", ""):
            raise RuntimeError(f"edge'ga kirib bo'lmadi: {r.status_code} {r.headers.get('location')}")
        r = self.ec.post("/api/settings", json={"telegram_enabled": "1", "telegram_send_video": "1", "camera_alerts": "1",
                                                "camera_alert_sec": str(a.camera_alert_sec), "telegram_min_score": "0",
                                                "store_name": "Soak do'kon"})
        r.raise_for_status()
        code = self.ec.post("/api/telegram/new-code").json()["code"]
        self.tg_edge.push_text(self.EDGE_CHAT, code, "Qorovul")
        if not self.wait(lambda: self.edge_db().execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0] >= 1, 60):
            raise RuntimeError("edge boti obunachini ulamadi")
        r = self.ec.post("/api/sources", json={"name": "Soak kamera", "kind": "rtsp", "uri": self.cam.url, "enabled": "1",
                                               "start": "1"})
        r.raise_for_status()
        if a.count_line:                     # 1.9.0: sanash chizig'i — o'tishlar ham bulutga aynan yetishi tekshiriladi
            x1, y1, x2, y2 = (float(v) for v in a.count_line.split(","))
            rz = self.ec.post(f"/api/sources/{r.json()['id']}/zones",
                              json={"kind": "chiziq", "name": "Soak eshik", "points": [[x1, y1], [x2, y2]]})
            rz.raise_for_status()
        self.say("Edge tayyor: kamera qo'shildi, Telegram obunachisi ulandi, bulutga proksi orqali ulangan")
        self.edge_log = LogTail(self.edge_data / "logs" / "app.log", "edge")
        self.cloud_log = LogTail(self.cloud_data / "logs" / "cloud.log", "bulut")
        # Konsol: faqat jurnalga tushmaydigan xatolar (uvicorn "Exception in ASGI application", asyncio, oqim xatolari)
        cpat = re.compile(r"Traceback \(most recent|Exception in |Fatal Python error|MemoryError")
        self.console_logs = [LogTail(self.logs / "edge_console.log", "edge-konsol", cpat),
                             LogTail(self.logs / "cloud_console.log", "bulut-konsol", cpat)]
        for lt in self.console_logs:       # tayyorlash paytidagi qatorlar hisobga olinmaydi
            try:
                lt.pos = lt.path.stat().st_size
            except OSError:
                pass
        # Birinchi hodisa butun zanjirdan o'tishini kutish (aks holda 72 soat bekorga ketadi)
        ok = self.wait(lambda: self.cloud_rows("SELECT COUNT(*) AS n FROM events WHERE device_id=:d AND clip_file IS NOT NULL",
                                               {"d": self.device})[0]["n"] >= 1, a.event_sec + 240, 3)
        if not ok:
            raise RuntimeError("birinchi hodisa bulutga klipi bilan yetmadi — sozlamani tekshiring (logs/)")
        self.say("Zanjir ishlayapti: hodisa → rasm → klip → Telegram → bulut ✔")

    # ------------------------------------------------------------------ nosozliklar rejasi
    def plan(self) -> list[dict]:
        a, C, T = self.a, self.cycle, self.total
        quiet = max(300.0, min(1800.0, 0.1 * T))
        self.quiet = quiet
        net_d = min(max(0.15 * C, 60.0), 600.0)
        tg_d = min(max(0.08 * C, 60.0), 300.0)
        cam_d = max(2 * a.camera_alert_sec + 30.0, min(max(0.06 * C, 90.0), 240.0))
        ev: list[dict] = []
        k = 0
        t = 0.0
        while True:
            items = [(0.10, "net", net_d, "refuse" if k % 2 == 0 else "blackhole"),
                     (0.35, "telegram", tg_d, ("5xx", "hang", "slow")[k % 3]),
                     (0.55, "camera", cam_d, "")]
            for frac, kind, dur, mode in items:
                s = t + frac * C
                if s + dur <= T - quiet:
                    ev.append({"at": s, "kind": kind, "dur": dur, "mode": mode})
            t += C
            k += 1
            if t >= T - quiet:
                break
        rh = a.restart_hours * H if a.restart_hours > 0 else 0
        rpoints = [rh * i for i in range(1, 100) if rh * i <= T - quiet - 600] if rh else []
        if not rpoints and T >= 1200:
            rpoints = [0.5 * (T - quiet)]
        for i, rp in enumerate(rpoints):
            ev.append({"at": rp, "kind": "edge_restart", "dur": 0, "mode": "graceful" if i % 2 == 0 else "hard"})
            off = min(900.0, 0.25 * C)
            if rp + off + 120 <= T - quiet:
                ev.append({"at": rp + off, "kind": "cloud_restart", "dur": 0, "mode": "graceful"})
        # 2-bosqich darvozasi: "internet 1 soat uzilsa ma'lumot yo'qolmaydi" — bitta uzoq uzilish (sukut 6+ soatli
        # sinovda 60 daqiqa, run'ning taxminan 40% ida); u bilan ustma-ust tushgan qisqa internet uzilishlari olib tashlanadi
        long_s = (a.long_outage_min if a.long_outage_min >= 0 else (60.0 if T >= 6 * H else 0.0)) * 60
        if long_s > 0:
            at = round(0.4 * (T - quiet))
            if at + long_s + 300 <= T - quiet:
                ev = [e for e in ev if not (e["kind"] == "net" and e["at"] < at + long_s + 120 and e["at"] + e["dur"] > at - 120)]
                ev.append({"at": at, "kind": "net", "dur": long_s, "mode": "refuse", "long": True})
        ev.sort(key=lambda e: e["at"])
        return ev

    def _apply(self, f: dict, start: bool) -> None:
        k = f["kind"]
        if k == "net":
            if start:
                self.proxy.down(f["mode"])
            else:
                self.proxy.up()
        elif k == "telegram":
            for tg in (self.tg_edge, self.tg_cloud):
                tg.mode = f["mode"] if start else None
        elif k == "camera":
            if start:
                self.cam.stop()
            else:
                self.cam.start()
        elif k == "edge_restart" and start:
            win = [now(), 0.0]
            self.restart_windows.append(win)
            if f["mode"] == "graceful":
                try:
                    self.ec.post("/healthz/shutdown", timeout=5,
                                 headers={"X-Hushyor-Token": self.shutdown_token, "X-Shutdown-Token": self.shutdown_token})
                    self.edge.p.wait(40)
                except Exception:  # noqa: BLE001
                    self.edge.stop()
            else:
                self.edge.kill()
            self.edge.start()
            import httpx

            ok = self.wait(lambda: httpx.get(f"{self.ebase}/healthz", timeout=3).status_code == 200, 240, 2)
            win[1] = now() + 5
            f["recovered_sec"] = round(now() - win[0], 1) if ok else None
            self._relogin()
        elif k == "cloud_restart" and start:
            import httpx

            t1 = now()
            self.cloud.stop()
            self.cloud.start()
            ok = self.wait(lambda: httpx.get(f"{self.cbase}/healthz", timeout=3).status_code == 200, 120, 1)
            f["recovered_sec"] = round(now() - t1, 1) if ok else None

    def _relogin(self) -> None:
        try:
            self.ec.cookies.clear()
            self.ec.post("/login", data={"username": "admin", "password": self.edge_pw, "next": "/"})
        except Exception:  # noqa: BLE001
            pass

    def in_fault(self, t: float, kinds: tuple = ("net", "telegram", "camera", "edge_restart", "cloud_restart"),
                 margin: float = 120.0) -> bool:
        for f in self.faults:
            if f["kind"] not in kinds:
                continue
            s, e = f["t_start"], f.get("t_end") or (f["t_start"] + max(f["dur"], f.get("recovered_sec") or 60))
            if s - 5 <= t <= e + margin:
                return True
        return False

    # ------------------------------------------------------------------ o'lchash
    def sample(self) -> dict:
        import httpx

        t = now()
        row: dict = {"t": round(t - self.t0, 1)}
        es, cs = self.edge.sample(), self.cloud.sample()
        for k, v in es.items():
            row[f"edge_{k}"] = v
        for k, v in cs.items():
            row[f"cloud_{k}"] = v
        try:
            row["healthz"] = int(httpx.get(f"{self.ebase}/healthz", timeout=5).status_code == 200)
        except Exception:  # noqa: BLE001
            row["healthz"] = 0
        if not row["healthz"] and not any(w[0] <= t <= (w[1] or t + 1) for w in self.restart_windows):
            self.health_fail.append(t)
        try:
            with self.edge_db() as c:
                row["outbox_wait"] = c.execute("SELECT COUNT(*) FROM outbox WHERE failed=0").fetchone()[0]
                row["outbox_failed"] = c.execute("SELECT COUNT(*) FROM outbox WHERE failed=1").fetchone()[0]
                row["edge_events"] = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        except Exception:  # noqa: BLE001
            row.update(outbox_wait=None, outbox_failed=None, edge_events=None)
        try:
            row["cloud_events"] = self.cloud_rows("SELECT COUNT(*) AS n FROM events WHERE device_id=:d",
                                                  {"d": self.device})[0]["n"]
            row["notify_pending"] = self.cloud_rows("SELECT COUNT(*) AS n FROM notify_outbox WHERE status='pending'")[0]["n"]
        except Exception:  # noqa: BLE001
            row.update(cloud_events=None, notify_pending=None)
        row["edge_disk_mb"] = round(dir_mb(self.edge_data), 1)
        row["cloud_disk_mb"] = round(dir_mb(self.cloud_data), 1)
        row["tg_edge_ok"] = len(self.tg_edge.ok_calls())
        row["tg_cloud_ok"] = len(self.tg_cloud.ok_calls())
        row["faults"] = ",".join(sorted({f["kind"] for f in self.faults if f.get("t_end") is None and f["dur"] > 0}))
        for lt in self.logtails():
            lt.poll()
        self.samples.append(row)
        return row

    def logtails(self) -> list:
        return [self.edge_log, self.cloud_log, *getattr(self, "console_logs", [])]

    def watch_recovery(self, f: dict) -> None:
        """Internet qaytgach: edge navbati qancha vaqtda bo'shaydi."""
        def run():
            t_end = f["t_end"]
            while now() - t_end < 1800:
                try:
                    with self.edge_db() as c:
                        n = c.execute("SELECT COUNT(*) FROM outbox WHERE failed=0 AND kind IN ('event','clip')").fetchone()[0]
                    if n == 0:
                        f["drain_sec"] = round(now() - t_end, 1)
                        return
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(3)
            f["drain_sec"] = None
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ asosiy sikl
    def run(self) -> int:
        self.t0 = now()
        try:
            self.setup()
        except Exception as e:  # noqa: BLE001
            self.say(f"TAYYORLASH XATOSI: {e}")
            self.cleanup()
            return 2
        a = self.a
        need_gb = self.total / max(a.event_sec, 1) * 2.2 / 1024 + 1.0     # ~1 MB klip edge'da va bulutda + jurnallar
        free_gb = shutil.disk_usage(self.dir).free / 2 ** 30
        if free_gb < need_gb:
            self.say(f"DIQQAT: diskda {free_gb:.1f} GB bo'sh, sinovga taxminan {need_gb:.1f} GB kerak (--event-sec ni oshiring)")
        self.t0 = now()
        plan = self.plan()
        self.say(f"Reja: {fmt_dur(self.total)}, {len(plan)} ta nosozlik, oxirgi {fmt_dur(self.quiet)} — tinch (tekshiruv)")
        with open(self.dir / "reja.json", "w", encoding="utf-8") as fp:
            json.dump(plan, fp, ensure_ascii=False, indent=1)
        self.measure_from = min(600.0, max(120.0, 0.08 * self.total))
        active: list[dict] = []
        try:
            try:
                self._loop(plan, active)
            except KeyboardInterrupt:
                # Ctrl+C: sinov shu yerda tugaydi, lekin hisobot baribir yoziladi (masalan, 24 soatdan keyin to'xtatilsa)
                self.total = now() - self.t0
                self.say(f"To'xtatildi (Ctrl+C) — {fmt_dur(self.total)} dan keyin; qisman hisobot tayyorlanmoqda")
            for f in active:
                if not f.get("t_end"):
                    self._apply(f, False)
                    f["t_end"] = now()
            # Yakuniy bo'shash: navbatlar (edge → bulut, bulut → Telegram) bo'shashini kutish
            self.say("Sinov vaqti tugadi — navbatlar bo'shashi kutilmoqda")
            self.wait(lambda: self._drained(), a.drain_min * 60, 5)
            time.sleep(min(60, a.sample_sec))
            self.sample()
            return self.finish()
        finally:
            self.cleanup()

    def _loop(self, plan: list[dict], active: list[dict]) -> None:
        a = self.a
        next_sample, pi, last_print = 0.0, 0, 0.0
        while True:
            t = now() - self.t0
            if t >= self.total:
                return
            while pi < len(plan) and plan[pi]["at"] <= t:
                f = dict(plan[pi])
                pi += 1
                f["t_start"] = now()
                self.faults.append(f)
                self.say(f"NOSOZLIK: {f['kind']} {f['mode']} " + (f"({fmt_dur(f['dur'])})" if f["dur"] else ""))
                self._apply(f, True)
                if f["dur"] > 0:
                    active.append(f)
                else:
                    f["t_end"] = now()
                    self.say(f"  tiklandi: {f.get('recovered_sec')} s")
            for f in list(active):
                if now() - f["t_start"] >= f["dur"]:
                    self._apply(f, False)
                    f["t_end"] = now()
                    active.remove(f)
                    self.say(f"TIKLANDI: {f['kind']}")
                    if f["kind"] == "net":
                        self.watch_recovery(f)
            for ch in (self.edge, self.cloud):
                if not ch.alive():
                    self.unexpected.append((now(), ch.name))
                    self.say(f"!!! {ch.name} kutilmaganda to'xtadi (kod {ch.p.returncode}) — qayta ishga tushirilmoqda")
                    ch.start()
                    if ch is self.edge:
                        self.restart_windows.append([now(), now() + 120])
            if t >= next_sample:
                row = self.sample()
                next_sample = t + a.sample_sec
                if now() - last_print >= max(a.sample_sec, 300):
                    last_print = now()
                    self.say(f"edge {row['edge_rss']} MB/{row['edge_threads']} oqim, bulut {row['cloud_rss']} MB, "
                             f"navbat {row['outbox_wait']}, hodisa {row['edge_events']}/{row['cloud_events']}, "
                             f"disk {row['edge_disk_mb']:.0f}+{row['cloud_disk_mb']:.0f} MB")
            time.sleep(1.0)

    def _drained(self) -> bool:
        with self.edge_db() as c:
            if c.execute("SELECT COUNT(*) FROM outbox WHERE failed=0").fetchone()[0] > 0:
                return False
        return self.cloud_rows("SELECT COUNT(*) AS n FROM notify_outbox WHERE status='pending'")[0]["n"] == 0

    def cleanup(self) -> None:
        for ch in (getattr(self, "edge", None), getattr(self, "cloud", None)):
            if ch is not None:
                try:
                    ch.stop(20)
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.cam.stop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ tekshirish
    def check_counts(self, chk) -> None:
        """1.9.0: har o'tish (counts) → daqiqalik yig'indi (metrics_minute) → bulut: aynan bir xil, uzilish va qayta
        ishga tushishlarda ham. Oxirgi 3 daqiqa (hali yuborilmagan bo'lishi mumkin) hisobga olinmaydi."""
        cut = int((time.time() - 180) // 60) * 60
        with self.edge_db() as c:
            raw = {(int(r["m"]), int(r["source_id"])): (int(r["i"] or 0), int(r["o"] or 0)) for r in c.execute(
                "SELECT CAST(t / 60 AS INTEGER) * 60 AS m, source_id, SUM(dir='in' AND staff=0 AND offhours=0) AS i, "
                "SUM(dir='out' AND staff=0 AND offhours=0) AS o FROM counts WHERE t < ? GROUP BY 1, 2", (cut,))}
            n_cross = c.execute("SELECT COUNT(*) FROM counts WHERE t < ?", (cut,)).fetchone()[0]
            loc = {}
            for r in c.execute("SELECT minute, source_id, extra FROM metrics_minute WHERE source_id > 0 AND minute < ?",
                               (cut,)):
                try:
                    d = json.loads(r["extra"] or "{}")
                except ValueError:
                    d = {}
                if "in" in d:
                    loc[(int(r["minute"]), int(r["source_id"]))] = (int(d["in"]), int(d.get("out", 0)))
        cl = {}
        for r in self.cloud_rows("SELECT m.minute, m.source_id, m.data FROM metrics_minute m JOIN branches b "
                                 "ON b.id = m.branch_id WHERE b.device_id = :d AND m.source_id > 0 AND m.minute < :c",
                                 {"d": self.device, "c": cut}):
            d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"] or "{}")
            if "in" in d:
                cl[(int(r["minute"]), int(r["source_id"]))] = (int(d["in"]), int(d.get("out", 0)))
        bad_loc = sorted(k for k in raw if loc.get(k) != raw[k])
        bad_cl = sorted(k for k in loc if cl.get(k) != loc[k])
        tot = lambda m: (sum(v[0] for v in m.values()), sum(v[1] for v in m.values()))  # noqa: E731
        chk("Sanash: har o'tish daqiqalik yig'indiga tushdi (qayta ishga tushishda ham)", n_cross > 0 and not bad_loc,
            f"{n_cross} o'tish, kirdi/chiqdi {tot(raw)}; mos emas: {len(bad_loc)} daqiqa {bad_loc[:5]}")
        chk("Sanash: bulutdagi kirdi/chiqdi edge bilan aynan bir xil (uzilishlarda yo'qolmadi, takrorlanmadi)",
            bool(loc) and not bad_cl, f"edge {tot(loc)}, bulut {tot(cl)}; mos emas: {len(bad_cl)} daqiqa {bad_cl[:5]}")

    def finish(self) -> int:
        checks: list[dict] = []

        def chk(name: str, ok: bool | None, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        S = [s for s in self.samples if s["t"] >= self.measure_from]
        # --- jarayonlar
        chk("Edge kutilmaganda to'xtamadi", not any(n == "edge" for _, n in self.unexpected),
            f"{sum(1 for _, n in self.unexpected if n == 'edge')} marta")
        chk("Bulut kutilmaganda to'xtamadi", not any(n == "bulut" for _, n in self.unexpected),
            f"{sum(1 for _, n in self.unexpected if n == 'bulut')} marta")
        chk("Panel doim javob berdi (/healthz)", not self.health_fail,
            f"{len(self.health_fail)} ta o'lchovda javob yo'q (rejali qayta ishga tushirishdan tashqari)")

        # --- xotira (qayta ishga tushirishlar orasidagi eng uzun bo'lak)
        self.mem = {}
        for who, restarts in (("edge", [w[0] - self.t0 for w in self.restart_windows]),
                              ("cloud", [f["t_start"] - self.t0 for f in self.faults if f["kind"] == "cloud_restart"])):
            cuts = [self.measure_from] + sorted(r for r in restarts if r > self.measure_from) + [self.total + 1e9]
            best = []
            for s0, s1 in zip(cuts, cuts[1:]):
                seg = [s for s in S if s0 + (300 if s0 > self.measure_from else 0) <= s["t"] < s1 and s.get(f"{who}_rss")]
                if len(seg) > len(best):
                    best = seg
            info: dict = {"n": len(best)}
            if len(best) >= 5:
                span = (best[-1]["t"] - best[0]["t"]) / H
                k = max(1, len(best) // 10)
                base = statistics.median(s[f"{who}_rss"] for s in best[:k])
                end = statistics.median(s[f"{who}_rss"] for s in best[-k:])
                sl = slope_per_hour([(s["t"], s[f"{who}_rss"]) for s in best])
                th0 = max(s[f"{who}_threads"] or 0 for s in best[:k])
                th1 = max(s[f"{who}_threads"] or 0 for s in best[-k:])
                hd0 = statistics.median(s[f"{who}_handles"] or 0 for s in best[:k])
                hd1 = statistics.median(s[f"{who}_handles"] or 0 for s in best[-k:])
                info.update(span_h=round(span, 2), base=round(base, 1), end=round(end, 1),
                            slope=round(sl, 2) if sl is not None else None, threads0=th0, threads1=th1,
                            handles0=hd0, handles1=hd1, max=max(s[f"{who}_rss"] for s in best))
                name = "Edge" if who == "edge" else "Bulut"
                judged = span >= self.a.min_leak_hours
                grow_ok = end <= base * 1.2 + 30 and (sl is None or sl <= self.a.max_mem_slope)
                chk(f"{name}: xotira oqishi yo'q", grow_ok if judged else None,
                    f"{base:.0f} → {end:.0f} MB, {sl:+.1f} MB/soat, {span:.1f} soatlik bo'lakda"
                    + ("" if judged else f" (≥ {self.a.min_leak_hours:g} soat kerak — faqat ma'lumot uchun)"))
                chk(f"{name}: oqimlar va dastaklar ko'paymayapti",
                    (th1 <= th0 + 8 and hd1 <= hd0 * 1.3 + 30) if judged else None,
                    f"oqim {th0} → {th1}, dastak {hd0:.0f} → {hd1:.0f}")
            self.mem[who] = info

        # --- edge → bulut
        with self.edge_db() as c:
            evs = [dict(r) for r in c.execute("SELECT id, created_at, clip_path, score FROM events ORDER BY id")]
            ob_wait = c.execute("SELECT COUNT(*) FROM outbox WHERE failed=0").fetchone()[0]
            ob_fail = [dict(r) for r in c.execute("SELECT kind, key, last_error FROM outbox WHERE failed=1 LIMIT 20")]
            nl = [dict(r) for r in c.execute("SELECT event_id, stage, ok, t_event, t_sent, chat_id FROM notify_log")]
        chk("Edge navbati bo'shadi (internet qaytgach hammasi yetkazildi)", ob_wait == 0, f"qolgan: {ob_wait}")
        chk("Bulut hech narsani rad etmadi", not ob_fail,
            "; ".join(f"{r['kind']} {r['key']}: {r['last_error']}" for r in ob_fail[:5]) or "0")
        cev = {r["local_id"]: r for r in self.cloud_rows(
            "SELECT local_id, clip_file, created_at, received_at FROM events WHERE device_id=:d", {"d": self.device})}
        miss = [e["id"] for e in evs if e["id"] not in cev]
        chk("Har edge hodisasi bulutda bor (yo'qolgan yo'q)", not miss and len(evs) > 0,
            f"edge {len(evs)}, bulut {len(cev)}, yo'q: {miss[:10]}")
        if self.a.count_line:
            self.check_counts(chk)
        clip_miss = [e["id"] for e in evs if e["clip_path"] and e["id"] in cev and not cev[e["id"]]["clip_file"]]
        no_clip = [e["id"] for e in evs if not e["clip_path"]]
        chk("Har klip bulutga yuklandi", not clip_miss, f"yuklanmagan: {clip_miss[:10]}")
        chk("Har hodisaga klip yaratildi", not no_clip or len(no_clip) <= max(1, len(evs) // 100),
            f"klipsiz: {len(no_clip)} ta {no_clip[:10]}")
        fdisk = [p for p in (self.cloud_data / "clips" / self.device).glob("*.mp4")] if (self.cloud_data / "clips").exists() else []
        chk("Bulutdagi klip fayllari diskda bor", len(fdisk) >= len([1 for r in cev.values() if r["clip_file"]]) * 0.99,
            f"fayl {len(fdisk)}, yozuv {sum(1 for r in cev.values() if r['clip_file'])}")

        # --- edge Telegram: har hodisa aynan bir marta (+ klip aynan bir marta). "slow" oynasida (xabar yetgan, lekin
        # javob kechikkan) takror — Bot API'da bundan qochib bo'lmaydi: alohida "ruxsat etilgan takror" deb sanaladi
        ok_calls = self.tg_edge.ok_calls(self.EDGE_CHAT)
        alert_c: dict[int, list[dict]] = {}
        video_c: dict[int, list[dict]] = {}
        for cl in ok_calls:
            m = re.search(r"Hodisa #(\d+)", cl["text"])
            if not m:
                continue
            eid = int(m.group(1))
            if cl["method"] in ("sendPhoto", "sendMessage") or (cl["method"] == "sendVideo" and "klipi" not in cl["text"]):
                alert_c.setdefault(eid, []).append(cl)
            if cl["method"] in ("editMessageMedia", "sendVideo"):
                video_c.setdefault(eid, []).append(cl)

        def split_dups(d: dict[int, list[dict]]) -> tuple[list[int], list[int]]:
            hard = [k for k, v in d.items() if len(v) > 1 and not any(c["mode"] == "slow" for c in v)]
            soft = [k for k, v in d.items() if len(v) > 1 and any(c["mode"] == "slow" for c in v)]
            return hard, soft
        lost = [e["id"] for e in evs if not alert_c.get(e["id"])]
        dup, dup_ok = split_dups(alert_c)
        chk("Edge Telegram: har hodisa xabari yetdi (yo'qolgan yo'q)", not lost,
            f"{len(evs) - len(lost)}/{len(evs)}; yetmagan: {lost[:15]}")
        chk("Edge Telegram: takroriy xabar yo'q", not dup,
            f"takrorlangan: {dup[:15]}; Telegram javobi kechikkanidagi (ruxsat etilgan) takror: {len(dup_ok)}")
        vlost = [e["id"] for e in evs if e["clip_path"] and not video_c.get(e["id"])]
        vdup, vdup_ok = split_dups(video_c)
        chk("Edge Telegram: har klip videosi yetdi", not vlost, f"yetmagan: {len(vlost)} ta {vlost[:15]}")
        chk("Edge Telegram: video takrorlanmadi", not vdup, f"takrorlangan: {vdup[:15]}; ruxsat etilgan: {len(vdup_ok)}")

        def in_win(c: dict, t0: float, t1: float) -> bool:
            return t0 <= c["t"] <= t1

        # --- kamera nosozligi xabarlari: HAR uzilish uchun aynan bitta "ishlamayapti" va bitta "tiklandi"
        cams = [f for f in self.faults if f["kind"] == "camera"]
        downs = [c for c in ok_calls if "Kamera ishlamayapti" in c["text"]]
        ups = [c for c in ok_calls if "kamera tiklandi" in c["text"].lower()]
        bad_cam = []
        for f in cams:
            nd = sum(1 for c in downs if in_win(c, f["t_start"], f["t_end"] + 30))
            nu = sum(1 for c in ups if in_win(c, f["t_end"], f["t_end"] + 120))
            if nd != 1 or nu != 1:
                bad_cam.append(f"{fmt_dur(f['t_start'] - self.t0)}: {nd}/{nu}")
        extra = len(downs) + len(ups) - 2 * (len(cams) - len(bad_cam))
        chk("Kamera uzilishi va tiklanishi haqida xabar (edge, har uzilishga bittadan)", not bad_cam and extra == 0,
            f"uzilish {len(cams)}; mos kelmagan: {bad_cam[:6]}; ortiqcha xabar: {max(extra, 0)}")

        # --- bulut → Telegram (qorovul): yangi hodisa uchun aynan bir marta, hodisa bo'yicha (tg_sent: uid ↔ message_id).
        # 10 daqiqadan eski (navbatdan kech kelgan) hodisa uchun xabar yo'q — loyiha qoidasi
        g_ok = [c for c in self.tg_cloud.ok_calls(self.GUARD_CHAT) if "Shubhali harakat" in c["text"]]
        sent_rows = self.cloud_rows("SELECT uid, message_id FROM tg_sent WHERE kind='event' AND chat_id=:c AND "
                                    "message_id IS NOT NULL", {"c": str(self.GUARD_CHAT)})
        by_mid = {int(r["message_id"]): r["uid"] for r in sent_rows}
        per_uid: dict[str, int] = {}
        orphan = []
        for c in g_ok:
            u = by_mid.get(int(c["mid"] or -1))
            if u is None:
                orphan.append(c)
            else:
                per_uid[u] = per_uid.get(u, 0) + 1
        must_u = [f"{self.device}:{lid}" for lid, r in cev.items() if r["received_at"]
                  and r["received_at"] - r["created_at"] <= NOTIFY_MAX_AGE - 30]
        never_u = [f"{self.device}:{lid}" for lid, r in cev.items() if r["received_at"]
                   and r["received_at"] - r["created_at"] > NOTIFY_MAX_AGE + 30]
        g_lost = [u for u in must_u if not per_uid.get(u)]
        g_dup = [u for u, n in per_uid.items() if n > 1]
        g_stale = [u for u in never_u if per_uid.get(u)]
        orphan_hard = [c for c in orphan if c["mode"] != "slow"]
        rows = self.cloud_rows("SELECT status, COUNT(*) AS n FROM notify_outbox WHERE kind='event' GROUP BY status")
        st = {r["status"]: r["n"] for r in rows}
        chk("Bulut Telegram: har yangi hodisa qorovulga aynan bir marta (hodisa bo'yicha)",
            not g_lost and not g_dup and not g_stale and not orphan_hard and not st.get("failed"),
            f"kerak {len(must_u)}, yetmagan {g_lost[:5]}, takror {g_dup[:5]}, eski hodisaga xabar {g_stale[:5]}, "
            f"bog'lanmagan xabar {len(orphan_hard)} (+{len(orphan) - len(orphan_hard)} ruxsat etilgan); navbat {st}")

        # --- bulut → egasi: do'kon aloqasi uzildi/tiklandi — har uzilish bo'yicha. Edge har 60 s da "salom" yuboradi:
        # uzilish offline chegarasidan 2 daqiqa uzun bo'lsa xabar SHART, biroz qisqa bo'lsa — bo'lishi mumkin;
        # uzilishlardan tashqarida (masalan, bulut qayta ishga tushgach) — bo'lmasligi kerak
        o_calls = self.tg_cloud.ok_calls(self.OWNER_CHAT)
        off_s = self.a.offline_min * 60
        offl = [c for c in o_calls if "aloqa uzildi" in c["text"].lower()]
        onl = [c for c in o_calls if "aloqa tiklandi" in c["text"].lower()]
        bad_net, used = [], set()
        for f in (f for f in self.faults if f["kind"] == "net"):
            nd = [c for c in offl if in_win(c, f["t_start"], f["t_end"] + 90)]
            nu = [c for c in onl if in_win(c, f["t_end"], f["t_end"] + 240)]
            used.update(id(c) for c in nd + nu)
            must = f["dur"] >= off_s + 120
            may = off_s - 70 < f["dur"] < off_s + 120
            okf = (len(nd) == 1 and len(nu) == 1) if must else ((len(nd) == len(nu) <= 1) if may else not nd and not nu)
            if not okf:
                bad_net.append(f"{fmt_dur(f['t_start'] - self.t0)} ({fmt_dur(f['dur'])}): {len(nd)}/{len(nu)}")
        false_alarm = [c for c in offl + onl if id(c) not in used]
        chk("Bulut: do'kon aloqasi uzildi/tiklandi xabari (egasiga, har uzilish bo'yicha; soxta xabar yo'q)",
            not bad_net and not false_alarm, f"mos kelmagan: {bad_net[:6]}; uzilishsiz xabar: {len(false_alarm)}")

        # --- disk
        if len(S) >= 5 and evs:
            third = max(1, len(S) // 3)
            a0, a1 = S[third - 1], S[-1]
            de = max(1, (a1["edge_events"] or 0) - (S[0]["edge_events"] or 0))
            per_ev = (a1["edge_disk_mb"] - S[0]["edge_disk_mb"]) / de
            logs_mb = dir_mb(self.edge_data / "logs") + dir_mb(self.cloud_data / "logs")
            tmp = [p.name for p in self.edge_data.rglob("*") if p.suffix in (".tmp", ".part")]
            bk = list((self.cloud_data / "backups").glob("cloud-*.db")) if (self.cloud_data / "backups").exists() else []
            chk("Disk chegaralangan (jurnallar aylanadi, vaqtinchalik fayl qolmaydi, zaxiralar ≤ 3)",
                logs_mb < 40 and not tmp and len(bk) <= 3,
                f"jurnallar {logs_mb:.1f} MB, vaqtinchalik {len(tmp)}, zaxira {len(bk)}; hodisaga {per_ev:.2f} MB")
            self.disk = {"per_event_mb": round(per_ev, 3), "edge_mb": a1["edge_disk_mb"], "cloud_mb": a1["cloud_disk_mb"],
                         "backups": len(bk), "logs_mb": round(logs_mb, 1)}
            _ = a0
        else:
            self.disk = {}

        # --- kutilmagan xatolar (nosozlik oynalaridan tashqarida)
        bad = [(t, f"{lt.name}: {ln}") for lt in self.logtails() for t, ln in lt.hits
               if not self.in_fault(t)]
        chk("Nosozliklardan tashqarida jurnalda xato (ERROR) yo'q", not bad,
            f"{len(bad)} ta" + (": " + bad[0][1][:200] if bad else ""))

        # --- kechikishlar (nosozliksiz vaqtdagi)
        lat_tg = [r["t_sent"] - r["t_event"] for r in nl if r["stage"] in ("alert", "full") and r["ok"] and r["t_event"]
                  and not self.in_fault(r["t_event"])]
        lat_cloud = [r["received_at"] - r["created_at"] for r in cev.values() if r["received_at"]
                     and not self.in_fault(r["created_at"])]
        self.lat = {"tg_p50": pct(lat_tg, 50), "tg_p95": pct(lat_tg, 95), "cloud_p50": pct(lat_cloud, 50),
                    "cloud_p95": pct(lat_cloud, 95)}
        drains = [(f.get("drain_sec"), f) for f in self.faults if f["kind"] == "net"]
        chk("Internet qaytgach navbat 15 daqiqada bo'shadi (1 soatlik uzilishdan keyin ham)",
            all(d is not None and d <= 900 for d, _ in drains) if drains else None,
            "bo'shash vaqtlari: " + ", ".join(("—" if d is None else f"{d:.0f} s") + (" (1 soatlik)" if f.get("long") else "")
                                              for d, f in drains))
        longs = [f for f in self.faults if f.get("long")]
        if longs:
            f = longs[0]
            n_in = sum(1 for e in evs if f["t_start"] <= e["created_at"] <= f["t_end"])
            lost_in = [e["id"] for e in evs if f["t_start"] <= e["created_at"] <= f["t_end"] and e["id"] not in cev]
            chk(f"Darvoza: internet {fmt_dur(f['dur'])} uzilganda ma'lumot yo'qolmadi", not lost_in and n_in > 0,
                f"uzilish paytidagi hodisalar {n_in}, bulutga yetmagan {len(lost_in)}")

        passed = all(c["ok"] is not False for c in checks)
        self.write_report(checks, passed, evs)
        self.say(("SINOV O'TDI ✅" if passed else "SINOV YIQILDI ❌") + f" — hisobot: {self.dir / 'hisobot.md'}")
        for c in checks:
            mark = "✅" if c["ok"] else ("ℹ️" if c["ok"] is None else "❌")
            print(f"  {mark} {c['name']}: {c['detail']}")
        return 0 if passed else 1

    # ------------------------------------------------------------------ hisobot
    def write_report(self, checks: list[dict], passed: bool, evs: list[dict]) -> None:
        for tg in (self.tg_edge, self.tg_cloud):     # tahlil uchun: soxta Telegram'ga kelgan barcha so'rovlar
            with open(self.dir / f"telegram_{tg.name}.jsonl", "w", encoding="utf-8") as fp:
                for c in list(tg.calls):
                    fp.write(json.dumps(dict(c, t_rel=round(c["t"] - self.t0, 1)), ensure_ascii=False) + "\n")
        with open(self.dir / "samples.csv", "w", newline="", encoding="utf-8") as fp:
            keys = list(self.samples[0].keys()) if self.samples else []
            w = csv.DictWriter(fp, fieldnames=keys)
            w.writeheader()
            for s in self.samples:
                w.writerow(s)
        summary = {"passed": passed, "hours": self.a.hours, "dir": str(self.dir), "checks": checks, "memory": self.mem,
                   "disk": self.disk, "latency": self.lat, "events": len(evs), "faults": self.faults,
                   "python": sys.version.split()[0], "platform": sys.platform, "cpu_count": os.cpu_count()}
        (self.dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        L = [f"# Hushyor AI — uzoq barqarorlik sinovi: {'O`TDI ✅' if passed else 'YIQILDI ❌'}", "",
             f"- Davomiyligi: **{fmt_dur(self.total)}** (o'lchov {fmt_dur(self.measure_from)} qizishdan keyin), "
             f"o'lchov har {self.a.sample_sec:g} s, kompyuter: {sys.platform}, {os.cpu_count()} yadro, Python {sys.version.split()[0]}",
             f"- Hodisalar: **{len(evs)}** (har {self.a.event_sec:g} s da soxta signal, haqiqiy yo'ldan: rasm → klip → Telegram → bulut)",
             f"- Nosozliklar: **{len(self.faults)}** ta (internet, Telegram, kamera, qayta ishga tushirish)", "",
             "| Tekshiruv | Natija | Tafsilot |", "|---|---|---|"]
        for c in checks:
            mark = "✅" if c["ok"] else ("ℹ️ ma'lumot" if c["ok"] is None else "❌")
            L.append(f"| {c['name']} | {mark} | {c['detail'].replace('|', '/')} |")
        L += ["", "## Resurslar", "", "| Jarayon | Boshida | Oxirida | O'zgarish | Oqimlar | Dastaklar |", "|---|---|---|---|---|---|"]
        for who, nm in (("edge", "Edge"), ("cloud", "Bulut")):
            m = self.mem.get(who) or {}
            if m.get("base") is not None:
                L.append(f"| {nm} | {m['base']} MB | {m['end']} MB | {m['slope']:+} MB/soat ({m['span_h']} soat) | "
                         f"{m['threads0']} → {m['threads1']} | {m['handles0']:.0f} → {m['handles1']:.0f} |")
        cpu = [s["edge_cpu"] for s in self.samples if s.get("edge_cpu") is not None]
        if cpu:
            L.append(f"\nEdge CPU: o'rtacha {statistics.fmean(cpu):.0f}%, eng ko'p {max(cpu):.0f}% (100% = bitta yadro).")
        if self.disk:
            L.append(f"Disk: edge {self.disk['edge_mb']:.0f} MB, bulut {self.disk['cloud_mb']:.0f} MB; bitta hodisa ≈ "
                     f"{self.disk['per_event_mb']:.2f} MB; jurnallar {self.disk['logs_mb']} MB; zaxiralar {self.disk['backups']}.")
        lt = self.lat
        f2 = lambda v: "—" if v is None else f"{v:.2f} s"  # noqa: E731
        L += ["", "## Kechikish (nosozliksiz vaqtda)", "",
              f"- Signal → edge Telegram xabari: p50 {f2(lt.get('tg_p50'))}, p95 {f2(lt.get('tg_p95'))}",
              f"- Signal → bulutga yetishi: p50 {f2(lt.get('cloud_p50'))}, p95 {f2(lt.get('cloud_p95'))}", "",
              "## Nosozliklar", "", "| Vaqt | Tur | Rejim | Davomiyligi | Tiklanish / navbat bo'shashi |", "|---|---|---|---|---|"]
        for f in self.faults:
            rec = f.get("drain_sec", f.get("recovered_sec"))
            L.append(f"| {fmt_dur(f['t_start'] - self.t0)} | {f['kind']} | {f['mode']} | {fmt_dur(f['dur']) if f['dur'] else '—'} | "
                     f"{'—' if rec is None else f'{rec:.0f} s'} |")
        hits = [(t, f"{lt.name}: {ln}") for lt in self.logtails() for t, ln in lt.hits]
        if hits:
            L += ["", "## Jurnaldagi xatolar", "", "| Vaqt | Nosozlik vaqtidami | Qator |", "|---|---|---|"]
            for t, ln in hits[:60]:
                L.append(f"| {fmt_dur(t - self.t0)} | {'ha' if self.in_fault(t) else '**yo`q**'} | `{ln[:180].replace('|', '/')}` |")
        L += ["", "Diqqat: bu sinov ishonchlilikni (yetkazish, tiklanish, xotira) tekshiradi. Aniqlik (o'g'rilikni topish) — "
              "alohida: panel → Aniqlik sinovi. Soxta signal haqiqiy kamera tasvirida emas, namunaviy videoda.",
              "", f"Fayllar: `{self.dir}` — samples.csv (har o'lchov), summary.json, logs/, edge_data/, cloud_data/."]
        (self.dir / "hisobot.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        (self.dir / "hisobot.html").write_text(html_report("\n".join(L), self.samples), encoding="utf-8")


# ============================================================================ HTML (grafiklar, tashqi kutubxonasiz)
def svg_chart(samples: list[dict], series: list[tuple[str, str, str]], title: str) -> str:
    W, Hh, P = 860, 220, 40
    pts_all = [(s["t"], s.get(k)) for s in samples for k, _, _ in series if s.get(k) is not None]
    if len(pts_all) < 2:
        return ""
    t0, t1 = min(p[0] for p in pts_all), max(p[0] for p in pts_all)
    vmax = max(p[1] for p in pts_all) or 1
    out = [f'<h3>{html.escape(title)}</h3><svg viewBox="0 0 {W} {Hh}" width="100%" role="img">',
           f'<line x1="{P}" y1="{Hh - P}" x2="{W - 10}" y2="{Hh - P}" stroke="#999"/>',
           f'<text x="4" y="{P - 6}" font-size="11">{vmax:.0f}</text><text x="4" y="{Hh - P}" font-size="11">0</text>',
           f'<text x="{W - 90}" y="{Hh - 12}" font-size="11">{(t1 - t0) / 3600:.1f} soat</text>']
    for i, (k, label, color) in enumerate(series):
        p = [(s["t"], s[k]) for s in samples if s.get(k) is not None]
        if len(p) < 2:
            continue
        xy = " ".join(f"{P + (t - t0) / max(t1 - t0, 1) * (W - P - 10):.1f},{Hh - P - v / vmax * (Hh - 2 * P):.1f}" for t, v in p)
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{xy}"/>')
        out.append(f'<text x="{P + 10 + i * 170}" y="14" font-size="12" fill="{color}">■ {html.escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def html_report(md: str, samples: list[dict]) -> str:
    body = []
    for line in md.splitlines():
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("|---"):
            continue
        elif line.startswith("|"):
            cells = [html.escape(c.strip()) for c in line.strip("|").split("|")]
            body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        elif line.strip():
            body.append(f"<p>{html.escape(line.lstrip('- '))}</p>")
    txt = "\n".join(body)
    txt = re.sub(r"((?:<tr>.*</tr>\n?)+)", lambda m: f"<table>{m.group(1)}</table>", txt)
    charts = (svg_chart(samples, [("edge_rss", "edge xotira MB", "#c0392b"), ("cloud_rss", "bulut xotira MB", "#2471a3")],
                        "Xotira (RSS, MB)")
              + svg_chart(samples, [("edge_threads", "edge oqimlar", "#c0392b"), ("cloud_threads", "bulut oqimlar", "#2471a3")],
                          "Oqimlar")
              + svg_chart(samples, [("outbox_wait", "edge navbati", "#7d3c98"), ("notify_pending", "bulut xabar navbati", "#148f77")],
                          "Navbatlar")
              + svg_chart(samples, [("edge_disk_mb", "edge disk MB", "#c0392b"), ("cloud_disk_mb", "bulut disk MB", "#2471a3")],
                          "Disk"))
    return ("<!doctype html><html lang='uz'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Hushyor AI — barqarorlik sinovi</title><style>body{font-family:system-ui,sans-serif;max-width:920px;"
            "margin:24px auto;padding:0 16px;color:#222;background:#fff}table{border-collapse:collapse;width:100%;font-size:14px}"
            "td{border:1px solid #ddd;padding:4px 6px;vertical-align:top}tr:first-child td{font-weight:600;background:#f4f4f4}"
            "h3{margin:18px 0 4px;font-size:15px}</style></head><body>" + txt + "<h2>Grafiklar</h2>" + charts + "</body></html>")


# ============================================================================ jonli tizim hisoboti
def report_live(db_path: Path, hours: float, out_dir: Path | None) -> int:
    """Jonli tizim (haqiqiy kameralar) N soat ishlagach: o'z ko'rsatkichlaridan hisobot. Bazani faqat o'qiydi."""
    if not db_path.exists():
        print(f"Baza topilmadi: {db_path}")
        return 2
    c = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    since = now() - hours * H
    cols = {r[1] for r in c.execute("PRAGMA table_info(metrics_minute)")}

    def metric_rows(where: str) -> list[dict]:
        """metrics_minute: 1.5.0 ustunlari (persons_avg, cpu, extra ...) yoki data JSON — ikkalasi ham o'qiladi."""
        out = []
        for r in c.execute(f"SELECT * FROM metrics_minute WHERE {where} AND minute>=? ORDER BY minute", (int(since),)):
            d = dict(r)
            if "data" in cols and d.get("data"):
                d.update(json.loads(d["data"]))
            out.append(d)
        return out
    rows = metric_rows("source_id=0")
    samples = []
    t0 = rows[0]["minute"] if rows else since
    for r in rows:
        try:
            ex = json.loads(r["extra"]) if r.get("extra") else {}
        except (TypeError, ValueError):
            ex = {}
        samples.append({"t": r["minute"] - t0, "edge_rss": ex.get("rss_mb"), "edge_threads": ex.get("threads"),
                        "outbox_wait": ex.get("outbox"), "edge_cpu": r.get("cpu"), "ram": r.get("ram")})
    cams = {}
    for d in metric_rows("source_id>0"):
        a = cams.setdefault(d.get("source_name") or d["source_id"], {"n": 0, "up": 0.0, "fps": 0.0})
        a["n"] += 1
        a["up"] += float(d.get("up_pct") or 0)
        a["fps"] += float(d.get("fps_ai") or 0)
    evs = c.execute("SELECT COUNT(*) FROM events WHERE created_at>=?", (since,)).fetchone()[0]
    nlog = [dict(r) for r in c.execute("SELECT event_id, stage, ok, t_event, t_sent FROM notify_log WHERE t_sent>=?", (since,))]
    try:
        ob = c.execute("SELECT SUM(failed=0), SUM(failed=1) FROM outbox").fetchone()
    except sqlite3.Error:
        ob = (None, None)
    c.close()
    checks = []
    span = (samples[-1]["t"] - samples[0]["t"]) / H if len(samples) > 1 else 0
    pts = [(s["t"], s["edge_rss"]) for s in samples if s.get("edge_rss")]
    sl = slope_per_hour(pts)
    if pts:
        k = max(1, len(pts) // 10)
        b, e = statistics.median(p[1] for p in pts[:k]), statistics.median(p[1] for p in pts[-k:])
        restarts = sum(1 for x, y in zip(pts, pts[1:]) if y[1] < x[1] * 0.6)
        checks.append(("Xotira oqishi yo'q", (e <= b * 1.2 + 30 and (sl or 0) <= 5) if span >= 24 and not restarts else None,
                       f"{b:.0f} → {e:.0f} MB, {sl or 0:+.1f} MB/soat, {span:.1f} soat; qayta ishga tushish belgisi: {restarts}"))
    th = [s["edge_threads"] for s in samples if s.get("edge_threads")]
    if th:
        k = max(1, len(th) // 10)
        checks.append(("Oqimlar ko'paymayapti", max(th[-k:]) <= max(th[:k]) + 8, f"{max(th[:k])} → {max(th[-k:])}"))
    alerts = [r for r in nlog if r["stage"] in ("alert", "full")]
    ok_ev = {r["event_id"] for r in alerts if r["ok"]}
    bad_ev = {r["event_id"] for r in alerts if not r["ok"]} - ok_ev
    checks.append(("Telegram: hodisa xabarlari yetdi", not bad_ev if alerts else None,
                   f"{len(ok_ev)} hodisa yetdi, {len(bad_ev)} tasi yetmadi (oxirgi {hours:g} soatda {evs} hodisa)"))
    lat = [r["t_sent"] - r["t_event"] for r in alerts if r["ok"] and r["t_event"]]
    if lat:
        checks.append(("Telegram kechikishi p95 ≤ 5 s", pct(lat, 95) <= 5.0, f"p50 {pct(lat, 50):.2f} s, p95 {pct(lat, 95):.2f} s"))
    if ob[0] is not None:
        checks.append(("Bulut navbati tiqilmagan", (ob[0] or 0) < 500 and not ob[1], f"kutmoqda {ob[0] or 0}, rad etilgan {ob[1] or 0}"))
    for nm, a in cams.items():
        checks.append((f"Kamera «{nm}» ishladi", ((a["up"] / max(a["n"], 1)) >= 0.98) if a["up"] > 0 else None,
                       f"{a['up'] / max(a['n'], 1) * 100:.1f}% vaqt, o'rtacha AI {a['fps'] / max(a['n'], 1):.1f} kadr/s"))
    passed = all(ok is not False for _, ok, _ in checks)
    out_dir = out_dir or db_path.parent / f"hisobot_jonli_{time.strftime('%Y%m%d_%H%M')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    L = [f"# Hushyor AI — jonli tizim, oxirgi {hours:g} soat: {'O`TDI ✅' if passed else 'DIQQAT ❌'}", "",
         f"- Ko'rsatkichlar: {len(samples)} daqiqa ({span:.1f} soat), hodisalar: {evs}", "",
         "| Tekshiruv | Natija | Tafsilot |", "|---|---|---|"]
    for nm, ok, det in checks:
        L.append(f"| {nm} | {'✅' if ok else ('ℹ️ ma`lumot' if ok is None else '❌')} | {det} |")
    L += ["", "Xotira 24 soatdan qisqa bo'lakda yoki dastur qayta ishga tushgan bo'lsa — faqat ma'lumot uchun.",
          "Jurnaldagi xatolar: data/logs/app.log va supervisor.log (qayta ishga tushishlar)."]
    (out_dir / "hisobot.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out_dir / "hisobot.html").write_text(html_report("\n".join(L), samples), encoding="utf-8")
    print("\n".join(L))
    print(f"\nHisobot: {out_dir / 'hisobot.md'} (grafiklar: hisobot.html)")
    return 0 if passed else 1


def main() -> int:
    for st in (sys.stdout, sys.stderr):     # Windows konsoli/fayl (cp1251) emoji'da yiqilmasin
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Hushyor AI — uzoq barqarorlik sinovi (72 soat)")
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--cycle-min", type=float, default=60.0, help="nosozliklar sikli (daqiqa)")
    ap.add_argument("--sample-sec", type=float, default=60.0)
    ap.add_argument("--event-sec", type=float, default=180.0, help="soxta signal oralig'i (soniya)")
    ap.add_argument("--restart-hours", type=float, default=24.0, help="edge/bulutni qayta ishga tushirish oralig'i")
    ap.add_argument("--camera-alert-sec", type=float, default=30.0)
    ap.add_argument("--offline-min", type=float, default=5.0, help="bulut: do'kon 'aloqa uzildi' chegarasi")
    ap.add_argument("--long-outage-min", type=float, default=-1,
                    help="bitta uzoq internet uzilishi (daqiqa); sukut: 6+ soatli sinovda 60, aks holda yo'q")
    ap.add_argument("--backup-hours", type=float, default=6.0)
    ap.add_argument("--drain-min", type=float, default=20.0, help="oxirida navbatlar bo'shashini kutish (daqiqa)")
    ap.add_argument("--max-mem-slope", type=float, default=5.0, help="ruxsat etilgan xotira o'sishi (MB/soat)")
    ap.add_argument("--min-leak-hours", type=float, default=6.0, help="xotira oqishini baholash uchun eng qisqa bo'lak")
    ap.add_argument("--cam-fps", type=float, default=12.0)
    ap.add_argument("--video", default=str(ROOT / "samples" / "sanash_namuna.mp4"),
                    help="soxta kamera videosi (sukut: 1.9.0 sanash namunasi — odamlar chiziqni kesib o'tadi)")
    ap.add_argument("--count-line", default="0,0.72,1,0.72",
                    help="sanash chizig'i x1,y1,x2,y2 (0..1); bo'sh qator — sanashsiz")
    ap.add_argument("--cloud-db-url", default="", help="masalan PostgreSQL (sukut: sinov papkasidagi SQLite)")
    ap.add_argument("--dir", default="", help="sinov papkasi (sukut: vaqtinchalik papka)")
    ap.add_argument("--report-live", metavar="DB", help="jonli tizim bazasi (data/hushyor.db) — faqat hisobot")
    a = ap.parse_args()
    if a.report_live:
        return report_live(Path(a.report_live), a.hours, Path(a.dir) if a.dir else None)
    return Soak(a).run()


if __name__ == "__main__":
    sys.exit(main())
