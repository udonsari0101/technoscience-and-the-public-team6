from __future__ import annotations

import json

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


MANIFEST = {
    "name": "AskPhilo — 철학자와 나누는 연애 상담",
    "short_name": "AskPhilo",
    "lang": "ko",
    "start_url": "/home",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f6f7f9",
    "theme_color": "#f6f7f9",
    "icons": [
        {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}

ROBOTS = """User-agent: *
Allow: /$
Disallow: /api/
Disallow: /c/
Disallow: /chat/
Disallow: /home
Disallow: /history
Disallow: /new
Disallow: /philosophers/
Disallow: /settings
Disallow: /dev/
"""


@bp.get("/manifest.webmanifest")
def manifest():
    """Lets phones add AskPhilo to the home screen (no service worker / offline)."""
    response = current_app.response_class(json.dumps(MANIFEST, ensure_ascii=False),
                                          mimetype="application/manifest+json")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@bp.get("/robots.txt")
def robots():
    response = current_app.response_class(ROBOTS, mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@bp.get("/favicon.ico")
def favicon():
    return send_from_directory(
        current_app.static_folder, "img/favicon.ico", mimetype="image/vnd.microsoft.icon"
    )
