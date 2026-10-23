# telegram-mcp

An MCP server that lets Claude act in Telegram as you - your own account, through Telethon - or as your bot,
through the Bot API: find a chat, read it, send a message, edit a message. `responder.py` makes the bot answer
the people on its list by itself, with Claude.

## Logging in

Run these yourself in a terminal; they ask for codes and secrets.

    .venv\Scripts\python.exe login.py me          # api_id and api_hash from my.telegram.org, then phone, code, password
    .venv\Scripts\python.exe login.py bot         # the token @BotFather gives for /newbot
    .venv\Scripts\python.exe login.py anthropic   # a Claude API key, for the bot's answers to people on its list
    .venv\Scripts\python.exe login.py claude      # optional: a Claude Code token of the bot's own, see below
    .venv\Scripts\python.exe login.py status
    .venv\Scripts\python.exe login.py logout me

`data\me.json` is the whole account, as good as a logged-in phone: never share it or put it in a repository.
The session is listed in Telegram under Settings > Devices as "Claude telegram-mcp" and can be ended there;
`login.py logout me` ends it from here. `data\bot.json` holds the bot token, `data\anthropic.json` the Claude API
key (billed by use, separately from a Claude subscription), `data\claude_token.json` the optional Claude Code token.

## Tools

| tool | account='me' | account='bot' |
|---|---|---|
| `find_chats` | your chats and contacts, by name | chats that wrote to the bot or added it |
| `read_messages` | the chat's history | messages meant for the bot that reached it in the last 24 hours, and what it sent |
| `send_message` | any chat you can write in | people who pressed Start in the bot, groups and channels it is in |
| `edit_message` | your own messages; 48 hours after sending in ordinary chats | the bot's own messages |
| `whoami` | who is logged in | which bot, and whether privacy mode is on |

| tool | what it does |
|---|---|
| `bot_access` | whom the bot answers, their daily limits and today's usage, the settings, whether responder.py runs |
| `bot_allow` | puts a person (user id or @username) on the list, or changes their daily limit |
| `bot_deny` | takes a person off the list |

Every chat tool but `find_chats` takes a chat id from `find_chats`, an `@username` or `me`, never a bare name.
Before `send_message` and `edit_message` Claude shows the chat, the account and the exact text and waits for a
yes; `bot_allow` and `bot_deny` change the list only when you ask.

A bot has no history to read, so what it saw and sent is kept in `data\bot_state.json` - only messages meant for
it: private messages, mentions, replies to it and commands. Without responder.py the server reads the bot's
updates without confirming them, so a dedicated bot works best.

## Groups and privacy mode

In privacy mode, which every bot starts in, Telegram sends a bot in a group only commands, replies to its own
messages and service messages - **not mentions**. For the bot to answer "@botname ..." in a group, either

- turn privacy mode off: @BotFather, `/setprivacy`, pick the bot, Disable - and then remove the bot from the
  group and add it again, since the change applies only to groups it joins afterwards; or
- make the bot an admin of the group (with no rights at all); admins get every message.

Either way Telegram then sends the bot every message of the group. The bot looks in each for its name, a mention of
it or a reply to it, and drops every other one without keeping it.

## The bot answering by itself

    .venv\Scripts\python.exe responder.py

While it runs, the bot answers when someone on its list writes to it privately, calls it by its name anywhere in a
group message, in any Russian case, mentions it or replies to its message. The name is found in the text by
the bot itself, without Claude, so looking through the chat costs nothing. Everyone else is ignored before Claude is called, so only the list spends anything. Claude can

- search the web;
- `search_chats`: find messages by a word in the chats your account shares with the person asking - all of your
  chats when you ask;
- `read_chat_history`: read the last days of the group the question came from - or one of its topics, or another
  chat in that scope - for questions about the chat itself. Your own questions on the subscription have no date
  limit: any period (`days=0`, `before_date`), up to 1000 messages a call, paging back with `before_id`; and
  `search_chats` finds older matches with `before_date`;
