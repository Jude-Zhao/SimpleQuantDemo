import pytest

from webapp.config import ServerConfig, get_config, WebAppConfig

pytestmark = pytest.mark.usefixtures("webapp_clean_state")


def test_server_host_defaults_to_loopback():
    """BUG-19：模型默认仅监听本机（直接构造，不经 yaml）。"""
    assert ServerConfig().host == "127.0.0.1"


def test_server_host_explicit_bind_allowed():
    """BUG-19：显式配置其他监听地址（如 0.0.0.0）仍可覆盖。"""
    assert ServerConfig(host="0.0.0.0").host == "0.0.0.0"


def test_server_host_effective_value_from_yaml():
    """BUG-19：仓库 yaml 的生效值与模型默认一致（get_config 读 yaml）。"""
    assert get_config().server.host == "127.0.0.1"


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
    assert cfg.datasource.cache_enabled is True
    assert cfg.datasource.cache_days_daily == 365


def test_factors_config():
    cfg = get_config()
    assert cfg.factors.auto_discover is True
    assert cfg.factors.scan_package == "core.factors"


def test_strategy_config():
    cfg = get_config()
    assert cfg.strategy.max_history_runs == 100


def test_webapp_config_constructs_without_yaml():
    """A11: 缺少 config/webapp.yaml 时 WebAppConfig(**{}) 采用模型默认。"""
    from webapp.config import SyncConfig

    cfg = WebAppConfig(**{})
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.database.url == "sqlite:///./data/simple_quant.db"
    assert cfg.datasource.cache_days_daily == 365
    assert cfg.sync.max_concurrent_tasks == 2
    # 单个嵌套段也可独立覆盖，其余段回落模型默认
    cfg2 = WebAppConfig(**{"sync": SyncConfig(max_concurrent_tasks=3)})
    assert cfg2.sync.max_concurrent_tasks == 3
    assert cfg2.server.host == "127.0.0.1"
