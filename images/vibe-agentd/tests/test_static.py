"""vibe-static (images/vibe-dev/static.py): a "fra bunden" preview's files, and /api/* to its server."""

import http.client
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "vibe-dev" / "static.py"


class FakeApi(BaseHTTPRequestHandler):
    def _reply(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(3):
                self.wfile.write(f"data: {i}\n\n".encode())
                self.wfile.flush()
            return
        data = json.dumps({"method": self.command, "path": self.path, "body": body.decode(),
                           "user": self.headers.get("X-Vibe-User-Id")}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "a=1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _reply

    def log_message(self, *a):
        pass


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def static(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location("vibe_static", STATIC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "index.html").write_text("<h1>side</h1>")
    (tmp_path / ".data").mkdir()
    (tmp_path / ".data" / "app.sqlite").write_text("x")
    api = serve(FakeApi)
    monkeypatch.setattr(mod, "API_PORT", api.server_address[1])
    srv = serve(mod.Handler)
    yield mod, srv.server_address[1], api
    srv.shutdown()
    api.shutdown()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_files_and_hidden(static):
    _, port, _ = static
    assert call(port, "GET", "/")[2] == b"<h1>side</h1>"
    assert call(port, "GET", "/.data/app.sqlite")[0] == 404
    assert call(port, "POST", "/index.html")[0] == 405


def test_api_is_forwarded_without_prefix(static):
    _, port, _ = static
    status, headers, body = call(port, "POST", "/api/svar?x=1", body=b'{"a":1}',
                                 headers={"Content-Type": "application/json", "X-Vibe-User-Id": "u1"})
    assert status == 201 and headers["Set-Cookie"] == "a=1"
    assert json.loads(body) == {"method": "POST", "path": "/svar?x=1", "body": '{"a":1}', "user": "u1"}
    assert json.loads(call(port, "DELETE", "/api")[2])["path"] == "/"
    assert call(port, "GET", "/apiary")[0] == 404, "only /api and /api/... are the server's"


def test_api_streams(static):
    _, port, _ = static
    status, _, body = call(port, "GET", "/api/stream")
    assert status == 200 and body == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_no_server_is_a_danish_502(static):
    mod, port, api = static
    api.shutdown()
    api.server_close()
    status, _, body = call(port, "GET", "/api/x")
    assert status == 502 and json.loads(body)["error"] == "no_server"


def test_sdk_reference_is_the_same_in_both_agent_guides():
    """Both kinds of project have the same server SDK; the guides share one copy of its reference."""
    import re

    root = Path(__file__).resolve().parents[3]
    pat = re.compile(r"<!-- shared: sdk-backend[^>]*-->\n(.*?)<!-- /shared: sdk-backend -->", re.S)
    blocks = [pat.findall((root / d / "AGENTS.md").read_text()) for d in ("template", "template-scratch")]
    assert all(len(b) == 1 for b in blocks), "one shared block per guide"
    assert blocks[0] == blocks[1], "edit the block in template/AGENTS.md and copy it to template-scratch/AGENTS.md"
