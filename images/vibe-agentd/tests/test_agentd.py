import io
import subprocess
import tarfile

import pytest
from fastapi.testclient import TestClient

from vibe_agentd import app as agentd


@pytest.fixture
def client(tmp_path, monkeypatch):
    platform = tmp_path.parent / (tmp_path.name + "-platform")
    (platform / "web").mkdir(parents=True)
    (platform / "web" / "package.json").write_text("{}\n")
    monkeypatch.setattr(agentd, "PLATFORM_DIR", platform)
    monkeypatch.setattr(agentd, "PLATFORM_FILES", ["web/package.json"])
    monkeypatch.setattr(agentd, "PLATFORM_ALLOWLIST", platform / "allow.txt")
    monkeypatch.setattr(agentd, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agentd, "TOKEN", "t0k")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "App.tsx").write_text("export default 1\n")
    (tmp_path / "web" / "package.json").write_text("{}\n")
    (tmp_path / ".data").mkdir()
    (tmp_path / ".data" / "app.sqlite").write_text("x")
    (tmp_path / ".gitignore").write_text(".data/\nnode_modules/\n")
    (tmp_path / "web" / "node_modules").mkdir()
    (tmp_path / "web" / "node_modules" / "x.js").write_text("")
    c = TestClient(agentd.app, headers={"Authorization": "Bearer t0k"})
    c.post("/commit", json={"message": "Start", "trailers": {}})
    return c


def test_auth_required(client):
    assert TestClient(agentd.app).get("/files").status_code == 401
    assert TestClient(agentd.app).get("/health").status_code == 200


def test_files_flags(client, tmp_path):
    files = {f["path"]: f for f in client.get("/files").json()["files"]}
    assert set(files) == {".gitignore", "web/App.tsx", "web/package.json"}
    assert not any(f["changed"] for f in files.values()), "the seed commit is not an AI turn"
    assert files["web/package.json"]["locked"] and not files["web/App.tsx"]["locked"]
    assert client.get("/files/.data/app.sqlite").status_code == 404
    assert client.get("/files/../etc/passwd").status_code in (400, 404)


def test_commit_history_diff_restore_export(client, tmp_path):
    first = client.get("/history").json()["commits"][0]["sha"]
    (tmp_path / "web" / "App.tsx").write_text("export default 2\n")
    (tmp_path / "web" / "New.tsx").write_text("new\n")
    assert {f["path"] for f in client.get("/files").json()["files"] if f["changed"]} >= {"web/App.tsx", "web/New.tsx"}
    c = client.post("/commit", json={"message": "Tilføj ny side",
                                     "trailers": {"Vibe-Thread": "th1", "Vibe-Author": "Mette"}}).json()
    assert c["threadId"] == "th1" and c["authorName"] == "Mette" and set(c["files"]) == {"web/App.tsx", "web/New.tsx"}
    assert client.post("/commit", json={"message": "noop"}).json() == {"sha": None, "files": []}
    changed = {f["path"] for f in client.get("/files").json()["files"] if f["changed"]}
    assert changed == {"web/App.tsx", "web/New.tsx"}, "a turn commit marks exactly the files it touched"

    d = client.get(f"/diff/{c['sha']}").json()
    assert {f["path"]: f["status"] for f in d["files"]} == {"web/App.tsx": "modified", "web/New.tsx": "added"}
    assert "+export default 2" in d["files"][0]["patch"]

    assert client.get(f"/files/web/App.tsx?sha={first}").json()["content"] == "export default 1\n"

    r = client.post("/restore", json={"sha": first, "trailers": {"Vibe-Author": "Mette"}}).json()
    assert (tmp_path / "web" / "App.tsx").read_text() == "export default 1\n"
    assert not (tmp_path / "web" / "New.tsx").exists()
    assert (tmp_path / ".data" / "app.sqlite").exists()  # data survives restore
    hist = client.get("/history").json()["commits"]
    assert [h["sha"] for h in hist] == [r["sha"], c["sha"], first]  # history never rewritten

    tar = tarfile.open(fileobj=io.BytesIO(client.post("/export", json={"sha": c["sha"]}).content))
    assert "web/New.tsx" in tar.getnames()
    assert client.post("/export", json={"sha": "deadbeef"}).status_code == 404


def test_export_rejects_changed_platform_files(client, tmp_path):
    good = client.get("/history").json()["commits"][0]["sha"]
    r = client.post("/export", json={"sha": good})
    assert r.status_code == 200 and r.headers["X-Vibe-Sha"] == good

    pkg = tmp_path / "web" / "package.json"
    pkg.write_text('{"dependencies": {"left-pad": "1"}}\n')
    bad = client.post("/commit", json={"message": "sneak a dependency in"}).json()["sha"]
    r = client.post("/export", json={"sha": bad})
    assert r.status_code == 422 and r.json() == {"paths": ["web/package.json"]}

    # An older platform version listed in the allowlist may still be published.
    import hashlib
    digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
    agentd.PLATFORM_ALLOWLIST.write_text(f"# old platform versions\n{digest}  web/package.json\n")
    assert client.post("/export", json={"sha": bad}).status_code == 200


