"""Session helpers: login state lives in Flask's signed HttpOnly cookie."""

from __future__ import annotations

import functools
import secrets

from flask import g, jsonify, redirect, request, session, url_for

SESSION_KEYS = ("uid", "name", "username")


def login_user(user: dict) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["name"] = user["name"]
    session["username"] = user["username"]
    session["csrf_token"] = secrets.token_urlsafe(32)


def logout_user() -> None:
    session.clear()


def current_user() -> dict | None:
    if "uid" not in session:
        return None
    return {k: session.get(k) for k in SESSION_KEYS}


def wants_json() -> bool:
    return request.path.startswith("/api/")


def safe_next(target: str | None) -> str | None:
    """Only allow same-site relative paths as post-login redirects."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if wants_json():
                return jsonify(error="unauthorized", message="다시 로그인해 주세요."), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        g.user = user
        return view(*args, **kwargs)

    return wrapper


def client_ip() -> str:
    return request.remote_addr or "unknown"
