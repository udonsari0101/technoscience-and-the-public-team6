from __future__ import annotations

import json
import logging
import queue
import threading
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
from ..data import DataError
from ..services import images as image_files
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
MSG_BUSY = "지금은 이용자가 많아요. 잠시 후 다시 시도해 주세요."
UPLOADS_PER_10_MIN = 40
STREAM_IDLE_SECONDS = 150  # longer than a reply plus photo reading
MSG_SERVER = "잠시 문제가 생겼어요. 다시 시도해 주세요."


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
    """Per-minute cap per user and a site-wide hourly breaker on reply requests
    (in-process: one Gunicorn worker). The daily allowance shown to users is
    persistent and enforced by the service (Firestore). None if allowed."""
    cfg = current_app.config
    limiter = get_limiter()
    uid = g.user["uid"]
    if not limiter.allowed(f"chat:{uid}", cfg["CHAT_MESSAGES_PER_MINUTE"], 60):
        return jsonify(ok=False, error="rate_limited", message=MSG_SLOW_DOWN), 429
    if not limiter.allowed("ai-global", cfg["AI_REPLIES_PER_HOUR"], 3600):
        return jsonify(ok=False, error="busy", message=MSG_BUSY), 503
    limiter.record(f"chat:{uid}", 60)
    limiter.record("ai-global", 3600)
    return None


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _stream_send(svc: ConversationService, conversation_id: str, content: str, image_ids: list,
                 meta: dict | None = None) -> Response:
    """Server-sent events while the single reply request runs: "meta", "user",
    "status", one "sentence" per finished sentence, then "done" or "error".
    The reply runs in its own thread and is stored even if the browser goes away."""
    app = current_app._get_current_object()
    uid = g.user["uid"]
    events: queue.Queue = queue.Queue()

    def emit(kind, data):
        events.put((kind, data))

    def work():
        try:
            result = svc.send(uid, conversation_id, content, image_ids, on_event=emit)
            if result["summary_due"]:
                if app.config.get("TESTING") or app.config.get("SUMMARY_INLINE"):
                    svc.maybe_summarize(uid, conversation_id)
                else:
                    background.submit(app, svc.maybe_summarize, uid, conversation_id)
            emit("done", {"ok": True, **result, **(meta or {})})
        except ChatError as exc:
            emit("error", {"ok": False, "error": exc.code, "message": exc.message,
                           "http_status": exc.status, **exc.extra})
        except DataError:
            emit("error", {"ok": False, "error": "storage", "message": MSG_SERVER, "http_status": 503})
        except Exception as exc:  # noqa: BLE001 — never leak internals to the browser
            log.error("streamed reply failed: %s", type(exc).__name__)
            emit("error", {"ok": False, "error": "server", "message": MSG_SERVER, "http_status": 500})
        finally:
            events.put(None)

    threading.Thread(target=work, name="askphilo-reply", daemon=True).start()

    def generate():
        yield ": stream\n\n"  # opens the stream right away through proxies
        if meta:
            yield _sse("meta", meta)
        while True:
            try:
                item = events.get(timeout=STREAM_IDLE_SECONDS)
            except queue.Empty:
                yield _sse("error", {"ok": False, "error": "timeout", "message": MSG_SERVER})
                return
            if item is None:
                return
            yield _sse(*item)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


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
    svc = service()
    return render_template(
        "chat.html", p=p, conversation=None, messages=[], has_more=False, reply_state=None,
        status=limit_status(0, svc.settings), sidebar=_sidebar(), max_chars=svc.settings.max_user_chars,
        daily=svc.daily(g.user["uid"]),
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
        daily=svc.daily(g.user["uid"]),
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


@bp.route("/c/<conversation_id>/rename", methods=["GET", "POST"])
@login_required
def rename(conversation_id):
    svc = service()
    try:
        conv = svc.get(g.user["uid"], conversation_id)
    except ChatError:
        abort(404)
    if request.method == "GET":
        return render_template("rename.html", conversation=conv)
    try:
        svc.rename(g.user["uid"], conversation_id, request.form.get("title"))
    except ChatError as exc:
        flash(exc.message, "error")
        return redirect(url_for("chat.rename", conversation_id=conversation_id))
    flash("대화 이름을 바꿨어요.", "success")
    return redirect(url_for("chat.conversation", conversation_id=conversation_id))


@bp.route("/c/<conversation_id>/delete", methods=["GET", "POST"])
@login_required
def delete(conversation_id):
    """Move to the trash (restorable); permanent deletion happens in the trash."""
    svc = service()
    try:
        conv = svc.get(g.user["uid"], conversation_id)
    except ChatError:
        abort(404)
    if request.method == "GET":
        return render_template("delete_confirm.html", conversation=conv)
    svc.trash(g.user["uid"], conversation_id)
    log.info("conversation trashed conv=%s", conversation_id)
    flash("휴지통으로 옮겼어요.", "success")
    return redirect(url_for("chat.history"))


@bp.get("/trash")
@login_required
def trash():
    items = service().trash_items(g.user["uid"])
    return render_template("trash.html", conversations=items)


@bp.post("/trash/<conversation_id>/restore")
@login_required
def restore(conversation_id):
    try:
        service().restore(g.user["uid"], conversation_id)
    except ChatError:
        abort(404)
    flash("대화를 되살렸어요.", "success")
    return redirect(url_for("chat.conversation", conversation_id=conversation_id))


@bp.post("/trash/<conversation_id>/delete")
@login_required
def delete_forever(conversation_id):
    try:
        service().delete_forever(g.user["uid"], conversation_id)
    except ChatError:
        abort(404)
    log.info("conversation deleted conv=%s", conversation_id)
    flash("영구 삭제했어요.", "success")
    return redirect(url_for("chat.trash"))


@bp.post("/trash/empty")
@login_required
def empty_trash():
    count = service().empty_trash(g.user["uid"])
    flash(f"휴지통을 비웠어요 ({count}개)." if count else "휴지통이 비어 있어요.", "success")
    return redirect(url_for("chat.trash"))


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
        image_ids = svc.check_images(data.get("image_ids"))
        content = svc.validate(data.get("content"), allow_empty=bool(image_ids))
        if get_philosopher(philosopher_id) is None:
            raise ChatError("bad_philosopher", "철학자를 다시 선택해 주세요.", 400)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    try:
        conv = svc.create_conversation(g.user["uid"], philosopher_id)
        result = svc.send(g.user["uid"], conv["id"], content, image_ids)
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
        image_ids = svc.check_images(data.get("image_ids"))
        content = svc.validate(data.get("content"), allow_empty=bool(image_ids))
        svc.get(g.user["uid"], conversation_id)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    try:
        result = svc.send(g.user["uid"], conversation_id, content, image_ids)
    except ChatError as exc:
        return _error(exc)
    if result["summary_due"]:
        _schedule_summary(svc, g.user["uid"], conversation_id)
    return jsonify(ok=True, **result)


@bp.post("/api/conversations/stream")
@login_required
def api_create_stream():
    data = _payload()
    svc = service()
    philosopher_id = str(data.get("philosopher_id", ""))
    try:
        image_ids = svc.check_images(data.get("image_ids"))
        content = svc.validate(data.get("content"), allow_empty=bool(image_ids))
        if get_philosopher(philosopher_id) is None:
            raise ChatError("bad_philosopher", "철학자를 다시 선택해 주세요.", 400)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    try:
        conv = svc.create_conversation(g.user["uid"], philosopher_id)
    except ChatError as exc:
        return _error(exc)
    meta = {"conversation_id": conv["id"],
            "conversation_url": url_for("chat.conversation", conversation_id=conv["id"])}
    return _stream_send(svc, conv["id"], content, image_ids, meta)


@bp.post("/api/conversations/<conversation_id>/messages/stream")
@login_required
def api_send_stream(conversation_id):
    data = _payload()
    svc = service()
    try:
        image_ids = svc.check_images(data.get("image_ids"))
        content = svc.validate(data.get("content"), allow_empty=bool(image_ids))
        svc.get(g.user["uid"], conversation_id)
    except ChatError as exc:
        return _error(exc)
    blocked = _budget_error()
    if blocked:
        return blocked
    return _stream_send(svc, conversation_id, content, image_ids)


@bp.post("/api/conversations/<conversation_id>/messages/<int:message_index>/basis")
@login_required
def api_basis(conversation_id, message_index):
    """Info button: why this reply sounds like its philosopher (first view uses a question)."""
    limiter = get_limiter()
    key = f"chat:{g.user['uid']}"
    if not limiter.allowed(key, current_app.config["CHAT_MESSAGES_PER_MINUTE"], 60):
        return jsonify(ok=False, error="rate_limited", message=MSG_SLOW_DOWN), 429
    limiter.record(key, 60)
    try:
        result = service().basis(g.user["uid"], conversation_id, message_index)
    except ChatError as exc:
        return _error(exc)
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


@bp.post("/api/uploads")
@login_required
def api_upload():
    """One photo per request (≤ 100 MB): shrunk, stored as base64, then waits for a message."""
    request.max_content_length = image_files.MAX_UPLOAD_BYTES + 64 * 1024  # + multipart framing
    limiter = get_limiter()
    key = f"upload:{g.user['uid']}"
    if not limiter.allowed(key, UPLOADS_PER_10_MIN, 600):
        return jsonify(ok=False, error="rate_limited", message=MSG_SLOW_DOWN), 429
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return jsonify(ok=False, error="not_image", message=image_files.MSG_NOT_IMAGE), 400
    limiter.record(key, 600)
    try:
        staged = service().stage_upload(g.user["uid"], upload.stream)
    except image_files.ImageError as exc:
        return jsonify(ok=False, error=exc.code, message=exc.message), 400
    except ChatError as exc:
        return _error(exc)
    return jsonify(ok=True, upload=staged), 201


@bp.post("/api/uploads/<upload_id>/discard")
@login_required
def api_discard_upload(upload_id):
    try:
        service().discard_upload(g.user["uid"], upload_id)
    except ChatError as exc:
        return _error(exc)
    return jsonify(ok=True)


@bp.get("/images/<image_id>")
@login_required
def image(image_id):
    """A photo part, decoded from its base64 copy in Firestore (owner only)."""
    found = service().image(g.user["uid"], image_id)
    if found is None:
        abort(404)
    data, mime = found
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


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
