#!/usr/bin/env python3
"""Telegram for Claude: the user's own account (through Telethon) and their bot (through the Bot API) behind one
MCP server - find a chat, read it, send a message, edit a message - plus the list of people the bot answers when
responder.py runs.

Log in with login.py and check the tools with client.py; README.md has both.
"""

import asyncio
import difflib
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from telethon import TelegramClient, errors, functions, types, utils
from telethon.extensions import html
from telethon.sessions import StringSession

import common
from common import bot_chat_entry, bot_message_item, bot_state, remember

INSTRUCTIONS = """\
Telegram on the user's behalf, from two accounts: account='me' is the user's own account, account='bot' is \
their bot.

Sending and editing speak for the user. Before send_message or edit_message, show the user the chat (name and \
id), the account and the exact final text, and wait for an explicit yes in the conversation; one yes covers one \
message.

Every tool but find_chats takes a chat id from find_chats, an @username or 'me' (for account='me' the user's \
Saved Messages, for account='bot' the user's own chat with the bot), never a bare name.

Telegram lets only the author edit a message, and a personal account's message in an ordinary chat only within \
48 hours of sending it; after that, send a new message. A bot writes only to people who pressed Start in it and \
to groups and channels it was added to, and it cannot read a chat's history.

While responder.py runs, the bot answers people on its list by itself, with Claude, billed to the user. bot_access \
shows the list; bot_allow and bot_deny change it - only when the user asks."""

LOGIN = f"{common.ROOT / '.venv' / 'Scripts' / 'python.exe'} {common.ROOT / 'login.py'}"
SELF_WORDS = {"me", "self", "saved", "saved messages", "избранное"}
FORMATTING = tuple(getattr(types, name) for name in (
    "MessageEntityBold", "MessageEntityItalic", "MessageEntityUnderline", "MessageEntityStrike",
    "MessageEntityCode", "MessageEntityPre", "MessageEntityTextUrl", "MessageEntityMentionName",
    "MessageEntitySpoiler", "MessageEntityBlockquote", "MessageEntityCustomEmoji") if hasattr(types, name))
EDIT_WINDOW_HOURS = 48
DIALOGS_SECONDS = 120
BOT_READ_NOTE = (
    "A bot cannot read a chat's history: these are the messages meant for the bot that reached it while this server "
    "or responder.py was looking (Telegram holds them for 24 hours) and the ones the bot sent. In privacy mode, the "
    "default, a group sends the bot only commands and replies to its messages - not mentions.")
REFUSALS = {
    "PeerFloodError": "Telegram limits this account's messages to people who do not have it in their contacts",
    "UserIsBlockedError": "this person has blocked the account",
    "YouBlockedUserError": "the account has blocked this person",
    "ChatWriteForbiddenError": "the account cannot write in this chat",
    "MessageTooLongError": "the text is longer than Telegram's 4096 characters",
    "MessageEmptyError": "the text is empty",
}

Account = Annotated[Literal["me", "bot"], Field(
    description="'me' acts as the user's own Telegram account, 'bot' as their bot")]
Chat = Annotated[str, Field(
    description="A chat id from find_chats, an @username, or 'me' (Saved Messages for account='me', the user's "
                "own chat with the bot for account='bot')")]
Format = Annotated[Literal["plain", "html"], Field(
    description="'plain' sends the text as it is; 'html' reads <b>, <i>, <u>, <s>, <code>, <pre>, <a href> and "
                "<blockquote> tags")]
Person = Annotated[str, Field(
    description="A person's Telegram user id (find_chats gives it for a user) or @username - not a name")]

READS = ToolAnnotations(read_only_hint=True, open_world_hint=True)
SENDS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
EDITS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)
SETTINGS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

_personal = None
_personal_lock = asyncio.Lock()
_dialogs = []
_dialogs_at = 0.0


@asynccontextmanager
async def lifespan(server):
    try:
        yield None
    finally:
        if _personal is not None:
            await _personal.disconnect()


mcp = MCPServer("telegram", instructions=INSTRUCTIONS, log_level="WARNING", lifespan=lifespan)


# --- shared --------------------------------------------------------------------------------------------------

def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=1)


def norm(text):
    return (text or "").casefold().replace("ё", "е").strip()


