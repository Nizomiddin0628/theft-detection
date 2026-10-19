"""Edge agent: do'kon kompyuteridan bulutga yuborish (store-and-forward).

Qoidalar:
  * Hodisa, uning yangilanishi (✅/❌, izoh, klip), kamera holati va daqiqalik ko'rsatkichlar avval shu
    kompyuterdagi `outbox` jadvaliga yoziladi (internet bormi-yo'qmi, farqi yo'q). Yetkazilgach qator o'chadi.
  * Yuborish tartibi — navbatdagi `id` bo'yicha: hodisa yozuvi klipidan oldin ketadi. Xato bo'lsa shu joyda
    to'xtab, kutib (5 s → 5 daqiqa, eksponensial) qaytadan uriniladi — hech narsa o'tkazib yuborilmaydi.
  * Takrorlanmaslik: har qatorda idempotent `key` (ev:12, clip:12, m:1718000000:3 ...). Server shu kalit va
    qurilma identifikatori bo'yicha takrorni aniqlaydi (upsert). Hodisa bir necha marta yangilansa, hali
    yuborilmagan qator ustiga yoziladi (coalesce) — navbat o'smaydi.
  * Server rad etsa (400/413/422 — ma'lumot noto'g'ri, fayl juda katta) qator `failed` deb belgilanadi va
    navbat davom etadi; 401/403 (token xato) — yuborish to'xtaydi, panelda xato ko'rinadi.
  * Sozlama: .env dagi CLOUD_URL va EDGE_TOKEN. Bo'lmasa — navbatga yozilmaydi (hamma narsa lokal).

Sync API shartnomasi: docs/SYNC_API.md. Qabul qiluvchining namunasi: cloud/server.py.
"""
from __future__ import annotations

import json
import threading
import time

import httpx

from . import db
from .config import APP_VERSION, CLIPS_DIR, CLOUD_URL, EDGE_TOKEN
from .logutil import get_logger

log = get_logger("sync")

API_VERSION = 1
BATCH = 50                       # bitta so'rovda eng ko'p hodisa / ko'rsatkich
CLIP_LIMIT = 60 * 1024 * 1024    # klip fayli chegarasi (serverniki bilan bir xil)
CLEAN_EXTRA = 5 * 1024 * 1024    # 3.1.0: klip + toza nusxa + rasm shundan oshsa (bulut tanani CLIP_LIMIT + 6 MB gacha oladi)
                                 # toza nusxa yuborilmaydi
BACKOFF_MIN, BACKOFF_MAX = 5.0, 300.0
HELLO_SEC = 60.0                 # heartbeat oralig'i
HELLO_ACTIVE_SEC = 15.0          # 3.0.0: bulut video sinovi ishlayotganda (panelda foiz yangilanib tursin)
POLL_SEC = 15.0                  # 3.0.0: bulutda yangi buyruq bormi (masalan, panelda video yuklandi) — yengil so'rov
CLIP_MAX_ATTEMPTS = 50           # klip shuncha vaqtinchalik xatodan keyin "rad etilgan" bo'ladi (boshqalar — cheksiz)
CLIP_REQUEUE_MAX = 3             # 409 (hodisa serverda yo'q) sababli klipni qayta navbatga qo'yish chegarasi
BACKFILL_DAYS = 7                # bulut birinchi ulanganda oxirgi shuncha kunlik hodisalar yuboriladi
KEEP_DAYS = 30                   # yetkazilmagan qator shuncha kundan keyin tashlanadi (disk to'lmasin)
TERMINAL_CODES = (400, 413, 415, 422)   # server ma'lumotni qabul qilmaydi — qayta urinish befoyda
# 404/405 — yo'l topilmadi (teskari proksi, joylashtirish paytida) — vaqtinchalik deb olinadi

# Hodisaning qaysi ustunlari o'zgarsa bulutga qayta yuboriladi
SYNC_FIELDS = {"status", "reviewed_by", "reviewed_at", "note", "claimed_by", "claimed_at", "clip_path", "thumb_path",
               "crop_path", "clip_start", "clip_error", "telegram_sent",
               "loss_sum", "loss_by", "loss_at", "clip_clean"}           # 3.1.0: zarar summasi, toza klip


def configured() -> bool:
    return bool(CLOUD_URL and EDGE_TOKEN)


def device_id() -> str:
    return db.get_setting("device_id", "") or "noma'lum"


def event_uid(event_id: int) -> str:
    return f"{device_id()}:{int(event_id)}"


# ----------------------------------------------------------------------------- navbatga yozish
def enqueue(kind: str, key: str, payload: dict, coalesce: bool = True) -> None:
    """Navbatga yozish. coalesce=True: shu kalitli, hali yuborilmayotgan qator bo'lsa — ustiga yoziladi."""
    if not configured():
        return
    data = json.dumps(payload, ensure_ascii=False)
    with db.tx() as c:
        if coalesce:
            cur = c.execute("UPDATE outbox SET payload=?, created_at=? WHERE key=? AND sending=0 AND failed=0",
                            (data, time.time(), key))
            if cur.rowcount:
                return
        c.execute("INSERT INTO outbox(kind, key, payload, created_at) VALUES(?,?,?,?)",
                  (kind, key, data, time.time()))
    if _worker is not None:
        _worker.kick()


def item_without_key(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "key"}


def event_payload(ev: dict) -> dict:
    """Hodisaning bulutga ketadigan ko'rinishi (fayl yo'llari — faqat nom, klip alohida yuklanadi)."""
    out = {k: v for k, v in ev.items() if k not in ("pose_path",)}
    out["uid"] = event_uid(ev["id"])
    out["local_id"] = ev["id"]
    out["device_id"] = device_id()
    out["updated_at"] = time.time()
    out["has_clip"] = bool(ev.get("clip_path"))
    if ev.get("job_id") is not None:
        # 3.1.0: bulut video sinovi hodisasi — bulut uni sinovni yuklagan foydalanuvchiga Telegram'da yuboradi
        try:
            out["cloud_test_id"] = db.scalar("SELECT cloud_test_id FROM jobs WHERE id=?", (int(ev["job_id"]),))
        except Exception:  # noqa: BLE001
            out["cloud_test_id"] = None
    return out


