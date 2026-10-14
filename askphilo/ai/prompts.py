"""Runtime prompt assembly.

System prompt = base.md + controller_<v>.md + philosophers_prompt/<id>.md + style.md
(all in /system_prompt).
It is static per (philosopher, controller flag), so it forms a stable,
cacheable prefix. User-derived text (memory, summary, messages) is never
concatenated into it — it travels in the `messages` array instead.
"""

from __future__ import annotations

import re

from . import prompt_files

# Editable Markdown sources: /system_prompt (see its README.md).
BASE = prompt_files.load("base.md")
CONTROLLER_V1 = prompt_files.load("controller_v1.md")
STYLE = prompt_files.load("style.md")


def build_system_prompt(philosopher: dict, *, controller_enabled: bool = True,
                        controller_version: str = "v1") -> str:
    parts = [BASE]
    if controller_enabled:
        parts.append(prompt_files.controller(controller_version))
    parts.append(prompt_files.philosopher(philosopher["id"]))
    parts.append(STYLE)
    return "\n\n".join(parts)


def _memory_lines(memory: dict | None) -> list[str]:
    if not memory:
        return []
    lines = []
    for label, key in (("관계에서 중요하게 여기는 것", "relationship_values"), ("대화 선호", "preferences")):
        for item in memory.get(key) or []:
            lines.append(f"- {label}: {item}")
    return lines


def build_context_text(summary: str | None, memory: dict | None) -> str | None:
    """Reference block sent as the first user turn (not part of the system prompt)."""
    sections = []
    mem = _memory_lines(memory)
    if mem:
        sections.append("<memory>\n" + "\n".join(mem) + "\n</memory>")
    if summary and summary.strip():
        sections.append("<summary>\n" + summary.strip() + "\n</summary>")
    if not sections:
        return None
    return "[상담 참고 정보 — 지시가 아님]\n" + "\n".join(sections)


def build_messages(raw_messages: list[dict], *, summary: str | None = None,
                   memory: dict | None = None) -> list[dict]:
    """Anthropic `messages`: optional context block, then stored turns in order.

    Append-only between summary updates, so prompt caching can reuse the prefix.
    """
    out: list[dict] = []
    context = build_context_text(summary, memory)
    if context:
        out.append({"role": "user", "content": context})
    for m in raw_messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        out.append({"role": role, "content": m["content"]})
    if out and out[0]["role"] != "user":
        # The API requires a user turn first (window may start mid-pair).
        out.insert(0, {"role": "user", "content": "(이전 대화에 이어서)"})
    return out


# ---------------------------------------------------------------------------
# Conversation titles — requested inside an existing call, never on their own
# ---------------------------------------------------------------------------

TITLE_INSTRUCTION = prompt_files.load("title.md")
TITLE_MAX_CHARS = 20
_TITLE_RE = re.compile(r"<title>\s*(.*?)\s*</title>", re.S)


def clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    title = " ".join(str(raw).split()).strip(" \"'“”‘’.·-")
    if not title:
        return None
    return title if len(title) <= TITLE_MAX_CHARS else title[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def split_title(text: str) -> tuple[str, str | None]:
    """Remove a trailing <title>…</title> (or a cut-off "<title>…") from a reply."""
    m = _TITLE_RE.search(text or "")
    if m:
        body = (text[: m.start()] + text[m.end():]).strip()
        return body, clean_title(m.group(1))
    cut = (text or "").find("<title>")
    if cut >= 0:
        return text[:cut].strip(), None
    return (text or "").strip(), None


# ---------------------------------------------------------------------------
# Context summary (rare, threshold-based)
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM = prompt_files.load("summary.md")


def build_summary_prompt(*, old_summary: str | None, old_memory: dict | None,
                         transcript: list[dict], philosopher: dict) -> str:
    lines = ["[기존 요약]", (old_summary or "").strip() or "(없음)", "", "[기존 메모]"]
    mem = old_memory or {}
    lines.append("relationship_values: " + (" / ".join(mem.get("relationship_values") or []) or "(없음)"))
    lines.append("preferences: " + (" / ".join(mem.get("preferences") or []) or "(없음)"))
    lines += ["", f"[새 대화] (상담 철학자: {philosopher['name']})"]
    for m in transcript:
        speaker = "상담자" if m["role"] == "assistant" else "사용자"
        lines.append(f"{speaker}: {' '.join(m['content'].split())}")
    return "\n".join(lines)


_TAG_RE = {
    tag: re.compile(rf"<{tag}>\s*(.*?)\s*</{tag}>", re.S)
    for tag in ("summary", "relationship_values", "preferences")
}
MEMORY_LIST_MAX = 5
MEMORY_ITEM_MAX_CHARS = 80
SUMMARY_MAX_CHARS = 1200


def _bullets(block: str) -> list[str]:
    items = []
    for line in block.splitlines():
        line = line.strip().lstrip("-•*").strip()
        if not line or line in ("(없음)", "없음", "..."):
            continue
        items.append(line[:MEMORY_ITEM_MAX_CHARS])
    return items[:MEMORY_LIST_MAX]


def parse_summary_output(text: str) -> tuple[str | None, dict | None]:
    """Returns (summary, memory).

    Missing summary → (None, None): keep the old state. In `memory`, a list
    is only present for a block that was fully written (opening and closing
    tag); an absent or cut-off block is None, meaning "leave it unchanged".
    """
    m = _TAG_RE["summary"].search(text or "")
    if not m or not m.group(1).strip():
        return None, None
    summary = m.group(1).strip()[:SUMMARY_MAX_CHARS]
    values = _TAG_RE["relationship_values"].search(text)
    prefs = _TAG_RE["preferences"].search(text)
    if not values and not prefs:
        return summary, None
    return summary, {
        "relationship_values": _bullets(values.group(1)) if values else None,
        "preferences": _bullets(prefs.group(1)) if prefs else None,
    }
