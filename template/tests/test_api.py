"""Template backend tests: runs api/main.py with the SDK's dev identity.

Run from template/:
  uv run --with-requirements api/requirements.lock --with ../sdk/python --with pytest --with respx pytest -q tests
"""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

API = Path(__file__).resolve().parents[1] / "api"
PLATFORM = "http://egress.test:8200"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_MODE", "dev")
    monkeypatch.setenv("VIBE_DB_PATH", str(tmp_path / "app.sqlite"))
    monkeypatch.delenv("VIBE_PLATFORM_URL", raising=False)
    monkeypatch.delenv("VIBE_EDGE_SECRET", raising=False)
    from miira_vibe import members

    members.invalidate()
    monkeypatch.syspath_prepend(str(API))
    spec = importlib.util.spec_from_file_location("template_main", API / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as c:
        yield c
    members._callbacks.clear()
    sys.modules.pop("template_main", None)


def test_hej_uses_dev_identity(client):
    body = client.get("/hej").json()
    assert body["bruger"]["role"] == "owner"
    assert body["besked"].startswith("Hej ")


def test_klassen_requires_editor(client, monkeypatch):
    klasser = client.get("/klassen").json()["klasser"]
    assert "7.A" in klasser
    monkeypatch.setenv("VIBE_DEV_ROLE", "viewer")
    assert client.get("/klassen").status_code == 403


def test_platform_routes_mounted(client):
    assert client.get("/_vibe/me").json()["id"] == "dev-owner"
    assert len(client.get("/_vibe/members").json()["members"]) >= 2


@respx.mock
def test_spoerg_and_budget(client, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", PLATFORM + "/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "tok")
    route = respx.post(f"{PLATFORM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Fotosyntese er…"}}]}))
    assert client.post("/spoerg", json={"spoergsmaal": "Hvad er fotosyntese?"}).json() == {"svar": "Fotosyntese er…"}
    assert route.calls.last.request.headers["x-vibe-user"] == "dev-owner"
    route.mock(return_value=httpx.Response(429, json={"error": {"type": "budget_exceeded", "retry_at": None}}))
    assert client.post("/spoerg", json={"spoergsmaal": "igen"}).status_code == 429
