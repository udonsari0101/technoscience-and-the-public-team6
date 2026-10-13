"""Anthropic Messages API provider (AI_MODE=anthropic only).

Cost rules enforced here:
  * one HTTP request per reply — SDK auto-retries are disabled (max_retries=0)
  * nothing is sent at import/startup; the client is created on first use
  * prompt caching on the stable prefix (system → context → turns)
  * optional (off by default) server-side refusal fallback where supported
"""

from __future__ import annotations

import logging
import threading

from .base import AIError, AIProvider, AIReply, AIUsage, SummaryResult
from .prompts import parse_summary_output

log = logging.getLogger("askphilo.ai")

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models where thinking can be switched off for short chat replies; on the
# rest (e.g. Opus 5.5) thinking is always on, so max_tokens gets headroom.
THINKING_OFF = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
}


def _trim_to_sentence(text: str) -> str:
    """Cut a max_tokens-truncated reply back to its last complete sentence."""
    cut = max(text.rfind(ch) for ch in (".", "?", "!", "…"))
    return text[: cut + 1].strip() if cut > len(text) * 0.4 else text.strip()


class AnthropicProvider(AIProvider):
    name = "anthropic"
    is_billable = True

    def __init__(self, *, api_key: str, model: str, summary_model: str | None = None,
                 effort: str = "low", thinking_headroom: int = 1024,
                 timeout: float = 45.0, prompt_cache: bool = True,
                 refusal_fallback: bool = False):
        self.api_key = api_key
        self.model = model
        self.summary_model = summary_model or model
        self.effort = effort
        self.thinking_headroom = max(0, thinking_headroom)
        self.timeout = timeout
        self.prompt_cache = prompt_cache
        self.refusal_fallback = refusal_fallback
        self._client = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ client
    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if not self.api_key:
                        raise AIError("missing_api_key")
                    import anthropic

                    self._client = anthropic.Anthropic(
                        api_key=self.api_key, max_retries=0, timeout=self.timeout
                    )
        return self._client

    # ----------------------------------------------------------------- request
    def _call(self, *, model: str, system: str, messages: list[dict], max_tokens: int):
        import anthropic

        params = {
            "model": model,
            "system": system,
            "messages": messages,
            "output_config": {"effort": self.effort},
        }
        thinking = THINKING_OFF.get(model)
        if thinking:
            params["thinking"] = thinking
            params["max_tokens"] = max_tokens
            if self.effort in ("xhigh", "max"):
                # Thinking-off modes are only accepted at effort high or below.
                params["output_config"] = {"effort": "high"}
        else:
            # Thinking shares max_tokens with the visible reply.
            params["max_tokens"] = max_tokens + self.thinking_headroom
        if self.prompt_cache:
            # Breakpoint 1: the static system prompt (survives summary rewrites).
            params["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
            # Breakpoint 2 (automatic): the end of the append-only conversation.
            params["cache_control"] = {"type": "ephemeral"}
        try:
            if self.refusal_fallback and model in FALLBACK_MODELS:
                response = self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                response = self.client.messages.create(**params)
        except AIError:
            raise
        except anthropic.APITimeoutError as exc:
            log.warning("anthropic timeout")
            raise AIError("timeout") from exc
        except anthropic.RateLimitError as exc:
            log.warning("anthropic rate limited (429)")
            raise AIError("rate_limited") from exc
        except anthropic.APIStatusError as exc:
            log.warning("anthropic status error %s request_id=%s", exc.status_code,
                        getattr(exc, "request_id", None))
            raise AIError(f"status_{exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            log.warning("anthropic connection error")
            raise AIError("connection") from exc
        except Exception as exc:  # noqa: BLE001 — never leak SDK internals to users
            log.warning("anthropic unexpected error: %s", type(exc).__name__)
            raise AIError("unexpected") from exc
        return response

    @staticmethod
    def _usage(response) -> AIUsage:
        u = getattr(response, "usage", None)
        if u is None:
            return AIUsage()
        return AIUsage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    @staticmethod
    def _text(response) -> str:
        parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()

    # ------------------------------------------------------------------- API
    def reply(self, *, system, messages, philosopher, max_tokens):
        response = self._call(model=self.model, system=system, messages=messages,
                              max_tokens=max_tokens)
        usage = self._usage(response)
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            log.info("anthropic refusal category=%s",
                     getattr(getattr(response, "stop_details", None), "category", None))
            err = AIError("refusal")
            err.usage = usage
            raise err
        text = self._text(response)
        if stop == "max_tokens" and text:
            text = _trim_to_sentence(text)
        if not text:
            err = AIError("empty")
            err.usage = usage
            raise err
        log.info("anthropic reply ok model=%s in=%s out=%s cache_read=%s",
                 getattr(response, "model", self.model), usage.input_tokens,
                 usage.output_tokens, usage.cache_read_input_tokens)
        return AIReply(text=text, provider=self.name,
                       model=getattr(response, "model", None) or self.model,
                       usage=usage, stop_reason=stop)

    def summarize(self, *, system, prompt, philosopher, old_summary, old_memory,
                  transcript, max_tokens):
        response = self._call(model=self.summary_model, system=system,
                              messages=[{"role": "user", "content": prompt}],
                              max_tokens=max_tokens)
        usage = self._usage(response)
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            err = AIError("refusal")
            err.usage = usage
            raise err
        summary, memory = parse_summary_output(self._text(response))
        if stop == "max_tokens":
            memory = None  # a cut-off answer must never overwrite stored memory
        if not summary:
            err = AIError("summary_unparseable")
            err.usage = usage
            raise err
        return SummaryResult(summary=summary, provider=self.name,
                             model=getattr(response, "model", None) or self.summary_model,
                             user_memory=memory, usage=usage)
