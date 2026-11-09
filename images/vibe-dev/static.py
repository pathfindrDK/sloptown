#!/usr/local/bin/python3
"""Static file server for a "fra bunden" project's preview (port 5173, where a
webapp runs Vite). Serves the workspace as plain files for the editor-only preview
host: `index.html` for directories, no listings, nothing hidden (any path segment
starting with "." such as .git, .opencode, .data; node_modules; __pycache__), and
nothing outside the workspace through symlinks. Never cached: the teacher sees each
turn's files on reload.

`/api/*` goes to the project's own server (`api/main.py`, uvicorn on :8000) without the
`/api` prefix, as Vite does for a webapp and the runtime does when published; streamed,
so server-sent events work. 502 with a Danish message while there is no server.
"""

import http.client
import json

import os
import posixpath
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

W = os.path.realpath(os.environ.get("VIBE_WORKSPACE", "/workspace"))
HIDDEN_NAMES = {"node_modules", "__pycache__"}
API_PORT = int(os.environ.get("VIBE_API_PORT", "8000"))
API_TIMEOUT = 600  # AI answers stream for a while
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


def api_target(url_path: str) -> str | None:
    """The API's path for a request to /api/..., or None if it is not an API request."""
    parts = urlsplit(url_path)
    if parts.path != "/api" and not parts.path.startswith("/api/"):
        return None
    return (parts.path[4:] or "/") + (f"?{parts.query}" if parts.query else "")


def resolve(url_path: str) -> str | None:
    """The file to serve for a URL path, or None (404)."""
    parts = [p for p in unquote(urlsplit(url_path).path).split("/") if p]
    if any(p.startswith(".") or p in HIDDEN_NAMES or "\\" in p or "\0" in p for p in parts):
        return None
    full = os.path.realpath(os.path.join(W, *parts))
    if full != W and not full.startswith(W + os.sep):
        return None
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    return full if os.path.isfile(full) else None


class Handler(SimpleHTTPRequestHandler):
    server_version = "vibe-static"
    sys_version = ""

    def do_GET(self):
        self.proxy() if api_target(self.path) else super().do_GET()

    def do_HEAD(self):
        self.proxy() if api_target(self.path) else super().do_HEAD()

    def do_POST(self):
        self.proxy() if api_target(self.path) else self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_POST

    def proxy(self):
        target = api_target(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        if body is not None:
            headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPConnection("127.0.0.1", API_PORT, timeout=API_TIMEOUT)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError:
            conn.close()
            msg = json.dumps({"error": "no_server", "message": "Projektets server kører ikke (api/main.py mangler eller har en fejl)."})
            data = msg.encode()
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_HEADERS and k.lower() not in ("server", "date"):  # send_response adds them
                    self.send_header(k, v)
            # HTTP/1.0 to the client: the body ends when the connection closes, so a
            # streamed answer can be passed on as it arrives.
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                while chunk := resp.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser went away
        finally:
            conn.close()

    def send_head(self):
        path = resolve(self.path)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Ikke fundet")
            return None
        # A directory URL without its slash would break the page's relative links.
        if os.path.basename(path) == "index.html" and not urlsplit(self.path).path.endswith(("/", ".html")):
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", urlsplit(self.path).path + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "Ikke fundet")
            return None
        fs = os.fstat(f.fileno())
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(fs.st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        return f

    def list_directory(self, path):  # never reached (send_head serves files only)
        self.send_error(HTTPStatus.NOT_FOUND, "Ikke fundet")
        return None

    def log_message(self, fmt, *args):
        sys.stderr.write("[static] " + (fmt % args) + "\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
