"""In-process repository for tests and Firebase-less local development.

Mirrors the Firestore semantics (transactions, ordering, optimistic
summary updates) with a single lock. Data disappears on restart.
"""

from __future__ import annotations

import copy
import threading
from datetime import timezone

from .base import (
    EMPTY_USAGE,
    SCHEMA_VERSION,
    Conflict,
    NotFound,
    Repository,
    UsernameTaken,
    keeps_user_title,
    message_doc_id,
    message_preview,
    utcnow,
)


# Fields the admin dashboard may read (no password data).
USER_PUBLIC = ('name', 'username', 'username_normalized', 'role', 'created_at', 'updated_at', 'last_login_at', 'daily_usage', 'schema_version')


class MemoryRepository(Repository):
    backend = "memory"

    def __init__(self):
        self._lock = threading.RLock()
        self.users: dict[str, dict] = {}
        self.username_index: dict[str, dict] = {}
        self.conversations: dict[str, dict[str, dict]] = {}
        self.messages: dict[tuple[str, str], dict[str, dict]] = {}
        self.memory: dict[str, dict] = {}
        self.usage_days: dict[str, dict] = {}
        self.usage_totals: dict[str, dict] = {}
        self.user_images: dict[str, dict[str, dict]] = {}
        self.billing: dict = {}

    # ---- users -----------------------------------------------------------
    def create_user(self, *, user_id, name, username, username_normalized, password_hash, role=None):
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
                "role": role,
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

    def set_user_fields(self, user_id, fields):
        with self._lock:
            if user_id in self.users:
                self.users[user_id].update(copy.deepcopy(fields))

    def list_users(self, limit=500):
        with self._lock:
            rows = [{"id": uid, **{k: copy.deepcopy(u.get(k)) for k in USER_PUBLIC}}
                    for uid, u in self.users.items()]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    def record_usage_day(self, day, increments, total_key="all"):
        with self._lock:
            for row in (self.usage_days.setdefault(day, {}), self.usage_totals.setdefault(total_key, {})):
                for key, value in (increments or {}).items():
                    if value:
                        row[key] = row.get(key, 0) + value

    def get_usage_total(self, key="all"):
        with self._lock:
            return copy.deepcopy(self.usage_totals.get(key, {}))

    def get_billing_settings(self):
        with self._lock:
            return copy.deepcopy(self.billing)

    def set_billing_settings(self, data):
        with self._lock:
            self.billing.update(copy.deepcopy(data), updated_at=utcnow())

    def get_usage_days(self, days):
        with self._lock:
            found = [d for d in days if d in self.usage_days]
            return [{"day": d, **copy.deepcopy(self.usage_days[d])} for d in sorted(found, reverse=True)]

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
        if before is not None and before.tzinfo is None:
            before = before.replace(tzinfo=timezone.utc)
        with self._lock:
            items = [
                {"id": cid, **copy.deepcopy(doc)}
                for cid, doc in self.conversations.get(user_id, {}).items()
                if (before is None or doc["updated_at"] < before) and not doc.get("deleted_at")
            ]
        items.sort(key=lambda c: c["updated_at"], reverse=True)
        return items[:limit]

    def list_trash(self, user_id, limit=100):
        with self._lock:
            items = [{"id": cid, **copy.deepcopy(doc)}
                     for cid, doc in self.conversations.get(user_id, {}).items() if doc.get("deleted_at")]
        items.sort(key=lambda c: c["deleted_at"], reverse=True)
        return items[:limit]

    def trash_conversation(self, user_id, conversation_id, when):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is None:
                raise NotFound(conversation_id)
            conv["deleted_at"] = when

    def restore_conversation(self, user_id, conversation_id):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is None:
                raise NotFound(conversation_id)
            conv.pop("deleted_at", None)

    def delete_conversation(self, user_id, conversation_id):
        with self._lock:
            existed = self.conversations.get(user_id, {}).pop(conversation_id, None)
            self.messages.pop((user_id, conversation_id), None)
            images = self.user_images.get(user_id, {})
            for image_id in [i for i, d in images.items() if d.get("conversation_id") == conversation_id]:
                images.pop(image_id)
            return existed is not None

    # ---- images ----------------------------------------------------------
    def put_image(self, user_id, image_id, doc):
        with self._lock:
            self.user_images.setdefault(user_id, {})[image_id] = copy.deepcopy(doc)

    def get_images(self, user_id, image_ids, *, with_data=True):
        with self._lock:
            stored = self.user_images.get(user_id, {})
            out = {i: copy.deepcopy(stored[i]) for i in image_ids if i in stored}
        if not with_data:
            for doc in out.values():
                doc.pop("data_b64", None)
        return out

    def attach_images(self, user_id, image_ids, conversation_id, message_index):
        with self._lock:
            for image_id in image_ids:
                doc = self.user_images.get(user_id, {}).get(image_id)
                if doc is not None:
                    doc.update(conversation_id=conversation_id, message_index=message_index)

    def delete_images(self, user_id, image_ids):
        with self._lock:
            for image_id in image_ids:
                self.user_images.get(user_id, {}).pop(image_id, None)

    def find_images(self, user_id, conversation_id):
        with self._lock:
            return {i: d.get("created_at") for i, d in self.user_images.get(user_id, {}).items()
                    if d.get("conversation_id") == conversation_id}

    # ---- messages --------------------------------------------------------
    def append_messages(self, user_id, conversation_id, messages, *,
                        conversation_updates=None, usage_increments=None, expect_count=None):
        with self._lock:
            conv = self.conversations.get(user_id, {}).get(conversation_id)
            if conv is None:
                raise NotFound(conversation_id)
            store = self.messages.setdefault((user_id, conversation_id), {})
            index = conv.get("message_count", 0)
            if expect_count is not None and index != expect_count:
                raise Conflict(conversation_id)
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
            conv["last_message_preview"] = message_preview(last)
            conv["last_message_role"] = last["role"]
            updates = conversation_updates or {}
            for key, value in updates.items():
                if keeps_user_title(conv, key, updates):
                    continue
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
                       summary, through_index, usage_increments=None, title=None):
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
            if title and conv.get("title_source") != "user":
                conv["title"], conv["title_source"] = title, "ai"
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

    # ---- daily question allowance ------------------------------------------
    def reserve_daily_message(self, user_id, day, limit, count=1):
        with self._lock:
            user = self.users.get(user_id)
            if user is None:
                return None
            usage = user.get("daily_usage") or {}
            used = usage.get("count", 0) if usage.get("date") == day else 0
            if used + count > limit:
                return None
            user["daily_usage"] = {"date": day, "count": used + count}
            return used + count

    def refund_daily_message(self, user_id, day, count=1):
        with self._lock:
            user = self.users.get(user_id)
            usage = (user or {}).get("daily_usage") or {}
            if usage.get("date") == day and usage.get("count", 0) > 0:
                user["daily_usage"] = {"date": day, "count": max(0, usage["count"] - count)}

    def get_daily_usage(self, user_id, day):
        with self._lock:
            usage = (self.users.get(user_id) or {}).get("daily_usage") or {}
            return usage.get("count", 0) if usage.get("date") == day else 0

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

    def delete_user_memory(self, user_id):
        with self._lock:
            self.memory.pop(user_id, None)

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
