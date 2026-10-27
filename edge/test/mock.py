"""Mock control plane and mock app for edge/test/validate.sh.

  python mock.py cp   -> :8000, answers forward_auth like the control plane
  python mock.py app  -> :9000, echoes the request it received as JSON
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class CP(BaseHTTPRequestHandler):
    def do_GET(self):
        h = self.headers
        if self.path.startswith("/edge/auth/") and (h.get("Upgrade") or "").lower() == "websocket":
            return self.reply(403, {})  # like uvicorn: a WebSocket handshake on an HTTP route
        if self.path.startswith("/edge/auth/app"):
            if "vibe_app=ok" not in (h.get("Cookie") or ""):
                return self.reply(302, {"Location": "http://studio.sloptown.localhost:8080/auth/login"})
            return self.reply(200, {"X-Vibe-Edge-Secret": "edge-secret", "X-Vibe-User": "member-1",
                                    "X-Vibe-Role": "viewer", "X-Vibe-Name": "Mette%20Hansen",
                                    "X-Vibe-Classes": "7.A", "X-Vibe-Upstream": "mockapp:9000"})
        if self.path.startswith("/edge/auth/preview"):
            role = h.get("X-Vibe-View-As") or "owner"
            return self.reply(200, {"X-Vibe-Edge-Secret": "edge-secret", "X-Vibe-User": "dev-" + role,
                                    "X-Vibe-Role": role, "X-Vibe-Upstream": "mockapp:9000"})
        if self.path.startswith("/edge/session"):
            return self.reply(302, {"Location": "/", "Set-Cookie": "vibe_app=ok; HttpOnly", "X-Seen-Path": self.path})
        return self.reply(200, {"Content-Type": "application/json"}, json.dumps({"cp": self.path}).encode())

    do_POST = do_GET

    def reply(self, code, headers, body=b""):
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class App(CP):
    def do_GET(self):
        body = json.dumps({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}).encode()
        self.reply(200, {"Content-Type": "application/json"}, body)


role = sys.argv[1]
ThreadingHTTPServer(("0.0.0.0", 8000 if role == "cp" else 9000), CP if role == "cp" else App).serve_forever()
