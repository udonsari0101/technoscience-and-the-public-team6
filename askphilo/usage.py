"""API usage aggregation for developers (no message content is ever read)."""

from __future__ import annotations

from .data import Repository
from .data.base import EMPTY_USAGE


def aggregate(repo: Repository, limit: int = 500) -> dict:
    rows = repo.iter_conversation_usage(limit=limit)
    totals = dict(EMPTY_USAGE)
    messages = 0
    summarized = 0
    for row in rows:
        messages += row["message_count"]
        if row["summary_version"]:
            summarized += 1
        for key, value in row["usage"].items():
            totals[key] = totals.get(key, 0) + (value or 0)
    replies = totals["reply_calls"] or 0
    return {
        "conversations": len(rows),
        "messages": messages,
        "conversations_with_summary": summarized,
        "totals": totals,
        "averages": {
            "input_tokens_per_reply": round(totals["input_tokens"] / replies, 1) if replies else 0,
            "output_tokens_per_reply": round(totals["output_tokens"] / replies, 1) if replies else 0,
            "summary_calls_per_100_replies": round(100 * totals["summary_calls"] / replies, 1) if replies else 0,
        },
        "truncated": len(rows) >= limit,
    }


def rows_without_ids(repo: Repository, limit: int = 50) -> list[dict]:
    """Per-conversation counters with ids shortened (no titles, no content)."""
    out = []
    for row in repo.iter_conversation_usage(limit=limit):
        out.append({
            "conversation": row["conversation_id"][:8],
            "philosopher": row["philosopher_id"],
            "controller": row["controller_version"],
            "messages": row["message_count"],
            "summarized_through": row["summarized_through_message_index"],
            **{k: row["usage"].get(k, 0) for k in (
                "api_calls", "reply_calls", "summary_calls", "failed_calls", "mock_calls",
                "input_tokens", "output_tokens", "cache_read_input_tokens")},
        })
    return out
