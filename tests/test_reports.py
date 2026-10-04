import pytest

from retail_agent.reports import ReportStore


@pytest.fixture
def store():
    s = ReportStore(":memory:")
    s.save("alice", "conv-1", "Q1 review", "Driftline returns are high. Brightwave grew 12%.")
    s.save("alice", "conv-1", "Texas deep dive", "Texas customers buy fewer items.")
    s.save("alice", "conv-2", "Brand comparison", "Driftline vs Cobalt Row.")
    s.save("bob", "conv-9", "Bob's Driftline notes", "Driftline is mine.")
    return s


def titles(reports):
    return [r.title for r in reports]


def test_users_only_see_their_own_reports(store):
    assert len(store.list("alice")) == 3 and len(store.list("bob")) == 1
    bobs = store.list("bob")[0]
    assert store.get("alice", bobs.id) is None
    assert store.get("bob", bobs.id).title == "Bob's Driftline notes"


def test_find_by_text_is_case_insensitive_and_covers_title_and_content(store):
    assert titles(store.find("alice", mentioning="driftline")) == ["Q1 review", "Brand comparison"]
    assert titles(store.find("alice", mentioning="TEXAS")) == ["Texas deep dive"]
    assert store.find("alice", mentioning="nothing like this") == []


def test_find_treats_wildcards_as_plain_text(store):
    assert titles(store.find("alice", mentioning="12%")) == ["Q1 review"]
    assert store.find("alice", mentioning="%") == [store.list("alice")[0]]
    assert store.find("alice", mentioning="_") == []


def test_find_by_conversation_and_combined_filters(store):
    assert len(store.find("alice", conversation_id="conv-1")) == 2
    assert titles(store.find("alice", mentioning="driftline", conversation_id="conv-1")) == [
        "Q1 review"
    ]
    assert len(store.find("alice")) == 3


def test_delete_only_removes_the_owners_reports(store):
    everything = [r.id for r in store.list("alice")] + [r.id for r in store.list("bob")]
    deleted = store.delete("alice", everything)
    assert len(deleted) == 3
    assert store.list("alice") == [] and len(store.list("bob")) == 1


def test_delete_with_no_ids_does_nothing(store):
    assert store.delete("alice", []) == []
    assert len(store.list("alice")) == 3


def test_deleted_reports_are_gone_for_good(store):
    first, second, third = (r.id for r in store.list("alice"))
    assert titles(store.delete("alice", [first, second])) == ["Q1 review", "Texas deep dive"]
    assert titles(store.list("alice")) == ["Brand comparison"]
    assert store.get("alice", first) is None
    assert store.find("alice", report_ids=[first, second]) == []
    assert store.find("alice", mentioning="Texas") == []
    assert store.delete("alice", [first, second]) == []  # deleting again finds nothing


def test_ids_are_never_reused_after_a_delete(store):
    """So an id in the audit log can never point at a different, later report."""
    last = store.list("bob")[-1].id
    store.delete("bob", [last])
    assert store.save("bob", "c", "A new report", "content").id > last


def test_every_change_is_audited(store):
    ids = [r.id for r in store.list("alice")]
    store.delete("alice", ids[:2])
    entry = store.audit("alice")[-1]
    assert (entry["action"], entry["report_ids"]) == ("delete", ids[:2])
    assert "Q1 review" in entry["detail"] and "Texas deep dive" in entry["detail"]  # titles survive
    assert [e["action"] for e in store.audit("alice")].count("save") == 3
    assert all(e["action"] == "save" for e in store.audit("bob"))


def test_reports_survive_a_restart(tmp_path):
    path = tmp_path / "reports.sqlite"
    ReportStore(path).save("alice", "c", "Kept", "content")
    assert titles(ReportStore(path).list("alice")) == ["Kept"]


def test_a_delete_survives_a_restart(tmp_path):
    path = tmp_path / "reports.sqlite"
    store = ReportStore(path)
    report = store.save("alice", "c1", "Q1", "content")
    store.delete("alice", [report.id])
    reopened = ReportStore(path)  # a second connection sees only what was committed
    assert reopened.list("alice") == []
    assert [entry["action"] for entry in reopened.audit("alice")] == ["save", "delete"]


def test_a_nul_in_the_search_text_matches_nothing_and_not_everything(store):
    assert store.find("alice", mentioning="\x00Texas") == []
