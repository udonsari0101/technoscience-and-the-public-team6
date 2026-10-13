import pytest

from askphilo.ai import set_provider
from askphilo.philosophers import PHILOSOPHERS
from askphilo.services.conversations import make_title

from .conftest import api_headers, signup
from .helpers import CountingProvider, ExplodingProvider, FailingProvider


@pytest.fixture()
def counting(app):
    provider = CountingProvider()
    set_provider(app, provider)
    return provider


def create(client, content="남자친구가 요즘 연락을 너무 안 해서 서운해", philosopher="plato"):
    return client.post("/api/conversations", json={"philosopher_id": philosopher, "content": content},
                       headers=api_headers(client))


def send(client, cid, content):
    return client.post(f"/api/conversations/{cid}/messages", json={"content": content},
                       headers=api_headers(client))


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


def test_selection_page_lists_philosopher_cards(logged_in):
    html = logged_in.get("/new").get_data(as_text=True)
    assert "누구와 이야기해볼까요?" in html
    for p in PHILOSOPHERS:
        assert p["tagline"] in html
        assert p["name"] in html
        assert f"/chat/{p['id']}" in html
        assert f"/philosophers/{p['id']}" in html
    assert "<select" not in html
    assert "플라톤과 대화 시작" in html and "니체와 대화 시작" in html


def test_fixed_taglines():
    tags = {p["id"]: p["tagline"] for p in PHILOSOPHERS}
    assert tags == {
        "plato": "사랑에서 정말 원하는 게 뭔지 함께 살펴봐요.",
        "aristotle": "좋은 관계를 만드는 반복된 행동을 살펴봐요.",
        "epicurus": "내가 정말 원하는 것과 불안 때문에 원하는 걸 구분해봐요.",
        "nietzsche": "남의 기준보다 내가 정말 원하는 것을 솔직하게 바라봐요.",
    }
    assert PHILOSOPHERS[0]["topics"] == ["사랑의 의미", "기대", "욕망", "가치"]


def test_philosopher_detail_page(logged_in):
    html = logged_in.get("/philosophers/aristotle").get_data(as_text=True)
    for section in ("이렇게 고민을 바라봐요", "이런 고민과 잘 맞아요", "이런 질문을 던져요"):
        assert section in html
    assert logged_in.get("/philosophers/socrates").status_code == 404


def test_philosopher_stylesheet(client):
    res = client.get("/assets/philosophers.css")
    assert res.status_code == 200 and res.mimetype == "text/css"
    assert ".ph-plato{--ph:" in res.get_data(as_text=True)


def test_start_page_creates_nothing(logged_in, repo):
    html = logged_in.get("/chat/epicurus").get_data(as_text=True)
    assert "편하게 이야기해 주세요" in html
    assert 'data-conversation-id=""' in html
    assert repo.list_conversations(uid(logged_in)) == []


def test_first_message_creates_conversation_and_reply(logged_in, repo, counting):
    res = create(logged_in)
    assert res.status_code == 201
    data = res.get_json()
    assert data["ok"] and data["user_message"]["index"] == 0
    assert data["assistant_message"]["index"] == 1
    assert data["assistant_message"]["content"]
    assert data["conversation_url"] == f"/c/{data['conversation_id']}"
    conv = repo.get_conversation(uid(logged_in), data["conversation_id"])
    assert conv["philosopher_id"] == "plato" and conv["philosopher_name"] == "플라톤"
    assert conv["message_count"] == 2
    assert conv["title"] == make_title("남자친구가 요즘 연락을 너무 안 해서 서운해")
    assert conv["controller_version"] == "v1" and conv["schema_version"] == 1
    assert conv["summarized_through_message_index"] == -1 and conv["context_summary"] == ""
    msgs = repo.get_messages(uid(logged_in), data["conversation_id"])
    assert [(m["role"], m["message_index"], m["id"]) for m in msgs] == [
        ("user", 0, "000000"), ("assistant", 1, "000001")]
    assert msgs[1]["provider"] == "mock" and msgs[1]["controller_version"] == "v1"
    assert len(counting.reply_calls) == 1
    assert counting.summary_calls == []


def test_title_is_deterministic_and_short():
    assert make_title("짧은 고민") == "짧은 고민"
    long = "남자친구가 요즘 연락을 너무 안 해서 계속 신경이 쓰이고 서운해"
    t = make_title(long)
    assert t.endswith("...") and len(t) <= 27
    assert make_title("  줄바꿈\n\n있는   메시지 ") == "줄바꿈 있는 메시지"


def test_followup_messages_are_ordered(logged_in, repo, counting):
    cid = create(logged_in).get_json()["conversation_id"]
    data = send(logged_in, cid, "3시간째 답장이 없어").get_json()
    assert data["user_message"]["index"] == 2 and data["assistant_message"]["index"] == 3
    conv = repo.get_conversation(uid(logged_in), cid)
    assert conv["title"] == make_title("남자친구가 요즘 연락을 너무 안 해서 서운해")  # unchanged
    assert conv["last_message_role"] == "assistant"
    assert len(counting.reply_calls) == 2


def test_invalid_input_creates_nothing(logged_in, repo, counting):
    assert create(logged_in, philosopher="socrates").status_code == 400
    assert create(logged_in, content="   ").status_code == 400
    assert create(logged_in, content="가" * 2001).status_code == 400
    assert repo.list_conversations(uid(logged_in)) == []
    assert counting.reply_calls == []


