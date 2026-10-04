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


def test_undo_restores_the_last_delete_only(store):
    first, second, third = (r.id for r in store.list("alice"))
    store.delete("alice", [first])
    store.delete("alice", [second, third])
    assert titles(store.restore_last("alice")) == ["Texas deep dive", "Brand comparison"]
    assert titles(store.list("alice")) == ["Texas deep dive", "Brand comparison"]
    assert titles(store.restore_last("alice")) == ["Q1 review"]
    assert store.restore_last("alice") == []


def test_every_change_is_audited(store):
    ids = [r.id for r in store.list("alice")]
    store.delete("alice", ids[:1])
    store.restore_last("alice")
    actions = [(e["action"], e["report_ids"]) for e in store.audit("alice")]
    assert actions[-2:] == [("delete", ids[:1]), ("restore", ids[:1])]
    assert [e["action"] for e in store.audit("alice")].count("save") == 3
    assert all(e["action"] == "save" for e in store.audit("bob"))


def test_reports_survive_a_restart(tmp_path):
    path = tmp_path / "reports.sqlite"
    ReportStore(path).save("alice", "c", "Kept", "content")
    assert titles(ReportStore(path).list("alice")) == ["Kept"]
