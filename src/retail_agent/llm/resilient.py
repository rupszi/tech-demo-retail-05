"""Keeps the conversation alive when the model provider has a bad moment.

- Transient failures (timeouts, server errors) are retried with exponential backoff and jitter.
- When a model keeps failing, the next model in the list is tried.
- A rate-limited model is rested for as long as the provider asks, and skipped meanwhile, so a
  model that said "come back in 40 seconds" is not called again until then.
- All of it stops at the caller's deadline: no retry, wait or fallback starts once the time a
  question has left is used up.
- If nothing works the caller gets `LLMUnavailable` and can tell the user, instead of crashing.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Sequence

from retail_agent.llm.base import LLM, LLMError, LLMResponse, LLMUnavailable, Message, ToolSpec

RetryHook = Callable[[str, int, LLMError], None]
_BACKOFF_CAP = 20.0  # seconds; the longest pause between two retries of one model


class ResilientLLM:
    def __init__(
        self,
        models: Sequence[LLM],
        *,
        attempts: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
        short_wait: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_failure: RetryHook | None = None,
        on_wait: Callable[[float], None] | None = None,
    ):
        self._models = list(models)
        self._attempts = attempts
        self._base_delay = base_delay
        self._max_delay = max_delay  # the longest we make a user wait for a model to come back
        self._short_wait = short_wait  # a requested wait this short is simply waited out
        self._sleep = sleep  # injected, like the clock, so the tests never really wait
        self._clock = clock
        # Keyed by the model object and not its name: two entries with the same name would
        # otherwise rest each other.
        self._resting_until: dict[int, float] = {}
        self._deadline = math.inf
        self._last_error: LLMError | None = None
        self.on_failure = on_failure  # called for every failed attempt, for tracing
        self.on_wait = on_wait  # called before a long wait, so the interface can say so
        self.name = self._models[0].name  # the first choice; shown in the interface header

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        time_left: float | None = None,
    ) -> LLMResponse:
        self._last_error = None
        started = self._clock()
        self._deadline = math.inf if time_left is None else started + time_left
        for model in self._models:
            if self._rest_left(model) > 0:
                continue  # it asked not to be called yet
            response = self._try(model, system, messages, tools)
            if response is not None:
                return response

        # Nothing answered. A rate-limited model gets one more chance: the one that is due back
        # first, among those that were resting when this call began or were rested during it.
        # (A model that simply failed has no time to come back at.) Its rest may already be over,
        # or it is waited for, unless that would take too long or run past the deadline.
        rested = [m for m in self._models if self._resting_until.get(id(m), 0.0) > started]
        if rested:
            soonest = min(rested, key=self._rest_left)
            wait = self._rest_left(soonest)
            if wait == 0 or (wait <= self._max_delay and wait + 0.5 < self._time_left()):
                if wait > 0:
                    if self.on_wait:
                        self.on_wait(wait)
                    self._sleep(wait + 0.5)  # a little longer than asked, to be on the safe side
                response = self._try(soonest, system, messages, tools)
                if response is not None:
                    return response
        wait = self._shortest_rest()
        # `retry_after` lets the caller tell the user how long the rate limit lasts.
        raise LLMUnavailable(
            f"All models failed. Last error: {self._last_error}", retry_after=wait or None
        ) from self._last_error

    def _rest_left(self, model: LLM) -> float:
        return max(0.0, self._resting_until.get(id(model), 0.0) - self._clock())

    def _shortest_rest(self) -> float:
        """How long until the first resting model may be called again; 0 if none is resting."""
        return min((r for r in map(self._rest_left, self._models) if r > 0), default=0.0)

    def _time_left(self) -> float:
        return self._deadline - self._clock()

    def _try(
        self, model: LLM, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> LLMResponse | None:
        """Call one model, retrying transient failures. None means: move on to another model."""
        for attempt in range(1, self._attempts + 1):
            left = self._time_left()
            if left <= 0:
                return None  # out of time; the caller reports it
            try:
                # The model gets what is left as its own timeout, so a call that hangs cannot
                # outlive the question.
                return model.generate(
                    system, messages, tools, time_left=None if math.isinf(left) else left
                )
            except LLMError as e:
                self._last_error = e
                if self.on_failure:
                    self.on_failure(model.name, attempt, e)
                if e.retry_after is not None and e.retry_after > self._short_wait:
                    # A rate limit with a long wait: rest this model and let the next one answer.
                    self._resting_until[id(model)] = self._clock() + e.retry_after
                    return None
                if not e.transient or attempt == self._attempts:
                    return None  # retrying cannot help, or the attempts are used up
                if e.retry_after is not None:
                    pause = e.retry_after + 0.5  # a short wait the provider asked for
                else:
                    # Exponential backoff with jitter, so retries do not arrive in step.
                    delay = min(_BACKOFF_CAP, self._base_delay * 2 ** (attempt - 1))
                    pause = delay * random.uniform(0.5, 1.5)
                if pause >= self._time_left():
                    return None  # the pause alone would run past the deadline
                self._sleep(pause)
        return None
