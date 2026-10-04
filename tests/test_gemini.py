"""The Gemini adapter, against a stand-in for the SDK client that returns real SDK objects.

The agent tests use a scripted model, so they never touch this file's subject: what is sent to
Gemini, and how what comes back is read. No network is used.
"""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from retail_agent.llm import LLMError, ToolSpec
from retail_agent.llm.gemini import GeminiLLM, _retry_after, _to_contents

TOOL = ToolSpec("run_sql", "Run a query.", {"type": "object", "properties": {}})
QUESTION = [{"role": "user", "text": "How many orders?"}]
COUNT = "SELECT COUNT(*) AS n FROM orders"


class FakeModels:
    """Replays responses (or raises errors) and records every request."""

    def __init__(self, *script):
        self.script, self.sent = list(script), []

    def generate_content(self, model, contents, config):
        self.sent.append(SimpleNamespace(model=model, contents=list(contents), config=config))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def gemini(*script):
    models = FakeModels(*script)
    return GeminiLLM(SimpleNamespace(models=models), "gemini-test"), models


def response(*parts, tokens=(100, 10, 5), finish="STOP"):
    content = types.Content(role="model", parts=list(parts))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=content, finish_reason=finish)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=tokens[0],
            candidates_token_count=tokens[1],
            thoughts_token_count=tokens[2],
        ),
        model_version="gemini-test-001",
    )


def text(value, thought=None):
    return types.Part(text=value, thought=thought)


def tool_call(sql, call_id=None, signature=None):
    wanted = types.FunctionCall(id=call_id, name="run_sql", args={"sql": sql})
    return types.Part(function_call=wanted, thought_signature=signature)


def api_error(code, details=None):
    return errors.APIError(code, {"error": {"code": code, "message": "x", "details": details}})


def failure(llm):
    with pytest.raises(LLMError) as caught:
        llm.generate("s", QUESTION, [])
    return caught.value


def test_the_request_carries_the_instructions_the_tools_and_the_time_left():
    llm, models = gemini(response(text("42")))
    llm.generate("You are an analyst.", QUESTION, [TOOL], time_left=12.5)
    sent = models.sent[0]
    assert sent.model == "gemini-test" and sent.config.system_instruction == "You are an analyst."
    assert [d.name for d in sent.config.tools[0].function_declarations] == ["run_sql"]
    assert sent.config.automatic_function_calling.disable is True  # the graph runs the tools
    assert sent.config.http_options.timeout == 12_500  # never longer than the question has left
    assert [(c.role, c.parts[0].text) for c in sent.contents] == [("user", "How many orders?")]


def test_thinking_is_left_out_of_the_answer_but_its_tokens_are_counted():
    llm, _ = gemini(response(text("Let me think.", thought=True), text("42 orders.")))
    result = llm.generate("s", QUESTION, [])
    assert result.text == "42 orders." and result.model == "gemini-test-001"
    assert (result.input_tokens, result.output_tokens) == (100, 15)


def test_a_tool_call_is_sent_back_exactly_as_it_came():
    llm, models = gemini(response(tool_call(COUNT, "abc", b"signed")), response(text("One.")))
    first = llm.generate("s", QUESTION, [TOOL])
    assert [(c.id, c.name, c.args) for c in first.tool_calls] == [
        ("abc", "run_sql", {"sql": COUNT})
    ]
    result = {"role": "tool", "call_id": "abc", "name": "run_sql", "result": {"rows": [[1]]}}
    llm.generate("s", [*QUESTION, first.to_message(), result], [TOOL])
    user, model, tool = models.sent[1].contents
    assert (user.role, model.role, tool.role) == ("user", "model", "user")
    assert model.parts[0].function_call.args == {"sql": COUNT}
    assert model.parts[0].thought_signature == b"signed"  # Gemini rejects a history without it
    sent_back = tool.parts[0].function_response
    assert (sent_back.id, sent_back.name, sent_back.response) == ("abc", "run_sql", {"rows": [[1]]})


def test_calls_that_arrive_without_an_id_are_given_one_each():
    llm, _ = gemini(response(tool_call("SELECT 1"), tool_call("SELECT 2")))
    assert [c.id for c in llm.generate("s", QUESTION, [TOOL]).tool_calls] == ["call_0", "call_1"]


def test_results_go_back_in_the_order_the_calls_were_made():
    asked = {
        "role": "assistant",
        "text": "",
        "raw": None,
        "tool_calls": [
            {"id": "d", "name": "delete_reports", "args": {"all_reports": True}},
            {"id": "q", "name": "run_sql", "args": {"sql": COUNT}},
        ],
    }
    # the query finished first; the delete is answered only after the user has decided
    done = [
        {"role": "tool", "call_id": "q", "name": "run_sql", "result": {"row_count": 1}},
        {"role": "tool", "call_id": "d", "name": "delete_reports", "result": {"deleted": 0}},
    ]
    _, model, results = _to_contents([*QUESTION, asked, *done])
    assert [p.function_call.name for p in model.parts] == ["delete_reports", "run_sql"]
    assert [p.function_response.name for p in results.parts] == ["delete_reports", "run_sql"]


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (api_error(503), True),
        (api_error(429), True),
        (api_error(400), False),
        (api_error(403), False),
        (httpx.ReadTimeout("timed out"), True),
        (ConnectionError("connection reset"), True),
        (ValueError("something unexpected inside the SDK"), False),
    ],
)
def test_failures_say_whether_the_same_call_is_worth_repeating(error, transient):
    llm, _ = gemini(error)
    assert failure(llm).transient is transient


def test_a_rate_limit_carries_the_wait_the_provider_asks_for():
    llm, _ = gemini(api_error(429, [{"@type": "x/QuotaFailure"}, {"retryDelay": "41s"}]))
    assert failure(llm).retry_after == 41.0


@pytest.mark.parametrize(
    "details",
    [
        None,
        "plain text",
        {"error": None},
        {"error": "text"},
        {"error": {"details": None}},
        {"error": {"details": [{"retryDelay": "-2s"}]}},
        {"error": {"details": [{"retryDelay": "nans"}]}},
        {"error": {"details": [{"retryDelay": "infs"}]}},
        {"error": {"details": [{"retryDelay": "soon"}]}},
    ],
)
def test_a_malformed_wait_is_ignored_and_does_not_raise(details):
    assert _retry_after(SimpleNamespace(details=details)) is None


def test_an_empty_response_is_worth_another_try_but_refused_content_is_not():
    empty, _ = gemini(response())
    refused, _ = gemini(response(finish="SAFETY"))
    feedback = types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")
    blocked, _ = gemini(types.GenerateContentResponse(candidates=[], prompt_feedback=feedback))
    assert failure(empty).transient is True
    assert failure(refused).transient is False and failure(blocked).transient is False


def test_a_whole_question_through_the_real_adapter(chat):
    """The agent loop with the real adapter in place of the scripted model."""
    llm, models = gemini(response(tool_call(COUNT, "c1")), response(text("There are many orders.")))
    result = chat(llm=llm).ask("How many orders do we have?")
    assert result.answer == "There are many orders." and result.trace["tokens_in"] == 200
    declared = [d.name for d in models.sent[0].config.tools[0].function_declarations]
    assert declared == ["run_sql", "save_report", "list_reports", "get_report", "delete_reports"]
    assert "Alder & Finch" in models.sent[0].config.system_instruction  # the user's brands
    assert [c.role for c in models.sent[1].contents] == ["user", "model", "user"]
    sent_back = models.sent[1].contents[2].parts[0].function_response
    assert sent_back.id == "c1" and sent_back.response["row_count"] == 1
