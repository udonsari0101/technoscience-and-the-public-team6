from __future__ import annotations

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from ..auth import current_user, login_required
from ..data import get_repository
from ..services.preferences import REPLY_SPEEDS, reply_speed
from ..services.quota import daily_status, today_kst

bp = Blueprint("pages", __name__)

HOME_RECENT_LIMIT = 5


@bp.get("/home")
@login_required
def home():
    repo = get_repository()
    conversations = repo.list_conversations(g.user["uid"], limit=HOME_RECENT_LIMIT)
    return render_template("home.html", conversations=conversations, daily=_daily(repo))


def _daily(repo) -> dict:
    used = repo.get_daily_usage(g.user["uid"], today_kst())
    return daily_status(used, current_app.config["CHAT_MESSAGES_PER_DAY"])


@bp.get("/settings")
@login_required
def settings():
    repo = get_repository()
    memory = repo.get_user_memory(g.user["uid"]) or {}
    return render_template("settings.html", memory=memory, daily=_daily(repo),
                           speeds=REPLY_SPEEDS, reply_speed=reply_speed(repo.get_user(g.user["uid"])))


@bp.post("/settings/reply-speed")
@login_required
def set_reply_speed():
    """빠름/보통/느림: the pause between a reply's bubbles (saved on the account)."""
    wants_json = request.is_json
    body = request.get_json(silent=True) if wants_json else request.form
    value = body.get("speed") if isinstance(body, dict) else None
    if not isinstance(value, str) or value not in REPLY_SPEEDS:
        if wants_json:
            return jsonify(ok=False, error="bad_speed", message="속도를 다시 골라 주세요."), 400
        flash("속도를 다시 골라 주세요.", "error")
        return redirect(url_for("pages.settings") + "#speed")
    get_repository().set_user_fields(g.user["uid"], {"reply_speed": value})
    if wants_json:
        return jsonify(ok=True, speed=value)
    flash("답변 속도를 바꿨어요.", "success")
    return redirect(url_for("pages.settings") + "#speed")


@bp.post("/settings/memory/clear")
@login_required
def clear_memory():
    """Forget the cross-conversation memory (values/preferences)."""
    get_repository().delete_user_memory(g.user["uid"])
    flash("상담 기억을 지웠어요.", "success")
    return redirect(url_for("pages.settings"))


def landing_redirect():
    user = current_user()
    if user:
        if user.get("role") == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("pages.home"))
    return None
