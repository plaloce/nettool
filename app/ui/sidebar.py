"""NetTool UI 层：左侧导航栏 Sidebar（4 个 checkable 按钮 + 版本号 + 权限徽标）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    APP_NAME,
    APP_TAGLINE,
    APP_TITLE,
    APP_VERSION,
    SIDEBAR_WIDTH,
)


class Sidebar(QFrame):
    """左侧导航栏，发 `nav_changed(int)` 信号（0/1/2/3）。"""

    nav_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        # 品牌标题 + 副标题（副标题写死在同一个小竖排里，不改外层 spacing，
        # 否则品牌与导航按钮之间的间距会被撑开）
        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(2)

        brand = QLabel(APP_NAME)
        brand.setObjectName("brandTitle")
        brand.setToolTip(APP_TITLE)
        brand_box.addWidget(brand)

        brand_sub = QLabel(APP_TAGLINE)
        brand_sub.setObjectName("brandSub")
        brand_sub.setToolTip(APP_TITLE)
        brand_box.addWidget(brand_sub)

        root.addLayout(brand_box)

        # 分隔线
        root.addWidget(self._hline())

        # 导航按钮
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        labels = [
            "🧩  网络配置",
            "🔍  局域网扫描",
            "📡  Ping 工具",
            "🌐  网络检查",
        ]
        for idx, text in enumerate(labels):
            btn = QPushButton(text)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, i=idx: self._on_nav(i))
            self._group.addButton(btn, idx)
            self._buttons.append(btn)
            root.addWidget(btn)

        root.addStretch(1)
        root.addWidget(self._hline())

        # 版本号
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("versionLabel")
        root.addWidget(version)

        # 权限徽标
        self._perm_badge = QLabel("🛡 普通权限 · 按需提权")
        self._perm_badge.setObjectName("permBadge")
        self._perm_badge.setWordWrap(True)
        root.addWidget(self._perm_badge)

        # 默认选中第一项
        self.set_current(0)

    @staticmethod
    def _hline() -> QFrame:
        """水平分隔线。"""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background: #30363D; border: none;")
        return line

    # ------------------------------------------------------------------ #
    def _on_nav(self, idx: int) -> None:
        self.nav_changed.emit(idx)

    def set_current(self, idx: int) -> None:
        """程序化切换选中项（不重复发信号）。"""
        if 0 <= idx < len(self._buttons):
            self._buttons[idx].setChecked(True)

    def set_elevated(self, elevated: bool) -> None:
        """切换权限徽标：提权后显示「✅ 已提权」（绿色）。"""
        if elevated:
            self._perm_badge.setText("✅ 已提权")
            self._perm_badge.setProperty("elevated", "true")
        else:
            self._perm_badge.setText("🛡 普通权限 · 按需提权")
            self._perm_badge.setProperty("elevated", "false")
        self._perm_badge.style().unpolish(self._perm_badge)
        self._perm_badge.style().polish(self._perm_badge)
