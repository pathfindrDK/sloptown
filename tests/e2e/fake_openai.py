"""Scripted stand-in for the OpenAI chat completions API, used by the end-to-end test.

Stdlib only, so it runs in any Python image:  python fake_openai.py [port]

Speaks both POST /v1/chat/completions and POST /v1/responses (the Responses API,
which OpenCode uses through @ai-sdk/openai), streaming or not. Behaviour:
- no `tools` in the request (title generation, app `ai.chat`): answer with plain text.
- tools present and the conversation has no tool result after the last user
  message: write web/src/App.tsx containing the marker from the user's message
  (`E2E-<WORD>-<x>`, e.g. `E2E-MARKER-1a2b`). Uses OpenCode's `write` tool when offered, else `bash`
  with a heredoc (OpenCode gives GPT models `apply_patch` instead of `write`),
  filling in whatever parameter names the tool's JSON schema declares.
- otherwise: finish the turn with a short Danish summary.

Every response reports usage (prompt 100 / completion 20 tokens) so the
egress metering can be checked. GET /requests returns what was received.
"""

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
SEEN: list[dict] = []

APP_TSX = """export default function App() {{
  return (
    <main style={{{{ padding: 24 }}}}>
      <h1>{marker}</h1>
    </main>
  );
}}
"""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _write_call(tools: list[dict], messages: list[dict]) -> dict | None:
    """Build a tool call to the write tool, or None if there's nothing to do."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    if any(m.get("role") == "tool" for m in messages[last_user + 1 :]):
        return None
    by_name = {t.get("function", {}).get("name"): t["function"] for t in tools}
    match = re.search(r"E2E-[A-Z]+-\w+", _text(messages[last_user].get("content")) if last_user >= 0 else "")
    source = APP_TSX.format(marker=match.group(0) if match else "E2E-MARKER")
    if "write" in by_name:
        props = by_name["write"].get("parameters", {}).get("properties", {})
        path_key = next((k for k in props if "path" in k.lower()), "filePath")
        content_key = next((k for k in props if "content" in k.lower()), "content")
        name, args = "write", {path_key: "/workspace/web/src/App.tsx", content_key: source}
    elif "bash" in by_name:
        props = by_name["bash"].get("parameters", {}).get("properties", {})
        name, args = "bash", {"command": f"mkdir -p /workspace/web/src && cat > /workspace/web/src/App.tsx <<'VIBE_EOF'\n{source}VIBE_EOF"}
        if "description" in props:
            args["description"] = "Skriv App.tsx"
    else:
        return None
    return {"id": f"call_{int(time.time() * 1000)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _from_responses(body: dict) -> tuple[list[dict], list[dict]]:
    """Map a Responses API request onto the chat-completions shapes _write_call reads."""
    tools = [{"function": {"name": t.get("name"), "parameters": t.get("parameters") or {}}}
             for t in body.get("tools") or [] if t.get("type") == "function"]
    items = body.get("input")
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    messages = []
    for it in items or []:
        if it.get("type") == "function_call_output":
            messages.append({"role": "tool", "content": it.get("output", "")})
        elif it.get("type") == "function_call":
            messages.append({"role": "assistant", "content": ""})
        elif "role" in it:
            messages.append({"role": it["role"], "content": it.get("content")})
    return tools, messages


def _sse(wfile, event: str, data: dict) -> None:
    wfile.write(f"event: {event}\ndata: {json.dumps({'type': event, **data})}\n\n".encode())
    wfile.flush()


RESPONSES_USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                   "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "gpt-5.1", "object": "model"}]})
        if self.path == "/requests":
            return self._json(200, SEEN)
        if self.path == "/health":
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        SEEN.append({"path": self.path, "auth": self.headers.get("authorization", ""), "model": body.get("model"),
                     "tools": [t.get("function", {}).get("name") for t in body.get("tools") or []]})
        if self.path.endswith("/responses"):
            return self._responses(body)
        if not self.path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        call = _write_call(body.get("tools") or [], body.get("messages") or [])
        text = None if call else ("E2E titel" if not body.get("tools") else "Færdig. Jeg har opdateret App.tsx.")
        model = body.get("model", "gpt-5.1")
        if not body.get("stream"):
            msg = {"role": "assistant", "content": text}
            if call:
                msg["tool_calls"] = [call]
            return self._json(200, {"id": "cmpl-e2e", "object": "chat.completion", "model": model,
                                    "choices": [{"index": 0, "message": msg,
                                                 "finish_reason": "tool_calls" if call else "stop"}],
                                    "usage": USAGE})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def chunk(delta, finish=None, usage=None):
            c = {"id": "cmpl-e2e", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if usage is None else []}
            if usage is not None:
                c["usage"] = usage
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        if call:
            chunk({"tool_calls": [{"index": 0, **call}]})
            chunk({}, "tool_calls")
        else:
            chunk({"content": text})
            chunk({}, "stop")
        chunk({}, usage=USAGE)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _responses_impl(self, body: dict) -> None:
    tools, messages = _from_responses(body)
    call = _write_call(tools, messages)
    model = body.get("model", "gpt-6.1-sol")
    rid, created = f"resp_{int(time.time() * 1000)}", int(time.time())
    if call:
        item = {"type": "function_call", "id": f"fc_{rid}", "call_id": call["id"], "status": "completed",
                "name": call["function"]["name"], "arguments": call["function"]["arguments"]}
    else:
        text = "E2E titel" if not tools else "Færdig. Jeg har opdateret App.tsx."
        item = {"type": "message", "id": f"msg_{rid}", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}
    response = {"id": rid, "object": "response", "created_at": created, "model": model,
                "status": "completed", "output": [item], "usage": RESPONSES_USAGE}
    if not body.get("stream"):
        return self._json(200, response)
    self.send_response(200)
    self.send_header("content-type", "text/event-stream")
    self.end_headers()
    w = self.wfile
    _sse(w, "response.created", {"response": {**response, "status": "in_progress", "output": [], "usage": None}})
    if call:
        _sse(w, "response.output_item.added", {"output_index": 0, "item": {**item, "arguments": "", "status": "in_progress"}})
        _sse(w, "response.function_call_arguments.delta", {"item_id": item["id"], "output_index": 0, "delta": item["arguments"]})
        _sse(w, "response.function_call_arguments.done", {"item_id": item["id"], "output_index": 0, "arguments": item["arguments"]})
    else:
        text = item["content"][0]["text"]
        _sse(w, "response.output_item.added", {"output_index": 0, "item": {**item, "content": [], "status": "in_progress"}})
        _sse(w, "response.content_part.added", {"item_id": item["id"], "output_index": 0, "content_index": 0,
                                                "part": {"type": "output_text", "text": "", "annotations": []}})
        _sse(w, "response.output_text.delta", {"item_id": item["id"], "output_index": 0, "content_index": 0, "delta": text, "logprobs": []})
        _sse(w, "response.output_text.done", {"item_id": item["id"], "output_index": 0, "content_index": 0, "text": text, "logprobs": []})
        _sse(w, "response.content_part.done", {"item_id": item["id"], "output_index": 0, "content_index": 0,
                                               "part": item["content"][0]})
    _sse(w, "response.output_item.done", {"output_index": 0, "item": item})
    _sse(w, "response.completed", {"response": response})


Handler._responses = _responses_impl


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8300
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
