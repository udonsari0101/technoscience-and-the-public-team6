from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .. import background
from ..ai import get_provider
from ..auth import login_required
from ..data import get_repository
from ..philosophers import PHILOSOPHERS, get_philosopher, philosopher_css
from ..ratelimit import get_limiter
from ..services.conversations import (
    ChatError,
    ChatSettings,
    ConversationService,
    limit_status,
)

bp = Blueprint("chat", __name__)
log = logging.getLogger("askphilo.chat")

HISTORY_PAGE_SIZE = 30
CHAT_PAGE_SIZE = 30
SIDEBAR_SIZE = 20
MSG_SLOW_DOWN = "메시지를 너무 빠르게 보내고 있어요. 잠시 후 다시 시도해 주세요."
MSG_DAILY_LIMIT = "오늘은 대화를 많이 나눴어요. 내일 다시 이야기해요."
MSG_BUSY = "지금은 이용자가 많아요. 잠시 후 다시 시도해 주세요."
DAY = 24 * 60 * 60


def service() -> ConversationService:
    return ConversationService(get_repository(), get_provider(),
                               ChatSettings.from_config(current_app.config))


def _schedule_summary(svc: ConversationService, user_id: str, conversation_id: str) -> None:
    """Run the (rare) context summary off the request thread."""
    app = current_app._get_current_object()
    if app.config.get("TESTING") or app.config.get("SUMMARY_INLINE"):
        svc.maybe_summarize(user_id, conversation_id)
        return
    background.submit(app, svc.maybe_summarize, user_id, conversation_id)


def _error(exc: ChatError):
    body = {"ok": False, "error": exc.code, "message": exc.message}
    body.update(exc.extra)
    return jsonify(body), exc.status


def _budget_error():
    """Per-minute and per-day caps per user, plus a site-wide hourly breaker
    on reply requests (in-process: one Gunicorn worker). None if allowed."""
    cfg = current_app.config
    limiter = get_limiter()
    uid = g.user["uid"]
    if not limiter.allowed(f"chat:{uid}", cfg["CHAT_MESSAGES_PER_MINUTE"], 60):
        return jsonify(ok=False, error="rate_limited", message=MSG_SLOW_DOWN), 429
    if not limiter.allowed(f"chat-day:{uid}", cfg["CHAT_MESSAGES_PER_DAY"], DAY):
        return jsonify(ok=False, error="daily_limit", message=MSG_DAILY_LIMIT), 429
    if not limiter.allowed("ai-global", cfg["AI_REPLIES_PER_HOUR"], 3600):
        return jsonify(ok=False, error="busy", message=MSG_BUSY), 503
    limiter.record(f"chat:{uid}", 60)
    limiter.record(f"chat-day:{uid}", DAY)
    limiter.record("ai-global", 3600)
    return None


def _payload() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@bp.get("/assets/philosophers.css")
def philosophers_stylesheet():
    response = Response(philosopher_css(), mimetype="text/css")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@bp.get("/new")
@login_required
def new():
    return render_template("new.html", philosophers=PHILOSOPHERS)


@bp.get("/philosophers/<philosopher_id>")
@login_required
def philosopher(philosopher_id):
    p = get_philosopher(philosopher_id)
    if p is None:
        abort(404)
    return render_template("philosopher.html", p=p)


@bp.get("/chat/<philosopher_id>")
@login_required
def start(philosopher_id):
    """Empty chat with a philosopher. The conversation document is created
    on the first message, so abandoned visits leave no empty conversations."""
    p = get_philosopher(philosopher_id)
    if p is None:
        abort(404)
    settings = ChatSettings.from_config(current_app.config)
    return render_template(
        "chat.html", p=p, conversation=None, messages=[], has_more=False, reply_state=None,
        status=limit_status(0, settings), sidebar=_sidebar(), max_chars=settings.max_user_chars,
    )


