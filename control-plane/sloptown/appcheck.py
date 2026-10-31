"""Pre-publish check: a static scan of a Vibe's exported tree for code that would hurt
the platform or its users once published.

Teachers who build Vibes don't read code, so publish runs this scan and, when it finds
something, has the AI fix it before the app goes out (`prepublish.py`). The rules aim
at what has actually hurt us: request loops without a delay (one app polling ~10/s
used up a school's whole rate limit and took the studio preview down with it), and
the classic security slips (keys in code, eval, SQL built from strings, shell=True).

Every rule needs a strong signal (a network call inside a loop that has no delay,
not just "a loop"), because a false positive costs the teacher a fix turn. A
non-security finding can be marked as intended with a `vibe-check: ok` comment on
the line or the line before; secrets can't.
"""

import io
import re
import tarfile
from dataclasses import dataclass

MAX_FILE_BYTES = 512 * 1024
MAX_FINDINGS = 30
# Polling faster than this hits the server more than any classroom app needs.
MIN_POLL_MS = 2000
# A self-scheduling setTimeout faster than this is a request loop.
MIN_RETRY_MS = 1000

JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
SKIP_DIRS = ("node_modules/", "dist/", ".data/", ".git/", ".opencode/", "tests/", "__pycache__/")
SKIP_FILES = ("package-lock.json", "requirements.lock", "uv.lock", "pnpm-lock.yaml", "yarn.lock")
SILENCE = "vibe-check: ok"

# Calls that leave the browser: fetch, the template's request()/api.x(), the SDK's AI, streams.
JS_NET = re.compile(
    r"\bfetch\s*\(|\brequest\s*[<(]|\bapi\.\w+\s*\(|\baxios\b|\.(?:chat|stream)\s*\(|"
    r"new\s+(?:EventSource|WebSocket|XMLHttpRequest)\b"
)
JS_WAIT = re.compile(r"\b(?:setTimeout|sleep|delay|wait|backoff)\b", re.I)
PY_NET = re.compile(r"\b(?:httpx|requests|urlopen|aiohttp)\b|\bai\.\w+\s*\(|\bmembers\.\w+\s*\(")
PY_WAIT = re.compile(r"\bsleep\s*\(|\bwait(?:_for)?\s*\(")

