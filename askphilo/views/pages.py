from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, url_for

from ..auth import current_user, login_required
from ..data import get_repository

bp = Blueprint("pages", __name__)

HOME_RECENT_LIMIT = 5


@bp.get("/home")
@login_required
def home():
    conversations = get_repository().list_conversations(g.user["uid"], limit=HOME_RECENT_LIMIT)
    return render_template("home.html", conversations=conversations)


@bp.get("/settings")
@login_required
def settings():
    return render_template("settings.html")


def landing_redirect():
    if current_user():
        return redirect(url_for("pages.home"))
    return None
