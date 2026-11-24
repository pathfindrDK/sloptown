"""vibe-search-mcp (images/vibe-dev/search_mcp.py): the agent's web_search tool over MCP stdio."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "vibe-dev" / "search_mcp.py"
SEEN: list[dict] = []


class FakeEgress(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        SEEN.append({"path": url.path, "query": q, "auth": self.headers.get("Authorization")})
        if q.get("q") == "fail":
            status, body = 429, {"error": {"type": "rate_limited", "message": "For mange søgninger. Vent et minut."}}
        elif q.get("q") == "empty":
            status, body = 200, {"query": "empty", "results": []}
        else:
            status, body = 200, {"query": q["q"], "results": [
                {"title": "Brøker", "url": "https://emu.dk/brok", "description": "Om brøker", "age": "2025"},
                {"title": "", "url": "https://example.com/", "description": ""},
            ]}
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def egress():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeEgress)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def run(messages: list[dict], env: dict) -> list[dict]:
    """Feed newline-delimited JSON-RPC to the server, return its replies."""
    stdin = "".join(json.dumps(m) + "\n" for m in messages) + "not json\n"
    full_env = {**os.environ, "HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", **env}
    out = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, env=full_env, timeout=30)
    assert out.returncode == 0, out.stderr
    return [json.loads(line) for line in out.stdout.splitlines()]


def call(id_: int, **arguments) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": {"name": "search", "arguments": arguments}}


def test_handshake_and_tool_list(egress):
    replies = run([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
    ], {"VIBE_PLATFORM_URL": egress, "VIBE_PROJECT_TOKEN": "tok"})
    assert [r.get("id") for r in replies] == [1, 2, 3, 4, None]  # no reply to the notification
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert replies[0]["result"]["capabilities"] == {"tools": {"listChanged": False}}
    tools = replies[1]["result"]["tools"]
    assert [t["name"] for t in tools] == ["search"]
    assert tools[0]["inputSchema"]["required"] == ["query"]
    assert replies[2]["result"] == {}
    assert replies[3]["error"]["code"] == -32601
    assert replies[4]["error"]["code"] == -32700  # the trailing "not json" line


def test_search_goes_to_egress_with_token_and_renders_text(egress):
    SEEN.clear()
    replies = run([call(1, query="brøker 5. klasse", count=3, language="da", country="DK", freshness="py")],
                  {"VIBE_PLATFORM_URL": egress + "/", "VIBE_PROJECT_TOKEN": "tok"})
    result = replies[0]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert text.splitlines() == [
        'Results for "brøker 5. klasse":', "",
        "1. Brøker", "   https://emu.dk/brok", "   Om brøker (2025)",
        "2. https://example.com/", "   https://example.com/",
    ]
    # Direct to the egress (HTTP_PROXY points nowhere), bearer project token.
    assert SEEN == [{"path": "/search/web", "auth": "Bearer tok", "query": {
        "q": "brøker 5. klasse", "count": "3", "search_lang": "da", "country": "DK", "freshness": "py"}}]


def test_errors_are_tool_errors(egress):
    env = {"VIBE_PLATFORM_URL": egress, "VIBE_PROJECT_TOKEN": "tok"}
    replies = run([call(1, query="fail"), call(2, query="empty"), call(3, query="  "),
                   {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "other"}}], env)
    assert replies[0]["result"] == {"content": [{"type": "text", "text": "Search failed: For mange søgninger. Vent et minut."}], "isError": True}
    assert replies[1]["result"]["content"][0]["text"] == 'No results for "empty".'
    assert replies[2]["result"]["isError"] is True
    assert replies[3]["error"]["code"] == -32602


def test_unconfigured_and_unreachable():
    replies = run([call(1, query="x")], {"VIBE_PLATFORM_URL": "", "VIBE_PROJECT_TOKEN": ""})
    assert replies[0]["result"]["isError"] is True
    assert "not configured" in replies[0]["result"]["content"][0]["text"]
    replies = run([call(1, query="x")], {"VIBE_PLATFORM_URL": "http://127.0.0.1:9", "VIBE_PROJECT_TOKEN": "tok"})
    assert replies[0]["result"]["isError"] is True
    assert replies[0]["result"]["content"][0]["text"].startswith("Search failed:")
