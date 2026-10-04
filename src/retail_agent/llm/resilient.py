"""Keeps the conversation alive when the model provider has a bad moment.

- Transient failures (timeouts, server errors) are retried with exponential backoff and jitter.
- When a model keeps failing, the next model in the list is tried.
- A rate-limited model is rested for as long as the provider asks, and skipped meanwhile, so a
  model that said "come back in 40 seconds" is not called again until then.
- If nothing works the caller gets `LLMUnavailable` and can tell the user, instead of crashing.
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
        short_wait: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_failure: RetryHook | None = None,
    ):
        self._models = list(models)
        self._attempts = attempts
        self._base_delay = base_delay
        self._max_delay = max_delay  # the longest we make a user wait for a model to come back
        self._short_wait = short_wait  # a requested wait this short is simply waited out
        self._sleep = sleep
        self._clock = clock
        self._resting_until: dict[int, float] = {}  # by model object, not name
        self._last_error: LLMError | None = None
        self.on_failure = on_failure  # called for every failed attempt, for tracing
        self.name = self._models[0].name

    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> LLMResponse:
        self._last_error = None
        for model in self._models:
            if self._rest_left(model) > 0:
                continue
            response = self._try(model, system, messages, tools)
            if response is not None:
                return response

        # Every model failed or is resting. If one comes back soon, wait for it once.
        soonest = min(self._models, key=self._rest_left)
        wait = self._rest_left(soonest)
        if 0 < wait <= self._max_delay:
            self._sleep(wait + 0.5)
            response = self._try(soonest, system, messages, tools)
            if response is not None:
                return response
            wait = self._rest_left(min(self._models, key=self._rest_left))
        raise LLMUnavailable(
            f"All models failed. Last error: {self._last_error}", retry_after=wait or None
        ) from self._last_error

    def _rest_left(self, model: LLM) -> float:
        return max(0.0, self._resting_until.get(id(model), 0.0) - self._clock())

    def _try(
        self, model: LLM, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> LLMResponse | None:
        """Call one model, retrying transient failures. None means: move on to another model."""
        for attempt in range(1, self._attempts + 1):
            try:
                return model.generate(system, messages, tools)
            except LLMError as e:
                self._last_error = e
                if self.on_failure:
                    self.on_failure(model.name, attempt, e)
                if e.retry_after is not None and e.retry_after > self._short_wait:
                    self._resting_until[id(model)] = self._clock() + e.retry_after
                    return None
                if not e.transient or attempt == self._attempts:
                    return None
                if e.retry_after is not None:
                    self._sleep(e.retry_after + 0.5)
                else:
                    delay = min(self._max_delay, self._base_delay * 2 ** (attempt - 1))
                    self._sleep(delay * random.uniform(0.5, 1.5))
        return None