def match_score(query, names):
    """How well one of a chat's names matches the query: 1 for the whole name, 0.9 when a word of the name starts
    with it, 0.85 when every word of the query is in the name, 0.8 when it is inside the name, up to 0.75 for a
    near spelling. An empty query matches everything at 0.5, the cut-off."""
    wanted = norm(query).lstrip("@")
    if not wanted:
        return 0.5
    best = 0.0
    for name in names:
        name = norm(name).lstrip("@")
        if not name:
            continue
        if name == wanted:
            return 1.0
        words = name.split()
        if wanted in name:
            best = max(best, 0.9 if any(word.startswith(wanted) for word in words) else 0.8)
        elif all(part in name for part in wanted.split()):
            best = max(best, 0.85)
        else:
            for candidate in (name, *words):
                best = max(best, difflib.SequenceMatcher(None, wanted, candidate).ratio() * 0.75)
    return best


def moment(value):
    return datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) else value


def local_time(value):
    return moment(value).astimezone().strftime("%Y-%m-%d %H:%M")


def hours_since(value):
    return round((datetime.now(timezone.utc) - moment(value)).total_seconds() / 3600, 1)


def preview(text, length=100):
    flat = " ".join((text or "").split())
    return flat if len(flat) <= length else flat[:length - 1] + "…"


# --- the personal account ------------------------------------------------------------------------------------

async def personal():
    """The user's own account, connected on first use and kept for the life of the server."""
    global _personal
    async with _personal_lock:
        if _personal is None:
            saved = common.load_json(common.ME_FILE)
            if not saved or not saved.get("session"):
                raise ToolError(f"the personal account is not logged in: the user runs '{LOGIN} me' in a terminal")
            client = TelegramClient(StringSession(saved["session"]), saved["api_id"], saved["api_hash"],
                                    receive_updates=False, **common.DEVICE)
            client.parse_mode = None
            try:
                await client.connect()
                authorized = await client.is_user_authorized()
            except OSError as error:
                raise ToolError(f"cannot reach Telegram: {error}") from None
            if not authorized:
                await client.disconnect()
                raise ToolError("the personal account's session has ended (under Settings > Devices?): the user runs "
                                f"'{LOGIN} me' again")
            _personal = client
        elif not _personal.is_connected():
            await _personal.connect()
        return _personal


async def dialogs(client, fresh=False):
    global _dialogs, _dialogs_at
    if fresh or not _dialogs or time.monotonic() - _dialogs_at > DIALOGS_SECONDS:
        _dialogs = await client.get_dialogs(limit=None)
        _dialogs_at = time.monotonic()
    return _dialogs


def refusal(error):
    if isinstance(error, errors.FloodWaitError):
        return f"too many requests, Telegram asks to wait {error.seconds} seconds"
    return REFUSALS.get(type(error).__name__, str(error))


def parse_mode(format):
    return "html" if format == "html" else None


def kind_of(entity):
    if isinstance(entity, types.User):
        return "saved messages" if entity.is_self else "bot" if entity.bot else "user"
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        return "group" if entity.megagroup else "channel"
    return "group"


def handles(entity):
    names = [entity.username] if getattr(entity, "username", None) else []
    names += [item.username for item in getattr(entity, "usernames", None) or [] if item.active]
    return list(dict.fromkeys(names))


def personal_chat_entry(entity):
    entry = {"id": utils.get_peer_id(entity), "name": utils.get_display_name(entity) or "(no name)",
             "kind": kind_of(entity)}
    if isinstance(entity, types.User) and entity.is_self:
        entry["name"] = "Saved Messages"
        return entry
    if handles(entity):
        entry["username"] = "@" + handles(entity)[0]
    if isinstance(entity, types.User) and entity.contact:
        entry["in_contacts"] = True
    return entry


async def personal_chat(client, chat):
    """The entity behind a chat reference: an id from find_chats, an @username or t.me link, or 'me'."""
    reference = chat.strip()
    if norm(reference) in SELF_WORDS:
        return await client.get_me()
    numeric = re.fullmatch(r"-?\d+", reference)
    if not (numeric or reference.startswith("@") or "t.me/" in reference):
        raise ToolError(f"{reference!r} is not a chat reference: pass an id from find_chats, an @username or 'me', "
                        "not a name")
    target = int(reference) if numeric else reference
    try:
        try:
            return await client.get_entity(target)
        except ValueError:
            if not numeric:
                raise
            await dialogs(client, fresh=True)
            return await client.get_entity(target)
    except (ValueError, TypeError, errors.RPCError) as error:
        raise ToolError(f"the personal account cannot find the chat {reference}: {error}. Look it up with "
                        "find_chats.") from None


