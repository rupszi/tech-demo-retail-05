from retail_agent.golden import find_similar, load_trios


def trios(settings):
    return load_trios(settings.golden_dir)


def test_similar_past_work_is_found(settings):
    def names(question):
        return [t.name for t in find_similar(question, trios(settings))]

    assert names("Why did our churn rate spike last month?")[0] == "churn_definition"
    assert "state_spending_gap" in names("Why are users in Texas underspending versus California?")
    assert "brand_comparison" in names("Compare Driftline and Cobalt Row")
    assert names("What data is available?") == []


def test_at_most_two_examples_are_used(settings):
    assert len(find_similar("compare monthly revenue for top customers", trios(settings))) <= 2


def test_every_stored_query_still_passes_the_gate_and_runs(settings, gateway):
    """The local version of the nightly check that keeps the bucket in step with the schema."""
    stored = trios(settings)
    assert len(stored) >= 5
    for trio in stored:
        result = gateway("carol").run(trio.sql)
        assert len(result.frame.columns) >= 2, trio.name