def test_api_requires_csrf(logged_in, repo):
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "hi"})
    assert res.status_code == 400 and res.get_json()["error"] == "csrf"


def test_api_requires_login(client):
    res = client.post("/api/conversations", json={"philosopher_id": "plato", "content": "hi"},
                      headers=api_headers(client))
    assert res.status_code == 401


def test_conversations_are_private(app, counting):
    a = app.test_client()
    signup(a, username="alice_1")
    cid = create(a).get_json()["conversation_id"]
    b = app.test_client()
    signup(b, username="bobby_2")
    assert b.get(f"/c/{cid}").status_code == 404
    assert send(b, cid, "hi").status_code == 404
    assert b.get(f"/api/conversations/{cid}/messages").status_code == 404
    assert b.post(f"/c/{cid}/delete", data={"csrf_token": api_headers(b)["X-CSRF-Token"]}).status_code == 404


def test_reopen_conversation_page(logged_in, counting):
    cid = create(logged_in).get_json()["conversation_id"]
    send(logged_in, cid, "주말에도 연락이 거의 없었어")
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "주말에도 연락이 거의 없었어" in html
    assert f'data-conversation-id="{cid}"' in html
    assert "대화 삭제" in html
    assert len(counting.reply_calls) == 2  # reopening made no AI call


def test_older_messages_pagination(logged_in, repo, counting):
    cid = create(logged_in).get_json()["conversation_id"]
    for i in range(20):
        send(logged_in, cid, f"메시지 {i}")
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "이전 대화 보기" in html
    res = logged_in.get(f"/api/conversations/{cid}/messages?before=12").get_json()
    assert [m["index"] for m in res["messages"]] == list(range(0, 12))
    assert res["has_more"] is False


def test_history_and_home_list_conversations(logged_in, counting):
    cid = create(logged_in).get_json()["conversation_id"]
    for path in ("/history", "/home"):
        html = logged_in.get(path).get_data(as_text=True)
        assert f"/c/{cid}" in html
        assert "플라톤" in html


def test_delete_conversation_and_messages(logged_in, repo, counting):
    cid = create(logged_in).get_json()["conversation_id"]
    page = logged_in.get(f"/c/{cid}/delete").get_data(as_text=True)
    assert "이 대화를 삭제할까요?" in page and "삭제한 대화는 다시 볼 수 없어요." in page
    res = logged_in.post(f"/c/{cid}/delete", data={"csrf_token": api_headers(logged_in)["X-CSRF-Token"]})
    assert res.status_code == 302 and res.headers["Location"].endswith("/history")
    assert repo.get_conversation(uid(logged_in), cid) is None
    assert repo.get_messages(uid(logged_in), cid) == []
    assert logged_in.get(f"/c/{cid}").status_code == 404


def test_ai_failure_keeps_user_message_and_allows_explicit_retry(app, logged_in, repo):
    failing = FailingProvider()
    set_provider(app, failing)
    res = create(logged_in, content="고백을 할까 고민돼")
    assert res.status_code == 502
    data = res.get_json()
    assert data["error"] == "ai_failed" and data["retryable"] is True
    assert data["message"] == "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
    cid = data["conversation_id"]
    assert failing.reply_calls == 1  # no automatic retry
    conv = repo.get_conversation(uid(logged_in), cid)
    assert conv["message_count"] == 1 and conv["last_message_role"] == "user"
    assert conv["usage"]["failed_calls"] == 1
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "data-retry" in html  # reopened page offers the retry button

    counting = CountingProvider()
    set_provider(app, counting)
    retry = logged_in.post(f"/api/conversations/{cid}/retry", headers=api_headers(logged_in))
    assert retry.status_code == 200
    assert retry.get_json()["assistant_message"]["index"] == 1
    assert len(counting.reply_calls) == 1
    again = logged_in.post(f"/api/conversations/{cid}/retry", headers=api_headers(logged_in))
    assert again.status_code == 409


def test_hard_limit_blocks_new_messages_without_ai_call(app, logged_in, repo):
    set_provider(app, CountingProvider())
    cid = create(logged_in).get_json()["conversation_id"]
    repo.set_conversation_fields(uid(logged_in), cid, {"message_count": 299})
    set_provider(app, ExplodingProvider())
    res = send(logged_in, cid, "계속 이야기하고 싶어")
    assert res.status_code == 409
    assert res.get_json()["message"] == "이 대화가 너무 길어져 새 대화에서 이어가는 게 좋아요."
    html = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "새 대화에서 이어가는 게 좋아요" in html  # still readable, composer locked
    assert "data-composer" not in html


def test_soft_limit_notice(app, logged_in, repo, counting):
    cid = create(logged_in).get_json()["conversation_id"]
    repo.set_conversation_fields(uid(logged_in), cid, {"message_count": 140,
                                                       "summarized_through_message_index": 129})
    data = send(logged_in, cid, "또 고민이 생겼어").get_json()
    assert data["status"]["soft_limit_reached"] is True
    assert "새 상담을 시작하면" in data["status"]["notice"]


def test_chat_rate_limit(app, logged_in, counting):
    app.config["CHAT_MESSAGES_PER_MINUTE"] = 3
    cid = create(logged_in).get_json()["conversation_id"]
    assert send(logged_in, cid, "둘").status_code == 200
    assert send(logged_in, cid, "셋").status_code == 200
    res = send(logged_in, cid, "넷")
    assert res.status_code == 429
    assert len(counting.reply_calls) == 3
