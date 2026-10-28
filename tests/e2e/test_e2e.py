"""End-to-end test of the running dev stack (deploy/dev-up.sh), driven over HTTP through the edge.

Run via tests/e2e/run.sh, which starts the stack with the scripted fake OpenAI.
Every request goes to the edge on EDGE (default 127.0.0.1:8080) with the real
Host header, so routing, forward_auth, cookies and per-host sessions are the
real ones. Steps run in file order and share state; once one fails the rest
are skipped.

What it covers, in order:
 1. teacher signs in to fake Miira, creates a Vibe file, gets a launch token
 2. studio: launch-token exchange, terms, lazy project creation (warm-pool claim)
 3. files + history from vibe-agentd in the real vibe-dev container
 4. preview through the edge (one-time code -> host cookie -> Vite)
 5. a chat turn: OpenCode -> egress -> fake OpenAI -> write tool -> commit,
    observed on the project SSE stream; usage metered on the build budget
 6. publish: export, vibe-build in a one-shot container, vibe-runtime up
 7. the published app as the owner (identity headers via the SDK)
 8. a student: denied before sharing; after a class share (webhook) gets in
    through OIDC as a viewer, and the app's member list shows the class
 9. restore adds a new commit; history is never rewritten
10. admin API via fake Miira: usage per bucket; hard limits block turns and app AI
    calls; reset and grant lift them
11. a "fra bunden" project: empty workspace, an AI turn, download as .zip, and no
    preview, app host or publish
"""

import json
import os
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

EDGE = os.environ.get("E2E_EDGE", "127.0.0.1:8080")
# E2E_DOMAIN=miira-apps.dk runs against the public test deployment (deploy/dev-up.sh --public):
# real DNS and TLS, and every client first passes the password gate (E2E_GATE_PASSWORD).
DOMAIN = os.environ.get("E2E_DOMAIN", "")
if DOMAIN:
    STUDIO, MIIRA = f"https://studio.{DOMAIN}", f"https://miira.{DOMAIN}"
else:
    STUDIO, MIIRA = "http://studio.sloptown.localhost:8080", "http://miira.sloptown.localhost:8080"
_GATE: dict[str, str] = {}
# E2E_MIIRA_ORIGIN runs against a real Miira (miira3, with VIBE_* configured) instead of
# fake-miira: Miira's own accounts, drive API and share dialog API. E2E_MIIRA_ADDR is
# where its origin is reached when that host has no DNS (e.g. 127.0.0.1:8090).
REAL_MIIRA = os.environ.get("E2E_MIIRA_ORIGIN", "").rstrip("/")
MIIRA_ADDR = os.environ.get("E2E_MIIRA_ADDR", "")
if REAL_MIIRA:
    MIIRA = REAL_MIIRA
    TEACHER, STUDENT, ADMIN = "teacher@miira.dk", "student@miira.dk", "admin@miira.dk"
    TEACHER_NAME, STUDENT_NAME, CLASS_NAME = "Lea Lærer", "Erik Elev", "1. A"
else:
    TEACHER, STUDENT, ADMIN = "u-mette", "u-ali", "u-admin"
    TEACHER_NAME, STUDENT_NAME, CLASS_NAME = "Mette Hansen", "Ali Yilmaz", "7.A"
TURN_TIMEOUT = float(os.environ.get("E2E_TURN_TIMEOUT", "180"))


class EdgeTransport(httpx.BaseTransport):
    """Send every request to the edge, keeping the URL's host as the Host header (no DNS needed)."""

    def __init__(self) -> None:
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        edge_host, edge_port = EDGE.split(":")
        if MIIRA_ADDR and request.url.host == urlsplit(MIIRA).hostname:
            edge_host, edge_port = MIIRA_ADDR.split(":")
        sent = httpx.Request(
            request.method,
            request.url.copy_with(scheme="http", host=edge_host, port=int(edge_port)),
            headers=[*((k, v) for k, v in request.headers.raw if k.lower() != b"host"),
                     (b"host", request.url.netloc)],
            stream=request.stream,
        )
        response = self._inner.handle_request(sent)
        response.request = request  # cookies and redirects resolve against the real host
        if request.url.host.endswith(".localhost"):
            # Browsers treat http://*.localhost as a secure context and send `Secure`
            # cookies there; httpx doesn't. Drop the attribute to behave like a browser.
            response.headers = httpx.Headers([
                (k, re.sub(r";\s*Secure(?=;|$)", "", v, flags=re.I) if k == "set-cookie" else v)
                for k, v in response.headers.multi_items()
            ])
        return response


