"""vibe-agentd: file, git and event helper inside vibe-dev.

Only the control plane calls it (bearer VIBE_PROJECT_TOKEN). See the
"vibe-agentd" section in docs/internal-api.md.
"""

import asyncio
import fnmatch
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

WORKSPACE = Path(os.environ.get("VIBE_WORKSPACE", "/workspace"))
TOKEN = os.environ.get("VIBE_PROJECT_TOKEN", "")
POLL_SECONDS = float(os.environ.get("VIBE_AGENTD_POLL", "1.0"))
# Platform copies of the locked files, and sha256 hashes of older platform
# versions that may still be published (see images/AGENTS.md).
PLATFORM_DIR = Path(os.environ.get("VIBE_PLATFORM_DIR", "/opt/template"))
PLATFORM_ALLOWLIST = Path(os.environ.get("VIBE_PLATFORM_ALLOWLIST", "/opt/vibe/platform-allowlist.txt"))
PLATFORM_FILES = ["AGENTS.md", "web/package.json", "web/package-lock.json", "web/vite.config.ts",
                  "api/requirements.lock"]
# A "fra bunden" project: no web app, only AGENTS.md is the platform's. The kind
# lives in an untracked marker the entrypoint and the supervisor read too.
SCRATCH_DIR = Path(os.environ.get("VIBE_SCRATCH_DIR", "/opt/template-scratch"))
KIND_FILE = ".vibe-kind"
RESET_SETTLE_SECONDS = float(os.environ.get("VIBE_RESET_SETTLE", "2.0"))  # > two supervisor ticks
# The design system ("style") and colour palette of a webapp: catalog, per-style
# files and generated palettes from template-design/. The choice lives in an
# untracked marker "<style> <palette>" that the entrypoint reads too. No marker
# means an older Vibe with no design files (Miira look via theme.css).
DESIGN_DIR = Path(os.environ.get("VIBE_DESIGN_DIR", "/opt/template-design"))
DESIGN_FILE = ".vibe-design"
DESIGN_TARGETS = {"DESIGN.md": "DESIGN.md", "design.css": "web/src/design.css"}  # style dir -> workspace
PALETTE_TARGET = "web/src/palette.css"
STARTER_FILES = ["web/src/main.tsx", "web/src/App.tsx", "web/src/styles.css"]
EXCLUDE = ("# managed by vibe-dev\n.data/\n.opencode/\n.vibe-kind\n.vibe-design\n"
           "node_modules\nweb/dist/\n__pycache__/\n")

HIDDEN = [".git", ".git/**", "node_modules", "node_modules/**", "*/node_modules", "*/node_modules/**",
          ".opencode", ".opencode/**", ".data", ".data/**", "web/dist", "web/dist/**", KIND_FILE,
          DESIGN_FILE, "**/__pycache__", "**/__pycache__/**"]
LOCKED = ["AGENTS.md", "web/package.json", "web/package-lock.json", "web/vite.config.ts",
          "api/requirements.lock", "DESIGN.md", "web/src/design.css", "web/src/palette.css",
          ".opencode/**", ".data/**"]

LANGUAGES = {
    ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
    ".py": "python", ".json": "json", ".css": "css", ".html": "html", ".md": "markdown",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql", ".svg": "xml", ".txt": "text",
}

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Vibe", "GIT_AUTHOR_EMAIL": "vibe@sloptown.invalid",
    "GIT_COMMITTER_NAME": "SlopTown", "GIT_COMMITTER_EMAIL": "vibe@sloptown.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}

app = FastAPI(title="vibe-agentd")


def _match(path: str, patterns: list[str]) -> bool:
    # fnmatch's "*" also matches "/", so "x/**" covers the whole subtree.
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def is_hidden(path: str) -> bool:
    return _match(path, HIDDEN) or any(
        _match("/".join(path.split("/")[: i + 1]), HIDDEN) for i in range(path.count("/"))
    )


def is_locked(path: str) -> bool:
    return _match(path, LOCKED)


