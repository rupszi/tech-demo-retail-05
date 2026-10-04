"""Checks against the real BigQuery dataset. Opt in with: uv run pytest -m bigquery

They need Google Cloud credentials and GCP_PROJECT_ID (see the README), and are skipped when
BigQuery is not set up. The corpus checks are dry-runs, which are free. The rest are a handful of
small queries: about 70 MB scanned in total, against 1 TB free per month.
"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv

from retail_agent.config import Settings
from retail_agent.data import DataError, create_backend
from retail_agent.data.schema import TABLES
from retail_agent.golden import load_trios
from retail_agent.safety import QueryGateway, load_profiles, validate_query
from retail_agent.safety.policy import PII_COLUMNS, SAFE_USER_COLUMNS

from .sql_cases import VALID

pytestmark = pytest.mark.bigquery

ROOT = Path(__file__).parents[1]
USERS = ["alice", "bob", "carol"]
_BIGQUERY_TYPES = {"INTEGER": "INT64", "FLOAT": "FLOAT64"}


@pytest.fixture(scope="module")
def live():
    # Unlike the offline tests, this group needs the developer's own setup (the project that is
    # billed), so it reads `.env` itself.
    load_dotenv(ROOT / ".env")
    settings = replace(Settings.from_env(), data_backend="bigquery")
    try:
        backend = create_backend(settings)
        backend.dry_run("SELECT 1")
    except Exception as e:  # noqa: BLE001 - any setup problem means "not configured here"
        pytest.skip(f"BigQuery is not set up: {e}")
    profiles = load_profiles(ROOT / "config" / "users.bigquery.json")

    def gateway(user):
        return QueryGateway(backend, profiles[user], max_rows=500, dataset=settings.bq_dataset)

    return SimpleNamespace(backend=backend, profiles=profiles, gateway=gateway)


def test_the_schema_in_code_matches_the_live_tables(live):
    for table, columns in TABLES.items():
        found = [
            (c.name, _BIGQUERY_TYPES.get(c.type, c.type))
            for c in live.backend.get_schema(table)
            if c.name != "user_geom"  # left out on purpose: it duplicates the coordinates
        ]
        assert found == [(c.name, c.type) for c in columns], table


@pytest.mark.parametrize("user", USERS)
@pytest.mark.parametrize("sql", VALID)
def test_rewritten_queries_are_valid_bigquery(sql, user, live):
    """Every legitimate query, after the gate has scoped it, passes a BigQuery dry-run."""
    rewritten = validate_query(sql, live.profiles[user], max_rows=500).sql
    live.backend.dry_run(rewritten)


def test_a_bare_table_name_does_not_resolve(live):
    """Jobs have no default dataset, so only the fully qualified names written by the gate work."""
    with pytest.raises(DataError):
        live.backend.dry_run("SELECT COUNT(*) FROM events")


@pytest.mark.parametrize("user", ["alice", "bob"])
def test_a_user_sees_only_their_brands_in_real_data(user, live):
    allowed = set(live.profiles[user].brands)
    listed = live.gateway(user).run("SELECT DISTINCT brand FROM products").frame["brand"]
    assert set(listed) == allowed
    sold = live.gateway(user).run(
        "SELECT DISTINCT p.brand FROM order_items oi JOIN products p ON p.id = oi.product_id"
    )
    assert set(sold.frame["brand"]) <= allowed
    other = "Carhartt" if user == "alice" else "Roxy"
    hidden = live.gateway(user).run(f"SELECT COUNT(*) AS n FROM products WHERE brand = '{other}'")
    assert hidden.frame["n"][0] == 0


def test_the_all_brands_grant_sees_every_brand(live):
    brands = live.gateway("carol").run("SELECT COUNT(DISTINCT brand) AS n FROM products").frame
    assert brands["n"][0] > 100


def test_personal_data_never_comes_back_from_real_data(live):
    result = live.gateway("carol").run("SELECT * FROM users LIMIT 5")
    assert tuple(result.frame.columns) == SAFE_USER_COLUMNS
    assert not set(result.frame.columns) & PII_COLUMNS
    assert "@" not in result.frame.to_string() and result.redactions == {}


def test_every_analyst_example_runs_and_stays_cheap(live):
    trios = load_trios(ROOT / "golden_bucket")
    assert len(trios) >= 5
    for trio in trios:
        result = live.gateway("carol").run(trio.sql)
        assert len(result.frame.columns) >= 2, trio.name
        assert len(result.frame) > 0, trio.name  # an example that finds nothing explains nothing
        assert result.bytes_processed < 100_000_000, trio.name  # each scans a few MB
