from retail_agent.config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.data_backend == "duckdb"
    assert s.max_rows > 0


def test_profiles_path_follows_backend():
    assert Settings().profiles_path == "config/users.duckdb.json"
    assert Settings(data_backend="bigquery").profiles_path == "config/users.bigquery.json"
    assert Settings(profiles_path_override="x.json").profiles_path == "x.json"
