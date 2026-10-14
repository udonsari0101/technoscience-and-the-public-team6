from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, url_for

from ..auth import current_user, login_required
from ..data import get_repository
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
    return render_template("settings.html", memory=memory, daily=_daily(repo))


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