SECRETS = [
    ("en OpenAI-nøgle", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}")),
    ("en AWS-nøgle", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("en GitHub-nøgle", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("en Slack-nøgle", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("en Google-nøgle", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("en privat nøgle", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
]

# Short Danish titles the studio shows the teacher, by rule.
TITLES = {
    "fast-poll": "Appen spørger serveren for tit",
    "retry-loop": "Appen prøver igen og igen uden pause",
    "loop-no-delay": "En løkke kalder serveren uden pause",
    "effect-no-deps": "Appen henter data ved hver opdatering af siden",
    "eval": "Koden kører tekst som kode (eval)",
    "sql-string": "En databaseforespørgsel bygges af tekst",
    "shell": "Koden kører kommandoer gennem en shell",
    "secret": "Der står en hemmelig nøgle i koden",
}
SECURITY = {"eval", "sql-string", "shell", "secret"}


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    detail: str  # for the AI: what was found and what to do instead

    def to_json(self) -> dict[str, object]:
        return {"rule": self.rule, "title": TITLES[self.rule], "path": self.path, "line": self.line}


# ---------------------------------------------------------------- source helpers

def _blank(s: str) -> str:
    """Spaces for everything but newlines, so offsets and line numbers survive."""
    return re.sub(r"[^\n]", " ", s)


def strip_js(src: str) -> str:
    """Blank out comments and string contents (quotes kept), so the scan sees code only."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(_blank(src[i:j]))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(_blank(src[i:j]))
            i = j
        elif c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif c != "`" and src[j] == "\n":
                    break
                j += 1
            out.append(c + _blank(src[i + 1:j]) + (c if j < n and src[j] == c else ""))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_py(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "#":
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(_blank(src[i:j]))
            i = j
        elif c in "'\"":
            q = src[i:i + 3] if src[i:i + 3] in ('"""', "'''") else c
            j = i + len(q)
            while j < n and not src.startswith(q, j):
                if src[j] == "\\":
                    j += 1
                elif len(q) == 1 and src[j] == "\n":
                    break
                j += 1
            out.append(q + _blank(src[i + len(q):j]) + (q if src.startswith(q, j) else ""))
            i = j + len(q)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _close(code: str, start: int) -> int:
    """Index just past the bracket matching code[start] ( '(' '{' '[' ); len(code) if unbalanced."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack: list[str] = []
    for i in range(start, len(code)):
        ch = code[i]
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
    return len(code)


def _args(code: str, open_paren: int) -> list[str]:
    """Top-level, comma-separated arguments of the call whose '(' is at open_paren."""
    end = _close(code, open_paren)
    inner = code[open_paren + 1:end - 1]
    args, depth, cur = [], 0, []
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        args.append("".join(cur))
    return [a.strip() for a in args]


def _ms(arg: str | None) -> int | None:
    """A delay written as a number or a product of numbers (`3 * 1000`, `1_000`); None otherwise."""
    if arg is None:
        return 0
    parts = [p.strip().replace("_", "") for p in arg.split("*")]
    if not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
        return None
    total = 1.0
    for p in parts:
        total *= float(p)
    return int(total)


def _line(code: str, pos: int) -> int:
    return code.count("\n", 0, pos) + 1


# `const name = [useCallback(] [async] (params) [: Type] => {` or `= async function (...) {`,
# up to the body's opening brace; `=> expr` without a brace is an expression body.
_ARROW = re.compile(
    r"(?:useCallback\s*\(\s*)?(?:async\s+)?(?:function\b[^(]*\([^)]*\)|\([^)]*\)|\w+)\s*(?::\s*[^={]+?)?\s*(=>)?\s*(\{)?"
)


def _function_bodies(code: str) -> dict[str, tuple[int, int]]:
    """name -> (start, end) of the body of `function name(...) {...}`, `const name = (...) => ...`
    or `const name = useCallback(async () => {...}, [...])`."""
    bodies: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r"\bfunction\s+(\w+)\s*\(", code):
        brace = code.find("{", _close(code, m.end() - 1))
        if brace != -1:
            bodies.setdefault(m.group(1), (brace, _close(code, brace)))
    for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=(?!=)\s*", code):
        a = _ARROW.match(code, m.end())
        if not a or not (a.group(1) or "function" in a.group(0)):
            continue
        if a.group(2):
            brace = a.end() - 1
            bodies.setdefault(m.group(1), (brace, _close(code, brace)))
        else:
            eol = code.find("\n", a.end())
            bodies.setdefault(m.group(1), (a.end(), len(code) if eol == -1 else eol))
    return bodies


# ---------------------------------------------------------------- rules

def _scan_js(path: str, code: str) -> list[Finding]:
    found: list[Finding] = []
    bodies = _function_bodies(code)

    def calls_net(expr: str) -> bool:
        if JS_NET.search(expr):
            return True
        name = expr.strip()
        if re.fullmatch(r"\w+", name) and name in bodies:
            s, e = bodies[name]
            return bool(JS_NET.search(code[s:e]))
        # `() => load()` / `() => { void load(); }`
        return any(JS_NET.search(code[s:e]) for n, (s, e) in bodies.items() if re.search(rf"\b{re.escape(n)}\s*\(", expr))

    for m in re.finditer(r"\bsetInterval\s*\(", code):
        args = _args(code, m.end() - 1)
        delay = _ms(args[1] if len(args) > 1 else None)
        if args and delay is not None and delay < MIN_POLL_MS and calls_net(args[0]):
            found.append(Finding("fast-poll", path, _line(code, m.start()),
                f"setInterval calls the server every {delay} ms. Poll at most every 3 s (or use live updates), "
                "skip a tick while the previous request is still running, and back off when requests fail."))

    for name, (s, e) in bodies.items():
        body = code[s:e]
        if not JS_NET.search(body):
            continue
        for m in re.finditer(r"\bsetTimeout\s*\(", body):
            args = _args(body, m.end() - 1)
            if not args or not re.search(rf"\b{re.escape(name)}\b", args[0]):
                continue
            delay = _ms(args[1] if len(args) > 1 else None)
            if delay is not None and delay < MIN_RETRY_MS:
                found.append(Finding("retry-loop", path, _line(code, s + m.start()),
                    f"`{name}` calls the server and schedules itself again after {delay} ms. Wait at least 3 s "
                    "between rounds and double the wait (up to a minute) after each failed request."))

    for m in re.finditer(r"\bwhile\s*\(\s*(?:true|1|!0)\s*\)|\bfor\s*\(\s*;\s*;\s*\)", code):
        brace = code.find("{", m.end())
        if brace == -1 or code[m.end():brace].strip():
            continue
        body = code[brace:_close(code, brace)]
        if JS_NET.search(body) and not JS_WAIT.search(body):
            found.append(Finding("loop-no-delay", path, _line(code, m.start()),
                "An endless loop calls the server with no pause. Wait between rounds (at least 3 s, longer after errors) "
                "or replace the loop with polling or live updates."))

    for m in re.finditer(r"\buseEffect\s*\(", code):
        args = _args(code, m.end() - 1)
        if len(args) == 1 and calls_net(args[0]):
            found.append(Finding("effect-no-deps", path, _line(code, m.start()),
                "useEffect has no dependency array, so it calls the server after every render (and a state update "
                "from the response renders again: an endless request loop). Add the dependency array, e.g. `[]`."))

    for m in re.finditer(r"(?<![\w.])eval\s*\(|\bnew\s+Function\s*\(", code):
        found.append(Finding("eval", path, _line(code, m.start()),
            "Runs a string as code. Parse data with JSON.parse or write the logic out instead."))
    return found


def _py_blocks(code: str, pat: re.Pattern[str]) -> list[tuple[int, str]]:
    """(offset, body) of each indented block opened by a line matching pat."""
    lines = code.split("\n")
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln) + 1)
    out = []
    for i, ln in enumerate(lines):
        if not pat.match(ln):
            continue
        indent = len(ln) - len(ln.lstrip())
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body.append(nxt)
        out.append((offsets[i], "\n".join(body)))
    return out


def _scan_py(path: str, code: str) -> list[Finding]:
    found: list[Finding] = []
    for off, body in _py_blocks(code, re.compile(r"\s*while\s+(?:True|1)\s*:")):
        if PY_NET.search(body) and not PY_WAIT.search(body):
            found.append(Finding("loop-no-delay", path, _line(code, off),
                "`while True` calls a service with no pause. Sleep between rounds (asyncio.sleep, at least a few "
                "seconds, longer after errors) or do the work on request instead."))
    for m in re.finditer(r"(?<![\w.])(?:eval|exec)\s*\(", code):
        found.append(Finding("eval", path, _line(code, m.start()),
            "Runs a string as code. Parse data with json.loads or write the logic out instead."))
    for m in re.finditer(r"\bshell\s*=\s*True\b", code):
        found.append(Finding("shell", path, _line(code, m.start()),
            "Runs a command through a shell. Pass the command as a list without shell=True."))
    return found


def _scan_sql(path: str, raw: str) -> list[Finding]:
    """SQL built with f-strings, % or .format() and handed to execute(). Needs string contents, so it reads raw."""
    found = []
    pat = re.compile(r"\.execute(?:many)?\s*\(\s*(?:f[\"']|[\"'][^\"'\n]*[\"']\s*(?:%|\.format\s*\())")
    for m in pat.finditer(raw):
        found.append(Finding("sql-string", path, _line(raw, m.start()),
            "Builds SQL from text, which allows SQL injection. Use parameters (`?` or `:name`) and pass the values separately."))
    return found


def _scan_secrets(path: str, raw: str) -> list[Finding]:
    found = []
    for what, pat in SECRETS:
        for m in pat.finditer(raw):
            found.append(Finding("secret", path, _line(raw, m.start()),
                f"Contains {what}. Remove it: the app gets AI and Miira through the platform SDK, never with its own keys."))
    return found


def _silenced(raw_lines: list[str], f: Finding) -> bool:
    if f.rule in SECURITY:
        return False
    near = raw_lines[max(0, f.line - 2):f.line]
    return any(SILENCE in ln for ln in near)


def scan_file(path: str, raw: str) -> list[Finding]:
    found = _scan_secrets(path, raw)
    if path.startswith("web/") and path.endswith(JS_EXT):
        found += _scan_js(path, strip_js(raw))
    elif path.endswith(".py"):
        found += _scan_py(path, strip_py(raw)) + _scan_sql(path, raw)
    lines = raw.split("\n")
    return [f for f in found if not _silenced(lines, f)]


def _scanned(path: str) -> bool:
    if any(f"/{d}" in f"/{path}" for d in SKIP_DIRS):
        return False
    name = path.rsplit("/", 1)[-1]
    return name not in SKIP_FILES and ".test." not in name and not name.startswith("test_")


def scan_tar(data: bytes) -> list[Finding]:
    """Scan an exported tree (`git archive --format=tar`). Findings sorted by path and line."""
    found: list[Finding] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar:
            if not member.isfile() or member.size > MAX_FILE_BYTES or not _scanned(member.name):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            blob = fh.read()
            if b"\0" in blob[:8000]:
                continue  # binary
            found += scan_file(member.name, blob.decode("utf-8", "replace"))
    found.sort(key=lambda f: (f.path, f.line, f.rule))
    return found[:MAX_FINDINGS]


def fix_prompt(findings: list[Finding]) -> str:
    """The message the fix turn sends to the AI (and the teacher sees in the chat)."""
    items = "\n".join(f"- `{f.path}:{f.line}` ({f.rule}): {f.detail}" for f in findings)
    return (
        "Tjek før udgivelse: det automatiske tjek fandt kode, der kan overbelaste serveren eller er usikker. "
        "Ret det, før appen udgives, uden at ændre hvad appen gør eller hvordan den ser ud.\n\n"
        f"{items}\n\n"
        "Regler for hele appen, ikke kun linjerne ovenfor: hent data højst hvert 3. sekund (brug live-opdateringer "
        "hvor de findes), vent længere efter hver fejl (backoff, op til et minut), lad aldrig to af de samme "
        "forespørgsler køre på én gang, giv altid useEffect en afhængighedsliste, og læg aldrig nøgler eller "
        "adgangskoder i koden. Er en linje ovenfor faktisk i orden, så skriv en kommentar med `vibe-check: ok` og "
        "hvorfor på linjen før (det gælder ikke nøgler, eval, SQL og shell). Fortæl til sidst kort og uden "
        "tekniske ord, hvad du har rettet."
    )
