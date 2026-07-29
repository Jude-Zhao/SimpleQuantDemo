from webapp.config import get_config, WebAppConfig


def test_config_loads():
    cfg = get_config()
    assert isinstance(cfg, WebAppConfig)
    assert cfg.server.port == 8000
    assert cfg.database.url.startswith("sqlite")


def test_config_has_all_sections():
    cfg = get_config()
    assert cfg.server is not None
    assert cfg.database is not None
    assert cfg.datasource is not None
    assert cfg.factors is not None
    assert cfg.strategy is not None


def test_datasource_config():
    cfg = get_config()
    assert cfg.datasource.primary == "akshare"
    assert cfg.datasource.secondary == "baostock"
    assert cfg.datasource.cache_enabled is True
    assert cfg.datasource.cache_days_daily == 365
    assert cfg.datasource.cache_days_minute == 60


def test_factors_config():
    cfg = get_config()
    assert cfg.factors.auto_discover is True
    assert cfg.factors.scan_package == "core.factors"


def test_strategy_config():
    cfg = get_config()
    assert cfg.strategy.max_history_runs == 100
