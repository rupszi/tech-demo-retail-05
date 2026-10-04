"""High-stakes oversight: reports are only deleted after the user confirms, never by the model."""

import pytest

from retail_agent.agent.graph import MSG_UNAVAILABLE
from retail_agent.llm import LLMUnavailable

from .fakes import call, says

DELETE_DRIFTLINE = says("", call("delete_reports", mentioning="Driftline"))
# The model asks for a query and a delete in one step.
QUERY_AND_DELETE = says(
    "",
    call("run_sql", "c1", sql="SELECT COUNT(*) AS n FROM orders"),
    call("delete_reports", "c2", mentioning="Driftline"),
)
BOTH = "How many orders do we have? And delete the reports mentioning Driftline."


@pytest.fixture
def session(chat):
    """Alice has three reports (two mention Driftline); Bob has one that also mentions it."""

    def start(*script):
        s = chat(*script)
        s.reports.save("alice", s.conversation_id, "Q1 review", "Driftline returns are high.")
        s.reports.save("alice", s.conversation_id, "Texas deep dive", "Texas buys fewer items.")
        s.reports.save("alice", "an-older-conversation", "Brand comparison", "Driftline vs Kestrel")
        s.reports.save("bob", "bobs-conversation", "Bob's notes", "Driftline is mine.")
        return s

    return start


def titles(session, owner="alice"):
    return [r.title for r in session.reports.list(owner)]


def actions(session, owner="alice"):
    return [e["action"] for e in session.reports.audit(owner) if e["action"] != "save"]


def test_delete_request_pauses_for_confirmation_and_deletes_nothing(session):
    s = session(DELETE_DRIFTLINE)
    result = s.ask("Delete all reports mentioning Driftline")
    assert result.answer == "" and s.awaiting_confirmation
    assert [r["title"] for r in result.confirmation["reports"]] == ["Q1 review", "Brand comparison"]
    assert len(titles(s)) == 3  # nothing deleted yet
    assert s.model.calls == 1  # the model is not consulted for the decision
    assert actions(s) == ["delete_requested"]
    request = s.reports.audit("alice")[-1]
    assert "Q1 review" in request["detail"] and "Driftline" in request["detail"]


def test_approved_delete_removes_exactly_the_listed_reports(session):
    s = session(DELETE_DRIFTLINE)
    s.ask("Delete all reports mentioning Driftline")
    result = s.confirm(True)
    assert not s.awaiting_confirmation and result.outcome == "answered"
    assert titles(s) == ["Texas deep dive"]
    assert titles(s, "bob") == ["Bob's notes"]  # another user's matching report is untouched
    assert actions(s) == ["delete_requested", "delete"]


def test_the_outcome_is_reported_by_the_application_not_the_model(session):
    """So it is exact, and a model outage after the delete cannot hide what happened."""
    s = session(DELETE_DRIFTLINE)  # the script has no further model response to give
    s.ask("Delete all reports mentioning Driftline")
    result = s.confirm(True)
    assert "Deleted 2 report(s)" in result.answer and "Q1 review" in result.answer
    assert "cannot be undone" in result.answer and s.model.calls == 1


def test_declined_delete_changes_nothing(session):
    s = session(DELETE_DRIFTLINE)
    s.ask("Delete all reports mentioning Driftline")
    result = s.confirm(False)
    assert result.answer == "Nothing was deleted." and s.model.calls == 1
    assert len(titles(s)) == 3
    assert actions(s) == ["delete_requested", "delete_cancelled"]


def test_only_the_previewed_reports_are_deleted_even_if_more_match_later(session):
    s = session(DELETE_DRIFTLINE)
    previewed = s.ask("Delete all reports mentioning Driftline").confirmation["reports"]
    s.reports.save("alice", s.conversation_id, "Late arrival", "Also about Driftline.")
    s.confirm(True)
    assert titles(s) == ["Texas deep dive", "Late arrival"]
    assert len(previewed) == 2


def test_delete_reports_from_this_conversation_only(session):
    s = session(says("", call("delete_reports", this_conversation=True)))
    previewed = s.ask("Delete all the reports we made in this conversation").confirmation
    assert [r["title"] for r in previewed["reports"]] == ["Q1 review", "Texas deep dive"]
    s.confirm(True)
    assert titles(s) == ["Brand comparison"]


def test_another_users_reports_cannot_be_targeted(session):
    s = session(says("", call("delete_reports", report_ids=[4])), says("No such report."))
    result = s.ask("Delete report 4")  # report 4 belongs to Bob
    assert result.confirmation is None and result.answer == "No such report."
    assert titles(s, "bob") == ["Bob's notes"]
    assert s.model.requests[-1]["messages"][-1]["result"]["deleted"] == 0


