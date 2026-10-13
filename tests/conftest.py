import re

import pytest

from askphilo import create_app
from askphilo.data import get_repository

CSRF = "test-csrf-token"


@pytest.fixture()
def app():
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret-key"})
    yield app


@pytest.fixture()
def repo(app):
    return get_repository(app)


@pytest.fixture()
def client(app):
    return app.test_client()


def set_csrf(client, token=CSRF):
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


def csrf_from(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token")


def signup(client, username="hong123", password="password123", name="홍길동", confirm=None):
    token = csrf_from(client) or set_csrf(client)
    return client.post(
        "/signup",
        data={
            "csrf_token": token,
            "name": name,
            "username": username,
            "password": password,
            "password_confirm": password if confirm is None else confirm,
        },
    )


def login(client, username="hong123", password="password123", next_url=None):
    token = csrf_from(client) or set_csrf(client)
    data = {"csrf_token": token, "username": username, "password": password}
    if next_url:
        data["next"] = next_url
    return client.post("/login", data=data)


def api_headers(client):
    return {"X-CSRF-Token": csrf_from(client) or set_csrf(client)}


@pytest.fixture()
def logged_in(client):
    res = signup(client)
    assert res.status_code == 302, res.get_data(as_text=True)
    return client


def meta_csrf(html: str) -> str:
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else ""
