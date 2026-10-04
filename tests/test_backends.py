from types import SimpleNamespace

import pytest
from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc

from retail_agent.data.base import DataError
from retail_agent.data.bigquery_backend import BigQueryBackend
from retail_agent.data.schema import TABLES

DS = "`bigquery-public-data`.thelook_ecommerce"


# ---- contract: DuckDB backend ----------------------------------------------------------------
def test_schema_matches_mock_tables(backend):
    for table, cols in TABLES.items():
        got = backend._con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog = 'bigquery-public-data' "
            "AND table_schema = 'thelook_ecommerce' AND table_name = ? "
            "ORDER BY ordinal_position",
            [table],
        ).fetchall()
        assert [g[0] for g in got] == [c.name for c in cols]
        assert backend.get_schema(table) == cols


def test_execute_bigquery_dialect(backend):
    df = backend.execute(
        "SELECT FORMAT_TIMESTAMP('%Y-%m', created_at) AS month, "
        "ROUND(SUM(sale_price), 2) AS revenue "
        f"FROM {DS}.order_items GROUP BY month ORDER BY month LIMIT 5"
    )
    assert list(df.columns) == ["month", "revenue"] and len(df) == 5


def test_dry_run_ok(backend):
    assert backend.dry_run(f"SELECT COUNT(*) FROM {DS}.orders").bytes_processed is None


def test_bare_table_names_do_not_resolve(backend):
    """Same behaviour as BigQuery without a default dataset."""
    for fn in (backend.dry_run, backend.execute):
        with pytest.raises(DataError):
            fn("SELECT COUNT(*) FROM orders")


@pytest.mark.parametrize("sql", ["SELEC 1", "SELECT nope FROM orders", "SELECT * FROM not_a_table"])
def test_bad_sql_is_syntax_error(backend, sql):
    for fn in (backend.dry_run, backend.execute):
        with pytest.raises(DataError) as e:
            fn(sql)
        assert e.value.kind == "syntax"


def test_unknown_table_schema(backend):
    with pytest.raises(DataError):
        backend.get_schema("information_schema")


# ---- BigQuery backend with a stubbed client ---------------------------------------------------
class FakeJob:
    def __init__(self, bytes_processed=1000, error=None, df=None):
        self.total_bytes_processed = bytes_processed
        self._error, self._df = error, df

    def result(self, timeout=None):
        self.timeout = timeout
        if self._error:
            raise self._error
        return SimpleNamespace(to_dataframe=lambda **kw: self._df)

    def cancel(self):
        self.cancelled = True


class FakeClient:
    def __init__(self, job=None, query_error=None):
        self.job, self.query_error, self.configs = job, query_error, []

    def query(self, sql, job_config=None):
        self.configs.append(job_config)
        if self.query_error:
            raise self.query_error
        return self.job


def bq(client, cap=10_000):
    return BigQueryBackend("proj", max_bytes_billed=cap, client=client)


def test_bq_dry_run_flags_and_cost():
    client = FakeClient(FakeJob(bytes_processed=5000))
    result = bq(client).dry_run("SELECT 1")
    cfg = client.configs[0]
    assert result.bytes_processed == 5000
    assert cfg.dry_run is True and cfg.maximum_bytes_billed == 10_000
    assert cfg.default_dataset is None  # bare table names must not resolve


def test_bq_dry_run_rejects_expensive_query():
    with pytest.raises(DataError) as e:
        bq(FakeClient(FakeJob(bytes_processed=99_999))).dry_run("SELECT 1")
    assert e.value.kind == "too_expensive"


def test_bq_execute_sets_byte_cap_and_returns_frame():
    import pandas as pd

    client = FakeClient(FakeJob(df=pd.DataFrame({"a": [1]})))
    assert bq(client).execute("SELECT 1")["a"].tolist() == [1]
    assert client.configs[0].maximum_bytes_billed == 10_000 and client.configs[0].dry_run is False


def test_bq_query_that_runs_out_of_time_is_cancelled_and_not_treated_as_an_outage():
    job = FakeJob(error=TimeoutError())
    with pytest.raises(DataError) as e:
        bq(FakeClient(job)).execute("SELECT 1", timeout_s=5)
    # "too_expensive" tells the model to narrow the query; an outage would re-run the same SQL
    assert e.value.kind == "too_expensive" and job.cancelled


@pytest.mark.parametrize(("given", "used"), [(None, 60.0), (7.5, 7.5), (500, 60.0), (0, 1.0)])
def test_bq_query_timeout_follows_the_time_that_is_left(given, used):
    import pandas as pd

    job = FakeJob(df=pd.DataFrame({"a": [1]}))
    bq(FakeClient(job)).execute("SELECT 1", timeout_s=given)
    assert job.timeout == used


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (gexc.BadRequest("Unrecognized name: foo"), "syntax"),
        (gexc.BadRequest("Query exceeded limit for bytes billed: 1000"), "too_expensive"),
        (gexc.ServiceUnavailable("backend down"), "unavailable"),
        (gexc.TooManyRequests("slow down"), "unavailable"),
        (gexc.Forbidden("no access"), "execution"),
        (gexc.Forbidden("Quota exceeded: your project exceeded its quota"), "unavailable"),
        (auth_exc.RefreshError("the credentials have expired"), "unavailable"),
    ],
)
def test_bq_error_classification(error, kind):
    with pytest.raises(DataError) as e:
        bq(FakeClient(query_error=error)).execute("SELECT 1")
    assert e.value.kind == kind


def test_bq_error_during_result_is_classified():
    with pytest.raises(DataError) as e:
        bq(FakeClient(FakeJob(error=gexc.DeadlineExceeded("timeout")))).execute("SELECT 1")
    assert e.value.kind == "unavailable"
