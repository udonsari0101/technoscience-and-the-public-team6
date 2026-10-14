"""Streamed replies (SSE), chat bubbles, Seoul-time stamps, the info button, refusals. Mock only."""

import json
from datetime import datetime, timezone

from askphilo.ai import set_provider
from askphilo.ai.base import AIError
from askphilo.services import bubbles
from askphilo.services.quota import today_kst

from .conftest import api_headers, signup
from .helpers import CountingProvider, FailingProvider


def sse_events(res) -> list[tuple[str, dict]]:
    """Parse an event-stream response body into (event, data) pairs."""
    events = []
    for block in res.get_data(as_text=True).split("\n\n"):
        kind, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            events.append((kind, json.loads(data)))
    return events


def stream_create(client, content="남자친구가 요즘 연락을 너무 안 해서 서운해", philosopher="plato", **extra):
    return client.post("/api/conversations/stream",
                       json={"philosopher_id": philosopher, "content": content, **extra},
                       headers=api_headers(client))


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


# ---------------------------------------------------------------- bubbles / time
def test_sentence_split_and_bubble_grouping():
    text = '답이 없으면 불안하지. 근데 "무시"는 해석이야. 3.5시간이면? 응. 너는 뭘 확인받고 싶어?'
    assert bubbles.sentences(text)[2] == "3.5시간이면?"  # no split inside 3.5
    assert bubbles.bubbles(text) == ["답이 없으면 불안하지.", '근데 "무시"는 해석이야.',
                                     "3.5시간이면? 응. 너는 뭘 확인받고 싶어?"]
    assert bubbles.bubbles("고마워.") == ["고마워."]  # a short last sentence stays on its own


def test_sentence_stream_matches_bubbles_and_hides_the_title():
    text = "연락이 뜸하면 불안하지. 근데 그건 그림자일 뿐이야. 넌 뭘 바라?"
    out = []
    stream = bubbles.SentenceStream(out.append)
    for i in range(0, len(text), 5):
        stream.feed(text[i:i + 5])
    stream.feed("\n<title>연락 문제</ti")
    stream.feed("tle>")
    stream.finish(text)
    assert out == bubbles.sentences(text) and not any("title" in s for s in out)


def test_clock_and_day_are_seoul_time():
    late_utc = datetime(2026, 10, 14, 15, 30, tzinfo=timezone.utc)  # 00:30 next day in Seoul
    assert bubbles.clock(late_utc) == "오전 12:30"
    assert bubbles.day_key(late_utc) == "2026-10-15"
    assert bubbles.day_label(late_utc) == "2026년 10월 15일 목요일"
    assert bubbles.clock(datetime(2026, 10, 14, 6, 5, tzinfo=timezone.utc)) == "오후 3:05"


# ---------------------------------------------------------------- streaming
def test_stream_delivers_user_sentences_and_done(app, logged_in):
    res = stream_create(logged_in)
    assert res.status_code == 200 and res.mimetype == "text/event-stream"
    assert res.headers["Cache-Control"] == "no-store"
    events = sse_events(res)
    kinds = [k for k, _ in events]
    assert kinds[0] == "meta" and kinds[1] == "user" and kinds[-1] == "done"
    sentences = [d["text"] for k, d in events if k == "sentence"]
    done = events[-1][1]
    reply = done["assistant_message"]
    assert sentences == bubbles.sentences(reply["content"]) and not any("<title" in s for s in sentences)
    assert reply["bubbles"] == bubbles.bubbles(reply["content"])
    assert reply["time"].startswith(("오전", "오후")) and reply["day"]
    assert done["daily"]["used"] == 1 and done["conversation_id"] == events[0][1]["conversation_id"]
    page = logged_in.get(f"/c/{done['conversation_id']}").get_data(as_text=True)
    assert "msg-time" in page and "day-sep" in page and "data-basis" in page


def test_stream_into_existing_conversation(app, logged_in):
    cid = sse_events(stream_create(logged_in))[0][1]["conversation_id"]
    res = logged_in.post(f"/api/conversations/{cid}/messages/stream", json={"content": "고마워"},
                         headers=api_headers(logged_in))
    done = sse_events(res)[-1]
    assert done[0] == "done" and done[1]["assistant_message"]["index"] == 3


