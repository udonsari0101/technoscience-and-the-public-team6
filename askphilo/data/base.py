"""Storage interface shared by the Firestore and in-memory backends.

Firestore layout (human-readable in the Firebase console):

    users/{user_id}
        conversations/{conversation_id}
            messages/{000000, 000001, ...}   zero-padded message_index
        memory/profile                        limited long-term user memory
    username_index/{username_normalized}      uniqueness guard

Every method returns plain dicts (with an "id" key) so the service layer
never depends on a backend type.
"""

from __future__ import annotations

from datetime import datetime, timezone

SCHEMA_VERSION = 1
MESSAGE_ID_WIDTH = 6

EMPTY_USAGE = {
    "api_calls": 0,
    "reply_calls": 0,
    "summary_calls": 0,
    "failed_calls": 0,
    "mock_calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "summary_input_tokens": 0,
    "summary_output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}


class DataError(Exception):
    """Base class for storage failures shown to users as a generic error."""


class DataUnavailable(DataError):
    """The backend is not configured or not reachable."""


class UsernameTaken(DataError):
    pass


class NotFound(DataError):
    pass


class Conflict(DataError):
    """An optimistic-concurrency precondition failed."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def message_doc_id(index: int) -> str:
    return f"{index:0{MESSAGE_ID_WIDTH}d}"


def preview(text: str, limit: int = 60) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def message_preview(message: dict) -> str:
    """List preview of a message; a photo-only message reads "사진"."""
    return preview(message.get("content")) or ("사진" if message.get("images") else "")


class Repository:
    """Abstract storage API. See firestore_repo / memory_repo."""

    backend = "abstract"

    # ---- users ---------------------------------------------------------
    def create_user(self, *, user_id: str, name: str, username: str,
                    username_normalized: str, password_hash: str,
                    role: str | None = None) -> dict:
        """Atomically reserve the username and create the user.

        Raises UsernameTaken if username_index already holds the name.
        """
        raise NotImplementedError

    def get_user(self, user_id: str) -> dict | None:
        raise NotImplementedError

    def get_user_by_username(self, username_normalized: str) -> dict | None:
        raise NotImplementedError

    def record_login(self, user_id: str) -> None:
        raise NotImplementedError

    def set_user_fields(self, user_id: str, fields: dict) -> None:
        raise NotImplementedError

    def list_users(self, limit: int = 500) -> list[dict]:
        """Users for the admin dashboard — never includes password data."""
        raise NotImplementedError

    # ---- service-wide daily usage (admin dashboard) ----------------------
    def record_usage_day(self, day: str, increments: dict, total_key: str = "all") -> None:
        """Add counters to usage_daily/{day} (KST date) and, in the same write,
        to the running total usage_totals/{total_key}."""
        raise NotImplementedError

    def get_usage_total(self, key: str = "all") -> dict:
        """Running totals since usage was first recorded ({} if none)."""
        raise NotImplementedError

    # ---- image attachments (users/{uid}/images/{part_id}, base64 JPEG) --------
    # Uploaded parts wait with conversation_id None until a message uses them.
    def put_image(self, user_id: str, image_id: str, doc: dict) -> None:
        raise NotImplementedError

    def get_images(self, user_id: str, image_ids: list[str], *, with_data: bool = True) -> dict[str, dict]:
        """{image_id: doc} for the ids that exist (without data_b64 if with_data=False)."""
        raise NotImplementedError

    def attach_images(self, user_id: str, image_ids: list[str], conversation_id: str,
                      message_index: int) -> None:
        raise NotImplementedError

    def delete_images(self, user_id: str, image_ids: list[str]) -> None:
        raise NotImplementedError

    def find_images(self, user_id: str, conversation_id: str | None) -> dict[str, datetime | None]:
        """{image_id: created_at} of a conversation's images (None: unattached uploads)."""
        raise NotImplementedError

    # ---- billing settings (admin) ------------------------------------------
    def get_billing_settings(self) -> dict:
        """settings/billing: {"credit_usd", "outside_spend_usd", ...} ({} if unset)."""
        raise NotImplementedError

    def set_billing_settings(self, data: dict) -> None:
        raise NotImplementedError

    def get_usage_days(self, days: list[str]) -> list[dict]:
        """Counters for the given KST dates (missing days omitted), newest first:
        [{"day": "2026-10-14", **counters}]. Direct reads — no index needed."""
        raise NotImplementedError

    # ---- conversations ---------------------------------------------------
    def create_conversation(self, user_id: str, conversation_id: str, data: dict) -> dict:
        raise NotImplementedError

    def get_conversation(self, user_id: str, conversation_id: str) -> dict | None:
        raise NotImplementedError

    def list_conversations(self, user_id: str, limit: int = 20,
                           before: datetime | None = None) -> list[dict]:
        """Newest first (updated_at desc), conversations in the trash left out."""
        raise NotImplementedError

    def list_trash(self, user_id: str, limit: int = 100) -> list[dict]:
        """Conversations in the trash, most recently trashed first."""
        raise NotImplementedError

    def trash_conversation(self, user_id: str, conversation_id: str, when: datetime) -> None:
        raise NotImplementedError

    def restore_conversation(self, user_id: str, conversation_id: str) -> None:
        raise NotImplementedError

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        """Permanently delete the conversation, its messages and its images."""
        raise NotImplementedError

    # ---- messages ----------------------------------------------------------
    def append_messages(self, user_id: str, conversation_id: str,
                        messages: list[dict], *, conversation_updates: dict | None = None,
                        usage_increments: dict | None = None,
                        expect_count: int | None = None) -> list[dict]:
        """Transactionally assign message_index values and store messages.

        Updates message_count, updated_at, last_message_* on the
        conversation, applies `conversation_updates` (except that a title the
        user chose — title_source "user" — is never replaced automatically)
        and adds `usage_increments` to usage.*.
        With `expect_count`, raises Conflict unless message_count equals it
        (keeps a reply directly after the message it answers).
        Returns the stored messages (with message_index).
        """
        raise NotImplementedError

    def get_messages(self, user_id: str, conversation_id: str, *,
                     min_index: int = 0, before_index: int | None = None,
                     limit: int | None = None) -> list[dict]:
        """Messages ordered by message_index ascending.

        With `limit`, returns the LAST `limit` messages in the range.
        """
        raise NotImplementedError

    # ---- context summary -------------------------------------------------
    def update_summary(self, user_id: str, conversation_id: str, *,
                       expected_through_index: int, summary: str,
                       through_index: int, usage_increments: dict | None = None,
                       title: str | None = None) -> bool:
        """Store a new context summary only if nobody else advanced it.

        `title` (optional) refreshes an automatic title in the same write; a
        user-chosen title is kept.

        Returns False (without writing) when the stored
        summarized_through_message_index differs from expected.
        """
        raise NotImplementedError

    def add_usage(self, user_id: str, conversation_id: str, increments: dict) -> None:
        raise NotImplementedError

    def set_conversation_fields(self, user_id: str, conversation_id: str, fields: dict) -> None:
        """Plain field update (no-op if the conversation no longer exists)."""
        raise NotImplementedError

    def set_message_fields(self, user_id: str, conversation_id: str, message_index: int,
                           fields: dict) -> None:
        """Plain field update on one message (e.g. its info-button note)."""
        raise NotImplementedError

    # ---- daily question allowance ---------------------------------------
    def reserve_daily_message(self, user_id: str, day: str, limit: int, count: int = 1) -> int | None:
        """Atomically use `count` questions of `day`. Returns the new total, or
        None (and changes nothing) when that would go over `limit`."""
        raise NotImplementedError

    def refund_daily_message(self, user_id: str, day: str, count: int = 1) -> None:
        """Give back reserved questions (no-op on another day)."""
        raise NotImplementedError

    def get_daily_usage(self, user_id: str, day: str) -> int:
        raise NotImplementedError

    # ---- user long-term memory -----------------------------------------
    def get_user_memory(self, user_id: str) -> dict | None:
        raise NotImplementedError

    def set_user_memory(self, user_id: str, memory: dict) -> None:
        raise NotImplementedError

    def delete_user_memory(self, user_id: str) -> None:
        raise NotImplementedError

    # ---- developer tooling ---------------------------------------------------
    def iter_conversation_usage(self, limit: int = 500) -> list[dict]:
        """Conversation metadata + usage counters across all users (no content)."""
        raise NotImplementedError


def keeps_user_title(conv: dict, key: str, updates: dict) -> bool:
    """True if `key` must be skipped: never auto-replace a title the user chose."""
    return (key in ("title", "title_source") and conv.get("title_source") == "user"
            and updates.get("title_source") != "user")


class UnavailableRepository(Repository):
    """Used in production when Firebase credentials are missing."""

    backend = "unavailable"

    def __getattribute__(self, name):
        if name in ("backend", "__class__", "__dict__"):
            return object.__getattribute__(self, name)
        attr = object.__getattribute__(self, name)
        if callable(attr) and not name.startswith("__"):
            def _raise(*_args, **_kwargs):
                raise DataUnavailable("storage is not configured")
            return _raise
        return attr
