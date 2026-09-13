"""The owner's Telegram chats and the bot's own writing, for the bot's answers - shared by responder.py (the API route)
and search_mcp.py (the subscription route): which chats one question may look into, searching them, reading a chat or
one of its topics, the people and topics of a group, and the bot posting or editing its own messages in the group the
question came from."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, errors, functions, types, utils
from telethon.sessions import StringSession

import common

DIALOGS_SECONDS = 600
FOUND_KEPT = 30
FOUND_KEPT_DEEP = 100
FOUND_TEXT = 500
HISTORY_TEXT = 300
HISTORY_DAYS = 30
HISTORY_LIMIT = 300
HISTORY_LIMIT_DEEP = 1000
GENERAL_TOPIC = 1
PICTURE_BYTES = 5_000_000
POSTED_PHOTO_BYTES = 10_000_000
VOICE_KINDS = ("voice", "video_note")
TRANSCRIBE_SECONDS = 60
TRANSCRIBE_POLL_SECONDS = 2

SEARCH_CHATS_DESCRIPTION = (
    "Search Telegram messages in the chats this question may draw on: the chats the owner's account shares with the "
    "person asking. Call it when the question is about something written or shared in Telegram. Telegram matches "
    "words, not meaning, so search for one or two key words and try another form of a word when nothing comes back. "
    "Returns the matching messages with chat, author, date and text.")
HISTORY_DESCRIPTION = (
    "Read the recent history of a chat, oldest message first: who wrote what and when. Call it when the question is "
    "about the chat itself - what was discussed, decided or shared lately, what someone said today, what is on a "
    "list - rather than about one particular word. Leave chat_id out for the group the question was asked in; in a "
    "group with topics, pass topic_id to read one topic, such as the one a list is kept in.")
SEARCH_CHATS_DEEP_DESCRIPTION = SEARCH_CHATS_DESCRIPTION + (
    " Matches come newest first, from any time; pass before_date to find older ones.")
HISTORY_DEEP_DESCRIPTION = (
    "Read the history of a chat, oldest message first: who wrote what and when - from any period, with no date limit. "
    "Call it when the question is about the chat itself - what was discussed, decided or shared, what someone said, "
    "what is on a list - rather than about one particular word. Leave chat_id out for the group the question was "
    "asked in; in a group with topics, pass topic_id to read one topic, such as the one a list is kept in. days counts "
    "back from now, or from before_date when that is given, and 0 means no date limit; before_date reads what was "
    "written before that day, a month or years ago. A result that stopped at its limit gives next_before_id: pass it "
    "as before_id to read further back.")
POST_MESSAGE_DESCRIPTION = (
    "Post a new message as the bot in the group the question was asked in - into one of its topics, or the topic of "
    "the question. Call it only when the person asking wants something written there: an item added to a list, a "
    "note for the others. A message someone else wrote cannot be edited, so to add to their list, post the whole "
    "updated list. Returns the new message's id.")
EDIT_MESSAGE_DESCRIPTION = (
    "Replace the text of a message the bot itself posted in the group the question was asked in, such as a list it "
    "keeps there. Call it only when the person asking wants that message changed, and pass the whole new text.")
READ_VOICE_DESCRIPTION = (
    "Read what was said in a voice or video message in a chat this question may read - one marked as voice or "
    "video_note by read_chat_history or search_chats - as Telegram transcribes it. Give its message_id, and chat_id "
    "when it is in another chat than the question's group.")
VIEW_PHOTO_DESCRIPTION = (
    "Look at a photo already in Telegram: one the question comes with or replies to, or one posted in a chat this "
    "question may read - read_chat_history and search_chats mark such messages as a photo. Give its message_id, and "
    "chat_id when it is in another chat than the question's. Returns the picture itself, with who posted it and when.")
SEND_IMAGE_DESCRIPTION = (
    "Post a picture or a GIF from the web into the chat the question came from, as a reply to the question - only for "
    "the owner's requests. Give the address of the file itself - .jpg, .png, .webp, .gif or .mp4; for Giphy "
    "https://media.giphy.com/media/<id>/giphy.gif - not of the page it is on: the bot downloads it and posts it.")


def person_name(user):
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return name or user.get("title") or user.get("username") or str(user.get("id", "someone"))


def day_start(text):
    """Local midnight of a YYYY-MM-DD date, or None for anything else."""
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").astimezone()
    except ValueError:
        return None


def media_kind(message):
    """What a Telethon message carries besides text: photo, picture (an image sent as a file), gif, video_note (a round
    video message), voice, video, sticker, file or media; None for nothing - a link preview included."""
    media = getattr(message, "media", None)
    if media is None or isinstance(media, types.MessageMediaWebPage):
        return None
    if isinstance(media, types.MessageMediaPhoto):
        return "photo"
    for kind in ("sticker", "gif", "video_note", "voice", "video"):
        if getattr(message, kind, None) is not None:
            return kind
    document = getattr(message, "document", None)
    if document is not None:
        return "picture" if (getattr(document, "mime_type", None) or "").startswith("image/") else "file"
    return "media"


def picture_result(data, details):
    """A downloaded photo as a tool returns it for Claude to look at, or why it cannot be looked at."""
    kind = common.image_kind(data or b"")
    if kind is None or kind[0] != "photo":
        return {"error": "that file is not a picture Claude can look at", **details}
    if len(data) > PICTURE_BYTES:
        return {"error": "that picture is too large to look at", **details}
    return {"data": data, "media_type": kind[1], **details}


async def connect_personal():
    """The owner's own account, connected, and who it is; (None, None) when it is not logged in."""
    saved = common.load_json(common.ME_FILE) or {}
    if not saved.get("session"):
        return None, None
    client = TelegramClient(StringSession(saved["session"]), saved["api_id"], saved["api_hash"],
                            receive_updates=False, **common.DEVICE)
    client.parse_mode = None
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None, None
    return client, await client.get_me()


