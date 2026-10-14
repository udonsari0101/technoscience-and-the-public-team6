"""Daily question allowance shown to users.

Counted per user in Firestore (users/{id}.daily_usage = {date, count}) so the
number on screen survives restarts and deploys. The day is the Korean
calendar day: the allowance refills at 00:00 KST. A question is reserved
before the AI call and refunded if no answer is delivered, so failed
replies never use up the allowance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))  # Korea has no DST

MSG_DAILY_LIMIT = "오늘 질문을 모두 사용했어요. 내일 0시에 다시 채워져요."


def today_kst(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(KST).date().isoformat()


def daily_status(used: int, limit: int) -> dict:
    used = max(0, min(int(used), limit))
    remaining = limit - used
    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "low": 0 < remaining <= max(3, limit // 10),
        "exhausted": remaining <= 0,
    }
