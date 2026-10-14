"""Admin dashboard: who uses AskPhilo and how much of the API budget is left.

Access requires users/{id}.role == "admin", checked against the database on
every request (not just the session). No conversation content and no
password data is ever loaded here.
"""

from __future__ import annotations

import functools

from flask import Blueprint, abort, current_app, g, render_template

from ..auth import login_required
from ..data import get_repository
from ..ratelimit import get_limiter
from datetime import date, timedelta

from ..services.quota import today_kst

bp = Blueprint("admin", __name__, url_prefix="/admin")

TOKEN_KEYS = ("input_tokens", "output_tokens", "summary_input_tokens", "summary_output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")
COUNT_KEYS = ("replies", "api_calls", "reply_calls", "summary_calls", "failed_calls", "mock_calls")


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


def estimate_cost(totals: dict, cfg) -> float | None:
    """USD estimate from configured per-1M-token prices (None if not configured)."""
    p_in = cfg["ANTHROPIC_PRICE_INPUT_PER_MTOK"]
    p_out = cfg["ANTHROPIC_PRICE_OUTPUT_PER_MTOK"]
    if not p_in and not p_out:
        return None
    cost = ((totals["input_tokens"] + totals["summary_input_tokens"]) * p_in
            + (totals["output_tokens"] + totals["summary_output_tokens"]) * p_out
            + totals["cache_read_input_tokens"] * cfg["ANTHROPIC_PRICE_CACHE_READ_PER_MTOK"]
            + totals["cache_creation_input_tokens"] * cfg["ANTHROPIC_PRICE_CACHE_WRITE_PER_MTOK"])
    return round(cost / 1_000_000, 4)


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
    month_cost = estimate_cost(month_totals, cfg)
    budget = cfg["ANTHROPIC_MONTHLY_BUDGET_USD"] or None
    remaining_budget = round(budget - month_cost, 4) if budget and month_cost is not None else None

    per_user: dict[str, dict] = {}
    for row in repo.iter_conversation_usage(limit=5000):
        agg = per_user.setdefault(row["user_id"], {"conversations": 0, "messages": 0, "api_calls": 0,
                                                    "tokens": 0})
        usage = row["usage"]
        agg["conversations"] += 1
        agg["messages"] += row["message_count"]
        agg["api_calls"] += usage.get("api_calls", 0)
        agg["tokens"] += sum(usage.get(k, 0) or 0 for k in TOKEN_KEYS)

    users = []
    for u in repo.list_users(limit=500):
        daily = u.get("daily_usage") or {}
        users.append({
            **u,
            "today_questions": daily.get("count", 0) if daily.get("date") == today else 0,
            **per_user.get(u["id"], {"conversations": 0, "messages": 0, "api_calls": 0, "tokens": 0}),
        })

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
        month_cost=month_cost,
        budget=budget,
        remaining_budget=remaining_budget,
        hourly_limit=hourly_limit,
        hourly_remaining=max(0, hourly_limit - hourly_used),
        daily_limit=cfg["CHAT_MESSAGES_PER_DAY"],
        ai_mode=cfg["AI_MODE"],
        model=cfg["ANTHROPIC_MODEL"] if cfg["AI_MODE"] == "anthropic" else "mock",
    )