def media_kind(message):
    for kind in ("sticker", "voice", "video_note", "gif", "video", "audio", "photo", "contact", "geo", "venue",
                 "poll", "dice", "game", "invoice", "document"):
        if getattr(message, kind, None):
            return kind.replace("_", " ")
    return type(message.media).__name__.removeprefix("MessageMedia").lower()


def personal_message(message):
    item = {"id": message.id, "date": local_time(message.date), "age_hours": hours_since(message.date)}
    if message.out:
        item["from"] = "me"
    else:
        item["from"] = utils.get_display_name(message.sender) if message.sender else "unknown"
    text = getattr(message, "message", None) or ""
    entities = getattr(message, "entities", None) or []
    if text:
        item["text"] = text
        if any(isinstance(entity, FORMATTING) for entity in entities):
            item["html"] = html.unparse(text, entities)
    if getattr(message, "edit_date", None):
        item["edited"] = True
    reply = getattr(message, "reply_to", None)
    if reply is not None and getattr(reply, "reply_to_msg_id", None):
        item["reply_to"] = reply.reply_to_msg_id
    media = getattr(message, "media", None)
    if media is not None and not isinstance(media, types.MessageMediaWebPage):
        item["media"] = media_kind(message)
    if getattr(message, "action", None) is not None:
        item["event"] = type(message.action).__name__.removeprefix("MessageAction")
    return item


async def whoami_personal():
    me = await (await personal()).get_me()
    entry = {"account": "me", "id": me.id, "name": utils.get_display_name(me)}
    if me.username:
        entry["username"] = "@" + me.username
    return entry


async def find_personal(query, limit):
    client = await personal()
    found = {}
    for dialog in await dialogs(client):
        entity = dialog.entity
        names = [dialog.name, *handles(entity)]
        if isinstance(entity, types.User) and entity.is_self:
            names += ["Saved Messages", "Избранное", "me"]
        score = match_score(query, names)
        if score < 0.5:
            continue
        entry = personal_chat_entry(entity)
        if dialog.archived:
            entry["archived"] = True
        if dialog.unread_count:
            entry["unread"] = dialog.unread_count
        last = dialog.message
        if last is not None and getattr(last, "date", None):
            entry["last_message"] = {"date": local_time(last.date), "from_me": bool(last.out),
                                     "text": preview(getattr(last, "message", None))}
        found[entry["id"]] = (score, dialog.date.timestamp() if dialog.date else 0.0, entry)

    contacts = await client(functions.contacts.GetContactsRequest(hash=0))
    for user in getattr(contacts, "users", []):
        entry = personal_chat_entry(user)
        if entry["id"] in found:
            continue
        score = match_score(query, [utils.get_display_name(user), *handles(user)])
        if score >= 0.5:
            entry["no_chat_yet"] = True
            found[entry["id"]] = (score, 0.0, entry)

    ranked = sorted(found.values(), key=lambda item: (-item[0], -item[1]))
    return {"account": "me", "chats": [entry for _, _, entry in ranked[:limit]]}


async def read_personal(chat, limit, search, from_me, before_id):
    client = await personal()
    entity = await personal_chat(client, chat)
    options = {"limit": limit}
    if search:
        options["search"] = search
    if from_me:
        options["from_user"] = "me"
    if before_id:
        options["offset_id"] = before_id
    try:
        messages = await client.get_messages(entity, **options)
    except errors.RPCError as error:
        raise ToolError(f"Telegram refused to show the chat: {refusal(error)}") from None
    return {"account": "me", "chat": personal_chat_entry(entity),
            "messages": [personal_message(message) for message in reversed(messages)]}


async def send_personal(chat, text, format, reply_to):
    client = await personal()
    entity = await personal_chat(client, chat)
    try:
        message = await client.send_message(entity, text, parse_mode=parse_mode(format), reply_to=reply_to)
    except errors.RPCError as error:
        raise ToolError(f"Telegram refused the message: {refusal(error)}") from None
    return {"account": "me", "sent": True, "chat": personal_chat_entry(entity), "message_id": message.id,
            "date": local_time(message.date), "text": message.message}


