"""Jinja filters and globals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Flask

from .auth import current_user

KST = timezone(timedelta(hours=9))  # Korea has no DST


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def relative_time(value: datetime | None, now: datetime | None = None) -> str:
    if not isinstance(value, datetime):
        return ""
    now = now or datetime.now(timezone.utc)
    value = _as_utc(value)
    seconds = (now - value).total_seconds()
    if seconds < 60:
        return "방금 전"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 전"
    local, local_now = value.astimezone(KST), now.astimezone(KST)
    days = (local_now.date() - local.date()).days
    if days == 0:
        return f"{int(seconds // 3600)}시간 전"
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    if local.year == local_now.year:
        return f"{local.month}월 {local.day}일"
    return f"{local.year}. {local.month}. {local.day}."


def josa_wa(word: str) -> str:
    """'플라톤과' / '니체와': pick the particle from the final syllable's batchim."""
    word = word or ""
    last = word[-1:] or " "
    code = ord(last) - 0xAC00
    has_batchim = 0 <= code <= 11171 and code % 28 != 0
    return f"{word}{'과' if has_batchim else '와'}"


def iso_time(value: datetime | None) -> str:
    return _as_utc(value).isoformat() if isinstance(value, datetime) else ""


def init_app(app: Flask) -> None:
    app.jinja_env.filters["relative_time"] = relative_time
    app.jinja_env.filters["iso_time"] = iso_time
    app.jinja_env.filters["josa_wa"] = josa_wa

    @app.context_processor
    def _inject():
        return {
            "current_user": current_user(),
            "app_name": app.config["APP_NAME"],
        }
