"""In-process repository for tests and Firebase-less local development.

Mirrors the Firestore semantics (transactions, ordering, optimistic
summary updates) with a single lock. Data disappears on restart.
"""

from __future__ import annotations

import copy
import threading

from .base import (
    EMPTY_USAGE,
    SCHEMA_VERSION,
    NotFound,
    Repository,
    UsernameTaken,
    message_doc_id,
    preview,
    utcnow,
)


class MemoryRepository(Repository):
    backend = "memory"

    def __init__(self):
        self._lock = threading.RLock()
        self.users: dict[str, dict] = {}
        self.username_index: dict[str, dict] = {}
        self.conversations: dict[str, dict[str, dict]] = {}
        self.messages: dict[tuple[str, str], dict[str, dict]] = {}
        self.memory: dict[str, dict] = {}

    # ---- users -----------------------------------------------------------
    def create_user(self, *, user_id, name, username, username_normalized, password_hash):
        with self._lock:
            if username_normalized in self.username_index:
                raise UsernameTaken(username_normalized)
            now = utcnow()
            self.username_index[username_normalized] = {"user_id": user_id, "created_at": now}
            user = {
                "name": name,
                "username": username,
                "username_normalized": username_normalized,
                "password_hash": password_hash,
                "schema_version": SCHEMA_VERSION,
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            }
            self.users[user_id] = user
            return {"id": user_id, **copy.deepcopy(user)}

    def get_user(self, user_id):
        with self._lock:
            user = self.users.get(user_id)
            return {"id": user_id, **copy.deepcopy(user)} if user else None

    def get_user_by_username(self, username_normalized):
        with self._lock:
            entry = self.username_index.get(username_normalized)
            return self.get_user(entry["user_id"]) if entry else None

    def record_login(self, user_id):
        with self._lock:
            if user_id in self.users:
                now = utcnow()
                self.users[user_id]["last_login_at"] = now
                self.users[user_id]["updated_at"] = now

    # ---- conversations -------------------------------------------------
    def create_conversation(self, user_id, conversation_id, data):
        with self._lock:
            convs = self.conversations.setdefault(user_id, {})
            doc = copy.deepcopy(data)
            doc.setdefault("usage", dict(EMPTY_USAGE))
            convs[conversation_id] = doc
            self.messages[(user_id, conversation_id)] = {}
            return {"id": conversation_id, **copy.deepcopy(doc)}

    def get_conversation(self, user_id, conversation_id):
        with self._lock:
            doc = self.conversations.get(user_id, {}).get(conversation_id)
            return {"id": conversation_id, **copy.deepcopy(doc)} if doc else None

    def list_conversations(self, user_id, limit=20, before=None):
        with self._lock:
            items = [
                {"id": cid, **copy.deepcopy(doc)}
                for cid, doc in self.conversations.get(user_id, {}).items()
                if before is None or doc["updated_at"] < before
            ]
        items.sort(key=lambda c: c["updated_at"], reverse=True)
        return items[:limit]

    def delete_conversation(self, user_id, conversation_id):
        with self._lock:
            existed = self.conversations.get(user_id, {}).pop(conversation_id, None)
            self.messages.pop((user_id, conversation_id), None)
            return existed is not None

    # ---- messages --------------------------------------------------------
    def append_messages(self, user_id, conversation_id, messages, *,
                        conversation_updates=None, usage_increments=None):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is None:
                raise NotFound(conversation_id)
            store = self.messages.setdefault((user_id, conversation_id), {})
            index = conv.get("message_count", 0)
            now = utcnow()
            stored = []
            for msg in messages:
                doc = copy.deepcopy(msg)
                doc["message_index"] = index
                doc.setdefault("created_at", now)
                store[message_doc_id(index)] = doc
                stored.append({"id": message_doc_id(index), **copy.deepcopy(doc)})
                index += 1
            last = messages[-1]
            conv["message_count"] = index
            conv["updated_at"] = now
            conv["last_message_preview"] = preview(last["content"])
            conv["last_message_role"] = last["role"]
            for key, value in (conversation_updates or {}).items():
                if key == "title" and conv.get("title"):
                    continue  # the title is fixed by the first user message
                conv[key] = value
            self._add_usage(conv, usage_increments)
            return stored

    def get_messages(self, user_id, conversation_id, *, min_index=0, before_index=None, limit=None):
        with self._lock:
            store = self.messages.get((user_id, conversation_id), {})
            items = [
                {"id": mid, **copy.deepcopy(doc)}
                for mid, doc in store.items()
                if doc["message_index"] >= min_index
                and (before_index is None or doc["message_index"] < before_index)
            ]
        items.sort(key=lambda m: m["message_index"])
        if limit is not None:
            items = items[-limit:] if limit > 0 else []
        return items

    # ---- summary ---------------------------------------------------------
    def update_summary(self, user_id, conversation_id, *, expected_through_index,
                       summary, through_index, usage_increments=None):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is None:
                return False
            current = conv.get("summarized_through_message_index", -1)
            if current != expected_through_index:
                return False
            conv["context_summary"] = summary
            conv["summarized_through_message_index"] = through_index
            conv["summary_version"] = conv.get("summary_version", 0) + 1
            conv["context_summary_updated_at"] = utcnow()
            self._add_usage(conv, usage_increments)
            return True

    def add_usage(self, user_id, conversation_id, increments):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is not None:
                self._add_usage(conv, increments)

    def set_conversation_fields(self, user_id, conversation_id, fields):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is not None:
                conv.update(copy.deepcopy(fields))

    @staticmethod
    def _add_usage(conv, increments):
        if not increments:
            return
        usage = conv.setdefault("usage", dict(EMPTY_USAGE))
        for key, value in increments.items():
            if value:
                usage[key] = usage.get(key, 0) + value

    # ---- user memory -----------------------------------------------------
    def get_user_memory(self, user_id):
        with self._lock:
            mem = self.memory.get(user_id)
            return copy.deepcopy(mem) if mem else None

    def set_user_memory(self, user_id, memory):
        with self._lock:
            doc = copy.deepcopy(memory)
            doc["updated_at"] = utcnow()
            doc.setdefault("schema_version", SCHEMA_VERSION)
            self.memory[user_id] = doc

    # ---- developer tooling -------------------------------------------------
    def iter_conversation_usage(self, limit=500):
        out = []
        with self._lock:
            for uid, convs in self.conversations.items():
                for cid, doc in convs.items():
                    out.append(_usage_row(uid, cid, doc))
        return out[:limit]


def _usage_row(user_id, conversation_id, doc):
    return {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "philosopher_id": doc.get("philosopher_id"),
        "controller_version": doc.get("controller_version"),
        "message_count": doc.get("message_count", 0),
        "summarized_through_message_index": doc.get("summarized_through_message_index", -1),
        "summary_version": doc.get("summary_version", 0),
        "usage": {**EMPTY_USAGE, **(doc.get("usage") or {})},
        "updated_at": doc.get("updated_at"),
    }
