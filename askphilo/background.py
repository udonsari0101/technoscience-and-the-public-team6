"""One background thread for rare, non-urgent work (context summaries).

Running summaries here instead of in the request thread (or its
call_on_close hook) keeps Gunicorn's request threads and keep-alive
connections free while a summary request is in flight. The executor is
created lazily, i.e. inside the worker process after the fork.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask

log = logging.getLogger("askphilo.background")
EXT_KEY = "askphilo.executor"
_lock = threading.Lock()


def get_executor(app: Flask) -> ThreadPoolExecutor:
    executor = app.extensions.get(EXT_KEY)
    if executor is None:
        with _lock:
            executor = app.extensions.get(EXT_KEY)
            if executor is None:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="askphilo-bg")
                app.extensions[EXT_KEY] = executor
    return executor


def submit(app: Flask, fn, *args) -> None:
    def _run():
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 — never kill the worker thread
            log.warning("background task failed: %s", type(exc).__name__)

    get_executor(app).submit(_run)
