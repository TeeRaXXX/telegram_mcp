"""Reminders the bot posts later. Someone asks the bot to remind them of something in two hours; Claude sets a reminder
with set_reminder, and responder.py posts it when it is due - into the chat and topic the question came from, as a
reply to the question, tagging whom it is for. They are kept in data/reminders.json, so they outlast a restart, and
posting one costs nothing: only the question that sets it goes through Claude."""

import re
import time
import uuid
from datetime import datetime

import common

PENDING_PER_PERSON = 20
LONGEST_DAYS = 400
LATE_SECONDS = 300

SET_REMINDER_DESCRIPTION = (
    "Set a reminder the bot posts later by itself into the chat the question came from - into its topic, as a reply "
    "to the question, tagging the person asking unless the text tags someone else. Call it when someone wants to be "
    "reminded of something later, or wants someone in the chat reminded later. Give minutes_from_now for \"in two "
    "hours\", or at for a local date and time. Returns the reminder's id and when it will be posted.")
LIST_REMINDERS_DESCRIPTION = (
    "List the reminders waiting to be posted into the chat the question came from: id, when, who set it and the text.")
CANCEL_REMINDER_DESCRIPTION = (
    "Cancel a reminder waiting in the chat the question came from, by its id from list_reminders. Anyone but the "
    "owner can cancel only the reminders they set themselves.")

NO_CHAT = "a reminder is posted into the chat the question came from, and this question came from none"


def load():
    return common.load_json(common.REMINDERS_FILE) or []


def local(stamp):
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")


def moment(minutes_from_now=None, at=None, now=None):
    """When a reminder is due, as a unix time, and None; or None and why it cannot be."""
    now = time.time() if now is None else now
    if minutes_from_now is not None:
        due = now + minutes_from_now * 60
    elif at:
        try:
            due = datetime.strptime(str(at).strip(), "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            return None, f"at {at!r} is not a local date and time like 2026-09-12 21:17"
    else:
        return None, "say when: minutes_from_now or at"
    if due < now + 30:
        return None, "that moment has passed already"
    if due > now + LONGEST_DAYS * 86400:
        return None, f"a reminder can be at most {LONGEST_DAYS} days ahead"
    return due, None


def add(origin, text, minutes_from_now=None, at=None, now=None):
    """Set a reminder for the question origin describes - its chat, topic, message and asker; what set_reminder
    returns."""
    if not origin:
        return {"error": NO_CHAT}
    text = (text or "").strip()
    if not text:
        return {"error": "the text is empty"}
    due, refusal = moment(minutes_from_now, at, now)
    if refusal:
        return {"error": refusal}
    owner = common.load_access()["owner_id"]
    with common.file_lock(common.REMINDERS_FILE):
        waiting = load()
        theirs = sum(1 for item in waiting if item["asker_id"] == origin["asker_id"])
        if origin["asker_id"] != owner and theirs >= PENDING_PER_PERSON:
            return {"error": f"{origin['asker_name']} has {theirs} reminders waiting already; cancel one first"}
        reminder = {"id": uuid.uuid4().hex[:8], "due": int(due), "text": text,
                    **{key: origin.get(key) for key in ("chat_id", "topic_id", "message_id", "asker_id", "asker_name")}}
        common.save_json(common.REMINDERS_FILE, [*waiting, reminder])
    return {"set": True, "id": reminder["id"], "at": local(reminder["due"])}


def listed(origin):
    """The reminders waiting in the chat of the question, earliest first; what list_reminders returns."""
    if not origin:
        return {"error": NO_CHAT}
    waiting = sorted((item for item in load() if item["chat_id"] == origin["chat_id"]), key=lambda item: item["due"])
    return {"reminders": [{"id": item["id"], "at": local(item["due"]), "set_by": item["asker_name"],
                           "text": item["text"]} for item in waiting]}


def cancel(origin, reminder_id):
    """Cancel a reminder waiting in the chat of the question: one the asker set, or any when the owner asks."""
    if not origin:
        return {"error": NO_CHAT}
    owner = common.load_access()["owner_id"]
    with common.file_lock(common.REMINDERS_FILE):
        waiting = load()
        found = next((item for item in waiting
                      if item["id"] == str(reminder_id).strip() and item["chat_id"] == origin["chat_id"]), None)
        if found is None:
            return {"error": f"no reminder {reminder_id} is waiting in this chat"}
        if origin["asker_id"] not in (found["asker_id"], owner):
            return {"error": f"only {found['asker_name']}, who set it, or the owner can cancel it"}
        common.save_json(common.REMINDERS_FILE, [item for item in waiting if item is not found])
    return {"cancelled": True, "id": found["id"], "text": found["text"]}


def due(now=None):
    """The reminders whose time has come, earliest first. They stay in the file until remove(): one that could not be
    posted is tried again, and a responder stopped halfway posts it again rather than never."""
    now = time.time() if now is None else now
    return sorted((item for item in load() if item["due"] <= now), key=lambda item: item["due"])


def remove(reminder_id):
    with common.file_lock(common.REMINDERS_FILE):
        waiting = load()
        kept = [item for item in waiting if item["id"] != reminder_id]
        if len(kept) != len(waiting):
            common.save_json(common.REMINDERS_FILE, kept)


def lateness(seconds):
    minutes = round(seconds / 60)
    return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"


def reminder_message(item, now=None):
    """What a due reminder says: ⏰, a tag of the person who set it - unless the text tags someone or the chat is a
    one-on-one one - the text, and a note when it is late because the bot was not running."""
    text = item["text"]
    if item["chat_id"] != item["asker_id"] and "{{mention:" not in text and not re.search(r"@\w{5,}", text):
        text = f"{{{{mention:{item['asker_id']}}}}}, {text}"
    late = (time.time() if now is None else now) - item["due"]
    if late > LATE_SECONDS:
        text += f"\n(с опозданием на {lateness(late)}: бот был выключен)"
    return "⏰ " + text
