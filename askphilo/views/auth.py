from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..auth import client_ip, current_user, login_user, logout_user, safe_next
from ..data import get_repository
from ..ratelimit import get_limiter
from ..services import accounts

bp = Blueprint("auth", __name__)
log = logging.getLogger("askphilo.auth")

LOGIN_WINDOW = 10 * 60
LOGIN_FAILS_PER_USER = 10
LOGIN_FAILS_PER_IP = 30
SIGNUPS_PER_IP_HOUR = 10
MSG_SLOW_DOWN = "시도가 너무 많아요. 잠시 후 다시 시도해 주세요."


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("pages.home"))
    form = {"name": "", "username": ""}
    errors: dict[str, str] = {}
    if request.method == "POST":
        form = {
            "name": request.form.get("name", ""),
            "username": request.form.get("username", ""),
        }
        limiter = get_limiter()
        if not limiter.hit(f"signup:{client_ip()}", SIGNUPS_PER_IP_HOUR, 3600):
            flash(MSG_SLOW_DOWN, "error")
            return render_template("auth/signup.html", form=form, errors=errors), 429
        try:
            user = accounts.signup(
                get_repository(),
                name=form["name"],
                username=form["username"],
                password=request.form.get("password", ""),
                password_confirm=request.form.get("password_confirm", ""),
            )
        except accounts.SignupError as exc:
            errors = exc.errors
            return render_template("auth/signup.html", form=form, errors=errors), 400
        log.info("signup ok user=%s", user["id"])
        login_user(user)
        flash(f"{user['name']}님, 반가워요!", "success")
        return redirect(url_for("pages.home"))
    return render_template("auth/signup.html", form=form, errors=errors)


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = safe_next(request.values.get("next"))
    if current_user():
        return redirect(next_url or url_for("pages.home"))
    username = ""
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        limiter = get_limiter()
        ip_key = f"login-ip:{client_ip()}"
        # Keys never contain raw input: invalid usernames share one bucket.
        normalized = accounts.normalize_username(username)
        user_key = (f"login-user:{normalized}" if accounts.is_valid_username(normalized)
                    else "login-user:<invalid>")
        if not (limiter.allowed(ip_key, LOGIN_FAILS_PER_IP, LOGIN_WINDOW)
                and limiter.allowed(user_key, LOGIN_FAILS_PER_USER, LOGIN_WINDOW)):
            error = MSG_SLOW_DOWN
            return render_template("auth/login.html", username=username[:20], error=error,
                                   next=next_url), 429
        user = accounts.authenticate(get_repository(), username=username, password=password)
        if user is None:
            limiter.record(ip_key, LOGIN_WINDOW)
            limiter.record(user_key, LOGIN_WINDOW)
            error = accounts.MSG["login_failed"]
            return render_template("auth/login.html", username=username[:20], error=error,
                                   next=next_url), 400
        limiter.reset(user_key)
        get_repository().record_login(user["id"])
        login_user(user)
        log.info("login ok user=%s", user["id"])
        return redirect(next_url or url_for("pages.home"))
    return render_template("auth/login.html", username=username, error=error, next=next_url)


@bp.post("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
