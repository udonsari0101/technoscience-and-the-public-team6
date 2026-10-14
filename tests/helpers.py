"""Test doubles for AI providers (never touch the network)."""

from askphilo.ai.base import AIError, AIProvider, AIUsage
from askphilo.ai.mock import MockProvider
from askphilo.data.memory_repo import MemoryRepository


def repo_with_user(user_id: str = "user-1") -> MemoryRepository:
    """In-memory repository that already holds `user_id` (needed for quotas)."""
    repo = MemoryRepository()
    repo.create_user(user_id=user_id, name="테스트", username=f"u_{user_id[-6:]}".replace("-", "_"),
                     username_normalized=f"u_{user_id[-6:]}".replace("-", "_"), password_hash="x")
    return repo


class CountingProvider(AIProvider):
    """Wraps the mock provider and records every call."""

    name = "mock"
    is_billable = False

    def __init__(self, inner=None):
        self.inner = inner or MockProvider()
        self.reply_calls = []
        self.summary_calls = []

    def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return self.inner.reply(**kwargs)

    def summarize(self, **kwargs):
        self.summary_calls.append(kwargs)
        return self.inner.summarize(**kwargs)


class BillableCountingProvider(CountingProvider):
    """Pretends to be a paid provider so usage counters are exercised."""

    name = "anthropic"
    is_billable = True

    def reply(self, **kwargs):
        r = super().reply(**kwargs)
        r.provider = "anthropic"
        r.model = "test-model"
        r.usage = AIUsage(input_tokens=1000, output_tokens=80, cache_read_input_tokens=600)
        return r

    def summarize(self, **kwargs):
        r = super().summarize(**kwargs)
        r.usage = AIUsage(input_tokens=1500, output_tokens=200)
        return r


class SonnetBillableProvider(BillableCountingProvider):
    """Billable test provider that reports a real, priced model id."""

    model = "claude-sonnet-5-5"

    def reply(self, **kwargs):
        r = super().reply(**kwargs)
        r.model = self.model
        return r


class FailingProvider(AIProvider):
    name = "anthropic"
    is_billable = True

    def __init__(self):
        self.reply_calls = 0
        self.summary_calls = 0

    def reply(self, **kwargs):
        self.reply_calls += 1
        raise AIError("status_500")

    def summarize(self, **kwargs):
        self.summary_calls += 1
        raise AIError("status_500")


class ExplodingProvider(AIProvider):
    """Fails the test if any AI call happens."""

    name = "explode"

    def reply(self, **kwargs):
        raise AssertionError("unexpected AI reply call")

    def summarize(self, **kwargs):
        raise AssertionError("unexpected AI summary call")
