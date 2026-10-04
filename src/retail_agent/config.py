"""Settings loaded from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from retail_agent.data.schema import DATASET


@dataclass(frozen=True)
class Settings:
    # model
    gemini_auth: str = "api_key"  # "api_key" (Google AI Studio) or "vertex" (ADC)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    gcp_location: str = "global"
    # data
    data_backend: str = "duckdb"
    duckdb_path: str = "data/mock.duckdb"
    gcp_project_id: str | None = None
    bq_dataset: str = DATASET
    bq_max_bytes_billed: int = 1_000_000_000
    # limits per question
    max_sql_retries: int = 2
    max_rows: int = 500
    rows_to_model: int = 50
    max_llm_calls: int = 8
    turn_token_budget: int = 60_000
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
        load_dotenv(".env")
        env = os.environ.get
        default = cls()
        return cls(
            gemini_auth=env("GEMINI_AUTH", default.gemini_auth),
            gemini_api_key=env("GEMINI_API_KEY") or None,
            gemini_model=env("GEMINI_MODEL") or default.gemini_model,
            gemini_fallback_model=env("GEMINI_FALLBACK_MODEL") or default.gemini_fallback_model,
            gcp_location=env("GCP_LOCATION", default.gcp_location),
            data_backend=env("DATA_BACKEND", default.data_backend),
            duckdb_path=env("DUCKDB_PATH", default.duckdb_path),
            gcp_project_id=env("GCP_PROJECT_ID") or None,
            bq_dataset=env("BQ_DATASET", default.bq_dataset),
            bq_max_bytes_billed=int(env("BQ_MAX_BYTES_BILLED", default.bq_max_bytes_billed)),
            max_sql_retries=int(env("MAX_SQL_RETRIES", default.max_sql_retries)),
            max_rows=int(env("MAX_ROWS", default.max_rows)),
            rows_to_model=int(env("ROWS_TO_MODEL", default.rows_to_model)),
            max_llm_calls=int(env("MAX_LLM_CALLS", default.max_llm_calls)),
            turn_token_budget=int(env("TURN_TOKEN_BUDGET", default.turn_token_budget)),
            reports_db_path=env("REPORTS_DB_PATH", default.reports_db_path),
            trace_dir=env("TRACE_DIR", default.trace_dir),
            persona_path=env("PERSONA_PATH", default.persona_path),
            golden_dir=env("GOLDEN_DIR", default.golden_dir),
            profiles_path_override=env("PROFILES_PATH") or None,
        )
