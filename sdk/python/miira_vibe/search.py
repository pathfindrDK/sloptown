"""Web search (Brave Search) through SlopTown's egress.

    from miira_vibe import search

    hits = await search.web("klimatopmøde 2026", language="da", freshness="pm", user=user)
    for hit in hits:
        print(hit.title, hit.url, hit.description, hit.age)

The egress holds the search key. In a published app every search costs app
budget (a fixed number of tokens, like an AI call); when the budget is used up
this raises ai.BudgetExceeded. Results are titles, links and short snippets
(safe search on); the app cannot fetch the pages themselves. Combine with
ai.chat to summarise them, and show the links as sources.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx

from . import _env
from .ai import BudgetExceeded
from .identity import User


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    description: str
    age: str | None = None  # Brave's page date, e.g. "3 days ago" or "April 16, 2026"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SearchError(Exception):
    """Search failed (rate limit, service down, bad query). `message` is Danish, fit for users."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


async def web(
    query: str,
    *,
    count: int | None = None,
    offset: int | None = None,
    language: str | None = None,
    country: str | None = None,
    freshness: str | None = None,
    user: User | None = None,
) -> list[SearchResult]:
    """Search the web. `count` 1–20 (default 8), `offset` 0–9 (result page), `language`
    e.g. "da", `country` e.g. "DK", `freshness` pd|pw|pm|py (day/week/month/year) or
    "YYYY-MM-DDtoYYYY-MM-DD". Pass `user` so the search is attributed to that member."""
    base = _env.platform_url()
    if not base:
        raise SearchError("Websøgning er ikke tilgængelig her (VIBE_PLATFORM_URL mangler).", 503)
    params: dict[str, Any] = {"q": query}
    for name, value in (("count", count), ("offset", offset), ("search_lang", language), ("country", country), ("freshness", freshness)):
        if value is not None and value != "":
            params[name] = value
    headers = {"authorization": f"Bearer {_env.project_token()}"}
    if user:
        headers["x-vibe-user"] = user.id
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        r = await client.get(f"{base}/search/web", params=params, headers=headers)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        err = err if isinstance(err, dict) else {}
        if r.status_code == 429 and err.get("type") == "budget_exceeded":
            retry = err.get("retry_at")
            raise BudgetExceeded(datetime.fromisoformat(retry.replace("Z", "+00:00")) if retry else None)
        raise SearchError(err.get("message") or f"Søgningen fejlede (HTTP {r.status_code}).", r.status_code)
    try:
        data = r.json()
    except json.JSONDecodeError:
        raise SearchError("Søgetjenesten svarede ikke med JSON.") from None
    return [
        SearchResult(title=h.get("title", ""), url=h["url"], description=h.get("description", ""), age=h.get("age"))
        for h in data.get("results", [])
        if isinstance(h, dict) and isinstance(h.get("url"), str)
    ]
