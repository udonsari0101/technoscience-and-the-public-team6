from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, send_from_directory

from .pages import landing_redirect

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    redirect_response = landing_redirect()
    if redirect_response is not None:
        return redirect_response
    return render_template("index.html")


@bp.get("/health")
def health():
    """Liveness probe for Render.

    Must stay cheap: no Firestore reads/writes, no AI calls, no secrets.
    """
    cfg = current_app.config
    payload = {
        "status": "ok",
        "app": cfg["APP_NAME"],
        "firebase_configured": cfg["FIREBASE_CONFIGURED"],
        "ai_mode": cfg["AI_MODE"],
    }
    if cfg.get("RENDER_GIT_COMMIT"):
        payload["commit"] = cfg["RENDER_GIT_COMMIT"][:7]
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/favicon.ico")
def favicon():
    return send_from_directory(
        current_app.static_folder, "img/favicon.ico", mimetype="image/vnd.microsoft.icon"
    )
