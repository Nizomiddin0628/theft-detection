"""Bulut paneli uchun jonli oqim (SSE): yangi hodisa, hodisa holati, filial onlayn/offlayn.

Har ulangan brauzer o'z foydalanuvchisining filiallari bo'yicha filtrlanadi. Nashr qilish istalgan oqimdan
(edge handler — event loop, bildirishnoma worker — alohida oqim) xavfsiz: call_soon_threadsafe.
Resurs: har ulanishga bitta asyncio.Queue (ko'pi bilan 200 xabar), bo'sh paytda hech narsa qilmaydi.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

QUEUE_MAX = 200


class Hub:
    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.Queue, set[int], int, str]] = []   # (navbat, filiallar, foydalanuvchi, sessiya)
        self._lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self, user_id: int, branch_ids: set[int], per_user: int = 0, session: str = "",
                  per_session: int = 0) -> asyncio.Queue:
        """Yangi oqim. Cheklovlar (0 — yo'q): bitta sessiyada (brauzerda) per_session ta, bitta xodimda (hamma
        qurilmalari) per_user ta — oshsa eng eskisiga yopilish belgisi (None) yuboriladi: yopilmagan eski tablar
        ulanishlarni to'plab qo'ymasin. Sessiya cheklovi alohida: bitta akkauntli bir nechta qurilma (qo'riqchilar
        planshetlari) bir-birini uzib turmasin. Event loop ichidan chaqiriladi."""
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            same_sess = [s_[0] for s_ in self._subs if session and s_[3] == session]
            same_user = [s_[0] for s_ in self._subs if s_[2] == user_id]
            self._subs.append((q, set(branch_ids), user_id, session))
        drop: list[asyncio.Queue] = []
        if per_session > 0:
            drop += same_sess[:max(0, len(same_sess) + 1 - per_session)]
        if per_user > 0:
            rest = [x for x in same_user if x not in drop]
            drop += rest[:max(0, len(rest) + 1 - per_user)]
        for old in drop:
            self._close(old)
        return q

    @staticmethod
    def _close(q: asyncio.Queue) -> None:
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s[0] is not q]

    def update_scope(self, q: asyncio.Queue, branch_ids: set[int]) -> None:
        with self._lock:
            self._subs = [(s[0], set(branch_ids) if s[0] is q else s[1], s[2], s[3]) for s in self._subs]

    def count(self) -> int:
        return len(self._subs)

    def _deliver(self, branch_id: int | None, msg: str) -> None:
        with self._lock:
            subs = list(self._subs)
        for q, scope, _u, _s in subs:
            if branch_id is not None and branch_id not in scope:
                continue
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:       # sekin brauzer — eng eskisi tashlanadi
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def publish(self, kind: str, data: dict, branch_id: int | None = None) -> None:
        """kind: event_new | event_update | branch. Istalgan oqimdan chaqirish mumkin."""
        msg = f"event: {kind}\ndata: {json.dumps({**data, 't': time.time()}, ensure_ascii=False, default=str)}\n\n"
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(branch_id, msg)
        else:
            try:
                loop.call_soon_threadsafe(self._deliver, branch_id, msg)
            except RuntimeError:            # loop yopilgan
                pass


hub = Hub()
