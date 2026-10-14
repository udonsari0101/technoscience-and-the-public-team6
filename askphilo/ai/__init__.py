"""Provider selection.

AI_MODE=mock (default) never touches the network, even when an
ANTHROPIC_API_KEY is present. Only AI_MODE=anthropic builds the real
provider, and even then no request is made until a user sends a chat
message.
"""

from __future__ import annotations

import logging
import threading

from flask import Flask, current_app

from .base import AIError, AIProvider, AIReply, AIUsage, Explanation, ImageReading, SummaryResult  # noqa: F401

log = logging.getLogger("askphilo.ai")
EXT_KEY = "askphilo.ai_provider"
_lock = threading.Lock()


def _build(app: Flask) -> AIProvider:
    cfg = app.config
    if cfg["AI_MODE"] == "anthropic" and not cfg.get("TESTING"):
        from .anthropic_provider import AnthropicProvider

        if not cfg["ANTHROPIC_API_KEY"]:
            log.error("AI_MODE=anthropic but ANTHROPIC_API_KEY is empty; replies will fail")
        return AnthropicProvider(
            api_key=cfg["ANTHROPIC_API_KEY"],
            model=cfg["ANTHROPIC_MODEL"],
            summary_model=cfg["ANTHROPIC_SUMMARY_MODEL"] or cfg["ANTHROPIC_MODEL"],
            vision_model=cfg["ANTHROPIC_VISION_MODEL"] or cfg["ANTHROPIC_MODEL"],
            light_model=cfg["ANTHROPIC_LIGHT_MODEL"] or cfg["ANTHROPIC_MODEL"],
            effort=cfg["ANTHROPIC_EFFORT"],
            thinking_headroom=cfg["ANTHROPIC_THINKING_HEADROOM_TOKENS"],
            timeout=cfg["ANTHROPIC_TIMEOUT_SECONDS"],
            prompt_cache=cfg["ANTHROPIC_PROMPT_CACHE"],
            refusal_fallback=cfg["ANTHROPIC_REFUSAL_FALLBACK"],
        )
    from .mock import MockProvider

    return MockProvider(delay_ms=cfg["MOCK_AI_DELAY_MS"])


def get_provider(app: Flask | None = None) -> AIProvider:
    app = app or current_app._get_current_object()
    provider = app.extensions.get(EXT_KEY)
    if provider is None:
        with _lock:
            provider = app.extensions.get(EXT_KEY)
            if provider is None:
                provider = _build(app)
                app.extensions[EXT_KEY] = provider
    return provider


def set_provider(app: Flask, provider: AIProvider) -> None:
    """Inject a provider (tests)."""
    app.extensions[EXT_KEY] = provider
