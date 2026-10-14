"""The one place where passwords are stored and checked.

store_password() turns the password typed at signup into the value saved in
users/{id}.password_hash; verify_password() checks a login attempt against
it. To use a different scheme, replace these two functions — nothing else in
the app reads or writes passwords.
"""

from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash


def store_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(stored: str, password: str) -> bool:
    return bool(stored) and check_password_hash(stored, password or "")