def _gate_cookie() -> str:
    """Log in to the public test gate once; the cookie covers every subdomain."""
    if "value" not in _GATE:
        with httpx.Client(timeout=30) as c:
            r = c.post(f"https://{DOMAIN}/_gate",
                       data={"password": os.environ["E2E_GATE_PASSWORD"], "next": f"{STUDIO}/"})
            assert r.status_code == 303, f"gate login failed: {r.status_code}"
            _GATE["value"] = r.cookies["sloptown_gate"]
    return _GATE["value"]


def client() -> httpx.Client:
    if not DOMAIN:
        return httpx.Client(transport=EdgeTransport(), timeout=60, follow_redirects=False)
    c = httpx.Client(timeout=60, follow_redirects=False)
    # With a real Miira the public stack runs without the gate (docs/miira-integration.md §11).
    if os.environ.get("E2E_GATE_PASSWORD"):
        c.cookies.set("sloptown_gate", _gate_cookie(), domain=f".{DOMAIN}")
    return c


def follow(c: httpx.Client, r: httpx.Response, limit: int = 15) -> httpx.Response:
    """Follow redirects across hosts by hand so cookies land on the right host each hop."""
    for _ in range(limit):
        if r.status_code not in (301, 302, 303, 307, 308):
            return r
        loc = r.headers["location"]
        url = loc if loc.startswith("http") else f"{r.request.url.scheme}://{r.request.url.netloc.decode()}{loc}"
        r = c.get(url)
    raise AssertionError("too many redirects")


def ok(r: httpx.Response, code: int = 200) -> Any:
    assert r.status_code == code, f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:500]}"
    return r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text


S: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _chain(request):
    """Steps depend on each other: after the first failure, skip the rest (see conftest.py)."""
    if S.get("failed"):
        pytest.skip(f"earlier step failed: {S['failed']}")
    yield
    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        S["failed"] = request.node.name


def miira_login(user: str) -> httpx.Client:
    c = client()
    if REAL_MIIRA:
        ok(c.post(f"{MIIRA}/api/auth/dev-login", json={"email": user}))
    else:
        ok(c.post(f"{MIIRA}/login", data={"user_id": user}), 303)
    return c


def miira_new_vibe(c: httpx.Client) -> str:
    title = f"E2E {uuid.uuid4().hex[:6]}"
    if not REAL_MIIRA:
        f = ok(c.post(f"{MIIRA}/api/files", json={"title": title}))
        assert f["content"] == {"projectId": f["id"]}
        return f["id"]
    fid = str(uuid.uuid4())
    r = c.post(f"{MIIRA}/api/drive/files",
               json={"id": fid, "name": title, "type": "vibe", "content": {"projectId": fid}})
    assert r.status_code in (200, 201), r.text
    return fid


def miira_launch(c: httpx.Client, fid: str) -> str:
    path = f"/api/vibe/files/{fid}/launch" if REAL_MIIRA else f"/api/files/{fid}/launch"
    return ok(c.post(MIIRA + path))["launchToken"]


def miira_share_with_class(c: httpx.Client, fid: str) -> int:
    """Share the Vibe with CLASS_NAME as viewers; returns how many elever that class has."""
    if not REAL_MIIRA:
        ok(c.put(f"{MIIRA}/api/files/{fid}/shares",
                 json={"shares": [{"kind": "class", "id": "c-7a", "role": "viewer"}]}))
        return 4
    klass = next(k for k in ok(c.get(f"{MIIRA}/api/shares/classes")) if k["name"] == CLASS_NAME)
    ok(c.post(f"{MIIRA}/api/shares/class", json={"fileId": fid, "classIds": [klass["id"]], "mode": "consumer"}), 204)
    return klass["studentCount"]


def admin_call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    url = f"{MIIRA}/api/sysadmin/vibe/call" if REAL_MIIRA else f"{MIIRA}/admin/vibe/call"
    r = ok(S["admin"].post(url, json={"method": method, "path": path, "body": body}))
    return r["status"], r["body"]


