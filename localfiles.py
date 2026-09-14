"""Files on the owner's computer for the bot to post, for the owner's questions alone - shared by responder.py (the API
route) and search_mcp.py (the subscription route): finding a file by words of its name and posting it into the chat the
question came from. The owner's own folders and every local drive but the system one are looked through; whatever may
keep logins or keys - a folder or file whose name starts with a dot or $, AppData, Telegram Desktop's tdata, this
project's own folder, key and session files - is never listed and never posted."""

import asyncio
import ctypes
import mimetypes
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import common

FILE_BYTES = 50_000_000
FOUND_FILES = 30
FOUND_KEPT = 2000
FIND_SECONDS = 20
UPLOAD_SECONDS = 300
DRIVE_FIXED = 3
HOME_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
CLOSED_FOLDERS = {"appdata", "tdata"}
SKIPPED_FOLDERS = {"node_modules", "__pycache__", "site-packages", "program files", "program files (x86)",
                   "programdata", "windowsapps", "system volume information"}
KEY_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".ppk", ".kdbx", ".keystore", ".jks", ".ovpn", ".session", ".gpg",
                ".asc"}
KEY_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "wallet.dat")

FIND_FILES_DESCRIPTION = (
    "Find a file on the owner's computer by words of its name, to post it with send_file - only for the owner's "
    "requests. Looks through the owner's folders - Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive - "
    "and the other local drives, shallow folders first, for up to 20 seconds, and returns the matching files newest "
    "first, with the size and when each was last changed. Every word given must be in the name - one or two, or pdf "
    "for the extension - and a word may list alternatives with |: a file may be named in another language or "
    "shortened, so резюме|resume|cv looks for all three at once, which is much faster than a search for each. Give "
    "folder to look through one folder and the folders in it.")
SEND_FILE_DESCRIPTION = (
    "Post a file from the owner's computer - a document, a PDF, a photo, an archive - into the chat the question came "
    "from, as a reply to the question, as a document of up to 50 MB - only when the owner asks for that file. Give its "
    "full path as find_files returned it. A file in a folder that may keep logins or keys is refused.")


def fold(text):
    return text.casefold().replace("ё", "е")


def terms(query):
    """The words of a query, each as its alternatives: "резюме|cv pdf" gives [("резюме", "cv"), ("pdf",)]."""
    found = []
    for word in fold(query or "").split():
        alternatives = tuple(part.strip("*") for part in word.split("|") if part.strip("*"))
        if alternatives:
            found.append(alternatives)
    return found


def fixed_drives():
    """The computer's local drives - not network, removable or optical ones."""
    if os.name != "nt":
        return []
    return [Path(drive) for drive in os.listdrives() if ctypes.windll.kernel32.GetDriveTypeW(drive) == DRIVE_FIXED]


def places():
    """Where files are looked for and posted from: the owner's own folders - OneDrive's too - and every local drive
    but the system one, each once."""
    home = Path.home()
    system = Path(os.environ.get("SystemDrive", "C:") + os.sep)
    found = []
    for candidate in [*(home / name for name in HOME_FOLDERS), *sorted(home.glob("OneDrive*")),
                      *(drive for drive in fixed_drives() if drive != system)]:
        try:
            real = candidate.resolve(strict=True)
        except OSError:
            continue
        if real.is_dir() and not any(real.is_relative_to(place) for place in found):
            found = [place for place in found if not place.is_relative_to(real)] + [real]
    return found


def closed(path, place):
    """Why a file or folder in place is never listed or posted - it may keep logins or keys - or None."""
    if path.is_relative_to(common.ROOT):
        return "the bot's own folder keeps its logins, so nothing is sent from it"
    for part in path.relative_to(place).parts:
        if part.startswith((".", "$")) or part.casefold() in CLOSED_FOLDERS:
            return f"«{part}» may keep logins or keys, so nothing is sent from it"
    return None


def key_file(path):
    """Why a file is never listed or posted by its name - a key, a password database, a session - or None."""
    if path.suffix.casefold() in KEY_SUFFIXES or path.name.casefold().startswith(KEY_NAMES):
        return "that file looks like a key or a login, which is never sent"
    return None