@bp.get("/c/<conversation_id>")
@login_required
def conversation(conversation_id):
    svc = service()
    try:
        conv = svc.get(g.user["uid"], conversation_id)
    except ChatError:
        abort(404)
    p = get_philosopher(conv["philosopher_id"])
    if p is None:
        abort(404)
    page = svc.page(g.user["uid"], conversation_id, limit=CHAT_PAGE_SIZE)
    messages = page["messages"]
    return render_template(
        "chat.html", p=p, conversation=conv, messages=messages, has_more=page["has_more"],
        reply_state=svc.reply_state(g.user["uid"], conv),
        status=limit_status(conv.get("message_count", 0), svc.settings),
        sidebar=_sidebar(active=conversation_id), max_chars=svc.settings.max_user_chars,
    )


def _sidebar(active: str | None = None) -> dict:
    items = get_repository().list_conversations(g.user["uid"], limit=SIDEBAR_SIZE)
    return {"items": items, "active": active}


@bp.get("/history")
@login_required
def history():
    before = None
    raw = request.args.get("before")
    if raw:
        try:
            before = datetime.fromisoformat(raw)
            if before.tzinfo is None:
                before = before.replace(tzinfo=timezone.utc)
        except ValueError:
            before = None
    items = get_repository().list_conversations(g.user["uid"], limit=HISTORY_PAGE_SIZE + 1,
                                                before=before)
    has_more = len(items) > HISTORY_PAGE_SIZE
    items = items[:HISTORY_PAGE_SIZE]
    next_before = items[-1]["updated_at"].isoformat() if has_more and items else None
    return render_template("history.html", conversations=items, next_before=next_before)


@bp.route("/c/<conversation_id>/delete", methods=["GET", "POST"])
@login_required
def delete(conversation_id):
    svc = service()
    try:
        conv = svc.get(g.user["uid"], conversation_id)
    except ChatError:
        abort(404)
    if request.method == "GET":
        return render_template("delete_confirm.html", conversation=conv)
    svc.delete(g.user["uid"], conversation_id)
    log.info("conversation deleted conv=%s", conversation_id)
    flash("대화를 삭제했어요.", "success")
    return redirect(url_for("chat.history"))


# ---------------------------------------------------------------------------
# JSON API (CSRF header required — see security.py)
# ---------------------------------------------------------------------------

@bp.post("/api/conversations")
@login_required
def api_create():
    data = _payload()
    svc = service()
    philosopher_id = str(data.get("philosopher_id", ""))
    try:
        content = svc.validate(data.get("content"))
        if get_philosopher(philosopher_id) is None:
            raise ChatError("bad_philosopher", "철학자를 다시 선택해 주세요.", 400)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    try:
        conv = svc.create_conversation(g.user["uid"], philosopher_id)
        result = svc.send(g.user["uid"], conv["id"], content)
    except ChatError as exc:
        return _error(exc)
    if result["summary_due"]:
        _schedule_summary(svc, g.user["uid"], conv["id"])
    return jsonify(ok=True, **result,
                   conversation_url=url_for("chat.conversation", conversation_id=conv["id"])), 201


@bp.post("/api/conversations/<conversation_id>/messages")
@login_required
def api_send(conversation_id):
    data = _payload()
    svc = service()
    try:
        content = svc.validate(data.get("content"))
        svc.get(g.user["uid"], conversation_id)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    try:
        result = svc.send(g.user["uid"], conversation_id, content)
    except ChatError as exc:
        return _error(exc)
    if result["summary_due"]:
        _schedule_summary(svc, g.user["uid"], conversation_id)
    return jsonify(ok=True, **result)


@bp.post("/api/conversations/<conversation_id>/retry")
@login_required
def api_retry(conversation_id):
    svc = service()
    try:
        svc.get(g.user["uid"], conversation_id)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    try:
        result = svc.retry(g.user["uid"], conversation_id)
    except ChatError as exc:
        return _error(exc)
    if result["summary_due"]:
        _schedule_summary(svc, g.user["uid"], conversation_id)
    return jsonify(ok=True, **result)


@bp.get("/api/conversations/<conversation_id>/messages")
@login_required
def api_messages(conversation_id):
    svc = service()
    try:
        svc.get(g.user["uid"], conversation_id)
    except ChatError as exc:
        return _error(exc)
    before = request.args.get("before", type=int)
    page = svc.page(g.user["uid"], conversation_id, before=before, limit=CHAT_PAGE_SIZE)
    return jsonify(ok=True, **page)