def enqueue_event(event_id: int) -> None:
    ev = db.get_event(event_id)
    if ev:
        enqueue("event", f"ev:{event_id}", event_payload(ev))


def enqueue_clip(event_id: int) -> None:
    ev = db.get_event(event_id)
    if ev and ev.get("clip_path"):
        enqueue("clip", f"clip:{event_id}", {"uid": event_uid(event_id), "local_id": event_id,
                                             "clip": ev["clip_path"], "thumb": ev.get("thumb_path"),
                                             "clean": ev.get("clip_clean")})


def enqueue_health(source: dict, state: str, reason: str, since: float | None = None) -> None:
    t = time.time()
    enqueue("health", f"h:{source.get('id')}:{state}:{int(t * 1000)}",
            {"device_id": device_id(), "t": t, "source_id": source.get("id"), "source_name": source.get("name"),
             "state": state, "reason": reason, "since": since}, coalesce=False)


def enqueue_metrics(rows: list[dict]) -> None:
    for r in rows:
        enqueue("metrics", f"m:{r['minute']}:{r['source_id']}", {"device_id": device_id(), **r})


# 2.0.0: bulut qo'llaydigan qo'shimcha turlar (hello javobidagi "features"). Eski bulut bilmagan turni (404) navbatga
# qo'ysak, tartib bo'yicha yuborishda butun navbat (hodisalar ham) to'xtab qolardi — shuning uchun faqat bulut
# e'lon qilgandan keyin yoziladi (oldingisi lokalda qoladi, hisobotga ta'sir yo'q).
def cloud_supports(feature: str) -> bool:
    if not configured():
        return False
    return feature in (db.get_setting("cloud_features", "") or "").split(",")


def enqueue_queue_minutes(rows: list[dict]) -> None:
    if not rows or not cloud_supports("queues"):
        return
    for r in rows:
        enqueue("queue", f"qm:{r['minute']}:{r['zone_id']}", {"device_id": device_id(), **r})


def enqueue_queue_alert(a: dict) -> None:
    if not a.get("id") or not cloud_supports("queues"):
        return
    enqueue("qalert", f"qa:{a['id']}", {"device_id": device_id(), "local_id": a["id"],
                                         **{k: a.get(k) for k in ("t", "rule", "level", "source_id", "source_name",
                                                                  "zone_id", "zone_name", "title", "text", "data")}},
            coalesce=False)


def enqueue_flow_hour(item: dict) -> None:
    """2.1.0: soatlik oqim yig'indisi (bo'limlar, yo'nalishlar, heatmap) — bulut "flow" ni e'lon qilgan bo'lsa.
    Soat davomida qayta yuboriladi (coalesce: yuborilmagan eskisi ustiga yoziladi)."""
    if not item or not cloud_supports("flow"):
        return
    enqueue("flow", f"fh:{item['plan_id']}:{item['hour']}", {"device_id": device_id(), **item})


def forget_event(event_id: int) -> None:
    """Hodisa lokal bazadan o'chirilganda uning navbatdagi qatorlari ham olib tashlanadi
    (aks holda klip qatori "fayl yo'q" bilan qizil "rad etilgan" bo'lib qolardi)."""
    if not configured():
        return
    db.execute("DELETE FROM outbox WHERE sending=0 AND key IN (?, ?)", (f"ev:{event_id}", f"clip:{event_id}"))


def _on_event_change(event_id: int, fields: dict) -> None:
    """db.EVENT_HOOKS: hodisa yozildi yoki muhim ustuni o'zgardi."""
    if not configured():
        return
    if fields.get("_new") or SYNC_FIELDS.intersection(fields):
        enqueue_event(event_id)
    if "clip_path" in fields and fields.get("clip_path"):
        enqueue_clip(event_id)


def install_hooks() -> None:
    if _on_event_change not in db.EVENT_HOOKS:
        db.EVENT_HOOKS.append(_on_event_change)


def backfill(days: int = BACKFILL_DAYS) -> int:
    """Bulut birinchi marta sozlanganda oxirgi kunlardagi hodisalar (va kliplar) navbatga qo'yiladi."""
    since = time.time() - days * 86400
    evs = db.rows("SELECT * FROM events WHERE created_at >= ? ORDER BY id", (since,))
    for ev in evs:
        ev = db.decode_event(ev)
        enqueue("event", f"ev:{ev['id']}", event_payload(ev))
        if ev.get("clip_path") and (CLIPS_DIR / ev["clip_path"]).exists():
            enqueue("clip", f"clip:{ev['id']}", {"uid": event_uid(ev["id"]), "local_id": ev["id"],
                                                 "clip": ev["clip_path"], "thumb": ev.get("thumb_path"),
                                                 "clean": ev.get("clip_clean")})
    return len(evs)


def pending() -> dict:
    """Navbat holati: nechta qator, nechtasi xato, klip hajmi."""
    n = db.scalar("SELECT COUNT(*) FROM outbox") or 0
    failed = db.scalar("SELECT COUNT(*) FROM outbox WHERE failed=1") or 0
    by_kind = {r["kind"]: r["n"] for r in db.rows("SELECT kind, COUNT(*) AS n FROM outbox GROUP BY kind")}
    clip_bytes = 0
    for r in db.rows("SELECT payload FROM outbox WHERE kind='clip'"):
        try:
            p = CLIPS_DIR / json.loads(r["payload"])["clip"]
            clip_bytes += p.stat().st_size if p.exists() else 0
        except (OSError, ValueError, KeyError):
            pass
    oldest = db.scalar("SELECT MIN(created_at) FROM outbox")
    return {"total": n, "failed": failed, "by_kind": by_kind, "clip_bytes": clip_bytes, "oldest": oldest}


