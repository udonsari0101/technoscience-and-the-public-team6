"""Conversation lifecycle, per-conversation memory and context compression.

API budget rules implemented here:
  * creating a conversation, titles, history and page loads never call AI
  * one user message → exactly one reply request (no automatic retry)
  * a context summary runs only when CONTEXT_SUMMARY_TRIGGER_MESSAGES
    unsummarized messages have piled up, compressing everything except the
    latest MAX_CONTEXT_MESSAGES; a failed summary waits for the next
    threshold step instead of retrying every turn
  * user-level memory is only refreshed inside that same summary call

Context sent to the model:
    system (base + controller + philosopher + style, static/cacheable)
    → [memory + conversation summary] reference block
    → unsummarized recent messages (incl. the new user message)
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass

from ..ai import AIError, AIProvider
from ..ai import prompts
from ..data import Repository
from ..data.base import SCHEMA_VERSION, utcnow
from ..philosophers import get_philosopher

log = logging.getLogger("askphilo.chat")

TITLE_MAX_CHARS = 24
SOFT_LIMIT_MARGIN = 10

MSG_AI_FAILED = "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
MSG_EMPTY = "메시지를 입력해 주세요."
MSG_TOO_LONG = "메시지가 너무 길어요. {limit}자 이내로 줄여 주세요."
MSG_HARD_LIMIT = "이 대화가 너무 길어져 새 대화에서 이어가는 게 좋아요."
MSG_SOFT_LIMIT = "이 대화가 꽤 길어졌어요. 새 상담을 시작하면 더 깔끔하게 이어갈 수 있어요."
MSG_NOT_FOUND = "대화를 찾을 수 없어요."
MSG_NOTHING_TO_RETRY = "다시 받을 답변이 없어요."
MSG_BAD_PHILOSOPHER = "철학자를 다시 선택해 주세요."


class ChatError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, **extra):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = http_status
        self.extra = extra  # JSON-safe fields added to the API error body


@dataclass
class ChatSettings:
    controller_version: str = "v1"
    controller_enabled: bool = True
    max_context_messages: int = 10
    max_output_tokens: int = 300
    summary_trigger: int = 20
    summary_max_output_tokens: int = 600
    soft_limit: int = 150
    hard_limit: int = 300
    max_user_chars: int = 2000

    @classmethod
    def from_config(cls, cfg) -> "ChatSettings":
        return cls(
            controller_version=cfg["CONTROLLER_VERSION"],
            controller_enabled=cfg["CONTROLLER_ENABLED"],
            max_context_messages=cfg["MAX_CONTEXT_MESSAGES"],
            max_output_tokens=cfg["MAX_OUTPUT_TOKENS"],
            summary_trigger=max(cfg["CONTEXT_SUMMARY_TRIGGER_MESSAGES"], cfg["MAX_CONTEXT_MESSAGES"] + 2),
            summary_max_output_tokens=cfg["SUMMARY_MAX_OUTPUT_TOKENS"],
            soft_limit=cfg["SOFT_CONVERSATION_MESSAGE_LIMIT"],
            hard_limit=max(cfg["HARD_CONVERSATION_MESSAGE_LIMIT"], cfg["SOFT_CONVERSATION_MESSAGE_LIMIT"]),
            max_user_chars=cfg["MAX_USER_MESSAGE_CHARS"],
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_title(text: str) -> str:
    """Deterministic title from the first user message (no AI call)."""
    flat = " ".join((text or "").split())
    if len(flat) <= TITLE_MAX_CHARS:
        return flat
    return flat[:TITLE_MAX_CHARS].rstrip() + "..."


def limit_status(message_count: int, settings: ChatSettings) -> dict:
    hard = message_count >= settings.hard_limit - 1
    soft = message_count >= settings.soft_limit - SOFT_LIMIT_MARGIN
    return {
        "message_count": message_count,
        "soft_limit_reached": soft and not hard,
        "hard_limit_reached": hard,
        "notice": MSG_HARD_LIMIT if hard else (MSG_SOFT_LIMIT if soft else None),
    }


def public_message(m: dict) -> dict:
    """Message fields safe to send to the browser."""
    return {"index": m["message_index"], "role": m["role"], "content": m["content"]}


def _usage_increments(provider: AIProvider, usage, *, summary: bool = False,
                      failed: bool = False) -> dict:
    if not provider.is_billable:
        return {"mock_calls": 1}
    inc = {"api_calls": 1}
    if failed:
        inc["failed_calls"] = 1
    if summary:
        inc["summary_calls"] = 1
        if usage:
            inc["summary_input_tokens"] = usage.input_tokens
            inc["summary_output_tokens"] = usage.output_tokens
    else:
        inc["reply_calls"] = 1
        if usage:
            inc["input_tokens"] = usage.input_tokens
            inc["output_tokens"] = usage.output_tokens
    if usage:
        inc["cache_read_input_tokens"] = usage.cache_read_input_tokens
        inc["cache_creation_input_tokens"] = usage.cache_creation_input_tokens
    return inc


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class ConversationService:
    _summarizing: set[str] = set()
    _summarizing_lock = threading.Lock()

    def __init__(self, repo: Repository, provider: AIProvider, settings: ChatSettings):
        self.repo = repo
        self.provider = provider
        self.settings = settings

    # ---- creation ------------------------------------------------------------
    def create_conversation(self, user_id: str, philosopher_id: str) -> dict:
        philosopher = get_philosopher(philosopher_id)
        if philosopher is None:
            raise ChatError("bad_philosopher", MSG_BAD_PHILOSOPHER, 400)
        now = utcnow()
        data = {
            "philosopher_id": philosopher["id"],
            "philosopher_name": philosopher["name"],
            "title": "",
            "controller_version": self.settings.controller_version,
            "controller_enabled": self.settings.controller_enabled,
            "schema_version": SCHEMA_VERSION,
            "message_count": 0,
            "last_message_preview": "",
            "last_message_role": None,
            "context_summary": "",
            "summarized_through_message_index": -1,
            "summary_version": 0,
            "context_summary_updated_at": None,
            "summary_retry_after_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        return self.repo.create_conversation(user_id, str(uuid.uuid4()), data)

    def get(self, user_id: str, conversation_id: str) -> dict:
        conv = self.repo.get_conversation(user_id, conversation_id)
        if conv is None:
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        return conv

    # ---- sending ---------------------------------------------------------------
    def _validate(self, content: str) -> str:
        text = (content or "").strip()
        if not text:
            raise ChatError("empty", MSG_EMPTY, 400)
        if len(text) > self.settings.max_user_chars:
            raise ChatError("too_long", MSG_TOO_LONG.format(limit=self.settings.max_user_chars), 400)
        return text

    def send(self, user_id: str, conversation_id: str, content: str) -> dict:
        text = self._validate(content)
        conv = self.get(user_id, conversation_id)
        if limit_status(conv.get("message_count", 0), self.settings)["hard_limit_reached"]:
            raise ChatError("hard_limit", MSG_HARD_LIMIT, 409,
                            status=limit_status(conv.get("message_count", 0), self.settings))
        updates = {"title": make_title(text)} if not conv.get("title") else None
        # 1) Persist the user's message first — an AI failure must never lose it.
        stored = self.repo.append_messages(user_id, conversation_id, [{
            "role": "user",
            "content": text,
            "provider": None,
            "model": None,
            "input_tokens": None,
            "output_tokens": None,
            "controller_version": conv.get("controller_version", self.settings.controller_version),
        }], conversation_updates=updates)
        user_message = stored[0]
        # 2) One reply request.
        return self._reply(user_id, conv, user_message)

    def retry(self, user_id: str, conversation_id: str) -> dict:
        """Explicit user retry for the pending (unanswered) user message."""
        conv = self.get(user_id, conversation_id)
        if conv.get("last_message_role") != "user":
            raise ChatError("nothing_to_retry", MSG_NOTHING_TO_RETRY, 409)
        last = self.repo.get_messages(user_id, conversation_id, limit=1)
        return self._reply(user_id, conv, last[0] if last else None)

    def _context(self, user_id: str, conv: dict) -> tuple[str, list[dict], dict]:
        philosopher = get_philosopher(conv["philosopher_id"])
        if philosopher is None:
            raise ChatError("bad_philosopher", MSG_BAD_PHILOSOPHER, 400)
        system = prompts.build_system_prompt(
            philosopher,
            controller_enabled=conv.get("controller_enabled", True),
            controller_version=conv.get("controller_version", self.settings.controller_version),
        )
        through = conv.get("summarized_through_message_index", -1)
        # Unsummarized turns only (bounded); everything older lives in the summary.
        raw = self.repo.get_messages(user_id, conv["id"], min_index=through + 1,
                                     limit=self.settings.summary_trigger)
        memory = self.repo.get_user_memory(user_id)
        messages = prompts.build_messages(raw, summary=conv.get("context_summary"), memory=memory)
        return system, messages, philosopher

    def build_request(self, user_id: str, conversation_id: str) -> dict:
        """The exact system + messages the next reply would use (diagnostics/tests)."""
        conv = self.get(user_id, conversation_id)
        system, messages, philosopher = self._context(user_id, conv)
        return {"system": system, "messages": messages, "philosopher_id": philosopher["id"]}

    def _reply(self, user_id: str, conv: dict, user_message: dict | None) -> dict:
        conversation_id = conv["id"]
        system, messages, philosopher = self._context(user_id, conv)
        try:
            reply = self.provider.reply(system=system, messages=messages, philosopher=philosopher,
                                        max_tokens=self.settings.max_output_tokens)
        except AIError as exc:
            log.warning("reply failed conv=%s reason=%s", conversation_id, exc.reason)
            if self.provider.is_billable and exc.reason != "missing_api_key":
                self.repo.add_usage(user_id, conversation_id,
                                    _usage_increments(self.provider, getattr(exc, "usage", None), failed=True))
            raise ChatError("ai_failed", MSG_AI_FAILED, 502, retryable=True,
                            user_message=public_message(user_message) if user_message else None,
                            conversation_id=conversation_id) from None
        stored = self.repo.append_messages(user_id, conversation_id, [{
            "role": "assistant",
            "content": reply.text,
            "provider": reply.provider,
            "model": reply.model,
            "input_tokens": reply.usage.input_tokens if self.provider.is_billable else None,
            "output_tokens": reply.usage.output_tokens if self.provider.is_billable else None,
            "cache_read_input_tokens": reply.usage.cache_read_input_tokens if self.provider.is_billable else None,
            "controller_version": conv.get("controller_version", self.settings.controller_version),
        }], usage_increments=_usage_increments(self.provider, reply.usage))
        assistant_message = stored[0]
        count = assistant_message["message_index"] + 1
        return {
            "conversation_id": conversation_id,
            "user_message": public_message(user_message) if user_message else None,
            "assistant_message": public_message(assistant_message),
            "status": limit_status(count, self.settings),
            "summary_due": self.summary_due(conv, count),
        }

    # ---- context compression -------------------------------------------------
    def summary_due(self, conv: dict, message_count: int) -> bool:
        through = conv.get("summarized_through_message_index", -1)
        unsummarized = message_count - (through + 1)
        if unsummarized < self.settings.summary_trigger:
            return False
        return message_count >= conv.get("summary_retry_after_count", 0)

    def maybe_summarize(self, user_id: str, conversation_id: str) -> bool:
        """Compress old messages into context_summary if the threshold is met.

        Never raises: summaries are an optimisation, the chat keeps working
        with the previous summary + recent messages if this fails.
        """
        key = f"{user_id}/{conversation_id}"
        with self._summarizing_lock:
            if key in self._summarizing:
                return False
            self._summarizing.add(key)
        try:
            return self._summarize(user_id, conversation_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("summary failed conv=%s: %s", conversation_id, type(exc).__name__)
            return False
        finally:
            with self._summarizing_lock:
                self._summarizing.discard(key)

    def _summarize(self, user_id: str, conversation_id: str) -> bool:
        conv = self.repo.get_conversation(user_id, conversation_id)
        if conv is None:
            return False
        count = conv.get("message_count", 0)
        if not self.summary_due(conv, count):
            return False
        philosopher = get_philosopher(conv["philosopher_id"])
        through = conv.get("summarized_through_message_index", -1)
        new_through = count - self.settings.max_context_messages - 1
        if new_through <= through or philosopher is None:
            return False
        # Only the old summary + the newly compressed slice are sent (never the full history).
        chunk = self.repo.get_messages(user_id, conversation_id, min_index=through + 1,
                                       before_index=new_through + 1)
        old_memory = self.repo.get_user_memory(user_id)
        prompt = prompts.build_summary_prompt(old_summary=conv.get("context_summary"),
                                              old_memory=old_memory, transcript=chunk,
                                              philosopher=philosopher)
        try:
            result = self.provider.summarize(
                system=prompts.SUMMARY_SYSTEM, prompt=prompt, philosopher=philosopher,
                old_summary=conv.get("context_summary"), old_memory=old_memory,
                transcript=chunk, max_tokens=self.settings.summary_max_output_tokens)
        except AIError as exc:
            log.warning("summary call failed conv=%s reason=%s", conversation_id, exc.reason)
            step = self.settings.summary_trigger - self.settings.max_context_messages
            # Wait for the next threshold step instead of retrying every turn.
            self._defer_summary(user_id, conversation_id, count + step,
                                _usage_increments(self.provider, getattr(exc, "usage", None),
                                                  summary=True, failed=True)
                                if self.provider.is_billable else None)
            return False
        ok = self.repo.update_summary(
            user_id, conversation_id, expected_through_index=through, summary=result.summary,
            through_index=new_through,
            usage_increments=_usage_increments(self.provider, result.usage, summary=True))
        if ok and result.user_memory is not None:
            cleaned = {
                "relationship_values": [s for s in result.user_memory.get("relationship_values", []) if s][:5],
                "preferences": [s for s in result.user_memory.get("preferences", []) if s][:5],
            }
            if cleaned != {k: (old_memory or {}).get(k, []) for k in cleaned}:
                self.repo.set_user_memory(user_id, cleaned)
        log.info("summary updated conv=%s through=%s ok=%s", conversation_id, new_through, ok)
        return ok

    def _defer_summary(self, user_id: str, conversation_id: str, retry_after: int,
                       usage: dict | None) -> None:
        self.repo.set_conversation_fields(user_id, conversation_id,
                                          {"summary_retry_after_count": retry_after})
        if usage:
            self.repo.add_usage(user_id, conversation_id, usage)

    # ---- reading / deleting ----------------------------------------------------
    def page(self, user_id: str, conversation_id: str, *, before: int | None = None,
             limit: int = 30) -> dict:
        msgs = self.repo.get_messages(user_id, conversation_id, before_index=before, limit=limit)
        first = msgs[0]["message_index"] if msgs else 0
        return {"messages": [public_message(m) for m in msgs], "has_more": first > 0,
                "oldest_index": first}

    def delete(self, user_id: str, conversation_id: str) -> bool:
        return self.repo.delete_conversation(user_id, conversation_id)


__all__ = ["ChatError", "ChatSettings", "ConversationService", "make_title", "limit_status",
           "public_message"]
