"""Conversation lifecycle, per-conversation memory and context compression.

API budget rules implemented here:
  * creating a conversation, titles, history and page loads never call AI
  * one user message → exactly one reply request (no automatic retry);
    a per-conversation lock stops parallel/duplicate replies
  * a context summary runs only when CONTEXT_SUMMARY_TRIGGER_MESSAGES
    unsummarized messages have piled up, compressing everything except the
    latest MAX_CONTEXT_MESSAGES; any failure waits for the next threshold
    step instead of retrying every turn
  * user-level memory is only refreshed inside that same summary call

Context sent to the model:
    system (base + controller + philosopher + style, static/cacheable)
    → [memory + conversation summary] reference block
    → unsummarized recent messages (incl. the new user message)
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from ..ai import AIError, AIProvider, AIReply, AIUsage
from ..ai import pricing, prompts
from ..ai.prompts import clean_title
from ..data import Conflict, DataError, Repository
from ..data.base import SCHEMA_VERSION, utcnow
from ..philosophers import get_philosopher
from . import bubbles
from . import images as image_files
from .quota import MSG_DAILY_LIMIT, daily_status, today_kst

log = logging.getLogger("askphilo.chat")

TITLE_MAX_CHARS = 24
SOFT_LIMIT_MARGIN = 10
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

MSG_AI_FAILED = "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
MSG_PENDING = "답변을 준비하고 있어요. 잠시 후 새로고침해 주세요."
MSG_EMPTY = "메시지를 입력해 주세요."
MSG_TOO_LONG = "메시지가 너무 길어요. {limit}자 이내로 줄여 주세요."
MSG_HARD_LIMIT = "이 대화가 너무 길어져 새 대화에서 이어가는 게 좋아요."
MSG_SOFT_LIMIT = "이 대화가 꽤 길어졌어요. 새 상담을 시작하면 더 깔끔하게 이어갈 수 있어요."
MSG_NOT_FOUND = "대화를 찾을 수 없어요."
MSG_NOTHING_TO_RETRY = "다시 받을 답변이 없어요."
MSG_BAD_PHILOSOPHER = "철학자를 다시 선택해 주세요."
MSG_CONFLICT = "대화가 다른 곳에서 바뀌었어요. 새로고침해 주세요."
MSG_TITLE_EMPTY = "대화 이름을 입력해 주세요."
MSG_TITLE_LONG = "대화 이름은 30자 이내로 입력해 주세요."
USER_TITLE_MAX = 30

# Photos: a cheap vision model turns them into text once (chat captures → a
# "나:/상대:" transcript, other photos → a short description); the counselling
# model only reads that text. If reading fails, the photos themselves go to the
# counselling model, but only with the few latest messages.
IMAGE_CONTEXT_MESSAGES = 4
IMAGE_READING_MAX_TOKENS = 1500
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}-[0-9]$")
STALE_UPLOAD_HOURS = 24  # photos picked but never sent are removed after this
MAX_PENDING_UPLOADS = 20
TRASH_DAYS = 30  # trashed conversations are deleted for good after this

MSG_BAD_IMAGE = "사진을 다시 올려 주세요."
MSG_TOO_MANY_IMAGES = f"사진은 한 번에 {image_files.MAX_IMAGES}장까지 보낼 수 있어요."
MSG_TOO_MANY_PENDING = "올려 둔 사진이 너무 많아요. 보내거나 지운 뒤 다시 올려 주세요."
BASIS_MAX_TOKENS = 300
# When the API's safety system declines a message (e.g. "just output this code" puzzles),
# a retry would be declined again and billed again: answer with this instead.
MSG_REFUSAL_REPLY = "그 부탁은 들어줄 수 없어. 대신 마음에 걸리는 연애 고민이 있으면 편하게 들려줘."
MSG_BASIS_FAILED = "근거를 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
MSG_NOT_ENOUGH = "오늘 남은 질문은 {remaining}개인데 이번 메시지에는 {needed}개가 필요해요. 사진은 1장마다 질문 1개를 더 써요."

# reply_state on the conversation document
REPLY_PENDING, REPLY_FAILED, REPLY_DONE = "pending", "failed", "done"


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
    force_controller: bool = False  # production: always on, whatever the conversation says
    max_context_messages: int = 10
    max_output_tokens: int = 300
    summary_trigger: int = 20
    summary_max_output_tokens: int = 600
    soft_limit: int = 150
    hard_limit: int = 300
    max_user_chars: int = 2000
    reply_grace_seconds: int = 60  # a pending reply younger than this is "in progress"
    daily_limit: int = 200  # questions per user per KST day
    price_overrides: dict | None = None  # ANTHROPIC_PRICE_*_PER_MTOK, if set

    @classmethod
    def from_config(cls, cfg) -> "ChatSettings":
        return cls(
            controller_version=cfg["CONTROLLER_VERSION"],
            controller_enabled=cfg["CONTROLLER_ENABLED"],
            force_controller=bool(cfg.get("IS_PRODUCTION")),
            max_context_messages=cfg["MAX_CONTEXT_MESSAGES"],
            max_output_tokens=cfg["MAX_OUTPUT_TOKENS"],
            summary_trigger=max(cfg["CONTEXT_SUMMARY_TRIGGER_MESSAGES"], cfg["MAX_CONTEXT_MESSAGES"] + 2),
            summary_max_output_tokens=cfg["SUMMARY_MAX_OUTPUT_TOKENS"],
            soft_limit=cfg["SOFT_CONVERSATION_MESSAGE_LIMIT"],
            hard_limit=max(cfg["HARD_CONVERSATION_MESSAGE_LIMIT"], cfg["SOFT_CONVERSATION_MESSAGE_LIMIT"]),
            max_user_chars=cfg["MAX_USER_MESSAGE_CHARS"],
            reply_grace_seconds=cfg["ANTHROPIC_TIMEOUT_SECONDS"] + 15,
            daily_limit=cfg["CHAT_MESSAGES_PER_DAY"],
            price_overrides=pricing.overrides_from_config(cfg) or None,
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


def public_message(m: dict, conversation_id: str | None = None) -> dict:
    """Message fields safe to send to the browser (photos as same-origin URLs)."""
    created = m.get("created_at")
    out = {"index": m["message_index"], "role": m["role"], "content": m["content"],
           "time": bubbles.clock(created), "day": bubbles.day_key(created), "day_label": bubbles.day_label(created)}
    if m["role"] == "assistant":
        out["bubbles"] = bubbles.bubbles(m["content"])
        if m.get("basis"):
            out["basis"] = m["basis"]
    if m.get("images"):
        out["images"] = [{"url": f"/images/{ref['id']}", "w": ref.get("w"), "h": ref.get("h"),
                          "g": ref.get("g", 0)} for ref in m["images"]]
    return out


def _usage_increments(provider: AIProvider, usage, *, summary: bool = False,
                      failed: bool = False, model: str | None = None,
                      price_overrides: dict | None = None, vision: bool = False) -> dict:
    if not provider.is_billable:
        return {"mock_calls": 1}
    inc = {"api_calls": 1}
    if vision:  # photo reading: priced, but not a reply (keeps the per-reply average honest)
        inc["vision_calls"] = 1
        if failed:
            inc["failed_calls"] = 1
        cost = pricing.cost_micro_usd(model or getattr(provider, "vision_model", None), usage, price_overrides)
        if cost:
            inc["cost_micro_usd"] = cost
        if usage:
            inc.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                       cache_read_input_tokens=usage.cache_read_input_tokens,
                       cache_creation_input_tokens=usage.cache_creation_input_tokens)
        return inc
    # What this call cost, priced with the model that actually answered.
    model = model or getattr(provider, "summary_model" if summary else "model", None)
    cost = pricing.cost_micro_usd(model, usage, price_overrides)
    if cost:
        inc["cost_micro_usd"] = cost
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


class _KeyLock:
    """In-process set of busy keys (one Gunicorn worker → process-wide)."""

    def __init__(self):
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    @contextmanager
    def hold(self, key: str):
        with self._lock:
            if key in self._busy:
                yield False
                return
            self._busy.add(key)
        try:
            yield True
        finally:
            with self._lock:
                self._busy.discard(key)

    def busy(self, key: str) -> bool:
        with self._lock:
            return key in self._busy


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class ConversationService:
    _replying = _KeyLock()
    _summarizing = _KeyLock()

    def __init__(self, repo: Repository, provider: AIProvider, settings: ChatSettings):
        self.repo = repo
        self.provider = provider
        self.settings = settings

    # ---- creation / lookup -------------------------------------------------------
    def create_conversation(self, user_id: str, philosopher_id: str) -> dict:
        philosopher = get_philosopher(philosopher_id)
        if philosopher is None:
            raise ChatError("bad_philosopher", MSG_BAD_PHILOSOPHER, 400)
        now = utcnow()
        data = {
            "philosopher_id": philosopher["id"],
            "philosopher_name": philosopher["name"],
            "title": "",
            "title_source": None,  # "auto" (first message) · "ai" · "user"
            "controller_version": self.settings.controller_version,
            "controller_enabled": self.settings.controller_enabled or self.settings.force_controller,
            "schema_version": SCHEMA_VERSION,
            "message_count": 0,
            "last_message_preview": "",
            "last_message_role": None,
            "reply_state": None,
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
        # Only ids we generated reach Firestore (no reserved/oversized paths).
        if not isinstance(conversation_id, str) or not UUID_RE.match(conversation_id):
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        conv = self.repo.get_conversation(user_id, conversation_id)
        if conv is None or conv.get("deleted_at"):  # trashed: only the trash page sees it
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        return conv

    def reply_state(self, user_id: str, conv: dict, *, lock_held: bool = False) -> str | None:
        """'pending' (still being generated), 'failed', or None when answered."""
        if conv.get("last_message_role") != "user":
            return None
        if not lock_held and self._replying.busy(f"{user_id}/{conv['id']}"):
            return REPLY_PENDING
        state = conv.get("reply_state")
        if state == REPLY_FAILED:
            return REPLY_FAILED
        updated = conv.get("updated_at")
        if updated and utcnow() - updated < timedelta(seconds=self.settings.reply_grace_seconds):
            return REPLY_PENDING
        return REPLY_FAILED

    # ---- sending -------------------------------------------------------------------
    def validate(self, content, *, allow_empty: bool = False) -> str:
        """allow_empty: a message may be photos only."""
        if content is None and allow_empty:
            return ""
        if not isinstance(content, str):
            raise ChatError("empty", MSG_EMPTY, 400)
        text = content.strip()
        if not text and not allow_empty:
            raise ChatError("empty", MSG_EMPTY, 400)
        if len(text) > self.settings.max_user_chars:
            raise ChatError("too_long", MSG_TOO_LONG.format(limit=self.settings.max_user_chars), 400)
        return text

    def check_images(self, image_ids) -> list[str]:
        """Upload ids sent with a message (validated, de-duplicated, in order)."""
        if image_ids is None:
            return []
        if not isinstance(image_ids, list) or not all(isinstance(i, str) for i in image_ids):
            raise ChatError("bad_image", MSG_BAD_IMAGE, 400)
        ids = list(dict.fromkeys(image_ids))
        if len(ids) > image_files.MAX_IMAGES:
            raise ChatError("too_many_images", MSG_TOO_MANY_IMAGES, 400)
        if not all(UPLOAD_ID_RE.match(i) for i in ids):
            raise ChatError("bad_image", MSG_BAD_IMAGE, 400)
        return ids

    def send(self, user_id: str, conversation_id: str, content, image_ids: list | None = None,
             on_event=None) -> dict:
        """image_ids: ids returned by stage_upload (each photo uses one more question).
        on_event(kind, data): progress for streaming — "user" (stored), "status",
        "sentence" (each finished sentence of the reply while it is written)."""
        emit = on_event or (lambda kind, data: None)
        image_ids = self.check_images(image_ids)
        text = self.validate(content, allow_empty=bool(image_ids))
        conv = self.get(user_id, conversation_id)
        with self._replying.hold(f"{user_id}/{conversation_id}") as acquired:
            if not acquired:
                raise ChatError("in_progress", MSG_PENDING, 409)
            status = limit_status(conv.get("message_count", 0), self.settings)
            if status["hard_limit_reached"]:
                raise ChatError("hard_limit", MSG_HARD_LIMIT, 409, status=status)
            uploads = self._load_uploads(user_id, image_ids)
            questions = 1 + len(uploads)
            day, used = self._reserve(user_id, questions)
            try:
                updates = {"reply_state": REPLY_PENDING}
                if not conv.get("title"):
                    # Instant placeholder; the first reply refines it (same API call).
                    updates["title"] = make_title(text) if text else "사진"
                    updates["title_source"] = "auto"
                message = {
                    "role": "user",
                    "content": text,
                    "provider": None,
                    "model": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "controller_version": conv.get("controller_version", self.settings.controller_version),
                }
                if uploads:
                    message["images"] = [{"id": d["_id"], "w": d.get("width"), "h": d.get("height"), "g": g}
                                         for g, parts in enumerate(uploads) for d in parts]
                    emit("status", {"stage": "reading_photos"})
                    reading = self._read_images(user_id, conversation_id, uploads)
                    if reading:
                        message["image_text"] = reading
                    # Claim the photos for this message (index = the one append_messages assigns).
                    self.repo.attach_images(user_id, [d["_id"] for parts in uploads for d in parts],
                                            conversation_id, conv.get("message_count", 0))
                # 1) Persist the user's message first — an AI failure must never lose it.
                stored = self.repo.append_messages(user_id, conversation_id, [message],
                                                   conversation_updates=updates)
                emit("user", {"user_message": public_message(stored[0], conversation_id),
                              "conversation_id": conversation_id})
                # 2) Exactly one reply request.
                result = self._reply(user_id, conv, stored[0], on_event=on_event)
            except Exception:
                self._refund(user_id, day, questions)
                raise
            result["daily"] = daily_status(used, self.settings.daily_limit)
            return result

    def retry(self, user_id: str, conversation_id: str) -> dict:
        """Explicit user retry for the unanswered last user message."""
        conv = self.get(user_id, conversation_id)
        with self._replying.hold(f"{user_id}/{conversation_id}") as acquired:
            if not acquired:
                raise ChatError("in_progress", MSG_PENDING, 409)
            state = self.reply_state(user_id, conv, lock_held=True)
            if state is None:
                raise ChatError("nothing_to_retry", MSG_NOTHING_TO_RETRY, 409)
            if state == REPLY_PENDING:
                # Possibly still running in another process: don't pay twice.
                raise ChatError("in_progress", MSG_PENDING, 409)
            last = self.repo.get_messages(user_id, conversation_id, limit=1)
            if not last or last[0]["role"] != "user":
                raise ChatError("nothing_to_retry", MSG_NOTHING_TO_RETRY, 409)
            day, used = self._reserve(user_id)
            try:
                self.repo.set_conversation_fields(user_id, conversation_id, {"reply_state": REPLY_PENDING})
                result = self._reply(user_id, conv, last[0])
            except Exception:
                self._refund(user_id, day)
                raise
            result["daily"] = daily_status(used, self.settings.daily_limit)
            return result

    # ---- photos ----------------------------------------------------------------------
    def stage_upload(self, user_id: str, fileobj) -> dict:
        """Shrink one uploaded photo and keep it (base64 JPEG parts) until a message uses it."""
        pending = self.repo.find_images(user_id, None)
        stale = [i for i, created in pending.items()
                 if created and utcnow() - created > timedelta(hours=STALE_UPLOAD_HOURS)]
        if stale:
            self.repo.delete_images(user_id, stale)
        if len({i.split("-")[0] for i in pending} - {i.split("-")[0] for i in stale}) >= MAX_PENDING_UPLOADS:
            raise ChatError("too_many_pending", MSG_TOO_MANY_PENDING, 429)
        parts = image_files.process_image(fileobj)  # ImageError → the view answers 400
        upload_id = uuid.uuid4().hex
        now = utcnow()
        for n, part in enumerate(parts):
            self.repo.put_image(user_id, f"{upload_id}-{n}", {
                "data_b64": base64.b64encode(part["data"]).decode("ascii"),
                "mime": "image/jpeg",
                "width": part["width"],
                "height": part["height"],
                "bytes": len(part["data"]),
                "upload_id": upload_id,
                "part": n,
                "conversation_id": None,
                "message_index": None,
                "created_at": now,
            })
        return {"id": upload_id, "parts": len(parts), "url": f"/images/{upload_id}-0",
                "width": parts[0]["width"], "height": parts[0]["height"]}

    def discard_upload(self, user_id: str, upload_id: str) -> None:
        """Remove a photo that was picked but not sent."""
        if not isinstance(upload_id, str) or not UPLOAD_ID_RE.match(upload_id):
            raise ChatError("bad_image", MSG_BAD_IMAGE, 400)
        ids = [f"{upload_id}-{n}" for n in range(image_files.MAX_PARTS)]
        meta = self.repo.get_images(user_id, ids, with_data=False)
        unsent = [i for i, d in meta.items() if d.get("conversation_id") is None]
        if unsent:
            self.repo.delete_images(user_id, unsent)

    def _load_uploads(self, user_id: str, upload_ids: list[str]) -> list[list[dict]]:
        """Unsent uploads of this user, each as its parts (with data) top to bottom."""
        if not upload_ids:
            return []
        ids = [f"{u}-{n}" for u in upload_ids for n in range(image_files.MAX_PARTS)]
        docs = self.repo.get_images(user_id, ids)
        uploads = []
        for upload_id in upload_ids:
            parts = sorted((dict(d, _id=i) for i, d in docs.items() if d.get("upload_id") == upload_id),
                           key=lambda d: d.get("part", 0))
            if not parts or any(d.get("conversation_id") is not None for d in parts):
                raise ChatError("bad_image", MSG_BAD_IMAGE, 400)
            uploads.append(parts)
        return uploads

    def _read_images(self, user_id: str, conversation_id: str, uploads: list[list[dict]]) -> str | None:
        """Photos → text with the (cheap) vision model. None: send the photos themselves."""
        try:
            reading = self.provider.read_images(
                system=prompts.IMAGE_READER,
                images=[[{"mime": d.get("mime") or "image/jpeg", "data_b64": d["data_b64"]} for d in parts]
                        for parts in uploads],
                max_tokens=IMAGE_READING_MAX_TOKENS)
        except NotImplementedError:
            return None
        except AIError as exc:
            log.warning("image reading failed conv=%s reason=%s", conversation_id, exc.reason)
            usage = getattr(exc, "usage", None)
            if usage and self.provider.is_billable:
                failed = _usage_increments(self.provider, usage, vision=True, failed=True,
                                           price_overrides=self.settings.price_overrides)
                self._track_usage(user_id, conversation_id, failed)
            return None
        increments = _usage_increments(self.provider, reading.usage, vision=True, model=reading.model,
                                       price_overrides=self.settings.price_overrides)
        self._track_usage(user_id, conversation_id, increments)
        return reading.text or None

    def _track_usage(self, user_id: str, conversation_id: str, increments: dict) -> None:
        try:
            self.repo.add_usage(user_id, conversation_id, increments)
        except DataError:
            log.warning("could not record usage conv=%s", conversation_id)
        self._track_day(increments)

    def image(self, user_id: str, image_id: str) -> tuple[bytes, str] | None:
        """Decoded photo part of this user (for display), or None."""
        if not isinstance(image_id, str) or not IMAGE_ID_RE.match(image_id):
            return None
        doc = self.repo.get_images(user_id, [image_id]).get(image_id)
        if not doc or not doc.get("data_b64"):
            return None
        return base64.b64decode(doc["data_b64"]), doc.get("mime") or "image/jpeg"

    # ---- daily allowance -------------------------------------------------------------
    def daily(self, user_id: str) -> dict:
        """Today's question allowance (KST day) for display."""
        return daily_status(self.repo.get_daily_usage(user_id, today_kst()), self.settings.daily_limit)

    def _reserve(self, user_id: str, count: int = 1) -> tuple[str, int]:
        day = today_kst()
        used = self.repo.reserve_daily_message(user_id, day, self.settings.daily_limit, count)
        if used is None:
            limit = self.settings.daily_limit
            current = self.repo.get_daily_usage(user_id, day)
            message = (MSG_NOT_ENOUGH.format(remaining=limit - current, needed=count)
                       if current < limit else MSG_DAILY_LIMIT)
            raise ChatError("daily_limit", message, 429, daily=daily_status(current, limit), needed=count)
        return day, used

    def _refund(self, user_id: str, day: str, count: int = 1) -> None:
        try:
            self.repo.refund_daily_message(user_id, day, count)
        except DataError:
            log.warning("could not refund daily question user=%s", user_id)

    def _system_prompt(self, conv: dict, philosopher: dict) -> str:
        enabled = True if self.settings.force_controller else conv.get("controller_enabled", True)
        return prompts.build_system_prompt(
            philosopher, controller_enabled=enabled,
            controller_version=conv.get("controller_version", self.settings.controller_version))

    def _context(self, user_id: str, conv: dict) -> tuple[str, list[dict], dict, list[dict]]:
        philosopher = get_philosopher(conv["philosopher_id"])
        if philosopher is None:
            raise ChatError("bad_philosopher", MSG_BAD_PHILOSOPHER, 400)
        system = self._system_prompt(conv, philosopher)
        through = conv.get("summarized_through_message_index", -1)
        # Unsummarized turns only (bounded); everything older lives in the summary.
        raw = self.repo.get_messages(user_id, conv["id"], min_index=through + 1,
                                     limit=self.settings.summary_trigger)
        memory = self.repo.get_user_memory(user_id)
        # Only photos that could not be read into text travel as images (latest messages only).
        recent_ids = [ref["id"] for m in raw[-IMAGE_CONTEXT_MESSAGES:]
                      if m["role"] == "user" and not m.get("image_text")
                      for ref in m.get("images") or []]
        image_data = self.repo.get_images(user_id, recent_ids) if recent_ids else {}
        messages = prompts.build_messages(raw, summary=conv.get("context_summary"), memory=memory,
                                          image_data=image_data, image_window=IMAGE_CONTEXT_MESSAGES)
        return system, messages, philosopher, raw

    def build_request(self, user_id: str, conversation_id: str) -> dict:
        """The exact system + messages the next reply would use (diagnostics/tests)."""
        conv = self.get(user_id, conversation_id)
        system, messages, philosopher, _ = self._context(user_id, conv)
        return {"system": system, "messages": messages, "philosopher_id": philosopher["id"]}

    def _track_day(self, increments: dict, *, replies: int = 0) -> None:
        """Service-wide per-day counters for the admin dashboard (best effort)."""
        data = dict(increments or {})
        if replies:
            data["replies"] = replies
        try:
            self.repo.record_usage_day(today_kst(), data)
        except DataError:
            log.warning("could not record daily usage")

    def _fail(self, user_id: str, conversation_id: str, user_message: dict, reason: str,
              usage=None, billed: bool = False) -> ChatError:
        log.warning("reply failed conv=%s reason=%s", conversation_id, reason)
        try:
            self.repo.set_conversation_fields(user_id, conversation_id, {"reply_state": REPLY_FAILED})
            if billed and self.provider.is_billable:
                failed = _usage_increments(self.provider, usage, failed=True,
                                           price_overrides=self.settings.price_overrides)
                self.repo.add_usage(user_id, conversation_id, failed)
                self._track_day(failed)
        except DataError:
            pass  # best effort; the reply already failed
        return ChatError("ai_failed", MSG_AI_FAILED, 502, retryable=True,
                         user_message=public_message(user_message, conversation_id),
                         conversation_id=conversation_id)

    def _reply(self, user_id: str, conv: dict, user_message: dict, on_event=None) -> dict:
        conversation_id = conv["id"]
        system, messages, philosopher, raw = self._context(user_id, conv)
        if not raw or raw[-1]["role"] != "user":
            raise ChatError("nothing_to_retry", MSG_NOTHING_TO_RETRY, 409)
        # The first answer also carries a short title — inside the same request.
        want_title = user_message["message_index"] == 0 and conv.get("title_source") != "user"
        stream = bubbles.SentenceStream(lambda s: on_event("sentence", {"text": s})) if on_event else None
        try:
            reply = self.provider.reply(system=system, messages=messages, philosopher=philosopher,
                                        max_tokens=self.settings.max_output_tokens,
                                        want_title=want_title,
                                        **({"on_text": stream.feed} if stream else {}))
        except AIError as exc:
            if exc.reason != "refusal":
                raise self._fail(user_id, conversation_id, user_message, exc.reason,
                                 getattr(exc, "usage", None),
                                 billed=exc.reason != "missing_api_key") from None
            log.info("reply declined by the API conv=%s", conversation_id)
            reply = AIReply(text=MSG_REFUSAL_REPLY, provider=self.provider.name,
                            model=getattr(self.provider, "model", None) or self.provider.name,
                            usage=getattr(exc, "usage", None) or AIUsage(), stop_reason="refusal")
        if stream:
            stream.finish(reply.text)  # whatever the stream hadn't finished yet
        increments = _usage_increments(self.provider, reply.usage, model=reply.model,
                                       price_overrides=self.settings.price_overrides)
        updates = {"reply_state": REPLY_DONE}
        new_title = clean_title(reply.title) if want_title else None
        if new_title:
            updates.update(title=new_title, title_source="ai")
        try:
            stored = self.repo.append_messages(user_id, conversation_id, [{
                "role": "assistant",
                "content": reply.text,
                "provider": reply.provider,
                "model": reply.model,
                "input_tokens": reply.usage.input_tokens if self.provider.is_billable else None,
                "output_tokens": reply.usage.output_tokens if self.provider.is_billable else None,
                "cache_read_input_tokens": reply.usage.cache_read_input_tokens if self.provider.is_billable else None,
                "controller_version": conv.get("controller_version", self.settings.controller_version),
                **({"refused": True} if reply.stop_reason == "refusal" else {}),
            }], conversation_updates=updates,
                usage_increments=increments,
                expect_count=user_message["message_index"] + 1)
        except Conflict:
            # Someone else answered or wrote meanwhile: keep the order intact.
            log.warning("reply dropped (conflict) conv=%s", conversation_id)
            raise ChatError("conflict", MSG_CONFLICT, 409) from None
        except DataError as exc:
            raise self._fail(user_id, conversation_id, user_message, type(exc).__name__,
                             reply.usage, billed=True) from None
        self._track_day(increments, replies=1)
        assistant_message = stored[0]
        count = assistant_message["message_index"] + 1
        return {
            "conversation_id": conversation_id,
            "user_message": public_message(user_message, conversation_id),
            "assistant_message": public_message(assistant_message, conversation_id),
            "status": limit_status(count, self.settings),
            "summary_due": self.summary_due(conv, count),
            "title": new_title,
        }

    # ---- context compression ---------------------------------------------------------
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
        with self._summarizing.hold(f"{user_id}/{conversation_id}") as acquired:
            if not acquired:
                return False
            try:
                return self._summarize(user_id, conversation_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("summary failed conv=%s: %s", conversation_id, type(exc).__name__)
                return False

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
        step = self.settings.summary_trigger - self.settings.max_context_messages
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
            usage = (_usage_increments(self.provider, getattr(exc, "usage", None), summary=True, failed=True,
                                       price_overrides=self.settings.price_overrides)
                     if self.provider.is_billable else None)
            self._defer_summary(user_id, conversation_id, count + step, usage)
            if usage:
                self._track_day(usage)
            return False
        summary_increments = _usage_increments(self.provider, result.usage, summary=True, model=result.model,
                                               price_overrides=self.settings.price_overrides)
        self._track_day(summary_increments)
        try:
            ok = self.repo.update_summary(
                user_id, conversation_id, expected_through_index=through, summary=result.summary,
                through_index=new_through,
                usage_increments=summary_increments,
                title=clean_title(result.title))
            if ok and result.user_memory is not None:
                merged = {k: list((old_memory or {}).get(k) or []) for k in ("relationship_values", "preferences")}
                for key, items in result.user_memory.items():
                    if items is not None and key in merged:
                        merged[key] = [s for s in items if s][:5]
                if merged != {k: list((old_memory or {}).get(k) or []) for k in merged}:
                    self.repo.set_user_memory(user_id, merged)
        except Exception as exc:  # noqa: BLE001 — a paid summary must not be re-bought next turn
            log.warning("summary save failed conv=%s: %s", conversation_id, type(exc).__name__)
            self._defer_summary(user_id, conversation_id, count + step, None)
            return False
        log.info("summary updated conv=%s through=%s ok=%s", conversation_id, new_through, ok)
        return ok

    def _defer_summary(self, user_id: str, conversation_id: str, retry_after: int,
                       usage: dict | None) -> None:
        try:
            self.repo.set_conversation_fields(user_id, conversation_id,
                                              {"summary_retry_after_count": retry_after})
            if usage:
                self.repo.add_usage(user_id, conversation_id, usage)
        except DataError:
            log.warning("could not defer summary conv=%s", conversation_id)

    # ---- reading / deleting --------------------------------------------------------
    def page(self, user_id: str, conversation_id: str, *, before: int | None = None,
             limit: int = 30) -> dict:
        msgs = self.repo.get_messages(user_id, conversation_id, before_index=before, limit=limit)
        first = msgs[0]["message_index"] if msgs else 0
        return {"messages": [public_message(m, conversation_id) for m in msgs], "has_more": first > 0,
                "oldest_index": first}

    def delete(self, user_id: str, conversation_id: str) -> bool:
        """Permanent delete (messages and photos too)."""
        return self.repo.delete_conversation(user_id, conversation_id)

    # ---- info button: why this reply sounds like its philosopher -----------------------
    def basis(self, user_id: str, conversation_id: str, message_index) -> dict:
        """The note behind a reply's info button. Written on first request by the light
        model (uses one daily question), then stored on the message and free to reopen."""
        conv = self.get(user_id, conversation_id)
        if not isinstance(message_index, int) or message_index < 1:
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        pair = self.repo.get_messages(user_id, conversation_id, min_index=message_index - 1,
                                      before_index=message_index + 1)
        reply = next((m for m in pair if m["message_index"] == message_index), None)
        asked = next((m for m in pair if m["message_index"] == message_index - 1), None)
        if reply is None or reply["role"] != "assistant":
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        if reply.get("basis"):
            return {"basis": reply["basis"], "charged": False, "daily": self.daily(user_id)}
        philosopher = get_philosopher(conv["philosopher_id"])
        with self._replying.hold(f"basis:{user_id}/{conversation_id}/{message_index}") as acquired:
            if not acquired:
                raise ChatError("in_progress", MSG_PENDING, 409)
            day, used = self._reserve(user_id, 1)
            try:
                asked_text = (asked or {}).get("content") or ""
                if asked and asked.get("image_text"):
                    asked_text += f"\n[보낸 사진 내용]\n{asked['image_text']}"
                prompt = prompts.build_basis_prompt(philosopher=philosopher, user_text=asked_text,
                                                    reply_text=reply["content"])
                note = self.provider.explain(system=prompts.BASIS_SYSTEM, prompt=prompt,
                                             max_tokens=BASIS_MAX_TOKENS)
            except (AIError, NotImplementedError) as exc:
                self._refund(user_id, day, 1)
                usage = getattr(exc, "usage", None)
                if usage and self.provider.is_billable:
                    self._track_usage(user_id, conversation_id, {
                        **_usage_increments(self.provider, usage, vision=True, failed=True,
                                            model=getattr(self.provider, "light_model", None),
                                            price_overrides=self.settings.price_overrides),
                        "vision_calls": 0, "basis_calls": 1})
                raise ChatError("basis_failed", MSG_BASIS_FAILED, 502) from None
            text = prompts.clean_basis(note.text)
            self.repo.set_message_fields(user_id, conversation_id, message_index, {"basis": text})
            increments = _usage_increments(self.provider, note.usage, vision=True, model=note.model,
                                           price_overrides=self.settings.price_overrides)
            if self.provider.is_billable:
                increments.update(vision_calls=0, basis_calls=1)
            self._track_usage(user_id, conversation_id, increments)
            return {"basis": text, "charged": True, "daily": daily_status(used, self.settings.daily_limit)}

    # ---- trash ---------------------------------------------------------------------
    def trash(self, user_id: str, conversation_id: str) -> None:
        """Move to the trash (restorable for TRASH_DAYS days)."""
        self.get(user_id, conversation_id)
        self.repo.trash_conversation(user_id, conversation_id, utcnow())

    def _trashed(self, user_id: str, conversation_id: str) -> dict:
        if not isinstance(conversation_id, str) or not UUID_RE.match(conversation_id):
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        conv = self.repo.get_conversation(user_id, conversation_id)
        if conv is None or not conv.get("deleted_at"):
            raise ChatError("not_found", MSG_NOT_FOUND, 404)
        return conv

    def restore(self, user_id: str, conversation_id: str) -> None:
        self._trashed(user_id, conversation_id)
        self.repo.restore_conversation(user_id, conversation_id)

    def delete_forever(self, user_id: str, conversation_id: str) -> None:
        self._trashed(user_id, conversation_id)
        self.repo.delete_conversation(user_id, conversation_id)

    def trash_items(self, user_id: str) -> list[dict]:
        """Trashed conversations; ones older than TRASH_DAYS are deleted on the way."""
        items = []
        for conv in self.repo.list_trash(user_id):
            purge_at = conv["deleted_at"] + timedelta(days=TRASH_DAYS)
            if purge_at <= utcnow():
                self.repo.delete_conversation(user_id, conv["id"])
                continue
            items.append({**conv, "purge_at": purge_at})
        return items

    def empty_trash(self, user_id: str) -> int:
        items = self.repo.list_trash(user_id, limit=500)
        for conv in items:
            self.repo.delete_conversation(user_id, conv["id"])
        return len(items)

    def rename(self, user_id: str, conversation_id: str, title) -> str:
        """User-chosen title; automatic titles never replace it afterwards."""
        self.get(user_id, conversation_id)
        if not isinstance(title, str) or not " ".join(title.split()):
            raise ChatError("title_empty", MSG_TITLE_EMPTY, 400)
        flat = " ".join(title.split())
        if len(flat) > USER_TITLE_MAX or any(ord(ch) < 32 for ch in flat):
            raise ChatError("title_long", MSG_TITLE_LONG, 400)
        self.repo.set_conversation_fields(user_id, conversation_id,
                                          {"title": flat, "title_source": "user"})
        return flat


__all__ = ["ChatError", "ChatSettings", "ConversationService", "make_title", "limit_status",
           "public_message", "REPLY_PENDING", "REPLY_FAILED"]
