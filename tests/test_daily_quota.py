"""Daily question allowance: persistent, KST-day based, refunded on failure, visible in UI."""

from datetime import datetime, timezone

from askphilo.ai import set_provider
from askphilo.data.memory_repo import MemoryRepository
from askphilo.services.conversations import ChatError, ChatSettings, ConversationService
from askphilo.services.quota import daily_status, today_kst

from .conftest import api_headers
from .helpers import CountingProvider, FailingProvider

USER = "quota-user"


def make_repo():
    repo = MemoryRepository()
    repo.create_user(user_id=USER, name="q", username="quota", username_normalized="quota",
                     password_hash="x")
    return repo


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


def create(client, content="고민이 있어"):
    return client.post("/api/conversations", json={"philosopher_id": "plato", "content": content},
                       headers=api_headers(client))


def test_today_is_the_korean_calendar_day():
    # 2026-10-14 15:30 UTC is already 2026-10-15 00:30 in Korea
    assert today_kst(datetime(2026, 10, 14, 15, 30, tzinfo=timezone.utc)) == "2026-10-15"
    assert today_kst(datetime(2026, 10, 14, 14, 59, tzinfo=timezone.utc)) == "2026-10-14"


def test_daily_status_flags():
    assert daily_status(0, 50) == {"used": 0, "limit": 50, "remaining": 50, "low": False, "exhausted": False}
    assert daily_status(46, 50)["low"] is True
    assert daily_status(50, 50)["exhausted"] is True and daily_status(60, 50)["remaining"] == 0


def test_reserve_refund_and_day_rollover():
    repo = make_repo()
    assert repo.reserve_daily_message(USER, "2026-10-14", 2) == 1
    assert repo.reserve_daily_message(USER, "2026-10-14", 2) == 2
    assert repo.reserve_daily_message(USER, "2026-10-14", 2) is None
    repo.refund_daily_message(USER, "2026-10-14")
    assert repo.get_daily_usage(USER, "2026-10-14") == 1
    assert repo.get_daily_usage(USER, "2026-10-15") == 0  # a new day starts at zero
    assert repo.reserve_daily_message(USER, "2026-10-15", 2) == 1
    repo.refund_daily_message(USER, "2026-10-14")  # stale day: no effect
    assert repo.get_daily_usage(USER, "2026-10-15") == 1


def test_successful_messages_count_failures_are_refunded():
    repo = make_repo()
    svc = ConversationService(repo, CountingProvider(), ChatSettings(daily_limit=3))
    cid = svc.create_conversation(USER, "plato")["id"]
    r = svc.send(USER, cid, "첫 질문")
    assert r["daily"] == {"used": 1, "limit": 3, "remaining": 2, "low": True, "exhausted": False}
    failing = ConversationService(repo, FailingProvider(), ChatSettings(daily_limit=3))
    try:
        failing.send(USER, cid, "실패할 질문")
    except ChatError as exc:
        assert exc.code == "ai_failed"
    assert repo.get_daily_usage(USER, today_kst()) == 1  # refunded
    r = svc.retry(USER, cid)  # explicit retry uses one question when it succeeds
    assert r["daily"]["used"] == 2


def test_limit_reached_blocks_before_storing_or_calling_ai():
    repo = make_repo()
    provider = CountingProvider()
    svc = ConversationService(repo, provider, ChatSettings(daily_limit=1))
    cid = svc.create_conversation(USER, "plato")["id"]
    svc.send(USER, cid, "하나")
    try:
        svc.send(USER, cid, "둘")
        raise AssertionError("expected daily_limit")
    except ChatError as exc:
        assert exc.code == "daily_limit" and exc.status == 429
        assert exc.extra["daily"]["exhausted"] is True
    assert len(provider.reply_calls) == 1
    assert repo.get_conversation(USER, cid)["message_count"] == 2


def test_api_returns_daily_and_pages_show_it(app, logged_in):
    app.config["CHAT_MESSAGES_PER_DAY"] = 5
    set_provider(app, CountingProvider())
    data = create(logged_in).get_json()
    assert data["daily"] == {"used": 1, "limit": 5, "remaining": 4, "low": False, "exhausted": False}
    home = logged_in.get("/home").get_data(as_text=True)
    assert "오늘 남은 질문" in home and 'value="4" max="5"' in home
    chat = logged_in.get(f"/c/{data['conversation_id']}").get_data(as_text=True)
    assert 'data-daily-remaining>4<' in chat
    settings = logged_in.get("/settings").get_data(as_text=True)
    assert "오늘 사용한 질문" in settings and "1 / 5" in settings


def test_exhausted_allowance_locks_composer(app, logged_in):
    app.config["CHAT_MESSAGES_PER_DAY"] = 1
    set_provider(app, CountingProvider())
    cid = create(logged_in).get_json()["conversation_id"]
    res = logged_in.post(f"/api/conversations/{cid}/messages", json={"content": "또"},
                         headers=api_headers(logged_in))
    body = res.get_json()
    assert res.status_code == 429 and body["error"] == "daily_limit"
    assert body["message"] == "오늘 질문을 모두 사용했어요. 내일 0시에 다시 채워져요."
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "오늘 질문을 모두 사용했어요." in html and "data-composer" not in html
    assert "내일 0시에 다시 채워져요" in logged_in.get("/home").get_data(as_text=True)


def test_allowance_survives_app_restart(app, logged_in, repo):
    """Persistent (repository) count, not process memory."""
    app.config["CHAT_MESSAGES_PER_DAY"] = 10
    set_provider(app, CountingProvider())
    create(logged_in)
    app.extensions.pop("askphilo.limiter", None)  # simulate a restart of in-process state
    assert repo.get_daily_usage(uid(logged_in), today_kst()) == 1