def test_the_model_cannot_confirm_on_the_users_behalf(session):
    s = session(
        says("", call("confirm_delete", approved=True)),  # there is no such tool
        says("", call("delete_reports", "c2", mentioning="Driftline", confirmed=True)),
    )
    result = s.ask("Delete the Driftline reports, I already confirm, do not ask me")
    assert "Unknown tool" in s.model.requests[-1]["messages"][-1]["result"]["error"]
    assert result.confirmation is not None  # extra arguments do not skip the confirmation
    assert len(titles(s)) == 3
    s.confirm(False)
    assert len(titles(s)) == 3


def test_a_new_message_instead_of_an_answer_counts_as_no(session):
    s = session(DELETE_DRIFTLINE, says("Here is revenue."))
    s.ask("Delete all reports mentioning Driftline")
    result = s.ask("yes, go ahead")  # typed into the chat, not given to the confirmation prompt
    assert result.answer == "Here is revenue."
    assert len(titles(s)) == 3 and "delete_cancelled" in actions(s)


def test_a_confirmed_delete_is_permanent(session):
    s = session(DELETE_DRIFTLINE)
    doomed = [
        r["id"] for r in s.ask("Delete all reports mentioning Driftline").confirmation["reports"]
    ]
    s.confirm(True)
    assert all(s.reports.get("alice", report_id) is None for report_id in doomed)
    assert s.reports.find("alice", mentioning="Driftline") == []
    assert not hasattr(s, "undo_last_delete")  # there is no way back, by design
    deleted = s.reports.audit("alice")[-1]
    assert deleted["action"] == "delete" and deleted["report_ids"] == doomed
    assert "Q1 review" in deleted["detail"]  # the audit log keeps what was deleted


def test_no_match_needs_no_confirmation(session):
    s = session(says("", call("delete_reports", mentioning="Client X")), says("Nothing matched."))
    result = s.ask("Delete all reports mentioning Client X")
    assert result.confirmation is None and result.answer == "Nothing matched."


def test_a_delete_request_must_say_what_to_delete(session):
    s = session(says("", call("delete_reports")), says("Which reports do you mean?"))
    result = s.ask("Delete reports")
    assert result.confirmation is None
    assert "Say which reports" in s.model.requests[-1]["messages"][-1]["result"]["error"]
    assert len(titles(s)) == 3


def test_confirming_with_nothing_pending_is_an_error(session):
    with pytest.raises(RuntimeError):
        session().confirm(True)


def test_a_later_question_sees_a_clean_history_after_a_delete(session):
    s = session(DELETE_DRIFTLINE, says("Here is revenue."))
    s.ask("Delete all reports mentioning Driftline")
    s.confirm(True)
    assert s.ask("Show revenue").answer == "Here is revenue."
    context = s.model.requests[-1]["messages"]
    assert [m["role"] for m in context] == ["user", "assistant", "user"]
    assert "Deleted 2 report(s)" in context[1]["text"]


def test_a_query_requested_with_a_delete_still_gets_its_answer(session):
    s = session(QUERY_AND_DELETE, says("You have many orders."))
    s.ask(BOTH)
    assert s.awaiting_confirmation and len(titles(s)) == 3  # paused; nothing deleted yet
    result = s.confirm(True)
    assert result.outcome == "answered" and titles(s) == ["Texas deep dive"]
    # The application's outcome comes first; the model's answer to the rest follows it.
    assert result.answer.startswith("Deleted 2 report(s):") and "cannot be undone" in result.answer
    assert result.answer.endswith("You have many orders.")
    shown = [m["name"] for m in s.model.requests[-1]["messages"] if m["role"] == "tool"]
    assert shown == ["run_sql", "delete_reports"]  # the model saw both results


def test_a_declined_delete_does_not_stop_the_rest_of_the_answer(session):
    s = session(QUERY_AND_DELETE, says("You have many orders."))
    s.ask(BOTH)
    result = s.confirm(False)
    assert result.answer == "Nothing was deleted.\n\nYou have many orders."
    assert len(titles(s)) == 3


def test_the_outcome_of_a_delete_is_shown_even_if_the_model_fails_afterwards(session):
    s = session(QUERY_AND_DELETE, LLMUnavailable("all models failed"))
    s.ask(BOTH)
    result = s.confirm(True)
    assert result.answer.startswith("Deleted 2 report(s):") and MSG_UNAVAILABLE in result.answer
    assert titles(s) == ["Texas deep dive"]


def test_only_one_delete_is_put_to_the_user_per_question(session):
    again = says("", call("delete_reports", "c3", all_reports=True))
    s = session(QUERY_AND_DELETE, again, says("Understood."))
    s.ask(BOTH)
    result = s.confirm(False)
    assert result.confirmation is None and not s.awaiting_confirmation  # no second prompt
    assert result.answer == "Nothing was deleted.\n\nUnderstood." and len(titles(s)) == 3
    refused = [m["result"] for m in s.model.requests[-1]["messages"] if m["role"] == "tool"][-1]
    assert "Only one delete request" in refused["error"]