async def edit_personal(chat, message_id, text, format, drop_formatting):
    client = await personal()
    entity = await personal_chat(client, chat)
    message = await client.get_messages(entity, ids=message_id)
    if message is None:
        raise ToolError(f"there is no message {message_id} in {personal_chat_entry(entity)['name']}")
    if not message.out:
        author = utils.get_display_name(message.sender) if message.sender else "someone else"
        raise ToolError(f"message {message_id} was written by {author}; Telegram lets only the author edit it")
    if format == "plain" and not drop_formatting and any(
            isinstance(item, FORMATTING) for item in getattr(message, "entities", None) or []):
        raise ToolError("the message has formatting (bold, links, ...) that plain text would erase: edit the 'html' "
                        "read_messages gives for it and pass format='html', or pass drop_formatting=true")
    try:
        edited = await client.edit_message(entity, message_id, text, parse_mode=parse_mode(format),
                                           link_preview=isinstance(message.media, types.MessageMediaWebPage))
    except errors.MessageEditTimeExpiredError:
        raise ToolError(f"Telegram no longer lets this message be edited: it is {hours_since(message.date)} hours "
                        f"old and ordinary chats allow {EDIT_WINDOW_HOURS}. Send a new message instead.") from None
    except errors.MessageNotModifiedError:
        raise ToolError("the new text is the same as the old one, nothing changed") from None
    except errors.RPCError as error:
        raise ToolError(f"Telegram refused the edit: {refusal(error)}") from None
    return {"account": "me", "edited": True, "chat": personal_chat_entry(entity), "message_id": message_id,
            "old_text": message.message or "", "new_text": edited.message}


# --- the bot -------------------------------------------------------------------------------------------------

def bot_token():
    saved = common.load_json(common.BOT_FILE)
    if not saved:
        raise ToolError(f"the bot is not logged in: the user runs '{LOGIN} bot' in a terminal")
    return saved["token"]


async def bot_call(method, **params):
    """One Bot API call, off the event loop. Parameters that are None are left out."""
    token = bot_token()
    arguments = {key: value for key, value in params.items() if value is not None}
    return await asyncio.to_thread(common.bot_request, token, method, arguments)


_bot_me = None


async def bot_me():
    """The bot's getMe, asked once per server; None while Telegram cannot be reached."""
    global _bot_me
    if _bot_me is None:
        try:
            _bot_me = await bot_call("getMe")
        except common.BotApiError:
            return None
    return _bot_me


async def poll_bot():
    """Take in what reached the bot in the last 24 hours. The updates are not confirmed, so nothing is taken away
    from another program reading the same bot; while responder.py runs it is the one reading them, and it records
    them in the same file. Group messages not meant for the bot are not kept. Returns a warning when the updates
    cannot be read."""
    if common.responder_alive():
        return None
    try:
        updates = await bot_call("getUpdates", timeout=0, limit=100)
    except common.BotApiError as error:
        if error.code == 409:
            return ("another program reads this bot's updates (a webhook or its own polling), so what people send "
                    "to the bot cannot be seen here")
        raise ToolError(str(error)) from None
    if updates:
        common.remember_updates(updates, bot=await bot_me())
    return None


async def bot_chat(chat):
    """The Bot API chat behind a reference: an id, an @username, or 'me' - the user's own chat with the bot."""
    reference = chat.strip()
    if norm(reference) in SELF_WORDS:
        if not common.ME_FILE.exists():
            raise ToolError("for account='bot', 'me' is the user's own chat with the bot, and finding the user's id "
                            "needs the personal account logged in; pass the id from find_chats(account='bot')")
        target = (await (await personal()).get_me()).id
    elif re.fullmatch(r"-?\d+", reference):
        target = int(reference)
    elif reference.startswith("@"):
        target = reference
    else:
        raise ToolError(f"{reference!r} is not a chat reference: pass an id from find_chats(account='bot'), an "
                        "@username or 'me', not a name")
    try:
        info = await bot_call("getChat", chat_id=target)
    except common.BotApiError as error:
        raise ToolError(f"the bot cannot reach {reference}: {error.description}. A bot writes only to people who "
                        "pressed Start in it and to groups and channels it was added to.") from None
    remember(chats=[(info, 0)])
    return info


