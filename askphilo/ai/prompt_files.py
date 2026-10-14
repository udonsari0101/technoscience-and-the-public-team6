"""Loads the system prompts kept as Markdown in /system_prompt.

    system_prompt/base.md, controller_<version>.md, style.md, summary.md, title.md
    system_prompt/philosophers_prompt/<philosopher_id>.md

HTML comments (<!-- ... -->) are notes for editors and are never sent to
the model. Files are read once per process; edit + push to change them.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "system_prompt"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLANKS = re.compile(r"\n{3,}")


class PromptFileError(RuntimeError):
    pass


@lru_cache(maxsize=None)
def load(relative: str) -> str:
    path = PROMPT_DIR / relative
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptFileError(f"missing prompt file: system_prompt/{relative}") from exc
    text = _BLANKS.sub("\n\n", _COMMENT.sub("", raw)).strip()
    if not text:
        raise PromptFileError(f"empty prompt file: system_prompt/{relative}")
    return text


def controller(version: str) -> str:
    """controller_<version>.md, falling back to v1 for unknown versions."""
    name = f"controller_{version}.md"
    return load(name) if (PROMPT_DIR / name).is_file() else load("controller_v1.md")


def philosopher(philosopher_id: str) -> str:
    return load(f"philosophers_prompt/{philosopher_id}.md")
