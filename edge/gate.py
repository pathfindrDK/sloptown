"""Password gate for a public *test* deployment (deploy/dev-up.sh --public). Stdlib only.

Keeps the internet out of a stack whose fake Miira lets anyone sign in as anyone.
One login sets an HttpOnly cookie for the whole domain (Domain=<GATE_DOMAIN>), so
the studio, fake Miira, previews and apps (all same-site) share it, iframes
included. Basic auth can't do that: browsers prompt per origin and refuse to
prompt inside cross-origin iframes.

  GET  /_gate/check   Caddy forward_auth: 200 with a valid cookie, else 302 to the login
  GET  /_gate         login form (?next=<url on the gate domain>)
  POST /_gate         password check -> cookie -> redirect to next

Env: GATE_PASSWORD (required), GATE_DOMAIN (e.g. miira-apps.dk), GATE_SECRET
(cookie HMAC key; default derived from the password), GATE_PORT (9100).
"""

import hashlib
import hmac
import html
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

COOKIE = "sloptown_gate"
MAX_AGE = 30 * 24 * 3600


def _secret() -> bytes:
    explicit = os.environ.get("GATE_SECRET")
    if explicit:
        return explicit.encode()
    return hashlib.sha256(b"sloptown-gate:" + os.environ["GATE_PASSWORD"].encode()).digest()


def make_token(now: int | None = None) -> str:
    exp = (now or int(time.time())) + MAX_AGE
    sig = hmac.new(_secret(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(token: str, now: int | None = None) -> bool:
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or int(exp) < (now or int(time.time())):
        return False
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good)


def safe_next(target: str, domain: str) -> str:
    """Only redirect back to the gated domain (no open redirect)."""
    parts = urlsplit(target)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == domain or host.endswith("." + domain)):
        return target
    return f"https://studio.{domain}/"


def cookie_value(header: str) -> str:
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return ""


PAGE = """<!doctype html><html lang=da><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SlopTown · test</title>
<style>body{{font:16px system-ui,sans-serif;background:#f4f4f2;display:grid;place-items:center;min-height:100vh;margin:0}}
form{{background:#fff;padding:28px;border-radius:12px;box-shadow:0 2px 12px #0001;min-width:280px}}
input,button{{font:inherit;padding:10px;width:100%;box-sizing:border-box;margin-top:10px;border-radius:8px;border:1px solid #ccc}}
button{{background:#2b5bd7;color:#fff;border:0;cursor:pointer}}p{{color:#a00;margin:8px 0 0}}</style>
<form method=post><b>SlopTown testmiljø</b><br><small>Adgangskode til testserveren</small>
<input type=hidden name=next value="{next}"><input type=password name=password autofocus required>
<button>Fortsæt</button>{error}</form></html>"""


class Gate(BaseHTTPRequestHandler):
    domain = os.environ.get("GATE_DOMAIN", "localhost")

    def log_message(self, *_):
        pass

    def _send(self, code: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _login_url(self) -> str:
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        uri = self.headers.get("X-Forwarded-Uri") or "/"
        return f"https://{self.domain}/_gate?next=" + quote(f"https://{host}{uri}", safe="")

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/_gate/check":
            if valid_token(cookie_value(self.headers.get("Cookie", ""))):
                return self._send(200, {})
            return self._send(302, {"Location": self._login_url()})
        if url.path == "/_gate":
            nxt = safe_next(parse_qs(url.query).get("next", [""])[0], self.domain)
            return self._page(nxt, "")
        self._send(404, {})

    def do_POST(self):
        if urlsplit(self.path).path != "/_gate":
            return self._send(404, {})
        form = parse_qs(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4096)).decode())
        nxt = safe_next(form.get("next", [""])[0], self.domain)
        if not hmac.compare_digest(form.get("password", [""])[0].encode(), os.environ["GATE_PASSWORD"].encode()):
            time.sleep(1)  # slow down guessing
            return self._page(nxt, "<p>Forkert adgangskode</p>", 401)
        cookie = (f"{COOKIE}={make_token()}; Domain={self.domain}; Path=/; Max-Age={MAX_AGE}; "
                  "Secure; HttpOnly; SameSite=Lax")
        self._send(303, {"Location": nxt, "Set-Cookie": cookie})

    def _page(self, nxt: str, error: str, code: int = 200) -> None:
        body = PAGE.format(next=html.escape(nxt, quote=True), error=error).encode()
        self._send(code, {"Content-Type": "text/html; charset=utf-8"}, body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("GATE_PORT", "9100"))), Gate).serve_forever()
