#!/usr/bin/env python3
"""A short report from the bot to its owner in Telegram when a Claude Code session of the owner's finishes a piece of
work. A Stop hook in ~/.claude/settings.json runs it and hands it the hook's JSON on stdin:

    {"type": "command", "command": "<.venv>/Scripts/pythonw.exe", "args": ["<here>/session_report.py"], "async": true}

It writes when the session worked at least settings.report_min_minutes (data/access.json; null: never) since the later
of the owner's last typed prompt and its last report there - not while one of the session's background tasks still
runs, since the session goes on by itself once that ends, and never for the Claude Code the bot starts under
data/claude-work. The report names the session and its folder, how long it worked, how many tools it called and how
many files it changed, the prompt, and Claude's last message: as it is when short, else shortened by Claude Code on the
owner's subscription with settings.report_model (null: cut instead), run with no tools and every hook off.

Why it did or did not write goes to data/session_report.log.

    .venv/Scripts/python.exe session_report.py --dry-run --force < hook.json    # print a report instead of sending it
"""

import asyncio
import json
import re
import shutil
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import common
from responder import (SubscriptionError, claude_code, claude_result, no_hooks_settings, remove_work, run_process,
                       subscription_environment, temporary_folder)

SHORT_ENOUGH = 700
BODY_LIMIT = 1500
REPORT_LIMIT = 3800
PROMPT_SHOWN = 160
SUMMARY_SECONDS = 120
SESSIONS_KEPT = 200
LOG_BYTES = 1_000_000
CHANGING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RUNNING = {"running", "pending", "in_progress", "started"}

SUMMARY_SYSTEM = """\
You turn the final message of a Claude Code session into a short report its owner reads in Telegram on a phone. \
Write 2 to 5 short lines of plain text in the language of the message: what was done and how it ended, what failed or \
is left, and anything the owner has to do. No Markdown, no headings, no preamble, and file paths only when they \
matter."""


def log(text):
    """A line in data/session_report.log - a hook has no console - kept under a megabyte."""
    try:
        common.DATA.mkdir(parents=True, exist_ok=True)
        if common.REPORT_LOG.exists() and common.REPORT_LOG.stat().st_size > LOG_BYTES:
            common.REPORT_LOG.replace(common.REPORT_LOG.with_name(common.REPORT_LOG.name + ".1"))
        with open(common.REPORT_LOG, "a", encoding="utf-8") as file:
            file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


# --- reading the session's transcript ------------------------------------------------------------------------

def read_transcript(path):
    """The entries of a session transcript; a line still being written is skipped."""
    entries = []
    try:
        with open(path, encoding="utf-8") as file:
            for line in file:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except (OSError, TypeError):
        pass
    return [entry for entry in entries if isinstance(entry, dict)]


def text_of(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text") or "" for block in content or []
                     if isinstance(block, dict) and block.get("type") == "text")