@pytest.fixture
def scratch_dir(tmp_path, monkeypatch):
    d = tmp_path.parent / (tmp_path.name + "-scratch")
    d.mkdir()
    (d / "AGENTS.md").write_text("# scratch rules\n")
    (d / "README.md").write_text("# Mit projekt\n")
    monkeypatch.setattr(agentd, "SCRATCH_DIR", d)
    return d


def _seed_by_platform(tmp_path):
    """A fresh warm-pool workspace: only the platform's own seed commit."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "App.tsx").write_text("export default 1\n")
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "state").write_text("keep")
    c = TestClient(agentd.app, headers={"Authorization": "Bearer t0k"})
    c.post("/commit", json={"message": "Start fra skabelon", "trailers": {"Vibe-Author": "SlopTown"}})
    return c


def test_reset_scratch_turns_a_fresh_workspace_into_an_empty_project(tmp_path, monkeypatch, scratch_dir):
    monkeypatch.setattr(agentd, "RESET_SETTLE_SECONDS", 0)
    monkeypatch.setattr(agentd, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agentd, "TOKEN", "t0k")
    c = _seed_by_platform(tmp_path)
    assert c.get("/kind").json() == {"kind": "webapp"}

    r = c.post("/reset-scratch")
    assert r.status_code == 200 and r.json()["kind"] == "scratch"
    assert c.get("/kind").json() == {"kind": "scratch"}
    assert {f["path"] for f in c.get("/files").json()["files"]} == {"AGENTS.md", "README.md"}
    commits = c.get("/history").json()["commits"]
    assert [x["message"] for x in commits] == ["Start fra bunden"]
    assert (tmp_path / ".opencode" / "state").read_text() == "keep"
    assert not (tmp_path / "web").exists()
    # The platform's AGENTS.md is the scratch one, so export passes the lock check.
    r = c.post("/export", json={"sha": commits[0]["sha"]})
    assert r.status_code == 200
    names = tarfile.open(fileobj=io.BytesIO(r.content)).getnames()
    assert "README.md" in names and ".vibe-kind" not in names

    assert c.post("/reset-scratch").status_code == 200, "idempotent"


def test_reset_scratch_never_throws_work_away(tmp_path, monkeypatch, scratch_dir):
    monkeypatch.setattr(agentd, "RESET_SETTLE_SECONDS", 0)
    monkeypatch.setattr(agentd, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agentd, "TOKEN", "t0k")
    c = _seed_by_platform(tmp_path)
    (tmp_path / "web" / "App.tsx").write_text("export default 2\n")
    assert c.post("/reset-scratch").status_code == 409, "uncommitted work"
    c.post("/commit", json={"message": "AI", "trailers": {"Vibe-Thread": "t1"}})
    assert c.post("/reset-scratch").status_code == 409, "an AI turn"
    assert (tmp_path / "web" / "App.tsx").read_text() == "export default 2\n"


# The real catalog, styles and palettes: these tests also check that they fit together.
DESIGN = __import__("pathlib").Path(__file__).resolve().parents[3] / "template-design"


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A fresh warm-pool webapp workspace (platform seed only), with the design dir."""
    monkeypatch.setattr(agentd, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agentd, "TOKEN", "t0k")
    monkeypatch.setattr(agentd, "DESIGN_DIR", DESIGN)
    monkeypatch.setattr(agentd, "PLATFORM_FILES", [])
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git" / "info" / "exclude").write_text(agentd.EXCLUDE)
    src = tmp_path / "web" / "src"
    src.mkdir(parents=True)
    (src / "main.tsx").write_text('import "@miira/vibe/theme.css";\nimport "./styles.css";\n\nrender();\n')
    (src / "App.tsx").write_text("export default 1\n")
    (src / "styles.css").write_text(".side {}\n")
    c = TestClient(agentd.app, headers={"Authorization": "Bearer t0k"})
    c.post("/commit", json={"message": "Start fra skabelon", "trailers": {"Vibe-Author": "SlopTown"}})
    return c


