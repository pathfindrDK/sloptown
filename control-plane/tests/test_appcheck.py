"""Pre-publish scan (appcheck): request loops, security slips, and no false alarms on ordinary code."""

import io
import tarfile

from sloptown.appcheck import fix_prompt, scan_file, scan_tar


def rules(path, src):
    return [(f.rule, f.line) for f in scan_file(path, src)]


def tar_of(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_fast_polling_is_found():
    # The incident: an arena app polling its state about every 100 ms.
    src = """import { useEffect } from "react";
export function Arena() {
  useEffect(() => {
    const id = setInterval(async () => {
      const r = await fetch(`/api/arena/state?player_id=${pid}`);
      setState(await r.json());
    }, 100);
    return () => clearInterval(id);
  }, []);
}
"""
    assert rules("web/src/Arena.tsx", src) == [("fast-poll", 4)]


def test_polling_through_a_named_function_and_api_helper():
    src = """const load = useCallback(async () => {
  setItems(await api.items());
}, []);
useEffect(() => { const t = setInterval(load, 500); return () => clearInterval(t); }, [load]);
function tick() { request("/stilling").then(setScore); }
setInterval(tick, 1_000);
setInterval(() => tick(), 2 * 250);
"""
    assert rules("web/src/App.tsx", src) == [("fast-poll", 4), ("fast-poll", 6), ("fast-poll", 7)]


def test_ordinary_timers_are_fine():
    src = """// setInterval(() => fetch("/x"), 10) was too fast
const clock = setInterval(() => setNow(Date.now()), 1000);
const poll = setInterval(() => fetch("/api/state"), 3000);
const custom = setInterval(() => fetch("/api/state"), POLL_MS);
const label = "setInterval(() => fetch('/x'), 5)";
useEffect(() => { fetch("/api/x").then(setX); }, []);
"""
    assert rules("web/src/App.tsx", src) == []


def test_retry_loop_and_endless_loop():
    src = """async function poll() {
  try { setState(await (await fetch("/api/state")).json()); }
  finally { setTimeout(poll, 0); }
}
const retry = async () => { await api.save(x).catch(() => setTimeout(() => retry(), 200)); };
async function ok() { await fetch("/api/state"); setTimeout(ok, 5000); }
async function spin() {
  while (true) {
    await fetch("/api/ping");
  }
}
async function gentle() {
  for (;;) { await fetch("/api/ping"); await sleep(3000); }
}
"""
    assert rules("web/src/poll.ts", src) == [("retry-loop", 3), ("retry-loop", 5), ("loop-no-delay", 8)]


def test_effect_without_dependencies():
    src = """useEffect(() => {
  fetch("/api/noter").then((r) => r.json()).then(setNoter);
});
useEffect(() => { document.title = titel; });
"""
    assert rules("web/src/Noter.tsx", src) == [("effect-no-deps", 1)]


def test_security_rules():
    js = 'const f = new Function("a", body);\nconst v = eval(input);\nconst k = "sk-proj-abcdefghijklmnopqrstuvwxyz0123";\n'
    assert rules("web/src/x.ts", js) == [("secret", 3), ("eval", 1), ("eval", 2)]
    py = '''import subprocess
db.execute(f"SELECT * FROM noter WHERE id = {nid}")
db.execute("SELECT * FROM noter WHERE id = %s" % nid)
db.execute("SELECT * FROM noter WHERE id = ?", (nid,))
subprocess.run(cmd, shell=True)
result = eval(udtryk)
'''
    assert sorted(rules("api/main.py", py)) == [("eval", 6), ("shell", 5), ("sql-string", 2), ("sql-string", 3)]


def test_python_busy_loop():
    py = """async def sync_forever():
    while True:
        await members.sync()

async def gentle():
    while True:
        await members.sync()
        await asyncio.sleep(60)

def parse(lines):
    while True:
        if not lines: break
"""
    assert rules("api/worker.py", py) == [("loop-no-delay", 2)]


def test_silencing_works_except_for_security_rules():
    src = """// vibe-check: ok — the game clock must tick fast, the server is local
const id = setInterval(() => fetch("/api/tick"), 250);
// vibe-check: ok
const k = "sk-abcdefghijklmnopqrstuvwxyz012345";
"""
    assert rules("web/src/game.ts", src) == [("secret", 4)]


def test_scan_tar_skips_dependencies_build_output_and_tests():
    loop = 'setInterval(() => fetch("/api/x"), 10);\n'
    data = tar_of({
        "web/src/App.tsx": loop,
        "web/node_modules/lib/index.js": loop,
        "web/dist/assets/index.js": loop,
        "web/src/App.test.tsx": loop,
        "tests/test_api.py": 'eval("1")\n',
        "README.md": "no code here\n",
    })
    found = scan_tar(data)
    assert [(f.path, f.rule) for f in found] == [("web/src/App.tsx", "fast-poll")]
    assert found[0].to_json() == {"rule": "fast-poll", "title": "Appen spørger serveren for tit", "path": "web/src/App.tsx", "line": 1}
    prompt = fix_prompt(found)
    assert prompt.startswith("Tjek før udgivelse") and "`web/src/App.tsx:1` (fast-poll)" in prompt


def test_the_template_is_clean():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "template"
    if not root.is_dir():
        return  # the image build copies only control-plane/
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and "node_modules" not in rel and "/dist/" not in rel and p.suffix in (".ts", ".tsx", ".py"):
            assert scan_file(rel, p.read_text()) == [], rel