- `post_message` and `edit_bot_message`: write as the bot into the group the question came from and nowhere else,
  its topics included - adding to a shopping list kept in a topic, say. A message someone else wrote cannot be
  edited, so the bot posts the whole updated list; a list it posted itself it edits;
- tag people: in a group it gets the group's people from your account and tags whoever a message is for - by
  @username, or by a link to their id when they have none;
- `set_reminder`, `list_reminders` and `cancel_reminder`: reminders it posts later into the chat of the question (see
  Reminders below);
- hear voice and round video messages through Telegram Premium on your account (`TranscribeAudio`): one from you or
  someone on the list in a group is transcribed and answered when it names the bot or replies to it; your own in the
  private chat with the bot too, found in that chat by its date. Nobody else's is transcribed, and the bot asks people
  on the list to write instead of sending it voice messages privately - your account cannot see those chats. A
  question replying to a voice message comes with its transcription, and `read_voice` reads one from a chat's history;
- `view_photo`: look at a photo already in Telegram - one the question comes with or replies to (fetched through the
  Bot API), or one from a chat in the question's scope (through your account; history marks messages with photos);
- `send_image`, for your requests only: post a GIF or a picture from the web into the chat of the question. The bot
  downloads the file itself - only from a public address, never a host on this computer or its network, and only a
  JPEG, PNG, WebP, GIF or MP4 - and uploads it with sendPhoto or sendAnimation. Photos looked at and pictures posted
  stay in memory and are never written to disk;
- `find_files` and `send_file`, for your requests only: find a file on this computer by words of its name and post it
  into the chat of the question as a document, up to 50 MB. It looks through your Desktop, Documents, Downloads,
  Pictures, Videos, Music and OneDrive folders and every local drive but the system one, shallow folders first, for
  at most 20 seconds a search. Whatever may keep logins or keys is never listed or sent: a folder or file whose name
  starts with a dot or `$`, `AppData`, Telegram Desktop's `tdata`, this project's own folder, key and session files
  (`.pem`, `.key`, `.pfx`, `.kdbx`, `.session`, `id_rsa`...). Every file sent is written down in `data\files_sent.log`;
- `search_engine_docs` and `read_engine_doc`: answer a question about the Doppel engine from its documentation in
  `G:\doppel\docs` (`enginedocs.DOPPEL`). Everyone on the list reads the manual, `docs\manual` in English and Russian;
  you also read the rest of `docs\` - the studies, plans and `SETUP.md`. A search returns the Markdown sections that
  hold every word given, the manual's first; a section is read by its heading;
- `query_engine_graph`, for your questions only: ask the graphify graph of the engine's code
  (`G:\doppel\graphify-out\graph.json`, through `graphify query` with the interpreter `graphify-out\.graphify_python`
  names) which classes, functions and files a question touches. It is only as fresh as the last `/graphify --update`.

Asked in a group, one-on-one chats stay out of reach, since the whole group reads the answer.

### Two routes

- **People on the list** are answered through the Claude API, billed to the API key: `settings.model`
  (`claude-sonnet-5`) at `settings.effort`, at most `web_searches_per_question` (1) web searches a question. Every
  step of an answer sends everything before it again, so the request caches: a breakpoint after the system prompt
  and the automatic one on the growing conversation, which each step reads at a tenth of the price. The log shows
  what came from the cache and what went into it.
- **You** are answered by Claude Code on your own Claude subscription (`owner_route: "subscription"`) with
  `owner_model` (`claude-opus-5-5`, Opus 5.5, which needs Claude Code 2.1.280 or newer) at `owner_effort`. It runs
  `claude -p` - the newest of the one on PATH and the Claude desktop app's copies, so a copy on PATH that stopped
  updating does not hold it back. The
  app is an MSIX package, so that copy is in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code`;
  only the app and what it starts see it as `%APPDATA%\Claude\claude-code`, and the autostart task does not. It
  runs in an empty folder under `data\claude-work`, with `--tools WebSearch,WebFetch`, `--strict-mcp-config` and
  only `search_mcp.py` - `search_chats`, `read_chat_history` and, for a question from a group, `post_message` and
  `edit_bot_message`, all kept to the question's scope - with no permission prompts, no saved session and every hook off. Its working directory, the temp folder it is given