def auth(authorization: str = Header(default="")) -> None:
    if not TOKEN:
        return  # unconfigured (tests, local poking); the container always sets it
    if not hmac.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(401, "unauthorized")


def git(*args: str, input: bytes | None = None, check: bool = True) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args], cwd=WORKSPACE, input=input,
        capture_output=True, env={**os.environ, **GIT_ENV},
    )
    if check and proc.returncode != 0:
        raise HTTPException(500, f"git {args[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def safe_path(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise HTTPException(400, "bad path")
    norm = str(p)
    if is_hidden(norm):
        raise HTTPException(404, "not found")
    return norm


def resolve_sha(sha: str) -> str:
    out = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False).decode().strip()
    if not out:
        raise HTTPException(404, "unknown sha")
    return out


def has_head() -> bool:
    return bool(git("rev-parse", "--verify", "--quiet", "HEAD", check=False).strip())


def changed_paths() -> set[str]:
    """Uncommitted changes plus whatever the last commit touched, if it was an AI turn.

    The template seed commit and platform updates carry no Vibe-Thread trailer;
    they would otherwise mark every file in a fresh project as changed.
    """
    out = set()
    for line in git("status", "--porcelain=v1", "-z", "--untracked-files=all").decode().split("\0"):
        if len(line) > 3:
            out.add(line[3:])
    if has_head() and "Vibe-Thread:" in git("log", "-1", "--format=%(trailers:only)", "HEAD").decode():
        out |= set(filter(None, git("show", "--name-only", "--format=", "-z", "HEAD").decode().split("\0")))
    return out


def walk() -> list[tuple[str, int, float]]:
    files = []
    for root, dirs, names in os.walk(WORKSPACE):
        rel_root = os.path.relpath(root, WORKSPACE)
        rel_root = "" if rel_root == "." else rel_root + "/"
        dirs[:] = [d for d in dirs if not is_hidden(rel_root + d)]
        for n in names:
            rel = rel_root + n
            if is_hidden(rel):
                continue
            try:
                st = os.lstat(os.path.join(root, n))
            except FileNotFoundError:
                continue
            files.append((rel, st.st_size, st.st_mtime))
    return sorted(files)


def parse_trailers(body: str) -> dict[str, str]:
    out = {}
    for line in body.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.startswith("Vibe-"):
                out[k.strip()] = v.strip()
    return out


@app.get("/health")
def health() -> dict:
    return {"ok": True, "head": git("rev-parse", "HEAD", check=False).decode().strip() or None}


@app.get("/files", dependencies=[Depends(auth)])
def list_files() -> dict:
    changed = changed_paths()
    return {"files": [
        {"path": p, "size": s, "locked": is_locked(p), "changed": p in changed} for p, s, _ in walk()
    ]}


@app.get("/files/{path:path}", dependencies=[Depends(auth)])
def read_file(path: str, sha: str | None = None) -> dict:
    path = safe_path(path)
    if sha:
        proc = subprocess.run(["git", "show", f"{resolve_sha(sha)}:{path}"], cwd=WORKSPACE, capture_output=True)
        if proc.returncode != 0:
            raise HTTPException(404, "not found")
        data = proc.stdout
    else:
        f = WORKSPACE / path
        if not f.is_file():
            raise HTTPException(404, "not found")
        data = f.read_bytes()
    if b"\0" in data[:8000]:
        content, language = None, "binary"
    else:
        content, language = data.decode("utf-8", errors="replace"), LANGUAGES.get(Path(path).suffix, "text")
    return {"path": path, "content": content, "locked": is_locked(path), "language": language}


SEP = "\x1e"


def commit_info(sha: str) -> dict:
    raw = git("show", "-s", f"--format=%H{SEP}%s{SEP}%an{SEP}%aI{SEP}%B", sha).decode()
    full, subject, author, date, body = raw.split(SEP, 4)
    trailers = parse_trailers(body)
    files = list(filter(None, git("show", "--name-only", "--format=", "-z", full).decode().split("\0")))
    return {
        "sha": full, "message": subject, "authorName": trailers.get("Vibe-Author", author),
        "threadId": trailers.get("Vibe-Thread"), "threadTitle": None, "createdAt": date,
        "files": [f for f in files if not is_hidden(f)],
    }


