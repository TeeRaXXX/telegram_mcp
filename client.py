#!/usr/bin/env python3
"""Call one tool of the Telegram MCP server from a shell, the way Claude would.

    .venv/Scripts/python.exe client.py list
    .venv/Scripts/python.exe client.py whoami account=me
    .venv/Scripts/python.exe client.py find_chats query=Masha
    .venv/Scripts/python.exe client.py read_messages chat=me limit=5

An argument is key=value; the value is read as JSON when it is JSON (numbers, true, lists) and as text
otherwise. One argument that is a whole JSON object works too. The tool's text comes back on stdout and the
exit code is 1 when the tool reported an error, so this is also how the server is tested.
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).with_name("server.py")


class Client:
    def __init__(self):
        self.proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=sys.stderr)
        self.next_id = 0

    def request(self, method, params=None):
        self.next_id += 1
        identifier = self.next_id
        message = {"jsonrpc": "2.0", "id": identifier, "method": method}
        if params is not None:
            message["params"] = params
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        while True:
            raw = self.proc.stdout.readline()
            if not raw:
                raise SystemExit("the server closed its output")
            reply = json.loads(raw.decode("utf-8"))
            if reply.get("id") == identifier:
                if "error" in reply:
                    raise SystemExit("error " + json.dumps(reply["error"], indent=1, ensure_ascii=False))
                return reply["result"]

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def parse_arguments(items):
    arguments = {}
    for item in items:
        if item.startswith("{"):
            arguments.update(json.loads(item))
            continue
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item!r}")
        key, value = item.split("=", 1)
        try:
            arguments[key] = json.loads(value)
        except ValueError:
            arguments[key] = value
    return arguments


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = Client()
    try:
        client.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "telegram-mcp client", "version": "1.0"},
        })
        client.notify("notifications/initialized")

        if argv[1] == "list":
            for tool in client.request("tools/list")["tools"]:
                print(f"{tool['name']}: {tool.get('description', '').strip().splitlines()[0]}")
            return 0

        result = client.request("tools/call", {"name": argv[1], "arguments": parse_arguments(argv[2:])})
        for item in result.get("content", []):
            if item.get("type") == "text":
                print(item["text"])
        return 1 if result.get("isError") else 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
