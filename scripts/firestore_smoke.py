"""Manual Firestore integration check (NOT part of pytest).

Creates a throw-away user `zz_smoke_<random>`, exercises every repository
operation against the real Firestore project, then deletes everything it
created. Run from the repo root:

    python scripts/firestore_smoke.py
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from askphilo.config import resolve_credentials_path  # noqa: E402
from askphilo.data.base import Conflict, UsernameTaken, utcnow  # noqa: E402
from askphilo.data.firestore_repo import FirestoreRepository  # noqa: E402


def main() -> int:
    creds = resolve_credentials_path(os.environ.get("FIREBASE_CREDENTIALS_PATH", ""))
    if not creds or not creds.is_file():
        print("FIREBASE_CREDENTIALS_PATH is not set to an existing file")
        return 2
    repo = FirestoreRepository(str(creds))
    username = f"zz_smoke_{secrets.token_hex(3)}"
    user_id = str(uuid.uuid4())
    conv_id = str(uuid.uuid4())
    steps = []

    def step(name, fn):
        t = time.perf_counter()
        result = fn()
        steps.append((name, round((time.perf_counter() - t) * 1000)))
        return result

    try:
        step("create_user", lambda: repo.create_user(
            user_id=user_id, name="스모크", username=username,
            username_normalized=username, password_hash="not-a-real-hash"))
        try:
            repo.create_user(user_id=str(uuid.uuid4()), name="dup", username=username,
                             username_normalized=username, password_hash="x")
            raise AssertionError("duplicate username was accepted")
        except UsernameTaken:
            steps.append(("duplicate_rejected", 0))
        user = step("get_user_by_username", lambda: repo.get_user_by_username(username))
        assert user and user["id"] == user_id
        step("record_login", lambda: repo.record_login(user_id))

        now = utcnow()
        step("create_conversation", lambda: repo.create_conversation(user_id, conv_id, {
            "philosopher_id": "plato", "philosopher_name": "플라톤", "title": "",
            "controller_version": "v1", "controller_enabled": True, "schema_version": 1,
            "message_count": 0, "created_at": now, "updated_at": now,
            "last_message_preview": "", "context_summary": "", "summary_version": 0,
            "summarized_through_message_index": -1, "context_summary_updated_at": None,
        }))
        stored = step("append_messages", lambda: repo.append_messages(
            user_id, conv_id,
            [{"role": "user", "content": "스모크 테스트 메시지"},
             {"role": "assistant", "content": "응답"}],
            conversation_updates={"title": "스모크 테스트"},
            usage_increments={"mock_calls": 1}))
        assert [m["message_index"] for m in stored] == [0, 1]
        try:
            repo.append_messages(user_id, conv_id, [{"role": "assistant", "content": "x"}], expect_count=1)
            raise AssertionError("expect_count mismatch was accepted")
        except Conflict:
            steps.append(("expect_count_conflict", 0))
        msgs = step("get_messages(limit)", lambda: repo.get_messages(user_id, conv_id, limit=1))
        assert [m["message_index"] for m in msgs] == [1]
        ok = step("update_summary", lambda: repo.update_summary(
            user_id, conv_id, expected_through_index=-1, summary="요약", through_index=0))
        assert ok is True
        stale = repo.update_summary(user_id, conv_id, expected_through_index=-1, summary="x", through_index=0)
        assert stale is False
        conv = step("get_conversation", lambda: repo.get_conversation(user_id, conv_id))
        assert conv["message_count"] == 2 and conv["title"] == "스모크 테스트"
        assert conv["usage"]["mock_calls"] == 1 and conv["summary_version"] == 1
        listed = step("list_conversations", lambda: repo.list_conversations(user_id, limit=5))
        assert listed and listed[0]["id"] == conv_id
        step("set_user_memory", lambda: repo.set_user_memory(user_id, {"preferences": ["짧은 답변"]}))
        assert repo.get_user_memory(user_id)["preferences"] == ["짧은 답변"]
        step("delete_user_memory", lambda: repo.delete_user_memory(user_id))
        assert repo.get_user_memory(user_id) is None
        deleted = step("delete_conversation", lambda: repo.delete_conversation(user_id, conv_id))
        assert deleted and repo.get_conversation(user_id, conv_id) is None
        assert repo.get_messages(user_id, conv_id) == []
        print("ALL CHECKS PASSED")
        return_code = 0
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", type(exc).__name__, exc)
        return_code = 1
    finally:
        # Clean up everything this script created.
        client = repo.client
        client.recursive_delete(client.collection("users").document(user_id))
        client.collection("username_index").document(username).delete()
        leftover = client.collection("users").document(user_id).get().exists or \
            client.collection("username_index").document(username).get().exists
        print("cleanup:", "LEFTOVER DATA!" if leftover else "ok (no test data left)")
    for name, ms in steps:
        print(f"  {name:<24} {ms:>5} ms")
    return return_code


if __name__ == "__main__":
    sys.exit(main())