@app.get("/history", dependencies=[Depends(auth)])
def history(limit: int = 200) -> dict:
    if not has_head():
        return {"commits": []}
    shas = git("rev-list", f"--max-count={limit}", "HEAD").decode().split()
    return {"commits": [commit_info(s) for s in shas]}


STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "modified"}


@app.get("/diff/{sha}", dependencies=[Depends(auth)])
def diff(sha: str) -> dict:
    sha = resolve_sha(sha)
    parent = git("rev-parse", "--verify", "--quiet", f"{sha}^", check=False).decode().strip()
    base = parent or git("hash-object", "-t", "tree", "/dev/null").decode().strip()
    files = []
    parts = git("diff", "--name-status", "-z", base, sha).decode().split("\0")
    i = 0
    while i < len(parts) and parts[i]:
        code = parts[i][0]
        if code in "RC":
            path = parts[i + 2]
            i += 3
        else:
            path = parts[i + 1]
            i += 2
        if is_hidden(path):
            continue
        patch = git("diff", base, sha, "--", path).decode(errors="replace")
        files.append({"path": path, "status": STATUS.get(code, "modified"), "patch": patch})
    return {"sha": sha, "files": files}


class CommitIn(BaseModel):
    message: str
    trailers: dict[str, str] = {}


def _commit(message: str, trailers: dict[str, str]) -> dict | None:
    git("add", "-A")
    if has_head() and not git("diff", "--cached", "--name-only").strip():
        return None
    msg = message.strip() or "Ændringer"
    if trailers:
        msg += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    git("commit", "-q", "--allow-empty-message", "-F", "-", input=msg.encode())
    return commit_info("HEAD")


@app.post("/commit", dependencies=[Depends(auth)])
def commit(body: CommitIn) -> dict:
    c = _commit(body.message, body.trailers)
    return c if c else {"sha": None, "files": []}


class RestoreIn(BaseModel):
    sha: str
    message: str = ""
    trailers: dict[str, str] = {}


@app.post("/restore", dependencies=[Depends(auth)])
def restore(body: RestoreIn) -> dict:
    sha = resolve_sha(body.sha)
    # Commit stray edits first so restore never destroys work silently.
    _commit("Ugemte ændringer før gendannelse", body.trailers)
    git("read-tree", "-u", "--reset", sha)
    msg = body.message or f"Gendan version {sha[:7]}"
    c = _commit(msg, {**body.trailers, "Vibe-Restored-From": sha})
    return c or commit_info("HEAD")


class ExportIn(BaseModel):
    sha: str


def _allowlist() -> set[str]:
    """Lines "<sha256>  <path>" of accepted older platform-file versions."""
    try:
        lines = PLATFORM_ALLOWLIST.read_text().splitlines()
    except FileNotFoundError:
        return set()
    return {" ".join(line.split()[:2]) for line in lines if line.strip() and not line.startswith("#")}


def kind() -> str:
    try:
        return "scratch" if (WORKSPACE / KIND_FILE).read_text().strip() == "scratch" else "webapp"
    except OSError:
        return "webapp"


def platform_sources() -> dict[str, Path]:
    """Workspace path -> the platform's copy, for every platform-owned file of this project."""
    if kind() == "scratch":
        return {"AGENTS.md": SCRATCH_DIR / "AGENTS.md"}
    out = {path: PLATFORM_DIR / path for path in PLATFORM_FILES}
    if d := design():
        style, palette = d
        out |= {target: DESIGN_DIR / "styles" / style / name for name, target in DESIGN_TARGETS.items()}
        out[PALETTE_TARGET] = DESIGN_DIR / "palettes" / f"{palette}.css"
    return out


