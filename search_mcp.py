#!/usr/bin/env python3
"""The chat tools for Claude Code when it answers the owner's questions on the subscription: search_chats and
read_chat_history over the owner's Telegram chats - with no date limit, since the scope responder.py writes for these
questions is a deep one - post_message and edit_bot_message for the bot in the group the question came from, and
set_reminder, list_reminders and cancel_reminder for the chat it came from, all kept inside the scope written for that
one question.

    .venv/Scripts/python.exe search_mcp.py --scope <scope file>
"""

import json
import sys
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from pydantic import Field
from telethon import utils

import common
import reminders
from chatsearch import (EDIT_MESSAGE_DESCRIPTION, FOUND_KEPT_DEEP, HISTORY_DEEP_DESCRIPTION, HISTORY_LIMIT_DEEP,
                        POST_MESSAGE_DESCRIPTION, READ_VOICE_DESCRIPTION, SEARCH_CHATS_DEEP_DESCRIPTION,
                        SEND_IMAGE_DESCRIPTION, VIEW_PHOTO_DESCRIPTION, BotWriter, Pictures, Scope, TelegramSearch,
                        connect_personal)

mcp = MCPServer("chats", log_level="WARNING")
_search = None


def scope():
    """The scope for this question, from the file named after --scope; nothing may be read or written without one."""
    if "--scope" not in sys.argv[1:-1]:
        return Scope(reason="no scope was given")
    data = common.load_json(Path(sys.argv[sys.argv.index("--scope") + 1]))
    return Scope.from_dict(data) if data else Scope(reason="the scope file is missing")


async def searcher():
    global _search
    if _search is None:
        client, me = await connect_personal()
        if client is None:
            return None
        _search = TelegramSearch(client, me.id, utils.get_display_name(me))
    return _search


def writer():
    token = (common.load_json(common.BOT_FILE) or {}).get("token")
    return BotWriter(token) if token else None


async def pictures():
    return Pictures((common.load_json(common.BOT_FILE) or {}).get("token"), await searcher())


def dump(value):
    return json.dumps(value, ensure_ascii=False)


@mcp.tool(description=SEARCH_CHATS_DEEP_DESCRIPTION, structured_output=False)
async def search_chats(
    query: Annotated[str, Field(description="One or two words to look for")],
    chat_id: Annotated[int | None, Field(description="Only this chat - a chat_id from an earlier result")] = None,
    limit: Annotated[int, Field(ge=1, le=FOUND_KEPT_DEEP, description="Messages per chat")] = 10,
    before_date: Annotated[str | None, Field(
        description="Only messages written before this day, YYYY-MM-DD - to find older ones")] = None,
) -> str:
    search = await searcher()
    if search is None:
        return dump({"error": "the owner's personal account is not logged in"})
    return dump(await search.search(scope(), query.strip(), chat_id, limit, before_date))


@mcp.tool(description=HISTORY_DEEP_DESCRIPTION, structured_output=False)
async def read_chat_history(
    chat_id: Annotated[int | None, Field(
        description="The chat to read; leave it out for the group the question was asked in")] = None,
    topic_id: Annotated[int | None, Field(description="One topic of a group with topics, from the list of topics")] = None,
    days: Annotated[int, Field(ge=0, description="How many days back from now, or from before_date; 0 for no date "
                                                 "limit")] = 1,
    limit: Annotated[int, Field(ge=1, le=HISTORY_LIMIT_DEEP, description="The most messages to read")] = 100,
    before_date: Annotated[str | None, Field(description="Read what was written before this day, YYYY-MM-DD")] = None,
    before_id: Annotated[int | None, Field(
        description="Read what was written before this message: next_before_id from an earlier result")] = None,
) -> str:
    search = await searcher()
    if search is None:
        return dump({"error": "the owner's personal account is not logged in"})
    return dump(await search.history(scope(), chat_id, topic_id, days, limit, before_id, before_date))


@mcp.tool(description=POST_MESSAGE_DESCRIPTION, structured_output=False)
async def post_message(
    text: Annotated[str, Field(description="The whole message to post")],
    topic_id: Annotated[int | None, Field(
        description="The topic to post into, from the list of topics; leave it out for the topic of the question")] = None,
) -> str:
    bot = writer()
    if bot is None:
        return dump({"error": "the bot is not logged in"})
    return dump(await bot.post(scope(), text, topic_id))


@mcp.tool(description=EDIT_MESSAGE_DESCRIPTION, structured_output=False)
async def edit_bot_message(
    message_id: Annotated[int, Field(description="The id of a message the bot posted in this group")],
    text: Annotated[str, Field(description="The whole new text")],
) -> str:
    bot = writer()
    if bot is None:
        return dump({"error": "the bot is not logged in"})
    return dump(await bot.edit(scope(), message_id, text))


@mcp.tool(description=READ_VOICE_DESCRIPTION, structured_output=False)
async def read_voice(
    message_id: Annotated[int, Field(description="The id of the voice or video message")],
    chat_id: Annotated[int | None, Field(
        description="The chat it is in, when not the group the question came from")] = None,
) -> str:
    search = await searcher()
    if search is None:
        return dump({"error": "the owner's personal account is not logged in"})
    return dump(await search.voice(scope(), message_id, chat_id))


@mcp.tool(description=VIEW_PHOTO_DESCRIPTION, structured_output=False)
async def view_photo(
    message_id: Annotated[int, Field(description="The id of the message with the photo")],
    chat_id: Annotated[int | None, Field(
        description="The chat it is in, when not the chat the question came from")] = None,
) -> list[Image | str] | str:
    found = await (await pictures()).view(scope(), message_id, chat_id)
    if "data" not in found:
        return dump(found)
    details = {name: value for name, value in found.items() if name != "data"}
    return [Image(data=found["data"], format=found["media_type"].split("/", 1)[1]), dump(details)]


@mcp.tool(description=SEND_IMAGE_DESCRIPTION, structured_output=False)
async def send_image(
    url: Annotated[str, Field(description="The address of the picture or GIF file itself")],
    caption: Annotated[str | None, Field(description="A short caption")] = None,
) -> str:
    return dump(await (await pictures()).send(scope(), url, caption))


@mcp.tool(description=reminders.SET_REMINDER_DESCRIPTION, structured_output=False)
async def set_reminder(
    text: Annotated[str, Field(description="The reminder as it will be posted, tagging whom it is for if that is not "
                                           "the person asking")],
    minutes_from_now: Annotated[int | None, Field(ge=1, description="In how many minutes to post it")] = None,
    at: Annotated[str | None, Field(description="When to post it, local time: YYYY-MM-DD HH:MM")] = None,
) -> str:
    return dump(reminders.add(scope().origin, text, minutes_from_now, at))


@mcp.tool(description=reminders.LIST_REMINDERS_DESCRIPTION, structured_output=False)
async def list_reminders() -> str:
    return dump(reminders.listed(scope().origin))


@mcp.tool(description=reminders.CANCEL_REMINDER_DESCRIPTION, structured_output=False)
async def cancel_reminder(
    reminder_id: Annotated[str, Field(description="The reminder's id from list_reminders")],
) -> str:
    return dump(reminders.cancel(scope().origin, reminder_id))


if __name__ == "__main__":
    mcp.run()