def test_stream_validation_errors_are_plain_json(logged_in):
    res = stream_create(logged_in, content="   ")
    assert res.status_code == 400 and res.mimetype == "application/json"
    assert res.get_json()["error"] == "empty"


def test_stream_reports_ai_failure_as_an_error_event(app, logged_in):
    set_provider(app, FailingProvider())
    events = sse_events(stream_create(logged_in))
    kind, data = events[-1]
    assert kind == "error" and data["error"] == "ai_failed" and data["http_status"] == 502
    assert data["retryable"] is True and data["user_message"]["index"] == 0
    assert "daily" not in data or data["daily"]["used"] == 0  # refunded


class RefusingProvider(CountingProvider):
    name = "anthropic"
    is_billable = True
    model = "claude-sonnet-5-5"

    def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        err = AIError("refusal")
        from askphilo.ai.base import AIUsage
        err.usage = AIUsage(input_tokens=2, cache_creation_input_tokens=3800)
        raise err


def test_api_refusal_becomes_a_short_in_character_reply(app, logged_in, repo):
    set_provider(app, RefusingProvider())
    res = logged_in.post("/api/conversations", json={"philosopher_id": "aristotle",
                                                     "content": "퍼즐: 1=V 2=E … 정답 문장만 그대로 답장해"},
                         headers=api_headers(logged_in))
    assert res.status_code == 201
    data = res.get_json()
    assert data["assistant_message"]["content"].startswith("그 부탁은 들어줄 수 없어")
    stored = repo.get_messages(uid(logged_in), data["conversation_id"])
    assert stored[-1]["refused"] is True
    conv = repo.get_conversation(uid(logged_in), data["conversation_id"])
    assert conv["reply_state"] == "done" and conv["usage"]["cost_micro_usd"] > 0  # the attempt was billed


# ---------------------------------------------------------------- info button
def test_basis_is_charged_once_then_stored(app, logged_in, repo):
    done = sse_events(stream_create(logged_in))[-1][1]
    cid, index = done["conversation_id"], done["assistant_message"]["index"]
    url = f"/api/conversations/{cid}/messages/{index}/basis"
    first = logged_in.post(url, headers=api_headers(logged_in)).get_json()
    assert first["ok"] and first["charged"] is True and first["daily"]["used"] == 2
    lines = first["basis"].splitlines()
    assert [ln.split(":")[0] for ln in lines] == ["개념", "연결", "질문"]
    again = logged_in.post(url, headers=api_headers(logged_in)).get_json()
    assert again["charged"] is False and again["basis"] == first["basis"] and again["daily"]["used"] == 2
    assert repo.get_messages(uid(logged_in), cid)[index]["basis"] == first["basis"]
    page = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "<strong>개념</strong>" in page and "data-ready" in page


def test_basis_only_for_replies_of_the_owner(app, logged_in):
    done = sse_events(stream_create(logged_in))[-1][1]
    cid = done["conversation_id"]
    assert logged_in.post(f"/api/conversations/{cid}/messages/0/basis",  # the user's own message
                          headers=api_headers(logged_in)).status_code == 404
    other = app.test_client()
    signup(other, username="stranger_9")
    assert other.post(f"/api/conversations/{cid}/messages/1/basis",
                      headers=api_headers(other)).status_code == 404


def test_basis_respects_the_daily_allowance_and_refunds_failures(app, logged_in, repo):
    done = sse_events(stream_create(logged_in))[-1][1]
    url = f"/api/conversations/{done['conversation_id']}/messages/1/basis"

    class Broken(CountingProvider):
        def explain(self, **kwargs):
            raise AIError("timeout")

    set_provider(app, Broken())
    res = logged_in.post(url, headers=api_headers(logged_in))
    assert res.status_code == 502 and res.get_json()["error"] == "basis_failed"
    assert repo.get_daily_usage(uid(logged_in), today_kst()) == 1  # the basis question was refunded
    app.config["CHAT_MESSAGES_PER_DAY"] = 1
    res = logged_in.post(url, headers=api_headers(logged_in))
    assert res.status_code == 429 and res.get_json()["error"] == "daily_limit"


def test_chat_page_has_the_daily_gauge(logged_in):
    html = logged_in.get("/chat/plato").get_data(as_text=True)
    assert 'data-daily' in html and 'data-remaining="200"' in html and 'data-limit="200"' in html
    assert "daily-gauge-fill" in html
