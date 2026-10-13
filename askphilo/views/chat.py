from __future__ import annotations

from flask import Blueprint, g, render_template

from ..auth import login_required
from ..data import get_repository

bp = Blueprint("chat", __name__)

HISTORY_PAGE_SIZE = 50


@bp.get("/new")
@login_required
def new():
    return render_template("soon.html", title="새 상담")


@bp.get("/history")
@login_required
def history():
    conversations = get_repository().list_conversations(g.user["uid"], limit=HISTORY_PAGE_SIZE)
    return render_template("history.html", conversations=conversations)


@bp.get("/c/<conversation_id>")
@login_required
def conversation(conversation_id):
    return render_template("soon.html", title="상담")
