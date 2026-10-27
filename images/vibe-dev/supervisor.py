#!/usr/local/bin/python3
"""Tiny process supervisor for vibe-dev: start everything, restart what dies.

Restarts with exponential backoff (1s..30s, reset after 60s of uptime) and
forwards SIGTERM/SIGINT to all children, exiting when they are gone.
"""

import os
import signal
import subprocess
import sys
import time

W = os.environ.get("VIBE_WORKSPACE", "/workspace")

PROGRAMS = {
    "agentd": (["/opt/agentd/bin/uvicorn", "vibe_agentd.app:app", "--host", "0.0.0.0", "--port", "4097"], W),
    "opencode": (["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"], W),
    "vite": (["node", "/opt/vibe/node_modules/vite/bin/vite.js", "--host", "0.0.0.0", "--port", "5173", "--strictPort"],
             f"{W}/web"),
    "api": (["uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000", "--app-dir", f"{W}/api",
             "--reload-dir", f"{W}/api"], f"{W}/api"),
}

# A "fra bunden" project has no web app to serve. agentd writes the marker when a
# claimed warm-pool container is converted, so it is re-read on every tick: Vite
# and the API stop then and are never started again.
WEB_PROGRAMS = ("vite", "api")


def web_enabled() -> bool:
    try:
        with open(f"{W}/.vibe-kind") as f:
            return f.read().strip() != "scratch"
    except OSError:
        return True


procs: dict[str, subprocess.Popen] = {}
started: dict[str, float] = {}
backoff: dict[str, float] = {name: 1.0 for name in PROGRAMS}
next_start: dict[str, float] = {name: 0.0 for name in PROGRAMS}
stopping = False


def log(msg: str) -> None:
    print(f"[supervisor] {msg}", file=sys.stderr, flush=True)


def start(name: str) -> None:
    if name in WEB_PROGRAMS and not web_enabled():
        next_start[name] = float("inf")
        return
    cmd, cwd = PROGRAMS[name]
    if not os.path.isdir(cwd):
        log(f"{name}: {cwd} missing, retrying later")
        next_start[name] = time.monotonic() + 10
        return
    procs[name] = subprocess.Popen(cmd, cwd=cwd)
    started[name] = time.monotonic()
    log(f"{name}: started pid {procs[name].pid}")


def stop(signum, _frame) -> None:
    global stopping
    stopping = True
    for p in procs.values():
        if p.poll() is None:
            p.send_signal(signum)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

for name in PROGRAMS:
    start(name)

while True:
    time.sleep(0.5)
    now = time.monotonic()
    if stopping:
        deadline = now + 10
        while any(p.poll() is None for p in procs.values()) and time.monotonic() < deadline:
            time.sleep(0.2)
        for p in procs.values():
            if p.poll() is None:
                p.kill()
        sys.exit(0)
    web = web_enabled()
    for name in PROGRAMS:
        p = procs.get(name)
        if name in WEB_PROGRAMS and not web:
            if p is not None:
                if p.poll() is None:
                    try:
                        p.terminate()
                    except OSError:
                        pass  # already exiting: its directory may be gone
                    log(f"{name}: stopped, project has no web app")
                procs.pop(name)
            next_start[name] = float("inf")
            continue
        if p is not None and p.poll() is None:
            if now - started[name] > 60:
                backoff[name] = 1.0
            continue
        if p is not None:
            log(f"{name}: exited with {p.returncode}, restart in {backoff[name]:.0f}s")
            procs.pop(name)
            next_start[name] = now + backoff[name]
            backoff[name] = min(backoff[name] * 2, 30.0)
        if now >= next_start[name]:
            start(name)
