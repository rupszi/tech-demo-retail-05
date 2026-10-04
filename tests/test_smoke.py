from retail_agent.config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.data_backend == "duckdb"
    assert s.max_rows > 0


def test_profiles_path_follows_backend():
    assert Settings().profiles_path == "config/users.duckdb.json"
    assert Settings(data_backend="bigquery").profiles_path == "config/users.bigquery.json"
    assert Settings(profiles_path_override="x.json").profiles_path == "x.json"


def test_models_are_an_ordered_list(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELS", " model-a, model-b ,model-a,")
    assert Settings.from_env().gemini_models == ("model-a", "model-b")
    monkeypatch.delenv("GEMINI_MODELS")
    assert Settings.from_env().gemini_models[-1] == "gemini-3.5-flash-lite"
