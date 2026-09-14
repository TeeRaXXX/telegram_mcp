"""What server.py, responder.py and login.py share: where the logins and the bot's records live, the device name
Telegram shows, the Bot API call, what the bot has seen and sent, and whom it answers."""

import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
import warnings
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

# Python 3.14's ssl warns when an entry of the Windows certificate store cannot be parsed, and this machine has such
# an entry. Certificates are still checked against the rest of the store, so the warning is only noise in the
# terminal and in the MCP log.
warnings.filterwarnings("ignore", message="Bad certificate in Windows certificate store")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ME_FILE = DATA / "me.json"
BOT_FILE = DATA / "bot.json"
BOT_STATE = DATA / "bot_state.json"
ANTHROPIC_FILE = DATA / "anthropic.json"
ACCESS_FILE = DATA / "access.json"
USAGE_FILE = DATA / "usage.json"
RESPONDER_FILE = DATA / "responder.json"
OFFSET_FILE = DATA / "bot_offset.json"
CLAUDE_TOKEN_FILE = DATA / "claude_token.json"
CLAUDE_WORK = DATA / "claude-work"
CLAUDE_CONFIG = DATA / "claude-config"
REPORTS_FILE = DATA / "session_reports.json"
REPORT_LOG = DATA / "session_report.log"
REMINDERS_FILE = DATA / "reminders.json"
FILES_LOG = DATA / "files_sent.log"

# What Telegram shows under Settings > Devices for the personal account's session, so it can be found and
# ended there.
DEVICE = {"device_model": "Claude telegram-mcp", "system_version": "Windows", "app_version": "1.0"}

BOT_MESSAGES_KEPT = 2000
DOWNLOAD_BYTES = 20_000_000
BOT_MEDIA = ("photo", "video", "animation", "voice", "video_note", "audio", "document", "sticker", "location",
             "venue", "contact", "poll", "dice")

# How the responder answers unless data/access.json says otherwise. model and effort are for the people on the list,
# answered through the Claude API; daily_limit is their questions a day. The owner has no limit and, with owner_route
# "subscription", is answered by Claude Code on the owner's own subscription with owner_model and owner_effort; a
# #simple, #medium or #hard in the owner's question picks that question's model from owner_tiers instead.
# session_report.py writes to the owner when a Claude Code session worked at least report_min_minutes since the last
# prompt (None: never), with Claude's last message shortened by report_model on the subscription (None: cut instead).
DEFAULT_SETTINGS = {"model": "claude-sonnet-5", "effort": "medium", "daily_limit": 10, "web_searches_per_question": 1,
                    "owner_route": "subscription", "owner_model": "opus", "owner_effort": "medium",
                    "owner_tiers": {"simple": "sonnet", "medium": "opus", "hard": "fable"},
                    "report_min_minutes": 3, "report_model": "haiku"}
# Claude API list prices in dollars per million input and output tokens, for the estimate the bot reports. Web
# searches are billed on top and are not in it.
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}
# The responder rewrites data/responder.json after every poll of the bot, which returns within half a minute; a
# file older than this means it is not running.
RESPONDER_STALE_SECONDS = 90
USAGE_DAYS_KEPT = 62


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, value):
    DATA.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def file_lock(path, timeout=10.0):
    """Hold path.lock while a file that several processes share is read, changed and written back."""
    DATA.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} is held by another process") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


# --- the Bot API ---------------------------------------------------------------------------------------------

class BotApiError(Exception):
    def __init__(self, method, code, description):
        super().__init__(f"Bot API {method}: {description}")
        self.method = method
        self.code = code
        self.description = description


def bot_request(token, method, params=None, timeout=30):
    """Call one Bot API method and return its result. The token never appears in what is raised."""
    return bot_send(token, method, json.dumps(params or {}).encode("utf-8"), "application/json", timeout)


def bot_upload(token, method, params, field, filename, data, content_type, timeout=120):
    """Call a Bot API method that sends a file - sendPhoto, sendAnimation, sendDocument - with the file in field."""
    body, form_type = multipart(params, field, filename, data, content_type)
    return bot_send(token, method, body, form_type, timeout)


def multipart(params, field, filename, data, content_type):
    """A multipart/form-data body and its content type: params as fields - anything but a string as JSON, None left
    out - and one file."""
    boundary = uuid.uuid4().hex
    # The file's own name goes into the header as UTF-8, with what would end the quoted name escaped, as browsers do.
    filename = filename.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
    parts = []
    for name, value in params.items():
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{text}\r\n'.encode("utf-8"))
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def bot_file(token, file_id, limit=DOWNLOAD_BYTES, timeout=60):
    """The bytes of a file someone sent the bot, by its file_id."""
    path = bot_request(token, "getFile", {"file_id": file_id}).get("file_path")
    if not path:
        raise BotApiError("getFile", None, "Telegram gave no path for the file")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{token}/{path}", timeout=timeout) as response:
            return response.read(limit)
    except (urllib.error.URLError, OSError) as error:
        raise BotApiError("getFile", None, f"cannot download the file ({str(error).replace(token, '***')})") from None