inside it and the MCP logs Claude Code keeps in its cache for that directory are deleted after every answer. None of this process's `CLAUDE*`/`ANTHROPIC*` variables reach it, and `MAX_MCP_OUTPUT_TOKENS` is raised so that a long history reaches Claude whole. It uses this computer's Claude Code
  login, which brings your global CLAUDE.md along (the bot is told to ignore instructions about coding); after
  `login.py claude` it uses that token and a config folder of its own (`data\claude-config`) instead. When the
  subscription answer fails - a usage limit, say - the bot tells you why and spends nothing on the API.
  `owner_route: "api"` sends your questions through the API like everyone else's.

A subscription is for its owner alone, so nobody else's question ever goes through Claude Code.

### Settings and commands

- `data\access.json` holds `owner_id`, `allowed` (user id -> name, `added`, an optional `daily_limit`) and
  `settings`: `model`, `effort`, `daily_limit` (10 questions a day per person; you have none),
  `web_searches_per_question`, `owner_route`, `owner_model`, `owner_effort`, `owner_tiers` - the model a `#simple`, `#medium` or `#hard` in
  your own question picks (`sonnet`, `claude-opus-5-5`, `fable`; anyone else's hashtag is only text) - and `report_min_minutes` and
  `report_model` for the session reports below. The responder reads it for every
  question, so a change needs no restart.
- Commands, obeyed only from you: `/allow` as a reply to someone's message (or `/allow <user id>`,
  `/allow @username`), `/allow all` in a group for everyone in it (found through your account; people already on the
  list keep their limits), `/deny` the same way, `/limit N` as a reply (or `/limit @username N`) for that person's
  questions a day, `/allowed` for the list, the limits and today's usage. In a group Telegram delivers a bare
  command to a bot in privacy mode only if it spoke last - `/allow@botname` always arrives.
- `data\usage.json` counts per person per day: questions, tokens, web searches, the estimated API cost at list
  prices (searches not included), and your subscription questions.
- Added to a group by you or someone on the list, the bot posts `greeting.txt` there - your own, kept out of the repository, or `greeting.example.txt` without one - (`{bot}` becomes its
  @username); added by anyone else, it stays quiet.
- It rewrites `data\responder.json` after every poll; while that file is fresh and its process exists, the MCP
  server leaves the bot's updates to the responder. `data\bot_offset.json` records where it stopped reading, saved
  before each update is handled: a responder stopped halfway skips that update when it starts again, rather than
  greeting or answering twice.
- It answers only while it runs on this computer. In a terminal Ctrl+C stops it; the task below runs it without a
  window.

### Reminders

"<name>, напомни через 2 часа позвонить в банк" - Claude sets a reminder with `set_reminder`, and when it is due the bot
posts it into the chat and topic the question came from, as a reply to the question, tagging whoever set it or whom
the text tags. `list_reminders` and `cancel_reminder` show and cancel the reminders waiting in that chat; anyone but
you cancels only their own and has up to 20 waiting. They are kept in `data\reminders.json` and outlast a restart: one
that fell due while the responder was stopped is posted when it starts, marked as late. The responder looks for due
reminders every 15 seconds, and posting one does not go through Claude.

### Inline mode

In any one-on-one chat you can type `@bot` and a question: the bot offers one result, and picking it posts
"⏳ ... думает…" as your message (via the bot), which the answer then replaces - on the subscription, reading chats
with no date limit, cut to one message. Nobody else gets inline results, and neither do groups, where you mention the
bot instead. The other person reads the answer, so the bot is told to bring in nothing private from other chats that
the question does not ask for.

