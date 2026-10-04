import pytest

from retail_agent.llm import LLMError, LLMUnavailable, ResilientLLM
from retail_agent.llm.gemini import _to_contents

from .fakes import ScriptedLLM, permanent, says, transient


def resilient(*models, **kwargs):
    sleeps = []
    llm = ResilientLLM(models, sleep=sleeps.append, **kwargs)
    return llm, sleeps


def test_success_needs_no_retry():
    model = ScriptedLLM(says("hi"))
    llm, sleeps = resilient(model)
    assert llm.generate("s", [], []).text == "hi"
    assert model.calls == 1 and sleeps == []


def test_transient_errors_are_retried_with_growing_backoff():
    model = ScriptedLLM(transient(), transient(), says("ok"))
    llm, sleeps = resilient(model, base_delay=2.0)
    assert llm.generate("s", [], []).text == "ok"
    assert model.calls == 3
    assert 1.0 <= sleeps[0] <= 3.0 and 2.0 <= sleeps[1] <= 6.0  # 2s and 4s, with jitter


def test_backoff_is_capped():
    model = ScriptedLLM(*[transient()] * 5, says("ok"))
    llm, sleeps = resilient(model, attempts=6, base_delay=2.0, max_delay=5.0)
    llm.generate("s", [], [])
    assert max(sleeps) <= 5.0 * 1.5


def test_falls_back_to_the_next_model_when_one_keeps_failing():
    primary = ScriptedLLM(transient(), transient(), transient())
    fallback = ScriptedLLM(says("from fallback"))
    llm, _ = resilient(primary, fallback)
    assert llm.generate("s", [], []).text == "from fallback"
    assert primary.calls == 3 and fallback.calls == 1


def test_permanent_error_skips_retries_and_falls_back():
    primary = ScriptedLLM(permanent())
    fallback = ScriptedLLM(says("ok"))
    llm, sleeps = resilient(primary, fallback)
    assert llm.generate("s", [], []).text == "ok"
    assert primary.calls == 1 and sleeps == []


def test_raises_unavailable_when_everything_fails():
    llm, _ = resilient(ScriptedLLM(*[transient()] * 3), ScriptedLLM(*[transient()] * 3))
    with pytest.raises(LLMUnavailable):
        llm.generate("s", [], [])


def test_the_wait_asked_for_by_the_provider_is_honoured():
    model = ScriptedLLM(LLMError("429", transient=True, retry_after=3.0), says("ok"))
    llm, sleeps = resilient(model)
    assert llm.generate("s", [], []).text == "ok" and sleeps == [3.5]


def test_a_long_requested_wait_falls_back_instead_of_waiting():
    primary = ScriptedLLM(LLMError("429", transient=True, retry_after=45.0))
    fallback = ScriptedLLM(says("from fallback"))
    llm, sleeps = resilient(primary, fallback, max_delay=20.0)
    assert llm.generate("s", [], []).text == "from fallback"
    assert primary.calls == 1 and sleeps == []


def test_retry_delay_is_read_from_a_gemini_rate_limit_error():
    from types import SimpleNamespace

    from retail_agent.llm.gemini import _retry_after

    def error(details):
        return SimpleNamespace(details=details)

    quota = {"error": {"details": [{"@type": "x/QuotaFailure"}, {"retryDelay": "3s"}]}}
    assert _retry_after(error(quota)) == 3.0
    assert _retry_after(error({"error": {"details": [{"retryDelay": "0.25s"}]}})) == 0.25
    assert _retry_after(error({"error": {}})) is None
    assert _retry_after(error(None)) is None


def test_every_failure_is_reported_for_tracing():
    seen = []
    llm, _ = resilient(ScriptedLLM(transient(), says("ok")))
    llm.on_failure = lambda model, attempt, error: seen.append((model, attempt, error.transient))
    llm.generate("s", [], [])
    assert seen == [("fake", 1, True)]


def test_messages_are_converted_to_gemini_contents():
    contents = _to_contents(
        [
            {"role": "user", "text": "revenue?"},
            {"role": "assistant", "text": "", "tool_calls": [], "raw": None},
            {"role": "tool", "call_id": "a", "name": "run_sql", "result": {"rows": 1}},
            {"role": "tool", "call_id": "b", "name": "run_sql", "result": {"rows": 2}},
            {"role": "user", "text": "thanks"},
        ]
    )
    assert [c.role for c in contents] == ["user", "model", "user", "user"]
    responses = contents[2].parts
    assert [p.function_response.id for p in responses] == ["a", "b"]  # grouped in one turn