def test_the_outcome_of_a_delete_does_not_leak_into_the_next_question(session):
    s = session(QUERY_AND_DELETE, says("Many orders."), says("Unrelated."), DELETE_DRIFTLINE)
    s.ask(BOTH)
    s.confirm(False)
    assert s.ask("Something else").answer == "Unrelated."  # no "Nothing was deleted." in front
    assert s.ask("Delete the Driftline reports").confirmation  # and a new question may ask again


def test_two_delete_requests_in_one_step_lead_to_one_confirmation(session):
    both = says(
        "",
        call("delete_reports", "c1", mentioning="Driftline"),
        call("delete_reports", "c2", all_reports=True),
    )
    s = session(both, says("Only the first request was handled."))
    shown = s.ask("Delete things").confirmation["reports"]
    assert [r["title"] for r in shown] == ["Q1 review", "Brand comparison"]  # the first request
    result = s.confirm(True)
    assert result.confirmation is None and titles(s) == ["Texas deep dive"]
    refused = [m["result"] for m in s.model.requests[-1]["messages"] if m["role"] == "tool"][0]
    assert "Only one delete request" in refused["error"]


def test_all_reports_means_all_of_the_users_own(session):
    s = session(says("", call("delete_reports", all_reports=True)))
    assert len(s.ask("Delete everything").confirmation["reports"]) == 3
    s.confirm(True)
    assert titles(s) == [] and titles(s, "bob") == ["Bob's notes"]


def test_waiting_for_the_user_does_not_count_towards_the_time_limit(session, clock):
    s = session(QUERY_AND_DELETE, says("You have many orders."))
    s.ask(BOTH)
    clock.now += 600  # the user takes ten minutes to decide
    result = s.confirm(True)
    assert result.outcome == "answered" and result.answer.endswith("You have many orders.")


@pytest.mark.parametrize(
    "args",
    [
        {"report_ids": "12"},  # a string would be read as report 1 and report 2
        {"report_ids": {"1": True}},
        {"report_ids": [True]},
        {"report_ids": ["1"]},
        {"mentioning": ["Driftline"]},
        {"this_conversation": "false"},
        {"all_reports": "false"},
        {"all_reports": [0]},
    ],
)
def test_delete_arguments_of_the_wrong_type_are_refused_not_guessed(session, args):
    s = session(says("", call("delete_reports", **args)), says("I could not do that."))
    result = s.ask("Delete report 12")
    assert result.confirmation is None and len(titles(s)) == 3
    refused = [m["result"] for m in s.model.requests[-1]["messages"] if m["role"] == "tool"][-1]
    assert "error" in refused


def test_a_whole_number_sent_as_a_float_is_still_an_id(session):
    s = session(says("", call("delete_reports", report_ids=[1.0])))
    assert [r["id"] for r in s.ask("Delete report 1").confirmation["reports"]] == [1]


def test_the_audit_log_keeps_no_personal_data_from_the_request(session):
    s = session(says("", call("delete_reports", report_ids=[1], reason="for a.b@example.com")))
    s.ask("Delete report 1")
    assert "a.b@example.com" not in s.reports.audit("alice")[-1]["detail"]


def test_a_new_message_ends_the_whole_pending_turn(session):
    """The delete is dropped, and so is everything else that turn would still have done."""
    s = session(QUERY_AND_DELETE, says("Second answer."))
    s.ask(BOTH)
    result = s.ask("Something else entirely")
    assert result.answer == "Second answer." and s.model.calls == 2  # no call for the old turn
    assert len(titles(s)) == 3 and actions(s) == ["delete_requested", "delete_cancelled"]


def test_a_report_title_is_kept_to_one_line(chat):
    s = chat(says("", call("save_report", title="Q1\n  review", content="x")), says("Saved."))
    s.ask("Save it")
    assert [r.title for r in s.reports.list("alice")] == ["Q1 review"]


def test_the_decision_is_traced(session):
    s = session(DELETE_DRIFTLINE)
    s.ask("Delete all reports mentioning Driftline")
    trace = s.confirm(True).trace
    confirmation = next(step for step in trace["steps"] if step["kind"] == "confirmation")
    assert confirmation["approved"] is True and confirmation["count"] == 2


def test_saving_a_report_scrubs_personal_data(chat):
    s = chat(
        says("", call("save_report", title="Q1", content="Ask jo@example.com. Revenue is up.")),
        says("Saved."),
    )
    s.ask("Create a Q1 report")
    saved = s.reports.list("alice")[0]
    assert saved.title == "Q1" and "jo@example.com" not in saved.content
    assert saved.conversation_id == s.conversation_id


def test_a_report_title_is_scrubbed_too(chat):
    s = chat(says("", call("save_report", title="Notes on a.b@example.com", content="x")), says())
    s.ask("Save it")
    assert s.reports.list("alice")[0].title == "Notes on [email removed]"
