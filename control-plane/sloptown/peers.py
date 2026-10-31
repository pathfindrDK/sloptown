"""Which network peers may talk to the control plane.

The control plane joins every project network (it calls into OpenCode and agentd by
IP), so a Vibe can open a connection to it. Nothing a Vibe needs is served here: app
code reaches the platform through the egress proxy and people come through the edge.
So only the platform containers (edge, egress) and loopback (health checks, `podman
exec`) are let in, and only their `X-Forwarded-For` is believed.

Their addresses come from Podman. A platform container gains an address with every
project network it joins, so an unknown peer triggers a refresh, at most every
REFRESH_SECONDS: short, because the edge and egress get new addresses whenever they are
recreated, and long enough that a Vibe hammering the port costs one Podman call per window.
"""

import asyncio
import ipaddress
import logging
import time

from .config import get_settings

log = logging.getLogger(__name__)

REFRESH_SECONDS = 2.0

_ips: set[str] = set()
_refreshed = 0.0
_lock = asyncio.Lock()


def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def trusted(ip: str | None) -> bool:
    """A peer whose forwarding headers are believed (cached addresses only, no I/O)."""
    return bool(ip) and (_loopback(ip) or ip in _ips)


async def allowed(ip: str | None) -> bool:
    """Whether a connection from `ip` may be served at all."""
    global _ips, _refreshed
    if not ip:
        return False
    if trusted(ip):
        return True
    # One refresh at a time. The studio opens a Vibe with a burst of parallel requests,
    # all from the edge's address on that Vibe's network: the ones that arrive while
    # the first is refreshing wait for it, instead of finding the window closed and
    # getting a 404.
    async with _lock:
        if trusted(ip):
            return True
        if time.monotonic() - _refreshed < REFRESH_SECONDS:
            return False
        _refreshed = time.monotonic()
        from .orchestrator import OrchestratorError, get_orchestrator

        s = get_settings()
        try:
            _ips = await get_orchestrator().container_ips([s.edge_container, s.egress_container])
        except OrchestratorError:
            log.exception("could not refresh platform peer addresses")
        return ip in _ips


def invalidate() -> None:
    """The edge or egress just joined a network: look them up on the next unknown peer
    instead of waiting out REFRESH_SECONDS (Caddy's on-demand TLS `ask` comes from the
    new address first, and a 404 there costs the Vibe its certificate)."""
    global _refreshed
    _refreshed = 0.0


def reset() -> None:
    """Forget the cached addresses (tests)."""
    global _ips, _refreshed
    _ips, _refreshed = set(), 0.0
