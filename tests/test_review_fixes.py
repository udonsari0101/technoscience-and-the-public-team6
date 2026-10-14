"""Regression tests for issues found in the security / spec review."""

import threading
from datetime import timedelta

import pytest

from askphilo import create_app
from askphilo.ai import set_provider
from askphilo.auth import safe_next
from askphilo.data import DataUnavailable
from askphilo.data.base import utcnow
from askphilo.data.memory_repo import MemoryRepository
from askphilo.ratelimit import RateLimiter
from askphilo.services import accounts
from askphilo.services.conversations import ChatSettings, ConversationService

from .conftest import api_headers, csrf_from, login, signup
from .helpers import CountingProvider, FailingProvider, repo_with_user

USER = "user-1"


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


def create(client, content="고민이 있어", philosopher="plato"):
    return client.post("/api/conversations", json={"philosopher_id": philosopher, "content": content},
                       headers=api_headers(client))


# ---------------------------------------------------------------- auth / input
@pytest.mark.parametrize("target", [
    "/\t/evil.example.com/", "/\n/evil.example.com", "//evil.example.com", "/\\evil.example.com",
    "https://evil.example.com", " /home", "/home\r\nSet-Cookie: x=1", "", None, "home",
])
def test_safe_next_rejects_tricks(target):
    assert safe_next(target) is None


def test_safe_next_allows_paths():
    assert safe_next("/history") == "/history"
    assert safe_next("/c/abc?x=1") == "/c/abc?x=1"


def test_tab_open_redirect_is_blocked_for_logged_in_user(logged_in):
    res = logged_in.get("/login?next=/%09/evil.example.com")
    assert res.status_code == 302 and res.headers["Location"].endswith("/home")


def test_limiter_checks_do_not_create_state():
    limiter = RateLimiter()
    for i in range(1000):
        assert limiter.allowed(f"probe-{i}", 5, 60)
    assert len(limiter) == 0


def test_limiter_prunes_with_each_keys_own_window():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    limiter.record("login", 600)
    now[0] = 120  # older than the chat window, inside the login window
    for i in range(600):
        limiter.hit(f"chat-{i}", 5, 60)
    assert not limiter.allowed("login", 1, 600)  # still counted


def test_login_garbage_usernames_do_not_grow_limiter(app):
    c = app.test_client()
    signup(app.test_client())
    for i in range(40):
        login(c, username=f"{'x' * 5000}{i}", password="whatever123")
    limiter = app.extensions["askphilo.limiter"]
    assert len(limiter) <= 3  # ip key + shared invalid-username key (+ signup ip)


def test_reserved_usernames_rejected():
    assert not accounts.is_valid_username("__ab__")
    assert accounts.is_valid_username("_ab_")
    assert "username" in accounts.validate_signup("이름", "__abc__", "password123", "password123")


def test_non_ascii_csrf_token_is_400_not_500(client):
    res = client.post("/login", data={"csrf_token": "토큰", "username": "a", "password": "b"})
    assert res.status_code in (302, 400)
    res = client.post("/api/conversations", json={}, headers={"X-CSRF-Token": "é"})
    assert res.status_code == 400


@pytest.mark.parametrize("payload", [{"content": 123}, {"content": ["a"]}, {"content": None}, {}])
def test_non_string_content_is_400(logged_in, payload):
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", **payload},
                         headers=api_headers(logged_in))
    assert res.status_code == 400 and res.get_json()["error"] == "empty"


def test_naive_history_cursor(logged_in):
    assert logged_in.get("/history?before=2026-01-01T00:00:00").status_code == 200


@pytest.mark.parametrize("cid", ["..", ".", "__x__", "a" * 1600, "not-a-uuid"])
def test_invalid_conversation_ids_are_404(logged_in, cid):
    assert logged_in.get(f"/c/{cid}").status_code == 404
    res = logged_in.post(f"/api/conversations/{cid}/messages", json={"content": "x"},
                         headers=api_headers(logged_in))
    assert res.status_code == 404


