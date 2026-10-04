"""Settings loaded from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from retail_agent.data.schema import DATASET


def _names(value: str | None) -> tuple[str, ...]:
    """'a, b,c' -> ('a', 'b', 'c'), without duplicates."""
    return tuple(dict.fromkeys(n.strip() for n in (value or "").split(",") if n.strip()))


def _int(name: str, default: int) -> int:
    """A whole-number setting. A value that is not one is reported by name."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {value!r}.") from None


BACKENDS = ("bigquery", "duckdb")


def _backend(value: str) -> str:
    if value not in BACKENDS:
        raise ValueError(f"DATA_BACKEND must be one of {', '.join(BACKENDS)}, got {value!r}.")
    return value


@dataclass(frozen=True)
class Settings:
    # model
    gemini_auth: str = "api_key"  # "api_key" (Google AI Studio) or "vertex" (ADC)
    gemini_api_key: str | None = None
    # Tried in order: the first model that is available answers. On the free tier the two larger
    # models allow 20 requests a day each, so the lite model carries the rest of the day.
    gemini_models: tuple[str, ...] = (
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    )
    gcp_location: str = "global"
    # data: "bigquery" is the real dataset; "duckdb" is an offline mock with the same tables
    data_backend: str = "bigquery"
    duckdb_path: str = "data/mock.duckdb"
    gcp_project_id: str | None = None
    bq_dataset: str = DATASET
    bq_max_bytes_billed: int = 1_000_000_000  # 1 GB per query; BigQuery refuses anything larger
    # limits per question
    max_sql_retries: int = 2  # corrections allowed after a failed query
    max_rows: int = 500  # rows fetched per query at most
    rows_to_model: int = 50  # of those, how many the model is shown
    max_llm_calls: int = 8
    max_queries: int = 12  # run_sql calls per question, failed ones included
    turn_token_budget: int = 60_000
    turn_time_budget_s: int = 120  # long reports may take one to two minutes, not more
    # local state and editable content
    reports_db_path: str = "data/reports.sqlite"
    trace_dir: str = "logs"
    persona_path: str = "config/persona.md"
    golden_dir: str = "golden_bucket"
    profiles_path_override: str | None = None

    @property
    def profiles_path(self) -> str:
        """User profiles differ per backend because the brands differ."""
        return self.profiles_path_override or f"config/users.{self.data_backend}.json"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(".env")  # values already set in the environment win over the file
        env = os.environ.get
        default = cls()  # one place for the defaults: the field definitions above
        return cls(
            gemini_auth=env("GEMINI_AUTH", default.gemini_auth),
            gemini_api_key=env("GEMINI_API_KEY") or None,
            gemini_models=_names(env("GEMINI_MODELS")) or default.gemini_models,
            gcp_location=env("GCP_LOCATION", default.gcp_location),
            data_backend=_backend(env("DATA_BACKEND", default.data_backend)),
            duckdb_path=env("DUCKDB_PATH", default.duckdb_path),
            gcp_project_id=env("GCP_PROJECT_ID") or None,
            bq_dataset=env("BQ_DATASET", default.bq_dataset),
            bq_max_bytes_billed=_int("BQ_MAX_BYTES_BILLED", default.bq_max_bytes_billed),
            max_sql_retries=_int("MAX_SQL_RETRIES", default.max_sql_retries),
            max_rows=_int("MAX_ROWS", default.max_rows),
            rows_to_model=_int("ROWS_TO_MODEL", default.rows_to_model),
            max_llm_calls=_int("MAX_LLM_CALLS", default.max_llm_calls),
            max_queries=_int("MAX_QUERIES", default.max_queries),
            turn_token_budget=_int("TURN_TOKEN_BUDGET", default.turn_token_budget),
            turn_time_budget_s=_int("TURN_TIME_BUDGET_SECONDS", default.turn_time_budget_s),
            reports_db_path=env("REPORTS_DB_PATH", default.reports_db_path),
            trace_dir=env("TRACE_DIR", default.trace_dir),
            persona_path=env("PERSONA_PATH", default.persona_path),
            golden_dir=env("GOLDEN_DIR", default.golden_dir),
            profiles_path_override=env("PROFILES_PATH") or None,
        )
