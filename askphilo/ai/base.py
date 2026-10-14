"""Provider-neutral types for reply generation and context summaries."""

from __future__ import annotations

from dataclasses import dataclass, field


class AIError(Exception):
    """Reply generation failed. Never retried automatically (API cost)."""

    def __init__(self, reason: str = "error"):
        super().__init__(reason)
        self.reason = reason


@dataclass
class AIUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class AIReply:
    text: str
    provider: str
    model: str
    usage: AIUsage = field(default_factory=AIUsage)
    stop_reason: str | None = None
    title: str | None = None  # short conversation title, only when requested


@dataclass
class SummaryResult:
    summary: str
    provider: str
    model: str
    # None means "leave the stored user memory untouched".
    user_memory: dict | None = None
    usage: AIUsage = field(default_factory=AIUsage)
    title: str | None = None


@dataclass
class ImageReading:
    """Attached photos turned into text by the (cheap) vision model."""
    text: str
    provider: str
    model: str
    usage: AIUsage = field(default_factory=AIUsage)


class AIProvider:
    name = "abstract"
    is_billable = False  # True when calls cost money (counted as api_calls)

    def read_images(self, *, system: str, images: list[list[dict]], max_tokens: int) -> ImageReading:
        """images: per photo, its parts top to bottom: [{"mime", "data_b64"}].
        Providers that can't read photos raise NotImplementedError (photos then
        go to the reply model directly)."""
        raise NotImplementedError

    def reply(self, *, system: str, messages: list[dict], philosopher: dict,
              max_tokens: int, want_title: bool = False) -> AIReply:
        raise NotImplementedError

    def summarize(self, *, system: str, prompt: str, philosopher: dict,
                  old_summary: str, old_memory: dict | None,
                  transcript: list[dict], max_tokens: int) -> SummaryResult:
        raise NotImplementedError
