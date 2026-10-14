"""Cloud Firestore repository (Firebase Admin SDK).

The Admin SDK is initialised lazily on first use, inside the Gunicorn
worker, so gRPC never starts before a fork. Browser code never talks to
Firestore directly: Browser → Flask → Admin SDK → Firestore.
"""

from __future__ import annotations

import functools
import logging
import threading

from .base import (
    EMPTY_USAGE,
    SCHEMA_VERSION,
    Conflict,
    DataError,
    DataUnavailable,
    NotFound,
    Repository,
    UsernameTaken,
    keeps_user_title,
    message_doc_id,
    message_preview,
    utcnow,
)

log = logging.getLogger("askphilo.firestore")

FIREBASE_APP_NAME = "askphilo"
# Image fields read when the data itself isn't needed.
IMAGE_META_FIELDS = ["upload_id", "part", "width", "height", "conversation_id", "message_index",
                     "created_at", "mime"]

# Fields the admin dashboard may read (no password data).
USER_PUBLIC = ['name', 'username', 'username_normalized', 'role', 'created_at', 'updated_at', 'last_login_at', 'daily_usage', 'schema_version']


def _guard(fn):
    """Translate SDK/network failures into DataUnavailable (never leak details)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 — SDK raises many types
            log.error("firestore %s failed: %s", fn.__name__, type(exc).__name__)
            raise DataUnavailable(fn.__name__) from exc

    return wrapper


class FirestoreRepository(Repository):
    backend = "firestore"

    def __init__(self, credentials_path: str):
        self._credentials_path = credentials_path
        self._client = None
        self._init_lock = threading.Lock()

    # ---- client --------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            with self._init_lock:
                if self._client is None:
                    self._client = self._connect()
        return self._client

    def _connect(self):
        import firebase_admin
        from firebase_admin import credentials, firestore

        try:
            app = firebase_admin.get_app(FIREBASE_APP_NAME)
        except ValueError:
            cred = credentials.Certificate(self._credentials_path)
            app = firebase_admin.initialize_app(cred, name=FIREBASE_APP_NAME)
        log.info("firestore client initialised")
        return firestore.client(app=app)

    # ---- refs ------------------------------------------------------------------
    def _user_ref(self, user_id):
        return self.client.collection("users").document(user_id)

    def _conv_ref(self, user_id, conversation_id):
        return self._user_ref(user_id).collection("conversations").document(conversation_id)

    def _messages_ref(self, user_id, conversation_id):
        return self._conv_ref(user_id, conversation_id).collection("messages")

    def _memory_ref(self, user_id):
        return self._user_ref(user_id).collection("memory").document("profile")

    # ---- users -------------------------------------------------------------------
    @_guard
    def create_user(self, *, user_id, name, username, username_normalized, password_hash, role=None):
        from google.cloud import firestore as gcf

        client = self.client
        index_ref = client.collection("username_index").document(username_normalized)
        user_ref = self._user_ref(user_id)
        now = utcnow()
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

        @gcf.transactional
        def _txn(transaction):
            if index_ref.get(transaction=transaction).exists:
                raise UsernameTaken(username_normalized)
            transaction.create(index_ref, {"user_id": user_id, "created_at": now})
            transaction.create(user_ref, user)

        try:
            _txn(client.transaction())
        except UsernameTaken:
            raise
        except Exception as exc:
            # A concurrent signup that won the race surfaces as AlreadyExists.
            if type(exc).__name__ in ("AlreadyExists", "Conflict"):
                raise UsernameTaken(username_normalized) from exc
            raise
        return {"id": user_id, **user}

    @_guard
    def get_user(self, user_id):
        snap = self._user_ref(user_id).get(timeout=15)
        return {"id": snap.id, **snap.to_dict()} if snap.exists else None

    @_guard
    def get_user_by_username(self, username_normalized):
        snap = self.client.collection("username_index").document(username_normalized).get(timeout=15)
        if not snap.exists:
            return None
        return self.get_user(snap.get("user_id"))

    @_guard
    def record_login(self, user_id):
        now = utcnow()
        self._user_ref(user_id).update({"last_login_at": now, "updated_at": now})

    @_guard
    def set_user_fields(self, user_id, fields):
        self._user_ref(user_id).update(dict(fields))

    @_guard
    def list_users(self, limit=500):
        from google.cloud import firestore as gcf

        query = (self.client.collection("users").select(list(USER_PUBLIC))
                 .order_by("created_at", direction=gcf.Query.DESCENDING).limit(limit))
        return [{"id": s.id, **{k: s.to_dict().get(k) for k in USER_PUBLIC}}
                for s in query.stream(timeout=30)]

    # ---- service-wide daily usage ------------------------------------------------------
    @_guard
    def record_usage_day(self, day, increments, total_key="all"):
        from google.cloud import firestore as gcf

        updates = {k: gcf.Increment(v) for k, v in (increments or {}).items() if v}
        if updates:
            batch = self.client.batch()  # the day and the running total move together
            batch.set(self.client.collection("usage_daily").document(day), updates, merge=True)
            batch.set(self.client.collection("usage_totals").document(total_key), updates, merge=True)
            batch.commit(timeout=20)

    @_guard
    def get_usage_total(self, key="all"):
        snap = self.client.collection("usage_totals").document(key).get(timeout=20)
        return snap.to_dict() or {} if snap.exists else {}

    # ---- image attachments (one ≤ ~1 MB base64 document per part) --------------------
    def _images(self, user_id):
        return self._user_ref(user_id).collection("images")

    @_guard
    def put_image(self, user_id, image_id, doc):
        self._images(user_id).document(image_id).set(doc, timeout=30)

    @_guard
    def get_images(self, user_id, image_ids, *, with_data=True):
        if not image_ids:
            return {}
        refs = [self._images(user_id).document(i) for i in image_ids]
        kwargs = {} if with_data else {"field_paths": IMAGE_META_FIELDS}
        snaps = self.client.get_all(refs, timeout=30, **kwargs)
        return {s.id: s.to_dict() for s in snaps if s.exists}

    @_guard
    def attach_images(self, user_id, image_ids, conversation_id, message_index):
        batch = self.client.batch()
        for image_id in image_ids:
            batch.update(self._images(user_id).document(image_id),
                         {"conversation_id": conversation_id, "message_index": message_index})
        batch.commit(timeout=30)

    @_guard
    def delete_images(self, user_id, image_ids):
        for start in range(0, len(image_ids), 400):
            batch = self.client.batch()
            for image_id in image_ids[start:start + 400]:
                batch.delete(self._images(user_id).document(image_id))
            batch.commit(timeout=30)

    @_guard
    def find_images(self, user_id, conversation_id):
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = (self._images(user_id)
                 .where(filter=FieldFilter("conversation_id", "==", conversation_id))
                 .select(["created_at"]))  # never download the image data here
        return {s.id: (s.to_dict() or {}).get("created_at") for s in query.stream(timeout=30)}

    # ---- billing settings (admin) ----------------------------------------------------
    @_guard
    def get_billing_settings(self):
        snap = self.client.collection("settings").document("billing").get(timeout=20)
        return snap.to_dict() or {} if snap.exists else {}

    @_guard
    def set_billing_settings(self, data):
        self.client.collection("settings").document("billing").set(
            {**data, "updated_at": utcnow()}, merge=True)

    @_guard
    def get_usage_days(self, days):
        collection = self.client.collection("usage_daily")
        snaps = self.client.get_all([collection.document(d) for d in days], timeout=20)
        rows = [{"day": s.id, **s.to_dict()} for s in snaps if s.exists]
        return sorted(rows, key=lambda r: r["day"], reverse=True)

    # ---- conversations -------------------------------------------------------------
    @_guard
    def create_conversation(self, user_id, conversation_id, data):
        doc = dict(data)
        doc.setdefault("usage", dict(EMPTY_USAGE))
        self._conv_ref(user_id, conversation_id).create(doc)
        return {"id": conversation_id, **doc}

    @_guard
    def get_conversation(self, user_id, conversation_id):
        snap = self._conv_ref(user_id, conversation_id).get(timeout=15)
        return {"id": snap.id, **snap.to_dict()} if snap.exists else None

    @_guard
    def list_conversations(self, user_id, limit=20, before=None):
        from google.cloud import firestore as gcf
        from google.cloud.firestore_v1.base_query import FieldFilter

        # Trashed conversations are skipped here rather than filtered in the
        # query, which would need a composite index; read in pages until enough.
        col = self._user_ref(user_id).collection("conversations")
        out, cursor, page = [], before, max(limit * 2, 10)
        while len(out) < limit:
            query = col
            if cursor is not None:
                query = query.where(filter=FieldFilter("updated_at", "<", cursor))
            snaps = list(query.order_by("updated_at", direction=gcf.Query.DESCENDING)
                         .limit(page).stream(timeout=20))
            for s in snaps:
                doc = s.to_dict()
                if not doc.get("deleted_at"):
                    out.append({"id": s.id, **doc})
            if len(snaps) < page:
                break
            cursor = snaps[-1].to_dict()["updated_at"]
        return out[:limit]

    @_guard
    def list_trash(self, user_id, limit=100):
        from google.cloud import firestore as gcf

        # order_by skips documents without the field: only trashed ones have it.
        query = (self._user_ref(user_id).collection("conversations")
                 .order_by("deleted_at", direction=gcf.Query.DESCENDING).limit(limit))
        return [{"id": s.id, **s.to_dict()} for s in query.stream(timeout=20)]

    @_guard
    def trash_conversation(self, user_id, conversation_id, when):
        self._conv_ref(user_id, conversation_id).update({"deleted_at": when}, timeout=15)

    @_guard
    def restore_conversation(self, user_id, conversation_id):
        from google.cloud import firestore as gcf

        self._conv_ref(user_id, conversation_id).update({"deleted_at": gcf.DELETE_FIELD}, timeout=15)

    @_guard
    def delete_conversation(self, user_id, conversation_id):
        ref = self._conv_ref(user_id, conversation_id)
        if not ref.get(timeout=15).exists:
            return False
        # Deletes the messages subcollection and then the document itself.
        self.client.recursive_delete(ref)
        self.delete_images(user_id, list(self.find_images(user_id, conversation_id)))
        return True

    # ---- messages ------------------------------------------------------------------
    @_guard
    def append_messages(self, user_id, conversation_id, messages, *,
                        conversation_updates=None, usage_increments=None, expect_count=None):
        from google.cloud import firestore as gcf

        conv_ref = self._conv_ref(user_id, conversation_id)
        msgs_ref = self._messages_ref(user_id, conversation_id)
        now = utcnow()

        @gcf.transactional
        def _txn(transaction):
            snap = conv_ref.get(transaction=transaction)
            if not snap.exists:
                raise NotFound(conversation_id)
            conv = snap.to_dict()
            index = conv.get("message_count", 0)
            if expect_count is not None and index != expect_count:
                raise Conflict(conversation_id)
            stored = []
            for msg in messages:
                doc = dict(msg)
                doc["message_index"] = index
                doc.setdefault("created_at", now)
                transaction.create(msgs_ref.document(message_doc_id(index)), doc)
                stored.append({"id": message_doc_id(index), **doc})
                index += 1
            last = messages[-1]
            updates = {
                "message_count": index,
                "updated_at": now,
                "last_message_preview": message_preview(last),
                "last_message_role": last["role"],
            }
            requested = conversation_updates or {}
            for key, value in requested.items():
                if keeps_user_title(conv, key, requested):
                    continue
                updates[key] = value
            for key, value in (usage_increments or {}).items():
                if value:
                    updates[f"usage.{key}"] = gcf.Increment(value)
            transaction.update(conv_ref, updates)
            return stored

        return _txn(self.client.transaction())

    @_guard
    def get_messages(self, user_id, conversation_id, *, min_index=0, before_index=None, limit=None):
        from google.cloud import firestore as gcf
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._messages_ref(user_id, conversation_id)
        if min_index > 0:
            query = query.where(filter=FieldFilter("message_index", ">=", min_index))
        if before_index is not None:
            query = query.where(filter=FieldFilter("message_index", "<", before_index))
        if limit is not None:
            if limit <= 0:
                return []
            query = query.order_by("message_index", direction=gcf.Query.DESCENDING).limit(limit)
            items = [{"id": s.id, **s.to_dict()} for s in query.stream(timeout=20)]
            items.reverse()
            return items
        query = query.order_by("message_index")
        return [{"id": s.id, **s.to_dict()} for s in query.stream(timeout=20)]

    # ---- summary ---------------------------------------------------------------------
    @_guard
    def update_summary(self, user_id, conversation_id, *, expected_through_index,
                       summary, through_index, usage_increments=None, title=None):
        from google.cloud import firestore as gcf

        conv_ref = self._conv_ref(user_id, conversation_id)

        @gcf.transactional
        def _txn(transaction):
            snap = conv_ref.get(transaction=transaction)
            if not snap.exists:
                return False
            conv = snap.to_dict()
            if conv.get("summarized_through_message_index", -1) != expected_through_index:
                return False
            updates = {
                "context_summary": summary,
                "summarized_through_message_index": through_index,
                "summary_version": conv.get("summary_version", 0) + 1,
                "context_summary_updated_at": utcnow(),
            }
            if title and conv.get("title_source") != "user":
                updates["title"], updates["title_source"] = title, "ai"
            for key, value in (usage_increments or {}).items():
                if value:
                    updates[f"usage.{key}"] = gcf.Increment(value)
            transaction.update(conv_ref, updates)
            return True

        return _txn(self.client.transaction())

    @_guard
    def add_usage(self, user_id, conversation_id, increments):
        from google.cloud import firestore as gcf

        updates = {f"usage.{k}": gcf.Increment(v) for k, v in (increments or {}).items() if v}
        if updates:
            try:
                self._conv_ref(user_id, conversation_id).update(updates)
            except Exception as exc:
                if type(exc).__name__ == "NotFound":
                    return  # conversation deleted meanwhile
                raise

    @_guard
    def set_message_fields(self, user_id, conversation_id, message_index, fields):
        self._conv_ref(user_id, conversation_id).collection("messages").document(
            message_doc_id(message_index)).update(dict(fields), timeout=15)

    @_guard
    def set_conversation_fields(self, user_id, conversation_id, fields):
        try:
            self._conv_ref(user_id, conversation_id).update(dict(fields))
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return
            raise

    # ---- daily question allowance ------------------------------------------------------
    @_guard
    def reserve_daily_message(self, user_id, day, limit, count=1):
        from google.cloud import firestore as gcf

        user_ref = self._user_ref(user_id)

        @gcf.transactional
        def _txn(transaction):
            snap = user_ref.get(transaction=transaction)
            if not snap.exists:
                return None
            usage = (snap.to_dict() or {}).get("daily_usage") or {}
            used = usage.get("count", 0) if usage.get("date") == day else 0
            if used + count > limit:
                return None
            transaction.update(user_ref, {"daily_usage": {"date": day, "count": used + count}})
            return used + count

        return _txn(self.client.transaction())

    @_guard
    def refund_daily_message(self, user_id, day, count=1):
        from google.cloud import firestore as gcf

        user_ref = self._user_ref(user_id)

        @gcf.transactional
        def _txn(transaction):
            snap = user_ref.get(transaction=transaction)
            usage = ((snap.to_dict() or {}) if snap.exists else {}).get("daily_usage") or {}
            if usage.get("date") == day and usage.get("count", 0) > 0:
                transaction.update(user_ref, {"daily_usage": {
                    "date": day, "count": max(0, usage["count"] - count)}})

        _txn(self.client.transaction())

    @_guard
    def get_daily_usage(self, user_id, day):
        snap = self._user_ref(user_id).get(field_paths=["daily_usage"], timeout=15)
        usage = ((snap.to_dict() or {}) if snap.exists else {}).get("daily_usage") or {}
        return usage.get("count", 0) if usage.get("date") == day else 0

    # ---- user memory -----------------------------------------------------------------
    @_guard
    def get_user_memory(self, user_id):
        snap = self._memory_ref(user_id).get(timeout=15)
        return snap.to_dict() if snap.exists else None

    @_guard
    def set_user_memory(self, user_id, memory):
        doc = dict(memory)
        doc["updated_at"] = utcnow()
        doc.setdefault("schema_version", SCHEMA_VERSION)
        self._memory_ref(user_id).set(doc)

    @_guard
    def delete_user_memory(self, user_id):
        self._memory_ref(user_id).delete()

    # ---- developer tooling -------------------------------------------------------------
    @_guard
    def iter_conversation_usage(self, limit=500):
        fields = [
            "philosopher_id",
            "controller_version",
            "message_count",
            "summarized_through_message_index",
            "summary_version",
            "usage",
            "updated_at",
        ]
        query = self.client.collection_group("conversations").select(fields).limit(limit)
        rows = []
        for snap in query.stream(timeout=30):
            doc = snap.to_dict()
            rows.append(
                {
                    "user_id": snap.reference.parent.parent.id,
                    "conversation_id": snap.id,
                    "philosopher_id": doc.get("philosopher_id"),
                    "controller_version": doc.get("controller_version"),
                    "message_count": doc.get("message_count", 0),
                    "summarized_through_message_index": doc.get("summarized_through_message_index", -1),
                    "summary_version": doc.get("summary_version", 0),
                    "usage": {**EMPTY_USAGE, **(doc.get("usage") or {})},
                    "updated_at": doc.get("updated_at"),
                }
            )
        return rows
