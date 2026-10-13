"""Repository selection.

DATA_BACKEND=auto (default) uses Firestore when the credentials file
exists. Without credentials, development falls back to an in-memory
store, while production refuses to store anything (no silent data loss).
"""

from __future__ import annotations

import logging
import threading

from flask import Flask, current_app

from ..config import resolve_credentials_path
from .base import (  # noqa: F401  re-exported
    Conflict,
    DataError,
    DataUnavailable,
    NotFound,
    Repository,
    UnavailableRepository,
    UsernameTaken,
)

log = logging.getLogger("askphilo.data")
_lock = threading.Lock()
EXT_KEY = "askphilo.repository"


def _build(app: Flask) -> Repository:
    backend = app.config["DATA_BACKEND"]
    creds = resolve_credentials_path(app.config["FIREBASE_CREDENTIALS_PATH"])
    has_creds = bool(creds and creds.is_file())

    if backend == "auto":
        if has_creds:
            backend = "firestore"
        elif app.config["IS_PRODUCTION"]:
            log.error("Firebase credentials missing in production; storage disabled")
            return UnavailableRepository()
        else:
            backend = "memory"

    if backend == "firestore":
        if not has_creds:
            log.error("DATA_BACKEND=firestore but credentials file is missing")
            return UnavailableRepository()
        from .firestore_repo import FirestoreRepository

        return FirestoreRepository(str(creds))

    from .memory_repo import MemoryRepository

    log.warning("Using in-memory storage: data is lost on restart")
    return MemoryRepository()


def get_repository(app: Flask | None = None) -> Repository:
    app = app or current_app._get_current_object()
    repo = app.extensions.get(EXT_KEY)
    if repo is None:
        with _lock:
            repo = app.extensions.get(EXT_KEY)
            if repo is None:
                repo = _build(app)
                app.extensions[EXT_KEY] = repo
    return repo


def set_repository(app: Flask, repo: Repository) -> None:
    """Inject a repository (tests)."""
    app.extensions[EXT_KEY] = repo
