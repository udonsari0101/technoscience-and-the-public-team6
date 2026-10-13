"""Conversation memory, context compression and user-level memory (Scenarios E–G)."""

import threading

import pytest

from askphilo.ai import set_provider
from askphilo.data.memory_repo import MemoryRepository
from askphilo.services.conversations import ChatSettings, ConversationService

from .helpers import BillableCountingProvider, CountingProvider, FailingProvider

USER = "user-1"


@pytest.fixture()
def settings():
    return ChatSettings(max_context_messages=10, summary_trigger=20)


@pytest.fixture()
def repo():
    return MemoryRepository()


def make(repo, provider, settings):
    return ConversationService(repo, provider, settings)


def chat(svc, cid, n, start=0, text="연락 문제로 고민이야 {i}"):
    results = []
    for i in range(start, start + n):
        r = svc.send(USER, cid, text.format(i=i))
        if r["summary_due"]:
            svc.maybe_summarize(USER, cid)
        results.append(r)
    return results


def test_summary_runs_only_at_threshold(repo, settings):
    provider = CountingProvider()
    svc = make(repo, provider, settings)
    cid = svc.create_conversation(USER, "plato")["id"]

    chat(svc, cid, 9)  # 18 messages
    assert provider.summary_calls == []
    chat(svc, cid, 1, start=9)  # 20 messages → first compression
    assert len(provider.summary_calls) == 1
    conv = repo.get_conversation(USER, cid)
    assert conv["summarized_through_message_index"] == 9  # 0..9 compressed, 10 recent kept
    assert conv["summary_version"] == 1 and conv["context_summary"]
    assert conv["context_summary_updated_at"] is not None

    chat(svc, cid, 4, start=10)  # 28 messages → still below the next step
    assert len(provider.summary_calls) == 1
    chat(svc, cid, 1, start=14)  # 30 messages → second compression
    assert len(provider.summary_calls) == 2
    conv = repo.get_conversation(USER, cid)
    assert conv["summarized_through_message_index"] == 19 and conv["summary_version"] == 2

    # exactly one reply request per user message
    assert len(provider.reply_calls) == 15


def test_summary_input_is_incremental(repo, settings):
    provider = CountingProvider()
    svc = make(repo, provider, settings)
    cid = svc.create_conversation(USER, "aristotle")["id"]
    chat(svc, cid, 15)
    first, second = provider.summary_calls
    assert len(first["transcript"]) == 10 and not first["old_summary"]
    assert [m["message_index"] for m in second["transcript"]] == list(range(10, 20))
    assert second["old_summary"]  # previous summary + only the new slice


def test_context_is_summary_plus_recent_messages(repo, settings):
    """Scenario E: old turns are compressed, only recent raw turns are sent."""
    provider = CountingProvider()
    svc = make(repo, provider, settings)
    cid = svc.create_conversation(USER, "plato")["id"]
    chat(svc, cid, 15)  # 30 messages, summarized through 19
    svc.send(USER, cid, "오늘 또 답장이 늦었어")
    last_call = provider.reply_calls[-1]
    msgs = last_call["messages"]
    assert msgs[0]["role"] == "user" and "<summary>" in msgs[0]["content"]
    raw = msgs[1:]
    assert len(raw) == 11  # 10 kept + the new user message
    assert raw[-1]["content"] == "오늘 또 답장이 늦었어"
    assert all("연락 문제로 고민이야 0" != m["content"] for m in raw)
    # user content never leaks into the system prompt
    assert "오늘 또 답장이 늦었어" not in last_call["system"]
    summary = repo.get_conversation(USER, cid)["context_summary"]
    assert summary and summary not in last_call["system"]


def test_raw_context_is_bounded_by_trigger(repo, settings):
    provider = CountingProvider()
    svc = make(repo, provider, settings)
    cid = svc.create_conversation(USER, "plato")["id"]
    for i in range(40):  # never run summaries → worst case
        svc.send(USER, cid, f"m{i}")
    raw = [m for m in provider.reply_calls[-1]["messages"]
           if not m["content"].startswith("[상담 참고") and m["content"] != "(이전 대화에 이어서)"]
    assert len(raw) <= settings.summary_trigger