async def whoami_bot():
    try:
        me = await bot_call("getMe")
    except common.BotApiError as error:
        if error.code in (401, 404):
            raise ToolError(f"Telegram does not accept the bot's token ({error.description}): the user runs "
                            f"'{LOGIN} bot' again") from None
        raise ToolError(str(error)) from None
    entry = {"account": "bot", "id": me["id"], "name": me.get("first_name", ""), "username": "@" + me["username"]}
    if not me.get("can_read_all_group_messages"):
        entry["group_privacy"] = ("on: in groups Telegram sends the bot only commands and replies to its messages, not "
                                  "mentions - turn it off with /setprivacy in @BotFather and add the bot to its groups "
                                  "again, or make the bot a group admin")
    return entry


async def find_bot(query, limit):
    warning = await poll_bot()
    ranked = []
    for entry in bot_state()["chats"].values():
        score = match_score(query, [entry.get("name"), entry.get("username")])
        if score >= 0.5:
            ranked.append((score, entry.get("seen", 0), {key: value for key, value in entry.items() if key != "seen"}))
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    result = {"account": "bot", "chats": [entry for _, _, entry in ranked[:limit]]}
    if not result["chats"]:
        result["hint"] = ("the bot knows only chats that wrote to it or added it: once the person presses Start in "
                          "the bot, or the bot is added to the group, the chat shows up here")
    if warning:
        result["warning"] = warning
    return result


async def read_bot(chat, limit, search, from_me, before_id):
    warning = await poll_bot()
    info = await bot_chat(chat)
    items = [item for item in bot_state()["messages"] if item["chat_id"] == info["id"]]
    if search:
        items = [item for item in items if norm(search) in norm(item.get("text"))]
    if from_me:
        items = [item for item in items if item["from_bot"]]
    if before_id:
        items = [item for item in items if item["id"] < before_id]
    messages = []
    for item in items[-limit:]:
        message = {"id": item["id"], "date": local_time(item["date"]), "age_hours": hours_since(item["date"]),
                   "from": item["from"]}
        message.update({key: item[key] for key in ("text", "html", "edited", "reply_to", "media") if key in item})
        messages.append(message)
    result = {"account": "bot", "chat": bot_chat_entry(info), "messages": messages, "note": BOT_READ_NOTE}
    if warning:
        result["warning"] = warning
    return result


async def send_bot(chat, text, format, reply_to):
    info = await bot_chat(chat)
    try:
        message = await bot_call("sendMessage", chat_id=info["id"], text=text,
                                 parse_mode="HTML" if format == "html" else None,
                                 reply_parameters={"message_id": reply_to} if reply_to else None)
    except common.BotApiError as error:
        raise ToolError(f"Telegram refused the bot's message: {error.description}") from None
    remember(messages=[{**bot_message_item(message, from_bot=True), "html": text if format == "html" else None}])
    return {"account": "bot", "sent": True, "chat": bot_chat_entry(info), "message_id": message["message_id"],
            "date": local_time(message["date"]), "text": message.get("text", "")}


async def edit_bot(chat, message_id, text, format, drop_formatting):
    info = await bot_chat(chat)
    known = next((item for item in bot_state()["messages"]
                  if item["chat_id"] == info["id"] and item["id"] == message_id), None)
    if known and not known["from_bot"]:
        raise ToolError(f"message {message_id} was written by {known['from']}; a bot can edit only its own messages")
    if known and known.get("html") and format == "plain" and not drop_formatting:
        raise ToolError("the bot sent this message with formatting that plain text would erase: edit its 'html' from "
                        "read_messages and pass format='html', or pass drop_formatting=true")
    options = {"chat_id": info["id"], "message_id": message_id, "parse_mode": "HTML" if format == "html" else None}
    try:
        try:
            edited = await bot_call("editMessageText", text=text, **options)
        except common.BotApiError as error:
            if "no text in the message" not in error.description:
                raise
            edited = await bot_call("editMessageCaption", caption=text, **options)
    except common.BotApiError as error:
        if "not modified" in error.description:
            raise ToolError("the new text is the same as the old one, nothing changed") from None
        raise ToolError(f"Telegram refused the bot's edit: {error.description}") from None
    result = {"account": "bot", "edited": True, "chat": bot_chat_entry(info), "message_id": message_id}
    if known and known.get("text"):
        result["old_text"] = known["text"]
    if isinstance(edited, dict):
        remember(messages=[{**bot_message_item(edited, from_bot=True), "html": text if format == "html" else None}])
        result["new_text"] = edited.get("text") or edited.get("caption") or text
    else:
        result["new_text"] = text
    return result


