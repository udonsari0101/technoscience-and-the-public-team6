"""Conversation titles: generated inside the first reply call, editable by the user."""

from types import SimpleNamespace

from askphilo.ai import set_provider
from askphilo.ai.anthropic_provider import AnthropicProvider
from askphilo.ai.base import AIReply, AIUsage
from askphilo.ai.prompts import TITLE_INSTRUCTION, clean_title, split_title
from askphilo.services.conversations import ChatError, ChatSettings, ConversationService

from .conftest import api_headers, csrf_from
from .helpers import CountingProvider, repo_with_user

USER = "user-1"


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


def test_title_comes_with_first_reply_only():
    repo = repo_with_user(USER)
    provider = CountingProvider()
    svc = ConversationService(repo, provider, ChatSettings())
    cid = svc.create_conversation(USER, "aristotle")["id"]
    first = svc.send(USER, cid, "애인이랑 같은 문제로 계속 싸워")
    assert first["title"] == "반복되는 다툼"
    assert provider.reply_calls[0]["want_title"] is True
    second = svc.send(USER, cid, "어제도 그랬어")
    assert second["title"] is None and provider.reply_calls[1]["want_title"] is False
    assert len(provider.reply_calls) == 2  # no separate title request
    assert repo.get_conversation(USER, cid)["title_source"] == "ai"


def test_placeholder_title_when_model_gives_none():
    class NoTitle(CountingProvider):
        def reply(self, **kw):
            r = super().reply(**kw)
            r.title = None
            return r

    repo = repo_with_user(USER)
    svc = ConversationService(repo, NoTitle(), ChatSettings())
    cid = svc.create_conversation(USER, "plato")["id"]
    svc.send(USER, cid, "아무 주제 없는 긴 첫 메시지인데 제목으로 잘려야 해요 정말로")
    conv = repo.get_conversation(USER, cid)
    assert conv["title_source"] == "auto" and conv["title"].endswith("...")


def test_user_title_is_never_overwritten():
    repo = repo_with_user(USER)
    svc = ConversationService(repo, CountingProvider(), ChatSettings())
    cid = svc.create_conversation(USER, "plato")["id"]

    class RenameDuringReply(CountingProvider):
        def reply(self, **kw):
            svc_user.rename(USER, cid, "내가 정한 이름")  # user renames while the AI answers
            return super().reply(**kw)

    svc_user = ConversationService(repo, CountingProvider(), ChatSettings())
    racing = ConversationService(repo, RenameDuringReply(), ChatSettings())
    racing.send(USER, cid, "연락이 없어")
    conv = repo.get_conversation(USER, cid)
    assert conv["title"] == "내가 정한 이름" and conv["title_source"] == "user"
    # summaries don't replace it either
    repo.update_summary(USER, cid, expected_through_index=-1, summary="- s", through_index=0,
                        title="요약 제목")
    assert repo.get_conversation(USER, cid)["title"] == "내가 정한 이름"


def test_summary_refreshes_automatic_title():
    repo = repo_with_user(USER)
    svc = ConversationService(repo, CountingProvider(), ChatSettings())
    cid = svc.create_conversation(USER, "plato")["id"]
    svc.send(USER, cid, "연락이 없어")
    repo.update_summary(USER, cid, expected_through_index=-1, summary="- s", through_index=0,
                        title="허전함의 정체")
    conv = repo.get_conversation(USER, cid)
    assert conv["title"] == "허전함의 정체" and conv["title_source"] == "ai"


def test_rename_validation():
    repo = repo_with_user(USER)
    svc = ConversationService(repo, CountingProvider(), ChatSettings())
    cid = svc.create_conversation(USER, "plato")["id"]
    for bad, code in (("   ", "title_empty"), (None, "title_empty"), ("가" * 31, "title_long")):
        try:
            svc.rename(USER, cid, bad)
            raise AssertionError("expected error")
        except ChatError as exc:
            assert exc.code == code
    assert svc.rename(USER, cid, "  여러   칸  ") == "여러 칸"


def test_rename_routes(app, logged_in, repo):
    set_provider(app, CountingProvider())
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "연락 문제"},
                         headers=api_headers(logged_in))
    cid = res.get_json()["conversation_id"]
    page = logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert "이름 바꾸기" in page and 'data-chat-title>연락 문제로 서운함<' in page
    assert logged_in.get(f"/c/{cid}/rename").status_code == 200
    ok = logged_in.post(f"/c/{cid}/rename", data={"csrf_token": csrf_from(logged_in), "title": "우리 이야기"})
    assert ok.status_code == 302 and ok.headers["Location"].endswith(f"/c/{cid}")
    conv = repo.get_conversation(uid(logged_in), cid)
    assert conv["title"] == "우리 이야기" and conv["title_source"] == "user"
    assert "우리 이야기" in logged_in.get("/history").get_data(as_text=True)
    bad = logged_in.post(f"/c/{cid}/rename", data={"csrf_token": csrf_from(logged_in), "title": " "})
    assert bad.status_code == 302 and bad.headers["Location"].endswith("/rename")
    assert logged_in.post(f"/c/{cid}/rename", data={"title": "x"}).status_code == 400  # CSRF


def test_new_chat_page_has_hidden_conversation_actions(logged_in):
    html = logged_in.get("/chat/plato").get_data(as_text=True)
    assert 'data-conversation-link="/c/{id}/rename"' in html and "hidden>이름 바꾸기" in html


def test_split_and_clean_title():
    body, title = split_title("좋은 질문이야. 너는 어떻게 생각해?\n<title>연락 문제로 서운함</title>")
    assert body == "좋은 질문이야. 너는 어떻게 생각해?" and title == "연락 문제로 서운함"
    assert split_title("답변만 있어.") == ("답변만 있어.", None)
    assert split_title("잘린 답변이야. <title>연락 문") == ("잘린 답변이야.", None)
    assert clean_title('"연락 문제"') == "연락 문제"
    assert len(clean_title("가" * 40)) == 20


def test_anthropic_title_request_uses_same_call():
    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            text = "그랬구나. 무엇이 제일 서운했어?\n<title>약속이 자꾸 미뤄짐</title>"
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                                                         cache_read_input_tokens=0,
                                                         cache_creation_input_tokens=0),
                                   stop_reason="end_turn", model="claude-sonnet-5-5", stop_details=None)

    p = AnthropicProvider(api_key="k", model="claude-sonnet-5-5")
    p._client = SimpleNamespace(messages=Msgs(), beta=SimpleNamespace(messages=Msgs()))
    reply = p.reply(system="S", messages=[{"role": "user", "content": "약속을 자꾸 미뤄"}],
                    philosopher={"id": "plato", "name": "플라톤"}, max_tokens=300, want_title=True)
    assert len(calls) == 1
    assert calls[0]["messages"][-1] == {"role": "system", "content": TITLE_INSTRUCTION}
    assert reply.title == "약속이 자꾸 미뤄짐" and "<title>" not in reply.text
    p.reply(system="S", messages=[{"role": "user", "content": "x"}],
            philosopher={"id": "plato", "name": "플라톤"}, max_tokens=300)
    assert all(m["role"] != "system" for m in calls[1]["messages"])
