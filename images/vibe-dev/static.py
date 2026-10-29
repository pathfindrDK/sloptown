#!/usr/local/bin/python3
"""Static file server for a "fra bunden" project's preview (port 5173, where a
webapp runs Vite). Serves the workspace as plain files for the editor-only preview
host: `index.html` for directories, no listings, nothing hidden (any path segment
starting with "." such as .git, .opencode, .data; node_modules; __pycache__), and
nothing outside the workspace through symlinks. Never cached: the teacher sees each
turn's files on reload.
"""

import os
import posixpath
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

W = os.path.realpath(os.environ.get("VIBE_WORKSPACE", "/workspace"))
HIDDEN_NAMES = {"node_modules", "__pycache__"}


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
