#!/usr/local/bin/python3
"""Tiny process supervisor for vibe-dev: start everything, restart what dies.

Restarts with exponential backoff (1s..30s, reset after 60s of uptime) and
forwards SIGTERM/SIGINT to all children, exiting when they are gone.
"""

import os
import signal
import subprocess
import sys
import threading
import time

W = os.environ.get("VIBE_WORKSPACE", "/workspace")

PROGRAMS = {
    "agentd": (["/opt/agentd/bin/uvicorn", "vibe_agentd.app:app", "--host", "0.0.0.0", "--port", "4097"], W),
    "opencode": (["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"], W),
    "vite": (["node", "/opt/vibe/node_modules/vite/bin/vite.js", "--host", "0.0.0.0", "--port", "5173", "--strictPort"],
             f"{W}/web"),
    "api": (["uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000", "--app-dir", f"{W}/api",
             "--reload-dir", f"{W}/api"], f"{W}/api"),
    # A "fra bunden" project's preview: its files as they are, on Vite's port.
    "static": (["/usr/local/bin/vibe-static", "5173"], W),
}

# A "fra bunden" project has no Vite app: its page is served as plain files (static,
# which also forwards /api/* to the API like Vite does). It may have a server too: the
# API runs in both kinds of project, as soon as api/main.py exists. agentd writes the
# marker when a claimed warm-pool container is converted, so it is re-read on every
# tick: Vite stops then and is never started again, static starts on the freed port,
# and the API is restarted, since the template's api/ was deleted under it.
WEB_PROGRAMS = ("vite",)
SCRATCH_PROGRAMS = ("static",)
RESTART_ON_KIND_CHANGE = ("api",)
API_MAIN = f"{W}/api/main.py"


def web_enabled() -> bool:
    try:
        with open(f"{W}/.vibe-kind") as f:
            return f.read().strip() != "scratch"
    except OSError:
        return True


# The app's own processes also log to /tmp/vibe-logs/<name>.log, where the studio
# agent can read why the API won't start (container logs are out of its reach; it
# once answered a teacher's "fejl 500" with "the tests pass"). tmpfs, so a restart
# clears them; LOG_MAX bytes per file plus one rotated ".1".
LOG_DIR = os.environ.get("VIBE_LOG_DIR", "/tmp/vibe-logs")
LOGGED = ("api", "vite")
LOG_MAX = 1024 * 1024


def tee(name: str, stream) -> None:
    """Copy a child's output to our stderr (the container log) and its log file."""
    path = os.path.join(LOG_DIR, f"{name}.log")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        f = open(path, "ab")
    except OSError:
        f = None
    for line in iter(stream.readline, b""):
        sys.stderr.buffer.write(line)
        sys.stderr.buffer.flush()
        if f is None:
            continue
        try:
            f.write(line)
            f.flush()
            if f.tell() > LOG_MAX:
                f.close()
                os.replace(path, path + ".1")
                f = open(path, "ab")
        except OSError:
            f = None
    stream.close()
    if f is not None:
        f.close()


procs: dict[str, subprocess.Popen] = {}
started: dict[str, float] = {}
backoff: dict[str, float] = {name: 1.0 for name in PROGRAMS}
next_start: dict[str, float] = {name: 0.0 for name in PROGRAMS}
# Programs told to stop because the project's kind changed: name -> (process, SIGKILL
# deadline). Vite and `uvicorn --reload` can hang on SIGTERM once their directory is
# gone, and Vite would keep :5173 from vibe-static.
dying: dict[str, tuple[subprocess.Popen, float]] = {}
KILL_AFTER = 5.0
stopping = False


def log(msg: str) -> None:
    print(f"[supervisor] {msg}", file=sys.stderr, flush=True)


def wanted(name: str, web: bool) -> bool:
    return not ((name in WEB_PROGRAMS and not web) or (name in SCRATCH_PROGRAMS and web))


