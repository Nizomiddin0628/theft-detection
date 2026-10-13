"""Eski botdan qolgan menyularni tozalash sinovi (soxta Telegram API bilan).

Ishga tushirish:  python -m tests.test_bot_menu
"""
from __future__ import annotations

import os
import sys
import time

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
os.environ["TELEGRAM_API_BASE"] = "http://127.0.0.1:8769"
os.environ["MOCK_TG_DELAY"] = "0"

from tests import mock_telegram as tg  # noqa: E402

tg.serve(8769)

from app import db  # noqa: E402
from app.telegram_bot import TelegramBot  # noqa: E402


def wait_for(cond, sec=8.0):
    t0 = time.time()
    while time.time() - t0 < sec:
        if cond():
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    results = []

    def ok(name, cond, info=""):
        results.append(bool(cond))
        print(("  OK   " if cond else "  FAIL ") + name + (f"  — {info}" if info else ""))

    db.init_db()
    db.execute("DELETE FROM subscribers")
    for cid in (501, 502):
        db.execute("INSERT INTO subscribers(chat_id, name, username, created_at, active, kb_cleared) "
                   "VALUES(?,?,?,?,1,0)", (cid, "Qorovul", "q", time.time()))
    tg.CALLS.clear()
    bot = TelegramBot()
    bot.start()

    def removed_for(cid):
        return [c for c in tg.calls("sendMessage") if int(c["data"].get("chat_id", 0)) == cid
                and (c["data"].get("reply_markup") or {}).get("remove_keyboard")]

    ok("Bot ulandi", wait_for(lambda: bot.me is not None), bot.status)
    ok("Obunachilarga eski pastki tugmalarni olib tashlash xabari",
       wait_for(lambda: removed_for(501) and removed_for(502)))
    dels = tg.calls("deleteMyCommands")
    scopes = {c["data"]["scope"]["type"] for c in dels}
    langs = {c["data"].get("language_code", "") for c in dels}
    ok("Eski buyruqlar barcha doira va tillarda o'chirildi", len(dels) >= 16 and len(scopes) == 4 and
       {"", "uz", "ru", "en"} <= langs, f"{len(dels)} ta, {sorted(scopes)}")
    sm = tg.calls("setMyCommands")
    cmds = [c["command"] for c in (sm[-1]["data"].get("commands") if sm else [])]
    ok("Yangi buyruqlar o'rnatildi", cmds == [c["command"] for c in TelegramBot.COMMANDS], str(cmds))
    mb = tg.calls("setChatMenuButton")
    ok("Menyu tugmasi 'buyruqlar' ga qaytarildi", mb and mb[-1]["data"]["menu_button"]["type"] == "commands")
    ok("Bazada belgilandi (kb_cleared=1)",
       db.scalar("SELECT COUNT(*) FROM subscribers WHERE kb_cleared=1") == 2)
    bot.stop()
    # Qayta ishga tushganda — takroriy xabar yuborilmaydi
    time.sleep(0.5)
    n_before = len(removed_for(501))
    bot2 = TelegramBot()
    bot2._reset_menus()
    ok("Qayta ishga tushganda takroriy xabar yo'q", len(removed_for(501)) == n_before)
    # Yangi foydalanuvchiga birinchi javob — pastki tugmalarsiz (remove_keyboard)
    tg.CALLS.clear()
    bot2._reply(777, "salom")
    bot2._reply(777, "yana")
    r = [c for c in tg.calls("sendMessage") if int(c["data"].get("chat_id", 0)) == 777]
    ok("Yangi chatga birinchi javobda remove_keyboard, keyingisida yo'q",
       len(r) == 2 and (r[0]["data"].get("reply_markup") or {}).get("remove_keyboard")
       and not r[1]["data"].get("reply_markup"))
    print(f"\nNatija: {sum(results)}/{len(results)} sinov o'tdi")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
