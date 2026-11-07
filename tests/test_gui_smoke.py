"""P0-1 · GUI 启动冒烟测试（最重要）。

- 实际构造 MainWindow 及三个页面，捕获 ImportError/AttributeError/TypeError。
- 验证 build_qss() 非空且无未替换占位符。
- 不阻塞：不进入 app.exec() 事件循环（构造即验证），必要时用 QTimer.singleShot 自动退出。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #
def test_build_qss_non_empty_and_resolved(qapp):
    from app.ui.styles import build_qss

    qss = build_qss()
    assert isinstance(qss, str)
    assert len(qss) > 500
    # 不应残留 $ 占位符（string.Template 未替换会在 substitute 抛错；
    # 这里再兜底检查常见残留）
    assert "$" not in qss.replace("$", "", 0) or "$" not in qss, "QSS 存在未替换的 $ 占位符"
    # 记录实际是否有 $ 残留（断言为无）
    assert "$" not in qss


# --------------------------------------------------------------------------- #
# 各控件可独立构造
# --------------------------------------------------------------------------- #
def test_construct_sidebar(qapp):
    from app.ui.sidebar import Sidebar
    s = Sidebar()
    assert s.objectName() == "sidebar"


def test_construct_scan_grid(qapp):
    from app.ui.scan_grid import ScanGridWidget
    w = ScanGridWidget()
    assert len(w._cells) == 254


def test_construct_page_config(qapp, tmp_path, monkeypatch):
    from app.config_service import ConfigService
    from app.netsh_service import NetshService
    from app.ui.page_config import PageConfig

    # 隔离配置文件
    monkeypatch.setattr(
        "app.paths.profiles_path", lambda: os.path.join(str(tmp_path), "profiles.json")
    )
    cfg = ConfigService(path=os.path.join(str(tmp_path), "profiles.json"))
    page = PageConfig(cfg, NetshService())
    assert page is not None


def test_construct_page_scan(qapp, monkeypatch):
    from app.netsh_service import NetshService
    from app.scanner import ScannerService
    from app.ui.page_scan import PageScan

    page = PageScan(ScannerService(), NetshService())
    assert page is not None
    page.shutdown()


def test_construct_page_ping(qapp):
    from app.netsh_service import NetshService
    from app.ping_service import PingService
    from app.ui.page_ping import PagePing

    page = PagePing(PingService(), NetshService())
    assert page is not None
    page.shutdown()


# --------------------------------------------------------------------------- #
# 主窗口整体构造（无阻塞）
# --------------------------------------------------------------------------- #
def test_construct_main_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.paths.profiles_path", lambda: os.path.join(str(tmp_path), "profiles.json")
    )
    from app.ui.main_window import MainWindow
    from app.ui.page_netcheck import PageNetCheck

    win = MainWindow()
    assert win.windowTitle()
    # 现为 4 页（配置 / 扫描 / Ping / 网络检查）
    assert win._pages.count() == 4
    # 侧栏 4 个导航按钮
    assert len(win._sidebar._buttons) == 4
    # 第 4 页（index 3）必须是 PageNetCheck，且绑定了关闭清理
    assert isinstance(win._pages.widget(3), PageNetCheck)
    # 四页切换不抛异常（页面四 on_enter 内部防重入）
    for i in range(4):
        win.goto_page(i)
    win.close()
    # 关闭清理不抛异常（网络检查线程池 shutdown）
    win._page_netcheck.shutdown()


# --------------------------------------------------------------------------- #
# 实际启动主循环后自动退出（真启动冒烟）
# --------------------------------------------------------------------------- #
def test_full_app_boot_and_autoquit(qapp, tmp_path, monkeypatch):
    """构造 MainWindow + show + QTimer 200ms 后 quit，验证事件循环能正常跑起并退出。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(
        "app.paths.profiles_path", lambda: os.path.join(str(tmp_path), "profiles.json")
    )
    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    QTimer.singleShot(200, QApplication.instance().quit)
    # 不阻塞地跑一小段事件循环
    QApplication.instance().exec()
    win.close()
