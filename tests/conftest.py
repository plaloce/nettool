"""pytest 公共配置：把项目根加入 sys.path，并提供 Qt 应用 fixture。

注意：
- 项目以 `python main.py` 方式运行，故根目录需在 sys.path 上。
- GUI 相关用例统一用 `qapp` fixture，避免各用例各自创建 QApplication 导致崩溃。
"""

from __future__ import annotations

import os
import sys

# 项目根（tests 的上一级）加入导入路径
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import pytest
except Exception:  # pragma: no cover - pytest 一定存在
    pytest = None


if pytest is not None:

    @pytest.fixture(scope="session")
    def qapp():
        """返回全局 QApplication 单例（无头模式下可创建）。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app
