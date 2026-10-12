from undertow.config import Settings


def test_defaults_do_not_contain_feed_key():
    s = Settings(_env_file=None)
    assert s.hockeytech_key == ""
    assert s.hockeytech_client_code == "pwhl"


def test_env_override(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    assert Settings(_env_file=None).is_prod