def test_reopen_uses_summary_and_recent_messages(repo, settings):
    """Scenario F: a fresh service (days later) continues from stored state."""
    svc = make(repo, CountingProvider(), settings)
    cid = svc.create_conversation(USER, "epicurus")["id"]
    chat(svc, cid, 10, text="애인이 바빠서 서운해 {i}")
    later = CountingProvider()
    svc2 = make(repo, later, settings)
    request = svc2.build_request(USER, cid)
    assert "<summary>" in request["messages"][0]["content"]
    assert "애인이 바빠서 서운해" in request["messages"][0]["content"]
    assert request["messages"][-1]["role"] == "assistant"
    assert later.reply_calls == []  # building context made no AI call


def test_new_conversation_does_not_import_other_conversations(repo, settings):
    """Scenario G: only minimal user-level memory crosses conversations."""
    svc = make(repo, CountingProvider(), settings)
    a = svc.create_conversation(USER, "plato")["id"]
    chat(svc, a, 10, text="비밀 이야기 {i} 답변은 짧게 해줘")
    memory = repo.get_user_memory(USER)
    assert memory["preferences"] == ["답변을 짧게 받는 것을 선호함"]
    b = svc.create_conversation(USER, "nietzsche")["id"]
    svc.send(USER, b, "새로운 고민이 있어")
    request = svc.build_request(USER, b)
    context = request["messages"][0]["content"]
    assert "<memory>" in context and "짧게" in context
    assert "<summary>" not in context
    assert all("비밀 이야기" not in m["content"] for m in request["messages"])


def test_summary_failure_does_not_break_chat_or_retry_every_turn(repo, settings):
    good = CountingProvider()
    svc = make(repo, good, settings)
    cid = svc.create_conversation(USER, "plato")["id"]
    chat(svc, cid, 9)
    failing = FailingProvider()
    failing.reply = good.reply  # replies work, summaries fail
    svc_fail = make(repo, failing, settings)
    r = svc_fail.send(USER, cid, "열 번째")
    assert r["summary_due"]
    assert svc_fail.maybe_summarize(USER, cid) is False  # swallowed, chat unaffected
    conv = repo.get_conversation(USER, cid)
    assert conv["summarized_through_message_index"] == -1
    assert conv["summary_retry_after_count"] == 30
    # next turns do not hammer the summary API
    for i in range(4):
        r = svc_fail.send(USER, cid, f"계속 {i}")
        assert r["summary_due"] is False
    assert failing.summary_calls == 1
    r = svc_fail.send(USER, cid, "다음 단계")
    assert r["summary_due"] is True  # 30 messages: next threshold step


def test_concurrent_summaries_call_api_once(repo, settings):
    class SlowProvider(CountingProvider):
        def summarize(self, **kwargs):
            gate.wait(2)
            return super().summarize(**kwargs)

    gate = threading.Event()
    provider = SlowProvider()
    svc = make(repo, provider, settings)
    cid = svc.create_conversation(USER, "plato")["id"]
    for i in range(10):
        svc.send(USER, cid, f"m{i}")
    threads = [threading.Thread(target=svc.maybe_summarize, args=(USER, cid)) for _ in range(3)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert len(provider.summary_calls) == 1
    assert repo.get_conversation(USER, cid)["summary_version"] == 1


def test_usage_counters_for_billable_provider(repo, settings):
    provider = BillableCountingProvider()
    svc = make(repo, provider, settings)
    cid = svc.create_conversation(USER, "plato")["id"]
    chat(svc, cid, 10)
    usage = repo.get_conversation(USER, cid)["usage"]
    assert usage["reply_calls"] == 10 and usage["summary_calls"] == 1
    assert usage["api_calls"] == 11
    assert usage["input_tokens"] == 10_000 and usage["output_tokens"] == 800
    assert usage["summary_input_tokens"] == 1500 and usage["cache_read_input_tokens"] == 6000
    msgs = repo.get_messages(USER, cid)
    assistant = [m for m in msgs if m["role"] == "assistant"]
    assert all(m["input_tokens"] == 1000 and m["output_tokens"] == 80 for m in assistant)
    assert all(m["model"] == "test-model" for m in assistant)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert all(m["provider"] is None and m["input_tokens"] is None for m in user_msgs)


def test_summary_runs_after_response_in_app(app, logged_in):
    from .conftest import api_headers

    provider = CountingProvider()
    set_provider(app, provider)
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "시작"},
                         headers=api_headers(logged_in))
    cid = res.get_json()["conversation_id"]
    for i in range(9):
        logged_in.post(f"/api/conversations/{cid}/messages", json={"content": f"m{i}"},
                       headers=api_headers(logged_in))
    assert len(provider.summary_calls) == 1
    assert len(provider.reply_calls) == 10
