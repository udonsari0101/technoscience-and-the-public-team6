"""Admin dashboard: who uses AskPhilo and how much of the API credit is left.

Access requires users/{id}.role == "admin", checked against the database on
every request (not just the session). No conversation content and no
password data is ever loaded here.

The Anthropic API has no balance endpoint (and the key's account may not be
reachable), so the remaining credit is computed here: the credit the admin
entered, minus what AskPhilo recorded for every call (priced per model in
ai/pricing.py), minus any spend outside the service the admin entered.
"""

from __future__ import annotations

import functools
import math
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..ai.pricing import overrides_from_config, price_for
from ..auth import login_required
from ..data import DataError, get_repository
from ..ratelimit import get_limiter
from ..services.quota import today_kst

bp = Blueprint("admin", __name__, url_prefix="/admin")

TOKEN_KEYS = ("input_tokens", "output_tokens", "summary_input_tokens", "summary_output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")
COUNT_KEYS = ("replies", "api_calls", "reply_calls", "summary_calls", "failed_calls", "mock_calls",
              "cost_micro_usd")
MAX_USD = 100_000
MSG_BAD_AMOUNT = "금액은 0 이상의 숫자로 입력해 주세요."


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        user = get_repository().get_user(g.user["uid"])
        if not user or user.get("role") != "admin":
            abort(404)  # do not reveal that the page exists
        return view(*args, **kwargs)

    return wrapper


def _sum(rows: list[dict]) -> dict:
    total = {k: 0 for k in COUNT_KEYS + TOKEN_KEYS}
    for row in rows:
        for key in total:
            total[key] += row.get(key, 0) or 0
    return total


def usd(micro) -> float:
    return (micro or 0) / 1_000_000


def credit_summary(settings: dict, totals: dict) -> dict:
    """Remaining credit = entered credit − recorded service spend − entered outside spend."""
    credit = settings.get("credit_usd")
    outside = settings.get("outside_spend_usd") or 0.0
    # integer micro-dollars so sums and divisions don't pick up float error
    service_micro = int(totals.get("cost_micro_usd") or 0)
    used_micro = service_micro + round(outside * 1_000_000)
    remaining_micro = None if credit is None else round(credit * 1_000_000) - used_micro
    replies = totals.get("replies", 0) or 0
    service, used = usd(service_micro), usd(used_micro)
    remaining = None if remaining_micro is None else usd(remaining_micro)
    per_reply = service / replies if replies and service_micro else None
    replies_left = (remaining_micro * replies // service_micro
                    if remaining_micro is not None and remaining_micro > 0 and per_reply else None)
    return {"credit": credit, "outside": outside, "service": service, "used": used,
            "remaining": remaining, "per_reply": per_reply, "replies_left": replies_left,
            "updated_at": settings.get("updated_at")}


def parse_usd(raw: str | None, *, blank_ok: bool = False) -> float | None:
    text = (raw or "").strip().replace("$", "").replace(",", "")
    if not text:
        if blank_ok:
            return None
        raise ValueError("empty")
    value = float(text)
    if not math.isfinite(value) or not 0 <= value <= MAX_USD:
        raise ValueError("out of range")
    return round(value, 2)


@bp.get("")
@admin_required
def dashboard():
    cfg = current_app.config
    repo = get_repository()
    today = today_kst()
    month = today[:7]

    start = date.fromisoformat(today)
    days = repo.get_usage_days([(start - timedelta(days=i)).isoformat() for i in range(62)])
    today_totals = _sum([d for d in days if d["day"] == today])
    month_totals = _sum([d for d in days if d["day"].startswith(month)])
    credit = credit_summary(repo.get_billing_settings(), repo.get_usage_total())

    per_user: dict[str, dict] = {}
    for row in repo.iter_conversation_usage(limit=5000):
        agg = per_user.setdefault(row["user_id"], {"conversations": 0, "messages": 0, "api_calls": 0,
                                                    "tokens": 0, "cost_micro_usd": 0})
        usage = row["usage"]
        agg["conversations"] += 1
        agg["messages"] += row["message_count"]
        agg["api_calls"] += usage.get("api_calls", 0)
        agg["tokens"] += sum(usage.get(k, 0) or 0 for k in TOKEN_KEYS)
        agg["cost_micro_usd"] += usage.get("cost_micro_usd", 0) or 0

    users = []
    for u in repo.list_users(limit=500):
        daily = u.get("daily_usage") or {}
        users.append({
            **u,
            "today_questions": daily.get("count", 0) if daily.get("date") == today else 0,
            **per_user.get(u["id"], {"conversations": 0, "messages": 0, "api_calls": 0, "tokens": 0,
                                     "cost_micro_usd": 0}),
        })

    live = cfg["AI_MODE"] == "anthropic"
    model = cfg["ANTHROPIC_MODEL"]
    hourly_limit = cfg["AI_REPLIES_PER_HOUR"]
    hourly_used = get_limiter().count("ai-global", 3600)
    return render_template(
        "admin.html",
        users=users,
        days=days[:14],
        today=today,
        today_totals=today_totals,
        month=month,
        month_totals=month_totals,
        credit=credit,
        usd=usd,
        hourly_limit=hourly_limit,
        hourly_remaining=max(0, hourly_limit - hourly_used),
        daily_limit=cfg["CHAT_MESSAGES_PER_DAY"],
        ai_mode=cfg["AI_MODE"],
        model=model if live else "mock",
        price=price_for(model, overrides=overrides_from_config(cfg)) if live else None,
    )


@bp.post("/billing")
@admin_required
def billing():
    try:
        credit = parse_usd(request.form.get("credit_usd"))
        outside = parse_usd(request.form.get("outside_spend_usd"), blank_ok=True) or 0.0
    except ValueError:
        flash(MSG_BAD_AMOUNT, "error")
        return redirect(url_for("admin.dashboard") + "#credit")
    try:
        get_repository().set_billing_settings({"credit_usd": credit, "outside_spend_usd": outside,
                                               "updated_by": g.user["uid"]})
    except DataError:
        flash("잠시 문제가 생겼어요. 다시 시도해 주세요.", "error")
        return redirect(url_for("admin.dashboard") + "#credit")
    flash("크레딧 정보를 저장했어요.", "success")
    return redirect(url_for("admin.dashboard") + "#credit")