class Scope:
    """What one question may reach: the chats of the owner's account it may read - all of them when the owner asks,
    otherwise the ones shared with the person asking - and, for writing, only the group it came from. Asked in a
    group, one-on-one chats stay out, since the whole group reads the answer. current is that group and topic the
    topic of the question in it. A deep scope - the owner's questions on the subscription - reads with no date limit.
    origin is where the question came from - chat, topic, message and asker - for the reminders it sets; None for an
    inline question, which comes from no chat the bot can post into."""

    def __init__(self, everything=False, chats=None, private=True, reason=None, current=None, topic=None, deep=False,
                 origin=None):
        self.everything = everything
        self.chats = chats or {}
        self.private = private
        self.reason = reason
        self.current = current
        self.topic = topic
        self.deep = deep
        self.origin = origin

    def allows(self, chat_id, is_private):
        if self.reason is not None or (is_private and not self.private):
            return False
        return self.everything or chat_id in self.chats

    def describe(self):
        if self.reason is not None:
            reading = f"search_chats and read_chat_history are not available: {self.reason}."
        else:
            covered = ("all of the owner's chats" if self.everything else
                       f"{len(self.chats)} chats the owner shares with the person asking")
            kept_out = "" if self.private else ", except one-on-one chats, since the group sees the answer"
            here = (" Without a chat_id, read_chat_history reads the group the question came from."
                    if self.current is not None else "")
            reading = f"search_chats and read_chat_history cover {covered}{kept_out}.{here}"
            if self.deep:
                reading += (" Neither has a date limit: read_chat_history reads any period - days=0, before_date, "
                            "before_id - and search_chats finds older matches with before_date.")
        writing = (" post_message and edit_bot_message write into that group only." if self.current is not None else
                   " Nothing can be posted: the question did not come from a group.")
        reminding = (" set_reminder, list_reminders and cancel_reminder work in this chat." if self.origin else
                     " Reminders cannot be set: the question came from no chat.")
        return reading + writing + reminding

    def to_dict(self):
        return {"everything": self.everything, "chats": {str(chat_id): title for chat_id, title in self.chats.items()},
                "private": self.private, "reason": self.reason, "current": self.current, "topic": self.topic,
                "deep": self.deep, "origin": self.origin}

    @classmethod
    def from_dict(cls, data):
        return cls(everything=bool(data.get("everything")),
                   chats={int(chat_id): title for chat_id, title in (data.get("chats") or {}).items()},
                   private=bool(data.get("private", True)), reason=data.get("reason"), current=data.get("current"),
                   topic=data.get("topic"), deep=bool(data.get("deep")), origin=data.get("origin"))


