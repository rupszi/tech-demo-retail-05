"""Keeps the conversation alive when the model provider has a bad moment.

Transient failures (rate limits, timeouts, server errors) are retried with exponential backoff and
jitter, or after the wait the provider asks for. When a model keeps failing, or asks for a wait
longer than a user should sit through, the next model in the list is tried. If nothing works the
caller gets `LLMUnavailable` and can tell the user, instead of the application crashing.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence

from retail_agent.llm.base import LLM, LLMError, LLMResponse, LLMUnavailable, Message, ToolSpec

RetryHook = Callable[[str, int, LLMError], None]


class ResilientLLM:
    def __init__(
        self,
        models: Sequence[LLM],
        *,
        attempts: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        on_failure: RetryHook | None = None,
    ):
        self._models = list(models)
        self._attempts = attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep
        self.on_failure = on_failure  # called for every failed attempt, for tracing
        self.name = self._models[0].name

    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> LLMResponse:
        last_error: LLMError | None = None
        for model in self._models:
            for attempt in range(1, self._attempts + 1):
                try:
                    return model.generate(system, messages, tools)
                except LLMError as e:
                    last_error = e
                    if self.on_failure:
                        self.on_failure(model.name, attempt, e)
                    if not e.transient or attempt == self._attempts:
                        break  # give up on this model and fall back to the next one
                    if e.retry_after is not None:
                        if e.retry_after > self._max_delay:
                            break  # falling back now is better than making the user wait
                        self._sleep(e.retry_after + 0.5)
                    else:
                        delay = min(self._max_delay, self._base_delay * 2 ** (attempt - 1))
                        self._sleep(delay * random.uniform(0.5, 1.5))
        raise LLMUnavailable(f"All models failed. Last error: {last_error}") from last_error
