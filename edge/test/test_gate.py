"""Unit tests for edge/gate.py (stdlib server; run with plain pytest)."""

import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ["GATE_PASSWORD"] = "hemmelig"
os.environ["GATE_DOMAIN"] = "miira-apps.dk"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gate  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), gate.Gate)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _req(url, data=None, headers=None):
    try:
        r = OPENER.open(urllib.request.Request(url, data=data, headers=headers or {}))
        return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def test_tokens_expire_and_are_signed():
    t = gate.make_token(now=1000)
    assert gate.valid_token(t, now=1001)
    assert not gate.valid_token(t, now=1000 + gate.MAX_AGE + 1)
    exp, sig = t.split(".")
    assert not gate.valid_token(f"{int(exp) + 99}.{sig}", now=1001)
    assert not gate.valid_token("garbage")


def test_next_only_on_the_gated_domain():
    assert gate.safe_next("https://preview-x.miira-apps.dk/a?b=1", "miira-apps.dk").startswith("https://preview-x.")
    assert gate.safe_next("https://evil.dk/", "miira-apps.dk") == "https://studio.miira-apps.dk/"
    assert gate.safe_next("https://miira-apps.dk.evil.dk/", "miira-apps.dk") == "https://studio.miira-apps.dk/"
    assert gate.safe_next("http://studio.miira-apps.dk/", "miira-apps.dk") == "https://studio.miira-apps.dk/"


def test_check_login_and_cookie_flow():
    srv, base = _server()
    try:
        code, h, _ = _req(f"{base}/_gate/check", headers={"X-Forwarded-Host": "studio.miira-apps.dk", "X-Forwarded-Uri": "/p/1"})
        assert code == 302 and h["Location"].startswith("https://miira-apps.dk/_gate?next=https%3A%2F%2Fstudio")

        code, _, body = _req(f"{base}/_gate", data=b"password=wrong&next=https://studio.miira-apps.dk/")
        assert code == 401 and "Forkert" in body

        code, h, _ = _req(f"{base}/_gate", data=b"password=hemmelig&next=https%3A%2F%2Fstudio.miira-apps.dk%2Fp%2F1")
        assert code == 303 and h["Location"] == "https://studio.miira-apps.dk/p/1"
        cookie = h["Set-Cookie"]
        assert "Domain=miira-apps.dk" in cookie and "HttpOnly" in cookie and "Secure" in cookie
        value = cookie.split(";")[0]

        code, _, _ = _req(f"{base}/_gate/check", headers={"Cookie": f"other=1; {value}"})
        assert code == 200
    finally:
        srv.shutdown()
