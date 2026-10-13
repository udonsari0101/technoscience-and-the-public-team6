"""WSGI entry point.

Production (Render):  gunicorn wsgi:app   (settings in gunicorn.conf.py)
Local development:    python wsgi.py
"""

import os

from askphilo import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=not app.config["IS_PRODUCTION"])
