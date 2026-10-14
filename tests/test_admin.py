"""Admin role and dashboard."""

import importlib.util
from pathlib import Path

from askphilo.ai import set_provider
from askphilo.views.admin import credit_summary, parse_usd

from .conftest import api_headers, csrf_from, login, signup
from .helpers import BillableCountingProvider, SonnetBillableProvider

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


def send_one(app, username="bob_22"):
    u = app.test_client()
    signup(u, username=username)
    res = u.post("/api/conversations", json={"philosopher_id": "plato", "content": "고민"},
                 headers=api_headers(u))
    assert res.status_code == 201, res.get_json()


def admin_client(app):
    make_admin(app)
    c = app.test_client()
    login(c, username="admin", password="Admin-pass-1")
    return c


def test_each_reply_records_its_cost(app):
    from askphilo.data import get_repository
    from askphilo.services.quota import today_kst

    set_provider(app, SonnetBillableProvider())
    send_one(app)
    repo = get_repository(app)
    # 1000 in × $2 + 80 out × $10 + 600 cache read × $0.10 per 1M tokens
    assert repo.get_usage_total()["cost_micro_usd"] == 2860
    assert repo.get_usage_days([today_kst()])[0]["cost_micro_usd"] == 2860
    conv_usage = next(iter(repo.iter_conversation_usage()))["usage"]
    assert conv_usage["cost_micro_usd"] == 2860


def test_unpriced_model_records_no_cost(app):
    from askphilo.data import get_repository

    set_provider(app, BillableCountingProvider())  # model "test-model"
    send_one(app)
    totals = get_repository(app).get_usage_total()
    assert totals["api_calls"] == 1 and "cost_micro_usd" not in totals


def test_credit_card_asks_for_amount_until_set(app):
    html = admin_client(app).get("/admin").get_data(as_text=True)
    assert "충전한 금액을 아래에 입력하면" in html


def test_credit_remaining_after_saving_amounts(app):
    set_provider(app, SonnetBillableProvider())
    send_one(app)
    c = admin_client(app)
    page = c.get("/admin")
    res = c.post("/admin/billing", data={"csrf_token": csrf_from(c), "credit_usd": "$20",
                                         "outside_spend_usd": "0.28"})
    assert res.status_code == 302 and res.headers["Location"].endswith("/admin#credit")
    html = c.get("/admin").get_data(as_text=True)
    # 20 − 0.00286 (this reply) − 0.28 (outside) = 19.71714
    assert "$19.72" in html and "/ $20.00" in html
    assert "서비스 $0.0029" in html and "서비스 밖 $0.28" in html
    assert "답변 1개 평균 $0.0029" in html
    assert page.status_code == 200


def test_billing_form_rejects_bad_amounts(app):
    from askphilo.data import get_repository

    c = admin_client(app)
    for bad in ("-1", "abc", "", "1e9", "nan"):
        c.post("/admin/billing", data={"csrf_token": csrf_from(c), "credit_usd": bad})
    assert get_repository(app).get_billing_settings() == {}
    assert "금액은 0 이상의 숫자로" in c.get("/admin").get_data(as_text=True)


def test_billing_form_is_admin_only_and_csrf_protected(app, client):
    signup(client, username="normal_user")
    res = client.post("/admin/billing", data={"csrf_token": csrf_from(client), "credit_usd": "20"})
    assert res.status_code == 404
    c = admin_client(app)
    assert c.post("/admin/billing", data={"credit_usd": "20"}).status_code == 400


def test_credit_summary_math():
    s = credit_summary({"credit_usd": 20.0, "outside_spend_usd": 0.5},
                       {"cost_micro_usd": 1_500_000, "replies": 300})
    assert s["used"] == 2.0 and s["remaining"] == 18.0
    assert s["per_reply"] == 0.005 and s["replies_left"] == 3600
    assert credit_summary({}, {})["remaining"] is None
    assert parse_usd(" $1,234.567 ") == 1234.57


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