With inline mode on, Telegram apps take a message that starts with `@bot` for an inline query and do not send it as a
message - in a group too, where the bot offers no results - so there people call the bot by its name instead.

It needs two switches in @BotFather: `/setinline` (Inline Mode on, with a placeholder such as "вопрос") and
`/setinlinefeedback` (Enabled), which is how the bot learns that a result was picked. Without the feedback the
thinking message waits until you press its ⏳ button.

## Session reports

`session_report.py` has the bot send you a short report when a Claude Code session of yours - in the desktop app, the
CLI or an IDE - finishes a piece of work. It is a Stop hook in `~/.claude/settings.json`:

    "hooks": {"Stop": [{"hooks": [{"type": "command", "async": true, "timeout": 180,
        "command": "C:\\path\\to\\telegram-mcp\\.venv\\Scripts\\pythonw.exe",
        "args": ["C:\\path\\to\\telegram-mcp\\session_report.py"]}]}]}

It writes when the session worked at least `settings.report_min_minutes` (3; `null` turns reports off) since the later
of your last typed prompt and its last report there - not while a background task of the session still runs, since
the session goes on by itself once that ends, and never for the bot's own Claude Code. The report is the session's
title and folder, how long it worked, how many tools it called and files it changed, the prompt, and Claude's last
message: as it is when short, otherwise shortened to a few lines by Claude Code on your subscription with
`settings.report_model` (`haiku`; `null` cuts it instead), run with no tools and every hook off. Why it did or did not
write is in `data\session_report.log`.

    .venv\Scripts\python.exe session_report.py --dry-run --force < hook.json   # print a report for a saved hook input

## Additions of your own

`local_hooks.py`, when it is there, adds to the bot without changing the code: `SYSTEM_EXTRA` goes at the end of
Claude's instructions ({bot_name} filled in) and `reply_note(chat_id, message_id)` gives Claude a sentence about the
message a question replies to - one another script of yours posted through the bot, say. Copy
`local_hooks.example.py` to start; the file stays out of the repository.

## Autostart

The Task Scheduler task `telegram-mcp responder` starts it at logon, without a window, as
`.venv\Scripts\pythonw.exe responder.py --log data\responder.log`: no time limit, started again every minute if it
fails, never two at once. responder.py itself starts again 30 seconds after an error - no network yet at logon, a
connection lost for good - and stops only on a setup problem, which it writes to the log.

    Start-ScheduledTask -TaskName 'telegram-mcp responder'       # start it now
    Stop-ScheduledTask -TaskName 'telegram-mcp responder'        # stop it
    Disable-ScheduledTask -TaskName 'telegram-mcp responder'     # no more autostart
    Unregister-ScheduledTask -TaskName 'telegram-mcp responder'  # remove the task

`data\responder.log` is its log, kept as `.1` past 5 MB. `login.py status` and `bot_access` say whether it runs:
a heartbeat under a minute and a half old, from a process that still exists.

## Registered as

    claude mcp add --scope user telegram -- C:\path\to\telegram-mcp\.venv\Scripts\python.exe C:\path\to\telegram-mcp\server.py

## Checking it

`test_offline.py` runs the chat tools and login.py against a fake Bot API and hand-made Telethon objects.
`test_responder.py` runs responder.py, chatsearch.py, session_report.py and the `bot_*` tools against a fake Bot API, a fake Claude, a
fake Claude Code process and fake chat search, and the real anthropic SDK over a mock HTTP transport. Neither needs
the network or a login:

    .venv\Scripts\python.exe test_offline.py
    .venv\Scripts\python.exe test_responder.py

`client.py` calls one tool the way Claude does, which also works in a session started before the server was
registered:

    .venv\Scripts\python.exe client.py list
    .venv\Scripts\python.exe client.py whoami account=bot
    .venv\Scripts\python.exe client.py find_chats query=Masha
    .venv\Scripts\python.exe client.py bot_access
