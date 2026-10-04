"""Gemini through the `google-genai` SDK."""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx
from google import genai
from google.genai import errors, types

from retail_agent.config import Settings
from retail_agent.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

_TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}  # worth trying again; anything else is not
_TIMEOUT_MS = 90_000  # the longest one call may take when the question sets no tighter limit
# Finish and block reasons that mean the provider refused the content. Asking the same model
# again would be refused again.
_REFUSED = ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED", "SPII")


def create_client(settings: Settings) -> genai.Client:
    options = types.HttpOptions(timeout=_TIMEOUT_MS)
    if settings.gemini_auth == "vertex":
        return genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
            http_options=options,
        )
    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not set. Add it to your .env file.", transient=False)
    return genai.Client(api_key=settings.gemini_api_key, http_options=options)


class GeminiLLM:
    def __init__(self, client: genai.Client, model: str, temperature: float = 0.2):
        self._client = client
        self.name = model
        self._temperature = temperature

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        time_left: float | None = None,
    ) -> LLMResponse:
        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
            tools=[types.Tool(function_declarations=declarations)] if declarations else None,
            # We run the tools ourselves, inside the graph, so every call is checked and traced.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            # Never wait for a response longer than the question has left.
            http_options=types.HttpOptions(timeout=_timeout_ms(time_left)),
        )
        try:
            response = self._client.models.generate_content(
                model=self.name, contents=_to_contents(messages), config=config
            )
            return self._parse(response)
        except LLMError:
            raise
        except errors.APIError as e:
            raise LLMError(
                f"{self.name}: {e}",
                transient=e.code in _TRANSIENT_CODES,
                retry_after=_retry_after(e),
            ) from e
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            raise LLMError(f"{self.name}: network error: {e}", transient=True) from e
        except Exception as e:  # noqa: BLE001 - one model's failure must not end the turn
            # Anything unexpected is this model's failure: the next model in the list gets its
            # turn, and the same call is not repeated.
            message = f"{self.name}: unexpected error: {type(e).__name__}: {e}"
            raise LLMError(message, transient=False) from e

    def _parse(self, response: types.GenerateContentResponse) -> LLMResponse:
        candidate = response.candidates[0] if response.candidates else None
        parts = (candidate.content.parts if candidate and candidate.content else None) or []
        # The model's thinking is not part of the answer.
        text = "".join(p.text for p in parts if p.text and not p.thought)
        calls = tuple(
            ToolCall(
                id=p.function_call.id or f"call_{i}",
                name=p.function_call.name or "",
                args=dict(p.function_call.args or {}),
            )
            for i, p in enumerate(parts)
            if p.function_call
        )
        if not text and not calls:
            # Nothing usable came back. Usually a hiccup worth another try; but content that the
            # provider refused will be refused again, so that goes straight to the next model.
            feedback = response.prompt_feedback
            reason = str(
                (candidate.finish_reason if candidate else None)
                or (feedback.block_reason if feedback else None)
                or "no candidate"
            )
            refused = any(word in reason.upper() for word in _REFUSED)
            raise LLMError(f"{self.name}: empty response ({reason})", transient=not refused)
        usage = response.usage_metadata
        return LLMResponse(
            text=text,
            tool_calls=calls,
            # Kept as the provider sent it and sent back unchanged on the next call: Gemini
            # attaches signatures to tool calls and rejects a history that has lost them.
            raw=candidate.content.model_dump_json(exclude_none=True),
            model=response.model_version or self.name,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                if usage
                else 0
            ),
        )


def _timeout_ms(time_left: float | None) -> int:
    """The request timeout: the default, or what the question has left if that is less."""
    if time_left is None:
        return _TIMEOUT_MS
    return int(min(_TIMEOUT_MS, max(1.0, time_left) * 1000))


def _retry_after(error: errors.APIError) -> float | None:
    """The wait the API asks for on a rate limit, e.g. {"retryDelay": "3s"}."""
    details = error.details if isinstance(error.details, dict) else {}
    inner = details.get("error")
    items = inner.get("details") if isinstance(inner, dict) else None
    for item in items if isinstance(items, list) else []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                seconds = float(delay[:-1])
            except ValueError:
                return None
            # A negative or non-finite delay is not a wait anyone can make.
            return seconds if math.isfinite(seconds) and seconds >= 0 else None
    return None


def _to_contents(messages: Sequence[Message]) -> list[types.Content]:
    """Turn the provider-independent messages into the form Gemini expects."""
    contents: list[types.Content] = []
    order: dict[str, int] = {}  # where each tool call stood in the model turn that made it
    for message in messages:
        role = message["role"]
        if role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=message["text"])]))
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            order = {c["id"]: i for i, c in enumerate(calls)}
            if message.get("raw"):
                contents.append(types.Content.model_validate_json(message["raw"]))
            else:
                # A turn that did not come from Gemini (written by the application, or by another
                # provider): rebuilt from the text and the tool calls.
                parts = [types.Part(text=message["text"])] if message["text"] or not calls else []
                parts += [
                    types.Part(
                        function_call=types.FunctionCall(id=c["id"], name=c["name"], args=c["args"])
                    )
                    for c in calls
                ]
                contents.append(types.Content(role="model", parts=parts))
        else:  # tool result; results for one assistant message travel together
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message["call_id"], name=message["name"], response=message["result"]
                )
            )
            last = contents[-1] if contents else None
            if last and last.role == "user" and last.parts and last.parts[-1].function_response:
                last.parts.append(part)
                # Results go back in the order the calls were made, whatever order they finished
                # in: a delete is answered only after the user has decided.
                last.parts.sort(key=lambda p: order.get(p.function_response.id, len(order)))
            else:
                contents.append(types.Content(role="user", parts=[part]))
    return contents
