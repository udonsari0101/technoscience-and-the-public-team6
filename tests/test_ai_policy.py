"""API cost policy: when AI may be called, and how the Anthropic request is shaped."""

from types import SimpleNamespace

import pytest

from askphilo import create_app
from askphilo.ai import get_provider, set_provider
from askphilo.ai.anthropic_provider import AnthropicProvider
from askphilo.ai.base import AIError
from askphilo.ai.mock import MockProvider

from .conftest import api_headers, csrf_from, login, signup
from .helpers import CountingProvider, ExplodingProvider


def test_testing_and_mock_never_build_anthropic_provider():
    app = create_app({"TESTING": True, "AI_MODE": "anthropic", "ANTHROPIC_API_KEY": "sk-x"})
    assert isinstance(get_provider(app), MockProvider)


def test_no_ai_calls_outside_chat_messages(app):
    set_provider(app, ExplodingProvider())
    c = app.test_client()
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code == 200
    assert signup(c).status_code == 302
    c.post("/logout", data={"csrf_token": csrf_from(c)})
    assert login(c).status_code == 302
    for path in ("/home", "/new", "/philosophers/plato", "/chat/plato", "/history", "/settings"):
        assert c.get(path).status_code == 200, path
    # conversation creation alone makes no AI call
    with app.app_context():
        from askphilo.views.chat import service

        with c.session_transaction() as s:
            uid = s["uid"]
        conv = service().create_conversation(uid, "plato")
    assert c.get(f"/c/{conv['id']}").status_code == 200
    assert c.get(f"/api/conversations/{conv['id']}/messages").status_code == 200
    assert c.get(f"/c/{conv['id']}/delete").status_code == 200
    assert c.post(f"/c/{conv['id']}/delete", data={"csrf_token": csrf_from(c)}).status_code == 302


def test_one_message_one_request(app, logged_in):
    provider = CountingProvider()
    set_provider(app, provider)
    res = logged_in.post("/api/conversations", json={"philosopher_id": "nietzsche", "content": "고민이 있어"},
                         headers=api_headers(logged_in))
    assert res.status_code == 201
    assert len(provider.reply_calls) == 1 and provider.summary_calls == []


# ---------------------------------------------------------------------------
# Anthropic request shape (fake client — no network)
# ---------------------------------------------------------------------------

class FakeMessages:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_response(text="좋은 질문이야. 너는 어떻게 생각해?", stop="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    usage = SimpleNamespace(input_tokens=900, output_tokens=120, cache_read_input_tokens=500,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=content, usage=usage, stop_reason=stop, model="claude-opus-5-5",
                           stop_details=None)


def provider_with(model, response=None, error=None, **kwargs):
    p = AnthropicProvider(api_key="sk-test", model=model, **kwargs)
    ga, beta = FakeMessages(response, error), FakeMessages(response, error)
    p._client = SimpleNamespace(messages=ga, beta=SimpleNamespace(messages=beta))
    return p, ga, beta


PHILO = {"id": "plato", "name": "플라톤"}


def test_opus_request_shape_defaults():
    p, ga, beta = provider_with("claude-opus-5-5", fake_response())
    reply = p.reply(system="SYS", messages=[{"role": "user", "content": "hi"}], philosopher=PHILO,
                    max_tokens=300)
    assert reply.text == "좋은 질문이야. 너는 어떻게 생각해?"
    assert reply.usage.input_tokens == 900 and reply.usage.cache_read_input_tokens == 500
    # refusal fallback is opt-in (one generation per message by default)
    assert beta.calls == [] and len(ga.calls) == 1
    call = ga.calls[0]
    assert "fallbacks" not in call
    assert call["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert call["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"] == {"effort": "low"}
    assert call["max_tokens"] == 300 + 1024  # thinking cannot be disabled on Opus 5.5
    assert "thinking" not in call and "temperature" not in call


def test_opus_refusal_fallback_when_opted_in():
    p, ga, beta = provider_with("claude-opus-5-5", fake_response(), refusal_fallback=True)
    p.reply(system="SYS", messages=[{"role": "user", "content": "hi"}], philosopher=PHILO, max_tokens=300)
    assert ga.calls == [] and len(beta.calls) == 1
    call = beta.calls[0]
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]


def test_thinking_off_models_clamp_effort():
    p, ga, _ = provider_with("claude-sonnet-5-5", fake_response(), effort="max")
    p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)
    assert ga.calls[0]["thinking"] == {"type": "between_tools"}
    assert ga.calls[0]["output_config"] == {"effort": "high"}
    p, ga, _ = provider_with("claude-opus-5-5", fake_response(), effort="max")
    p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)
    assert ga.calls[0]["output_config"] == {"effort": "max"}


def test_truncated_summary_never_touches_memory():
    text = "<summary>\n- 연락 문제\n</summary>\n<relationship_values>\n- 신뢰"
    p, _, _ = provider_with("claude-haiku-5-5", fake_response(text=text, stop="max_tokens"))
    result = p.summarize(system="S", prompt="P", philosopher=PHILO, old_summary="", old_memory=None,
                         transcript=[], max_tokens=600)
    assert result.summary == "- 연락 문제" and result.user_memory is None


def test_sdk_loggers_are_not_debug():
    import logging

    create_app({"TESTING": True})
    for name in ("anthropic", "httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_haiku_request_disables_thinking_without_fallback():
    p, ga, beta = provider_with("claude-haiku-5-5", fake_response())
    p.reply(system="SYS", messages=[{"role": "user", "content": "hi"}], philosopher=PHILO, max_tokens=300)
    assert beta.calls == [] and len(ga.calls) == 1
    call = ga.calls[0]
    assert call["thinking"] == {"type": "disabled"} and call["max_tokens"] == 300
    assert "fallbacks" not in call


def test_refusal_and_empty_responses_raise():
    p, _, _ = provider_with("claude-opus-5-5", fake_response(stop="refusal"))
    with pytest.raises(AIError) as e:
        p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)
    assert e.value.reason == "refusal"
    p, _, _ = provider_with("claude-opus-5-5", fake_response(text="   "))
    with pytest.raises(AIError):
        p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)


def test_truncated_reply_is_cut_to_last_sentence():
    text = "기다리는 시간이 길면 여러 생각이 들 수 있어. 그런데 연락 횟수보다 어떤 순간에 특히 서운"
    p, _, _ = provider_with("claude-opus-5-5", fake_response(text=text, stop="max_tokens"))
    reply = p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)
    assert reply.text == "기다리는 시간이 길면 여러 생각이 들 수 있어."


def test_api_errors_are_not_retried():
    import anthropic

    p, ga, beta = provider_with("claude-opus-5-5", error=anthropic.APIConnectionError(request=None))
    with pytest.raises(AIError):
        p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)
    assert len(ga.calls) == 1 and beta.calls == []


def test_real_client_is_built_without_sdk_retries(monkeypatch):
    import anthropic

    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    p = AnthropicProvider(api_key="sk-test", model="claude-opus-5-5")
    assert captured == {}  # nothing happens at construction
    p.client
    assert captured["max_retries"] == 0 and captured["api_key"] == "sk-test"


def test_missing_key_fails_without_network():
    p = AnthropicProvider(api_key="", model="claude-opus-5-5")
    with pytest.raises(AIError) as e:
        p.reply(system="S", messages=[{"role": "user", "content": "x"}], philosopher=PHILO, max_tokens=300)
    assert e.value.reason == "missing_api_key"
