"""Sinov uchun soxta Telegram Bot API serveri (internet kerak emas)."""
from __future__ import annotations

import asyncio
import json
import threading
import time

from fastapi import FastAPI, Request

app = FastAPI()
CALLS: list[dict] = []
UPDATES: list[dict] = []
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
    CALLS.append({"method": method, "token": token, "data": data, "files": files, "t": time.time()})
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
    if method in ("sendMessage", "sendVideo", "sendPhoto"):
        _mid[0] += 1
        return {"ok": True, "result": {"message_id": _mid[0], "chat": {"id": int(data.get("chat_id", 0))}}}
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
