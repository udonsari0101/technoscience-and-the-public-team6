"""Create (or promote) the admin account in Firestore.

    ADMIN_PASSWORD=... python scripts/create_admin.py [--username admin] [--name 관리자]

The password is read from ADMIN_PASSWORD or asked interactively; it is never
written to the repository. An existing account with that username is
promoted to admin and its password replaced.
"""

from __future__ import annotations

import getpass
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from askphilo.config import resolve_credentials_path  # noqa: E402
from askphilo.data.base import Repository  # noqa: E402
from askphilo.services import accounts  # noqa: E402
from askphilo.services.passwords import store_password  # noqa: E402


def create_admin(repo: Repository, username: str, password: str, name: str = "관리자") -> dict:
    if not accounts.is_valid_username(username):
        raise SystemExit("invalid username (3-20 letters, digits, _)")
    if len(password) < accounts.PASSWORD_MIN:
        raise SystemExit(f"password must be at least {accounts.PASSWORD_MIN} characters")
    normalized = accounts.normalize_username(username)
    existing = repo.get_user_by_username(normalized)
    if existing:
        repo.set_user_fields(existing["id"], {"role": "admin", "password_hash": store_password(password)})
        return {**existing, "role": "admin", "created": False}
    user = repo.create_user(user_id=str(uuid.uuid4()), name=name, username=username,
                            username_normalized=normalized, password_hash=store_password(password),
                            role="admin")
    return {**user, "created": True}


def main(argv: list[str]) -> int:
    username = argv[argv.index("--username") + 1] if "--username" in argv else "admin"
    name = argv[argv.index("--name") + 1] if "--name" in argv else "관리자"
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("admin password: ")
    from askphilo.data.firestore_repo import FirestoreRepository

    creds = resolve_credentials_path(os.environ.get("FIREBASE_CREDENTIALS_PATH", ""))
    if not creds or not creds.is_file():
        print("FIREBASE_CREDENTIALS_PATH is not set to an existing file")
        return 2
    user = create_admin(FirestoreRepository(str(creds)), username, password, name)
    print(("created" if user["created"] else "promoted"), "admin account:", user["username"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
