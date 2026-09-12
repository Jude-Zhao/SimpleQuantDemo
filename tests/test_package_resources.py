"""A11/OPT-06: 包资源完整性——从构建产物 wheel 中验证必要资源存在。

先运行 ``$PY -m build --wheel``（输出到 dist/），再运行本测试。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_RESOURCES = [
    "core/factors/builtin/factors.yaml",
    "research/factor_config.yaml",
    "webapp/static/index.html",
    "webapp/static/css/base.css",
    "webapp/static/css/variables.css",
    "webapp/static/js/app.js",
    "webapp/static/js/api.js",
    "webapp/static/js/components.js",
    "webapp/static/js/utils.js",
    "webapp/static/js/pages/dashboard.js",
    "webapp/static/js/pages/strategies.js",
    "webapp/static/js/pages/universe.js",
    "webapp/static/vendor/echarts.min.js",
]

FORBIDDEN_PREFIXES = (
    "data/",  # 数据库文件不打包
)


def _latest_wheel() -> Path:
    dist = ROOT / "dist"
    wheels = sorted(dist.glob("simple_quant_demo-*.whl"))
    assert wheels, (
        "未找到 wheel 构建产物；请先运行: $PY -m build --wheel"
    )
    return wheels[-1]


def test_wheel_contains_required_resources():
    wheel = _latest_wheel()
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    missing = [res for res in REQUIRED_RESOURCES if res not in names]
    assert not missing, f"wheel 缺少必要资源: {missing}"


def test_wheel_excludes_database_and_env():
    wheel = _latest_wheel()
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    leaked = [n for n in names if n.startswith(FORBIDDEN_PREFIXES) or n == ".env"]
    assert not leaked, f"wheel 不应包含数据/环境文件: {leaked}"


def test_wheel_excludes_tests_and_docs():
    wheel = _latest_wheel()
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    leaked = [n for n in names if n.startswith(("tests/", "docs/"))]
    assert not leaked, f"wheel 不应包含测试/文档: {leaked}"
