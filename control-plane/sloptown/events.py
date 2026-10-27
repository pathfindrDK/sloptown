"""In-process pub/sub and SSE helpers.

Channels: `project:<pid>` (studio events, see docs/internal-api.md) and
`members:<pid>` (members.changed for apps). Single-process only: running more
than one control-plane worker needs a shared bus (Postgres LISTEN/NOTIFY) first.
"""

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from starlette.responses import StreamingResponse

PING_SECONDS = 15.0
QUEUE_MAX = 1000


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[tuple[str, Any]]]] = defaultdict(set)
        # Called with (channel, count) when subscriber count goes 0->1 or 1->0.
        self.on_first: Callable[[str], Awaitable[None] | None] | None = None
        self.on_last: Callable[[str], Awaitable[None] | None] | None = None

    def subscribers(self, channel: str) -> int:
        return len(self._subs.get(channel, ()))

    def publish(self, channel: str, event: str, data: Any) -> None:
        for q in list(self._subs.get(channel, ())):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:  # slow consumer: drop it, the client reconnects
                self._subs[channel].discard(q)

    async def subscribe(self, channel: str) -> AsyncIterator[tuple[str, Any]]:
        q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(QUEUE_MAX)
        first = not self._subs[channel]
        self._subs[channel].add(q)
        if first and self.on_first:
            r = self.on_first(channel)
            if asyncio.iscoroutine(r):
                await r
        try:
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    yield ("ping", {})
        finally:
            self._subs[channel].discard(q)
            if not self._subs[channel]:
                del self._subs[channel]
                if self.on_last:
                    r = self.on_last(channel)
                    if asyncio.iscoroutine(r):
                        await r


hub = Hub()


def sse_format(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def sse_response(channel: str, initial: list[tuple[str, Any]] | None = None) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        for event, data in initial or []:
            yield sse_format(event, data)
        async for event, data in hub.subscribe(channel):
            yield sse_format(event, data)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Parse an SSE byte stream (as text lines) into (event, data) pairs."""
    event, data = "message", []
    async for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)
