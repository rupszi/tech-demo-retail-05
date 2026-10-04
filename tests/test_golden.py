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


def test_no_example_names_a_brand_or_quotes_a_result(settings):
    """One example serves every user, so it may carry the method and nothing that is theirs."""
    import json
    import re
    from pathlib import Path

    from retail_agent.data.mock import BRANDS

    root = Path(settings.golden_dir).parent
    brands = set(BRANDS)
    for path in (root / "config").glob("users.*.json"):
        for user in json.loads(path.read_text())["users"]:
            brands |= {s.split(":", 1)[1] for s in user["scopes"] if s != "brand:*"}
    assert len(brands) > 15
    figure = re.compile(r"[$€£]\s?\d|\d\s?%")  # an amount of money or a percentage
    for trio in trios(settings):
        text = f"{trio.question}\n{trio.sql}\n{trio.report}"
        named = sorted(b for b in brands if re.search(rf"\b{re.escape(b)}\b", text, re.I))
        assert not named, f"{trio.name} names {named}"
        assert not figure.search(text), f"{trio.name} quotes a figure: {figure.search(text)[0]}"


def test_every_stored_query_still_passes_the_gate_and_runs(settings, gateway):
    """The local version of the nightly check that keeps the bucket in step with the schema."""
    stored = trios(settings)
    assert len(stored) >= 5
    for trio in stored:
        result = gateway("carol").run(trio.sql)
        assert len(result.frame.columns) >= 2, trio.name
    # That each example also finds rows is checked on the real dataset (`pytest -m bigquery`).
    # The small mock has too few items a month for the minimum group sizes some examples use.