# ----------------------------------------------------------------------------- yuboruvchi
class SyncWorker(threading.Thread):
    """Navbatni bulutga yetkazadi. Bitta nusxa, daemon oqim."""

    def __init__(self, url: str = CLOUD_URL, token: str = EDGE_TOKEN, manager=None):
        super().__init__(daemon=True, name="sync")
        self.url = url.rstrip("/")
        self.token = token
        self.manager = manager
        self.stop_evt = threading.Event()
        self.wake = threading.Event()
        self.client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0),
                                   headers={"Authorization": f"Bearer {token}", "X-Device-Id": device_id(),
                                            "X-Api-Version": str(API_VERSION), "User-Agent": f"HushyorAI/{APP_VERSION}"})
        self.state = "o'chirilgan" if not (url and token) else "ulanmoqda"
        self.last_ok = 0.0
        self.last_hello = 0.0
        self.last_error = ""
        self.sent = {"event": 0, "clip": 0, "metrics": 0, "health": 0}
        self.backoff = BACKOFF_MIN
        self.batch_size = BATCH          # 413 (proksi chegarasi) bo'lsa kamayadi, muvaffaqiyatda qaytadi
        self.auth_failed = False
        self.hello_failed = True         # birinchi hello'gacha "ulanish yo'q" deb olinadi (kutayotgan qatorlar ochiladi)
        self.server_time_skew = None
        self.last_poll = 0.0
        self.hello_due = False           # 3.0.0: navbatdan tashqari hello kerak (hello_soon / poll)

    # ------------------------------------------------------------------ boshqaruv
    def stop(self) -> None:
        self.stop_evt.set()
        self.wake.set()
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

    def kick(self) -> None:
        """Yangi qator qo'shildi — kutmasdan yuborishga urinish."""
        self.wake.set()

    def status(self) -> dict:
        p = pending() if configured() else {"total": 0, "failed": 0, "by_kind": {}, "clip_bytes": 0, "oldest": None}
        return {"configured": configured(), "url": self.url, "device_id": device_id(), "state": self.state,
                "last_ok": self.last_ok, "last_error": self.last_error, "sent": dict(self.sent), "pending": p,
                "backoff": round(self.backoff, 1) if self.state == "kutilmoqda" else 0,
                "clock_skew": self.server_time_skew}

    # ------------------------------------------------------------------ HTTP
    def _post(self, path: str, json_body: dict | None = None, files: dict | None = None,
              timeout: float = 30.0) -> tuple[int, dict]:
        """Qaytaradi (status, javob JSON). Tarmoq xatosi — status 0; JSON bo'lmagan javob (nginx 502 sahifasi,
        Wi-Fi kirish sahifasi) — bo'sh lug'at va xato matni."""
        try:
            if files:
                r = self.client.post(f"{self.url}{path}", data={"meta": json.dumps(json_body or {})}, files=files,
                                     timeout=timeout)
            else:
                r = self.client.post(f"{self.url}{path}", json=json_body or {}, timeout=timeout)
        except httpx.HTTPError as e:
            self.last_error = f"Tarmoq: {e.__class__.__name__}"
            return 0, {}
        body: dict = {}
        try:
            parsed = r.json() if r.content else {}
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            self.last_error = f"HTTP {r.status_code}: javob JSON emas (proksi yoki Wi-Fi kirish sahifasi?)"
            return r.status_code, {}
        if r.status_code >= 400:
            self.last_error = f"HTTP {r.status_code}: {str(body.get('detail') or body.get('error') or r.text[:120])}"
        return r.status_code, body

    def hello(self) -> bool:
        """Heartbeat: qurilma, versiya, kameralar, navbat. Server vaqti bilan farq ham o'lchanadi."""
        # Bayroq yig'ishdan OLDIN tushiriladi: hello davomida yangi o'zgarish bo'lsa (hello_soon) — keyingi sikl yana yuboradi
        self.hello_due = False
        cams = []
        if self.manager is not None:
            try:
                cams = [{"id": s["id"], "name": s["name"], "kind": s["kind"], "state": s["state"],
                         "running": s.get("running", False)} for s in self.manager.source_info()]
            except Exception:  # noqa: BLE001
                cams = []
        payload = {"device_id": device_id(), "name": db.get_setting("store_name", ""), "version": APP_VERSION,
                   "cameras": cams, "pending": pending()["total"]}
        try:
            from .visits import occupancy       # 1.9.0: "hozir ichkarida" — panel va bulut bir xil ko'rsatsin

            occ = occupancy()
            if occ is not None:
                payload["occupancy"] = occ
        except Exception:  # noqa: BLE001
            log.exception("Sanash holatini olib bo'lmadi")
        try:
            from .ai import queues               # 2.0.0: kassalar hozirgi holati (eski bulut bu maydonni e'tiborsiz qoldiradi)

            if queues.has_queues():
                payload["queues"] = [{k: x[k] for k in ("zone_id", "name", "source_name", "length", "max_wait",
                                                         "cashier", "state", "live")} for x in queues.board()]
        except Exception:  # noqa: BLE001
            log.exception("Navbat holatini olib bo'lmadi")
        try:
            from .ai import flow                 # 2.1.0: do'kon rejasi va bo'limlar (bulut heatmap'i uchun)

            plans = flow.layout().plans
            if plans or cloud_supports("flow"):     # bo'sh ro'yxat ham: reja o'chirilsa bulutda ham o'chsin
                payload["flow_plans"] = [flow.plan_public(p) for p in plans.values()][:5]
        except Exception:  # noqa: BLE001
            log.exception("Reja ma'lumotini olib bo'lmadi")
        try:
            from . import pilot                  # 2.5.0: tayyorlik va pilot holati (bulut filiallar ro'yxatida)

            payload["readiness"] = pilot.summary()
        except Exception:  # noqa: BLE001
            log.exception("Tayyorlik holatini olib bo'lmadi")
        vt: list = []
        try:
            from . import cloud_tests           # 3.0.0: bulut panelidan yuborilgan video sinovlari holati

            vt = cloud_tests.report()
            if vt:
                payload["video_tests"] = vt
        except Exception:  # noqa: BLE001
            log.exception("Video sinovlari holatini olib bo'lmadi")
        t0 = time.time()
        payload["time"] = t0
        code, body = self._post(f"/api/v{API_VERSION}/edge/hello", payload, timeout=15)
        self.last_hello = time.time()
        if code == 200 and body.get("ok"):
            st = body.get("server_time")
            if isinstance(st, (int, float)):
                self.server_time_skew = round(float(st) - (t0 + time.time()) / 2, 2)
            if self.hello_failed:
                # Ulanish tiklandi — kutayotgan qatorlar darhol urinilsin
                db.execute("UPDATE outbox SET next_try=0 WHERE sending=0 AND failed=0 AND next_try > 0")
                self.hello_failed = False
            feats = body.get("features")
            feats = ",".join(sorted(str(f)[:40] for f in feats if isinstance(f, str)))[:500] if isinstance(feats, list) else ""
            if feats != (db.get_setting("cloud_features", "") or ""):
                db.set_settings({"cloud_features": feats})
                log.info("Bulut imkoniyatlari: %s", feats or "—")
            offered = [x["id"] for x in vt if isinstance(x, dict) and "metrics" in x]
            if offered:
                try:
                    from . import cloud_tests

                    cloud_tests.metrics_offered(offered)
                except Exception:  # noqa: BLE001
                    log.exception("Video sinovi o'lchovlari holatini yozib bo'lmadi")
            stored = body.get("video_tests_stored")
            if isinstance(stored, list) and stored:
                try:
                    from . import cloud_tests       # 3.1.0: o'lchovlar bulutda saqlandi — endi qayta yuborilmaydi

                    cloud_tests.metrics_delivered(stored)
                except Exception:  # noqa: BLE001
                    log.exception("Video sinovi o'lchovlarini belgilab bo'lmadi")
            cmds = body.get("commands")
            if isinstance(cmds, list) and cmds:
                self._apply_commands([c for c in cmds if isinstance(c, dict)])
            return True
        if code in (401, 403):
            self.auth_failed = True
            self.state = "token xato"
            log.error("Bulut tokenni qabul qilmadi (%s) — .env dagi EDGE_TOKEN ni tekshiring", code)
        elif code in (400, 404, 405, 422):
            # Manzil yoki qurilma identifikatori noto'g'ri (CLOUD_URL yo'li xato, eski server) — sozlama xatosi
            self.last_error = "Sozlama xato (CLOUD_URL yo'li yoki server versiyasi): " + self.last_error
        self.hello_failed = True
        return False

    def _apply_commands(self, cmds: list[dict]) -> None:
        """Bulutdan kelgan buyruqlar (hozircha: review — bulutda ✅/❌ bosildi). Bajarilgach ack yuboriladi.
        Takror kelsa (ack yetmagan) — holat allaqachon shunday bo'lsa, qayta yozilmaydi."""
        done: list[int] = []
        for c in cmds:
            try:
                if c.get("type") in ("video_test", "video_test_cancel"):
                    from . import cloud_tests      # 3.0.0: bulut panelidagi "Video sinovi"

                    if c["type"] == "video_test":
                        cloud_tests.accept(c, self)
                    else:
                        cloud_tests.cancel(c, self.manager)
                elif c.get("type") == "loss" and c.get("local_id"):
                    self._apply_loss(c)                    # 3.1.0: bulutda zarar summasi kiritildi
                elif c.get("type") == "review" and c.get("local_id"):
                    ev = db.get_event(int(c["local_id"]))
                    status = str(c.get("status") or "")
                    if ev and status in ("yangi", "tasdiqlangan", "rad_etilgan"):
                        by = str(c.get("by") or "Bulut")[:100]
                        # Bulut vaqti edge soatiga o'tkaziladi (server_time_skew = server − shu kompyuter)
                        t_cloud_local = float(c.get("t") or 0) - float(self.server_time_skew or 0)
                        newer_local = float(ev.get("reviewed_at") or 0) > t_cloud_local
                        same = ev.get("status") == status and (status == "yangi" or ev.get("reviewed_by") == by)
                        if newer_local:
                            log.info("Bulut buyrug'i eskirgan: hodisa #%d da yangiroq lokal qaror bor", ev["id"])
                        elif not same:
                            db.review_event(ev["id"], status, by, str(c["note"])[:2000] if c.get("note") is not None else None)
                            if status == "yangi":
                                db.update_event(ev["id"], reviewed_by=None, reviewed_at=None)
                            elif t_cloud_local > 0:
                                # 3.2.0: qaror vaqti — bulutdagi vaqt (shu kompyuter soatiga o'tkazilgan), "hozir" emas:
                                # aks holda bir partiyadagi keyingi (to'g'rilangan) buyruq "eskirgan" deb tashlanardi
                                db.update_event(ev["id"], reviewed_at=t_cloud_local)
                            log.info("Bulutdan tekshirish: hodisa #%d → %s (%s)", ev["id"], status, by)
                            if self.manager is not None:
                                try:
                                    if self.manager.notifier is not None:
                                        self.manager.notifier.refresh_event(ev["id"])
                                    self.manager.emit("update", ev["id"])
                                except Exception:  # noqa: BLE001
                                    log.exception("Bulut buyrug'idan keyin yangilashda xato")
                    elif not ev:
                        log.warning("Bulut buyrug'i: hodisa #%s lokal bazada yo'q", c.get("local_id"))
                else:
                    log.warning("Bulut buyrug'i tushunarsiz: %s", str(c.get("type"))[:40])
                if isinstance(c.get("id"), int):
                    done.append(c["id"])
            except Exception:  # noqa: BLE001
                log.exception("Bulut buyrug'ini bajarishda xato")
        if done:
            code, _ = self._post(f"/api/v{API_VERSION}/edge/commands/ack", {"ids": done}, timeout=15)
            if code != 200:
                log.warning("Buyruqlar ack qilinmadi (%s) — bulut 2 daqiqadan keyin qayta yuboradi", code)

    # ------------------------------------------------------------------ navbat
    def _apply_loss(self, c: dict) -> None:
        """3.1.0: bulut panelida yoki bulut botida kiritilgan zarar summasi. Lokal (keyinroq) kiritilgan summa ustidan
        yozilmaydi; takror kelsa — o'zgarish yo'q."""
        from . import losses

        ev = db.get_event(int(c["local_id"]))
        if not ev:
            log.warning("Bulut buyrug'i (summa): hodisa #%s lokal bazada yo'q", c.get("local_id"))
            return
        try:
            amount = losses.parse_sum(c.get("loss_sum"))
        except ValueError:
            log.warning("Bulut buyrug'i (summa) noto'g'ri: %s", str(c.get("loss_sum"))[:40])
            return
        t_cloud_local = float(c.get("t") or 0) - float(self.server_time_skew or 0)
        if float(ev.get("loss_at") or 0) > t_cloud_local:
            log.info("Bulut summasi eskirgan: hodisa #%d da yangiroq lokal summa bor", ev["id"])
            return
        if (ev.get("loss_sum") or None) == amount:
            return
        losses.set_loss(ev["id"], amount, str(c.get("by") or "Bulut")[:100], at=t_cloud_local if t_cloud_local > 0 else None)
        log.info("Bulutdan zarar summasi: hodisa #%d → %s", ev["id"], amount)
        if self.manager is not None:
            try:
                if self.manager.notifier is not None:
                    self.manager.notifier.refresh_event(ev["id"])
                self.manager.emit("update", ev["id"])
            except Exception:  # noqa: BLE001
                log.exception("Bulut summasidan keyin yangilashda xato")

    def _take(self) -> list[dict]:
        """Navbatdan keyingi partiya: id tartibida, bir xil turdagi ketma-ket qatorlar (klip — bittadan).

        O'qish va `sending=1` belgisi BITTA tranzaksiyada — orada kelgan yangilanish eski yukga qo'shilib
        yo'qolmaydi (enqueue coalesce faqat sending=0 qatorga yozadi).
        Tartib qoidasi faqat bitta: klip (clip:N) o'z hodisasi (ev:N) yetgandan keyin. Qolgan qatorlar
        bir-biriga bog'liq emas, shuning uchun kutayotgan (next_try kelajakda) qator boshqalarni to'smaydi.
        """
        now = time.time()
        with db.tx() as c:
            # 3.2.0: hodisalar (va kamera/navbat signallari) birinchi — uzoq aloqasizlikdan keyin minglab daqiqalik
            # ko'rsatkichlar orqasida kutib qolmasin (bulut 10 daqiqadan eski hodisa uchun xabar yubormaydi)
            prio = [dict(r) for r in c.execute(
                "SELECT * FROM outbox WHERE sending=0 AND failed=0 AND next_try <= ? AND kind IN ('event', 'health', "
                "'qalert') ORDER BY id LIMIT ?", (now, self.batch_size)).fetchall()]
            if prio:
                kind = prio[0]["kind"]
                batch = [r for r in prio if r["kind"] == kind]
                ids = [r["id"] for r in batch]
                c.execute(f"UPDATE outbox SET sending=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
                return batch
            for r in c.execute("SELECT * FROM outbox WHERE sending=0 AND failed=0 AND next_try <= ? AND kind='clip' "
                               "ORDER BY id LIMIT 50", (now,)).fetchall():
                r = dict(r)
                try:
                    lid = json.loads(r["payload"]).get("local_id")
                except ValueError:
                    lid = None
                if lid is None or not c.execute("SELECT 1 FROM outbox WHERE key=? AND failed=0",
                                                (f"ev:{lid}",)).fetchone():
                    c.execute("UPDATE outbox SET sending=1 WHERE id=?", (r["id"],))
                    return [r]
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM outbox WHERE sending=0 AND failed=0 AND next_try <= ? ORDER BY id LIMIT ?",
                (now, self.batch_size)).fetchall()]
            batch: list[dict] = []
            kind = None
            for r in rows:
                if r["kind"] == "clip":
                    try:
                        lid = json.loads(r["payload"]).get("local_id")
                    except ValueError:
                        lid = None
                    if lid is not None and c.execute("SELECT 1 FROM outbox WHERE key=? AND failed=0",
                                                     (f"ev:{lid}",)).fetchone():
                        continue           # hodisa yozuvi hali yetmagan — klip kutadi
                if kind is None:
                    kind = r["kind"]
                if r["kind"] != kind:
                    if batch:
                        break
                    continue
                batch.append(r)
                if kind == "clip":
                    break
            if not batch and rows:
                # Oyna to'liq "hodisasini kutayotgan klip"lardan iborat — hodisa qatorlari oynadan tashqarida.
                # Klipsiz qayta so'raymiz, aks holda navbat abadiy turib qolardi.
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM outbox WHERE sending=0 AND failed=0 AND next_try <= ? AND kind != 'clip' "
                    "ORDER BY id LIMIT ?", (now, self.batch_size)).fetchall()]
                for r in rows:
                    if kind is None or kind == "clip":
                        kind = r["kind"]
                    if r["kind"] != kind:
                        break
                    batch.append(r)
            if not batch:
                return []
            ids = [r["id"] for r in batch]
            c.execute(f"UPDATE outbox SET sending=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        return batch

    def _done(self, ids: list[int]) -> None:
        if ids:
            db.execute(f"DELETE FROM outbox WHERE id IN ({','.join('?' * len(ids))})", ids)

    def _release(self, ids: list[int]) -> None:
        """Qatorlar yana navbatga (urinish hisoblanmaydi) — partiya bo'linganda."""
        if ids:
            db.execute(f"UPDATE outbox SET sending=0 WHERE id IN ({','.join('?' * len(ids))})", ids)

    def _fail(self, ids: list[int], err: str, terminal: bool = False) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        if terminal:
            db.execute(f"UPDATE outbox SET sending=0, failed=1, last_error=? WHERE id IN ({marks})", [err[:300], *ids])
            log.warning("Bulut navbati: %d ta qator rad etilgan deb belgilandi — %s", len(ids), err[:160])
        else:
            # Har qator o'z urinishlari bo'yicha eksponensial kutadi (5 s → 5 daqiqa): bitta katta klip
            # tez-tez qayta urinilmaydi, lekin boshqa qatorlarni ham to'smaydi
            db.execute(f"UPDATE outbox SET sending=0, attempts=attempts+1, "
                       f"next_try=? + min(?, ? * (1 << min(attempts, 12))), last_error=? WHERE id IN ({marks})",
                       [time.time(), BACKOFF_MAX, BACKOFF_MIN, err[:300], *ids])

    def _send_batch(self, batch: list[dict]) -> bool:
        """Bitta partiyani yuborish. True — davom etish mumkin; False — to'xtab kutish kerak."""
        kind = batch[0]["kind"]
        ids = [r["id"] for r in batch]
        try:
            return self._send_batch_inner(kind, ids, batch)
        except Exception as e:  # noqa: BLE001 — fayl o'chib qolgan, antivirus band qilgan va h.k.
            log.exception("Bulutga yuborishda xato (%s)", kind)
            self._fail(ids, f"ichki xato: {e.__class__.__name__}: {str(e)[:120]}")
            return False

    def _send_batch_inner(self, kind: str, ids: list[int], batch: list[dict]) -> bool:
        items = []
        for r in batch:
            try:
                items.append({"key": r["key"], **json.loads(r["payload"])})
            except ValueError:
                items.append({"key": r["key"]})
        if kind == "clip":
            return self._send_clip(batch[0], items[0])
        path = {"event": "events", "metrics": "metrics", "health": "health", "queue": "queues",
                "qalert": "queue_alerts", "flow": "flow"}.get(kind)
        if path is None:
            self._fail(ids, f"noma'lum tur: {kind}", terminal=True)
            return True
        code, body = self._post(f"/api/v{API_VERSION}/edge/{path}", {"device_id": device_id(), "items": items})
        if code == 200 and body.get("ok"):
            rejected = {x.get("key"): x.get("reason", "") for x in body.get("rejected") or [] if isinstance(x, dict)}
            if rejected:
                bad = [r["id"] for r in batch if r["key"] in rejected]
                self._fail(bad, "server rad etdi: " + "; ".join(str(v) for v in rejected.values())[:200], terminal=True)
                ids = [i for i in ids if i not in bad]
            self._done(ids)
            self.sent[kind] = self.sent.get(kind, 0) + len(ids)
            self.batch_size = min(BATCH, self.batch_size * 2)
            return True
        if code == 413 and len(batch) > 1:
            # So'rov katta (proksi chegarasi) — partiya ikkiga bo'linadi, urinish hisoblanmaydi
            self.batch_size = max(1, len(batch) // 2)
            self._release(ids)
            log.warning("Bulut: so'rov juda katta (413) — partiya %d taga kamaytirildi", self.batch_size)
            return True
        return self._handle_error(code, ids)

    def _post_clip(self, item: dict, p, thumb, clean) -> tuple[int, dict]:
        with open(p, "rb") as fv:
            files = {"clip": (p.name, fv, "video/mp4")}
            extra = []
            try:
                if thumb is not None:
                    extra.append(open(thumb, "rb"))
                    files["thumb"] = (thumb.name, extra[-1], "image/jpeg")
                if clean is not None:
                    extra.append(open(clean, "rb"))
                    files["clean"] = (clean.name, extra[-1], "video/mp4")
                return self._post(f"/api/v{API_VERSION}/edge/events/{item['uid']}/clip",
                                  {"device_id": device_id(), "local_id": item.get("local_id")}, files=files, timeout=180)
            finally:
                for f in extra:
                    f.close()

    def _send_clip(self, row: dict, item: dict) -> bool:
        p = CLIPS_DIR / str(item.get("clip") or "")
        if not item.get("clip") or not p.is_file():
            self._fail([row["id"]], "klip fayli yo'q", terminal=True)
            return True
        if p.stat().st_size > CLIP_LIMIT:
            self._fail([row["id"]], f"klip juda katta ({p.stat().st_size // (1 << 20)} MB)", terminal=True)
            return True
        thumb = CLIPS_DIR / item["thumb"] if item.get("thumb") else None
        thumb = thumb if thumb is not None and thumb.is_file() else None
        # 3.1.0: chizmalarsiz nusxa ham (bulut Telegram'ga shuni yuboradi; eski bulut bu maydonni e'tiborsiz qoldiradi).
        # Bulut so'rov tanasini CLIP_LIMIT + 6 MB gacha qabul qiladi (eski bulut ham) — sig'masa, toza nusxasiz yuboriladi:
        # dalil (asosiy klip) toza nusxa uchun hech qachon yo'qolmasin
        clean = (CLIPS_DIR / item["clean"] if item.get("clean") and not item.get("no_clean")
                 and item.get("clean") != item.get("clip") else None)
        if clean is not None and (not clean.is_file() or p.stat().st_size + clean.stat().st_size
                                  + (thumb.stat().st_size if thumb else 0) > CLIP_LIMIT + CLEAN_EXTRA):
            clean = None
        code, body = self._post_clip(item, p, thumb, clean)
        if code == 413 and clean is not None:
            log.warning("Bulut klipni toza nusxa bilan qabul qilmadi (413) — toza nusxasiz qayta yuborilmoqda")
            code, body = self._post_clip(item, p, thumb, None)
        if code == 200 and body.get("ok"):
            self._done([row["id"]])
            self.sent["clip"] += 1
            return True
        if code == 409:
            # Server: bunday hodisa hali yo'q (hodisa yozuvi oldin ketishi kerak edi). Hodisa qayta navbatga
            # qo'yiladi, klip qatori esa uning ORQASIGA ko'chiriladi (yangi id) — tartib tiklanadi.
            # Bu CLIP_REQUEUE_MAX martadan ko'p takrorlansa — server bilan kelishmovchilik, klip rad etiladi.
            n_req = int(item.get("requeued") or 0)
            ev = db.get_event(int(item.get("local_id") or 0)) if item.get("local_id") else None
            if ev is None:
                self._fail([row["id"]], "server: hodisa yo'q, lokal bazada ham yo'q", terminal=True)
                return True
            if n_req >= CLIP_REQUEUE_MAX:
                self._fail([row["id"]], f"server hodisani qabul qilmayapti ({n_req} marta 409)", terminal=True)
                return True
            enqueue("event", f"ev:{ev['id']}", event_payload(ev))
            payload = item_without_key(item)
            payload["requeued"] = n_req + 1
            enqueue("clip", row["key"], payload, coalesce=False)
            self._done([row["id"]])          # yangi qatorlar yozilgach eskisi o'chadi (xato bo'lsa klip yo'qolmaydi)
            return True
        if code in (401, 403) or code in TERMINAL_CODES:
            return self._handle_error(code, [row["id"]])
        # Vaqtinchalik (tarmoq, 5xx, 429, 404/405): klip o'z navbatida kutadi, boshqa qatorlar yuborilaveradi.
        # Server butunlay yotgan bo'lsa keyingi partiya (hodisa/ko'rsatkich) ham xato beradi va worker kutadi.
        if int(row.get("attempts") or 0) + 1 >= CLIP_MAX_ATTEMPTS:
            self._fail([row["id"]], f"klip {CLIP_MAX_ATTEMPTS} urinishda yetmadi: {self.last_error}", terminal=True)
            return True
        self._fail([row["id"]], self.last_error or f"HTTP {code}")
        return code != 0

    def _handle_error(self, code: int, ids: list[int]) -> bool:
        if code in (401, 403):
            self.auth_failed = True
            self.state = "token xato"
            self._fail(ids, self.last_error)
            log.error("Bulut tokenni qabul qilmadi (%s) — yuborish to'xtatildi. .env dagi EDGE_TOKEN ni tekshiring", code)
            return False
        if code in TERMINAL_CODES:
            self._fail(ids, self.last_error, terminal=True)
            return True
        # 0 (tarmoq), 5xx, 429, 404/405 (proksi/joylashtirish) — vaqtinchalik: kutib yana uriniladi
        self._fail(ids, self.last_error or f"HTTP {code}")
        return False

    def _prune(self) -> None:
        """Juda eski yetkazilmagan qatorlar (disk va navbat o'smasin) — faqat KEEP_DAYS dan keyin."""
        cutoff = time.time() - KEEP_DAYS * 86400
        old = db.scalar("SELECT COUNT(*) FROM outbox WHERE created_at < ?", (cutoff,)) or 0
        if old:
            db.execute("DELETE FROM outbox WHERE created_at < ?", (cutoff,))
            log.warning("Bulut navbatidan %d ta %d kundan eski yetkazilmagan qator tashlandi", old, KEEP_DAYS)

    # ------------------------------------------------------------------ asosiy sikl
    def run(self) -> None:
        if not (self.url and self.token):
            return
        install_hooks()
        try:
            from . import cloud_tests

            n = cloud_tests.resume(self)
            if n:
                log.info("Bulut video sinovlari: %d ta yuklab olish davom ettirilmoqda", n)
        except Exception:  # noqa: BLE001
            log.exception("Bulut video sinovlarini tiklab bo'lmadi")
        bf_key = f"sync_backfilled:{self.url}"
        if not db.get_setting(bf_key, ""):
            n = backfill()
            db.set_settings({bf_key: str(int(time.time()))})
            log.info("Bulut: oxirgi %d kunlik %d ta hodisa navbatga qo'yildi", BACKFILL_DAYS, n)
        last_prune = 0.0
        while not self.stop_evt.is_set():
            try:
                if self.auth_failed:
                    # Token xato — 5 daqiqada bir marta qayta tekshiriladi (foydalanuvchi .env ni tuzatgan bo'lsa)
                    if self.stop_evt.wait(300):
                        break
                    self.auth_failed = False
                    self.state = "ulanmoqda"
                if self.state != "ishlayapti" or self.hello_due or time.time() - self.last_hello >= self._hello_every():
                    # Heartbeat — ayni paytda ulanish tekshiruvi. Ulanish tiklangach kutayotgan qatorlar darhol ketadi
                    if not self.hello():
                        if self.auth_failed:
                            continue
                        self._wait_backoff()
                        continue
                    self.state, self.last_ok = "ishlayapti", time.time()
                stalled = False
                while not self.stop_evt.is_set():
                    batch = self._take()
                    if not batch:
                        break
                    if not self._send_batch(batch):
                        stalled = True
                        break
                    self.last_ok = time.time()
                    self.backoff = BACKOFF_MIN          # faqat haqiqiy yuborish muvaffaqiyati kutishni nolga qaytaradi
                if stalled:
                    if not self.auth_failed:
                        self._wait_backoff()
                    continue
                if time.time() - last_prune > 3600:
                    last_prune = time.time()
                    self._prune()
                if time.time() - self.last_poll >= POLL_SEC and cloud_supports("video_tests"):
                    self.last_poll = time.time()
                    if self._poll():
                        continue                        # buyruq bor — darhol hello (buyruqlar shu javobda keladi)
                self.wake.wait(5.0)
                self.wake.clear()
            except Exception:
                log.exception("Bulut sinxronizatsiyasida xato")
                self.stop_evt.wait(5)

    def _poll(self) -> bool:
        """3.0.0: bulutda yetkazilmagan buyruq bormi (GET /edge/poll). Bor bo'lsa — keyingi sikl darhol hello yuboradi.
        Xato — jim (keyingi hello odatdagidek)."""
        try:
            r = self.client.get(f"{self.url}/api/v{API_VERSION}/edge/poll", timeout=10)
            body = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return False
        if isinstance(body, dict) and int(body.get("pending") or 0) > 0:
            self.hello_due = True
            return True
        return False

    def _hello_every(self) -> float:
        try:
            from . import cloud_tests

            return HELLO_ACTIVE_SEC if cloud_tests.active() else HELLO_SEC
        except Exception:  # noqa: BLE001
            return HELLO_SEC

    def _wait_backoff(self) -> None:
        """Eksponensial kutish. Yangi qator (kick) kutishni qisqartirmaydi — server yotgan bo'lsa urish befoyda."""
        if self.state != "token xato":
            self.state = "kutilmoqda"
        self.stop_evt.wait(self.backoff)
        self.backoff = min(self.backoff * 2, BACKOFF_MAX)


_worker: SyncWorker | None = None


def hello_soon() -> None:
    """3.0.0: navbatdagi hello'ni kutmasdan yuborish (masalan, bulut video sinovi holati o'zgardi)."""
    w = _worker
    if w is not None and w.is_alive():
        w.hello_due = True
        w.kick()


def get_worker(manager=None) -> SyncWorker:
    global _worker
    if _worker is None:
        _worker = SyncWorker(manager=manager)
    elif manager is not None and _worker.manager is None:
        _worker.manager = manager
    return _worker


def start_worker(manager=None) -> SyncWorker:
    """Yuboruvchini ishga tushirish (allaqachon ishlayotgan bo'lsa — o'sha; to'xtatilgan bo'lsa — yangisi)."""
    global _worker
    w = get_worker(manager)
    if w.is_alive():
        return w
    if w.ident is not None or w.stop_evt.is_set():      # oldin ishlab to'xtagan oqimni qayta boshlab bo'lmaydi
        _worker = w = SyncWorker(manager=manager)
    w.start()
    return w


# ----------------------------------------------------------------------------- 3.0.0: buyruq qatori (ulash)
def check_connection(url: str, token: str) -> tuple[bool, str]:
    """Bulut manzili va qurilma tokeni to'g'rimi (GET /edge/poll — buyruqlarni olmaydi, hech narsani o'zgartirmaydi)."""
    url = url.rstrip("/")
    try:
        r = httpx.get(f"{url}/api/v{API_VERSION}/edge/poll", timeout=15,
                      headers={"Authorization": f"Bearer {token}", "X-Device-Id": device_id(),
                               "X-Api-Version": str(API_VERSION), "User-Agent": f"HushyorAI/{APP_VERSION}"})
    except httpx.HTTPError as e:
        return False, f"server bilan aloqa yo'q ({e.__class__.__name__}) — manzil va internetni tekshiring"
    if r.status_code == 200:
        return True, "aloqa bor, token to'g'ri"
    if r.status_code == 401:
        return False, "token xato (yoki u boshqa qurilma identifikatori uchun berilgan)"
    if r.status_code in (404, 405):
        return False, "bulut serveri eski (3.0.0 dan oldingi) yoki manzil noto'g'ri"
    return False, f"bulut javobi HTTP {r.status_code}"


def write_env(updates: dict[str, str], env_file=None):
    """.env dagi kalitlarni almashtiradi (bo'lmasa — qo'shadi), qolgan qatorlarga tegmaydi. UTF-8 (BOM'siz)."""
    from pathlib import Path

    from .config import BASE_DIR

    f = Path(env_file) if env_file else BASE_DIR / ".env"
    lines = f.read_text(encoding="utf-8-sig").splitlines() if f.exists() else []
    out, done = [], set()
    for raw in lines:
        s = raw.strip()
        key = s.split("=", 1)[0].strip() if "=" in s and not s.startswith("#") else None
        if key and key.lower().startswith("export "):
            key = key[7:].strip()
        if key in updates:
            if key not in done:
                out.append(f"{key}={updates[key]}")
                done.add(key)
            continue
        out.append(raw)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    f.write_text("\n".join(out) + "\n", encoding="utf-8")
    return f


def main(argv=None) -> int:
    """python -m app.sync --device-id | --connect <URL> <TOKEN> | --check"""
    import argparse
    import re

    ap = argparse.ArgumentParser(prog="python -m app.sync", description="Do'kon kompyuterini bulutga ulash")
    ap.add_argument("--device-id", action="store_true", help="qurilma identifikatori (bulut panelida filial qo'shish uchun)")
    ap.add_argument("--connect", nargs=2, metavar=("URL", "TOKEN"), help="tekshirib, .env ga CLOUD_URL va EDGE_TOKEN yozadi")
    ap.add_argument("--check", action="store_true", help=".env dagi CLOUD_URL va EDGE_TOKEN bilan aloqani tekshirish")
    a = ap.parse_args(argv)
    try:
        dev = db.get_setting("device_id", "")
    except Exception:  # noqa: BLE001 — baza hali yo'q
        dev = ""
    if not dev:
        print("Qurilma identifikatori hali yo'q — dasturni bir marta ishga tushiring (start_windows.bat), keyin qaytadan.")
        return 1
    if a.device_id:
        print(dev)
        return 0
    if a.connect:
        url, token = a.connect[0].strip().rstrip("/"), a.connect[1].strip()
        if not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
            print("Manzil http:// yoki https:// bilan boshlanishi kerak, masalan: https://hushyor.misol.uz")
            return 1
        if not re.fullmatch(r"[A-Za-z0-9_\-.~]{16,200}", token):
            print("Token noto'g'ri ko'rinishda (bulut panelidagi EDGE_TOKEN= dan keyingi qism kerak)")
            return 1
        ok, why = check_connection(url, token)
        print(("OK: " if ok else "XATO: ") + why)
        if not ok:
            return 1
        f = write_env({"CLOUD_URL": url, "EDGE_TOKEN": token})
        print(f"Yozildi: {f} (CLOUD_URL, EDGE_TOKEN). Endi dasturni qayta ishga tushiring:")
        print("  Stop-ScheduledTask HushyorAI; Start-ScheduledTask HushyorAI      (yoki start_windows.bat)")
        return 0
    if a.check:
        if not configured():
            print("Bulut sozlanmagan (.env da CLOUD_URL va EDGE_TOKEN yo'q)")
            return 1
        ok, why = check_connection(CLOUD_URL, EDGE_TOKEN)
        print(("OK: " if ok else "XATO: ") + why + f"  ({CLOUD_URL}, qurilma {dev})")
        return 0 if ok else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