def walk(starts, words, seconds=FIND_SECONDS):
    """The files under the start folders whose names hold every word - one of its alternatives - as (modified, path,
    size), shallow folders looked in first; and why the walk stopped before every folder was looked in - "time",
    "many" - or None."""
    deadline = time.monotonic() + seconds
    own = os.path.normcase(str(common.ROOT))
    queue = deque(starts)
    found = []
    while queue:
        if time.monotonic() > deadline:
            return found, "time"
        try:
            with os.scandir(queue.popleft()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        name = entry.name
                        if entry.is_dir(follow_symlinks=False):
                            folded = name.casefold()
                            if not (name.startswith((".", "$")) or folded in CLOSED_FOLDERS or folded in SKIPPED_FOLDERS
                                    or os.path.normcase(entry.path) == own):
                                queue.append(entry.path)
                            continue
                        folded = fold(name)
                        if all(any(part in folded for part in word) for word in words) and key_file(Path(name)) is None:
                            stat = entry.stat(follow_symlinks=False)
                            found.append((stat.st_mtime, entry.path, stat.st_size))
                            if len(found) >= FOUND_KEPT:
                                return found, "many"
                    except OSError:
                        continue
        except OSError:
            continue
    return found, None


class LocalFiles:
    """A file on this computer found by its name and posted into the chat of the question, when the owner asks."""

    def __init__(self, token, where=places):
        self.token = token
        self.where = where

    def refusal(self, scope):
        if not scope.origin:
            return "a file is posted into the chat the question came from, and this question came from none"
        if scope.origin.get("asker_id") != common.load_access()["owner_id"]:
            return "only the owner can have the bot find and send files from this computer"
        return None

    def locate(self, given, folder=False):
        """The real path of a file or folder Claude named, or why it cannot be used."""
        kind = "folder" if folder else "file"
        text = str(given or "").strip().strip('"')
        if not text:
            return None, f"give the {kind}'s full path"
        try:
            real = Path(text).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None, f"there is no {kind} {text}"
        if not (real.is_dir() if folder else real.is_file()):
            return None, f"{text} is not a {kind}"
        allowed = self.where()
        place = next((place for place in allowed if real.is_relative_to(place)), None)
        if place is None:
            return None, f"that {kind} is outside the places files are sent from: " + ", ".join(map(str, allowed))
        refusal = closed(real, place) or (None if folder else key_file(real))
        return (None, refusal) if refusal else (real, None)

    async def find(self, scope, query, folder=None):
        refusal = self.refusal(scope)
        if refusal:
            return {"error": refusal}
        words = terms(query)
        if not words:
            return {"error": "give a word of the file's name"}
        if folder:
            start, refusal = await asyncio.to_thread(self.locate, folder, True)
            if refusal:
                return {"error": refusal}
            starts = [start]
        else:
            starts = await asyncio.to_thread(self.where)
        found, stopped = await asyncio.to_thread(walk, [str(start) for start in starts], words)
        found.sort(reverse=True)
        result = {"files": [{"path": path, "size": size, "modified": f"{datetime.fromtimestamp(modified):%Y-%m-%d %H:%M}"}
                            for modified, path, size in found[:FOUND_FILES]],
                  "looked_in": [str(start) for start in starts]}
        if len(found) > FOUND_FILES:
            result["more"] = len(found) - FOUND_FILES
        if stopped == "time":
            result["note"] = (f"the search stopped after {FIND_SECONDS} seconds, before every folder was looked in: "
                              "give folder to look through one place, or other words")
        elif stopped == "many":
            result["note"] = f"more than {FOUND_KEPT} files have these words in their names: add a word"
        elif not found:
            result["note"] = "no file has these words in its name: try another word, another language or a shorter part"
        return result

    async def send(self, scope, path, caption=None):
        refusal = self.refusal(scope)
        if refusal:
            return {"error": refusal}
        file, refusal = await asyncio.to_thread(self.locate, path)
        if refusal:
            return {"error": refusal}
        size = file.stat().st_size
        if not size:
            return {"error": "the file is empty, and Telegram does not send empty files"}
        if size > FILE_BYTES:
            return {"error": f"the file is {size / 1_000_000:.1f} MB, and a bot sends at most {FILE_BYTES // 1_000_000} MB"}
        try:
            data = await asyncio.to_thread(file.read_bytes)
        except OSError as error:
            return {"error": f"the file cannot be read: {error.strerror or error}"}
        origin = scope.origin
        params = {"chat_id": origin["chat_id"], "caption": (caption or "").strip()[:1024] or None,
                  "message_thread_id": origin.get("topic_id"),
                  "reply_parameters": {"message_id": origin["message_id"], "allow_sending_without_reply": True}
                  if origin.get("message_id") else None}
        media_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        try:
            sent = await asyncio.to_thread(common.bot_upload, self.token, "sendDocument", params, "document", file.name,
                                           data, media_type, UPLOAD_SECONDS)
        except common.BotApiError as error:
            return {"error": f"Telegram refused the file: {error.description}"}
        common.remember(messages=[common.bot_message_item(sent, from_bot=True)])
        where = f"chat {origin['chat_id']}" + (f" topic {origin['topic_id']}" if origin.get("topic_id") else "")
        with open(common.FILES_LOG, "a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {file} ({size} bytes) to {where}\n")
        return {"posted": True, "message_id": sent["message_id"], "path": str(file), "size": size}
