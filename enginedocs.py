"""Doppel's documentation and the graph of its code, for the bot's answers about the engine - shared by responder.py (the
API route) and search_mcp.py (the subscription route). Everyone the bot answers reads the manual, docs/manual in English
and Russian with its console and scripting references; the owner also reads the rest of docs/ - the studies, the plans,
what each iteration added - and asks the graphify graph of the code (the owner, 2026-10-23: "вам всё, остальным
manual")."""

import os
import re
import subprocess
from pathlib import Path

from localfiles import fold, terms

DOPPEL = Path(r"G:\doppel")
FOUND_SECTIONS = 10
EXCERPT_LINES = 4
EXCERPT_CHARS = 600
READ_CHARS = 8000
GRAPH_BUDGET = 1500
GRAPH_SECONDS = 90
GRAPH_CHARS = 12000
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
ANCHOR = re.compile(r"\s*\{#[^}]*\}$")

SEARCH_ENGINE_DOCS_DESCRIPTION = (
    "Search the documentation of Doppel - the owner's game engine and its editor - for a question about it: the manual, "
    "which says what a panel, a component, a setting or a console command is and how to use it, in English and Russian; "
    "for the owner's questions also the engine's other documents - its studies, plans and what each iteration added. "
    "Every word given must be in a section; a word may list alternatives with |, and most of it is in English, so "
    "туман|fog looks for both at once. Returns the matching sections, the manual's first, each with its file, heading "
    "and the lines that matched; read one whole with read_engine_doc.")
READ_ENGINE_DOC_DESCRIPTION = (
    "Read a section of Doppel's documentation: a file as search_engine_docs named it and the heading of the section, "
    "with the sections under it; without a heading, the file from its start. Long text is cut.")
QUERY_ENGINE_GRAPH_DESCRIPTION = (
    "Ask the knowledge graph of Doppel's code - for the owner's questions about how the engine is built: the classes, "
    "functions and files a question touches and how they connect, each with its source file and line. Ask in English, "
    "in the code's own words.")


def available():
    return (DOPPEL / "docs" / "manual").is_dir()


def documents(owner):
    """The Markdown files a question may read, as (name under docs/, path): the manual's for everyone, all of docs/ for
    the owner - the manual's first."""
    docs = DOPPEL / "docs"
    manual = sorted((docs / "manual").rglob("*.md"))
    rest = sorted(path for path in docs.rglob("*.md") if not path.is_relative_to(docs / "manual")) if owner else []
    return [(path.relative_to(docs).as_posix(), path) for path in manual + rest]


def sections(path):
    """A Markdown file cut at its headings - none counted inside a code block, and a heading's {#anchor} left out - as
    (level, heading, first line, lines); what comes before the first heading is a section of level 0 named after the
    file."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found, level, heading, start, fenced = [], 0, path.stem, 0, False
    for number, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else HEADING.match(line)
        if match:
            if number > start:
                found.append((level, heading, start + 1, lines[start:number]))
            level, heading, start = len(match.group(1)), ANCHOR.sub("", match.group(2)), number
    found.append((level, heading, start + 1, lines[start:]))
    return found


def excerpt(lines, words):
    """The lines of a section that hold a word looked for, the first few of them."""
    kept = [line.strip() for line in lines if any(word in fold(line) for alternatives in words for word in alternatives)]
    return "\n".join(kept[:EXCERPT_LINES])[:EXCERPT_CHARS]


def search(query, owner):
    if not available():
        return {"error": f"Doppel's documentation is not found in {DOPPEL}"}
    words = terms(query)
    if not words:
        return {"error": "give a word or two to look for"}
    found = []
    for order, (name, path) in enumerate(documents(owner)):
        for _, heading, line, lines in sections(path):
            text = fold("\n".join(lines))
            if all(any(word in text for word in alternatives) for alternatives in words):
                manual = name.startswith("manual/")
                score = sum(text.count(word) for alternatives in words for word in alternatives)
                found.append((not manual, -score, order, line, {"file": name, "heading": heading, "line": line,
                                                                "excerpt": excerpt(lines, words)}))
    found.sort(key=lambda item: item[:4])
    return {"found": [item[4] for item in found[:FOUND_SECTIONS]], "more": max(0, len(found) - FOUND_SECTIONS)}


def read(file, heading, owner):
    if not available():
        return {"error": f"Doppel's documentation is not found in {DOPPEL}"}
    names = dict(documents(owner))
    path = names.get(str(file or "").replace("\\", "/").strip("/"))
    if path is None:
        return {"error": f"no {file} among the documents this question may read"
                         + ("" if owner else " - the manual's, under manual/")}
    cut = sections(path)
    if heading:
        wanted = fold(heading).strip(" #")
        index = next((index for index, item in enumerate(cut) if fold(item[1]) == wanted), None)
        if index is None:
            index = next((index for index, item in enumerate(cut) if wanted in fold(item[1])), None)
        if index is None:
            return {"error": f"no heading «{heading}» in {file}",
                    "headings": [item[1] for item in cut if item[0]][:60]}
        level = cut[index][0]
        end = next((later for later in range(index + 1, len(cut)) if cut[later][0] <= level), len(cut))
        cut = cut[index:end]
    text = "\n".join(line for item in cut for line in item[3])
    return {"file": file, "heading": cut[0][1], "line": cut[0][2], "text": text[:READ_CHARS],
            "cut": len(text) > READ_CHARS}


def graph_command(question):
    python = (DOPPEL / "graphify-out" / ".graphify_python").read_text(encoding="utf-8").strip()
    return [python, "-m", "graphify", "query", question, "--budget", str(GRAPH_BUDGET),
            "--graph", str(DOPPEL / "graphify-out" / "graph.json")]


def graph(question, owner):
    if not owner:
        return {"error": "the graph of the code is for the owner's questions alone"}
    if not (DOPPEL / "graphify-out" / "graph.json").is_file():
        return {"error": f"there is no graph of the code in {DOPPEL / 'graphify-out'}"}
    if not str(question or "").strip():
        return {"error": "give the question to ask the graph"}
    try:
        done = subprocess.run(graph_command(str(question).strip()), cwd=DOPPEL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GRAPH_SECONDS,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": f"the graph did not answer: {error}"}
    if done.returncode != 0:
        return {"error": f"the graph failed: {(done.stderr or done.stdout).strip()[:500]}"}
    return {"graph": done.stdout.strip()[:GRAPH_CHARS]}
