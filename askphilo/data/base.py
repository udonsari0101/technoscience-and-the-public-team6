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


class Repository:
    """Abstract storage API. See firestore_repo / memory_repo."""

    backend = "abstract"

    # ---- users ---------------------------------------------------------
    def create_user(self, *, user_id: str, name: str, username: str,
                    username_normalized: str, password_hash: str) -> dict:
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

    # ---- conversations ---------------------------------------------------
    def create_conversation(self, user_id: str, conversation_id: str, data: dict) -> dict:
        raise NotImplementedError

    def get_conversation(self, user_id: str, conversation_id: str) -> dict | None:
        raise NotImplementedError

    def list_conversations(self, user_id: str, limit: int = 20,
                           before: datetime | None = None) -> list[dict]:
        """Newest first (updated_at desc)."""
        raise NotImplementedError

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        """Delete the conversation and its messages subcollection."""
        raise NotImplementedError

    # ---- messages ----------------------------------------------------------
    def append_messages(self, user_id: str, conversation_id: str,
                        messages: list[dict], *, conversation_updates: dict | None = None,
                        usage_increments: dict | None = None) -> list[dict]:
        """Transactionally assign message_index values and store messages.

        Updates message_count, updated_at, last_message_* on the
        conversation, sets `title` if it is still empty, applies
        `conversation_updates` and adds `usage_increments` to usage.*.
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
                       through_index: int, usage_increments: dict | None = None) -> bool:
        """Store a new context summary only if nobody else advanced it.

        Returns False (without writing) when the stored
        summarized_through_message_index differs from expected.
        """
        raise NotImplementedError

    def add_usage(self, user_id: str, conversation_id: str, increments: dict) -> None:
        raise NotImplementedError

    # ---- user long-term memory -----------------------------------------
    def get_user_memory(self, user_id: str) -> dict | None:
        raise NotImplementedError

    def set_user_memory(self, user_id: str, memory: dict) -> None:
        raise NotImplementedError

    # ---- developer tooling ---------------------------------------------------
    def iter_conversation_usage(self, limit: int = 500) -> list[dict]:
        """Conversation metadata + usage counters across all users (no content)."""
        raise NotImplementedError


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
