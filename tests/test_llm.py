import pytest

from retail_agent.llm import LLMError, LLMUnavailable, ResilientLLM
from retail_agent.llm.gemini import _timeout_ms, _to_contents

from .fakes import ScriptedLLM, SlowModel, permanent, says, transient


class FakeTime:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def resilient(*models, **kwargs):
    fake = FakeTime()
    llm = ResilientLLM(models, sleep=fake.sleep, clock=fake.clock, **kwargs)
    llm.time = fake
    return llm, fake.sleeps


def rate_limited(seconds):
    return LLMError("429 rate limit", transient=True, retry_after=seconds)


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
    model = ScriptedLLM(*[transient()] * 7, says("ok"))
    llm, sleeps = resilient(model, attempts=8, base_delay=2.0)
    llm.generate("s", [], [])
    assert max(sleeps) <= 20.0 * 1.5


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


def test_a_short_wait_asked_for_by_the_provider_is_waited_out():
    model = ScriptedLLM(rate_limited(3.0), says("ok"))
    llm, sleeps = resilient(model)
    assert llm.generate("s", [], []).text == "ok" and sleeps == [3.5]


def test_a_rate_limited_model_is_rested_and_skipped_until_it_may_be_called_again():
    primary = ScriptedLLM(rate_limited(40.0), says("primary is back"))
    fallback = ScriptedLLM(says("fallback 1"), says("fallback 2"))
    llm, sleeps = resilient(primary, fallback)

    assert llm.generate("s", [], []).text == "fallback 1"  # falls back at once, no waiting
    assert llm.generate("s", [], []).text == "fallback 2"  # primary is not even tried
    assert primary.calls == 1 and sleeps == []

    llm.time.now += 41  # the rest period is over
    assert llm.generate("s", [], []).text == "primary is back"


def test_when_every_model_is_rate_limited_the_soonest_one_is_waited_for():
    primary = ScriptedLLM(rate_limited(50.0))
    fallback = ScriptedLLM(rate_limited(12.0), says("after the wait"))
    llm, sleeps = resilient(primary, fallback)
    announced = []
    llm.on_wait = announced.append
    assert llm.generate("s", [], []).text == "after the wait"
    assert sleeps == [12.5] and primary.calls == 1 and announced == [12.0]


def test_when_every_model_is_rate_limited_for_long_the_user_is_told_how_long():
    llm, sleeps = resilient(ScriptedLLM(rate_limited(600.0)), ScriptedLLM(rate_limited(450.0)))
    with pytest.raises(LLMUnavailable) as e:
        llm.generate("s", [], [])
    assert sleeps == [] and e.value.retry_after == 450.0


def test_a_model_that_only_failed_does_not_hide_one_that_is_due_back_soon():
    """Whether the rest ends during the other model's retries or after them, it gets its turn."""
    rested = ScriptedLLM(rate_limited(8), says("back again"))
    broken = ScriptedLLM(transient(), transient(), transient())
    llm, sleeps = resilient(rested, broken)
    assert llm.generate("s", [], []).text == "back again"
    assert rested.calls == 2 and broken.calls == 3


def test_a_model_whose_rest_ended_while_others_were_tried_is_called_without_waiting():
    fake = FakeTime()
    rested = ScriptedLLM(rate_limited(8), says("back again"))
    slow = SlowModel(fake, 20, permanent())  # takes longer than the rest lasts
    llm = ResilientLLM([rested, slow], sleep=fake.sleep, clock=fake.clock)
    assert llm.generate("s", [], []).text == "back again"
    assert fake.sleeps == []  # nothing left to wait for


def test_a_wait_that_would_end_after_the_deadline_is_not_started():
    model = ScriptedLLM(rate_limited(10), says("never asked"))
    llm, sleeps = resilient(model)
    with pytest.raises(LLMUnavailable) as e:
        llm.generate("s", [], [], time_left=10.2)  # the wait is 10 seconds plus a margin
    assert sleeps == [] and model.calls == 1 and e.value.retry_after == 10


def test_each_call_is_told_how_long_it_may_take():
    model = ScriptedLLM(says("hi"), says("hi"))
    llm, _ = resilient(model)
    llm.generate("s", [], [], time_left=42)
    llm.generate("s", [], [])
    assert [r["time_left"] for r in model.requests] == [42, None]


def test_no_retry_starts_once_the_time_is_used_up():
    fake = FakeTime()
    slow = SlowModel(fake, 50, transient(), says("too late"))  # every call takes 50 seconds
    backup = ScriptedLLM(says("never asked"))
    llm = ResilientLLM([slow, backup], sleep=fake.sleep, clock=fake.clock)
    with pytest.raises(LLMUnavailable):
        llm.generate("s", [], [], time_left=40)
    assert slow.calls == 1 and backup.calls == 0 and fake.sleeps == []


def test_a_rate_limit_longer_than_the_time_left_is_not_waited_for():
    models = [ScriptedLLM(rate_limited(30)), ScriptedLLM(rate_limited(30))]
    llm, sleeps = resilient(*models)
    with pytest.raises(LLMUnavailable) as e:
        llm.generate("s", [], [], time_left=10)
    assert sleeps == [] and e.value.retry_after == 30


def test_a_gemini_call_never_waits_longer_than_the_question_has_left():
    assert _timeout_ms(None) == 90_000  # no deadline: the default
    assert _timeout_ms(12.5) == 12_500
    assert _timeout_ms(500) == 90_000  # never longer than the default
    assert _timeout_ms(0.2) == 1_000  # and never so short that nothing can answer


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


def test_a_refused_key_is_reported_as_such_when_every_model_fails():
    refused = LLMError("400 API key not valid", transient=False, auth=True)
    llm, sleeps = resilient(ScriptedLLM(refused), ScriptedLLM(refused))
    with pytest.raises(LLMUnavailable) as caught:
        llm.generate("s", [], [])
    assert caught.value.auth is True and sleeps == []  # nothing was retried or waited for