def typed_prompt(entry):
    """Whether a transcript entry is a prompt the person typed, a slash command included - not a tool result, a task
    notification, an injected skill or the summary a compaction leaves."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return False
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return origin["kind"] == "human"
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result"
                                         for block in content):
        return False
    text = text_of(entry).lstrip()
    return bool(text) and not text.startswith("<")


def moment(entry):
    try:
        return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return None


def work_summary(entries, since=None):
    """What the session did lately - from its last typed prompt, or from since, a report already sent, when that is
    later: when that began, the prompt, the tools it called and the files it changed; and the session's title."""
    titles = {}
    for entry in entries:
        if entry.get("type") == "custom-title" and entry.get("customTitle"):
            titles["custom"] = entry["customTitle"]
        elif entry.get("type") == "ai-title" and entry.get("aiTitle"):
            titles["ai"] = entry["aiTitle"]
    summary = {"title": titles.get("custom") or titles.get("ai"), "started": None, "prompt": "", "tools": 0,
               "files": []}
    first = next((index for index in range(len(entries) - 1, -1, -1)
                  if typed_prompt(entries[index]) and moment(entries[index]) is not None), None)
    if first is None:
        return summary
    summary["prompt"] = text_of(entries[first]).strip()
    summary["started"] = max(moment(entries[first]), since) if since is not None else moment(entries[first])
    seen = set()
    for entry in entries[first + 1:]:
        when = moment(entry)
        if entry.get("type") != "assistant" or entry.get("isSidechain") or (when and when < summary["started"]):
            continue
        for block in (entry.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("id") in seen:
                continue
            seen.add(block.get("id"))
            summary["tools"] += 1
            given = block.get("input") or {}
            path = given.get("file_path") or given.get("notebook_path")
            if block.get("name") in CHANGING_TOOLS and path and path not in summary["files"]:
                summary["files"].append(path)
    return summary


def last_words(entries):
    """Claude's last text in the transcript, for a hook input that does not carry it."""
    for entry in reversed(entries):
        if entry.get("type") == "assistant" and not entry.get("isSidechain") and text_of(entry).strip():
            return text_of(entry).strip()
    return ""


# --- whether to report, and what ------------------------------------------------------------------------------

def inside(path, folder):
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except (TypeError, ValueError, OSError):
        return False


def running(tasks):
    """The background tasks of a hook's input that have not ended."""
    return [task for task in tasks or []
            if not isinstance(task, dict) or str(task.get("status", "running")).lower() in RUNNING]


def why_not(hook, summary, settings, now):
    """Why no report is due, or None when one is."""
    minutes = settings.get("report_min_minutes")
    if inside(hook.get("cwd"), common.CLAUDE_WORK):
        return "the bot's own Claude Code"
    if minutes is None:
        return "reports are off (report_min_minutes is null)"
    if hook.get("stop_hook_active"):
        return "a stop hook keeps this session going"
    if running(hook.get("background_tasks")):
        return "background tasks are still running"
    if summary["started"] is None:
        return "no typed prompt in the transcript"
    worked = (now - summary["started"]).total_seconds()
    if worked < minutes * 60:
        return f"worked {worked:.0f} s, less than {minutes} min"
    return None


def plain(text):
    """Markdown as plain text for Telegram: links by their text, no emphasis, code or heading marks."""
    text = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", text)
    text = re.sub(r"```[^\n]*\n?", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def cut(text, limit):
    """Text cut to a length, at the end of a line or a sentence when one is near."""
    if len(text) <= limit:
        return text
    piece = text[:limit - 1]
    end = max(piece.rfind("\n"), piece.rfind(". "))
    if end > limit // 2:
        piece = piece[:end + 1]
    return piece.rstrip() + "…"


def duration(seconds):
    minutes = round(seconds / 60)
    if minutes < 1:
        return f"{seconds:.0f} с"
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60:02d} мин"


def report_text(hook, summary, body, now):
    folder = hook.get("cwd") or ""
    title = summary["title"] or Path(folder).name or "Сессия Claude Code"
    worked = (now - summary["started"]).total_seconds() if summary["started"] else 0
    facts = [f"работала {duration(worked)}"]
    if summary["tools"]:
        facts.append(f"инструментов: {summary['tools']}")
    if summary["files"]:
        facts.append(f"файлов изменено: {len(summary['files'])}")
    lines = [f"✅ {title}", " · ".join([folder, *facts] if folder else facts)]
    prompt = " ".join(re.sub(r"<[^>]+>", " ", summary["prompt"]).split())
    if prompt:
        lines.append("Задача: " + cut(prompt, PROMPT_SHOWN))
    lines += ["", body]
    return cut("\n".join(lines), REPORT_LIMIT)


async def shorten(text, settings, run=run_process):
    """Claude's last message cut down to a few plain lines by Claude Code on the owner's subscription, with no tools
    and every hook off; None when the message is short already, no report_model is set, or that fails."""
    model = settings.get("report_model")
    executable = claude_code() if model and len(text) > SHORT_ENOUGH else None
    if executable is None:
        return None
    work = common.CLAUDE_WORK / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        arguments = [executable, "-p", "--output-format", "json", "--model", model, "--tools", "",
                     "--strict-mcp-config", "--permission-prompts", "none", "--no-session-persistence",
                     "--disable-slash-commands", "--settings", no_hooks_settings(work),
                     "--system-prompt", SUMMARY_SYSTEM]
        code, stdout, stderr = await run(arguments, text, work, {**subscription_environment(), **temporary_folder(work)},
                                         SUMMARY_SECONDS)
        return (claude_result(code, stdout, stderr).get("result") or "").strip() or None
    except (SubscriptionError, OSError) as error:
        log(f"could not shorten the last message, cutting it instead: {error}")
        return None
    finally:
        remove_work(work)


# --- what was reported ----------------------------------------------------------------------------------------

def last_report(session):
    stamp = (common.load_json(common.REPORTS_FILE) or {}).get(session)
    try:
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


def remember_report(session, when):
    with common.file_lock(common.REPORTS_FILE):
        reports = common.load_json(common.REPORTS_FILE) or {}
        reports[session] = when.isoformat()
        common.save_json(common.REPORTS_FILE, dict(sorted(reports.items(), key=lambda item: item[1])[-SESSIONS_KEPT:]))


async def report(hook, dry_run=False, force=False, now=None, run=run_process):
    """Send the owner a report on the work that just ended, when one is due; returns its text, or None. force skips
    every rule but the one about the bot's own Claude Code."""
    now = now or datetime.now(timezone.utc)
    session = str(hook.get("session_id") or "")
    access = common.load_access()
    entries = read_transcript(hook.get("transcript_path"))
    summary = work_summary(entries, last_report(session))
    reason = why_not(hook, summary, access["settings"], now)
    if reason is not None and not (force and reason != "the bot's own Claude Code"):
        log(f"{session[:8]} no report: {reason}")
        return None
    last = (hook.get("last_assistant_message") or last_words(entries)).strip()
    body = (await shorten(last, access["settings"], run) if last else None) or cut(plain(last), BODY_LIMIT) or \
        "(в конце Claude ничего не написал)"
    text = report_text(hook, summary, body, now)
    if dry_run:
        print(text)
        return text
    token = (common.load_json(common.BOT_FILE) or {}).get("token")
    if not token or not access["owner_id"]:
        log(f"{session[:8]} no report: the bot or its owner is unknown (login.py bot, login.py me)")
        return None
    try:
        sent = await asyncio.to_thread(common.bot_request, token, "sendMessage", {
            "chat_id": access["owner_id"], "text": text, "link_preview_options": {"is_disabled": True}})
    except common.BotApiError as error:
        log(f"{session[:8]} could not send the report: {error.description}")
        return None
    common.remember(messages=[common.bot_message_item(sent, from_bot=True)])
    remember_report(session, now)
    log(f"{session[:8]} reported: {summary['tools']} tools, {len(summary['files'])} files changed")
    return text


def main():
    dry_run, force = "--dry-run" in sys.argv[1:], "--force" in sys.argv[1:]
    if dry_run and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace") if sys.stdin is not None else ""
        asyncio.run(report(json.loads(raw or "{}"), dry_run=dry_run, force=force))
    except Exception:  # noqa: BLE001 - a hook must not trouble the session it reports on
        log("failed:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
