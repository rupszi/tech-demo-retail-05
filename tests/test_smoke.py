from retail_agent.config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.data_backend == "duckdb"
    assert s.max_rows > 0