# --- whom the bot answers ------------------------------------------------------------------------------------

async def owner_id():
    """The owner's user id: the one the responder recorded, or else the personal account's."""
    access = common.load_access()
    if access["owner_id"] is None and (common.load_json(common.ME_FILE) or {}).get("session"):
        me = await (await personal()).get_me()
        common.change_access(lambda access: access.update(owner_id=me.id))
        return me.id
    return access["owner_id"]


async def resolve_person(person):
    """(user id, name) for a person given as a user id or @username."""
    reference = person.strip()
    numeric = re.fullmatch(r"\d+", reference)
    if not (numeric or re.fullmatch(r"@\w{4,}", reference)):
        raise ToolError(f"{reference!r} is not a person reference: pass a user id (find_chats gives it) or an "
                        "@username, not a name")
    for chat in bot_state()["chats"].values():
        if chat.get("kind") == "user" and (str(chat["id"]) == reference or norm(chat.get("username")) == norm(reference)):
            return chat["id"], chat["name"]
    if (common.load_json(common.ME_FILE) or {}).get("session"):
        client = await personal()
        target = int(reference) if numeric else reference
        try:
            try:
                entity = await client.get_entity(target)
            except ValueError:
                if not numeric:
                    raise
                await dialogs(client, fresh=True)
                entity = await client.get_entity(target)
        except (ValueError, TypeError, errors.RPCError) as error:
            raise ToolError(f"cannot find the person {reference}: {error}") from None
        if not isinstance(entity, types.User):
            raise ToolError(f"{reference} is a chat, not a person")
        return entity.id, utils.get_display_name(entity) or reference
    if numeric:
        return int(reference), reference
    raise ToolError(f"without the personal account logged in, {reference} cannot be turned into a user id: pass the "
                    "number")


@mcp.tool(annotations=READS, structured_output=False)
async def bot_access() -> str:
    """Whom the bot answers by itself with Claude when asked in Telegram, how many questions a day each may ask,
    what they used today (questions, tokens, estimated dollars), the settings it answers with, and whether
    responder.py is running."""
    owner = await owner_id()
    access = common.load_access()
    usage = (common.load_json(common.USAGE_FILE) or {}).get(common.today(), {})
    people = []
    if owner is not None:
        people.append({"id": owner, "name": "owner", "daily_limit": None, "today": usage.get(str(owner), {})})
    for key, entry in access["allowed"].items():
        people.append({"id": int(key), "name": entry.get("name"), "added": entry.get("added"),
                       "daily_limit": common.daily_limit(access, int(key)), "today": usage.get(key, {})})
    status = common.responder_status()
    if status is None:
        responder = {"running": False}
    else:
        responder = {"running": status["alive"], "last_poll": local_time(status.get("polled", 0)),
                     "answered": status.get("answered", 0), "ignored": status.get("ignored", 0)}
        if status.get("last_error"):
            responder["last_error"] = status["last_error"]
    return dump({"owner_id": owner, "people": people, "settings": access["settings"], "responder": responder})


@mcp.tool(annotations=SETTINGS, structured_output=False)
async def bot_allow(
    person: Person,
    daily_limit: Annotated[int | None, Field(
        ge=0, description="Questions a day for this person; leave it out for the default in the settings")] = None,
) -> str:
    """Put a person on the list the bot answers: when they mention the bot, reply to it or write to it privately,
    it answers with Claude, billed to the user. Also changes the daily limit of someone already on the list. Change
    the list only when the user asks."""
    person_id, name = await resolve_person(person)
    if person_id == await owner_id():
        raise ToolError("that is the owner, whom the bot always answers")

    def change(access):
        entry = access["allowed"].get(str(person_id), {"added": common.today()})
        if name != str(person_id) or "name" not in entry:
            entry["name"] = name
        if daily_limit is not None:
            entry["daily_limit"] = daily_limit
        access["allowed"][str(person_id)] = entry

    access = common.change_access(change)
    return dump({"allowed": True, "id": person_id, "name": access["allowed"][str(person_id)]["name"],
                 "daily_limit": common.daily_limit(access, person_id)})


