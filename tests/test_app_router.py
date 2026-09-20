"""F24: SPA 路由乱序渲染回归——通过 node VM 执行 app.js 断言修复后行为。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tests" / "static" / "app_router.test.cjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")


def test_router_out_of_order_responses():
    result = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