def start(name: str) -> None:
    if not wanted(name, web_enabled()):
        next_start[name] = float("inf")
        return
    cmd, cwd = PROGRAMS[name]
    if name == "api" and not os.path.isfile(API_MAIN):
        # Normal in a "fra bunden" project without a server: start when the agent adds one.
        if web_enabled():
            log(f"api: {API_MAIN} missing, retrying later")
        next_start[name] = time.monotonic() + 5
        return
    if not os.path.isdir(cwd):
        log(f"{name}: {cwd} missing, retrying later")
        next_start[name] = time.monotonic() + 10
        return
    # Own process group, so a stop reaches its children too (esbuild, uvicorn's workers).
    if name in LOGGED:
        procs[name] = subprocess.Popen(cmd, cwd=cwd, start_new_session=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(target=tee, args=(name, procs[name].stdout), daemon=True).start()
    else:
        procs[name] = subprocess.Popen(cmd, cwd=cwd, start_new_session=True)
    started[name] = time.monotonic()
    log(f"{name}: started pid {procs[name].pid}")


def signal_group(p: subprocess.Popen, signum: int) -> bool:
    """False if the signal was refused. Under plain crun on an AppArmor host
    (containers-default stacked with crun) signals between the container's own
    processes are denied; gVisor, which Vibes run on, allows them."""
    try:
        os.killpg(p.pid, signum)
    except ProcessLookupError:
        pass  # already gone
    except OSError as e:
        log(f"pid {p.pid}: signal {signum} refused ({e})")
        return False
    return True


def stop(signum, _frame) -> None:
    global stopping
    stopping = True
    for p in [*procs.values(), *(d[0] for d in dying.values())]:
        if p.poll() is None:
            signal_group(p, signum)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

for name in PROGRAMS:
    start(name)
last_web = web_enabled()

while True:
    time.sleep(0.5)
    now = time.monotonic()
    if stopping:
        deadline = now + 10
        while any(p.poll() is None for p in procs.values()) and time.monotonic() < deadline:
            time.sleep(0.2)
        for p in [*procs.values(), *(d[0] for d in dying.values())]:
            if p.poll() is None:
                signal_group(p, signal.SIGKILL)
        sys.exit(0)
    for name, (p, deadline) in list(dying.items()):
        if p.poll() is not None:
            dying.pop(name)
        elif now > deadline:
            log(f"{name}: did not stop, killing")
            if signal_group(p, signal.SIGKILL):
                dying[name] = (p, now + KILL_AFTER)
            else:
                # Can't stop it: don't hold the others back. The change takes effect
                # at the container's next start, when the entrypoint sees the marker.
                log(f"{name}: left running until the container restarts")
                dying.pop(name)
    web = web_enabled()
    if web != last_web:
        last_web = web
        for name in RESTART_ON_KIND_CHANGE:
            p = procs.pop(name, None)
            if p is not None and p.poll() is None and signal_group(p, signal.SIGTERM):
                dying[name] = (p, now + KILL_AFTER)
                log(f"{name}: restarting, the kind of project changed")
            next_start[name] = 0.0
    for name in PROGRAMS:
        p = procs.get(name)
        if not wanted(name, web):
            if p is not None:
                if p.poll() is None and signal_group(p, signal.SIGTERM):
                    dying[name] = (p, now + KILL_AFTER)
                    log(f"{name}: stopping, not used by this kind of project")
                procs.pop(name)
            next_start[name] = float("inf")
            continue
        if next_start[name] == float("inf"):
            next_start[name] = 0.0  # e.g. static, when the marker appeared after start
        if p is not None and p.poll() is None:
            if now - started[name] > 60:
                backoff[name] = 1.0
            continue
        if p is not None:
            log(f"{name}: exited with {p.returncode}, restart in {backoff[name]:.0f}s")
            procs.pop(name)
            next_start[name] = now + backoff[name]
            backoff[name] = min(backoff[name] * 2, 30.0)
        # Wait for what was stopped to let go of its port (static takes Vite's).
        if now >= next_start[name] and not dying:
            start(name)
