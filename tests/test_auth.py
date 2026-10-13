import logging
import threading

import pytest

from askphilo import create_app
from askphilo.data import get_repository
from askphilo.data.memory_repo import MemoryRepository
from askphilo.services import accounts

from .conftest import csrf_from, login, set_csrf, signup


def test_signup_creates_user_with_hash_only(client, repo):
    res = signup(client, username="Hong_123", password="s3cret-pass", name="  홍길동 ")
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/home")
    user = repo.get_user_by_username("hong_123")
    assert user["name"] == "홍길동"
    assert user["username"] == "Hong_123"
    assert user["username_normalized"] == "hong_123"
    assert user["schema_version"] == 1
    assert "password" not in user
    assert user["password_hash"] != "s3cret-pass"
    assert "s3cret-pass" not in str(user)
    assert repo.username_index["hong_123"]["user_id"] == user["id"]
    with client.session_transaction() as sess:
        assert sess["uid"] == user["id"]


def test_username_is_case_insensitive(client):
    assert signup(client, username="UserABC").status_code == 302
    other = client.application.test_client()
    res = signup(other, username="userabc")
    assert res.status_code == 400
    assert "이미 사용 중인 아이디예요" in res.get_data(as_text=True)


@pytest.mark.parametrize(
    "fields, field_key",
    [
        ({"username": "ab"}, "username"),
        ({"username": "a" * 21}, "username"),
        ({"username": "한글아이디"}, "username"),
        ({"username": "bad-name"}, "username"),
        ({"password": "short"}, "password"),
        ({"confirm": "different123"}, "password_confirm"),
        ({"name": "   "}, "name"),
        ({"name": "가" * 31}, "name"),
    ],
)
def test_signup_validation(client, fields, field_key):
    kwargs = {"username": "valid_user", "password": "password123", "name": "테스트"}
    kwargs.update(fields)
    res = signup(client, **kwargs)
    assert res.status_code == 400
    errors = accounts.validate_signup(
        kwargs["name"], kwargs["username"], kwargs["password"], kwargs.get("confirm", kwargs["password"])
    )
    assert field_key in errors


def test_concurrent_duplicate_signup_only_one_wins():
    repo = MemoryRepository()
    results = []
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        try:
            accounts.signup(repo, name=f"user{i}", username="SameName",
                            password="password123", password_confirm="password123")
            results.append("ok")
        except accounts.SignupError:
            results.append("taken")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert results.count("taken") == 7
    assert len(repo.users) == 1


def test_login_success_and_failure(app):
    c1 = app.test_client()
    signup(c1)
    c2 = app.test_client()
    bad = login(c2, password="wrong-password")
    assert bad.status_code == 400
    assert "아이디 또는 비밀번호가 맞지 않아요" in bad.get_data(as_text=True)
    ok = login(c2, username="HONG123")  # case-insensitive
    assert ok.status_code == 302
    assert ok.headers["Location"].endswith("/home")
    user = get_repository(app).get_user_by_username("hong123")
    assert user["last_login_at"] is not None


def test_login_unknown_user(client):
    res = login(client, username="nobody_here")
    assert res.status_code == 400


def test_login_next_redirect_is_safe(app):
    signup(app.test_client())
    c = app.test_client()
    res = login(c, next_url="/history")
    assert res.headers["Location"].endswith("/history")
    c2 = app.test_client()
    res = login(c2, next_url="//evil.example.com/")
    assert res.headers["Location"].endswith("/home")
    c3 = app.test_client()
    res = login(c3, next_url="https://evil.example.com/")
    assert res.headers["Location"].endswith("/home")


@pytest.mark.parametrize("path", ["/home", "/settings", "/history", "/new", "/c/abc"])
def test_pages_require_login(client, path):
    res = client.get(path)
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_logout_requires_post_with_csrf(logged_in):
    assert logged_in.get("/logout").status_code == 405
    res = logged_in.post("/logout", data={"csrf_token": "wrong"})
    assert res.status_code == 400
    with logged_in.session_transaction() as sess:
        assert "uid" in sess
    res = logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in)})
    assert res.status_code == 302
    with logged_in.session_transaction() as sess:
        assert "uid" not in sess
    assert logged_in.get("/home").status_code == 302


def test_signup_without_csrf_is_rejected(client, repo):
    res = client.post("/signup", data={"name": "a", "username": "nocsrf", "password": "password123",
                                       "password_confirm": "password123"})
    assert res.status_code == 302  # bounced back to the form with a message
    assert repo.get_user_by_username("nocsrf") is None


def test_login_rate_limit(app):
    signup(app.test_client())
    c = app.test_client()
    set_csrf(c)
    for _ in range(10):
        assert login(c, password="wrong-password").status_code == 400
    res = login(c, password="password123")
    assert res.status_code == 429


def test_passwords_never_logged(app, caplog):
    caplog.set_level(logging.DEBUG)
    c = app.test_client()
    signup(c, username="logcheck", password="VerySecretPw!")
    c2 = app.test_client()
    login(c2, username="logcheck", password="VerySecretPw!")
    login(app.test_client(), username="logcheck", password="WrongSecretPw!")
    assert "VerySecretPw!" not in caplog.text
    assert "WrongSecretPw!" not in caplog.text
    assert "scrypt:" not in caplog.text and "pbkdf2:" not in caplog.text


def test_home_shows_greeting(logged_in):
    html = logged_in.get("/home").get_data(as_text=True)
    assert "안녕하세요, 홍길동님" in html
    assert "오늘은 어떤 이야기를" in html
    assert "새 상담 시작" in html


def test_settings_page(logged_in):
    html = logged_in.get("/settings").get_data(as_text=True)
    assert "hong123" in html
    assert "로그아웃" in html
    assert 'value="system"' in html and 'value="dark"' in html


def test_session_cookie_flags(client):
    res = signup(client)
    cookie = res.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_landing_redirects_when_logged_in(logged_in):
    res = logged_in.get("/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/home")


def test_production_without_credentials_fails_gracefully(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "x"})
    app.config.update(IS_PRODUCTION=True, DATA_BACKEND="auto",
                      FIREBASE_CREDENTIALS_PATH=str(tmp_path / "missing.json"))
    c = app.test_client()
    res = signup(c, username="produser")
    assert res.status_code == 503
    assert "잠시 문제가 생겼어요" in res.get_data(as_text=True)
