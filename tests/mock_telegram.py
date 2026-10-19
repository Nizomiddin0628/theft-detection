"""Sinov uchun soxta Telegram Bot API serveri (internet kerak emas).

Tarmoqni taqlid qilish (ixtiyoriy): MOCK_TG_DELAY — har so'rovga qo'shimcha kechikish (soniya),
MOCK_TG_BW — yuklash tezligi (bayt/soniya; video yuklash vaqti shunga qarab hisoblanadi).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()
CALLS: list[dict] = []
UPDATES: list[dict] = []
FAIL_NEXT: dict[str, int] = {}     # method -> keyingi shuncha chaqiruv xato bilan qaytadi (tarmoq xatosi taqlidi)
FAIL_STATUS: dict[str, int] = {}   # method -> xato kodi (sukut 500; 403 — "bot bloklangan")
FAIL_DESC: dict[str, str] = {}     # 3.2.0: method -> xato matni (masalan "Forbidden: not enough rights ...")
HTML_NEXT: dict[str, int] = {}     # 3.2.0: method -> keyingi shuncha javob JSON emas (nginx'ning HTML 502 sahifasi)
MIGRATE: dict[str, int] = {}       # 3.2.0: chat_id -> superguruhning yangi raqami (400 + migrate_to_chat_id)
_uid = [1000]
_mid = [1]


def push_update(upd: dict) -> None:
    _uid[0] += 1
    upd["update_id"] = _uid[0]
    UPDATES.append(upd)


def text_message(chat_id: int, text: str, first_name: str = "Qorovul") -> None:
    push_update({"message": {"message_id": _mid[0], "date": int(time.time()), "text": text,
                             "chat": {"id": chat_id, "type": "private"},
                             "from": {"id": chat_id, "first_name": first_name, "username": "qorovul1"}}})


def callback(chat_id: int, data: str, message_id: int = 1) -> None:
    push_update({"callback_query": {"id": f"cb{_uid[0]}", "data": data, "from": {"id": chat_id, "first_name": "Qorovul"},
                                    "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}}}})


@app.post("/bot{token}/{method}")
async def method(token: str, method: str, request: Request):
    ctype = request.headers.get("content-type", "")
    files = {}
    if "multipart" in ctype:
        form = await request.form()
        data = {}
        for k, v in form.items():
            if hasattr(v, "read"):
                content = await v.read()
                files[k] = {"filename": v.filename, "size": len(content), "head": content[:12].hex()}
            else:
                data[k] = v
    else:
        try:
            data = await request.json()
        except Exception:
            data = {}
    delay = float(os.environ.get("MOCK_TG_DELAY", "0") or 0)
    bw = float(os.environ.get("MOCK_TG_BW", "0") or 0)
    up = sum(f["size"] for f in files.values())
    if delay or (bw and up):
        await asyncio.sleep(delay + (up / bw if bw else 0))
    CALLS.append({"method": method, "token": token, "data": data, "files": files, "t": time.time()})
    if HTML_NEXT.get(method, 0) > 0:
        HTML_NEXT[method] -= 1
        return HTMLResponse("<html><body><h1>502 Bad Gateway</h1></body></html>", status_code=502)
    if str(data.get("chat_id", "")) in MIGRATE:
        return JSONResponse(status_code=400, content={
            "ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup chat",
            "parameters": {"migrate_to_chat_id": MIGRATE[str(data.get("chat_id"))]}})
    if FAIL_NEXT.get(method, 0) > 0:
        FAIL_NEXT[method] -= 1
        code = FAIL_STATUS.get(method, 500)
        desc = FAIL_DESC.get(method) or ("Forbidden: bot was blocked by the user" if code == 403 else "soxta tarmoq xatosi")
        return JSONResponse(status_code=code, content={"ok": False, "error_code": code, "description": desc})
    if method == "getMe":
        return {"ok": True, "result": {"id": 42, "is_bot": True, "first_name": "Hushyor", "username": "hushyor_test_bot"}}
    if method == "getUpdates":
        offset = int(data.get("offset", 0) or 0)
        for _ in range(10):
            res = [u for u in UPDATES if u["update_id"] >= offset]
            if res:
                return {"ok": True, "result": res}
            await asyncio.sleep(0.1)
        return {"ok": True, "result": []}
    now = int(time.time())
    if method in ("sendMessage", "sendVideo", "sendPhoto"):
        _mid[0] += 1
        res = {"message_id": _mid[0], "date": now, "chat": {"id": int(data.get("chat_id", 0))}}
        if method == "sendPhoto":
            res["photo"] = [{"file_id": f"photo{_mid[0]}", "width": 320, "height": 180}]
        if method == "sendVideo":
            res["video"] = {"file_id": f"video{_mid[0]}"}
        return {"ok": True, "result": res}
    if method == "editMessageMedia":
        media = data.get("media")
        if isinstance(media, str):
            try:
                media = json.loads(media)
            except ValueError:
                media = {}
        ref = (media or {}).get("media", "")
        if ref.startswith("attach://") and ref[9:] not in files:
            return {"ok": False, "error_code": 400, "description": "Bad Request: attached file not found"}
        fid = ref if ref and not ref.startswith("attach://") else f"video_edit{_uid[0]}"
        return {"ok": True, "result": {"message_id": int(data.get("message_id", 0)), "date": now,
                                       "chat": {"id": int(data.get("chat_id", 0))}, "video": {"file_id": fid}}}
    if method in ("editMessageCaption", "editMessageText"):
        return {"ok": True, "result": {"message_id": int(data.get("message_id", 0)), "date": now}}
    return {"ok": True, "result": True}


def serve(port: int = 8765) -> threading.Thread:
    import uvicorn

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    return th


def calls(method: str) -> list[dict]:
    return [c for c in CALLS if c["method"] == method]


if __name__ == "__main__":
    serve()
    print(json.dumps({"ok": True}))
    while True:
        time.sleep(1)
