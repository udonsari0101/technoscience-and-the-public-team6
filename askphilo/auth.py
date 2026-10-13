"""Session helpers: login state lives in Flask's signed HttpOnly cookie."""

from __future__ import annotations

import functools
import ipaddress
import secrets
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, redirect, request, session, url_for

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
    """Only allow same-site relative paths as post-login redirects.

    Rejects control characters/whitespace too: urlsplit() strips TAB/CR/LF,
    so "/<TAB>/evil.com" would otherwise become the protocol-relative "//evil.com".
    """
    if not target or len(target) > 512:
        return None
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in target) or "\\" in target:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
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


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def client_ip() -> str:
    """Client address for rate limiting.

    On Render every request passes Cloudflare, which overwrites
    True-Client-IP / CF-Connecting-IP with the real client address; off
    Render these headers are client-controlled and therefore ignored.
    """
    if current_app.config.get("ON_RENDER"):
        for header in ("True-Client-IP", "CF-Connecting-IP"):
            ip = _valid_ip(request.headers.get(header))
            if ip:
                return ip
    return request.remote_addr or "unknown"
