from askphilo import create_app
from askphilo.config import load_config


def test_health_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["app"] == "AskPhilo"
    assert data["ai_mode"] == "mock"
    assert isinstance(data["firebase_configured"], bool)
    assert res.headers["Cache-Control"] == "no-store"


def test_health_does_not_leak_secrets():
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "super-secret-session-key",
            "ANTHROPIC_API_KEY": "sk-test-should-never-appear",
            "FIREBASE_CREDENTIALS_PATH": "/etc/secrets/firebase-service-account.json",
        }
    )
    body = app.test_client().get("/health").get_data(as_text=True)
    assert "super-secret-session-key" not in body
    assert "sk-test-should-never-appear" not in body
    assert "/etc/secrets" not in body
    assert "firebase-service-account" not in body


def test_testing_forces_mock_mode():
    app = create_app({"TESTING": True, "AI_MODE": "anthropic"})
    assert app.config["AI_MODE"] == "mock"
    assert app.config["DATA_BACKEND"] == "memory"


def test_unknown_ai_mode_falls_back_to_mock():
    assert load_config({"AI_MODE": "live"})["AI_MODE"] == "mock"
    assert load_config({"AI_MODE": "anthropic"})["AI_MODE"] == "anthropic"
    assert load_config({})["AI_MODE"] == "mock"


def test_api_key_alone_does_not_enable_live_mode():
    cfg = load_config({"ANTHROPIC_API_KEY": "sk-something"})
    assert cfg["AI_MODE"] == "mock"


def test_production_cookie_flags():
    cfg = load_config({"APP_ENV": "production"})
    assert cfg["SESSION_COOKIE_SECURE"] is True
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True
    assert cfg["ENABLE_DEV_ROUTES"] is False


def test_landing_page_and_static(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "AskPhilo" in html
    assert "/static/css/app.css" in html
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/img/logo.svg").status_code == 200


def test_favicon(client):
    res = client.get("/favicon.ico")
    assert res.status_code == 200
    assert res.mimetype == "image/vnd.microsoft.icon"


def test_manifest_and_robots(client):
    res = client.get("/manifest.webmanifest")
    assert res.status_code == 200 and res.mimetype == "application/manifest+json"
    data = res.get_json(force=True)
    assert data["short_name"] == "AskPhilo" and data["start_url"] == "/home"
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /api/" in robots and "Disallow: /c/" in robots
    assert 'rel="manifest"' in client.get("/").get_data(as_text=True)


def test_security_headers(client):
    res = client.get("/")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in res.headers["Content-Security-Policy"]
