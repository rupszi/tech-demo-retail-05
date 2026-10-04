"""Test doubles for the model."""

from __future__ import annotations

from retail_agent.llm import LLMError, LLMResponse, ToolCall


def call(name: str, call_id: str = "c1", **args) -> ToolCall:
    return ToolCall(id=call_id, name=name, args=args)


def says(text: str = "", *calls: ToolCall, tokens: int = 100, out: int = 0) -> LLMResponse:
    """A model response: text and/or tool calls, with `tokens` read and `out` written."""
    return LLMResponse(
        text=text, tool_calls=tuple(calls), model="fake", input_tokens=tokens, output_tokens=out
    )


class ScriptedLLM:
    """Replays a list of responses (or raises the exceptions in it) and records what it was sent."""

    name = "fake"

    def __init__(self, *script: LLMResponse | BaseException):
        self._script = list(script)
        self.requests: list[dict] = []

    def generate(self, system, messages, tools, time_left=None):
        self.requests.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "time_left": time_left,
            }
        )
        if not self._script:
            raise AssertionError("ScriptedLLM ran out of responses")
        step = self._script.pop(0)
        if isinstance(step, BaseException):  # includes KeyboardInterrupt, for Ctrl-C tests
            raise step
        return step

    @property
    def calls(self) -> int:
        return len(self.requests)


class SlowModel(ScriptedLLM):
    """A scripted model whose every call takes `seconds` on a fake clock (anything with `.now`)."""

    def __init__(self, clock, seconds: float, *script: LLMResponse | BaseException):
        super().__init__(*script)
        self._clock, self._seconds = clock, seconds

    def generate(self, system, messages, tools, time_left=None):
        self._clock.now += self._seconds
        return super().generate(system, messages, tools, time_left)


def transient(message: str = "503 unavailable") -> LLMError:
    return LLMError(message, transient=True)


def permanent(message: str = "400 bad request") -> LLMError:
    return LLMError(message, transient=False)
