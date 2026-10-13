"""AskPhilo — philosopher-guided relationship conversations."""

from __future__ import annotations

import logging
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import load_config, resolve_credentials_path

log = logging.getLogger("askphilo")


def create_app(overrides: dict | None = None) -> Flask:
    overrides = overrides or {}
    testing = bool(overrides.get("TESTING"))

    if not testing:
        # Local development reads .env; Render injects real env vars.
        from dotenv import load_dotenv

        load_dotenv(override=False)

    app = Flask(__name__)
    app.config.update(load_config())
    app.config.update(overrides)

    if testing:
        # Tests must never reach paid APIs or production data,
        # regardless of what the developer's shell or .env contains.
        app.config["AI_MODE"] = "mock"
        app.config["DATA_BACKEND"] = "memory"
        app.config["MOCK_AI_DELAY_MS"] = 0
        app.config["ENABLE_DEV_ROUTES"] = overrides.get("ENABLE_DEV_ROUTES", False)

    if app.config["IS_PRODUCTION"]:
        # The controller is an evaluation switch only; production keeps it on.
        app.config["CONTROLLER_ENABLED"] = True
        app.config["ENABLE_DEV_ROUTES"] = False

    _configure_logging(app)

    # Render terminates TLS at its proxy; trust one hop for scheme/host.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    creds = resolve_credentials_path(app.config["FIREBASE_CREDENTIALS_PATH"])
    app.config["FIREBASE_CONFIGURED"] = bool(creds and creds.is_file())

    from . import templating
    from .security import init_security
    from .views.auth import bp as auth_bp
    from .views.chat import bp as chat_bp
    from .views.main import bp as main_bp
    from .views.pages import bp as pages_bp

    init_security(app)
    templating.init_app(app)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(chat_bp)
    if app.config["ENABLE_DEV_ROUTES"]:
        from .views.dev import bp as dev_bp

        app.register_blueprint(dev_bp)

    from . import cli

    cli.init_app(app)

    if app.config["SECRET_KEY_GENERATED"] and app.config["IS_PRODUCTION"]:
        log.warning("SECRET_KEY is not set; using an ephemeral key.")

    log.info(
        "AskPhilo started env=%s ai_mode=%s firebase_configured=%s",
        app.config["APP_ENV"],
        app.config["AI_MODE"],
        app.config["FIREBASE_CONFIGURED"],
    )
    return app


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if os.environ.get("ASKPHILO_DEBUG_LOG") else logging.INFO
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    log.setLevel(level)
