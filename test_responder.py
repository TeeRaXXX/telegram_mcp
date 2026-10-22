"""Exercise responder.py, chatsearch.py and the bot_* MCP tools without Telegram, Claude or Claude Code: a fake Bot
API, a fake Claude, a fake Claude Code process, fake Telegram search - and the real anthropic SDK over a mock HTTP
transport.

    .venv/Scripts/python.exe test_responder.py
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import common  # noqa: E402

TEMP = Path(tempfile.mkdtemp(prefix="tgmcp-responder-"))
common.DATA = TEMP
for attribute, file_name in (("ME_FILE", "me.json"), ("BOT_FILE", "bot.json"), ("BOT_STATE", "bot_state.json"),
                             ("ANTHROPIC_FILE", "anthropic.json"), ("ACCESS_FILE", "access.json"),
                             ("USAGE_FILE", "usage.json"), ("RESPONDER_FILE", "responder.json"),
                             ("OFFSET_FILE", "bot_offset.json"), ("CLAUDE_TOKEN_FILE", "claude_token.json"),
                             ("CLAUDE_WORK", "claude-work"), ("CLAUDE_CONFIG", "claude-config"),
                             ("REPORTS_FILE", "session_reports.json"), ("REPORT_LOG", "session_report.log"),
                             ("REMINDERS_FILE", "reminders.json"), ("FILES_LOG", "files_sent.log")):
    setattr(common, attribute, TEMP / file_name)

import responder  # noqa: E402
import server  # noqa: E402
import chatsearch  # noqa: E402
import reminders  # noqa: E402
import session_report  # noqa: E402
import localfiles  # noqa: E402
from chatsearch import BotWriter, Pictures, Scope, TelegramSearch  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from telethon import functions, types  # noqa: E402

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


OWNER, MASHA, STRANGER, DASHA = 393, 555, 999, 777001
BOT_ME = {"id": 1, "is_bot": True, "first_name": "Робин", "username": "family_bot"}
FAMILY = {"id": -100777, "type": "supergroup", "title": "Семья", "is_forum": True}
OTHER_GROUP = {"id": -100888, "type": "group", "title": "Чужая группа"}
PEOPLE = [{"id": OWNER, "name": "Олег", "username": "oleg_example"}, {"id": MASHA, "name": "Маша", "username": "masha"},
          {"id": DASHA, "name": "Даша"}]
TOPICS = [{"id": 1, "title": "Общие вопросы"}, {"id": 42, "title": "Покупки"}]
serial = [100]


def user(person_id, first_name, username=None):
    return {"id": person_id, "is_bot": False, "first_name": first_name, **({"username": username} if username else {})}


def message(sender, text, chat=None, entities=None, reply_to=None, topic=None):
    serial[0] += 1
    result = {"message_id": serial[0], "date": int(time.time()), "from": sender, "text": text,
              "chat": chat or {"id": sender["id"], "type": "private", "first_name": sender["first_name"]}}
    if entities:
        result["entities"] = entities
    if reply_to:
        result["reply_to_message"] = reply_to
    if topic:
        result.update(message_thread_id=topic, is_topic_message=True)
    return result


def mention(text, name="@family_bot"):
    return [{"type": "mention", "offset": len(text[:text.index(name)].encode("utf-16-le")) // 2, "length": len(name)}]


def command(text):
    return [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]


def joined(chat, adder):
    return {"update_id": 1, "my_chat_member": {"chat": chat, "from": adder, "date": int(time.time()),
                                               "old_chat_member": {"user": BOT_ME, "status": "left"},
                                               "new_chat_member": {"user": BOT_ME, "status": "member"}}}


def totals(**changes):
    return {"input_tokens": 1000, "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "web_searches": 1, **changes}


class FakeBot:
    def __init__(self):
        self.me = BOT_ME
        self.sent = []

    async def call(self, method, http_timeout=30, **params):
        return True

    async def send(self, chat_id, text, reply_to=None, parse_mode=None, thread=None):
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, reply_to=reply_to, parse_mode=parse_mode,
                                         thread=thread))

    async def reply(self, message, text, parse_mode=None):
        await self.send(message["chat"]["id"], text, message["message_id"], parse_mode)


class FakeAnswerer:
    def __init__(self, reply_text="Ответ"):
        self.questions = []
        self.settings = []
        self.reply_text = reply_text

    async def answer(self, question, scope, settings):
        self.questions.append((question, scope))
        self.settings.append(settings)
        return self.reply_text, totals()


class FakeScopes:
    async def scope_for(self, asker, in_group):
        if asker["id"] == OWNER:
            return Scope(everything=True, private=not in_group)
        return Scope(chats={-100777: "Семья"}, private=not in_group)

    async def participants(self, chat_id):
        return PEOPLE if chat_id == FAMILY["id"] else []

    async def topics(self, chat_id):
        return TOPICS if chat_id == FAMILY["id"] else []


async def responder_checks():
    bot, api, subscription = FakeBot(), FakeAnswerer(), FakeAnswerer("Ответ через подписку")
    common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {str(MASHA): {"name": "Маша"}}})
    bot_responder = responder.Responder(bot, api, FakeScopes(), OWNER, subscription)
    oleg, masha, stranger = user(OWNER, "Олег"), user(MASHA, "Маша", "masha"), user(STRANGER, "Чужой")

    async def deliver(update):
        await bot_responder.handle(update)
        await asyncio.gather(*list(bot_responder.tasks))

    async def send(item):
        await deliver({"update_id": item["message_id"], "message": item})

    await send(message(oleg, "что такое MCP?"))
    check("the owner is answered by Claude Code on the subscription, not the API",
          len(subscription.questions) == 1 and not api.questions and bot.sent[-1].text == "Ответ через подписку",
          bot.sent)
    scope = subscription.questions[-1][1]
    check("the owner asking privately may read everything, and there is no group to write into",
          scope.everything and scope.private and scope.current is None and scope.topic is None, vars(scope))
    check("the owner's question on the subscription reads chats with no date limit", scope.deep, vars(scope))
    check("a question knows the chat and message it came from, for the reminders it sets",
          scope.origin and scope.origin["chat_id"] == OWNER and scope.origin["asker_id"] == OWNER
          and scope.origin["topic_id"] is None and isinstance(scope.origin["message_id"], int), scope.origin)
    owner_usage = common.usage_today(OWNER)
    check("the owner's question counts as a subscription one, with no dollars",
          owner_usage.get("subscription_questions") == 1 and not owner_usage.get("cost_usd"), owner_usage)

    question = "@family_bot сколько стоит билет?"
    await send(message(stranger, question, FAMILY, mention(question)))
    check("a stranger's mention is ignored before any Claude", not api.questions and len(bot.sent) == 1, bot.sent)

    await send(message(masha, "просто болтаем", FAMILY))
    check("a group message not addressed to the bot is left alone", not api.questions)

    await send(message(masha, question, FAMILY, mention(question)))
    text, scope = api.questions[-1]
    check("a listed person is answered through the API, without the mention in the question",
          len(api.questions) == 1 and len(subscription.questions) == 1 and "Question: сколько стоит билет?" in text
          and "@family_bot" not in text, text)
    check("asked in a group: no one-on-one chats, and the group is where it may write",
          not scope.private and scope.current == FAMILY["id"] and scope.topic is None, vars(scope))
    check("a question through the API keeps the date limit", not scope.deep, vars(scope))
    check("the question lists the group's people for tagging", "- 777001 Даша" in text and "@masha" in text, text)
    check("the question lists the group's topics and says it came from the general one",
          "- 42 Покупки" in text and "in its general topic" in text, text)

    shopping = "@family_bot добавь хлеб"
    await send(message(masha, shopping, FAMILY, mention(shopping), topic=42))
    text, scope = api.questions[-1]
    check("a question in a topic names that topic and scopes to it",
          scope.topic == 42 and 'in its topic "Покупки" (topic_id 42)' in text, (vars(scope), text))
    check("a reminder set from a topic goes back into that topic, for the person who asked",
          scope.origin["chat_id"] == FAMILY["id"] and scope.origin["topic_id"] == 42
          and scope.origin["asker_name"] == "Маша", scope.origin)

    posted = message(BOT_ME, "Список: хлеб", FAMILY)
    await send(message(masha, "а молоко?", FAMILY, reply_to=posted))
    check("a reply to the bot counts as asking it, with the message it replies to",
          len(api.questions) == 3 and "Список: хлеб" in api.questions[-1][0], api.questions[-1][0])

    emoji = "🎉 @family_bot привет"
    await send(message(masha, emoji, FAMILY, mention(emoji)))
    check("a mention after an emoji is found by its UTF-16 offset", len(api.questions) == 4)

    await send(message(masha, "Робин, что купить к ужину?", FAMILY))
    await send(message(masha, "надо спросить у Робина про билеты", FAMILY))
    check("a group message that calls the bot by its name, in any case and anywhere in the text, asks it",
          len(api.questions) == 6 and "Question: надо спросить у Робина про билеты" in api.questions[-1][0],
          api.questions[-1][0])
    await send(message(masha, "Робинсон звонил", FAMILY))
    await send(message(stranger, "Робин, привет", FAMILY))
    check("a longer word is not the name, and someone not on the list who names the bot is still ignored",
          len(api.questions) == 6, api.questions[-1][0])

    photo_sizes = [{"file_id": "small", "width": 320, "height": 240}, {"file_id": "big", "width": 1280, "height": 960},
                   {"file_id": "huge", "width": 2560, "height": 1920}]
    await send({**message(oleg, ""), "photo": photo_sizes})
    text, scope = subscription.questions[-1]
    check("a photo sent to the bot privately without a word is a question about it, fetched at a size Claude can read",
          len(subscription.questions) == 2 and "say what is in the photo" in text and "view_photo" in text
          and list(scope.origin["photos"].values()) == ["big"], (text, scope.origin))

    snapshot = {**message(masha, "", FAMILY), "photo": photo_sizes}
    snapshot.pop("text")
    await send(message(masha, "Робин, что на фото?", FAMILY, reply_to=snapshot))
    text, scope = api.questions[-1]
    check("a question replying to a photo says which message to look at, and carries the photo's file",
          f"view_photo, message_id {snapshot['message_id']}" in text
          and scope.origin["photos"] == {str(snapshot["message_id"]): "big"}, (text, scope.origin))

    api.reply_text = "{{mention:777001}}, Олег просил купить молоко <3"
    remind = "@family_bot напомни Даше про молоко"
    await send(message(masha, remind, FAMILY, mention(remind)))
    last = bot.sent[-1]
    check("someone without a username is tagged by a link to their id, and the rest is escaped",
          last.parse_mode == "HTML" and last.text == '<a href="tg://user?id=777001">Даша</a>, Олег просил купить '
                                                     'молоко &lt;3', vars(last))
    api.reply_text = "{{mention:424242}} привет"
    await send(message(masha, "@family_bot скажи привет", FAMILY, mention("@family_bot скажи привет")))
    check("a tag of someone not in the chat is dropped and the answer goes plain",
          bot.sent[-1].parse_mode is None and bot.sent[-1].text == "привет", vars(bot.sent[-1]))
    api.reply_text = "Ответ"

    wants_in = message(stranger, "я тоже хочу", FAMILY)
    await send(message(stranger, "/allow", FAMILY, command("/allow"), reply_to=wants_in))
    check("/allow from someone else changes nothing", str(STRANGER) not in common.load_access()["allowed"])
    await send(message(oleg, "/allow", FAMILY, command("/allow"), reply_to=wants_in))
    check("/allow from the owner adds the person, at the default of 10 a day",
          str(STRANGER) in common.load_access()["allowed"] and "Чужой" in bot.sent[-1].text
          and "до 10 вопросов" in bot.sent[-1].text, bot.sent[-1].text)

    await send(message(stranger, "/allow all", FAMILY, command("/allow all")))
    check("/allow all from someone else changes nothing", str(DASHA) not in common.load_access()["allowed"])
    await send(message(oleg, "/allow all", FAMILY, command("/allow all")))
    allowed_now = common.load_access()["allowed"]
    check("/allow all from the owner puts everyone in the group on the list and names only those it added",
          allowed_now.get(str(DASHA), {}).get("name") == "Даша" and str(OWNER) not in allowed_now
          and "Даша" in bot.sent[-1].text and "Маша" not in bot.sent[-1].text, (allowed_now, bot.sent[-1].text))
    await send(message(oleg, "/allow all", FAMILY, command("/allow all")))
    check("run again, it says everyone is already there", "уже в списке" in bot.sent[-1].text, bot.sent[-1].text)
    await send(message(oleg, "/allow all", None, command("/allow all")))
    check("/allow all in a private chat asks for it in a group", "в группе" in bot.sent[-1].text, bot.sent[-1].text)

    await send(message(stranger, "/limit 99", FAMILY, command("/limit"), reply_to=wants_in))
    check("/limit from someone else changes nothing",
          "daily_limit" not in common.load_access()["allowed"][str(STRANGER)])
    await send(message(oleg, "/limit 1", FAMILY, command("/limit"), reply_to=wants_in))
    check("/limit N from the owner, as a reply, sets that person's limit",
          common.load_access()["allowed"][str(STRANGER)].get("daily_limit") == 1 and "1 в день" in bot.sent[-1].text,
          bot.sent[-1].text)
    common.remember(chats=[({"id": MASHA, "type": "private", "first_name": "Маша", "username": "masha"},
                            int(time.time()))])
    await send(message(oleg, "/limit@family_bot @masha 25", None, command("/limit@family_bot")))
    check("/limit @username N sets it too", common.load_access()["allowed"][str(MASHA)].get("daily_limit") == 25,
          common.load_access()["allowed"])
    await send(message(oleg, "/limit 5", None, command("/limit")))
    check("/limit without a person explains itself", "/limit N" in bot.sent[-1].text, bot.sent[-1].text)
    newcomer = message(user(555555, "Новенький"), "привет", FAMILY)
    await send(message(oleg, "/limit 5", FAMILY, command("/limit"), reply_to=newcomer))
    check("/limit for someone not on the list says to /allow first", "/allow" in bot.sent[-1].text, bot.sent[-1].text)

    answers, sent = len(api.questions), len(bot.sent)
    for _ in range(3):
        await send(message(stranger, question, FAMILY, mention(question)))
    check("at a limit of 1: one answer, then one notice, then silence",
          len(api.questions) == answers + 1 and len(bot.sent) == sent + 2 and "1 в день" in bot.sent[-1].text,
          [item.text for item in bot.sent[sent:]])
    usage = common.usage_today(STRANGER)
    check("the API answer is counted with its estimated cost on Sonnet 5",
          usage.get("questions") == 1 and usage.get("cost_usd") == common.estimate_cost("claude-sonnet-5", totals()),
          usage)

    await send(message(oleg, "/deny@other_bot 999", None, command("/deny@other_bot")))
    check("a command addressed to another bot is not taken", str(STRANGER) in common.load_access()["allowed"])
    await send(message(oleg, f"/deny@family_bot {STRANGER}", None, command("/deny@family_bot")))
    check("/deny@bot <id> from the owner removes the person", str(STRANGER) not in common.load_access()["allowed"])
    await send(message(oleg, "/allowed", None, command("/allowed")))
    check("/allowed names the owner's subscription, the list and the models",
          "подписке" in bot.sent[-1].text and "Маша" in bot.sent[-1].text and "claude-sonnet-5" in bot.sent[-1].text,
          bot.sent[-1].text)

    class BrokenSubscription:
        async def answer(self, question, scope, settings):
            raise responder.SubscriptionError("Claude AI usage limit reached")

    bot_responder.subscription = BrokenSubscription()
    answers = len(api.questions)
    await send(message(oleg, "ещё вопрос"))
    check("a failing subscription answer tells the owner why and spends nothing on the API",
          "usage limit reached" in bot.sent[-1].text and len(api.questions) == answers, bot.sent[-1].text)
    bot_responder.subscription = subscription

    common.change_access(lambda access: access["settings"].update(owner_route="api"))
    await send(message(oleg, "а через API?"))
    check("with owner_route 'api' the owner goes through the API too", len(api.questions) == answers + 1)
    common.change_access(lambda access: access["settings"].update(owner_route="subscription"))

    check("the status counts what was answered and ignored",
          bot_responder.status["answered"] == len(api.questions) + len(subscription.questions)
          and bot_responder.status["ignored"] == 2, bot_responder.status)

    sent = len(bot.sent)
    await deliver(joined(FAMILY, oleg))
    check("added to a group by the owner, the bot greets it",
          len(bot.sent) == sent + 1 and bot.sent[-1].chat_id == FAMILY["id"] and "@family_bot" in bot.sent[-1].text
          and bot.sent[-1].reply_to is None, bot.sent[sent:])
    await deliver(joined(OTHER_GROUP, stranger))
    check("added by someone not on the list, it stays quiet", len(bot.sent) == sent + 1, bot.sent[sent:])
    check("greeting.txt names the bot and nothing is left unfilled",
          "{bot}" not in responder.greeting(BOT_ME) and "@family_bot" in responder.greeting(BOT_ME))

    chatter = message(masha, "что на ужин?", FAMILY)
    await send(chatter)
    asked = message(masha, question, FAMILY, mention(question))
    await send(asked)
    kept = {item["id"] for item in common.bot_state()["messages"] if item["chat_id"] == FAMILY["id"]}
    check("group chatter not meant for the bot is not kept; a question to it is",
          chatter["message_id"] not in kept and asked["message_id"] in kept, sorted(kept))

    for tag, model in (("#simple", "sonnet"), ("#medium", "claude-opus-5-5"), ("#HARD", "fable")):
        await send(message(oleg, f"{tag} сколько планет в солнечной системе?"))
        check(f"the owner's {tag} picks {model}, and the tag is not part of the question",
              subscription.settings[-1]["owner_model"] == model
              and subscription.questions[-1][0].endswith("Question: сколько планет в солнечной системе?"),
              (subscription.settings[-1].get("owner_model"), subscription.questions[-1][0][-70:]))
    await send(message(oleg, "сколько #hard лун у Юпитера?"))
    check("a tag inside the question is taken out without leaving a gap",
          subscription.questions[-1][0].endswith("Question: сколько лун у Юпитера?"), subscription.questions[-1][0][-60:])
    await send(message(oleg, "а без тега?"))
    check("without a tag the owner's model is the default", subscription.settings[-1]["owner_model"] == "claude-opus-5-5",
          subscription.settings[-1].get("owner_model"))
    tagged = "@family_bot #hard что это?"
    await send(message(masha, tagged, FAMILY, mention(tagged)))
    check("anyone else's tag is only text: their model stays and the tag stays in the question",
          api.settings[-1]["model"] == "claude-sonnet-5" and "Question: #hard что это?" in api.questions[-1][0],
          api.questions[-1][0][-60:])
    common.change_access(lambda access: access["settings"].update(owner_route="api"))
    await send(message(oleg, "#hard а через API?"))
    picked = [api.settings[-1].get("model")]
    await send(message(oleg, "#medium а через API?"))
    picked.append(api.settings[-1].get("model"))
    check("on the API route the owner's tag picks the API model: an alias becomes its API id, a full id stays",
          picked == ["claude-fable-5-1", "claude-opus-5-5"], picked)
    common.change_access(lambda access: access["settings"].update(owner_route="subscription"))

    class PollingBot(FakeBot):
        """Hands out batches of updates, then ends run() the way a stop would."""

        def __init__(self, batches):
            super().__init__()
            self.batches = list(batches)
            self.offsets = []

        async def call(self, method, http_timeout=30, **params):
            if method != "getUpdates":
                return True
            self.offsets.append(params.get("offset"))
            if not self.batches:
                raise asyncio.CancelledError
            return self.batches.pop(0)

    async def run_until_stopped(polling_bot):
        try:
            await responder.Responder(polling_bot, FakeAnswerer(), FakeScopes(), OWNER).run()
        except asyncio.CancelledError:
            pass
        common.RESPONDER_FILE.unlink(missing_ok=True)

    common.save_json(common.OFFSET_FILE, {"next": 500, "at": int(time.time())})
    polling = PollingBot([[{"update_id": 500, "message": message(stranger, "привет", FAMILY)}]])
    await run_until_stopped(polling)
    check("polling starts from the saved offset, and the next one is saved as the update is taken",
          polling.offsets == [500, 501] and common.load_json(common.OFFSET_FILE)["next"] == 501, polling.offsets)
    common.save_json(common.OFFSET_FILE, {"next": 900, "at": int(time.time()) - 8 * 86400})
    polling = PollingBot([])
    await run_until_stopped(polling)
    check("an offset older than six days is not used", polling.offsets == [None], polling.offsets)


def block(**fields):
    return SimpleNamespace(**fields)


def reply(stop_reason, *content, searches=0):
    usage = SimpleNamespace(input_tokens=100, output_tokens=10, cache_creation_input_tokens=0,
                            cache_read_input_tokens=50, server_tool_use=SimpleNamespace(web_search_requests=searches))
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **options):
        self.calls.append({**options, "messages": list(options["messages"])})
        return self.script.pop(0)


class RecordingSearch:
    def __init__(self):
        self.calls = []

    async def search(self, scope, query, chat_id=None, limit=10):
        self.calls.append(("search", query, chat_id, limit))
        return {"results": [{"chat": "Семья", "text": "билеты на 14:00"}]}

    async def history(self, scope, chat_id=None, topic_id=None, days=1, limit=100):
        self.calls.append(("history", chat_id, topic_id, days, limit))
        return {"messages": []}

    async def voice(self, scope, message_id, chat_id=None):
        self.calls.append(("voice", message_id, chat_id))
        return {"text": "купи хлеба", "message_id": message_id}


class RecordingWriter:
    def __init__(self):
        self.calls = []

    async def post(self, scope, text, topic_id=None):
        self.calls.append(("post", scope.current, text, topic_id))
        return {"posted": True, "message_id": 900}

    async def edit(self, scope, message_id, text):
        self.calls.append(("edit", scope.current, message_id, text))
        return {"error": "the bot can edit only a message it posted itself"}


async def answerer_checks():
    api = FakeMessages([
        reply("tool_use", block(type="text", text="Поищу."),
              block(type="tool_use", id="t1", name="search_chats", input={"query": "билеты", "limit": 50})),
        reply("pause_turn", block(type="server_tool_use", id="s1", name="web_search", input={"query": "x"}),
              searches=1),
        reply("tool_use", block(type="tool_use", id="t2", name="read_chat_history", input={"topic_id": 42, "days": 99})),
        reply("end_turn", block(type="text", text="Билеты на 14:00."), searches=1),
    ])
    search = RecordingSearch()
    answerer = responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=api)), search, "system")
    opus = {**common.DEFAULT_SETTINGS, "model": "claude-opus-5"}
    text, used = await answerer.answer("вопрос", Scope(chats={-100777: "Семья"}, private=False, current=-100777), opus)
    first, second, third, fourth = api.calls
    check("the answer is the final text", text == "Билеты на 14:00.", text)
    check("the system prompt ends in a cache breakpoint and every step caches the conversation so far",
          all(call.get("cache_control") == {"type": "ephemeral"}
              and call["system"] == [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}]
              for call in api.calls), first)
    check("Opus 5 requests carry medium effort and the default fallbacks",
          first["model"] == "claude-opus-5" and first.get("output_config") == {"effort": "medium"}
          and first.get("fallbacks") == "default" and first.get("betas") == [responder.FALLBACK_BETA], first)
    check("without a writer: web search with a cap, search_chats and read_chat_history",
          [tool["name"] for tool in first["tools"]] == ["web_search", "search_chats", "read_chat_history", "read_voice"]
          and first["tools"][0]["type"] == "web_search_20260209" and first["tools"][0]["max_uses"] == 1,
          first["tools"])
    check("both chat tools ran with their arguments checked and clamped",
          search.calls == [("search", "билеты", None, 20), ("history", None, 42, 30, 100)], search.calls)
    check("a tool result goes back as the next user turn",
          second["messages"][-1]["role"] == "user" and second["messages"][-1]["content"][0]["tool_use_id"] == "t1",
          second["messages"][-1])
    check("a paused turn resumes without a new user message",
          third["messages"][-1]["role"] == "assistant" and len(third["messages"]) == len(second["messages"]) + 1,
          [item["role"] for item in third["messages"]])
    check("tokens and searches add up over the turns",
          used == {"input_tokens": 400, "output_tokens": 40, "cache_creation_input_tokens": 0,
                   "cache_read_input_tokens": 200, "web_searches": 2}, used)

    writes = FakeMessages([
        reply("tool_use", block(type="tool_use", id="w1", name="post_message", input={"text": "Хлеб", "topic_id": 42}),
              block(type="tool_use", id="w2", name="edit_bot_message", input={"message_id": 591, "text": "Список"})),
        reply("end_turn", block(type="text", text="Добавил хлеб в «Покупки»."))])
    writer = RecordingWriter()
    writing = responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=writes)), search, "system", writer)
    group = Scope(chats={-100777: "Семья"}, private=False, current=-100777, topic=None)
    text, _ = await writing.answer("вопрос", group, dict(common.DEFAULT_SETTINGS))
    check("with a writer in a group, post_message and edit_bot_message are offered",
          [tool["name"] for tool in writes.calls[0]["tools"]][-2:] == ["post_message", "edit_bot_message"],
          [tool["name"] for tool in writes.calls[0]["tools"]])
    check("both writing tools ran into the group of the question",
          writer.calls == [("post", -100777, "Хлеб", 42), ("edit", -100777, 591, "Список")], writer.calls)
    results = writes.calls[1]["messages"][-1]["content"]
    check("both results go back in one user turn, a refusal marked as an error",
          [result["tool_use_id"] for result in results] == ["w1", "w2"]
          and [result["is_error"] for result in results] == [False, True], results)
    private = FakeMessages([reply("end_turn", block(type="text", text="ok"))])
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=private)), search, "system", writer).answer(
        "вопрос", Scope(everything=True), dict(common.DEFAULT_SETTINGS))
    check("asked privately, nothing to write into, so no writing tools",
          [tool["name"] for tool in private.calls[0]["tools"]] == ["web_search", "search_chats", "read_chat_history", "read_voice"],
          [tool["name"] for tool in private.calls[0]["tools"]])

    plain = FakeMessages([reply("end_turn", block(type="text", text="ok"))])
    sonnet = responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=plain)), None, "system")
    await sonnet.answer("вопрос", Scope(reason="no personal account"), dict(common.DEFAULT_SETTINGS))
    call = plain.calls[0]
    check("by default the API answers with Sonnet 5 at medium effort, without fallbacks",
          call["model"] == "claude-sonnet-5" and call.get("output_config") == {"effort": "medium"}
          and "fallbacks" not in call, call)
    check("without chat access only web search is offered", [tool["name"] for tool in call["tools"]] == ["web_search"],
          call["tools"])
    haiku_api = FakeMessages([reply("end_turn", block(type="text", text="ok"))])
    haiku = responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=haiku_api)), None, "system")
    await haiku.answer("вопрос", Scope(reason="none"), {**common.DEFAULT_SETTINGS, "model": "claude-haiku-4-5"})
    check("Haiku gets neither effort nor fallbacks",
          "output_config" not in haiku_api.calls[0] and "fallbacks" not in haiku_api.calls[0], haiku_api.calls[0])

    common.REMINDERS_FILE.unlink(missing_ok=True)
    origin = {"chat_id": -100777, "topic_id": 42, "message_id": 700, "asker_id": MASHA, "asker_name": "Маша"}
    remind_api = FakeMessages([
        reply("tool_use", block(type="tool_use", id="m1", name="set_reminder",
                                input={"text": "забрать посылку", "minutes_from_now": 120})),
        reply("end_turn", block(type="text", text="Напомню через два часа."))])
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=remind_api)), search, "system").answer(
        "вопрос", Scope(chats={-100777: "Семья"}, private=False, current=-100777, topic=42, origin=origin),
        dict(common.DEFAULT_SETTINGS))
    names = [tool["name"] for tool in remind_api.calls[0]["tools"]]
    check("a question from a chat gets the reminder tools, and set_reminder sets one for that chat and topic",
          names[-3:] == ["set_reminder", "list_reminders", "cancel_reminder"]
          and [(item["text"], item["chat_id"], item["topic_id"]) for item in reminders.load()]
          == [("забрать посылку", -100777, 42)], (names, reminders.load()))
    real_hooks = responder.local_hooks
    responder.local_hooks = SimpleNamespace(SYSTEM_EXTRA="{bot_name} speaks briefly.",
                                            reply_note=lambda chat_id, message_id: "A note of the owner's own."
                                            if message_id == 4242 else None)
    try:
        replied_to = {**message(user(MASHA, "Маша"), "Список", FAMILY), "message_id": 4242}
        block_text = responder.question_block(message(user(MASHA, "Маша"), "а молоко?", FAMILY, reply_to=replied_to),
                                              "а молоко?", Scope(), [], [])
        check("local_hooks.py can add to Claude's instructions and note what a question replies to",
              responder.local_system("Робин") == "\n\nРобин speaks briefly." and "A note of the owner's own." in block_text,
              block_text)
    finally:
        responder.local_hooks = real_hooks
    check("without local_hooks.py nothing is added",
          responder.local_system("Робин") == "" if responder.local_hooks is None else True)


class FakeMessage:
    def __init__(self, chat_id, text, date, message_id=7):
        self.chat_id, self.id, self.message, self.out, self.media, self.date = chat_id, message_id, text, False, None, date

    async def get_chat(self):
        return family_chat() if self.chat_id < 0 else types.User(id=self.chat_id, first_name="Маша")

    async def get_sender(self):
        return types.User(id=MASHA, first_name="Маша")


def family_chat():
    return types.Chat(id=777, title="Семья", photo=types.ChatPhotoEmpty(), participants_count=3, date=None, version=1)


class FakeTelethon:
    def __init__(self):
        self.calls = []
        self.refreshed = False
        self.transcriptions = 0

    async def get_input_entity(self, target):
        if target in (MASHA, "@masha"):
            return types.InputPeerUser(MASHA, 42)
        if target == -777:
            return types.InputPeerChat(777)
        raise ValueError(f"unknown {target}")

    async def get_dialogs(self, limit=None):
        self.refreshed = True
        return []

    async def __call__(self, request):
        if isinstance(request, functions.messages.TranscribeAudioRequest):
            self.transcriptions += 1
            return SimpleNamespace(text="Робин, купи" if self.transcriptions == 1 else "Робин, купи хлеба",
                                   pending=self.transcriptions == 1)
        if isinstance(request, functions.messages.GetForumTopicsRequest):
            return SimpleNamespace(topics=[SimpleNamespace(id=1, title="Общие вопросы"),
                                           SimpleNamespace(id=42, title="Покупки"), types.ForumTopicDeleted(id=7)])
        return SimpleNamespace(chats=[family_chat()])

    async def get_participants(self, chat_id, limit=None):
        return [types.User(id=OWNER, first_name="Олег", username="oleg_example"), types.User(id=MASHA, first_name="Маша"),
                types.User(id=5, first_name="Бот", bot=True), types.User(id=6, deleted=True)]

    async def get_messages(self, entity, ids=None):
        now = datetime.now(timezone.utc)
        if ids == 31:
            posted = FakeMessage(entity, "", now - timedelta(days=40), 31)
            posted.media = types.MessageMediaPhoto()
            return posted
        if ids == 33:
            said = FakeMessage(entity, "", now - timedelta(hours=3), 33)
            said.media, said.voice = types.MessageMediaDocument(), SimpleNamespace()
            return said
        return FakeMessage(entity, "просто текст", now, ids) if ids == 32 else None

    async def download_media(self, message, file=None, thumb=None):
        return b"\xff\xd8\xff\xe0" + b"0" * 100

    def iter_messages(self, entity, search=None, limit=None, reply_to=None, offset_id=0, offset_date=None):
        self.calls.append((entity, search, limit, reply_to, offset_id, offset_date))
        now = datetime.now(timezone.utc)
        if search is not None:
            messages = [FakeMessage(entity or -777, "билеты на 14:00", now - timedelta(hours=2))]
        else:
            messages = [FakeMessage(entity, "последнее", now - timedelta(hours=1), 9),
                        FakeMessage(entity, "раньше", now - timedelta(hours=5), 8),
                        FakeMessage(entity, "", now - timedelta(hours=6), 7),
                        FakeMessage(entity, "старое", now - timedelta(days=3), 6)]
        messages = messages[:limit] if limit else messages

        async def found():
            for item in messages:
                yield item

        return found()


async def search_checks():
    telethon = FakeTelethon()
    search = TelegramSearch(telethon, OWNER, "Олег")
    masha = user(MASHA, "Маша", "masha")
    in_group = await search.scope_for(masha, in_group=True)
    check("asked in a group: the shared group, not the one-on-one chat",
          in_group.chats == {-777: "Семья"} and not in_group.private, vars(in_group))
    in_private = await search.scope_for(masha, in_group=False)
    check("asked privately: the shared group and the chat with the person",
          in_private.chats == {-777: "Семья", MASHA: "Маша"}, vars(in_private))
    unknown = await search.scope_for(user(STRANGER, "Чужой"), in_group=False)
    check("a person the owner's account does not know gets no chat access, after one refresh",
          unknown.reason is not None and telethon.refreshed, vars(unknown))
    outside = await search.search(in_private, "билеты", chat_id=-100999)
    check("a chat outside the scope is refused without being searched", "error" in outside and not telethon.calls,
          outside)
    found = await search.search(in_group, "билеты")
    check("the search covers the scope's chats",
          [call[0] for call in telethon.calls if call[1] is not None] == [-777] and found["results"][0]["chat"] == "Семья"
          and found["results"][0]["from"] == "Маша", found)

    here = Scope(chats={-777: "Семья"}, private=False, current=-777)
    history = await search.history(here)
    check("read_chat_history reads the current chat's last day, oldest first, skipping empty messages",
          [line["text"] for line in history.get("messages", [])] == ["раньше", "последнее"]
          and telethon.calls[-1][3] is None, (history, telethon.calls[-1]))
    in_topic = Scope(chats={-777: "Семья"}, private=False, current=-777, topic=42)
    history = await search.history(in_topic)
    check("asked in a topic, read_chat_history reads that topic", telethon.calls[-1][3] == 42
          and history.get("topic_id") == 42, (history, telethon.calls[-1]))
    await search.history(in_topic, topic_id=1)
    check("the general topic is read as the whole chat", telethon.calls[-1][3] is None, telethon.calls[-1])

    deep = Scope(everything=True, private=True, current=-777, deep=True)
    check("a deep scope tells Claude there is no date limit, an ordinary one does not",
          "date limit" in deep.describe() and "date limit" not in here.describe(), deep.describe())
    whole_chat = await search.history(deep, days=0)
    check("a deep read with days=0 has no date limit",
          [line["text"] for line in whole_chat["messages"]] == ["старое", "раньше", "последнее"]
          and "next_before_id" not in whole_chat, whole_chat)
    paged = await search.history(deep, days=0, limit=2)
    check("a deep read that stopped at its limit says where to go on",
          [line["text"] for line in paged["messages"]] == ["раньше", "последнее"] and paged.get("next_before_id") == 8,
          paged)
    await search.history(deep, days=0, before_id=8)
    check("before_id reads below that message", telethon.calls[-1][4] == 8, telethon.calls[-1])
    await search.history(deep, days=7, before_date="2025-03-01")
    check("before_date reads from that day's local midnight back",
          telethon.calls[-1][5] == datetime(2025, 3, 1).astimezone(), telethon.calls[-1])
    check("a before_date that is not a date is refused", "error" in await search.history(deep, before_date="вчера"))
    limited = await search.history(here, days=99, limit=5000, before_id=8, before_date="2025-03-01")
    check("an ordinary scope keeps to 30 days and 300 messages and ignores before_id and before_date",
          telethon.calls[-1][2] == 300 and telethon.calls[-1][4] == 0 and telethon.calls[-1][5] is None
          and limited["days"] == 30 and "before_date" not in limited, (limited, telethon.calls[-1]))
    deep_found = await search.search(deep, "билеты", limit=500, before_date="2025-03-01")
    check("a deep search looks through everything, keeps up to 100 and starts before before_date",
          telethon.calls[-1][0] is None and telethon.calls[-1][2] == 200
          and telethon.calls[-1][5] == datetime(2025, 3, 1).astimezone() and deep_found["results"][0]["chat"] == "Семья",
          (deep_found, telethon.calls[-1]))

    class QuietTelethon(FakeTelethon):
        def iter_messages(self, entity, search=None, limit=None, reply_to=None):
            now = datetime.now(timezone.utc)
            messages = [FakeMessage(entity, "Яблоки / Сыр", now - timedelta(days=9), 20),
                        FakeMessage(entity, "Какао", now - timedelta(days=12), 19)]

            async def found():
                for item in messages:
                    yield item

            return found()

    quiet = await TelegramSearch(QuietTelethon(), OWNER, "Олег").history(in_topic)
    check("a topic nobody wrote in lately still shows its latest messages, marked as older",
          [line["text"] for line in quiet["messages"]] == ["Какао", "Яблоки / Сыр"]
          and "nothing was written in the last 1 days" in quiet.get("note", ""), quiet)
    check("read_chat_history refuses a chat outside the scope",
          "error" in await search.history(here, chat_id=-100999))
    check("read_chat_history without a current chat asks for one",
          "error" in await search.history(Scope(chats={-777: "Семья"}, private=False)))
    people = await search.participants(-777)
    check("the people of a group leave out bots and deleted accounts",
          people == [{"id": OWNER, "name": "Олег", "username": "oleg_example"}, {"id": MASHA, "name": "Маша"}], people)
    topics = await search.topics(-777)
    check("the topics of a group leave out deleted ones", topics == TOPICS, topics)
    from_chat = Scope(everything=True, private=False, current=-777, origin={
        "chat_id": -777, "topic_id": 42, "message_id": 5, "asker_id": MASHA, "asker_name": "Маша"})
    check("a scope survives the trip to search_mcp.py, deep or not, with where the question came from",
          all(vars(Scope.from_dict(json.loads(json.dumps(item.to_dict())))) == vars(item)
              for item in (in_topic, deep, from_chat)))
    check("a question from a chat is told it can set reminders, one from nowhere that it cannot",
          "set_reminder" in from_chat.describe() and "set_reminder" not in in_topic.describe())
    owner_in_group = Scope(everything=True, private=False)
    check("the owner asking in a group: groups yes, one-on-one chats no",
          owner_in_group.allows(-777, False) and not owner_in_group.allows(MASHA, True))
    check("long answers are split under Telegram's limit",
          all(len(piece) <= responder.MESSAGE_LIMIT for piece in responder.split_message("слово " * 2000)))


async def writer_checks():
    calls = []

    def fake_request(token, method, params=None, timeout=30):
        calls.append((method, params))
        if method == "editMessageText" and params["message_id"] == 1:
            raise common.BotApiError(method, 400, "Bad Request: message can't be edited")
        return {"message_id": 900 + len(calls), "date": int(time.time()), "text": params.get("text"),
                "chat": {"id": params["chat_id"], "type": "supergroup", "title": "Семья"},
                "from": {"id": 1, "is_bot": True, "first_name": "Робин"}}

    real_request = common.bot_request
    common.bot_request = fake_request
    try:
        writer = BotWriter("123:fake")
        in_topic = Scope(chats={-100777: "Семья"}, private=False, current=-100777, topic=42)
        posted = await writer.post(in_topic, " Хлеб ")
        check("post_message writes into the topic of the question",
              posted["posted"] and calls[-1] == ("sendMessage", {"chat_id": -100777, "text": "Хлеб", "message_thread_id": 42,
                                                                 "link_preview_options": {"is_disabled": True}}),
              (posted, calls[-1]))
        await writer.post(in_topic, "В общий", topic_id=1)
        check("into the general topic it goes without a thread id", "message_thread_id" not in calls[-1][1], calls[-1])
        kept = [item for item in common.bot_state()["messages"] if item["from_bot"] and item["chat_id"] == -100777]
        check("what the bot posted is remembered as its own", {"Хлеб", "В общий"} <= {item.get("text") for item in kept},
              kept)
        made = len(calls)
        private = await writer.post(Scope(everything=True), "Хлеб")
        check("a question from a private chat writes nowhere", "error" in private and len(calls) == made, private)
        check("an empty text is not posted", "error" in await writer.post(in_topic, "  ") and len(calls) == made)
        edited = await writer.edit(in_topic, 905, "Список: хлеб")
        check("edit_bot_message edits in the group of the question",
              edited == {"edited": True, "message_id": 905} and calls[-1][0] == "editMessageText"
              and calls[-1][1]["chat_id"] == -100777, (edited, calls[-1]))
        refused_edit = await writer.edit(in_topic, 1, "Список")
        check("editing someone else's message says to post a new one", "post a new one" in refused_edit.get("error", ""),
              refused_edit)
    finally:
        common.bot_request = real_request


async def subscription_checks():
    runs = []
    outputs = []

    def result(**changes):
        return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": " Ответ через подписку ",
                           "total_cost_usd": 0.05, "usage": {
                               "input_tokens": 5, "output_tokens": 7, "cache_creation_input_tokens": 100,
                               "cache_read_input_tokens": 50, "server_tool_use": {"web_search_requests": 2}}, **changes})

    async def fake_run(arguments, stdin_text, cwd, environment):
        config_path = arguments[arguments.index("--mcp-config") + 1] if "--mcp-config" in arguments else None
        config = json.loads(Path(config_path).read_text(encoding="utf-8")) if config_path else None
        scope_path = config["mcpServers"]["chats"]["args"][-1] if config else None
        for folder in responder.claude_cache_folders(Path(cwd)):
            (folder / "mcp-logs-chats").mkdir(parents=True, exist_ok=True)
        settings_path = arguments[arguments.index("--settings") + 1] if "--settings" in arguments else None
        runs.append({"arguments": arguments, "stdin": stdin_text, "cwd": Path(cwd), "environment": environment,
                     "config": config, "scope": json.loads(Path(scope_path).read_text(encoding="utf-8"))
                     if scope_path else None,
                     "settings": json.loads(Path(settings_path).read_text(encoding="utf-8")) if settings_path else None})
        return outputs.pop(0)

    def after(arguments, flag):
        return arguments[arguments.index(flag) + 1]

    real_claude_code = responder.claude_code
    responder.claude_code = lambda: "claude.exe"
    real_local = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(TEMP / "local")
    os.environ["ANTHROPIC_BASE_URL"] = "http://desktop-proxy"
    os.environ["CLAUDE_CODE_SESSION_ID"] = "desktop-session"
    try:
        answerer = responder.SubscriptionAnswerer("system", chats_available=True, run=fake_run)
        scope = Scope(everything=True, private=False, current=-100777, topic=42)
        outputs.append((0, "some log line\n" + result(), ""))
        text, used = await answerer.answer("вопрос", scope, dict(common.DEFAULT_SETTINGS))
        run = runs[-1]
        arguments = run["arguments"]
        check("Claude Code's answer and usage are read from its JSON result",
              text == "Ответ через подписку" and used == {"input_tokens": 5, "output_tokens": 7,
                                                          "cache_creation_input_tokens": 100,
                                                          "cache_read_input_tokens": 50, "web_searches": 2},
              (text, used))
        check("Claude Code runs headless with Opus at medium effort, the question on stdin",
              arguments[:2] == ["claude.exe", "-p"] and after(arguments, "--model") == "claude-opus-5-5"
              and after(arguments, "--effort") == "medium" and after(arguments, "--output-format") == "json"
              and after(arguments, "--system-prompt") == "system" and run["stdin"] == "вопрос", arguments)
        check("in a group it gets the web tools and all four of our tools, no other MCP, no prompts, no session",
              after(arguments, "--tools") == "WebSearch,WebFetch" and "--strict-mcp-config" in arguments
              and after(arguments, "--permission-prompts") == "none" and "--no-session-persistence" in arguments
              and after(arguments, "--allowedTools") == "WebSearch,WebFetch,mcp__chats__search_chats,"
                                                         "mcp__chats__read_chat_history,mcp__chats__read_voice,mcp__chats__view_photo,"
                                                         "mcp__chats__post_message,"
                                                         "mcp__chats__edit_bot_message", arguments)
        check("the MCP config starts search_mcp.py with this question's scope, topic included",
              run["config"]["mcpServers"]["chats"]["args"][0].endswith("search_mcp.py") and run["scope"] == scope.to_dict()
              and run["scope"]["topic"] == 42, run["config"])
        check("none of this process's Claude or Anthropic variables reach it, and without a token no config dir",
              not any(name.upper().startswith(("CLAUDE", "ANTHROPIC")) for name in run["environment"]), sorted(
                  name for name in run["environment"] if name.upper().startswith(("CLAUDE", "ANTHROPIC"))))
        check("the working directory is removed afterwards", not run["cwd"].exists(), run["cwd"])
        cache = responder.claude_cache_folders(run["cwd"])
        check("and so is what Claude Code kept about it in its cache - its MCP servers' logs",
              cache and cache[0].name.endswith(run["cwd"].name) and not any(folder.exists() for folder in cache), cache)
        check("its temp folder is inside that directory, so nothing kept there - a photo it was shown - outlives the answer",
              all(Path(run["environment"].get(name, "")).parent == run["cwd"] for name in ("TEMP", "TMP", "TMPDIR")),
              {name: run["environment"].get(name) for name in ("TEMP", "TMP", "TMPDIR")})
        check("Claude Code runs with every hook off and room for long MCP results",
              run["settings"] == {"disableAllHooks": True}
              and run["environment"].get("MAX_MCP_OUTPUT_TOKENS") == str(responder.MCP_OUTPUT_TOKENS),
              (run["settings"], run["environment"].get("MAX_MCP_OUTPUT_TOKENS")))

        outputs.append((0, result(), ""))
        await answerer.answer("вопрос", Scope(everything=True), dict(common.DEFAULT_SETTINGS))
        check("asked privately it may read chats but gets no writing tools",
              after(runs[-1]["arguments"], "--allowedTools") == "WebSearch,WebFetch,mcp__chats__search_chats,"
                                                                "mcp__chats__read_chat_history,mcp__chats__read_voice,mcp__chats__view_photo",
              runs[-1]["arguments"])
        outputs.append((0, result(), ""))
        await answerer.answer("вопрос", Scope(everything=True, origin={
            "chat_id": OWNER, "topic_id": None, "message_id": 5, "asker_id": OWNER, "asker_name": "Олег"}),
            dict(common.DEFAULT_SETTINGS))
        check("a question from a chat may set, list and cancel reminders there, with its origin in the scope file",
              after(runs[-1]["arguments"], "--allowedTools") == "WebSearch,WebFetch,mcp__chats__search_chats,"
                                                                "mcp__chats__read_chat_history,mcp__chats__read_voice,mcp__chats__view_photo,"
                                                                "mcp__chats__set_reminder,mcp__chats__list_reminders,"
                                                                "mcp__chats__cancel_reminder,mcp__chats__send_image,"
                                                                "mcp__chats__find_files,mcp__chats__send_file"
              and runs[-1]["scope"]["origin"]["chat_id"] == OWNER, (runs[-1]["arguments"], runs[-1]["scope"]))

        common.save_json(common.CLAUDE_TOKEN_FILE, {"token": "token-from-setup"})
        outputs.append((0, result(), ""))
        await answerer.answer("вопрос", scope, dict(common.DEFAULT_SETTINGS))
        environment = runs[-1]["environment"]
        check("with a token from login.py claude it gets its own config dir and that token",
              environment.get("CLAUDE_CONFIG_DIR") == str(common.CLAUDE_CONFIG)
              and environment.get("CLAUDE_CODE_OAUTH_TOKEN") == "token-from-setup", sorted(environment))
        common.CLAUDE_TOKEN_FILE.unlink()

        no_chats = responder.SubscriptionAnswerer("system", chats_available=False, run=fake_run)
        logged_out = Scope(reason="the owner's personal account is not logged in", private=False,
                           current=FAMILY["id"], topic=42, deep=True,
                           origin={"chat_id": FAMILY["id"], "topic_id": 42, "message_id": 9, "asker_id": OWNER,
                                   "asker_name": "Олег", "photos": {"9": "AgAD-photo"}})
        outputs.append((0, result(), ""))
        await no_chats.answer("вопрос", logged_out, dict(common.DEFAULT_SETTINGS))
        check("with the owner's account logged out no chat is read, but the bot still looks at the photo it was sent, "
              "writes into the group, sets reminders and posts pictures and files",
              "--mcp-config" in runs[-1]["arguments"]
              and after(runs[-1]["arguments"], "--allowedTools") == "WebSearch,WebFetch,mcp__chats__view_photo,"
                                                                    "mcp__chats__post_message,mcp__chats__edit_bot_message,"
                                                                    "mcp__chats__set_reminder,mcp__chats__list_reminders,"
                                                                    "mcp__chats__cancel_reminder,mcp__chats__send_image,"
                                                                    "mcp__chats__find_files,mcp__chats__send_file",
              runs[-1]["arguments"])
        outputs.append((0, result(), ""))
        await no_chats.answer("вопрос", Scope(reason="the owner's personal account is not logged in"),
                              dict(common.DEFAULT_SETTINGS))
        check("a question from no chat, with no chat to read either, gets no MCP config and only the web tools",
              "--mcp-config" not in runs[-1]["arguments"]
              and after(runs[-1]["arguments"], "--allowedTools") == "WebSearch,WebFetch", runs[-1]["arguments"])

        async def raises(needle):
            try:
                await answerer.answer("вопрос", scope, dict(common.DEFAULT_SETTINGS))
            except responder.SubscriptionError as error:
                return needle in str(error), str(error)
            return False, "no error"

        outputs.append((1, result(is_error=True, result="Claude AI usage limit reached|1789300000"), ""))
        ok, detail = await raises("usage limit reached")
        check("an error result becomes a SubscriptionError with its text", ok, detail)
        outputs.append((1, "", "boom"))
        ok, detail = await raises("boom")
        check("no JSON at all becomes a SubscriptionError with the error output", ok, detail)
        responder.claude_code = lambda: None
        ok, detail = await raises("not found")
        check("no Claude Code at all is explained", ok, detail)
    finally:
        responder.claude_code = real_claude_code
        if real_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_local
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    check("the desktop app keeps a Claude Code this finds", bool(responder.claude_code()), responder.claude_code())

    real_which, real_reported = responder.shutil.which, responder.reported_version
    real_profile, real_appdata = os.environ.get("USERPROFILE"), os.environ.pop("APPDATA", None)
    home = TEMP / "home"
    app = home / "AppData" / "Local" / "Packages" / "Claude_test" / "LocalCache" / "Roaming" / "Claude" / "claude-code"
    # 2.1.275 the old way, straight in its version's folder; 2.1.280 the way the app keeps them since 2.1.286, one
    # folder deeper and verified; 2.1.299 one folder deeper but not verified yet - a download still running.
    (app / "2.1.275").mkdir(parents=True, exist_ok=True)
    (app / "2.1.275" / "claude.exe").write_bytes(b"")
    for version, verified in (("2.1.280", True), ("2.1.299", False)):
        (app / version / "0123abcd").mkdir(parents=True, exist_ok=True)
        (app / version / "0123abcd" / "claude.exe").write_bytes(b"")
        if verified:
            (app / version / "0123abcd" / ".verified").write_bytes(b"")
    on_path = home / ".local" / "bin" / "claude.exe"
    on_path.parent.mkdir(parents=True, exist_ok=True)
    on_path.write_bytes(b"")
    reported, chosen = {}, []
    responder.shutil.which = lambda name: str(on_path)
    responder.reported_version = lambda executable, modified: reported.get(executable, ())
    try:
        os.environ["USERPROFILE"] = str(home)
        for version in ("2.1.272", "2.1.290", None):
            reported.clear()
            if version:
                reported[str(on_path)] = responder.version_key(version)
            chosen.append(responder.claude_code())
        os.environ["USERPROFILE"] = str(TEMP / "no-app")
        chosen.append(responder.claude_code())
        os.environ["USERPROFILE"] = str(home)
        data = home / "AppData" / "Local" / "Claude-Data" / "claude_test" / "claude-code" / "2.1.304" / "aed62990"
        data.mkdir(parents=True, exist_ok=True)
        (data / "claude.exe").write_bytes(b"")
        (data / ".verified").write_bytes(b"")
        reported[str(on_path)] = responder.version_key("2.1.290")
        chosen.append(responder.claude_code())
    finally:
        responder.shutil.which, responder.reported_version = real_which, real_reported
        if real_profile is not None:
            os.environ["USERPROFILE"] = real_profile
        if real_appdata is not None:
            os.environ["APPDATA"] = real_appdata
    newest = str(app / "2.1.280" / "0123abcd" / "claude.exe")
    check("the newest Claude Code wins - the desktop app's, in its package or in Claude-Data, or the one on PATH - and "
          "an old copy on PATH does not hold the bot back from a new model",
          chosen == [newest, str(on_path), newest, str(on_path), str(data / "claude.exe")], chosen)


async def tool_checks():
    common.remember(chats=[({"id": MASHA, "type": "private", "first_name": "Маша", "username": "masha"},
                            int(time.time()))])
    common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {}})
    allowed = json.loads(await server.bot_allow(person="@masha", daily_limit=5))
    check("bot_allow by @username", allowed == {"allowed": True, "id": MASHA, "name": "Маша", "daily_limit": 5},
          allowed)
    ok, detail = await refused(server.bot_allow(person="Маша"), "not a person reference")
    check("bot_allow refuses a bare name", ok, detail)
    ok, detail = await refused(server.bot_allow(person=str(OWNER)), "owner")
    check("bot_allow on the owner is explained", ok, detail)
    access = json.loads(await server.bot_access())
    check("bot_access lists the owner and the person, and a stopped responder",
          [person["id"] for person in access["people"]] == [OWNER, MASHA] and access["responder"] == {"running": False},
          access)
    denied = json.loads(await server.bot_deny(person=str(MASHA)))
    check("bot_deny removes the person", denied["was_allowed"] and str(MASHA) not in common.load_access()["allowed"],
          denied)

    common.save_json(common.BOT_FILE, {"token": "123:fake"})
    methods = []

    def fake_request(token, method, params=None, timeout=30):
        methods.append(method)
        if method == "getUpdates":
            return []
        raise AssertionError(method)

    common.bot_request = fake_request
    common.save_json(common.RESPONDER_FILE, {"pid": os.getpid(), "polled": int(time.time())})
    await server.find_chats(account="bot")
    check("with the responder running, the MCP server leaves the updates to it", "getUpdates" not in methods, methods)
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    common.save_json(common.RESPONDER_FILE, {"pid": finished.pid, "polled": int(time.time())})
    check("a fresh heartbeat left by a process that has exited does not count", not common.responder_alive())
    common.save_json(common.RESPONDER_FILE, {"pid": os.getpid(), "polled": int(time.time()) - 600})
    await server.find_chats(account="bot")
    check("with a stale heartbeat it polls again", "getUpdates" in methods, methods)


async def sdk_checks():
    """The real anthropic SDK against a mock HTTP transport: what it puts on the wire, and what comes back."""
    import anthropic
    import httpx2

    reply_body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
                  "content": [{"type": "text", "text": "Ответ"}], "stop_reason": "end_turn", "stop_sequence": None,
                  "usage": {"input_tokens": 12, "output_tokens": 3, "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0, "server_tool_use": {"web_search_requests": 1}}}

    def client_for(handler):
        return anthropic.AsyncAnthropic(api_key="test-key", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(
            transport=httpx2.MockTransport(handler)))

    seen = []

    def accepting(request):
        seen.append((request.headers.get("anthropic-beta", ""), json.loads(request.content)))
        return httpx2.Response(200, json=reply_body)

    opus = {**common.DEFAULT_SETTINGS, "model": "claude-opus-5"}
    answerer = responder.Answerer(client_for(accepting), None, "system")
    text, used = await answerer.answer("вопрос", Scope(reason="none"), opus)
    beta, body = seen[0]
    check("the real SDK sends Opus 5 with the fallback beta, fallbacks, effort and the web search tool",
          responder.FALLBACK_BETA in beta and body.get("fallbacks") == "default"
          and body.get("output_config") == {"effort": "medium"} and body["model"] == "claude-opus-5"
          and body["tools"][0]["type"] == "web_search_20260209", (beta, body))
    check("the real SDK puts both cache breakpoints on the wire",
          body.get("cache_control") == {"type": "ephemeral"}
          and body["system"][0].get("cache_control") == {"type": "ephemeral"}, body)
    check("and the answer and the usage are read from its reply",
          text == "Ответ" and used["input_tokens"] == 12 and used["web_searches"] == 1, (text, used))
    await responder.Answerer(client_for(accepting), None, "system").answer("вопрос", Scope(reason="none"),
                                                                          dict(common.DEFAULT_SETTINGS))
    beta, body = seen[-1]
    check("the real SDK sends Sonnet 5 without the fallback beta or fallbacks",
          body["model"] == "claude-sonnet-5" and "fallbacks" not in body and responder.FALLBACK_BETA not in beta,
          (beta, body))

    attempts = []

    def refusing_the_beta(request):
        body = json.loads(request.content)
        attempts.append("fallbacks" in body)
        if "fallbacks" in body:
            return httpx2.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": f"Unexpected value(s) `{responder.FALLBACK_BETA}` for the `anthropic-beta` header."}})
        return httpx2.Response(200, json=reply_body)

    without = responder.Answerer(client_for(refusing_the_beta), None, "system")
    text, _ = await without.answer("вопрос", Scope(reason="none"), opus)
    await without.answer("ещё вопрос", Scope(reason="none"), opus)
    check("a key refused the fallback beta still gets its answer, and the beta is not asked again",
          text == "Ответ" and attempts == [True, False, False] and without.fallbacks is False, attempts)


class InlineBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def call(self, method, http_timeout=30, **params):
        self.calls.append((method, params))
        return True


async def inline_checks():
    bot, api, subscription = InlineBot(), FakeAnswerer(), FakeAnswerer("Купить хлеб и молоко")
    common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {str(MASHA): {"name": "Маша"}}})
    inline = responder.Responder(bot, api, FakeScopes(), OWNER, subscription)
    oleg, masha = user(OWNER, "Олег"), user(MASHA, "Маша", "masha")
    asked_before = common.usage_today(OWNER).get("subscription_questions", 0)

    async def deliver(update):
        await inline.handle(update)
        await asyncio.gather(*list(inline.tasks))

    def calls(method):
        return [params for name, params in bot.calls if name == method]

    def query(sender, text, chat_type="private"):
        return {"update_id": 1, "inline_query": {"id": f"q{len(bot.calls)}", "from": sender, "query": text,
                                                  "chat_type": chat_type, "offset": ""}}

    def picked(sender, inline_id, text="что купить?"):
        return {"update_id": 2, "chosen_inline_result": {"result_id": key, "from": sender, "query": text,
                                                         "inline_message_id": inline_id}}

    def press(sender, inline_id, data):
        return {"update_id": 3, "callback_query": {"id": f"c{len(bot.calls)}", "from": sender, "data": data,
                                                   "inline_message_id": inline_id, "chat_instance": "1"}}

    await deliver(query(masha, "что купить?"))
    answered = calls("answerInlineQuery")[-1]
    check("someone else's inline query gets no results, cached for that person only",
          answered["results"] == [] and answered["is_personal"] is True, answered)
    await deliver(query(oleg, "что купить?", chat_type="supergroup"))
    check("the owner's inline query from a group gets no results", calls("answerInlineQuery")[-1]["results"] == [])
    await deliver(query(oleg, "что купить?"))
    answered = calls("answerInlineQuery")[-1]
    result = answered["results"][0] if answered["results"] else {}
    key = result.get("id", "")
    check("the owner's inline query from a one-on-one chat gets one result: a thinking message with a button",
          len(answered["results"]) == 1 and answered["cache_time"] == 0 and answered["is_personal"]
          and "думает" in result["input_message_content"]["message_text"]
          and result["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "ask:" + key, answered)
    check("nothing is asked while the owner is still typing", not subscription.questions and not api.questions)

    await deliver(picked(masha, "IM0"))
    check("a pick reported for someone else starts nothing", not subscription.questions and not calls("editMessageText"))
    await deliver(picked(oleg, "IM1"))
    edit = calls("editMessageText")[-1] if calls("editMessageText") else {}
    check("the owner's pick is answered on the subscription into the message it posted, without the button",
          len(subscription.questions) == 1 and edit.get("inline_message_id") == "IM1"
          and edit.get("text") == "Купить хлеб и молоко" and "reply_markup" not in edit, edit)
    text, scope = subscription.questions[-1] if subscription.questions else ("", Scope())
    check("the inline question says where the answer goes, reads with no date limit and writes nowhere",
          "inline mode" in text and "Question: что купить?" in text and scope.deep and scope.current is None,
          (text, vars(scope)))
    check("an inline answer counts as the owner's subscription question",
          common.usage_today(OWNER).get("subscription_questions") == asked_before + 1, common.usage_today(OWNER))

    await deliver(press(oleg, "IM1", "ask:" + key))
    check("pressing the button of a message already being answered starts nothing again",
          len(subscription.questions) == 1 and calls("answerCallbackQuery")[-1].get("text"), calls("answerCallbackQuery"))
    await deliver(press(masha, "IM2", "ask:" + key))
    check("someone else pressing the button starts nothing",
          len(subscription.questions) == 1 and "владельца" in calls("answerCallbackQuery")[-1].get("text", ""))
    await deliver(press(oleg, "IM2", "ask:" + key))
    check("without Inline Feedback the owner pressing the button starts the answer",
          len(subscription.questions) == 2 and calls("editMessageText")[-1]["inline_message_id"] == "IM2")
    await deliver(press(oleg, "IM3", "ask:" + "0" * 32))
    check("a button whose question is gone says to ask again",
          len(subscription.questions) == 2 and "ещё раз" in calls("answerCallbackQuery")[-1].get("text", ""))

    subscription.reply_text = "слово " * 1500
    await deliver(picked(oleg, "IM4", "длинно"))
    check("a long inline answer is cut to one message",
          0 < len(calls("editMessageText")[-1]["text"]) <= responder.MESSAGE_LIMIT)

    class BrokenSubscription:
        async def answer(self, question, scope, settings):
            raise responder.SubscriptionError("Claude AI usage limit reached")

    inline.subscription = BrokenSubscription()
    await deliver(picked(oleg, "IM5", "ещё"))
    check("a failed inline answer says why in its message",
          calls("editMessageText")[-1]["inline_message_id"] == "IM5"
          and "usage limit" in calls("editMessageText")[-1]["text"], calls("editMessageText")[-1])
    inline.subscription = subscription
    common.change_access(lambda access: access["settings"].update(owner_route="api"))
    await deliver(picked(oleg, "IM6", "через API"))
    check("with owner_route 'api' an inline question goes through the API, with the date limit",
          bool(api.questions) and not api.questions[-1][1].deep, api.questions)
    common.change_access(lambda access: access["settings"].update(owner_route="subscription"))
    await deliver(picked(oleg, "IM7", "#simple что купить?"))
    check("an inline question takes the owner's tag too",
          subscription.settings[-1]["owner_model"] == "sonnet" and subscription.questions[-1][0].endswith(
              "Question: что купить?"), subscription.settings[-1].get("owner_model"))


async def report_checks():
    now = datetime.now(timezone.utc)

    def at(minutes_ago):
        return (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")

    def said(minutes_ago, content, **fields):
        return {"type": "user", "timestamp": at(minutes_ago), "message": {"role": "user", "content": content}, **fields}

    def did(minutes_ago, *blocks):
        return {"type": "assistant", "timestamp": at(minutes_ago), "message": {"role": "assistant", "content": list(blocks)}}

    entries = [
        said(60, "старая задача", origin={"kind": "human"}),
        did(59, {"type": "tool_use", "id": "old", "name": "Edit", "input": {"file_path": "C:/work/project/old.cpp"}}),
        {"type": "ai-title", "aiTitle": "Сборка"},
        {"type": "custom-title", "customTitle": "Починка сборки"},
        said(12, "почини сборку", origin={"kind": "human"}),
        did(11, {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "C:/work/project/build.bat"}}),
        said(11, [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], toolUseResult={}),
        said(6, "<task-notification>done</task-notification>", origin={"kind": "task-notification"}),
        said(5, "Base directory for this skill", isMeta=True),
        did(4, {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "build.bat"}}),
        did(4, {"type": "tool_use", "id": "t3", "name": "Write", "input": {"file_path": "C:/work/project/build.bat"}}),
        did(1, {"type": "text", "text": "Готово."}),
    ]
    transcript = TEMP / "session.jsonl"
    transcript.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + '\n{"type": "assis',
                          encoding="utf-8")
    summary = session_report.work_summary(session_report.read_transcript(transcript))
    check("the work starts at the last typed prompt, not at a task notification or an injected skill",
          summary["prompt"] == "почини сборку" and summary["started"] is not None
          and abs((summary["started"] - (now - timedelta(minutes=12))).total_seconds()) < 1, summary)
    check("it counts the tools called since then and the files they changed, once each, under the name given",
          summary["tools"] == 3 and summary["files"] == ["C:/work/project/build.bat"] and summary["title"] == "Починка сборки",
          summary)

    settings = dict(common.DEFAULT_SETTINGS)
    hook = {"session_id": "abcdef12-session", "transcript_path": str(transcript), "cwd": "C:\\work\\project",
            "hook_event_name": "Stop", "stop_hook_active": False, "background_tasks": [],
            "last_assistant_message": "Починил **сборку** в [build.bat](C:/work/project/build.bat): `core_tests` проходят."}
    why_not = session_report.why_not
    check("twelve minutes of work is worth a report", why_not(hook, summary, settings, now) is None,
          why_not(hook, summary, settings, now))
    check("work under the minimum is not", why_not(hook, summary, {**settings, "report_min_minutes": 15}, now))
    check("nothing is reported with reports off", why_not(hook, summary, {**settings, "report_min_minutes": None}, now))
    check("nothing while a background task still runs",
          why_not({**hook, "background_tasks": [{"id": "b1", "status": "running"}]}, summary, settings, now))
    check("a background task that ended does not hold a report back",
          why_not({**hook, "background_tasks": [{"id": "b1", "status": "completed"}]}, summary, settings, now) is None)
    check("never for the bot's own Claude Code",
          why_not({**hook, "cwd": str(common.CLAUDE_WORK / "abc")}, summary, settings, now))
    check("Markdown becomes plain text",
          session_report.plain(hook["last_assistant_message"]) == "Починил сборку в build.bat: core_tests проходят.",
          session_report.plain(hook["last_assistant_message"]))

    sent, runs = [], []

    def fake_request(token, method, params=None, timeout=30):
        sent.append((method, params))
        return {"message_id": 70 + len(sent), "date": int(time.time()), "text": params["text"],
                "chat": {"id": params["chat_id"], "type": "private", "first_name": "Олег"},
                "from": {"id": 1, "is_bot": True, "first_name": "Робин"}}

    async def fake_run(arguments, stdin_text, cwd, environment, seconds=None):
        runs.append({"arguments": arguments, "stdin": stdin_text, "settings": json.loads(
            Path(arguments[arguments.index("--settings") + 1]).read_text(encoding="utf-8"))})
        return 0, json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": "Сборка починена."}), ""

    async def broken_run(arguments, stdin_text, cwd, environment, seconds=None):
        return 1, "", "boom"

    def after(arguments, flag):
        return arguments[arguments.index(flag) + 1]

    real_request, real_claude_code = common.bot_request, session_report.claude_code
    common.bot_request, session_report.claude_code = fake_request, lambda: "claude.exe"
    try:
        common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {}})
        common.save_json(common.BOT_FILE, {"token": "123:fake"})
        text = await session_report.report(hook, now=now, run=fake_run) or ""
        check("a short last message goes to the owner as it is, without Claude Code",
              not runs and len(sent) == 1 and sent[-1][1]["chat_id"] == OWNER
              and "Починил сборку в build.bat" in text and "✅ Починка сборки" in text and "12 мин" in text
              and "Задача: почини сборку" in text and "файлов изменено: 1" in text, (text, runs))
        again = await session_report.report(hook, now=now + timedelta(minutes=1), run=fake_run)
        check("the same work is not reported twice", again is None and len(sent) == 1, sent)
        long_hook = {**hook, "last_assistant_message": "Подробно. " * 200}
        text = await session_report.report(long_hook, now=now + timedelta(minutes=20), run=fake_run) or ""
        arguments = runs[-1]["arguments"] if runs else []
        check("a long last message is shortened by Claude Code on Haiku, with no tools and every hook off",
              "Сборка починена." in text and runs and after(arguments, "--tools") == ""
              and after(arguments, "--model") == "haiku" and runs[-1]["settings"] == {"disableAllHooks": True}
              and "Подробно." in runs[-1]["stdin"], (text, arguments))
        text = await session_report.report(long_hook, now=now + timedelta(minutes=40), run=broken_run) or ""
        check("when shortening fails the message is cut instead",
              "Подробно." in text and len(text) <= session_report.REPORT_LIMIT and len(sent) == 3, text)
        check("the reports' working directories are removed",
              not common.CLAUDE_WORK.exists() or not any(common.CLAUDE_WORK.iterdir()))
        check("why a report was not sent is logged",
              "no report: worked 60 s" in common.REPORT_LOG.read_text(encoding="utf-8"))
    finally:
        common.bot_request, session_report.claude_code = real_request, real_claude_code


async def reminder_checks():
    common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {str(MASHA): {"name": "Маша"}}})
    common.REMINDERS_FILE.unlink(missing_ok=True)
    now = time.time()
    in_topic = {"chat_id": FAMILY["id"], "topic_id": 42, "message_id": 700, "asker_id": MASHA, "asker_name": "Маша"}
    private = {"chat_id": OWNER, "topic_id": None, "message_id": 701, "asker_id": OWNER, "asker_name": "Олег"}
    drone = reminders.add(in_topic, "забрать посылку на почте", minutes_from_now=120, now=now)
    check("a reminder is set for some minutes from now and says when it comes",
          drone.get("set") and drone.get("at") == datetime.fromtimestamp(now + 7200).strftime("%Y-%m-%d %H:%M"), drone)
    tomorrow = datetime.fromtimestamp(now + 86400).strftime("%Y-%m-%d %H:%M")
    milk = reminders.add(private, "купить молоко", at=tomorrow, now=now)
    check("a reminder is set for a local date and time", milk.get("set") and milk.get("at") == tomorrow, milk)
    refusals = [reminders.add(in_topic, "x", minutes_from_now=0, now=now),
                reminders.add(in_topic, "x", at="2020-01-01 10:00", now=now),
                reminders.add(in_topic, "x", at="завтра", now=now),
                reminders.add(in_topic, "x", minutes_from_now=60 * 24 * 500, now=now),
                reminders.add(in_topic, "  ", minutes_from_now=5, now=now),
                reminders.add(None, "x", minutes_from_now=5, now=now)]
    check("a passed or far-off moment, a date that is not one, an empty text and a question from no chat are refused",
          all("error" in item for item in refusals), refusals)

    flowers = reminders.add(in_topic, "полить цветы", minutes_from_now=30, now=now)
    waiting = reminders.listed(in_topic)
    check("list_reminders shows the reminders waiting in this chat, earliest first",
          [item["text"] for item in waiting.get("reminders", [])] == ["полить цветы", "забрать посылку на почте"],
          waiting)
    check("someone else cannot cancel a reminder they did not set",
          "error" in reminders.cancel({**in_topic, "asker_id": STRANGER, "asker_name": "Чужой"}, flowers["id"]))
    cancelled = reminders.cancel({**in_topic, "asker_id": OWNER, "asker_name": "Олег"}, flowers["id"])
    check("the owner can cancel any", cancelled.get("cancelled")
          and all(item["id"] != flowers["id"] for item in reminders.load()), cancelled)
    kept_limit = reminders.PENDING_PER_PERSON
    reminders.PENDING_PER_PERSON = 1
    try:
        over = reminders.add(in_topic, "ещё", minutes_from_now=5, now=now)
        owners = reminders.add(private, "ещё", minutes_from_now=5, now=now)
        check("a person on the list has a limit of reminders waiting, the owner none",
              "error" in over and owners.get("set"), (over, owners))
        reminders.remove(owners.get("id"))
    finally:
        reminders.PENDING_PER_PERSON = kept_limit

    bot = FakeBot()
    reminding = responder.Responder(bot, FakeAnswerer(), FakeScopes(), OWNER)
    await reminding.post_due_reminders(now + 60)
    check("nothing is posted before its time", not bot.sent, bot.sent)
    await reminding.post_due_reminders(now + 7205)
    posted = bot.sent[-1] if bot.sent else SimpleNamespace()
    check("a due reminder goes into its topic as a reply to the question, tagging whoever set it",
          len(bot.sent) == 1 and posted.chat_id == FAMILY["id"] and posted.thread == 42 and posted.reply_to == 700
          and posted.parse_mode == "HTML"
          and posted.text == '⏰ <a href="tg://user?id=555">Маша</a>, забрать посылку на почте', vars(posted))
    check("a posted reminder is gone", all(item["id"] != drone["id"] for item in reminders.load()), reminders.load())
    await reminding.post_due_reminders(now + 86400 + 3600)
    posted = bot.sent[-1]
    check("a reminder in a one-on-one chat tags nobody, and one posted late says so",
          len(bot.sent) == 2 and posted.chat_id == OWNER and posted.reply_to == 701 and posted.thread is None
          and posted.text.startswith("⏰ купить молоко") and "с опозданием на 1 ч" in posted.text, vars(posted))

    class RefusingBot(FakeBot):
        def __init__(self, code):
            super().__init__()
            self.code = code

        async def send(self, chat_id, text, reply_to=None, parse_mode=None, thread=None):
            raise common.BotApiError("sendMessage", self.code, "Forbidden: bot was kicked from the supergroup chat"
                                     if self.code else "cannot reach api.telegram.org")

    stuck = reminders.add(in_topic, "проверить почту", minutes_from_now=1, now=now)
    await responder.Responder(RefusingBot(None), FakeAnswerer(), FakeScopes(), OWNER).post_due_reminders(now + 120)
    check("a reminder that could not be posted for want of a network waits for the next look",
          any(item["id"] == stuck["id"] for item in reminders.load()), reminders.load())
    await responder.Responder(RefusingBot(403), FakeAnswerer(), FakeScopes(), OWNER).post_due_reminders(now + 120)
    check("one Telegram refuses - the bot was removed from the chat - is dropped",
          all(item["id"] != stuck["id"] for item in reminders.load()), reminders.load())


async def picture_checks():
    jpeg, png, gif = b"\xff\xd8\xff\xe0" + b"0" * 100, b"\x89PNG\r\n\x1a\n" + b"0" * 100, b"GIF89a" + b"0" * 100
    kinds = [common.image_kind(item) for item in (jpeg, png, b"RIFF1234WEBPVP8 ", gif, b"\x00\x00\x00\x18ftypmp42",
                                                  b"<!doctype html><html>")]
    check("a downloaded file is told apart by its first bytes, and a web page is not a picture",
          kinds == [("photo", "image/jpeg"), ("photo", "image/png"), ("photo", "image/webp"), ("animation", "image/gif"),
                    ("animation", "video/mp4"), None], kinds)

    hosts = {"example.com": "93.184.216.34", "router.lan": "192.168.1.1", "localhost": "127.0.0.1"}
    real_getaddrinfo = common.socket.getaddrinfo
    common.socket.getaddrinfo = lambda host, port, *rest: [(2, 1, 6, "", (hosts[host], port))]
    try:
        check("only public http and https addresses may be fetched",
              common.public_url("https://example.com/cat.gif") is None and common.public_url("http://router.lan/a.jpg")
              and common.public_url("http://localhost:8080/a.png") and common.public_url("ftp://example.com/a.gif"))
    finally:
        common.socket.getaddrinfo = real_getaddrinfo

    body, form_type = common.multipart({"chat_id": -100777, "caption": "кот", "reply_parameters": {"message_id": 700},
                                        "message_thread_id": None}, "animation", "picture.gif", gif, "image/gif")
    boundary = form_type.split("boundary=", 1)[1]
    check("a file goes to the Bot API as multipart form data, its fields as text or JSON",
          b'name="chat_id"\r\n\r\n-100777\r\n' in body and 'name="caption"\r\n\r\nкот'.encode() in body
          and b'name="reply_parameters"\r\n\r\n{"message_id": 700}' in body and b"message_thread_id" not in body
          and b'filename="picture.gif"\r\nContent-Type: image/gif\r\n\r\n' + gif in body
          and body.endswith(f"--{boundary}--\r\n".encode()), body[:400])

    uploads = []

    def fake_upload(token, method, params, field, filename, data, content_type, timeout=120):
        uploads.append((method, params, field, filename, content_type))
        return {"message_id": 950 + len(uploads), "date": int(time.time()),
                "chat": {"id": params["chat_id"], "type": "supergroup", "title": "Семья"},
                "from": {"id": 1, "is_bot": True, "first_name": "Робин"}}

    files = {"https://example.com/cat.gif": (gif, "animation", "image/gif"),
             "https://example.com/cat.jpg": (jpeg, "photo", "image/jpeg")}

    def fake_download(url, limit=None, timeout=30):
        if url not in files:
            raise ValueError("that address is not an image or an animation")
        return files[url]

    real_download, real_upload, real_file = common.download_image, common.bot_upload, common.bot_file
    common.download_image, common.bot_upload = fake_download, fake_upload
    common.bot_file = lambda token, file_id, limit=None, timeout=60: jpeg if file_id == "AgAD-photo" else png
    try:
        common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {str(MASHA): {"name": "Маша"}}})
        pictures = Pictures("123:fake")
        owner_origin = {"chat_id": FAMILY["id"], "topic_id": 42, "message_id": 700, "asker_id": OWNER,
                        "asker_name": "Олег"}
        owner_here = Scope(everything=True, private=False, current=FAMILY["id"], origin=owner_origin)
        posted = await pictures.send(owner_here, "https://example.com/cat.gif", "Вот кот")
        sent = uploads[-1] if uploads else (None, {}, None, None, None)
        check("a GIF the owner asks for is downloaded and posted as an animation into the topic, as a reply",
              posted.get("posted") and sent[0] == "sendAnimation" and sent[2] == "animation"
              and sent[1]["chat_id"] == FAMILY["id"] and sent[1]["message_thread_id"] == 42
              and sent[1]["reply_parameters"]["message_id"] == 700 and sent[1]["caption"] == "Вот кот", (posted, sent))
        await pictures.send(owner_here, "https://example.com/cat.jpg")
        check("a picture goes as a photo", uploads[-1][0] == "sendPhoto" and uploads[-1][2] == "photo", uploads[-1])
        masha_here = Scope(everything=False, chats={FAMILY["id"]: "Семья"}, private=False, current=FAMILY["id"],
                           origin={**owner_origin, "asker_id": MASHA, "asker_name": "Маша"})
        refused_post = await pictures.send(masha_here, "https://example.com/cat.gif")
        check("nobody but the owner has pictures posted", "error" in refused_post and len(uploads) == 2, refused_post)
        page = await pictures.send(owner_here, "https://example.com/page.html")
        check("an address that is not a picture is refused with the reason", "not an image" in page.get("error", ""),
              page)
        check("a question from no chat posts nothing",
              "error" in await pictures.send(Scope(everything=True), "https://example.com/cat.gif"))

        private_scope = Scope(everything=True, origin={"chat_id": OWNER, "topic_id": None, "message_id": 701,
                                                       "asker_id": OWNER, "asker_name": "Олег",
                                                       "photos": {"701": "AgAD-photo"}})
        seen = await Pictures("123:fake").view(private_scope, 701)
        check("a photo that came with the question is fetched through the Bot API, without the personal account",
              seen.get("data") == jpeg and seen.get("media_type") == "image/jpeg", seen)
        check("any other photo needs the personal account", "error" in await Pictures("123:fake").view(private_scope, 5))
    finally:
        common.download_image, common.bot_upload, common.bot_file = real_download, real_upload, real_file

    search = TelegramSearch(FakeTelethon(), OWNER, "Олег")
    group = Scope(chats={-777: "Семья"}, private=False, current=-777)
    from_history = await Pictures("123:fake", search).view(group, 31)
    check("a photo from the chat's history is fetched through the owner's account, with who posted it",
          from_history.get("data", b"").startswith(b"\xff\xd8") and from_history.get("from") == "Маша",
          {name: value for name, value in from_history.items() if name != "data"})
    check("a message without a photo and a chat outside the scope are refused",
          "error" in await search.photo(group, 32) and "error" in await search.photo(group, 31, chat_id=-100999))

    class PhotoTelethon(FakeTelethon):
        def iter_messages(self, entity, search=None, limit=None, reply_to=None, offset_id=0, offset_date=None):
            posted = FakeMessage(entity, "", datetime.now(timezone.utc) - timedelta(hours=1), 40)
            posted.media = types.MessageMediaPhoto()

            async def found():
                yield posted

            return found()

    marked = await TelegramSearch(PhotoTelethon(), OWNER, "Олег").history(group)
    check("history marks a message with a photo, so Claude knows it can look at it",
          marked["messages"][0]["text"] == "[photo]" and marked["messages"][0].get("media") == "photo", marked)

    class RecordingPictures:
        def __init__(self):
            self.calls = []

        async def view(self, scope, message_id, chat_id=None):
            self.calls.append(("view", message_id, chat_id))
            return {"data": jpeg, "media_type": "image/jpeg", "message_id": message_id, "from": "Маша"}

        async def send(self, scope, url, caption=None):
            self.calls.append(("send", url, caption))
            return {"posted": True, "message_id": 950}

    looks = FakeMessages([
        reply("tool_use", block(type="tool_use", id="p1", name="view_photo", input={"message_id": 31}),
              block(type="tool_use", id="p2", name="send_image", input={"url": "https://example.com/cat.gif"})),
        reply("end_turn", block(type="text", text="На фото кот."))])
    recording = RecordingPictures()
    owner_origin = {"chat_id": FAMILY["id"], "topic_id": None, "message_id": 700, "asker_id": OWNER,
                    "asker_name": "Олег"}
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=looks)), RecordingSearch(), "system", None,
                             recording).answer("вопрос", Scope(everything=True, private=False, current=FAMILY["id"],
                                                               origin=owner_origin), dict(common.DEFAULT_SETTINGS))
    names = [tool["name"] for tool in looks.calls[0]["tools"]]
    results = looks.calls[1]["messages"][-1]["content"] if len(looks.calls) > 1 else []
    check("the owner's question gets view_photo and send_image, and a photo comes back as an image to look at",
          "view_photo" in names and names[-1] == "send_image" and results
          and results[0]["content"][0]["type"] == "image" and results[0]["content"][0]["source"]["media_type"] == "image/jpeg"
          and json.loads(results[0]["content"][1]["text"]) == {"media_type": "image/jpeg", "message_id": 31, "from": "Маша"}
          and recording.calls == [("view", 31, None), ("send", "https://example.com/cat.gif", "")],
          (names, recording.calls))
    masha_asks = FakeMessages([reply("end_turn", block(type="text", text="ok"))])
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=masha_asks)), RecordingSearch(), "system",
                             None, recording).answer(
        "вопрос", Scope(chats={FAMILY["id"]: "Семья"}, private=False, current=FAMILY["id"],
                        origin={**owner_origin, "asker_id": MASHA, "asker_name": "Маша"}), dict(common.DEFAULT_SETTINGS))
    names = [tool["name"] for tool in masha_asks.calls[0]["tools"]]
    check("someone else may have photos looked at, but not pictures posted",
          "view_photo" in names and "send_image" not in names, names)


async def file_checks():
    common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {str(MASHA): {"name": "Маша"}}})
    disk, outside = (TEMP / "disk").resolve(), (TEMP / "outside").resolve()
    pdf = b"%PDF-1.4 resume"
    now = time.time()
    layout = {"Резюме Олег.pdf": (pdf, now), "big резюме.zip": (b"0" * 64, now - 3600),
              "empty резюме.txt": (b"", now - 7200), "old/резюме-старое.pdf": (pdf, now - 7 * 86400),
              "old/deeper/notes.txt": (b"notes", now), ".hidden/резюме.pdf": (pdf, now), "AppData/резюме.pdf": (pdf, now),
              "keys/резюме.pem": (b"key", now)}
    for name, (data, modified) in layout.items():
        path = disk / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (modified, modified))
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "резюме.pdf").write_bytes(pdf)
    files = localfiles.LocalFiles("123:fake", where=lambda: [disk])
    owner_origin = {"chat_id": FAMILY["id"], "topic_id": 42, "message_id": 700, "asker_id": OWNER, "asker_name": "Олег"}
    owner_here = Scope(everything=True, private=False, current=FAMILY["id"], origin=owner_origin)
    masha_here = Scope(chats={FAMILY["id"]: "Семья"}, private=False, current=FAMILY["id"],
                       origin={**owner_origin, "asker_id": MASHA, "asker_name": "Маша"})

    def names_of(found):
        return [Path(item["path"]).relative_to(disk).as_posix() for item in found.get("files", [])]

    found = await files.find(owner_here, "РЕЗЮМЕ")
    check("the owner finds files by a word of the name in any case, newest first - none in a hidden folder, AppData or "
          "a key file", names_of(found) == ["Резюме Олег.pdf", "big резюме.zip", "empty резюме.txt",
                                            "old/резюме-старое.pdf"] and found["looked_in"] == [str(disk)], found)
    check("every word must be in the name, and a word may be the extension",
          names_of(await files.find(owner_here, "резюме pdf")) == ["Резюме Олег.pdf", "old/резюме-старое.pdf"])
    check("a word may list alternatives with |, one search for all of them",
          names_of(await files.find(owner_here, "cv|РЕЗЮМЕ pdf|txt")) == ["Резюме Олег.pdf", "empty резюме.txt",
                                                                          "old/резюме-старое.pdf"]
          and localfiles.terms("резюме|*cv* pdf|") == [("резюме", "cv"), ("pdf",)])
    in_old = await files.find(owner_here, "резюме", str(disk / "old"))
    check("a folder narrows the search to itself and the folders in it", names_of(in_old) == ["old/резюме-старое.pdf"],
          in_old)
    check("a folder outside the places or a hidden one is refused",
          "outside" in (await files.find(owner_here, "резюме", str(outside))).get("error", "")
          and "error" in await files.find(owner_here, "резюме", str(disk / ".hidden")))
    check("a search out of time says so", localfiles.walk([str(disk)], [("резюме",)], -1) == ([], "time"))
    check("nothing in the bot's own folder is ever sent - its logins are there",
          localfiles.closed(common.ROOT / "data" / "me.json", common.ROOT.parent) is not None)
    places = localfiles.places()
    check("by default it looks in the owner's folders and the local drives but the system one, each once",
          places and all(place.is_dir() for place in places) and len(set(places)) == len(places)
          and Path(os.environ.get("SystemDrive", "C:") + os.sep) not in places, places)

    body, _ = common.multipart({}, "document", 'Резюме "Олег"\r\n.pdf', b"x", "application/pdf")
    check("a file's own name goes into the form as UTF-8, with quotes and line breaks escaped",
          'filename="Резюме %22Олег%22%0D%0A.pdf"'.encode() in body, body[:300])

    uploads = []

    def fake_upload(token, method, params, field, filename, data, content_type, timeout=120):
        uploads.append((method, params, field, filename, data, content_type))
        return {"message_id": 960 + len(uploads), "date": int(time.time()),
                "chat": {"id": params["chat_id"], "type": "supergroup", "title": "Семья"},
                "from": {"id": 1, "is_bot": True, "first_name": "Робин"}, "document": {"file_name": filename}}

    real_upload, real_bytes = common.bot_upload, localfiles.FILE_BYTES
    common.bot_upload = fake_upload
    try:
        posted = await files.send(owner_here, str(disk / "Резюме Олег.pdf"), "Моё резюме")
        sent = uploads[-1] if uploads else (None, {}, None, None, None, None)
        check("a file the owner asks for is posted as a document under its own name into the topic, as a reply",
              posted.get("posted") and sent[0] == "sendDocument" and sent[2] == "document"
              and sent[3] == "Резюме Олег.pdf" and sent[4] == pdf and sent[5] == "application/pdf"
              and sent[1]["chat_id"] == FAMILY["id"] and sent[1]["message_thread_id"] == 42
              and sent[1]["reply_parameters"]["message_id"] == 700 and sent[1]["caption"] == "Моё резюме",
              (posted, sent[:4]))
        logged = common.FILES_LOG.read_text(encoding="utf-8") if common.FILES_LOG.exists() else ""
        check("every file posted is written down with where it went",
              str(disk / "Резюме Олег.pdf") in logged and f"chat {FAMILY['id']} topic 42" in logged, logged)
        refusals = {}
        for name, path in (("hidden", disk / ".hidden" / "резюме.pdf"), ("appdata", disk / "AppData" / "резюме.pdf"),
                           ("key", disk / "keys" / "резюме.pem"), ("outside", outside / "резюме.pdf"),
                           ("missing", disk / "нет.pdf"), ("folder", disk / "old"),
                           ("empty", disk / "empty резюме.txt"), ("bare name", "Резюме Олег.pdf")):
            refusals[name] = await files.send(owner_here, str(path))
        check("a hidden folder, AppData, a key, a file outside the places, a missing file, a folder, an empty file and a "
              "bare name are refused, and nothing is posted",
              all("error" in result for result in refusals.values()) and len(uploads) == 1, refusals)
        localfiles.FILE_BYTES = 10
        too_big = await files.send(owner_here, str(disk / "big резюме.zip"))
        check("a file larger than a bot may send is refused", "at most" in too_big.get("error", "") and len(uploads) == 1,
              too_big)
        localfiles.FILE_BYTES = real_bytes
        check("nobody but the owner has files found or posted, and a question from no chat has none",
              "error" in await files.send(masha_here, str(disk / "Резюме Олег.pdf"))
              and "error" in await files.find(masha_here, "резюме")
              and "error" in await files.find(Scope(everything=True), "резюме") and len(uploads) == 1)
    finally:
        common.bot_upload, localfiles.FILE_BYTES = real_upload, real_bytes

    class RecordingFiles:
        def __init__(self):
            self.calls = []

        async def find(self, scope, query, folder=None):
            self.calls.append(("find", query, folder))
            return {"files": [{"path": "G:/docs/Резюме.pdf", "size": 15, "modified": "2026-09-14 10:00"}]}

        async def send(self, scope, path, caption=None):
            self.calls.append(("send", path, caption))
            return {"posted": True, "message_id": 970}

    asks = FakeMessages([
        reply("tool_use", block(type="tool_use", id="f1", name="find_files", input={"query": "резюме"}),
              block(type="tool_use", id="f2", name="send_file", input={"path": "G:/docs/Резюме.pdf"})),
        reply("end_turn", block(type="text", text="Скинул резюме."))])
    recording = RecordingFiles()
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=asks)), RecordingSearch(), "system", None,
                             None, recording).answer("скинь резюме", owner_here, dict(common.DEFAULT_SETTINGS))
    names = [tool["name"] for tool in asks.calls[0]["tools"]]
    check("the owner's question gets find_files and send_file, and Claude's calls reach them",
          names[-2:] == ["find_files", "send_file"]
          and recording.calls == [("find", "резюме", None), ("send", "G:/docs/Резюме.pdf", "")], (names, recording.calls))
    masha_asks = FakeMessages([reply("end_turn", block(type="text", text="ok"))])
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=masha_asks)), RecordingSearch(), "system",
                             None, None, recording).answer("скинь резюме", masha_here, dict(common.DEFAULT_SETTINGS))
    names = [tool["name"] for tool in masha_asks.calls[0]["tools"]]
    check("someone else's question gets neither", "find_files" not in names and "send_file" not in names, names)


class VoiceScopes(FakeScopes):
    """FakeScopes that also transcribe voice messages, from a table of what each one says."""

    def __init__(self, said):
        self.said = said
        self.heard = []

    async def transcribe(self, chat, message_id):
        self.heard.append((chat, message_id))
        return (self.said[message_id], None) if message_id in self.said else (None, "Telegram heard no words in it")

    async def transcribe_own(self, bot_username, date):
        self.heard.append(("own", bot_username))
        return self.said.get("own"), None


async def voice_checks():
    chatsearch.TRANSCRIBE_POLL_SECONDS = 0
    telethon = FakeTelethon()
    search = TelegramSearch(telethon, OWNER, "Олег")
    group = Scope(chats={-777: "Семья"}, private=False, current=-777)
    heard = await search.voice(group, 33)
    check("a voice message in the chat is read as Telegram transcribes it for the owner's account, once it is done",
          heard.get("text") == "Робин, купи хлеба" and heard.get("from") == "Маша" and telethon.transcriptions == 2,
          heard)
    check("a message that is not a voice message, and a chat outside the scope, are refused",
          "error" in await search.voice(group, 32) and "error" in await search.voice(group, 33, chat_id=-100999))

    class BotChatTelethon(FakeTelethon):
        async def get_input_entity(self, target):
            if target == "@family_bot" or isinstance(target, types.InputPeerUser):
                return types.InputPeerUser(1, 7)
            return await super().get_input_entity(target)

        def iter_messages(self, entity, search=None, limit=None, reply_to=None, offset_id=0, offset_date=None):
            own = FakeMessage(entity, "", datetime.fromtimestamp(1789240000, timezone.utc), 88)
            own.out, own.media, own.voice = True, types.MessageMediaDocument(), SimpleNamespace()

            async def found():
                yield own

            return found()

    own_text, why = await TelegramSearch(BotChatTelethon(), OWNER, "Олег").transcribe_own("family_bot", 1789240000)
    check("the owner's own voice message to the bot is found in the owner's chat with the bot by its date",
          own_text == "Робин, купи хлеба", why)

    listens = FakeMessages([
        reply("tool_use", block(type="tool_use", id="v1", name="read_voice", input={"message_id": 33})),
        reply("end_turn", block(type="text", text="Просит купить хлеба."))])
    recording = RecordingSearch()
    await responder.Answerer(SimpleNamespace(beta=SimpleNamespace(messages=listens)), recording, "system").answer(
        "вопрос", Scope(chats={-777: "Семья"}, private=False, current=-777), dict(common.DEFAULT_SETTINGS))
    check("read_voice comes with the chat tools and reads the voice message asked about",
          "read_voice" in [tool["name"] for tool in listens.calls[0]["tools"]] and recording.calls == [("voice", 33, None)],
          recording.calls)

    common.save_json(common.ACCESS_FILE, {"owner_id": OWNER, "allowed": {str(MASHA): {"name": "Маша"}}})
    common.USAGE_FILE.unlink(missing_ok=True)
    bot, api, subscription = FakeBot(), FakeAnswerer(), FakeAnswerer("Ответ через подписку")
    oleg, masha, stranger = user(OWNER, "Олег"), user(MASHA, "Маша", "masha"), user(STRANGER, "Чужой")

    def voiced(sender, chat=None, reply_to=None):
        item = message(sender, "", chat, reply_to=reply_to)
        item.pop("text")
        item["voice"] = {"file_id": "voice", "duration": 3}
        return item

    naming, chatting, strangers = voiced(masha, FAMILY), voiced(masha, FAMILY), voiced(stranger, FAMILY)
    scopes = VoiceScopes({naming["message_id"]: "Робин, что купить к ужину?",
                          chatting["message_id"]: "я задержусь на полчаса",
                          strangers["message_id"]: "Робин, привет", "own": "напомни позвонить маме"})
    hearing = responder.Responder(bot, api, scopes, OWNER, subscription)

    async def send(item):
        await hearing.handle({"update_id": item["message_id"], "message": item})
        await asyncio.gather(*list(hearing.tasks))

    await send(naming)
    check("a voice message from someone on the list that names the bot is heard and answered as a question",
          len(api.questions) == 1 and "Question: Робин, что купить к ужину?" in api.questions[-1][0]
          and "was a voice message" in api.questions[-1][0], api.questions)
    await send(chatting)
    check("one that does not name the bot is left alone", len(api.questions) == 1)
    await send(strangers)
    check("a voice message from someone not on the list is not even transcribed",
          (FAMILY["id"], strangers["message_id"]) not in scopes.heard and len(api.questions) == 1, scopes.heard)
    await send(voiced(oleg))
    check("the owner's voice message in the private chat is found through the owner's account and answered",
          len(subscription.questions) == 1 and "Question: напомни позвонить маме" in subscription.questions[-1][0]
          and scopes.heard[-1] == ("own", "family_bot"), scopes.heard)
    sent = len(bot.sent)
    await send(voiced(masha))
    check("someone else's voice message in a private chat is asked to be written instead, with no Claude",
          len(bot.sent) == sent + 1 and "голосовые" in bot.sent[-1].text.lower() and len(api.questions) == 1,
          bot.sent[-1].text if bot.sent else None)
    await send(message(masha, "Робин, что в голосовом?", FAMILY, reply_to=naming))
    check("a question replying to a voice message comes with that message's transcription",
          len(api.questions) == 2 and "Telegram's transcription of it" in api.questions[-1][0]
          and "что купить к ужину" in api.questions[-1][0], api.questions[-1][0])


async def main():
    await responder_checks()
    await answerer_checks()
    await search_checks()
    await writer_checks()
    await subscription_checks()
    await tool_checks()
    await inline_checks()
    await reminder_checks()
    await picture_checks()
    await file_checks()
    await voice_checks()
    await report_checks()
    await sdk_checks()


asyncio.run(main())
print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