def locked_violations(sha: str) -> list[str]:
    """Platform-owned files whose content at `sha` is neither the platform copy nor allowlisted."""
    allowed = _allowlist()
    bad = []
    for path, platform in platform_sources().items():
        expected = platform.read_bytes() if platform.is_file() else None
        proc = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=WORKSPACE, capture_output=True)
        actual = proc.stdout if proc.returncode == 0 else None
        if actual == expected:
            continue
        if actual is not None and f"{hashlib.sha256(actual).hexdigest()} {path}" in allowed:
            continue
        bad.append(path)
    return bad


@app.post("/export", dependencies=[Depends(auth)], response_model=None)
def export(body: ExportIn) -> StreamingResponse | JSONResponse:
    sha = resolve_sha(body.sha)
    if bad := locked_violations(sha):
        return JSONResponse({"paths": bad}, status_code=422)
    proc = subprocess.Popen(["git", "archive", "--format=tar", sha], cwd=WORKSPACE, stdout=subprocess.PIPE)

    def gen():
        assert proc.stdout
        while chunk := proc.stdout.read(65536):
            yield chunk
        proc.wait()

    return StreamingResponse(gen(), media_type="application/x-tar", headers={"X-Vibe-Sha": sha})


def catalog() -> dict:
    return json.loads((DESIGN_DIR / "catalog.json").read_text())


def design() -> tuple[str, str] | None:
    """(style, palette) from the marker, if it names ones the catalog knows."""
    try:
        style, palette = (WORKSPACE / DESIGN_FILE).read_text().split()[:2]
    except (OSError, ValueError):
        return None
    c = catalog()
    if style in {s["id"] for s in c["styles"]} and palette in {p["id"] for p in c["palettes"]}:
        return style, palette
    return None


def only_platform_commits() -> bool:
    """True while nobody but the platform has changed the workspace (every commit
    carries `Vibe-Author: SlopTown` and nothing is uncommitted)."""
    if has_head():
        for sha in git("rev-list", "HEAD").decode().split():
            if "Vibe-Author: SlopTown" not in git("log", "-1", "--format=%(trailers:only)", sha).decode():
                return False
    return not git("status", "--porcelain").strip()


def write_platform_file(path: str, data: bytes) -> None:
    target = WORKSPACE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)  # read-only copies: replace, don't open for writing
    target.write_bytes(data)
    os.chmod(target, 0o444)


def ensure_design_imports(main: Path) -> None:
    """An older Miira-look Vibe getting its first palette: its main.tsx predates
    palette.css and design.css, so import them before the app's own styles."""
    if not main.is_file():
        return
    text = main.read_text()
    missing = [f'import "./{n}";\n' for n in ("palette.css", "design.css") if f'"./{n}"' not in text]
    if not missing:
        return
    anchor = 'import "./styles.css";'
    i = text.find(anchor)
    i = i if i >= 0 else 0
    main.write_text(text[:i] + "".join(missing) + text[i:])


class DesignIn(BaseModel):
    style: str | None = None
    palette: str | None = None
    # Also lay the style's starter files over the template: only for a project
    # nobody has worked on yet (right after it is created).
    starter: bool = False


@app.get("/design", dependencies=[Depends(auth)])
def get_design() -> dict:
    d = design()
    return {"style": d[0], "palette": d[1]} if d else {"style": None, "palette": None}


