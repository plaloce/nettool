"""NetTool UI 层：可复用小组件（Toast / ResultBanner / ProfileCard / StatChip / ChipButton）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Profile


class ChipButton(QPushButton):
    """圆角 chip 快捷按钮（objectName=chipBtn）。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("chipBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class StatChip(QLabel):
    """统计文字标签（带 objectName 以便 QSS 着色）。"""

    def __init__(self, text: str = "", object_name: str = "stat", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(object_name)


class Toast(QLabel):
    """2 秒自动消失的浮层提示（objectName=toast）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, level: str = "info", duration_ms: int = 2000) -> None:
        """显示提示，`level` ∈ {info, success, danger, warning}。"""
        self.setText(text)
        self.setProperty("level", level)
        # 触发 QSS 属性选择器重算
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def _reposition(self) -> None:
        """把 Toast 居中显示在父控件顶部偏下。"""
        parent = self.parentWidget()
        if parent is None:
            return
        x = max((parent.width() - self.width()) // 2, 0)
        y = 72
        self.move(x, y)


class ResultBanner(QLabel):
    """执行结果提示条（成功绿底 / 失败红底），objectName 动态切换。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultSuccess")
        self.setWordWrap(True)
        self.hide()

    def show_result(self, ok: bool, text: str) -> None:
        """展示结果：ok=True 用成功皮肤，否则用错误皮肤。"""
        self.setObjectName("resultSuccess" if ok else "resultError")
        self.setText(text)
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()

    def clear(self) -> None:
        """隐藏并清空。"""
        self.setText("")
        self.hide()


class ProfileCard(QFrame):
    """方案卡片（objectName=profileCard）。

    展示方案名 + 生效中标签 + 上次应用弱提示点 + IP + 网卡/DNS + 编辑/删除按钮。
    """

    clicked = Signal(str)          # profile id
    edit_requested = Signal(str)   # profile id
    delete_requested = Signal(str)  # profile id

    def __init__(self, profile: Profile, last_applied: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("profileCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._profile_id: str = profile.id
        self._selected: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(6)

        # 第一行：方案名 + 生效中 + 上次应用点 + 弹簧
        top = QHBoxLayout()
        top.setSpacing(8)
        self._title = QLabel(profile.name or "(未命名)")
        self._title.setObjectName("cardTitle")
        top.addWidget(self._title)

        self._active_tag = QLabel("生效中")
        self._active_tag.setObjectName("activeTag")
        self._active_tag.setVisible(bool(profile.active))
        if profile.active_conflict:
            self._active_tag.setProperty("conflict", "true")
        self._active_tag.setToolTip(
            "该网卡有多个方案同时匹配，请检查配置" if profile.active_conflict else "当前系统配置与该方案一致"
        )
        top.addWidget(self._active_tag)

        self._dot = QLabel("● 上次应用")
        self._dot.setObjectName("lastAppliedDot")
        self._dot.setVisible(last_applied)
        top.addWidget(self._dot)

        top.addStretch(1)
        self._edit_btn = QPushButton("✎")
        self._edit_btn.setObjectName("iconBtn")
        self._edit_btn.setToolTip("编辑")
        self._edit_btn.setFixedWidth(32)
        self._edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._profile_id))
        top.addWidget(self._edit_btn)

        self._del_btn = QPushButton("🗑")
        self._del_btn.setObjectName("iconBtn")
        self._del_btn.setProperty("danger", "true")
        self._del_btn.setToolTip("删除")
        self._del_btn.setFixedWidth(32)
        self._del_btn.clicked.connect(lambda: self.delete_requested.emit(self._profile_id))
        top.addWidget(self._del_btn)

        root.addLayout(top)

        # 第二行：IP / DHCP
        if profile.dhcp:
            ip_text = "自动获取 (DHCP)"
        else:
            ip_text = profile.ip or "—"
        self._ip = QLabel(ip_text)
        self._ip.setObjectName("cardIp")
        root.addWidget(self._ip)

        # 第三行：网卡 · DNS
        dns_text = profile.dns_primary or "—"
        meta = f"{profile.adapter or '未选网卡'} · DNS {dns_text}"
        self._meta = QLabel(meta)
        self._meta.setObjectName("cardMeta")
        root.addWidget(self._meta)

    # ------------------------------------------------------------------ #
    @property
    def profile_id(self) -> str:
        return self._profile_id

    def set_selected(self, selected: bool) -> None:
        """设置选中态（左竖条 + 背景提亮）。"""
        self._selected = selected
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # 点击卡片（非按钮区域）→ 选中
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._profile_id)
        super().mouseReleaseEvent(event)