@mcp.tool(annotations=SETTINGS, structured_output=False)
async def bot_deny(person: Person) -> str:
    """Take a person off the list the bot answers; from then on the bot ignores them without calling Claude. Change
    the list only when the user asks."""
    person_id, name = await resolve_person(person)
    removed = []
    common.change_access(lambda access: removed.append(access["allowed"].pop(str(person_id), None)))
    return dump({"denied": True, "id": person_id, "name": name, "was_allowed": removed[0] is not None})


# --- tools ---------------------------------------------------------------------------------------------------

@mcp.tool(annotations=READS, structured_output=False)
async def whoami(account: Account = "me") -> str:
    """Which Telegram account or bot this server acts as - the way to check that a login works."""
    return dump(await (whoami_bot() if account == "bot" else whoami_personal()))


@mcp.tool(annotations=READS, structured_output=False)
async def find_chats(
    query: Annotated[str, Field(description="A name, a part of it or an @username; empty lists recent chats")] = "",
    account: Account = "me",
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    """Find chats and people by name and get the ids the other tools take, best matches first. Each chat comes with
    its last message, so two people with the same name can be told apart.

    For account='me' this searches the user's chats and contacts. For account='bot' it lists the chats the bot
    knows: people who wrote to it and groups it was added to."""
    if account == "bot":
        return dump(await find_bot(query, limit))
    return dump(await find_personal(query, limit))


@mcp.tool(annotations=READS, structured_output=False)
async def read_messages(
    chat: Chat,
    account: Account = "me",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    search: Annotated[str | None, Field(description="Only messages that contain this text")] = None,
    from_me: Annotated[bool, Field(description="Only messages the account wrote itself, the ones it can edit")] = False,
    before_id: Annotated[int | None, Field(description="Only messages older than this id, to page back")] = None,
) -> str:
    """Read the latest messages of a chat, oldest first: id, author, age in hours and text. A message with
    formatting also comes as 'html', which is what edit_message needs to keep the formatting.

    A bot cannot read history: for account='bot' this shows what reached the bot recently and what it sent through
    this server or answered through responder.py."""
    if account == "bot":
        return dump(await read_bot(chat, limit, search, from_me, before_id))
    return dump(await read_personal(chat, limit, search, from_me, before_id))


@mcp.tool(annotations=SENDS, structured_output=False)
async def send_message(
    chat: Chat,
    text: Annotated[str, Field(description="The exact text the user approved")],
    account: Account = "me",
    format: Format = "plain",
    reply_to: Annotated[int | None, Field(description="The id of a message in the same chat to reply to")] = None,
) -> str:
    """Send a message to a chat from the user's account or from their bot.

    This speaks for the user: call it only after showing the user the chat (name and id), the account and the
    exact text, and getting an explicit yes."""
    if account == "bot":
        return dump(await send_bot(chat, text, format, reply_to))
    return dump(await send_personal(chat, text, format, reply_to))


@mcp.tool(annotations=EDITS, structured_output=False)
async def edit_message(
    chat: Chat,
    message_id: int,
    text: Annotated[str, Field(description="The whole new text of the message, not only the change")],
    account: Account = "me",
    format: Format = "plain",
    drop_formatting: Annotated[bool, Field(
        description="Let plain text replace a message that has formatting, erasing it")] = False,
) -> str:
    """Replace the text of a message the account wrote itself - add an item to a list sent earlier, fix a typo.
    Returns the old and the new text.

    Telegram lets only the author edit, and a personal account's message in an ordinary chat only within 48 hours
    of sending it. This speaks for the user: call it only after showing the user the chat, the account and the
    whole new text, and getting an explicit yes."""
    if account == "bot":
        return dump(await edit_bot(chat, message_id, text, format, drop_formatting))
    return dump(await edit_personal(chat, message_id, text, format, drop_formatting))


if __name__ == "__main__":
    mcp.run()
