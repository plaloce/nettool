"""NetTool UI 层：主窗口（Sidebar + QStackedWidget + 状态栏 + 快捷键 + 关闭清理）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from app.config_service import ConfigService
from app.constants import (
    APP_WINDOW_TITLE,
    PALETTE,
    PROJECT_URL,
    STATUS_LEVEL_COLORS,
    WINDOW_DEFAULT_H,
    WINDOW_DEFAULT_W,
    WINDOW_MIN_H,
    WINDOW_MIN_W,
)
from app.netsh_service import NetshService
from app.ping_service import PingService
from app.scanner import ScannerService
from app.ui.page_config import PageConfig
from app.ui.page_netcheck import PageNetCheck
from app.ui.page_ping import PagePing
from app.ui.page_scan import PageScan
from app.ui.sidebar import Sidebar


class MainWindow(QMainWindow):
    """NetTool 主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_WINDOW_TITLE)
        self.resize(WINDOW_DEFAULT_W, WINDOW_DEFAULT_H)
        self.setMinimumSize(WINDOW_MIN_W, WINDOW_MIN_H)

        # 业务服务（单例）
        self._config_service = ConfigService()
        self._netsh_service = NetshService()
        self._scanner = ScannerService(self)
        self._ping = PingService(self)

        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()

        # 初始进入页面一
        # 说明：`goto_page(0)` 会调 `PageConfig.on_enter()` 预加载方案。
        # 调用方 `main.py` 在本构造之后再 `window.show()`，所以这里不存在"钩子执行两次"
        # 的问题 —— **不要**在本类里再调 `self.show()` 来"避免重复"，会与 main.py 重复显示。
        self.goto_page(0)
        # 初始权限徽标
        self.set_elevated_badge(self._netsh_service.admin_now)

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 侧栏
        self._sidebar = Sidebar()
        layout.addWidget(self._sidebar)

        # 页面容器
        self._pages = QStackedWidget()
        self._pages.setObjectName("pages")

        self._page_config = PageConfig(self._config_service, self._netsh_service)
        self._page_scan = PageScan(self._scanner, self._netsh_service)
        self._page_ping = PagePing(self._ping, self._netsh_service)
        self._page_netcheck = PageNetCheck(self._netsh_service)

        self._pages.addWidget(self._page_config)      # index 0
        self._pages.addWidget(self._page_scan)        # index 1
        self._pages.addWidget(self._page_ping)        # index 2
        self._pages.addWidget(self._page_netcheck)    # index 3
        layout.addWidget(self._pages, 1)

        # 状态栏
        self._status_bar = QStatusBar()
        self._status_bar.setObjectName("statusBar")
        self.setStatusBar(self._status_bar)

        self._status_label = QLabel("就绪")
        self._status_bar.addWidget(self._status_label)

        # 右下角：项目主页链接（永久部件，不会被临时消息冲掉）。
        # 用 linkActivated + 自己调 QDesktopServices，而不是 setOpenExternalLinks(True)：
        # 后者由 C++ 内部直接开浏览器，Python 侧无法拦截，测试也就证明不了"点得开"。
        self._github_link = QLabel()
        self._github_link.setObjectName("githubLink")
        self._github_link.setTextFormat(Qt.TextFormat.RichText)
        self._github_link.setText(
            f'<a href="{PROJECT_URL}" style="color:{PALETTE["info"]}; '
            f'text-decoration:none;">GitHub 项目</a>'
        )
        self._github_link.setToolTip(PROJECT_URL)
        self._github_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._github_link.linkActivated.connect(self._open_project_page)
        self._status_bar.addPermanentWidget(self._github_link)

        # 权限状态只在**左侧栏**显示（`#permBadge`）—— 那里还能表达"普通权限 · 按需提权"，
        # 信息量比状态栏的裸"已提权"更大。状态栏也曾放一个，但两者内容重复，
        # 用户反馈"两处显示已提权，保留一个即可"，故状态栏不再放权限标签。

    # ------------------------------------------------------------------ #
    # 信号
    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._sidebar.nav_changed.connect(self.goto_page)

        # 页面二 → 状态栏
        self._page_scan.status_message.connect(self.show_status)
        # 页面二 → 跨页跳转 Ping（F2-12）
        self._page_scan.quick_ping_requested.connect(self.goto_ping_with)

        # 页面三 → 状态栏
        self._page_ping.status_message.connect(self.show_status)

        # 页面四 → 状态栏
        self._page_netcheck.status_message.connect(self.show_status)

        # 页面一 → 应用成功 → 联动徽标
        self._page_config.apply_finished.connect(self._on_apply_finished)

    def _setup_shortcuts(self) -> None:
        """Ctrl+1 ~ Ctrl+4 快速切页。"""
        for idx in range(4):
            sc = QShortcut(QKeySequence(f"Ctrl+{idx + 1}"), self)
            sc.activated.connect(lambda i=idx: self.goto_page(i))

    # ------------------------------------------------------------------ #
    # 页面切换
    # ------------------------------------------------------------------ #
    def goto_page(self, idx: int) -> None:
        """切换到指定页面（0/1/2/3）并同步侧栏选中态。"""
        if 0 <= idx < self._pages.count():
            self._pages.setCurrentIndex(idx)
            self._sidebar.set_current(idx)
            # 进入页面钩子
            # ⚠️ 页面三（Ping）**没有** on_enter —— 它进入时不需要预加载数据，
            #    这是既有设计，不是遗漏。
            if idx == 0:
                self._page_config.on_enter()
            elif idx == 1:
                self._page_scan.on_enter()
            elif idx == 3:
                # 网络检查：进入即自动检测（F4-08）；on_enter 内部防重入
                self._page_netcheck.on_enter()

    def goto_ping_with(self, ip: str) -> None:
        """跨页跳转：切到 Ping 页，填入 IP、切单次模式并自动执行（F2-12 / U5）。"""
        self.goto_page(2)
        self._page_ping.start_with(ip, single_mode=True, auto_run=True)
        self.show_status(f"已跳转 Ping 工具：{ip}", "info")

    # ------------------------------------------------------------------ #
    # 状态栏
    # ------------------------------------------------------------------ #
    def show_status(self, text: str, level: str = "normal") -> None:
        """状态栏提示，`level` ∈ {info, warning, danger, normal, success}（四色）。"""
        color_key = STATUS_LEVEL_COLORS.get(level, "text_secondary")
        color = PALETTE.get(color_key, PALETTE["text_secondary"])
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};")

    def _open_project_page(self, url: str | None = None) -> None:
        """状态栏「GitHub 项目」被点击 → 用系统默认浏览器打开项目主页。"""
        QDesktopServices.openUrl(QUrl(url or PROJECT_URL))

    # ------------------------------------------------------------------ #
    # 权限徽标
    # ------------------------------------------------------------------ #
    def set_elevated_badge(self, elevated: bool) -> None:
        """更新权限徽标（只在侧栏显示，避免与状态栏重复）。"""
        self._sidebar.set_elevated(elevated)

    def _on_apply_finished(self, ok: bool) -> None:
        """应用方案成功后联动徽标（提权成功）。"""
        if ok and self._netsh_service.elevated_session:
            self.set_elevated_badge(True)

    # ------------------------------------------------------------------ #
    # 关闭清理
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """优雅停止扫描 / 持续 ping / 网络检查 / 线程池，无残留进程。"""
        try:
            self._page_scan.shutdown()
        except Exception:
            pass
        try:
            self._page_ping.shutdown()
        except Exception:
            pass
        try:
            self._page_netcheck.shutdown()
        except Exception:
            pass
        try:
            self._page_config.shutdown(wait=False)
        except Exception:
            pass
        try:
            self._scanner.shutdown(wait=False)
        except Exception:
            pass
        event.accept()