@app.post("/design", dependencies=[Depends(auth)])
def set_design(body: DesignIn) -> dict:
    """Set the design system and/or palette: write the platform's design files and
    the marker, and commit as the platform. Never touches the agent's code, except
    the starter files (`starter`, fresh projects only) and adding the palette
    imports to an older Vibe's main.tsx."""
    if kind() == "scratch":
        raise HTTPException(409, "a scratch project has no design")
    c = catalog()
    styles = {s["id"]: s for s in c["styles"]}
    palettes = {p["id"]: p for p in c["palettes"]}
    current = design()
    style = body.style or (current[0] if current else "miira")
    palette = body.palette or (current[1] if current else "miira")
    if style not in styles or palette not in palettes:
        raise HTTPException(400, "unknown style or palette")
    if body.starter and not only_platform_commits():
        raise HTTPException(409, "project already has work")
    if body.starter:
        for path in STARTER_FILES:
            src = DESIGN_DIR / "styles" / style / path
            if src.is_file():
                (WORKSPACE / path).parent.mkdir(parents=True, exist_ok=True)
                (WORKSPACE / path).write_bytes(src.read_bytes())
    elif current is None:
        ensure_design_imports(WORKSPACE / "web" / "src" / "main.tsx")
    (WORKSPACE / DESIGN_FILE).write_text(f"{style} {palette}\n")
    for path, src in platform_sources().items():
        if path in (PALETTE_TARGET, *DESIGN_TARGETS.values()):
            write_platform_file(path, src.read_bytes())
    s_name, p_name = styles[style]["name"]["da"], palettes[palette]["name"]["da"]
    if body.starter:
        msg = f"Start med {s_name} og farverne {p_name}"
    elif current and current[0] == style:
        msg = f"Farver: {p_name}"
    else:
        msg = f"Design: {s_name} med farverne {p_name}"
    commit = _commit(msg, {"Vibe-Author": "SlopTown"})
    return {"style": style, "palette": palette, **(commit or commit_info("HEAD"))}


@app.get("/kind", dependencies=[Depends(auth)])
def get_kind() -> dict:
    return {"kind": kind()}


@app.post("/reset-scratch", dependencies=[Depends(auth)])
def reset_scratch() -> dict:
    """Turn a freshly claimed webapp workspace into an empty "fra bunden" project.

    Only while nobody but the platform has committed (every commit carries
    `Vibe-Author: SlopTown`), so it can never throw a teacher's work away. The
    history starts over, because a scratch project has nothing to do with the
    template it was pre-warmed with. Idempotent.
    """
    if kind() == "scratch":
        return {"kind": "scratch", **commit_info("HEAD")}
    if not only_platform_commits():
        raise HTTPException(409, "project already has work")
    # Marker first: the supervisor polls it and stops Vite and the API, which must
    # not be running in web/ and api/ while those are deleted under them.
    (WORKSPACE / KIND_FILE).write_text("scratch\n")
    time.sleep(RESET_SETTLE_SECONDS)
    (WORKSPACE / DESIGN_FILE).unlink(missing_ok=True)
    for entry in WORKSPACE.iterdir():
        if entry.name in (".opencode", KIND_FILE):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.copytree(SCRATCH_DIR, WORKSPACE, dirs_exist_ok=True)
    (WORKSPACE / ".data").mkdir(exist_ok=True)
    git("init", "-q", "-b", "main")
    git("config", "core.fileMode", "false")
    (WORKSPACE / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / ".git" / "info" / "exclude").write_text(EXCLUDE)
    os.chmod(WORKSPACE / "AGENTS.md", 0o444)
    git("add", "-A")
    git("commit", "-q", "-F", "-", input=b"Start fra bunden\n\nVibe-Author: SlopTown")
    return {"kind": "scratch", **commit_info("HEAD")}


@app.get("/events", dependencies=[Depends(auth)])
async def events() -> StreamingResponse:
    async def gen():
        prev = {p: (s, m) for p, s, m in await asyncio.to_thread(walk)}
        yield "event: ping\ndata: {}\n\n"
        ticks = 0
        while True:
            await asyncio.sleep(POLL_SECONDS)
            cur = {p: (s, m) for p, s, m in await asyncio.to_thread(walk)}
            paths = sorted({p for p in cur.keys() | prev.keys() if cur.get(p) != prev.get(p)})
            prev = cur
            if paths:
                yield f"event: files.changed\ndata: {json.dumps({'paths': paths})}\n\n"
            ticks += 1
            if ticks * POLL_SECONDS >= 15:
                ticks = 0
                yield "event: ping\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
