"""AI calls through SlopTown's inference proxy (OpenAI-compatible).

The proxy injects the real key, meters tokens against the app budget and may
switch to a smaller model. When the budget is used up it answers 429 and these
functions raise BudgetExceeded with `retry_at` (next reset).
"""

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from . import _env
from .identity import User

Message = dict[str, Any]  # {"role": "system"|"user"|"assistant", "content": str}


class BudgetExceeded(Exception):
    def __init__(self, retry_at: datetime | None, message: str = "AI-budgettet er brugt op for denne uge."):
        super().__init__(message)
        self.retry_at = retry_at
        self.message = message


class AiError(Exception):
    pass


def _request(messages: list[Message], model: str | None, user: User | None, stream: bool, extra: dict[str, Any]):
    base = _env.openai_base_url()
    if not base:
        raise AiError("OPENAI_BASE_URL / VIBE_PLATFORM_URL is not set")
    headers = {"authorization": f"Bearer {_env.openai_api_key()}"}
    if user:
        headers["x-vibe-user"] = user.id
    body: dict[str, Any] = {"model": model or _env.default_model(), "messages": messages, **extra}
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return f"{base}/chat/completions", headers, body


def _raise_for(r: httpx.Response, body: bytes) -> None:
    if r.status_code == 429:
        try:
            err = json.loads(body).get("error", {})
        except ValueError:
            err = {}
        if isinstance(err, dict) and err.get("type") == "budget_exceeded":
            retry = err.get("retry_at")
            raise BudgetExceeded(datetime.fromisoformat(retry.replace("Z", "+00:00")) if retry else None)
    if r.status_code >= 400:
        raise AiError(f"inference proxy returned {r.status_code}: {body[:300]!r}")


async def chat(
    messages: list[Message] | str,
    *,
    model: str | None = None,
    user: User | None = None,
    **extra: Any,
) -> str:
    """One completion; returns the assistant text. A plain string is sent as one user message."""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    url, headers, body = _request(messages, model, user, False, extra)
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        r = await client.post(url, headers=headers, json=body)
    _raise_for(r, r.content)
    data = r.json()
    return data["choices"][0]["message"].get("content") or ""


async def stream(
    messages: list[Message] | str,
    *,
    model: str | None = None,
    user: User | None = None,
    **extra: Any,
) -> AsyncIterator[str]:
    """Yield text deltas as they arrive."""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    url, headers, body = _request(messages, model, user, True, extra)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, read=300), trust_env=False) as client:
        async with client.stream("POST", url, headers=headers, json=body) as r:
            if r.status_code >= 400:
                _raise_for(r, await r.aread())
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                for choice in chunk.get("choices", []):
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta
