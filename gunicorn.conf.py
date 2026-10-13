"""Gunicorn settings, loaded automatically by `gunicorn wsgi:app`.

Render's free instance has 512 MB RAM, so one worker process with a
small thread pool keeps memory low while still overlapping the slow
I/O (Firestore, Anthropic) of concurrent requests.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = "gthread"
# Long enough for one AI reply plus an occasional context-summary call.
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
# Do not preload: gRPC (Firestore) must initialise after the worker forks.
preload_app = False
