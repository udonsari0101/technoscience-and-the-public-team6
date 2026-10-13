"""Application configuration loaded from environment variables.

Every value has a safe default so the app boots with no .env at all
(AI_MODE=mock, no Firebase). Secrets are read here and nowhere else.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

AI_MODES = ("mock", "anthropic")
DATA_BACKENDS = ("auto", "firestore", "memory")


def _str(env, name, default=""):
    value = env.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _int(env, name, default, minimum=0):
    raw = _str(env, name, "")
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, value)


def _bool(env, name, default):
    raw = _str(env, name, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _effort(value: str) -> str:
    value = value.lower()
    return value if value in EFFORTS else "low"


def resolve_credentials_path(raw_path: str) -> Path | None:
    """Resolve FIREBASE_CREDENTIALS_PATH relative to the project root."""
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_config(env=None) -> dict:
    env = os.environ if env is None else env

    app_env = _str(env, "APP_ENV", "development").lower()
    is_production = app_env == "production"

    ai_mode = _str(env, "AI_MODE", "mock").lower()
    if ai_mode not in AI_MODES:
        # Never switch to a paid provider because of a typo.
        ai_mode = "mock"

    data_backend = _str(env, "DATA_BACKEND", "auto").lower()
    if data_backend not in DATA_BACKENDS:
        data_backend = "auto"

    secret_key = _str(env, "SECRET_KEY", "")
    secret_key_generated = False
    if not secret_key:
        # Sessions will not survive a restart, but the app still boots.
        secret_key = secrets.token_hex(32)
        secret_key_generated = True

    return {
        "APP_ENV": app_env,
        "APP_NAME": _str(env, "APP_NAME", "AskPhilo"),
        "APP_URL": _str(env, "APP_URL", "http://localhost:5000"),
        "IS_PRODUCTION": is_production,
        "SECRET_KEY": secret_key,
        "SECRET_KEY_GENERATED": secret_key_generated,
        "FIREBASE_CREDENTIALS_PATH": _str(env, "FIREBASE_CREDENTIALS_PATH", ""),
        "DATA_BACKEND": data_backend,
        "AI_MODE": ai_mode,
        "ANTHROPIC_API_KEY": _str(env, "ANTHROPIC_API_KEY", ""),
        "ANTHROPIC_MODEL": _str(env, "ANTHROPIC_MODEL", "claude-opus-5-5"),
        # Optional separate model for the rare context-summary call.
        "ANTHROPIC_SUMMARY_MODEL": _str(env, "ANTHROPIC_SUMMARY_MODEL", ""),
        "ANTHROPIC_EFFORT": _effort(_str(env, "ANTHROPIC_EFFORT", "low")),
        # Extra max_tokens for models whose thinking cannot be switched off.
        "ANTHROPIC_THINKING_HEADROOM_TOKENS": _int(env, "ANTHROPIC_THINKING_HEADROOM_TOKENS", 1024),
        "ANTHROPIC_TIMEOUT_SECONDS": _int(env, "ANTHROPIC_TIMEOUT_SECONDS", 45, minimum=5),
        "ANTHROPIC_PROMPT_CACHE": _bool(env, "ANTHROPIC_PROMPT_CACHE", True),
        "ANTHROPIC_REFUSAL_FALLBACK": _bool(env, "ANTHROPIC_REFUSAL_FALLBACK", True),
        "CONTROLLER_VERSION": _str(env, "CONTROLLER_VERSION", "v1"),
        "CONTROLLER_ENABLED": _bool(env, "CONTROLLER_ENABLED", True),
        "MAX_CONTEXT_MESSAGES": _int(env, "MAX_CONTEXT_MESSAGES", 10, minimum=2),
        "MAX_OUTPUT_TOKENS": _int(env, "MAX_OUTPUT_TOKENS", 300, minimum=50),
        "SOFT_CONVERSATION_MESSAGE_LIMIT": _int(env, "SOFT_CONVERSATION_MESSAGE_LIMIT", 150, minimum=10),
        "HARD_CONVERSATION_MESSAGE_LIMIT": _int(env, "HARD_CONVERSATION_MESSAGE_LIMIT", 300, minimum=20),
        "CONTEXT_SUMMARY_TRIGGER_MESSAGES": _int(env, "CONTEXT_SUMMARY_TRIGGER_MESSAGES", 20, minimum=4),
        "SUMMARY_MAX_OUTPUT_TOKENS": _int(env, "SUMMARY_MAX_OUTPUT_TOKENS", 600, minimum=100),
        "MAX_USER_MESSAGE_CHARS": _int(env, "MAX_USER_MESSAGE_CHARS", 2000, minimum=100),
        "CHAT_MESSAGES_PER_MINUTE": _int(env, "CHAT_MESSAGES_PER_MINUTE", 12, minimum=1),
        "MOCK_AI_DELAY_MS": _int(env, "MOCK_AI_DELAY_MS", 500),
        "ENABLE_DEV_ROUTES": _bool(env, "ENABLE_DEV_ROUTES", not is_production),
        # Flask / session hardening
        "SESSION_COOKIE_NAME": "askphilo_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": is_production,
        "PERMANENT_SESSION_LIFETIME": 60 * 60 * 24 * 30,
        "MAX_CONTENT_LENGTH": 64 * 1024,
        "RENDER_GIT_COMMIT": _str(env, "RENDER_GIT_COMMIT", ""),
    }
