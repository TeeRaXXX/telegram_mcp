"""Exercise telegram-mcp without Telegram: a fake Bot API for the bot, hand-made Telethon objects for the rest.

    .venv/Scripts/python.exe test_offline.py
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import common  # noqa: E402

TEMP = Path(tempfile.mkdtemp(prefix="tgmcp-"))
common.DATA = TEMP
common.ME_FILE = TEMP / "me.json"
common.BOT_FILE = TEMP / "bot.json"
common.BOT_STATE = TEMP / "bot_state.json"
common.ANTHROPIC_FILE = TEMP / "anthropic.json"
common.ACCESS_FILE = TEMP / "access.json"
common.USAGE_FILE = TEMP / "usage.json"
common.RESPONDER_FILE = TEMP / "responder.json"

import server  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from telethon import types  # noqa: E402
from telethon.tl.patched import Message  # noqa: E402

failures = []


def check(name, condition, detail=None):
    print(("ok   " if condition else "FAIL ") + name + ("" if condition or detail is None else f" - {detail}"))
    if not condition:
        failures.append(name)


async def refused(call, needle):
    try:
        await call
    except ToolError as error:
        return needle in str(error), str(error)
    return False, "no error"


NOW = int(datetime.now(timezone.utc).timestamp())
MASHA = {"id": 555, "type": "private", "first_name": "Маша", "last_name": "Иванова", "username": "masha"}
FAMILY = {"id": -100777, "type": "supergroup", "title": "Семья"}
UPDATES = [
    {"update_id": 1, "message": {"message_id": 10, "date": NOW - 3600, "chat": MASHA,
                                 "from": {"id": 555, "first_name": "Маша", "last_name": "Иванова"}, "text": "/start"}},
    {"update_id": 2, "my_chat_member": {"date": NOW - 1800, "chat": FAMILY, "from": {"id": 555}}},
]
calls = []
conflict = False
next_message_id = 42


def fake_request(token, method, params=None, timeout=30):
    global next_message_id
    params = params or {}
    calls.append((method, params))
    bot = {"id": 1, "is_bot": True, "first_name": "Bot"}
    if method == "getUpdates":
        if conflict:
            raise common.BotApiError(method, 409, "Conflict: can't use getUpdates method while webhook is active")
        return UPDATES
    if method == "getChat":
        chats = {555: MASHA, -100777: FAMILY}
        if params["chat_id"] in chats:
            return chats[params["chat_id"]]
        raise common.BotApiError(method, 400, "Bad Request: chat not found")
    if method == "sendMessage":
        next_message_id += 1
        text = params["text"].replace("<b>", "").replace("</b>", "")
        return {"message_id": next_message_id - 1, "date": NOW, "chat": FAMILY, "from": bot, "text": text}
    if method == "editMessageText":
        if params["text"] == "same":
            raise common.BotApiError(method, 400, "Bad Request: message is not modified: specified new message "
                                                  "content and reply markup are exactly the same")
        return {"message_id": params["message_id"], "date": NOW, "edit_date": NOW + 60, "chat": FAMILY, "from": bot,
                "text": params["text"]}
    if method == "getMe":
        return {"id": 1, "is_bot": True, "first_name": "Bot", "username": "family_bot",
                "can_read_all_group_messages": False}
    raise AssertionError(method)


common.bot_request = fake_request


async def main():
    global conflict

    check("exact name", server.match_score("Маша", ["маша"]) == 1.0)
    check("start of a word", server.match_score("маш", ["Маша Иванова"]) == 0.9)
    check("words in another order", server.match_score("иванова маша", ["Маша Иванова"]) == 0.85)
    check("ё is е", server.match_score("алена", ["Алёна"]) == 1.0)
    check("a different name is out", server.match_score("Петя", ["Маша Иванова"]) < 0.5)
    check("empty query lists everything", server.match_score("", ["x"]) == 0.5)

    ok, detail = await refused(server.whoami(account="me"), "login.py")
    check("personal account not logged in is explained", ok and " me'" in detail, detail)
    ok, detail = await refused(server.whoami(account="bot"), "login.py")
    check("bot not logged in is explained", ok and " bot'" in detail, detail)

    common.save_json(common.BOT_FILE, {"token": "123:fake"})
    who = json.loads(await server.whoami(account="bot"))
    check("bot whoami", who["username"] == "@family_bot" and "group_privacy" in who, who)

    found = json.loads(await server.find_chats(query="маша", account="bot"))
    check("bot finds a person by name", [chat["id"] for chat in found["chats"]] == [555], found)
    listed = json.loads(await server.find_chats(account="bot"))
    check("bot lists its chats, latest first", [chat["id"] for chat in listed["chats"]] == [-100777, 555], listed)

    ok, detail = await refused(server.send_message(chat="Семья", text="x", account="bot"), "not a chat reference")
    check("a bare name is refused", ok, detail)
    ok, detail = await refused(server.send_message(chat="12345", text="x", account="bot"), "pressed Start")
    check("a chat the bot cannot reach is explained", ok, detail)
    ok, detail = await refused(server.send_message(chat="me", text="x", account="bot"), "personal account")
    check("'me' for the bot without the personal login is explained", ok, detail)

    sent = json.loads(await server.send_message(chat="-100777", text="Купить: хлеб", account="bot"))
    check("bot sends", sent["message_id"] == 42 and sent["chat"]["name"] == "Семья", sent)
    edited = json.loads(await server.edit_message(chat="-100777", message_id=42, text="Купить: хлеб, молоко",
                                                  account="bot"))
    check("bot edit gives the old and the new text",
          edited.get("old_text") == "Купить: хлеб" and edited["new_text"] == "Купить: хлеб, молоко", edited)
    read = json.loads(await server.read_messages(chat="-100777", account="bot"))
    last = read["messages"][-1]
    check("bot read shows the edited list", last["text"] == "Купить: хлеб, молоко" and last.get("edited"), read)
    ok, detail = await refused(server.edit_message(chat="555", message_id=10, text="hi", account="bot"), "only its own")
    check("bot will not edit someone else's message", ok, detail)
    ok, detail = await refused(server.edit_message(chat="-100777", message_id=42, text="same", account="bot"),
                               "same as the old one")
    check("an unchanged edit is explained", ok, detail)

    await server.send_message(chat="-100777", text="<b>Список</b>", account="bot", format="html")
    ok, detail = await refused(server.edit_message(chat="-100777", message_id=43, text="Список", account="bot"),
                               "formatting")
    check("plain text over the bot's html is refused", ok, detail)
    read = json.loads(await server.read_messages(chat="-100777", account="bot", from_me=True))
    check("bot read keeps the html it sent", read["messages"][-1].get("html") == "<b>Список</b>", read)
    dropped = json.loads(await server.edit_message(chat="-100777", message_id=43, text="Список", account="bot",
                                                   drop_formatting=True))
    check("drop_formatting lets it through", dropped["new_text"] == "Список", dropped)
    read = json.loads(await server.read_messages(chat="-100777", account="bot", from_me=True))
    check("the html is gone after a plain edit", "html" not in read["messages"][-1], read)

    conflict = True
    warned = json.loads(await server.find_chats(account="bot"))
    check("a webhook on the bot is reported", "another program" in warned.get("warning", ""), warned)
    check("known chats survive the conflict", len(warned["chats"]) == 2, warned)

    masha = types.User(id=555, first_name="Маша", last_name="Иванова", username="masha", contact=True)
    entry = server.personal_chat_entry(masha)
    check("personal chat entry",
          entry == {"id": 555, "name": "Маша Иванова", "kind": "user", "username": "@masha", "in_contacts": True},
          entry)
    check("saved messages", server.personal_chat_entry(types.User(id=1, is_self=True, first_name="I"))["name"]
          == "Saved Messages")
    family = types.Channel(id=777, title="Семья", photo=types.ChatPhotoEmpty(), date=None, megagroup=True)
    check("a supergroup is a group", server.personal_chat_entry(family) == {"id": -1000000000777, "name": "Семья",
                                                                           "kind": "group"})

    old = datetime.now(timezone.utc) - timedelta(hours=50)
    message = Message(id=7, peer_id=types.PeerUser(555), date=old, message="Купить: хлеб", out=True,
                      entities=[types.MessageEntityBold(offset=0, length=6)])
    item = server.personal_message(message)
    check("personal message: author, age, html",
          item["from"] == "me" and item["age_hours"] >= 50 and item.get("html") == "<strong>Купить</strong>: хлеб",
          item)
    ok, detail = await refused(server.personal_chat(None, "Маша"), "not a chat reference")
    check("the personal account refuses a bare name too", ok, detail)

    state = json.loads(common.BOT_STATE.read_text(encoding="utf-8"))
    check("state keeps no None fields", all(None not in item.values() for item in state["messages"]), state)
    check("no lock file left behind", not any(TEMP.glob("*.lock")), list(TEMP.iterdir()))


def login_checks():
    """login.py me against a fake Telegram: a wrong code and a wrong password on the way, then a second attempt that
    reuses the api_id - and the server not logged in while only the api_id is saved."""
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    from telethon import errors

    import login

    common.save_json(common.ME_FILE, {"api_id": 1, "api_hash": "x"})
    try:
        asyncio.run(server.whoami(account="me"))
        check("an api_id without a session is not a login", False, "no error")
    except ToolError as error:
        check("an api_id without a session is not a login", "login.py" in str(error), str(error))
    common.ME_FILE.unlink()

    class FakeClient:
        authorized = True

        def __init__(self, session, api_id, api_hash, **options):
            self.session = SimpleNamespace(save=lambda: "SESSION")

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def is_user_authorized(self):
            return self.authorized

        async def send_code_request(self, phone):
            return SimpleNamespace(phone_code_hash="hash")

        async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
            if password is None:
                if code != "12345":
                    raise errors.PhoneCodeInvalidError(request=None)
                raise errors.SessionPasswordNeededError(request=None)
            if password != "right":
                raise errors.PasswordHashInvalidError(request=None)

        async def get_me(self):
            return types.User(id=1, first_name="Oleg", username="oleg")

    def attempt(answers, secrets):
        answers, secrets = iter(answers), iter(secrets)
        login.input = lambda prompt="": next(answers)
        login.getpass = SimpleNamespace(getpass=lambda prompt="": next(secrets))
        printed = io.StringIO()
        with redirect_stdout(printed):
            login.login_me()
        return printed.getvalue()

    login.TelegramClient = FakeClient
    printed = attempt(["1234567", "+79990000000", "00000", "12345"], ["0123456789abcdef", "wrong", "wrong", "wrong",
                                                                      "right"])
    check("login gets past a wrong code and three wrong passwords",
          common.load_json(common.ME_FILE) == {"api_id": 1234567, "api_hash": "0123456789abcdef",
                                               "session": "SESSION"}, printed)
    check("login names the right code and the keyboard layout", "not the letters" in printed and "layout" in printed,
          printed)

    common.save_json(common.ME_FILE, {"api_id": 1234567, "api_hash": "0123456789abcdef"})
    printed = attempt(["+79990000000", "12345"], ["right"])
    check("a second attempt reuses the api_id",
          common.load_json(common.ME_FILE).get("session") == "SESSION" and "earlier attempt" in printed, printed)

    real_string_session = login.StringSession
    login.StringSession = lambda value=None: value
    try:
        common.save_json(common.ME_FILE, {"api_id": 1234567, "api_hash": "0123456789abcdef", "session": "OLD"})
        printed = attempt([], [])
        check("a session Telegram still accepts is kept",
              common.load_json(common.ME_FILE).get("session") == "OLD" and "already logged in" in printed, printed)
        FakeClient.authorized = False
        printed = attempt(["+79990000000", "12345"], ["right"])
        check("a session Telegram has ended is replaced by a new login, not refused as already logged in",
              common.load_json(common.ME_FILE).get("session") == "SESSION" and "no longer accepts" in printed, printed)
    finally:
        login.StringSession, FakeClient.authorized = real_string_session, True
    common.ME_FILE.unlink()


asyncio.run(main())
login_checks()
print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
