"""HTTP clients for the services inside a project's vibe-dev container.

- `agentd(project)`: vibe-agentd on :4097 (files, git history, commits, export), bearer dev token.
- `opencode(project)`: `opencode serve` on :4096, HTTP basic `opencode:<dev token>`
  (the image sets OPENCODE_SERVER_PASSWORD from VIBE_PROJECT_TOKEN).

Targets OpenCode server API as of opencode-ai 1.19.4.
"""

from typing import Any

import httpx

from .activity import touch
from .errors import ApiError
from .models import Project
from .orchestrator import get_orchestrator
from .tokens import dev_token

AGENTD_PORT = 4097
OPENCODE_PORT = 4096
VITE_PORT = 5173
RUNTIME_PORT = 8000


async def dev_host(project: Project) -> str:
    if not project.container_name or not project.network_name:
        raise ApiError(409, "not_ready", "Vibe er ikke klar endnu")
    touch(project.id)
    orch = get_orchestrator()
    await orch.ensure_running(project.container_name)
    return await orch.address(project.container_name, project.network_name)


def _token(project: Project) -> str:
    if not project.token_seed:
        raise ApiError(409, "not_ready", "Vibe er ikke klar endnu")
    return dev_token(project.token_seed)


async def agentd(project: Project, timeout: float = 30) -> httpx.AsyncClient:
    host = await dev_host(project)
    return httpx.AsyncClient(
        base_url=f"http://{host}:{AGENTD_PORT}",
        headers={"Authorization": f"Bearer {_token(project)}"},
        timeout=timeout,
    )


async def opencode(project: Project, timeout: float | None = 60) -> httpx.AsyncClient:
    host = await dev_host(project)
    return httpx.AsyncClient(
        base_url=f"http://{host}:{OPENCODE_PORT}",
        auth=("opencode", _token(project)),
        timeout=timeout,
    )


def raise_for(r: httpx.Response, what: str) -> Any:
    if r.status_code >= 400:
        if r.status_code == 404:
            raise ApiError(404, "not_found", f"{what}: ikke fundet")
        raise ApiError(502, "upstream_error", f"{what} fejlede", upstreamStatus=r.status_code, detail=r.text[:300])
    if not r.content:
        return None
    return r.json()
