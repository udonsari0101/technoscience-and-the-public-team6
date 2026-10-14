"""Delete ALL users (with their conversations, messages and memory) from Firestore.

    python scripts/wipe_users.py           # dry run: counts only
    python scripts/wipe_users.py --yes     # delete

Removes users/* (recursively) and username_index/*. Service-wide usage
statistics (usage_daily) are kept. This cannot be undone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from askphilo.config import resolve_credentials_path  # noqa: E402


def main(argv: list[str]) -> int:
    from askphilo.data.firestore_repo import FirestoreRepository

    creds = resolve_credentials_path(os.environ.get("FIREBASE_CREDENTIALS_PATH", ""))
    if not creds or not creds.is_file():
        print("FIREBASE_CREDENTIALS_PATH is not set to an existing file")
        return 2
    client = FirestoreRepository(str(creds)).client
    users = list(client.collection("users").select(["username"]).stream())
    index = list(client.collection("username_index").select([]).stream())
    print(f"users: {len(users)} · username_index: {len(index)}")
    for snap in users:
        print("  -", (snap.to_dict() or {}).get("username"))
    if "--yes" not in argv:
        print("dry run: add --yes to delete everything listed above")
        return 0
    for snap in users:
        client.recursive_delete(snap.reference)
    for snap in index:
        snap.reference.delete()
    left = len(list(client.collection("users").limit(1).stream())) + \
        len(list(client.collection("username_index").limit(1).stream()))
    print("deleted." if not left else "WARNING: some documents remain")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
