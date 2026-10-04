"""The model interface the agent depends on, independent of any provider.

Conversation messages are plain dictionaries so they can be stored in the graph state:

    {"role": "user", "text": "..."}
    {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "args"}], "raw": "..."}
    {"role": "tool", "call_id": "...", "name": "...", "result": {...}}

`raw` is the provider's own form of an assistant message. It is sent back unchanged so that
provider-specific fields (for Gemini, the signatures attached to tool calls) are preserved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

Message = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    raw: str | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0  # includes the model's thinking tokens

    def to_message(self) -> Message:
        calls = [{"id": c.id, "name": c.name, "args": c.args} for c in self.tool_calls]
        return {"role": "assistant", "text": self.text, "tool_calls": calls, "raw": self.raw}


class LLMError(Exception):
    """A model call failed.

    `transient` means the same request may succeed if tried again. `retry_after` is how long the
    provider asked us to wait, when it said so (rate limits do).
    """

    def __init__(self, message: str, *, transient: bool, retry_after: float | None = None):
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    """Every model and every retry failed. `retry_after` is set when rate limits are the cause."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message, transient=True, retry_after=retry_after)


class LLM(Protocol):
    name: str

    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> LLMResponse: ...