def wait_for(fn, timeout: float, what: str, every: float = 1.0):
    """Poll fn until truthy. A 503 (upstream_unreachable, Retry-After) counts as "not yet"."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = fn()
        except AssertionError as e:
            if "-> 503:" not in str(e):
                raise
            last = None
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {last!r})")


# --- 1-2: Miira file, launch token, studio session, project ------------------------------


def test_01_teacher_creates_vibe_in_miira():
    S["miira"] = miira_login(TEACHER)
    S["fid"] = miira_new_vibe(S["miira"])
    S["launch"] = miira_launch(S["miira"], S["fid"])
    S["admin"] = miira_login(ADMIN)
    # Fresh settings for this run: budgets on, no limits.
    status, _ = admin_call("PUT", "/admin/v1/budget-settings", {
        "enabled": True, "nerfModel": "gpt-6-luna", "warningThresholds": [0.8], "watcherEmails": [],
        "build": {"byRole": {r: {"nerfLimitTokens": None, "hardLimitTokens": None} for r in ("laerer", "elev", "admin", "default")}},
        "app": {"perProject": {"nerfLimitTokens": None, "hardLimitTokens": None}},
    })
    assert status == 200


def test_02_studio_launch_terms_and_project():
    st = client()
    r = ok(st.post(f"{STUDIO}/api/auth/launch", json={"launchToken": S["launch"]}))
    assert r["fileId"] == S["fid"] and r["fileRole"] == "owner"
    st.headers["authorization"] = f"Bearer {r['token']}"
    S["studio"] = st
    # replayed launch token is refused
    assert client().post(f"{STUDIO}/api/auth/launch", json={"launchToken": S["launch"]}).status_code in (400, 401)

    me = ok(st.get(f"{STUDIO}/api/me"))
    assert me["user"]["name"] == TEACHER_NAME and me["user"]["role"] == "laerer"
    if not me.get("termsAcceptedAt"):
        assert st.get(f"{STUDIO}/api/projects/{S['fid']}").json().get("error") == "terms_required"
        ok(st.post(f"{STUDIO}/api/terms/accept", json={"version": ok(st.get(f"{STUDIO}/api/terms"))["version"]}))

    # The owner's first open asks what to build; nothing exists until they choose.
    r = st.get(f"{STUDIO}/api/projects/{S['fid']}")
    assert r.status_code == 404 and r.json()["error"] == "not_started", r.text
    p = ok(st.post(f"{STUDIO}/api/projects", json={"fileId": S["fid"], "kind": "webapp"}), 201)
    assert p["myRole"] == "owner" and p["kind"] == "webapp"
    p = wait_for(lambda: (x := ok(st.get(f"{STUDIO}/api/projects/{S['fid']}")))["status"] == "ready" and x,
                 120, "project ready")
    S["project"] = p
    assert any(x["id"] == S["fid"] for x in ok(st.get(f"{STUDIO}/api/projects")))


# --- 3-4: files, history, preview ----------------------------------------------------------


def test_03_files_and_history_from_agentd():
    st, pid = S["studio"], S["fid"]
    files = wait_for(lambda: ok(st.get(f"{STUDIO}/api/projects/{pid}/files"))["files"], 60, "file list")
    paths = {f["path"]: f for f in files}
    assert "web/src/App.tsx" in paths and "api/main.py" in paths
    assert paths["AGENTS.md"]["locked"] and not paths["web/src/App.tsx"]["locked"]
    assert not any(p.startswith((".git/", "node_modules/", ".data/")) for p in paths)
    src = ok(st.get(f"{STUDIO}/api/projects/{pid}/files/web/src/main.tsx"))  # App.tsx starts empty
    assert "@miira/vibe" in src["content"]
    hist = ok(st.get(f"{STUDIO}/api/projects/{pid}/history"))
    assert hist["commits"], "template seeding should leave an initial commit"
    S["initial_sha"] = hist["commits"][-1]["sha"]


def test_04_preview_through_edge():
    st, pid = S["studio"], S["fid"]
    url = ok(st.post(f"{STUDIO}/api/projects/{pid}/preview-session", json={}))["url"]
    pv = client()
    r = follow(pv, pv.get(url))
    assert r.status_code == 200 and "<div id=\"root\">" in r.text, r.text[:300]
    S["preview"] = pv
    S["preview_origin"] = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    # the dev backend behind Vite's /api proxy sees the owner identity from the edge
    who = ok(pv.get(f"{S['preview_origin']}/api/hej"))
    assert who["bruger"]["role"] == "owner"
    # no cookie -> sent to log in, never served
    r = client().get(f"{S['preview_origin']}/")
    assert r.status_code in (302, 401, 403)


# --- 5: a chat turn ------------------------------------------------------------------------


def test_05_chat_turn_commits_and_streams():
    st, pid = S["studio"], S["fid"]
    events: list[tuple[str, Any]] = []

    def listen() -> None:
        with st.stream("GET", f"{STUDIO}/api/projects/{pid}/events", timeout=TURN_TIMEOUT + 30) as r:
            name = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name:
                    events.append((name, json.loads(line[5:])))
                    if name == "turn.finished":
                        return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(1)
    thread = ok(st.post(f"{STUDIO}/api/projects/{pid}/threads", json={"title": "E2E"}), 201)
    S["thread"] = thread["id"]
    S["marker"] = f"E2E-MARKER-{uuid.uuid4().hex[:8]}"
    r = ok(st.post(f"{STUDIO}/api/threads/{thread['id']}/messages",
                   json={"text": f"Skriv {S['marker']} som overskrift i App.tsx"}), 202)
    assert r["queued"] is False
    t.join(TURN_TIMEOUT)
    names = [n for n, _ in events]
    assert "turn.started" in names and "turn.finished" in names, names
    fin = dict(events)["turn.finished"]
    assert fin["status"] == "done", fin
    assert fin["commitSha"] and "web/src/App.tsx" in fin["changedFiles"], fin
    assert any(n == "message.part" and d["part"]["type"] == "tool" for n, d in events), "tool step streamed"
    S["turn_sha"] = fin["commitSha"]

    src = ok(st.get(f"{STUDIO}/api/projects/{pid}/files/web/src/App.tsx"))["content"]
    assert S["marker"] in src
    head = ok(st.get(f"{STUDIO}/api/projects/{pid}/history"))["commits"][0]
    assert head["sha"] == S["turn_sha"] and head["threadId"] == S["thread"]
    diff = ok(st.get(f"{STUDIO}/api/projects/{pid}/history/{S['turn_sha']}/diff"))
    assert any(f["path"] == "web/src/App.tsx" and S["marker"] in f["patch"] for f in diff["files"])
    msgs = ok(st.get(f"{STUDIO}/api/threads/{thread['id']}/messages"))
    assert [m["role"] for m in msgs][:1] == ["user"] and msgs[-1]["role"] == "assistant"

    budget = ok(st.get(f"{STUDIO}/api/projects/{pid}/budget"))
    assert budget["build"]["usedTokens"] >= 120, budget
    # the preview serves the new code (Vite transforms the module on request)
    assert S["marker"] in S["preview"].get(f"{S['preview_origin']}/src/App.tsx").text


# --- 6-7: publish and the app as owner ----------------------------------------------------


def test_06_publish_builds_and_runs():
    st, pid = S["studio"], S["fid"]
    r = ok(st.post(f"{STUDIO}/api/projects/{pid}/publish", json={"sha": S["turn_sha"]}))
    assert r["publishedSha"].startswith(S["turn_sha"][:7])
    S["app_url"] = r["url"].rstrip("/")
    assert ok(st.get(f"{STUDIO}/api/projects/{pid}/history"))["publishedSha"] == r["publishedSha"]


def test_07_app_as_owner():
    st, pid = S["studio"], S["fid"]
    url = ok(st.post(f"{STUDIO}/api/projects/{pid}/app-session"))["url"]
    app = client()
    r = follow(app, app.get(url))
    if r.status_code != 200:  # runtime may still be starting
        r = wait_for(lambda: (x := app.get(S["app_url"] + "/")).status_code == 200 and x, 60, "app up")
    assert '<div id="root">' in r.text
    js = re.search(r'src="(/assets/[^"]+\.js)"', r.text)
    assert js and S["marker"] in app.get(S["app_url"] + js.group(1)).text, "built bundle contains the turn's change"
    who = ok(app.get(f"{S['app_url']}/api/hej"))
    assert who["bruger"]["name"] == TEACHER_NAME and who["bruger"]["role"] == "owner"
    # identity headers from the client are stripped by the edge
    forged = client().get(f"{S['app_url']}/api/hej", headers={"X-Vibe-User": "x", "X-Vibe-Role": "owner"})
    assert forged.status_code in (302, 401, 403)
    S["app"] = app


# --- 8: a student, sharing, members -------------------------------------------------------


def student_into_app(user_id: str) -> tuple[httpx.Client, httpx.Response]:
    """Sign a student in through the full OIDC flow, starting from the app URL."""
    c = miira_login(user_id)
    r = follow(c, c.get(S["app_url"] + "/"))
    if r.status_code == 200 and "oidc/authorize/select" in r.text:  # fake Miira's login form
        q = dict(httpx.URL(str(r.request.url)).params)
        form = {k: q.get(k, "") for k in ("redirect_uri", "state", "nonce", "code_challenge", "client_id")}
        r = follow(c, c.post(f"{MIIRA}/oidc/authorize/select", data={**form, "user_id": user_id}))
    return c, r


def test_08_student_denied_then_shared_via_class():
    _, r = student_into_app(STUDENT)
    assert r.status_code == 403, f"unshared student must not get in: {r.status_code} {r.text[:200]}"

    elever = miira_share_with_class(S["miira"], S["fid"])

    def admitted():
        c, r = student_into_app(STUDENT)
        return r.status_code == 200 and (c, r)

    c, r = wait_for(admitted, 30, "student admitted after share (webhook)", every=2)
    who = ok(c.get(f"{S['app_url']}/api/hej"))
    assert who["bruger"]["name"] == STUDENT_NAME and who["bruger"]["role"] == "viewer"
    assert CLASS_NAME in who["bruger"]["classes"]
    assert c.get(f"{S['app_url']}/api/klassen").status_code == 403, "viewer can't see the class list"
    assert client().get(f"{STUDIO}/api/projects/{S['fid']}").status_code == 401

    klassen = wait_for(lambda: (k := ok(S["app"].get(f"{S['app_url']}/api/klassen"))["klasser"]).get(CLASS_NAME) and k,
                       30, "class list in app")
    # Every elev of the class, expanded from the one class share; the teacher may be on the
    # roster too (a real Miira records who teaches a class there).
    assert STUDENT_NAME in klassen[CLASS_NAME] and len(klassen[CLASS_NAME]) >= elever, klassen
    # the platform never leaks the service-level sub into the app
    assert "sub" not in json.dumps(ok(S["app"].get(f"{S['app_url']}/api/_vibe/members")))


# --- 9: restore ---------------------------------------------------------------------------


def test_09_restore_adds_a_commit():
    st, pid = S["studio"], S["fid"]
    before = ok(st.get(f"{STUDIO}/api/projects/{pid}/history"))["commits"]
    c = ok(st.post(f"{STUDIO}/api/projects/{pid}/restore", json={"sha": S["initial_sha"]}))
    after = ok(st.get(f"{STUDIO}/api/projects/{pid}/history"))["commits"]
    assert after[0]["sha"] == c["sha"] and [x["sha"] for x in after[1:]] == [x["sha"] for x in before]
    src = ok(st.get(f"{STUDIO}/api/projects/{pid}/files/web/src/App.tsx"))["content"]
    assert S["marker"] not in src


# --- 10: admin API and budget enforcement -------------------------------------------------


def test_10_admin_usage_and_budget_limits():
    """Build budget: a hard limit refuses turns, a reset lifts it. App budget: the published
    app's AI calls are metered per Vibe, blocked at the limit, and a grant lifts it.
    Only resets and grants on this run's own project are used, so runs don't accumulate state."""
    st, app = S["studio"], S["app"]
    me = ok(st.get(f"{STUDIO}/api/me"))["user"]["sub"]
    # an AI call from inside the published app (SDK ai.chat -> egress -> app bucket)
    assert ok(app.post(f"{S['app_url']}/api/spoerg", json={"spoergsmaal": "Hvad er en viking?"}))["svar"]

    status, usage = admin_call("GET", "/admin/v1/usage")
    assert status == 200, usage
    mine = [p for p in usage["byProject"] if p.get("id") == S["fid"]]
    assert {p["bucket"] for p in mine} >= {"build", "app"}, usage

    status, settings = admin_call("GET", "/admin/v1/budget-settings")
    settings["build"]["byRole"]["laerer"] = {"nerfLimitTokens": None, "hardLimitTokens": 10}
    settings["app"]["perProject"] = {"nerfLimitTokens": None, "hardLimitTokens": 10}
    assert admin_call("PUT", "/admin/v1/budget-settings", settings)[0] == 200
    try:
        r = st.post(f"{STUDIO}/api/threads/{S['thread']}/messages", json={"text": "en gang til"})
        assert r.status_code == 429 and r.json()["error"] == "budget_blocked" and r.json()["retryAt"], r.text
        r = app.post(f"{S['app_url']}/api/spoerg", json={"spoergsmaal": "igen"})
        assert r.status_code == 429, f"app bucket should be blocked: {r.status_code} {r.text[:200]}"

        status, limited = admin_call("GET", "/admin/v1/budgets/limited")
        keys = {(i["subject"], i["id"], i["bucket"], i["state"]) for i in limited["items"]}
        assert ("user", me, "build", "blocked") in keys and ("project", S["fid"], "app", "blocked") in keys, limited

        assert admin_call("POST", "/admin/v1/budgets/reset", {"subject": "user", "id": me, "bucket": "build"})[0] == 200
        assert ok(st.get(f"{STUDIO}/api/projects/{S['fid']}/budget"))["build"]["state"] != "blocked"
        assert admin_call("POST", "/admin/v1/budgets/grant",
                          {"subject": "project", "id": S["fid"], "bucket": "app", "extraTokens": 100000})[0] == 200
        assert ok(app.post(f"{S['app_url']}/api/spoerg", json={"spoergsmaal": "nu igen"}))["svar"]
    finally:
        settings["build"]["byRole"]["laerer"] = {"nerfLimitTokens": None, "hardLimitTokens": None}
        settings["app"]["perProject"] = {"nerfLimitTokens": None, "hardLimitTokens": None}
        admin_call("PUT", "/admin/v1/budget-settings", settings)


# --- 11: "fra bunden" -----------------------------------------------------------------------


def test_11_scratch_project_is_never_served():
    import io
    import zipfile

    fid = miira_new_vibe(S["miira"])
    st = client()
    r = ok(st.post(f"{STUDIO}/api/auth/launch", json={"launchToken": miira_launch(S["miira"], fid)}))
    st.headers["authorization"] = f"Bearer {r['token']}"
    p = ok(st.post(f"{STUDIO}/api/projects", json={"fileId": fid, "kind": "scratch", "title": "Trafiklys"}), 201)
    assert p["kind"] == "scratch" and p["previewUrl"] is None and p["appUrl"] is None
    files = {f["path"] for f in ok(st.get(f"{STUDIO}/api/projects/{fid}/files"))["files"]}
    assert files == {"AGENTS.md", "README.md"}, files
    assert [c["message"] for c in ok(st.get(f"{STUDIO}/api/projects/{fid}/history"))["commits"]] == ["Start fra bunden"]

    thread = ok(st.post(f"{STUDIO}/api/projects/{fid}/threads", json={"title": "Arduino"}), 201)
    marker = f"E2E-SCRATCH-{uuid.uuid4().hex[:8]}"
    ok(st.post(f"{STUDIO}/api/threads/{thread['id']}/messages", json={"text": f"Skriv {marker} som overskrift i App.tsx"}), 202)
    head = wait_for(
        lambda: (c := ok(st.get(f"{STUDIO}/api/projects/{fid}/history"))["commits"][0])["threadId"] == thread["id"] and c,
        TURN_TIMEOUT, "the AI turn's commit in the scratch project", every=2,
    )
    z = zipfile.ZipFile(io.BytesIO(st.get(f"{STUDIO}/api/projects/{fid}/download").content))
    assert "README.md" in z.namelist()
    assert any(marker in z.read(n).decode(errors="replace") for n in head["files"] if n in z.namelist()), head

    for path in ("publish", "preview-session", "app-session"):
        r = st.post(f"{STUDIO}/api/projects/{fid}/{path}", json={"sha": head["sha"]})
        assert r.status_code == 409 and r.json()["error"] == "not_a_webapp", (path, r.text)
    # The edge asks the control plane about every app/preview host; a scratch
    # project's hosts are unknown to it, so nothing is ever served there.
    for host in (p["slug"], f"preview-{p['slug']}"):
        url = S["app_url"].replace(S["project"]["slug"], host) + "/"
        r = client().get(url)
        assert r.status_code == 404 or (DOMAIN and r.status_code >= 400), f"{url} must not be served: {r.status_code}"