# ---------------------------------------------------------------- reply races
def test_concurrent_send_in_same_conversation_is_refused():
    repo = repo_with_user(USER)
    gate, entered = threading.Event(), threading.Event()

    class Slow(CountingProvider):
        def reply(self, **kw):
            entered.set()
            gate.wait(2)
            return super().reply(**kw)

    provider = Slow()
    svc = ConversationService(repo, provider, ChatSettings())
    cid = svc.create_conversation(USER, "plato")["id"]
    results = []
    t = threading.Thread(target=lambda: results.append(svc.send(USER, cid, "첫 메시지")))
    t.start()
    entered.wait(2)
    from askphilo.services.conversations import ChatError
    with pytest.raises(ChatError) as e:
        svc.send(USER, cid, "두 번째")
    assert e.value.code == "in_progress"
    with pytest.raises(ChatError) as e2:
        svc.retry(USER, cid)
    assert e2.value.code == "in_progress"
    gate.set()
    t.join()
    assert len(provider.reply_calls) == 1
    roles = [m["role"] for m in repo.get_messages(USER, cid)]
    assert roles == ["user", "assistant"]


def test_retry_refused_while_reply_may_still_be_running(app, logged_in, repo):
    set_provider(app, CountingProvider())
    cid = create(logged_in).get_json()["conversation_id"]
    # Simulate a reload while another process is still answering: last role user, recent, no failure.
    repo.append_messages(uid(logged_in), cid, [{"role": "user", "content": "또 질문"}],
                         conversation_updates={"reply_state": "pending"})
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "답변을 준비하고 있어요" in html and "data-retry" not in html
    res = logged_in.post(f"/api/conversations/{cid}/retry", headers=api_headers(logged_in))
    assert res.status_code == 409 and res.get_json()["error"] == "in_progress"
    # After the grace period it counts as failed and retry works once.
    repo.set_conversation_fields(uid(logged_in), cid, {"updated_at": utcnow() - timedelta(minutes=5)})
    assert "data-retry" in logged_in.get(f"/c/{cid}").get_data(as_text=True)
    ok = logged_in.post(f"/api/conversations/{cid}/retry", headers=api_headers(logged_in))
    assert ok.status_code == 200
    again = logged_in.post(f"/api/conversations/{cid}/retry", headers=api_headers(logged_in))
    assert again.status_code == 409


def test_reply_is_dropped_if_order_changed():
    repo = repo_with_user(USER)

    class Interleaving(CountingProvider):
        def reply(self, **kw):
            # another writer appends while the reply is generated
            repo.append_messages(USER, cid, [{"role": "user", "content": "다른 탭"}])
            return super().reply(**kw)

    svc = ConversationService(repo, Interleaving(), ChatSettings())
    cid = svc.create_conversation(USER, "plato")["id"]
    from askphilo.services.conversations import ChatError
    with pytest.raises(ChatError) as e:
        svc.send(USER, cid, "첫 메시지")
    assert e.value.code == "conflict"
    assert [m["role"] for m in repo.get_messages(USER, cid)] == ["user", "user"]


def test_storage_failure_after_reply_is_retryable_with_ids(app, logged_in, repo):
    set_provider(app, CountingProvider())
    original = repo.append_messages

    def flaky(user_id, conversation_id, messages, **kw):
        if messages[0]["role"] == "assistant":
            raise DataUnavailable("down")
        return original(user_id, conversation_id, messages, **kw)

    repo.append_messages = flaky
    res = create(logged_in)
    data = res.get_json()
    assert res.status_code == 502 and data["error"] == "ai_failed" and data["retryable"]
    assert data["conversation_id"] and data["user_message"]["index"] == 0
    repo.append_messages = original
    retry = logged_in.post(f"/api/conversations/{data['conversation_id']}/retry",
                           headers=api_headers(logged_in))
    assert retry.status_code == 200


# ---------------------------------------------------------------- budgets
def test_daily_message_cap(app, logged_in):
    app.config["CHAT_MESSAGES_PER_DAY"] = 2
    set_provider(app, CountingProvider())
    cid = create(logged_in).get_json()["conversation_id"]
    ok = logged_in.post(f"/api/conversations/{cid}/messages", json={"content": "둘"},
                        headers=api_headers(logged_in))
    assert ok.status_code == 200
    res = logged_in.post(f"/api/conversations/{cid}/messages", json={"content": "셋"},
                         headers=api_headers(logged_in))
    assert res.status_code == 429 and res.get_json()["error"] == "daily_limit"