def bot_send(token, method, body, content_type, timeout):
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body,
                                     headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        body = error.read()
    except (urllib.error.URLError, OSError) as error:
        reason = str(error).replace(token, "***")
        raise BotApiError(method, None, f"cannot reach api.telegram.org ({reason})") from None
    try:
        reply = json.loads(body)
    except ValueError:
        raise BotApiError(method, None, "the answer is not JSON") from None
    if not reply.get("ok"):
        raise BotApiError(method, reply.get("error_code"), reply.get("description", "unknown error"))
    return reply["result"]


# --- pictures from the web ------------------------------------------------------------------------------------

def image_kind(data):
    """What a file is by its first bytes: ("photo", media type) for a JPEG, PNG or WebP picture, ("animation", media
    type) for a GIF or an MP4, None for anything else - a web page above all."""
    if data[:3] == b"\xff\xd8\xff":
        return "photo", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "photo", "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "photo", "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "animation", "image/gif"
    if data[4:8] == b"ftyp":
        return "animation", "video/mp4"
    return None


def public_url(url):
    """Why an address may not be fetched - not http or https, or a host on this computer or its network - or None.
    A page read on the web must not get the bot to fetch the router's admin page and post it into a chat."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "only http and https addresses can be fetched"
    try:
        found = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        return f"{parsed.hostname} does not resolve"
    for info in found:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            return f"{parsed.hostname} is not a public address"
    return None


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another public address."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        refusal = public_url(newurl)
        if refusal:
            raise urllib.error.URLError(f"it redirects elsewhere: {refusal}")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_image(url, limit=DOWNLOAD_BYTES, timeout=30):
    """A picture or an animation from a public address: (data, kind, media type) with kind as image_kind gives it.
    Raises ValueError with the reason for an address that is not public, fails, is too large or is not one."""
    refusal = public_url(url)
    if refusal:
        raise ValueError(refusal)
    opener = urllib.request.build_opener(PublicRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except (urllib.error.URLError, OSError) as error:
        raise ValueError(f"could not download it: {error}") from None
    if len(data) > limit:
        raise ValueError(f"it is larger than {limit // 1_000_000} MB")
    kind = image_kind(data)
    if kind is None:
        raise ValueError("that address is not an image or an animation - give the address of the file itself, not "
                         "of the page it is on")
    return data, *kind


# --- what the bot saw and sent -------------------------------------------------------------------------------

def bot_state():
    state = load_json(BOT_STATE) or {}
    state.setdefault("chats", {})
    state.setdefault("messages", [])
    return state


def bot_chat_entry(chat):
    if chat.get("type") == "private":
        name = " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part)
    else:
        name = chat.get("title") or ""
    entry = {"id": chat["id"], "name": name or "(no name)",
             "kind": {"private": "user", "supergroup": "group"}.get(chat.get("type"), chat.get("type"))}
    if chat.get("username"):
        entry["username"] = "@" + chat["username"]
    return entry


def bot_message_item(message, from_bot):
    sender = message.get("from") or message.get("sender_chat") or {}
    name = sender.get("title") or " ".join(
        part for part in (sender.get("first_name"), sender.get("last_name")) if part)
    return {"chat_id": message["chat"]["id"], "id": message["message_id"], "date": message["date"],
            "from_bot": from_bot, "from": "bot" if from_bot else name or "unknown",
            "text": message.get("text") or message.get("caption"),
            "edited": True if message.get("edit_date") else None,
            "reply_to": (message.get("reply_to_message") or {}).get("message_id"),
            "media": next((kind for kind in BOT_MEDIA if kind in message), None)}


def remember(chats=(), messages=()):
    """Add what the bot saw or sent to data/bot_state.json, which every process shares: the Bot API keeps no
    history to look it up in later. chats are (chat, unix time of its latest activity) pairs; a None in a message
    removes that field."""
    with file_lock(BOT_STATE):
        state = bot_state()
        for chat, when in chats:
            key = str(chat["id"])
            seen = max(when, state["chats"].get(key, {}).get("seen", 0))
            state["chats"][key] = {**bot_chat_entry(chat), "seen": seen}
        kept = {(item["chat_id"], item["id"]): item for item in state["messages"]}
        for item in messages:
            key = (item["chat_id"], item["id"])
            merged = {**kept.get(key, {}), **item}
            kept[key] = {name: value for name, value in merged.items() if value is not None}
        state["messages"] = sorted(kept.values(), key=lambda item: (item["date"], item["id"]))[-BOT_MESSAGES_KEPT:]
        save_json(BOT_STATE, state)


def utf16_slice(text, offset, length):
    """Telegram counts entity offsets in UTF-16 code units, where an emoji takes two."""
    encoded = text.encode("utf-16-le")
    return encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="replace")


def message_text(message):
    return message.get("text") or message.get("caption") or ""


def message_entities(message):
    return message.get("entities") or message.get("caption_entities") or []


def named(text, bot):
    """Whether a text calls the bot by its first name anywhere in it, as a word of its own and - for a name that ends
    in a consonant - in any Russian case: Робин, Робина, Робину, Робином, Робине, but not Робинзон."""
    name = (bot.get("first_name") or "").strip()
    if not name:
        return False
    endings = "(?:а|у|ом|е)?" if re.search(r"[бвгджзклмнпрстфхцчшщ]$", name, flags=re.IGNORECASE) else ""
    return re.search(rf"(?<![\w@]){re.escape(name)}{endings}(?!\w)", text, flags=re.IGNORECASE) is not None


def addressed(message, bot):
    """Whether a message asks the bot: a private message, one that calls the bot by its name anywhere in it
    ("Робин, добавь хлеб", "спроси у Робина"), a mention of it, or a reply to its message."""
    if message["chat"]["type"] == "private":
        return True
    text = message_text(message)
    if named(text, bot):
        return True
    for entity in message_entities(message):
        if entity["type"] == "mention" and \
                utf16_slice(text, entity["offset"], entity["length"]).lower() == "@" + bot["username"].lower():
            return True
        if entity["type"] == "text_mention" and (entity.get("user") or {}).get("id") == bot["id"]:
            return True
    return ((message.get("reply_to_message") or {}).get("from") or {}).get("id") == bot["id"]


def meant_for(message, bot):
    """Whether a message is the bot's business: addressed to it, or a command. With privacy mode off - which a bot
    needs to hear mentions in groups - Telegram sends it every message of its groups, and the rest is not kept."""
    command = any(entity["type"] == "bot_command" and entity["offset"] == 0 for entity in message_entities(message))
    return command or addressed(message, bot)


def remember_updates(updates, bot=None):
    """Take in Bot API updates: the chats they come from and the messages people sent the bot. Given the bot's own
    getMe, a group message not meant for it leaves no trace but its chat."""
    chats, messages = [], []
    for update in updates:
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            if key in update:
                chats.append((update[key]["chat"], update[key]["date"]))
                if bot is None or meant_for(update[key], bot):
                    messages.append(bot_message_item(update[key], from_bot=False))
        for key in ("my_chat_member", "chat_member", "chat_join_request"):
            if key in update:
                chats.append((update[key]["chat"], update[key]["date"]))
    if chats:
        remember(chats, messages)


# --- whom the bot answers, and what it spent -----------------------------------------------------------------

def today():
    return date.today().isoformat()


def load_access():
    access = load_json(ACCESS_FILE) or {}
    access.setdefault("owner_id", None)
    access.setdefault("allowed", {})
    access["settings"] = {**DEFAULT_SETTINGS, **access.get("settings", {})}
    return access


def change_access(change):
    """Apply change(access) to data/access.json under its lock; returns the changed access."""
    with file_lock(ACCESS_FILE):
        access = load_access()
        change(access)
        save_json(ACCESS_FILE, access)
        return access


def daily_limit(access, person_id):
    """Questions a day for a person on the list; None for the owner, who has no limit."""
    if person_id == access["owner_id"]:
        return None
    return access["allowed"].get(str(person_id), {}).get("daily_limit", access["settings"]["daily_limit"])


def usage_today(person_id):
    return (load_json(USAGE_FILE) or {}).get(today(), {}).get(str(person_id), {})


def add_usage(person_id, **amounts):
    """Add amounts to today's record for a person in data/usage.json and return the record."""
    with file_lock(USAGE_FILE):
        usage = load_json(USAGE_FILE) or {}
        record = usage.setdefault(today(), {}).setdefault(str(person_id), {})
        for name, amount in amounts.items():
            record[name] = round(record.get(name, 0) + amount, 6)
        for old in sorted(usage)[:-USAGE_DAYS_KEPT]:
            del usage[old]
        save_json(USAGE_FILE, usage)
        return record


def estimate_cost(model, totals):
    """Dollars for the tokens in totals at the model's list price; None for a model without a known price."""
    if model not in PRICES:
        return None
    per_input, per_output = (price / 1_000_000 for price in PRICES[model])
    return round(totals.get("input_tokens", 0) * per_input
                 + totals.get("cache_creation_input_tokens", 0) * per_input * 1.25
                 + totals.get("cache_read_input_tokens", 0) * per_input * 0.1
                 + totals.get("output_tokens", 0) * per_output, 6)


def pid_alive(pid):
    """Whether a process with this id is running. On Windows os.kill(pid, 0) would end the process, so this asks
    the kernel for its exit code instead."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def responder_status():
    """What responder.py last wrote about itself, with alive: a poll within the last minute and a half by a process
    that still exists - a crashed or killed responder leaves a fresh file behind."""
    status = load_json(RESPONDER_FILE)
    if status is None:
        return None
    status["alive"] = time.time() - status.get("polled", 0) < RESPONDER_STALE_SECONDS and pid_alive(status.get("pid"))
    return status


def responder_alive():
    status = responder_status()
    return bool(status and status["alive"])
