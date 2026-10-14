"""Per-account display preferences (stored on users/{id})."""

from __future__ import annotations

# How fast a reply's bubbles follow each other. The pause per bubble is computed
# in static/js/chat.js (PACE) from these keys.
REPLY_SPEEDS = {"fast": "빠름", "normal": "보통", "slow": "느림"}
DEFAULT_REPLY_SPEED = "normal"


def reply_speed(user: dict | None) -> str:
    value = (user or {}).get("reply_speed")
    return value if value in REPLY_SPEEDS else DEFAULT_REPLY_SPEED