def test_design_starter_lays_a_style_over_a_fresh_project(fresh, tmp_path):
    assert fresh.get("/design").json() == {"style": None, "palette": None}
    r = fresh.post("/design", json={"style": "bootstrap", "palette": "hav", "starter": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["style"] == "bootstrap" and body["palette"] == "hav"
    assert body["message"] == "Start med Bootstrap og farverne Hav" and body["authorName"] == "SlopTown"
    assert fresh.get("/design").json() == {"style": "bootstrap", "palette": "hav"}
    src = tmp_path / "web" / "src"
    assert '"bootstrap/dist/css/bootstrap.min.css"' in (src / "main.tsx").read_text()
    assert (src / "palette.css").read_bytes() == (DESIGN / "palettes" / "hav.css").read_bytes()
    assert (src / "design.css").read_bytes() == (DESIGN / "styles" / "bootstrap" / "design.css").read_bytes()
    assert (tmp_path / "DESIGN.md").read_text().startswith("# Design: Bootstrap")
    files = {f["path"]: f for f in fresh.get("/files").json()["files"]}
    assert files["DESIGN.md"]["locked"] and files["web/src/palette.css"]["locked"]
    assert ".vibe-design" not in files
    # Platform files match the platform's copies, so the version can be published.
    assert fresh.post("/export", json={"sha": body["sha"]}).status_code == 200
    # Still nobody's work: a "fra bunden" reset would be allowed.
    assert agentd.only_platform_commits()


def test_design_starter_never_overwrites_work(fresh, tmp_path):
    (tmp_path / "web" / "src" / "App.tsx").write_text("export default 2\n")
    fresh.post("/commit", json={"message": "AI", "trailers": {"Vibe-Thread": "t1"}})
    assert fresh.post("/design", json={"style": "bulma", "palette": "skov", "starter": True}).status_code == 409
    assert (tmp_path / "web" / "src" / "App.tsx").read_text() == "export default 2\n"


def test_design_later_changes_only_platform_files(fresh, tmp_path):
    fresh.post("/design", json={"style": "tailwind", "palette": "miira", "starter": True})
    app = tmp_path / "web" / "src" / "App.tsx"
    app.write_text("export default 'mine'\n")
    fresh.post("/commit", json={"message": "AI", "trailers": {"Vibe-Thread": "t1"}})

    r = fresh.post("/design", json={"palette": "solnedgang"}).json()
    assert r["message"] == "Farver: Solnedgang" and r["style"] == "tailwind"
    assert set(r["files"]) == {"web/src/palette.css"}
    r = fresh.post("/design", json={"style": "bulma"}).json()
    assert r["message"] == "Design: Bulma med farverne Solnedgang" and r["palette"] == "solnedgang"
    assert set(r["files"]) == {"DESIGN.md", "web/src/design.css"}
    assert app.read_text() == "export default 'mine'\n", "the AI rebuilds the UI, not the platform"
    assert fresh.post("/export", json={"sha": r["sha"]}).status_code == 200


def test_design_on_an_older_vibe_adds_the_imports(fresh, tmp_path):
    r = fresh.post("/design", json={"palette": "skov"})
    assert r.status_code == 200 and r.json()["style"] == "miira"
    main = (tmp_path / "web" / "src" / "main.tsx").read_text()
    assert main.index('"./palette.css"') < main.index('"./design.css"') < main.index('"./styles.css"')
    assert main.startswith('import "@miira/vibe/theme.css";')
    # Idempotent: a second change adds nothing.
    fresh.post("/design", json={"palette": "hav"})
    assert (tmp_path / "web" / "src" / "main.tsx").read_text().count("palette.css") == 1


def test_design_rejects_unknown_ids_and_edited_platform_files(fresh, tmp_path):
    assert fresh.post("/design", json={"style": "foundation"}).status_code == 400
    assert fresh.post("/design", json={"palette": "lilla"}).status_code == 400
    fresh.post("/design", json={"style": "miira", "palette": "hav", "starter": True})
    pal = tmp_path / "web" / "src" / "palette.css"
    pal.chmod(0o644)
    pal.write_text(":root { --palette-primary: hotpink; }\n")
    sha = fresh.post("/commit", json={"message": "AI", "trailers": {"Vibe-Thread": "t1"}}).json()["sha"]
    r = fresh.post("/export", json={"sha": sha})
    assert r.status_code == 422 and r.json() == {"paths": ["web/src/palette.css"]}
    # A marker the agent rewrote to nonsense is ignored (no design, no design files expected).
    (tmp_path / ".vibe-design").write_text("../../etc x\n")
    assert fresh.get("/design").json() == {"style": None, "palette": None}


def test_catalog_has_files_for_every_style_and_palette():
    cat = __import__("json").loads((DESIGN / "catalog.json").read_text())
    for s in cat["styles"]:
        for name in ("DESIGN.md", "design.css", *agentd.STARTER_FILES):
            assert (DESIGN / "styles" / s["id"] / name).is_file(), (s["id"], name)
    for p in cat["palettes"]:
        assert (DESIGN / "palettes" / f"{p['id']}.css").is_file(), p["id"]


def test_raw_media_for_the_file_preview(client, tmp_path):
    png = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
    (tmp_path / "web" / "logo.png").write_bytes(png)
    r = client.get("/raw/web/logo.png")
    assert r.status_code == 200 and r.content == png and r.headers["content-type"] == "image/png"
    sha = client.post("/commit", json={"message": "logo"}).json()["sha"]
    (tmp_path / "web" / "logo.png").write_bytes(b"new")
    assert client.get("/raw/web/logo.png", params={"sha": sha}).content == png
    assert client.get("/raw/web/App.tsx").status_code == 415, "text goes through /files"
    assert client.get("/raw/.data/x.png").status_code == 404
    assert client.get("/raw/web/missing.png").status_code == 404
    (tmp_path / "web" / "escape.png").symlink_to("/etc/hostname")
    assert client.get("/raw/web/escape.png").status_code == 404
    assert TestClient(agentd.app).get("/raw/web/logo.png").status_code == 401
