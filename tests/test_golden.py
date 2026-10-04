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


def test_the_two_closest_examples_are_used(settings):
    found = find_similar("compare monthly revenue for top customers", trios(settings))
    assert [t.name for t in found] == ["monthly_revenue", "top_customers"]  # of three that match


def test_every_stored_query_still_passes_the_gate_and_runs(settings, gateway):
    """The local version of the nightly check that keeps the bucket in step with the schema."""
    stored = trios(settings)
    assert len(stored) >= 5
    for trio in stored:
        result = gateway("carol").run(trio.sql)
        assert len(result.frame.columns) >= 2, trio.name
    # That each example also finds rows is checked on the real dataset (`pytest -m bigquery`).
    # The small mock has too few items a month for the minimum group sizes some examples use.
