"""Pure unit tests (no database, no event loop)."""


def test_demux_log_frames():
    from sloptown.orchestrator import _demux

    frame = lambda kind, text: bytes([kind, 0, 0, 0]) + len(text).to_bytes(4, "big") + text  # noqa: E731
    assert _demux(frame(1, b"vite ") + frame(2, b"error")) == "vite error"
    assert _demux(b"plain text") == "plain text"

def test_container_env_points_vite_at_the_edge(monkeypatch):
    from sloptown.config import get_settings
    from sloptown.orchestrator import container_env

    env = container_env(token="vt_x", seed="s1", mode="dev")
    assert env["VIBE_APPS_DOMAIN"] == get_settings().apps_domain
    assert env["VIBE_HMR_CLIENT_PORT"] == "8080"
    assert env["OPENAI_API_KEY"] == "vt_x" and "sk-" not in "".join(env.values())
    assert env["VIBE_AGENT_MODEL"] == get_settings().agent_model and env["VIBE_AI_MODEL"]
    monkeypatch.setattr(get_settings(), "apps_port", "")
    monkeypatch.setattr(get_settings(), "apps_scheme", "https")
    assert container_env(token="vt_x", seed="s1", mode="dev")["VIBE_HMR_CLIENT_PORT"] == "443"


def test_reserved_labels_are_never_app_hosts():
    from sloptown.edge import parse_host

    assert parse_host("studio.apps.sloptown.localhost") is None
    assert parse_host("preview-x1.apps.sloptown.localhost") == ("preview", "x1")


def test_miira_backchannel_maps_the_issuer_origin(monkeypatch):
    """The real Miira's issuer has a path (<origin>/api/vibe/oidc), fake-miira's doesn't;
    discovery is issuer + /.well-known/..., and every URL on the issuer's origin goes to
    miira_api_base instead (docs/miira-integration.md §4)."""
    from sloptown.config import get_settings
    from sloptown.miira import miira

    s = get_settings()
    monkeypatch.setattr(s, "miira_issuer", "https://staging.miira.dk/api/vibe/oidc")
    monkeypatch.setattr(s, "miira_api_base", "http://miira-api:8000")
    assert miira.backchannel("https://staging.miira.dk/api/vibe/oidc/jwks") == "http://miira-api:8000/api/vibe/oidc/jwks"
    assert miira.backchannel("https://staging.miira.dk") == "http://miira-api:8000"
    assert miira.backchannel("https://staging.miira.dk.evil.example/x") == "https://staging.miira.dk.evil.example/x"
    monkeypatch.setattr(s, "miira_issuer", "http://miira.sloptown.localhost:8080")
    monkeypatch.setattr(s, "miira_api_base", "http://sloptown-fake-miira:8100")
    assert miira.backchannel("http://miira.sloptown.localhost:8080/oidc/token") == "http://sloptown-fake-miira:8100/oidc/token"


def test_each_vibe_gets_its_own_edge_secret():
    """A secret read out of one Vibe's environment must not vouch for another Vibe."""
    from sloptown.orchestrator import container_env
    from sloptown.tokens import edge_secret_for

    a = container_env(token="vt_a", seed="seed-a", mode="runtime")["VIBE_EDGE_SECRET"]
    b = container_env(token="vt_b", seed="seed-b", mode="runtime")["VIBE_EDGE_SECRET"]
    assert a != b and a == edge_secret_for("seed-a")
