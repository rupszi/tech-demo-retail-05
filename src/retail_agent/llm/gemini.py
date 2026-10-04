"""Gemini through the `google-genai` SDK."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from google import genai
from google.genai import errors, types

from retail_agent.config import Settings
from retail_agent.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

_TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}
_TIMEOUT_MS = 90_000


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
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
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
        )
        try:
            response = self._client.models.generate_content(
                model=self.name, contents=_to_contents(messages), config=config
            )
        except errors.APIError as e:
            raise LLMError(
                f"{self.name}: {e}",
                transient=e.code in _TRANSIENT_CODES,
                retry_after=_retry_after(e),
            ) from e
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            raise LLMError(f"{self.name}: network error: {e}", transient=True) from e
        return self._parse(response)

    def _parse(self, response: types.GenerateContentResponse) -> LLMResponse:
        candidate = response.candidates[0] if response.candidates else None
        parts = (candidate.content.parts if candidate and candidate.content else None) or []
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
            reason = candidate.finish_reason if candidate else "no candidate"
            raise LLMError(f"{self.name}: empty response ({reason})", transient=True)
        usage = response.usage_metadata
        return LLMResponse(
            text=text,
            tool_calls=calls,
            raw=candidate.content.model_dump_json(exclude_none=True),
            model=response.model_version or self.name,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                if usage
                else 0
            ),
        )


def _retry_after(error: errors.APIError) -> float | None:
    """The wait the API asks for on a rate limit, e.g. {"retryDelay": "3s"}."""
    details = error.details if isinstance(error.details, dict) else {}
    for item in details.get("error", {}).get("details", []):
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


def _to_contents(messages: Sequence[Message]) -> list[types.Content]:
    contents: list[types.Content] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=message["text"])]))
        elif role == "assistant":
            if message.get("raw"):
                contents.append(types.Content.model_validate_json(message["raw"]))
            else:
                contents.append(
                    types.Content(role="model", parts=[types.Part(text=message["text"])])
                )
        else:  # tool result; results for one assistant message travel together
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message["call_id"], name=message["name"], response=message["result"]
                )
            )
            last = contents[-1] if contents else None
            if last and last.role == "user" and last.parts and last.parts[-1].function_response:
                last.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
    return contents
