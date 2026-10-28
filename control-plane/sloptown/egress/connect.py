"""Raw HTTP forward proxy (CONNECT tunnels + absolute-form HTTP) for allowlisted hosts.

ASGI servers can't do CONNECT, so this is a small asyncio server on
EGRESS_PROXY_PORT (default 8201). Containers reach it through
HTTP(S)_PROXY=http://vibe:<VIBE_PROJECT_TOKEN>@sloptown-egress:8201; the token is
taken from Proxy-Authorization (Basic password or Bearer).

Only hosts in EGRESS_ALLOWLIST are reachable. Everything else gets 403. The same
Limiter as the ASGI app applies (rate + concurrency per project).
"""

import asyncio
import base64
import logging

from .auth import resolve_token
from .config import EgressConfig, host_allowed
from .limits import Limiter, Throttled

log = logging.getLogger("sloptown.egress.connect")

MAX_HEAD = 16 * 1024


def _token_from(headers: dict[str, str]) -> str:
    value = headers.get("proxy-authorization", "")
    scheme, _, cred = value.partition(" ")
    if scheme.lower() == "bearer":
        return cred.strip()
    if scheme.lower() == "basic":
        try:
            return base64.b64decode(cred.strip()).decode().partition(":")[2]
        except ValueError:
            return ""
    return ""


def _split_hostport(value: str, default_port: int) -> tuple[str, int]:
    if value.startswith("["):  # [v6]:port
        host, _, rest = value[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default_port
    host, sep, port = value.rpartition(":")
    if sep and port.isdigit():
        return host, int(port)
    return value, default_port


async def _reply(writer: asyncio.StreamWriter, status: str, extra: str = "") -> None:
    writer.write(f"HTTP/1.1 {status}\r\n{extra}Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
    try:
        await writer.drain()
    finally:
        writer.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle: float) -> None:
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=idle)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cfg: EgressConfig, limiter: Limiter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=cfg.timeout_seconds)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        return await _reply(writer, "400 Bad Request")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

    caller = await resolve_token(_token_from(headers))
    if caller is None:
        return await _reply(writer, "407 Proxy Authentication Required", 'Proxy-Authenticate: Basic realm="sloptown"\r\n')

    if method.upper() == "CONNECT":
        host, port = _split_hostport(target, 443)
        forward_head = None
    elif target.lower().startswith("http://"):
        rest = target[7:]
        hostport, slash, path = rest.partition("/")
        host, port = _split_hostport(hostport, 80)
        kept = [l for l in lines[1:] if l and not l.lower().startswith(("proxy-", "connection:"))]
        forward_head = (f"{method} /{path if slash else ''} {version}\r\n" + "".join(f"{l}\r\n" for l in kept) + "Connection: close\r\n\r\n").encode("latin-1")
    else:
        return await _reply(writer, "400 Bad Request")

    if not host_allowed(cfg.allowlist, host, port):
        log.info("egress denied project=%s host=%s:%s", caller.project_id, host, port)
        return await _reply(writer, "403 Forbidden", "X-Vibe-Egress: host-not-allowed\r\n")

    try:
        limiter.check_rate(caller.project_id)
        limiter.check_host(caller.project_id, host)
        limiter.acquire(caller.project_id)
    except Throttled as t:
        log.info("egress throttled project=%s host=%s reason=%s", caller.project_id, host, t.reason)
        retry = "60" if t.reason.startswith("host_rpm") else "1"
        return await _reply(writer, "429 Too Many Requests", f"Retry-After: {retry}\r\n")

    try:
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=cfg.timeout_seconds)
        except (OSError, asyncio.TimeoutError):
            return await _reply(writer, "502 Bad Gateway")
        if forward_head is None:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            up_writer.write(forward_head)
            await up_writer.drain()
        await asyncio.gather(
            _pipe(reader, up_writer, cfg.timeout_seconds),
            _pipe(up_reader, writer, cfg.timeout_seconds),
        )
    finally:
        limiter.release(caller.project_id)


async def start_connect_proxy(host: str, port: int, cfg: EgressConfig, limiter: Limiter) -> asyncio.base_events.Server:
    return await asyncio.start_server(lambda r, w: handle(r, w, cfg, limiter), host, port, limit=MAX_HEAD)
