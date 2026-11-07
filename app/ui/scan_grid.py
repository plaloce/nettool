"""NetTool UI 层：**自绘扫描网格控件** `ScanGridWidget`（架构第 6 章，风险1 核心）。

- 单个 `QWidget` + `paintEvent` 绘制 254 格（**绝不用 254 个 QPushButton**）。
- 悬停 / 单击 / 双击 / 右键全部靠 `index_at(x, y)` 坐标反算，不创建 per-cell 控件。
- 唯一状态源：`self._cells: list[ScanResult]`（索引 0..253 对应 .1..254）。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.constants import (
    GRID_CELL_GAP,
    GRID_CELL_H,
    GRID_CELL_MAX_W,
    GRID_CELL_RADIUS,
    GRID_CELL_W,
    GRID_COLS,
    GRID_PAD_X,
    GRID_PAD_Y,
    GRID_PULSE_INTERVAL_MS,
    GRID_STATE_COLOR,
    PALETTE,
    SCAN_TOTAL_ADDRESSES,
    STATUS_TEXT,
)
from app.models import ScanResult


def _lighten(color: QColor, factor: float) -> QColor:
    """按比例提亮颜色（0.10 = 提亮 10%）。"""
    h, s, v, a = color.getHsv()
    # 提亮：向 255 靠拢，同时对低饱和度色也提升亮度
    v = int(min(255, v + 255 * factor))
    s = int(max(0, s - 40 * factor))
    out = QColor()
    out.setHsv(h, s, v, a)
    return out


class ScanGridWidget(QWidget):
    """254 格自绘扫描网格（16 列 × 16 行，最后一格空）。"""

    cellClicked = Signal(str)                 # ip
    cellDoubleClicked = Signal(str)           # ip（在线格才由页面执行业务）
    contextMenuRequested = Signal(str, object)  # (ip, 全局坐标 QPoint)
    hoverChanged = Signal(str)                # ip（供外部更新详情/状态栏）

    COLS: int = GRID_COLS
    # 格子尺寸在 [MIN_CELL, MAX_CELL] 区间内按可用宽度自适应（见 set_available_width）。
    MIN_CELL: int = GRID_CELL_W          # 下限：可点击性保证（≥16px）
    MAX_CELL: int = GRID_CELL_MAX_W      # 上限：避免宽屏下格子变得过大失去"密网"观感
    CELL_W: int = GRID_CELL_W            # 当前生效值（实例级，会随宽度变化）
    CELL_H: int = GRID_CELL_H
    GAP: int = GRID_CELL_GAP
    # 左右内边距补偿：把整除余数平摊进来，让网格绘制宽尽量贴近可用宽度。
    # 不动列间距的原因：index_at / _cell_rect 依赖统一步长互逆，非等距会错格。
    EXTRA_PAD: int = 0
    MAX_EXTRA_PAD: int = 8               # 补偿上限，避免边距被撑得肉眼可见
    PAD_X: int = GRID_PAD_X
    PAD_Y: int = GRID_PAD_Y

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 注意：CELL_W/H 是**实例属性**（下面两行把它从类属性遮蔽为实例级），
        # 因为自适应缩放需要每个实例独立维护自己的格子尺寸。
        self.CELL_W: int = type(self).MIN_CELL
        self.CELL_H: int = type(self).MIN_CELL
        self.EXTRA_PAD: int = 0
        self._prefix: str = "192.168.1"
        self._cells: list[ScanResult] = [
            ScanResult(ip=f"{self._prefix}.{i + 1}", status="pending")
            for i in range(SCAN_TOTAL_ADDRESSES)
        ]
        self._hover_idx: int = -1
        self._selected_idx: int = -1
        self._pulse_phase: float = 0.0

        self.setMouseTracking(True)  # 关键：开启才能收到 mouseMoveEvent
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # 呼吸动画（仅扫描进行中启动）
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(GRID_PULSE_INTERVAL_MS)
        self._anim_timer.timeout.connect(self._on_pulse)

        # 字体（等宽小号）
        self._font = QFont("Consolas", 8)

    # ------------------------------------------------------------------ #
    # 数据接口
    # ------------------------------------------------------------------ #
    def set_prefix(self, prefix: str) -> None:
        """换网段：重建 254 格为 pending 态，并清空悬停/选中。"""
        self._prefix = (prefix or "").strip()
        self._cells = [
            ScanResult(ip=f"{self._prefix}.{i + 1}", status="pending")
            for i in range(SCAN_TOTAL_ADDRESSES)
        ]
        self._hover_idx = -1
        self._selected_idx = -1
        self.update()

    def reset_all(self) -> None:
        """全部回未检测态。"""
        for cell in self._cells:
            cell.status = "pending"
            cell.latency_ms = None
            cell.checked_at = ""
        self._selected_idx = -1
        self.update()

    def update_cells(self, results: list[ScanResult]) -> None:
        """批量就地更新格子状态；每批只触发一次 `update()`。"""
        if not results:
            return
        changed = False
        for r in results:
            idx = self._index_for_ip(r.ip)
            if idx < 0:
                continue
            self._cells[idx] = r
            changed = True
        if changed:
            self.update()

    def set_selected(self, ip: str) -> None:
        """按 IP 设置选中格；找不到则清空选中。"""
        idx = self._index_for_ip(ip)
        self._selected_idx = idx
        self.update()

    def cell_at(self, idx: int) -> ScanResult | None:
        """按索引取格子数据；越界返回 None。"""
        if 0 <= idx < len(self._cells):
            return self._cells[idx]
        return None

    def cell_by_ip(self, ip: str) -> ScanResult | None:
        """按 IP 取格子数据。"""
        idx = self._index_for_ip(ip)
        return self._cells[idx] if idx >= 0 else None

    def ip_at(self, idx: int) -> str:
        """索引 -> IP。"""
        if 0 <= idx < len(self._cells):
            return self._cells[idx].ip
        return ""

    # ------------------------------------------------------------------ #
    # 呼吸动画
    # ------------------------------------------------------------------ #
    def start_pulse(self) -> None:
        """启动呼吸动画（扫描进行中调用）。"""
        self._pulse_phase = 0.0
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def stop_pulse(self) -> None:
        """停止呼吸动画（扫描结束调用）。"""
        self._anim_timer.stop()
        self._pulse_phase = 0.0
        self.update()

    def _on_pulse(self) -> None:
        """动画帧：推进相位并局部重绘。"""
        self._pulse_phase = (self._pulse_phase + 0.08) % 1.0
        if self._has_pending():
            self.update()
        else:
            self.stop_pulse()

    def _has_pending(self) -> bool:
        """是否存在未检测格。"""
        return any(c.status == "pending" for c in self._cells)

    # ------------------------------------------------------------------ #
    # 尺寸与坐标
    # ------------------------------------------------------------------ #
    def set_available_width(self, avail: int) -> None:
        """按可用宽度**自适应放大格子**，让网格吃满面板、不留大片右侧空位。

        设计目标（用户反馈：「格图标变小了，与右边留了较大的空位」）：
        - 宽度足够时**放大**格子（上限 `MAX_CELL`），视觉更饱满、更好点；
        - 宽度不足时回落到 `MIN_CELL`，此时由外层 `QScrollArea` 出滚动条兜底。

        算法：`cell = (avail - 2*PAD - (COLS-1)*GAP) / COLS`，再夹到 [MIN, MAX]。

        为避免整除余数在右侧留下一条 10px 左右的空隙，把余数**平摊进左右内边距**
        （`self.PAD_X` 之外的补偿量 `self.EXTRA_PAD`）。之所以不动列间距：
        `index_at` 与 `_cell_rect` 依赖**统一步长**互为逆运算，一旦列间距不等距，
        点击命中判定就会出现错格。加内边距则完全不影响步长，最安全。
        """
        if avail <= 0:
            return
        span = avail - 2 * self.PAD_X - (self.COLS - 1) * self.GAP
        cell = span // self.COLS
        cell = max(self.MIN_CELL, min(self.MAX_CELL, cell))
        # 余数平摊到左右内边距（只有未触到上下限时才补偿，否则会撑破可用宽）
        remainder = span - cell * self.COLS
        extra = 0
        if 0 < remainder and self.MIN_CELL <= cell <= self.MAX_CELL:
            extra = min(remainder // 2, self.MAX_EXTRA_PAD)
        changed = cell != self.CELL_W or extra != self.EXTRA_PAD
        if changed:
            self.CELL_W = cell
            self.CELL_H = cell
            self.EXTRA_PAD = extra
            self.updateGeometry()   # 通知布局：sizeHint 变了
            self.update()

    def sizeHint(self):  # noqa: N802 (Qt 命名)
        """首选尺寸 = 网格几何 + 内边距。

        注意必须把**内边距**算进去：`paintEvent` 从 `(PAD_X, PAD_Y)` 开始画第一格，
        若 sizeHint 只返回网格几何，"最后一列/最后一行"就会超出控件可见区
        （用户报的「16/32/48 列要往右拉滚动条才看得见」正是此因）。
        """
        from PySide6.QtCore import QSize

        return QSize(
            self._grid_width() + 2 * self._pad_x(),
            self._grid_height() + 2 * self.PAD_Y,
        )

    def minimumSizeHint(self):  # noqa: N802
        # 横向：必须按**下限格子**算。若直接返回 sizeHint()，当格子被放大到
        # MAX_CELL 时最小宽会被抬到 632px，反而顶宽父母板，
        # 让 QScrollArea 误判"放不下"而弹横向滚动条。
        #
        # 纵向：**刻意只给 3 行的高度**（≈66px），不按 16 行全高（360px）算。
        # 原因：960×640 最小窗口下纵向预算只有约 615px，若网格声明需要 360px 全高，
        # 它会顶高整个内容行，把页面撑出纵向滚动条（实测溢出 24px）。
        # 网格自己有 `grid_scroll`（QScrollArea）兜底 —— 高度不够时出**垂直**滚动条，
        # 比"整个页面被撑高"体验更好（页面级滚动条会连带把标题/控制条一起滚走）。
        from PySide6.QtCore import QSize

        rows = (SCAN_TOTAL_ADDRESSES + self.COLS - 1) // self.COLS
        min_rows = 3
        return QSize(
            self.COLS * (self.MIN_CELL + self.GAP) - self.GAP + 2 * self.PAD_X,
            min_rows * (self.MIN_CELL + self.GAP) - self.GAP + 2 * self.PAD_Y,
        )

    def _pad_x(self) -> int:
        """生效左内边距 = 基础内边距 + 余数补偿（见 set_available_width）。"""
        return self.PAD_X + self.EXTRA_PAD

    def _grid_width(self) -> int:
        return self.COLS * (self.CELL_W + self.GAP) - self.GAP

    def _grid_height(self) -> int:
        rows = (SCAN_TOTAL_ADDRESSES + self.COLS - 1) // self.COLS
        return rows * (self.CELL_H + self.GAP) - self.GAP

    def _cell_rect(self, idx: int) -> QRect:
        """索引 -> 格子矩形（含内边距偏移）。"""
        col = idx % self.COLS
        row = idx // self.COLS
        left = self._pad_x() + col * (self.CELL_W + self.GAP)
        top = self.PAD_Y + row * (self.CELL_H + self.GAP)
        return QRect(left, top, self.CELL_W, self.CELL_H)

    def index_at(self, x: int, y: int) -> int:
        """坐标 -> 格子索引；落在间隙/越界返回 -1（架构 6.5 公式）。

        先减去内边距（`_pad_x()/PAD_Y`），保证与 `_cell_rect` 严格互为逆运算。
        """
        x -= self._pad_x()
        y -= self.PAD_Y
        if x < 0 or y < 0:
            return -1
        col = x // (self.CELL_W + self.GAP)
        row = y // (self.CELL_H + self.GAP)
        if col < 0 or col >= self.COLS or row < 0:
            return -1
        # 落在格子间隙里（格子像素区间为 [left, left+CELL_W)，故用 >=）
        if (x % (self.CELL_W + self.GAP)) >= self.CELL_W:
            return -1
        if (y % (self.CELL_H + self.GAP)) >= self.CELL_H:
            return -1
        idx = row * self.COLS + col
        return idx if 0 <= idx < SCAN_TOTAL_ADDRESSES else -1

    def _index_for_ip(self, ip: str) -> int:
        """IP -> 索引（最后一段 - 1）；不匹配当前网段返回 -1。"""
        if not ip:
            return -1
        parts = ip.split(".")
        if len(parts) != 4:
            return -1
        if ".".join(parts[:3]) != self._prefix:
            return -1
        try:
            last = int(parts[3])
        except ValueError:
            return -1
        if 1 <= last <= SCAN_TOTAL_ADDRESSES:
            return last - 1
        return -1

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802
        """按架构 6.4 的 5 步绘制 254 格。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 背景
        painter.fillRect(self.rect(), QColor(PALETTE["bg_root"]))

        color_online = QColor(PALETTE["success"])
        color_offline = QColor(PALETTE["danger"])
        color_idle = QColor(PALETTE["bg_cell_idle"])
        border_default = QColor(PALETTE["border_default"])
        text_on_color = QColor(PALETTE["grid_text_on_color"])
        text_disabled = QColor(PALETTE["text_disabled"])
        color_primary = QColor(PALETTE["primary"])
        color_selected = QColor(PALETTE["text_primary"])

        # 呼吸 alpha（扫描中 pending 格的柔和闪烁）
        pulse_alpha = 0.35 + 0.30 * abs(0.5 - (self._pulse_phase % 1.0)) * 2.0

        painter.setFont(self._font)

        for idx in range(SCAN_TOTAL_ADDRESSES):
            r = self._cell_rect(idx)
            cell = self._cells[idx]
            status = cell.status

            if status == "online":
                fill = QColor(color_online)
                border = None
                text_color = text_on_color
            elif status == "offline":
                fill = QColor(color_offline)
                border = None
                text_color = text_on_color
            else:
                fill = QColor(color_idle)
                border = border_default
                text_color = text_disabled
                # 扫描中 pending 加呼吸
                if self._anim_timer.isActive():
                    fill = QColor(fill)
                    fill.setAlphaF(max(0.0, min(1.0, pulse_alpha)))

            # 悬停提亮
            if idx == self._hover_idx:
                fill = _lighten(fill, 0.10)

            # 画圆角矩形
            if border is not None:
                painter.setPen(QPen(border, 1))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(r, GRID_CELL_RADIUS, GRID_CELL_RADIUS)

            # 序号文字
            painter.setPen(text_color)
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, str(idx + 1))

            # 选中：外描边 2px #E6EDF3
            if idx == self._selected_idx:
                painter.setPen(QPen(color_selected, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(r, GRID_CELL_RADIUS, GRID_CELL_RADIUS)

            # 悬停：外描边 2px #388BFD
            if idx == self._hover_idx:
                painter.setPen(QPen(color_primary, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(r, GRID_CELL_RADIUS, GRID_CELL_RADIUS)

        painter.end()

    # ------------------------------------------------------------------ #
    # 鼠标事件
    # ------------------------------------------------------------------ #
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        idx = self.index_at(pos.x(), pos.y())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
            self.hoverChanged.emit(self.ip_at(idx) if idx >= 0 else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        idx = self.index_at(pos.x(), pos.y())
        if idx < 0:
            super().mousePressEvent(event)
            return
        ip = self.ip_at(idx)
        if event.button() == Qt.MouseButton.LeftButton:
            self._selected_idx = idx
            self.update()
            self.cellClicked.emit(ip)
        elif event.button() == Qt.MouseButton.RightButton:
            self._selected_idx = idx
            self.update()
            self.contextMenuRequested.emit(ip, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        idx = self.index_at(pos.x(), pos.y())
        if idx >= 0 and event.button() == Qt.MouseButton.LeftButton:
            # 只发 ip，由页面判断是否在线并决定业务动作（职责更清晰）
            self.cellDoubleClicked.emit(self.ip_at(idx))
        super().mouseDoubleClickEvent(event)

    def event(self, e) -> bool:  # noqa: N802
        """拦截 ToolTip 事件，按悬停格组装提示文本（PRD F2-09）。"""
        if e.type() == QEvent.Type.ToolTip:
            idx = self._hover_idx
            if idx < 0:
                self.setToolTip("")
                return True
            cell = self._cells[idx]
            status_cn = STATUS_TEXT.get(cell.status, "未检测")
            latency = f"{cell.latency_ms}ms" if cell.latency_ms is not None else "--"
            self.setToolTip(
                f"IP: {cell.ip} | 状态: {status_cn} | 延迟: {latency}"
            )
            return True
        return super().event(e)
