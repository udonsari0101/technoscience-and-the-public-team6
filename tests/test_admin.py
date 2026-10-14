"""Admin role and dashboard."""

import importlib.util
from pathlib import Path

from askphilo.ai import set_provider
from askphilo.views.admin import estimate_cost

from .conftest import api_headers, csrf_from, login, signup
from .helpers import BillableCountingProvider

ROOT = Path(__file__).resolve().parent.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_admin(app, username="admin", password="Admin-pass-1"):
    from askphilo.data import get_repository

    return load_script("create_admin").create_admin(get_repository(app), username, password)


def test_admin_login_lands_on_dashboard(app):
    make_admin(app)
    c = app.test_client()
    res = login(c, username="admin", password="Admin-pass-1")
    assert res.status_code == 302 and res.headers["Location"].endswith("/admin")
    page = c.get("/admin")
    assert page.status_code == 200 and "관리자 대시보드" in page.get_data(as_text=True)
    assert c.get("/").headers["Location"].endswith("/admin")


def test_dashboard_is_hidden_from_others(app, client):
    assert client.get("/admin").status_code == 302  # anonymous → login
    signup(client, username="normal_user")
    assert client.get("/admin").status_code == 404  # regular users don't see it


def test_role_is_checked_against_the_database(app):
    from askphilo.data import get_repository

    user = make_admin(app)
    c = app.test_client()
    login(c, username="admin", password="Admin-pass-1")
    get_repository(app).set_user_fields(user["id"], {"role": None})  # demoted
    assert c.get("/admin").status_code == 404


def test_dashboard_lists_users_and_usage_without_secrets(app):
    set_provider(app, BillableCountingProvider())
    alice = app.test_client()
    signup(alice, username="alice_1", password="alice-password")
    alice.post("/api/conversations", json={"philosopher_id": "plato", "content": "연락 문제"},
               headers=api_headers(alice))
    make_admin(app)
    c = app.test_client()
    login(c, username="admin", password="Admin-pass-1")
    html = c.get("/admin").get_data(as_text=True)
    assert "alice_1" in html and "사용자 2명" in html and "관리자" in html
    assert "1,000" in html  # input tokens from the billable test provider
    assert "alice-password" not in html and "scrypt" not in html and "pbkdf2" not in html
    assert "연락 문제" not in html  # no conversation content


def test_cost_estimate_needs_configured_prices(app):
    cfg = dict(app.config)
    totals = {"input_tokens": 1_000_000, "summary_input_tokens": 0, "output_tokens": 100_000,
              "summary_output_tokens": 0, "cache_read_input_tokens": 2_000_000,
              "cache_creation_input_tokens": 0}
    assert estimate_cost(totals, cfg) is None
    cfg.update(ANTHROPIC_PRICE_INPUT_PER_MTOK=2.0, ANTHROPIC_PRICE_OUTPUT_PER_MTOK=10.0,
               ANTHROPIC_PRICE_CACHE_READ_PER_MTOK=0.2, ANTHROPIC_PRICE_CACHE_WRITE_PER_MTOK=2.5)
    assert estimate_cost(totals, cfg) == 3.4


def test_budget_remaining_shown(app):
    app.config.update(ANTHROPIC_PRICE_INPUT_PER_MTOK=2.0, ANTHROPIC_PRICE_OUTPUT_PER_MTOK=10.0,
                      ANTHROPIC_MONTHLY_BUDGET_USD=5.0)
    set_provider(app, BillableCountingProvider())
    u = app.test_client()
    signup(u, username="bob_22")
    u.post("/api/conversations", json={"philosopher_id": "plato", "content": "고민"}, headers=api_headers(u))
    make_admin(app)
    c = app.test_client()
    login(c, username="admin", password="Admin-pass-1")
    html = c.get("/admin").get_data(as_text=True)
    assert "남은 예산 $" in html and "/ $5.00" in html


def test_create_admin_promotes_existing_user(app, client):
    from askphilo.data import get_repository

    signup(client, username="admin")
    user = make_admin(app, password="New-admin-pass")
    assert user["created"] is False
    assert get_repository(app).get_user(user["id"])["role"] == "admin"
    assert login(app.test_client(), username="admin", password="New-admin-pass").status_code == 302


def test_home_shows_admin_link_only_to_admin(app, client):
    signup(client, username="plain_user")
    assert 'href="/admin"' not in client.get("/home").get_data(as_text=True)
    make_admin(app)
    c = app.test_client()
    login(c, username="admin", password="Admin-pass-1")
    assert 'href="/admin"' in c.get("/home").get_data(as_text=True)
