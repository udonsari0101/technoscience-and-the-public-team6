"""Development-only diagnostics. Not registered when APP_ENV=production."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..auth import login_required
from ..data import get_repository
from ..usage import aggregate, rows_without_ids

bp = Blueprint("dev", __name__, url_prefix="/dev")


@bp.get("/usage")
@login_required
def usage():
    repo = get_repository()
    return jsonify(summary=aggregate(repo), conversations=rows_without_ids(repo))