class TelegramSearch:
    """The owner's chats through the owner's own account, kept inside a question's Scope."""

    def __init__(self, client, owner_id, owner_name):
        self.client = client
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.dialogs_at = None

    async def refresh_dialogs(self):
        """Load the owner's dialogs, which teaches the client the chats and people it can address - at most every ten
        minutes. Returns whether it did."""
        if self.dialogs_at is not None and time.monotonic() - self.dialogs_at < DIALOGS_SECONDS:
            return False
        self.dialogs_at = time.monotonic()
        await self.client.get_dialogs(limit=None)
        return True

    async def resolve(self, chat_id):
        try:
            return await self.client.get_input_entity(chat_id)
        except ValueError:
            if not await self.refresh_dialogs():
                raise
            return await self.client.get_input_entity(chat_id)

    async def scope_for(self, asker, in_group):
        if asker["id"] == self.owner_id:
            return Scope(everything=True, private=not in_group)
        person = await self.find_person(asker)
        if person is None:
            return Scope(reason="the owner's account does not know this person, so no shared chats can be found")
        try:
            shared = await self.client(functions.messages.GetCommonChatsRequest(user_id=person, max_id=0, limit=100))
        except errors.RPCError as error:
            return Scope(reason=f"Telegram did not list the shared chats ({error})")
        chats = {utils.get_peer_id(chat): utils.get_display_name(chat) for chat in shared.chats}
        if not in_group:
            chats[asker["id"]] = person_name(asker)
        return Scope(chats=chats, private=not in_group)

    async def find_person(self, asker):
        """The asker as the owner's account knows them, found by id or username - and only if the ids agree."""
        targets = [asker["id"]] + (["@" + asker["username"]] if asker.get("username") else [])
        for attempt in range(2):
            for target in targets:
                try:
                    entity = await self.client.get_input_entity(target)
                except (ValueError, TypeError, errors.RPCError):
                    continue
                if isinstance(entity, types.InputPeerUser) and entity.user_id == asker["id"]:
                    return entity
            if attempt or not await self.refresh_dialogs():
                break
        return None

    async def search(self, scope, query, chat_id=None, limit=10, before_date=None):
        """Messages with a word in the chats of the scope, newest first. A deep scope keeps up to FOUND_KEPT_DEEP of
        them and, given before_date (YYYY-MM-DD), looks only at older ones."""
        if scope.reason is not None:
            return {"error": scope.reason}
        if not query:
            return {"error": "the query is empty"}
        kept = FOUND_KEPT_DEEP if scope.deep else FOUND_KEPT
        limit = max(1, min(limit, kept if scope.deep else 20))
        before = None
        if scope.deep and before_date:
            before = day_start(before_date)
            if before is None:
                return {"error": f"before_date {before_date!r} is not a date like 2025-03-01"}
        found = []
        try:
            if chat_id is not None:
                if not scope.allows(chat_id, chat_id > 0):
                    return {"error": f"chat {chat_id} is not one this question may search"}
                await self.collect(found, chat_id, query, limit, before)
            elif scope.everything:
                async for message in self.client.iter_messages(None, search=query, limit=kept * 2, offset_date=before):
                    if scope.allows(message.chat_id, message.chat_id > 0):
                        found.append(await self.found(message))
                    if len(found) >= kept:
                        break
            else:
                for target in scope.chats:
                    if scope.allows(target, target > 0):
                        await self.collect(found, target, query, limit, before)
                    if len(found) >= kept:
                        break
        except (ValueError, TypeError, errors.RPCError) as error:
            return {"error": f"Telegram search failed: {error}", "results": found}
        if not found:
            return {"results": [], "note": "nothing found; try another form of the word"}
        return {"results": found[:kept]}

    async def collect(self, found, chat_id, query, limit, before=None):
        await self.resolve(chat_id)
        async for message in self.client.iter_messages(chat_id, search=query, limit=limit, offset_date=before):
            found.append(await self.found(message))

    async def found(self, message):
        chat = await message.get_chat()
        return {"chat": utils.get_display_name(chat) if chat else str(message.chat_id), "chat_id": message.chat_id,
                **await self.line(message, FOUND_TEXT)}

    async def history(self, scope, chat_id=None, topic_id=None, days=1, limit=100, before_id=None, before_date=None):
        """The messages of a chat of the scope - or of one topic of it - oldest first. Without a chat_id it is the group
        the question came from, and without a topic_id there the topic of the question.

        An ordinary scope reads the last 1 to HISTORY_DAYS days, at most HISTORY_LIMIT messages. A deep one reads any
        period: days counts back from before_date (YYYY-MM-DD) or from now and 0 is no date limit, before_id starts
        below a message, and a call reads up to HISTORY_LIMIT_DEEP messages, giving next_before_id when it stopped
        there. A chat or topic nobody wrote in for those days - a shopping list from last week - gives its latest few
        messages instead, marked as older."""
        if scope.reason is not None:
            return {"error": scope.reason}
        if chat_id is None:
            chat_id = scope.current
            topic_id = scope.topic if topic_id is None else topic_id
        if chat_id is None:
            return {"error": "the question did not come from a group of the owner's, so name the chat: a chat_id from "
                             "search_chats"}
        if not scope.allows(chat_id, chat_id > 0):
            return {"error": f"chat {chat_id} is not one this question may read"}
        before = None
        if scope.deep:
            days, limit = max(0, days), max(1, min(limit, HISTORY_LIMIT_DEEP))
            if before_date:
                before = day_start(before_date)
                if before is None:
                    return {"error": f"before_date {before_date!r} is not a date like 2025-03-01"}
        else:
            days, limit, before_id = max(1, min(days, HISTORY_DAYS)), max(1, min(limit, HISTORY_LIMIT)), None
        since = (before or datetime.now(timezone.utc)) - timedelta(days=days) if days else None
        options = {"limit": limit}
        if topic_id and topic_id != GENERAL_TOPIC:
            options["reply_to"] = topic_id
        if before_id:
            options["offset_id"] = before_id
        if before is not None:
            options["offset_date"] = before
        latest_before = 5
        lines, older, seen, ended_by_date = [], [], 0, False
        try:
            await self.resolve(chat_id)
            async for message in self.client.iter_messages(chat_id, **options):
                seen += 1
                line = await self.line(message, HISTORY_TEXT)
                if not line["text"]:
                    continue
                if since is None or message.date >= since:
                    lines.append(line)
                elif lines or len(older) >= latest_before:
                    ended_by_date = True
                    break
                else:
                    older.append(line)
        except (ValueError, TypeError, errors.RPCError) as error:
            return {"error": f"Telegram did not show the chat: {error}"}
        result = {"chat_id": chat_id, **({"topic_id": topic_id} if topic_id else {}), "days": days}
        if before is not None:
            result["before_date"] = before_date
        if before_id:
            result["before_id"] = before_id
        if lines:
            result["messages"] = lines[::-1]
            if seen >= limit and not ended_by_date:
                oldest = lines[-1]["message_id"]
                if scope.deep:
                    result["next_before_id"] = oldest
                    result["note"] = f"stopped at {limit} messages; pass before_id={oldest} to read further back"
                else:
                    result["note"] = f"stopped at {limit} messages; older ones within {days} days were not read"
        else:
            result["messages"] = older[::-1]
            window = f"the last {days} days" if before is None else f"the {days} days before {before_date}"
            if older:
                result["note"] = f"nothing was written in {window}; these are the latest messages before that"
            else:
                result["note"] = "nothing older was found" if before is not None or before_id else \
                    "nothing was written here yet"
        return result

    async def line(self, message, length):
        if message.out:
            author = self.owner_name
        else:
            sender = await message.get_sender()
            author = utils.get_display_name(sender) if sender else "unknown"
        text = (getattr(message, "message", None) or "")[:length]
        media = media_kind(message)
        if not text and media:
            text = f"[{media}]"
        line = {"message_id": message.id, "date": message.date.astimezone().strftime("%Y-%m-%d %H:%M"),
                "from": author, "text": text, **({"media": media} if media else {})}
        reply = getattr(message, "reply_to", None)
        if reply is not None and getattr(reply, "forum_topic", False):
            line["topic_id"] = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
        return line

    async def photo(self, scope, message_id, chat_id=None):
        """A photo, or a picture sent as a file, in a chat of the scope - the group of the question unless chat_id
        names another - by its message id, for Claude to look at."""
        if scope.reason is not None:
            return {"error": scope.reason}
        if chat_id is None:
            chat_id = scope.current
        if chat_id is None:
            return {"error": "name the chat: a chat_id from search_chats or read_chat_history"}
        if not scope.allows(chat_id, chat_id > 0):
            return {"error": f"chat {chat_id} is not one this question may read"}
        try:
            await self.resolve(chat_id)
            message = await self.client.get_messages(chat_id, ids=message_id)
            if message is None:
                return {"error": f"there is no message {message_id} in that chat"}
            kind = media_kind(message)
            if kind not in ("photo", "picture"):
                return {"error": f"message {message_id} has no photo"}
            large = kind == "picture" and (getattr(getattr(message, "file", None), "size", 0) or 0) > PICTURE_BYTES
            data = await self.client.download_media(message, bytes, thumb=-1 if large else None)
            line = await self.line(message, FOUND_TEXT)
        except (ValueError, TypeError, errors.RPCError) as error:
            return {"error": f"Telegram did not give the photo: {error}"}
        return picture_result(data, {"message_id": message_id, "chat_id": chat_id, "from": line["from"],
                                     "date": line["date"], "caption": line["text"]})

    async def transcribe(self, chat, message_id):
        """What was said in a voice or video message, as Telegram Premium transcribes it for the owner's account:
        (text, None), or (None, why not). A transcription still being made is asked for again until it is done."""
        try:
            entity = await self.resolve(chat)
            deadline = time.monotonic() + TRANSCRIBE_SECONDS
            while True:
                result = await self.client(functions.messages.TranscribeAudioRequest(peer=entity, msg_id=message_id))
                if not result.pending or time.monotonic() > deadline:
                    break
                await asyncio.sleep(TRANSCRIBE_POLL_SECONDS)
        except errors.RPCError as error:
            return None, f"Telegram did not transcribe it: {error}"
        except (ValueError, TypeError) as error:
            return None, f"the message could not be found: {error}"
        text = (result.text or "").strip()
        if not text:
            return None, "Telegram heard no words in it"
        return (text + " (the transcription was not finished)" if result.pending else text), None

    async def transcribe_own(self, bot_username, date):
        """The owner's own voice or video message to the bot, transcribed. The owner's account numbers the messages of
        its chat with the bot its own way, so the message is found there by its date."""
        try:
            entity = await self.client.get_input_entity("@" + bot_username)
            async for message in self.client.iter_messages(entity, limit=20):
                if message.out and media_kind(message) in VOICE_KINDS and int(message.date.timestamp()) == date:
                    return await self.transcribe(entity, message.id)
        except (ValueError, TypeError, errors.RPCError) as error:
            return None, f"the owner's chat with the bot could not be read: {error}"
        return None, "the voice message was not found in the owner's chat with the bot"

    async def voice(self, scope, message_id, chat_id=None):
        """What was said in a voice or video message in a chat of the scope - the group of the question unless chat_id
        names another - by its message id."""
        if scope.reason is not None:
            return {"error": scope.reason}
        if message_id is None:
            return {"error": "give the message_id of the voice message"}
        if chat_id is None:
            chat_id = scope.current
        if chat_id is None:
            return {"error": "name the chat: a chat_id from search_chats or read_chat_history"}
        if not scope.allows(chat_id, chat_id > 0):
            return {"error": f"chat {chat_id} is not one this question may read"}
        try:
            await self.resolve(chat_id)
            message = await self.client.get_messages(chat_id, ids=message_id)
            if message is None:
                return {"error": f"there is no message {message_id} in that chat"}
            if media_kind(message) not in VOICE_KINDS:
                return {"error": f"message {message_id} is not a voice or video message"}
            line = await self.line(message, FOUND_TEXT)
        except (ValueError, TypeError, errors.RPCError) as error:
            return {"error": f"Telegram did not show the message: {error}"}
        text, why = await self.transcribe(chat_id, message_id)
        details = {"message_id": message_id, "chat_id": chat_id, "from": line["from"], "date": line["date"]}
        return {**details, "text": text} if text is not None else {"error": why, **details}

    async def participants(self, chat_id, limit=200):
        """The people of a group as the owner's account sees them - id, name, username - for tagging them; none when
        Telegram does not list them."""
        try:
            await self.resolve(chat_id)
            users = await self.client.get_participants(chat_id, limit=limit)
        except (ValueError, TypeError, errors.RPCError):
            return []
        people = []
        for user in users:
            if getattr(user, "bot", False) or getattr(user, "deleted", False):
                continue
            person = {"id": user.id, "name": utils.get_display_name(user) or str(user.id)}
            if user.username:
                person["username"] = user.username
            people.append(person)
        return people

    async def topics(self, chat_id):
        """The topics of a group with topics - id and title - as the owner's account sees them; none otherwise."""
        try:
            entity = await self.resolve(chat_id)
            result = await self.client(functions.messages.GetForumTopicsRequest(
                peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
        except (ValueError, TypeError, errors.RPCError):
            return []
        return [{"id": topic.id, "title": topic.title} for topic in result.topics
                if not isinstance(topic, types.ForumTopicDeleted)]


class BotWriter:
    """The bot posting and editing its own messages - in the group the question came from, the only chat it may write
    to for that question."""

    def __init__(self, token):
        self.token = token

    async def call(self, method, **params):
        arguments = {name: value for name, value in params.items() if value is not None}
        return await asyncio.to_thread(common.bot_request, self.token, method, arguments)

    @staticmethod
    def refusal(scope, text):
        if scope.current is None:
            return {"error": "the bot writes only into the group the question came from, and this question did not "
                             "come from a group"}
        if not text:
            return {"error": "the text is empty"}
        return None

    async def post(self, scope, text, topic_id=None):
        text = (text or "").strip()
        refused = self.refusal(scope, text)
        if refused:
            return refused
        topic = scope.topic if topic_id is None else topic_id
        try:
            sent = await self.call("sendMessage", chat_id=scope.current, text=text,
                                   message_thread_id=topic if topic and topic != GENERAL_TOPIC else None,
                                   link_preview_options={"is_disabled": True})
        except common.BotApiError as error:
            return {"error": f"Telegram refused the message: {error.description}"}
        common.remember(messages=[common.bot_message_item(sent, from_bot=True)])
        return {"posted": True, "message_id": sent["message_id"], **({"topic_id": topic} if topic else {})}

    async def edit(self, scope, message_id, text):
        text = (text or "").strip()
        refused = self.refusal(scope, text)
        if refused:
            return refused
        try:
            edited = await self.call("editMessageText", chat_id=scope.current, message_id=message_id, text=text)
        except common.BotApiError as error:
            if "not modified" in error.description:
                return {"error": "the new text is the same as the old one"}
            if "can't be edited" in error.description or "not found" in error.description:
                return {"error": f"{error.description}: the bot can edit only a message it posted itself - post a new "
                                 "one instead"}
            return {"error": f"Telegram refused the edit: {error.description}"}
        if isinstance(edited, dict):
            common.remember(messages=[common.bot_message_item(edited, from_bot=True)])
        return {"edited": True, "message_id": message_id}


class Pictures:
    """Photos for Claude to look at, and pictures for the bot to post. A photo that came with the question, or with the
    message it replies to, is fetched through the Bot API; any other photo of a chat in the question's scope through
    the owner's account. A picture or a GIF from the web is posted into the chat of the question, and only when the
    owner asks for it."""

    def __init__(self, token, searcher=None):
        self.token = token
        self.searcher = searcher

    async def view(self, scope, message_id, chat_id=None):
        if message_id is None:
            return {"error": "give the message_id of the message with the photo"}
        origin = scope.origin or {}
        file_id = (origin.get("photos") or {}).get(str(message_id))
        if file_id and chat_id in (None, origin.get("chat_id")):
            try:
                data = await asyncio.to_thread(common.bot_file, self.token, file_id)
            except common.BotApiError as error:
                return {"error": f"Telegram did not give the photo: {error.description}"}
            return picture_result(data, {"message_id": message_id, "chat_id": origin.get("chat_id")})
        if self.searcher is None:
            return {"error": "photos from a chat's history need the owner's personal account, which is not logged in"}
        return await self.searcher.photo(scope, message_id, chat_id)

    async def send(self, scope, url, caption=None):
        origin = scope.origin
        if not origin:
            return {"error": "a picture is posted into the chat the question came from, and this question came from none"}
        if origin.get("asker_id") != common.load_access()["owner_id"]:
            return {"error": "only the owner can have the bot post pictures"}
        try:
            data, kind, media_type = await asyncio.to_thread(common.download_image, (url or "").strip())
        except ValueError as error:
            return {"error": str(error)}
        if kind == "photo" and len(data) > POSTED_PHOTO_BYTES:
            return {"error": f"the picture is larger than {POSTED_PHOTO_BYTES // 1_000_000} MB, too large to post"}
        method, field = ("sendAnimation", "animation") if kind == "animation" else ("sendPhoto", "photo")
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
                     "video/mp4": "mp4"}[media_type]
        params = {"chat_id": origin["chat_id"], "caption": (caption or "").strip()[:1024] or None,
                  "message_thread_id": origin.get("topic_id"),
                  "reply_parameters": {"message_id": origin["message_id"], "allow_sending_without_reply": True}
                  if origin.get("message_id") else None}
        try:
            sent = await asyncio.to_thread(common.bot_upload, self.token, method, params, field,
                                           f"picture.{extension}", data, media_type)
        except common.BotApiError as error:
            return {"error": f"Telegram refused the picture: {error.description}"}
        common.remember(messages=[common.bot_message_item(sent, from_bot=True)])
        return {"posted": True, "message_id": sent["message_id"], "as": field}
