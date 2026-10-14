"""How replies are shown: chat bubbles (also while streaming) and Seoul-time clocks.

The same splitting runs in static/js/chat.js (keep the two in step):
  1. a sentence ends at . ? ! … ~ followed by whitespace, or at a line break
  2. a sentence shorter than MIN_BUBBLE_CHARS rides along with the next one
     (the last one stays on its own, so streaming and reloads look the same)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))  # Asia/Seoul, no DST
MIN_BUBBLE_CHARS = 12
_END = re.compile(r"([.?!…~]+)\s+")
WEEKDAYS = "월화수목금토일"


def sentences(text: str) -> list[str]:
    marked = _END.sub(lambda m: m.group(1) + "\n", text or "")
    return [s.strip() for s in re.split(r"\n+", marked) if s.strip()]


def group(parts: list[str]) -> list[str]:
    out, pending = [], ""
    for s in parts:
        pending = f"{pending} {s}" if pending else s
        if len(pending) >= MIN_BUBBLE_CHARS:
            out.append(pending)
            pending = ""
    if pending:
        out.append(pending)
    return out


def bubbles(text: str) -> list[str]:
    return group(sentences(text))


class SentenceStream:
    """Feed streamed text; `emit(sentence)` fires for every finished sentence.
    Nothing after "<title>" (the conversation title tag) is ever emitted."""

    def __init__(self, emit):
        self.emit = emit
        self.buffer = ""
        self.sent = 0  # sentences emitted so far
        self.closed = False  # reached the title tag

    def feed(self, delta: str) -> None:
        if self.closed or not delta:
            return
        self.buffer += delta
        cut = self.buffer.find("<title")
        text = self.buffer if cut < 0 else self.buffer[:cut]
        if cut >= 0:
            self.closed = True
        # everything up to the last sentence boundary is final
        last = None
        for m in re.finditer(r"[.?!…~]+\s+|\n+", text):
            last = m
        if self.closed:
            done = sentences(text)
        elif last is None:
            return
        else:
            done = sentences(text[: last.end()])
        for s in done[self.sent:]:
            self.emit(s)
        self.sent = max(self.sent, len(done))

    def finish(self, final_text: str) -> None:
        """Emit what is left, from the text that was actually stored."""
        for s in sentences(final_text)[self.sent:]:
            self.emit(s)
        self.sent = len(sentences(final_text))


def _kst(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(KST)


def clock(value: datetime | None) -> str:
    """'오후 3:12' in Seoul time."""
    if not isinstance(value, datetime):
        return ""
    local = _kst(value)
    hour = local.hour % 12 or 12
    return f"{'오전' if local.hour < 12 else '오후'} {hour}:{local.minute:02d}"


def day_key(value: datetime | None) -> str:
    return _kst(value).date().isoformat() if isinstance(value, datetime) else ""


def day_label(value: datetime | None) -> str:
    """'2026년 10월 14일 화요일' in Seoul time."""
    if not isinstance(value, datetime):
        return ""
    local = _kst(value)
    return f"{local.year}년 {local.month}월 {local.day}일 {WEEKDAYS[local.weekday()]}요일"