def test_global_hourly_breaker(app):
    app.config["AI_REPLIES_PER_HOUR"] = 1
    provider = CountingProvider()
    set_provider(app, provider)
    a = app.test_client()
    signup(a, username="first_user")
    assert create(a).status_code == 201
    b = app.test_client()
    signup(b, username="second_user")
    res = create(b)
    assert res.status_code == 503 and res.get_json()["error"] == "busy"
    assert len(provider.reply_calls) == 1


def test_validation_errors_do_not_consume_budget(app, logged_in):
    app.config["CHAT_MESSAGES_PER_DAY"] = 1
    set_provider(app, CountingProvider())
    for _ in range(3):
        assert create(logged_in, content="  ").status_code == 400
    assert create(logged_in).status_code == 201


# ---------------------------------------------------------------- summary / controller
def test_summary_save_failure_defers_instead_of_rebuying():
    repo = repo_with_user(USER)
    provider = CountingProvider()
    settings = ChatSettings()
    svc = ConversationService(repo, provider, settings)
    cid = svc.create_conversation(USER, "plato")["id"]
    for i in range(10):
        svc.send(USER, cid, f"m{i}")

    def broken(*a, **k):
        raise DataUnavailable("down")

    repo.update_summary = broken
    assert svc.maybe_summarize(USER, cid) is False
    assert repo.get_conversation(USER, cid)["summary_retry_after_count"] == 30
    r = svc.send(USER, cid, "다음")
    assert r["summary_due"] is False
    assert len(provider.summary_calls) == 1


def test_production_forces_controller_even_for_old_conversations():
    repo = repo_with_user(USER)
    dev = ConversationService(repo, CountingProvider(), ChatSettings(controller_enabled=False))
    cid = dev.create_conversation(USER, "plato")["id"]
    dev.send(USER, cid, "질문")
    assert "지켜야 할 원칙" not in dev.build_request(USER, cid)["system"]
    prod = ConversationService(repo, CountingProvider(), ChatSettings(force_controller=True))
    assert "지켜야 할 원칙" in prod.build_request(USER, cid)["system"]


def test_production_config_forces_controller():
    app = create_app({"TESTING": True})
    app.config.update(IS_PRODUCTION=True)
    assert ChatSettings.from_config(app.config).force_controller is True


# ---------------------------------------------------------------- settings memory
def test_settings_shows_and_clears_memory(logged_in, repo):
    repo.set_user_memory(uid(logged_in), {"relationship_values": ["관심 표현이 중요"],
                                          "preferences": ["짧은 답변"]})
    html = logged_in.get("/settings").get_data(as_text=True)
    assert "관심 표현이 중요" in html and "상담 기억 지우기" in html
    res = logged_in.post("/settings/memory/clear", data={"csrf_token": csrf_from(logged_in)})
    assert res.status_code == 302
    assert repo.get_user_memory(uid(logged_in)) is None
    assert "아직 기억해 둔 내용이 없어요" in logged_in.get("/settings").get_data(as_text=True)


def test_failed_reply_page_uses_full_error_copy(app, logged_in):
    set_provider(app, FailingProvider())
    cid = create(logged_in).get_json()["conversation_id"]
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요." in html


# ---------------------------------------------------------------- client IP
def test_client_ip_trusts_cloudflare_header_only_on_render():
    from askphilo.auth import client_ip

    app = create_app({"TESTING": True})
    with app.test_request_context("/", headers={"True-Client-IP": "203.0.113.9"},
                                  environ_base={"REMOTE_ADDR": "10.0.0.5"}):
        assert client_ip() == "10.0.0.5"  # off Render: spoofable header ignored
    app.config["ON_RENDER"] = True
    with app.test_request_context("/", headers={"True-Client-IP": "203.0.113.9"},
                                  environ_base={"REMOTE_ADDR": "10.0.0.5"}):
        assert client_ip() == "203.0.113.9"
    with app.test_request_context("/", headers={"True-Client-IP": "not-an-ip"},
                                  environ_base={"REMOTE_ADDR": "10.0.0.5"}):
        assert client_ip() == "10.0.0.5"


def test_render_proxyfix_uses_third_hop():
    from askphilo.config import load_config

    assert load_config({"RENDER": "true"})["ON_RENDER"] is True
    assert load_config({})["ON_RENDER"] is False
