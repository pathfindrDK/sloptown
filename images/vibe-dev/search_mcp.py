#!/usr/bin/env python3
"""vibe-search-mcp: the build agent's `web_search` tool (an MCP server on stdio).

OpenCode starts it from /etc/opencode/opencode.json (`mcp.web`). Each call goes to
the egress, `GET $VIBE_PLATFORM_URL/search/web` with the project token; the egress
holds the Brave key, checks that the token is a dev token and throttles. Nothing
here talks to the internet. Standard library only (the image has no network installs).

MCP stdio transport: one JSON-RPC message per line on stdin/stdout; logs go to stderr.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVER = {"name": "vibe-search", "version": "1.0.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
TIMEOUT = 25

TOOL = {
    "name": "search",
    "title": "Web search",
    "description": (
        "Search the web (Brave Search, safe search on). Returns titles, links, short snippets and "
        "age. You cannot open the pages: rely on the snippets, and give the teacher the links when "
        "they should read more. Use it for facts, curriculum, datasheets and library docs. "
        "Queries leave the platform: never put names or other personal data about students or "
        "teachers in a query."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for (at most 400 characters, 50 words)."},
            "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Number of results (default 8)."},
            "offset": {"type": "integer", "minimum": 0, "maximum": 9, "description": "Result page, for more results (default 0)."},
            "language": {"type": "string", "description": "Language of the results, e.g. \"da\" or \"en\" (default: any)."},
            "country": {"type": "string", "description": "Country of the results, e.g. \"DK\" (default: any)."},
            "freshness": {
                "type": "string",
                "description": "Only recent pages: pd (24 h), pw (week), pm (month), py (year), or YYYY-MM-DDtoYYYY-MM-DD.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
}

# The egress is reached directly (it is in NO_PROXY anyway); never via HTTP_PROXY.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SearchError(Exception):
    pass


def search(args: dict) -> dict:
    base = os.environ.get("VIBE_PLATFORM_URL", "").rstrip("/")
    token = os.environ.get("VIBE_PROJECT_TOKEN", "")
    if not base or not token:
        raise SearchError("Web search is not configured in this container (VIBE_PLATFORM_URL / VIBE_PROJECT_TOKEN).")
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise SearchError("query is required.")
    params = {"q": query}
    for arg, name in (("count", "count"), ("offset", "offset"), ("language", "search_lang"),
                      ("country", "country"), ("freshness", "freshness")):
        value = args.get(arg)
        if value is not None and value != "":
            params[name] = str(value)
    req = urllib.request.Request(
        f"{base}/search/web?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with _opener.open(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc)["error"]["message"]
        except Exception:
            message = f"HTTP {exc.code}"
        raise SearchError(f"Search failed: {message}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SearchError(f"Search failed: {exc}") from None


def render(data: dict) -> str:
    results = data.get("results") or []
    if not results:
        return f'No results for "{data.get("query", "")}".'
    lines = [f'Results for "{data.get("query", "")}":', ""]
    for i, hit in enumerate(results, 1):
        lines.append(f"{i}. {hit.get('title') or hit.get('url')}")
        lines.append(f"   {hit.get('url')}")
        desc = hit.get("description") or ""
        if hit.get("age"):
            desc = f"{desc} ({hit['age']})" if desc else f"({hit['age']})"
        if desc:
            lines.append(f"   {desc}")
    return "\n".join(lines)


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, the reply out (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notifications (initialized, cancelled, ...)
        return None
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        result = {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {params.get('name')!r}"}}
        try:
            text, is_error = render(search(params.get("arguments") or {})), False
        except SearchError as exc:
            text, is_error = str(exc), True
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
