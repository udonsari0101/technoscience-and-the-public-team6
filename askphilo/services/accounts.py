"""Signup / login rules. Passwords are only ever stored as hashes."""

from __future__ import annotations

import re
import unicodedata
import uuid

from ..data import Repository, UsernameTaken
from .passwords import store_password, verify_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
NAME_MAX = 30
PASSWORD_MIN = 8
PASSWORD_MAX = 128

# Used to spend the same hashing time when the username does not exist,
# so response timing does not reveal which usernames are registered.
_DUMMY_HASH = store_password("askphilo-timing-equaliser")

MSG = {
    "name_required": "이름을 입력해 주세요.",
    "name_long": f"이름은 {NAME_MAX}자 이하로 입력해 주세요.",
    "name_invalid": "이름에 쓸 수 없는 문자가 있어요.",
    "username_invalid": "아이디는 3~20자의 영문, 숫자, 밑줄(_)만 쓸 수 있어요.",
    "username_taken": "이미 사용 중인 아이디예요.",
    "password_short": f"비밀번호는 {PASSWORD_MIN}자 이상으로 입력해 주세요.",
    "password_long": f"비밀번호는 {PASSWORD_MAX}자 이하로 입력해 주세요.",
    "password_mismatch": "비밀번호가 일치하지 않아요.",
    "login_failed": "아이디 또는 비밀번호가 맞지 않아요.",
}


class SignupError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("invalid signup")
        self.errors = errors


def is_valid_username(username: str) -> bool:
    """3–20 of [A-Za-z0-9_], and not Firestore-reserved (`__name__` ids)."""
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        return False
    return not (name.startswith("__") and name.endswith("__"))


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def clean_name(name: str) -> str:
    return " ".join((name or "").split())


def validate_signup(name: str, username: str, password: str, password_confirm: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    name = clean_name(name)
    if not name:
        errors["name"] = MSG["name_required"]
    elif len(name) > NAME_MAX:
        errors["name"] = MSG["name_long"]
    elif any(unicodedata.category(ch).startswith("C") for ch in name):
        errors["name"] = MSG["name_invalid"]

    if not is_valid_username(username):
        errors["username"] = MSG["username_invalid"]

    password = password or ""
    if len(password) < PASSWORD_MIN:
        errors["password"] = MSG["password_short"]
    elif len(password) > PASSWORD_MAX:
        errors["password"] = MSG["password_long"]

    if "password" not in errors and password != (password_confirm or ""):
        errors["password_confirm"] = MSG["password_mismatch"]
    return errors


def signup(repo: Repository, *, name: str, username: str, password: str, password_confirm: str) -> dict:
    errors = validate_signup(name, username, password, password_confirm)
    if errors:
        raise SignupError(errors)
    username = username.strip()
    try:
        return repo.create_user(
            user_id=str(uuid.uuid4()),
            name=clean_name(name),
            username=username,
            username_normalized=normalize_username(username),
            password_hash=store_password(password),
        )
    except UsernameTaken:
        raise SignupError({"username": MSG["username_taken"]}) from None


def authenticate(repo: Repository, *, username: str, password: str) -> dict | None:
    normalized = normalize_username(username)
    password = password or ""
    if not is_valid_username(normalized) or not password or len(password) > PASSWORD_MAX:
        verify_password(_DUMMY_HASH, password[:PASSWORD_MAX])
        return None
    user = repo.get_user_by_username(normalized)
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.get("password_hash", ""), password):
        return None
    return user
