"""Response hardening, CSRF protection and friendly error pages."""

from __future__ import annotations

import hmac
import logging
import secrets

from flask import Flask, flash, jsonify, redirect, render_template, request, session

from .data import DataError

log = logging.getLogger("askphilo")

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "  # blob: previews of photos being uploaded
    "style-src 'self'; "
    "script-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MSG_SERVER = "잠시 문제가 생겼어요. 다시 시도해 주세요."
MSG_CSRF = "페이지가 오래되었어요. 다시 시도해 주세요."
# Pages whose POST handler has a GET counterpart we can bounce back to.
CSRF_RETRY_ENDPOINTS = {"auth.login", "auth.signup"}


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _csrf_valid() -> bool:
    expected = session.get("csrf_token")
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not expected or not sent:
        return False
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input.
    return hmac.compare_digest(str(expected).encode("utf-8"), str(sent).encode("utf-8"))


def _is_api() -> bool:
    return request.path.startswith("/api/")


def init_security(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _csrf_protect():
        if request.method not in UNSAFE_METHODS:
            return None
        if _csrf_valid():
            return None
        if _is_api():
            return jsonify(error="csrf", message=MSG_CSRF), 400
        if request.endpoint in CSRF_RETRY_ENDPOINTS:
            flash(MSG_CSRF, "error")
            return redirect(request.full_path.rstrip("?"))
        return render_template("error.html", code=400, message=MSG_CSRF), 400

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if app.config.get("IS_PRODUCTION"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if response.mimetype == "text/html":
            # Pages show private conversations; never cache them in shared caches.
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.errorhandler(DataError)
    def _data_error(exc):
        log.error("data error on %s: %s", request.endpoint, type(exc).__name__)
        if _is_api():
            return jsonify(error="storage", message=MSG_SERVER), 503
        return render_template("error.html", code=503, message=MSG_SERVER), 503

    @app.errorhandler(404)
    def _not_found(_exc):
        if _is_api():
            return jsonify(error="not_found", message="찾을 수 없어요."), 404
        return render_template("error.html", code=404, message="페이지를 찾을 수 없어요."), 404

    @app.errorhandler(405)
    def _method_not_allowed(_exc):
        if _is_api():
            return jsonify(error="method_not_allowed", message="지원하지 않는 요청이에요."), 405
        return render_template("error.html", code=405, message="지원하지 않는 요청이에요."), 405

    @app.errorhandler(413)
    def _too_large(_exc):
        if request.path.startswith("/api/uploads"):
            return jsonify(error="too_large", message="사진은 한 장에 100MB까지 보낼 수 있어요."), 413
        if _is_api():
            return jsonify(error="too_large", message="메시지가 너무 길어요."), 413
        return render_template("error.html", code=413, message="요청이 너무 커요."), 413

    @app.errorhandler(500)
    def _server_error(exc):
        original = getattr(exc, "original_exception", None)
        log.error("unhandled error on %s: %s", request.endpoint, type(original or exc).__name__)
        if _is_api():
            return jsonify(error="server", message=MSG_SERVER), 500
        return render_template("error.html", code=500, message=MSG_SERVER), 500
