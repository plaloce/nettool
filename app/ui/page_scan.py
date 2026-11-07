"""NetTool UI 层：页面二 · 局域网扫描（PRD 5.2 / 原型 2）。

包含：控制条、统计进度、自绘网格、IP 详情卡、在线列表、右键菜单。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    MSG_COPIED,
    MSG_NO_ADAPTER,
    MSG_SCAN_ALL_OFFLINE,
    MSG_SCAN_BAD_SUBNET,
    MSG_SCAN_DONE,
    MSG_SCAN_RANGE,
    MSG_SCAN_STOPPED,
    MSG_SHARE_BAD_IP,
    MSG_SHARE_FAILED,
    MSG_SHARE_NOT_ONLINE,
    MSG_SHARE_OPENING,
    MSG_SHARE_REQUESTED,
    PALETTE,
    SCAN_CONCURRENCY_CHOICES,
    SCAN_DEFAULT_CONCURRENCY,
    ADAPTER_COMBO_MAX_W,
    SCAN_DEFAULT_TIMEOUT_MS,
    SCAN_RIGHT_PANEL_MAX_W,
    SCAN_RIGHT_PANEL_MIN_W,
    SCAN_TIMEOUT_CHOICES,
    SCAN_TOTAL_ADDRESSES,
    STATUS_TEXT,
)
from app.models import AdapterInfo, ScanResult, ScanStats
from app.netsh_service import NetshService
from app.procutil import popen_silent
from app.scanner import ScannerService
from app.ui.scan_grid import ScanGridWidget
from app.validators import is_ipv4, is_valid_subnet_prefix


# FlowRow 与控件宽度自适应工具已抽到 app/ui/flow_row.py（Ping 页复用同一份实现，
# 避免一处修好另一处漂移）。这里保留同名导入，兼容既有测试断言与内部用法。
from app.ui.flow_row import (  # noqa: E402  (放在此处以保持文件既有阅读顺序)
    COMBO_CHROME_W,
    FlowRow,
    fit_button,
    fit_button_multi,
    fit_combo,
    fit_edit,
)


class PageScan(QWidget):
    """页面二：局域网扫描（网段/网卡/并发/超时 + 网格 + 详情 + 在线列表）。"""

    quick_ping_requested = Signal(str)   # 跨页跳转到 Ping（F2-12）
    status_message = Signal(str, str)    # (text, level) 转交主窗口状态栏

    # 起停合并按钮的两态文案（定宽时按较长者算，防止切换态被裁）
    _TOGGLE_TEXT_IDLE = "▶ 开始扫描"
    _TOGGLE_TEXT_RUNNING = "■ 停止扫描"
    # 工作线程 → 主线程的网卡列表回传通道。
    # ⚠️ 不要改回 QTimer.singleShot：从工作线程调用它会静默失效（见 _adapters_worker）。
    _adapters_ready = Signal(list)

    def __init__(
        self,
        scanner: ScannerService,
        netsh_service: NetshService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scanner = scanner
        self._netsh = netsh_service
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan-page")
        self._online: dict[str, int | None] = {}   # ip -> latency
        self._detail_ip: str = ""
        self._adapters: list[AdapterInfo] = []

        self._build_ui()
        self._connect_signals()
        QTimer.singleShot(150, self.load_adapters)

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        # ---- 横向宽度守卫 ----
        # 本页最小横向需求 = 200(sidebar) + 48(页边距) + 476(网格) + 16(spacing) + 320(右栏)
        #                  ≈ 1060 < 默认窗口 1100，但 > 最小窗口 960。
        # 用 QScrollArea 包一层：窗口够宽时完全透明不可见；窗口被拖到很窄时才出现
        # 横向滚动条（而不是内容被挤压变形 / 网格被裁掉）。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        page_scroll = QScrollArea()
        page_scroll.setObjectName("pageScroll")
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(page_scroll)

        page_host = QWidget()
        page_scroll.setWidget(page_host)

        root = QVBoxLayout(page_host)
        # 960×640 下纵向预算极紧（视口仅 615px），故页边距与块间距都收到够用即止：
        # 原 24/16 → 16/10，直接省出 24 + 18 = 42px 纵向空间给中间内容区。
        # 目标是「默认窗口不拉动尺寸就能全部显示」（用户明确要求）。
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("局域网扫描")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # ---- 控制条（可换行：窄窗口自动折成两行）----
        bar = QFrame()
        bar.setObjectName("controlBar")
        bar_layout = QVBoxLayout(bar)
        bar_layout.setContentsMargins(10, 8, 10, 8)
        bar_layout.setSpacing(0)

        flow = FlowRow(h_gap=8, v_gap=6)
        bar_layout.addWidget(flow)
        bar_layout = flow   # 后续 addWidget 全部落到 flow 上

        # ------------------------------------------------------------------ #
        # 控件宽度**一律运行时按自身文本量**，不写死像素
        # ------------------------------------------------------------------ #
        # 为什么不能写死：文本像素宽取决于**实际生效的字体**，而字体由
        # `main.py::setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))` 与 QSS
        # `font-size: 13px` 共同决定（QSS 会覆盖 font 的 pointSize，最终 pixelSize=13）。
        # 离线用 `QFont(fam, 13)` 估算偏差可达 40%+：
        #     离线 "192.168.2" = 153px，运行态 117px
        #     离线 "IP 或域名…" = 289px，运行态 221px
        # 任何写死的数字迟早会错，而错的后果就是「文字被裁」或「白占空间挤到换行」。
        # 所以统一走 app/ui/flow_row.py 的 fit_combo/fit_edit/fit_button：
        #     fit_* 内部用控件自己的 fontMetrics 量文本，再加 QSS 的边框开销。
        # ------------------------------------------------------------------ #

        bar_layout.addWidget(self._hint("网段"))
        self._subnet_combo = QComboBox()
        self._subnet_combo.setEditable(True)
        # ⚠️ 必须限制 minimumContentsLength：QComboBox 默认按**最长选项文本**算
        # minimumSizeHint（历史上网卡下拉含 "VMware Network Adapter VMnet1" → 139px），
        # 会盖掉 setMaximumWidth，导致控制条总宽超预算，「重置」被挤到第二行。
        # 设成 0 表示"最小宽不依赖内容"，实际宽度交给 min/maxWidth 决定。
        self._subnet_combo.setMinimumContentsLength(0)
        self._subnet_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        # 可编辑网段框是**唯一例外**：用户会键入完整 IP，所以不能按当前文本定宽。
        # 但基准取「当前实际网段」（如 "192.168.2" = 117px）而不是最长的
        # "192.168.100"（143px）—— 后者要多占 26px，把「重置」挤到第二行。
        # 用户原话是「先保证要完整显示网段」，而本机网段就是 192.168.2 这种三段等长
        # 之外的短形态；超出宽度时输入框会横向滚动，不影响输入。
        _fm_subnet = self._subnet_combo.fontMetrics()
        _w_subnet = _fm_subnet.horizontalAdvance("192.168.2") + COMBO_CHROME_W
        self._subnet_combo.setMinimumWidth(_w_subnet)
        self._subnet_combo.setMaximumWidth(_w_subnet)
        self._subnet_combo.setToolTip(
            "可输入前三段，如 192.168.2；留空则自动使用本机当前网段"
        )
        bar_layout.addWidget(self._subnet_combo)

        bar_layout.addWidget(self._hint("网卡"))
        self._adapter_combo = QComboBox()
        # 同上：关掉"按最长项算最小宽"的行为（这是控制条换行的根因）
        self._adapter_combo.setMinimumContentsLength(0)
        self._adapter_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        # 网卡下拉的内容是**动态**的（要等 netsh 查完才知道），构造时还没数据，
        # 此刻 fit_combo 会算出很小值。所以先给「5 个中文字符 + 边框」保底
        # —— 用户明确要求「网卡显示的宽度可以缩小部分，但要保证 5 个中文字符以上」。
        # 真实数据到达后 `_refresh_adapter_combo` 会按实际文本重新 fit。
        _fm = self._adapter_combo.fontMetrics()
        _min_adapter_w = _fm.horizontalAdvance("网网网网网") + COMBO_CHROME_W
        self._adapter_combo.setMinimumWidth(_min_adapter_w)
        self._adapter_combo.setMaximumWidth(_min_adapter_w)
        self._adapter_combo.setToolTip("选择用于扫描的网卡（悬停看全名）")
        self._adapter_combo.currentIndexChanged.connect(self._on_adapter_changed)
        bar_layout.addWidget(self._adapter_combo)

        bar_layout.addWidget(self._hint("并发"))
        self._concurrency_combo = QComboBox()
        for c in SCAN_CONCURRENCY_CHOICES:
            self._concurrency_combo.addItem(str(c))
        self._concurrency_combo.setCurrentText(str(SCAN_DEFAULT_CONCURRENCY))
        self._concurrency_combo.setMinimumContentsLength(0)
        self._concurrency_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        fit_combo(self._concurrency_combo)
        bar_layout.addWidget(self._concurrency_combo)

        bar_layout.addWidget(self._hint("超时"))
        self._timeout_combo = QComboBox()
        for t in SCAN_TIMEOUT_CHOICES:
            self._timeout_combo.addItem(f"{t} ms", t)
        self._timeout_combo.setCurrentIndex(SCAN_TIMEOUT_CHOICES.index(SCAN_DEFAULT_TIMEOUT_MS))
        self._timeout_combo.setMinimumContentsLength(0)
        self._timeout_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        fit_combo(self._timeout_combo)
        bar_layout.addWidget(self._timeout_combo)

        # 起停按钮**合并为一个**：空闲态显示「开始扫描」，工作中切换为「停止扫描」。
        # 好处：少一个常驻按钮，「重置」不再被挤到第二行（用户反馈的换行问题）。
        self._toggle_btn = QPushButton(self._TOGGLE_TEXT_IDLE)
        self._toggle_btn.setObjectName("primaryBtn")
        fit_button_multi(self._toggle_btn, [self._TOGGLE_TEXT_IDLE, self._TOGGLE_TEXT_RUNNING])
        self._toggle_btn.clicked.connect(self.on_toggle)
        bar_layout.add_item(self._toggle_btn)

        self._reset_btn = QPushButton("🔄 重置")
        self._reset_btn.setObjectName("ghostBtn")
        fit_button(self._reset_btn)
        self._reset_btn.clicked.connect(self.on_reset)
        bar_layout.add_item(self._reset_btn)

        # 全部 item 就位后做首次排版（此后由 resizeEvent 自动重排）
        flow.apply_style()

        root.addWidget(bar)

        # ---- 统计面板 ----
        stats_card = QFrame()
        stats_card.setObjectName("card")
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(14, 8, 14, 8)
        stats_layout.setSpacing(4)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(20)
        self._scanned_label = QLabel("已扫描 0 / 254")
        self._online_label = QLabel("在线 0")
        self._online_label.setObjectName("statOnline")
        self._offline_label = QLabel("空闲 0")
        self._offline_label.setObjectName("statOffline")
        self._time_label = QLabel("耗时 0.0s")
        self._time_label.setObjectName("statTime")
        stats_row.addWidget(self._scanned_label)
        stats_row.addWidget(self._online_label)
        stats_row.addWidget(self._offline_label)
        stats_row.addWidget(self._time_label)
        stats_row.addStretch(1)
        stats_layout.addLayout(stats_row)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanProgress")
        self._progress.setRange(0, SCAN_TOTAL_ADDRESSES)
        self._progress.setValue(0)
        stats_layout.addWidget(self._progress)

        root.addWidget(stats_card)

        # ---- 内容：网格 + 右侧面板 ----
        content = QHBoxLayout()
        content.setSpacing(12)

        # 左：网格
        # 注意：`left_layout` 的**左/右边距必须为 0**。网格控件自己已有 PAD_X 内边距，
        # 若这里再加一层，就会白白吃掉本可用于网格的横向空间。
        left = QWidget()
        # = 网格 sizeHint 宽 + 垂直滚动条宽（约 10~12px）。
        # 必须把滚动条的占位算进来：网格区在窄/矮窗口下会出现**垂直**滚动条，
        # 而 QScrollArea 的垂直滚动条是从视口宽度里扣的，若不留这 10px，
        # 网格就会差 10px 放不下而多出一条**横向**滚动条（实测 960px 下即为 10px）。
        left.setMinimumWidth(372)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._grid = ScanGridWidget()
        # 让网格把可用宽度吃满（自适应格子尺寸），避免右侧留出大片空位。
        # 具体缩放逻辑见 ScanGridWidget.set_available_width()。
        grid_scroll.setWidget(self._grid)
        grid_scroll.viewport().installEventFilter(self)
        self._grid_scroll = grid_scroll
        left_layout.addWidget(grid_scroll, 1)

        # 图例 + 提示**合并到同一行**：原本图例与提示各占一行（共约 40px），
        # 在 960×640 下这 40px 直接决定「页面要不要出纵向滚动条」。
        # 合并后图例在左、提示在右（用 stretch 顶开），语义不变但只占一行。
        legend = QHBoxLayout()
        legend.setSpacing(14)
        legend.addWidget(self._legend_item(PALETTE["success"], "在线"))
        legend.addWidget(self._legend_item(PALETTE["danger"], "离线"))
        legend.addWidget(self._legend_item(PALETTE["bg_cell_idle"], "未检测"))
        legend.addStretch(1)
        tip = QLabel("💡 双击绿色方格可打开共享")
        tip.setObjectName("hintLabel")
        legend.addWidget(tip)
        left_layout.addLayout(legend)

        content.addWidget(left, 1)

        # 右：详情 + 在线列表
        # 关键：右栏**必须可收缩**（只用 minimum/maximum，绝不能用 setFixedWidth）。
        # 原因：窗口横向预算 = 200(sidebar) + 48(页边距) + 476(网格) + 16(spacing) + 右栏。
        # 默认窗口宽 1100 → 右栏最多只能拿 360。若写死 380 就会撑破窗口，
        # 使 QScrollArea 误判为"内容放不下"而弹出横向滚动条（用户报的 16/32/48 列看不到）。
        right = QWidget()
        right.setMinimumWidth(SCAN_RIGHT_PANEL_MIN_W)
        right.setMaximumWidth(SCAN_RIGHT_PANEL_MAX_W)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        # 详情卡
        # 垂直空间很紧张（960×640 下右栏可用高仅约 380px，要装下详情卡 + 在线列表卡）。
        # 故详情卡收紧内外边距：内容本身只有 5 行文字，不需要 16px 的呼吸感，
        # 省下的每 1px 都留给下方在线列表（用户反馈「一屏看不到几条」）。
        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        detail_layout.setSpacing(5)

        detail_title = QLabel("IP 详情")
        detail_title.setObjectName("blockTitle")
        detail_layout.addWidget(detail_title)

        self._detail_ip_label = QLabel("—")
        self._detail_ip_label.setObjectName("cardIp")
        detail_layout.addWidget(self._detail_ip_label)

        self._detail_status = QLabel("状态：未检测")
        detail_layout.addWidget(self._detail_status)

        self._detail_latency = QLabel("延迟：--")
        detail_layout.addWidget(self._detail_latency)

        self._detail_time = QLabel("检测时间：--")
        self._detail_time.setObjectName("hintLabel")
        detail_layout.addWidget(self._detail_time)

        # 三个按钮横向并排时最小宽 = 其 minHint 之和 ≈ 358px，会把右栏顶到 358
        # （覆盖掉 setMinimumWidth(256)），进而挤压左侧网格区。
        # 故这里把它们也放进可换行的 FlowRow —— 窄右栏时自动折成两行，
        # 右栏因此能真正收缩到 SCAN_RIGHT_PANEL_MIN_W。
        detail_btns = FlowRow(h_gap=8, v_gap=6)
        self._copy_btn = QPushButton("复制 IP")
        self._copy_btn.setObjectName("ghostBtn")
        self._copy_btn.clicked.connect(self.on_copy_detail_ip)
        detail_btns.add_item(self._copy_btn)

        self._quick_ping_btn = QPushButton("快速 Ping")
        self._quick_ping_btn.setObjectName("ghostBtn")
        self._quick_ping_btn.clicked.connect(self.on_quick_ping_detail)
        detail_btns.add_item(self._quick_ping_btn)

        self._open_share_btn = QPushButton("打开共享")
        self._open_share_btn.setObjectName("openShareBtn")
        self._open_share_btn.setEnabled(False)
        self._open_share_btn.clicked.connect(self.on_open_share_detail)
        detail_btns.add_item(self._open_share_btn)
        detail_btns.apply_style()
        detail_layout.addWidget(detail_btns)

        # 详情卡：内容行数与文字长度都固定 → 垂直方向不参与拉伸。
        # 用 Maximum 策略把它锁在 sizeHint 高度，避免它在空间紧张时仍去抢行。
        from PySide6.QtWidgets import QSizePolicy as _QSP

        detail_card.setSizePolicy(_QSP.Policy.Preferred, _QSP.Policy.Maximum)
        right_layout.addWidget(detail_card)

        # 在线列表卡
        # 两个垂直空间的策略：
        # 1. 详情卡固定高度（内容不会变），故 `right_layout.addWidget(detail_card)` 不给 stretch；
        # 2. 本卡拿满剩余空间（stretch=1），并给表格设**最小高度** ——
        #    否则在 960×640 下表格会被压到只剩 28px 视口（一屏 0~1 行），
        #    用户会觉得"在线列表是坏的"。
        list_card = QFrame()
        list_card.setObjectName("card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(14, 12, 14, 12)
        list_layout.setSpacing(6)

        self._online_title = QLabel("在线列表 (0)")
        self._online_title.setObjectName("blockTitle")
        list_layout.addWidget(self._online_title)

        self._online_table = QTableWidget(0, 2)
        # 至少露出 4 行（4 × 30px 行高 + 表头 ≈ 150px），低于此值不如直接给滚动区
        self._online_table.setMinimumHeight(150)
        self._online_table.setHorizontalHeaderLabels(["IP", "延迟"])
        self._online_table.verticalHeader().setVisible(False)
        self._online_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._online_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._online_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        # ---- 修复「在线列表一个 IP 占一行」----
        # 右栏只有 256~360px，IP 列（Stretch）在窄宽下会被压到不足一个 "192.168.1.100"，
        # Qt 默认 wordWrap=True 就会把 IP 折成两行，看起来"一行只显示一个"。
        # 关掉换行 + 省略号，并让列宽按内容撑开，IP 永远单行完整显示。
        self._online_table.setWordWrap(False)
        self._online_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        # 行高固定，避免 QSS padding 与折行叠加把行撑高（否则一屏只看得到几条）
        self._online_table.verticalHeader().setDefaultSectionSize(30)
        self._online_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        self._online_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._online_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._online_table.horizontalHeader().setStretchLastSection(True)
        self._online_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._online_table.cellDoubleClicked.connect(self._on_online_row_double)
        list_layout.addWidget(self._online_table, 1)

        self._copy_all_btn = QPushButton("📋 复制全部")
        self._copy_all_btn.setObjectName("ghostBtn")
        self._copy_all_btn.clicked.connect(self.on_copy_all)
        list_layout.addWidget(self._copy_all_btn)

        right_layout.addWidget(list_card, 1)
        content.addWidget(right, 0)

        root.addLayout(content, 1)

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hintLabel")
        return label

    @staticmethod
    def _legend_item(color: str, text: str) -> QWidget:
        """图例项：色块 + 文字。"""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        swatch = QLabel()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(f"background: {color}; border-radius: 3px;")
        lay.addWidget(swatch)
        lbl = QLabel(text)
        lbl.setObjectName("hintLabel")
        lay.addWidget(lbl)
        return w

    # ------------------------------------------------------------------ #
    # 信号
    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._scanner.batch_ready.connect(self.on_batch)
        self._scanner.stats_changed.connect(self.update_stats)
        self._scanner.finished.connect(self.on_scan_finished)

        # 网卡列表：工作线程 → 主线程（跨线程自动 QueuedConnection）
        self._adapters_ready.connect(self._on_adapters_ready)

        self._grid.cellClicked.connect(self.on_cell_clicked)
        self._grid.cellDoubleClicked.connect(self.on_cell_double_clicked)
        self._grid.contextMenuRequested.connect(self.show_context_menu)
        self._grid.hoverChanged.connect(self.on_hover)

    # ------------------------------------------------------------------ #
    # 事件过滤：让网格随可用宽度自适应缩放
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):  # noqa: N802
        """监听网格滚动区视口的尺寸变化 → 重算格子尺寸。

        为什么用事件过滤器而不是重写 `resizeEvent`：
        需要的是**视口（viewport）**的宽度，而视口是 QScrollArea 内部对象，
        从 ScanGridWidget 或 PageScan 都拿不到它的 resize 事件。
        """
        if obj is getattr(self, "_grid_scroll", None) or (
            getattr(self, "_grid_scroll", None) is not None
            and obj is self._grid_scroll.viewport()
        ):
            if event.type() == QEvent.Type.Resize:
                vp_w = self._grid_scroll.viewport().width()
                # 预留垂直滚动条宽度：网格会在窄/矮窗口下出现垂直滚动条，
                # 若不预留，网格差几像素会多出一条横向滚动条。
                self._grid.set_available_width(vp_w - 2)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # 网卡 / 网段
    # ------------------------------------------------------------------ #
    def load_adapters(self) -> None:
        """后台加载网卡并回填网段（不阻塞 UI）。"""
        self._pool.submit(self._adapters_worker)

    def _adapters_worker(self) -> None:
        """工作线程：读取网卡列表，回主线程填充。

        ⚠️ **必须用信号而非 `QTimer.singleShot`**：
        从工作线程调用 `QTimer.singleShot(0, cb)` 时，定时器会挂到该工作线程上，
        而工作线程没有事件循环 → 回调**永远不执行且不报错**。
        这正是此前「网卡获取为空」的根因（实测回调 0 次）。
        """
        adapters = self._netsh.list_adapters()
        self._adapters_ready.emit(adapters)

    def _on_adapters_ready(self, adapters: list) -> None:
        """主线程：填充网卡下拉 + 自动带出网段。"""
        self._adapters = list(adapters)
        self._adapter_combo.blockSignals(True)
        self._adapter_combo.clear()
        for info in self._adapters:
            self._adapter_combo.addItem(info.name)
        self._adapter_combo.blockSignals(False)

        if not self._adapters:
            # 网卡读不到也**不能卡住用户**：仍按需求 3 尝试推断本机网段
            self.status_message.emit(MSG_NO_ADAPTER, "warning")
            self._auto_fill_subnet()
            return

        # 默认选中「最可能是真实出口」的那块网卡，而不是列表第一项。
        # 否则会默认落在 VMware/VirtualBox 虚拟网卡上（实测填出 192.168.80），
        # 而用户真实网段是 192.168.2（以太网，有网关）—— 与需求 3 的语义不符。
        best = self._best_adapter_index()
        self._adapter_combo.setCurrentIndex(best)
        self._resize_adapter_combo()
        self._auto_fill_subnet(force=True)

    def _resize_adapter_combo(self) -> None:
        """按**当前选中**的网卡名重新定宽（真实数据到达后调用）。

        为什么不按最长网卡名：闭合的下拉只显示选中项，按最长项定宽会把控件
        撑得过大（"VMware Network Adapter VMnet1" 有 348px，一旦被选中或参与
        计算就把整行挤爆）。全名靠 tooltip + 展开下拉看。

        下界是构造时设的「5 个中文字符」，上界是 `ADAPTER_COMBO_MAX_W`
        —— 网卡名可能有 "VMware Network Adapter VMnet1" 这种超长的，
        不设上限会让它独占大半行宽。
        """
        _fm = self._adapter_combo.fontMetrics()
        _min_w = _fm.horizontalAdvance("网网网网网") + COMBO_CHROME_W
        text = self._adapter_combo.currentText()
        _w = _fm.horizontalAdvance(text) + COMBO_CHROME_W + 8
        _w = max(_min_w, min(_w, ADAPTER_COMBO_MAX_W))
        self._adapter_combo.setMinimumWidth(_w)
        self._adapter_combo.setMaximumWidth(_w)

    def _best_adapter_index(self) -> int:
        """按评分挑选"真实出口"网卡，返回其在下拉框中的索引。

        评分规则（与 `NetshService.guess_local_subnet` 保持一致）：
        有网关（真实出口）+4、已连接 +2、虚拟网卡 -3、回环/APIPA 直接跳过。
        """
        best_idx, best_score = 0, -999
        for idx, info in enumerate(self._adapters):
            ip = (info.ip or "").strip()
            if ip.count(".") != 3 or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            score = 0
            if info.is_up:
                score += 2
            if getattr(info, "gateway", ""):
                score += 4
            low = info.name.lower()
            if "vmware" in low or "vethernet" in low or "virtualbox" in low:
                score -= 3
            if score > best_score:
                best_idx, best_score = idx, score
        return best_idx

    def _on_adapter_changed(self, _idx: int) -> None:
        """网卡切换 → 按新选中名重定宽 + 回填网段前三段。"""
        self._resize_adapter_combo()
        self._auto_fill_subnet()

    def _auto_fill_subnet(self, force: bool = False) -> None:
        """把网段填成"当前所选网卡的网段"。

        需求 3：网段为空时默认使用本机当前网段，减少操作。
        `force=True` 用于启动阶段（覆盖掉初始化时写入的默认网段）。
        """
        name = self._adapter_combo.currentText().strip()
        info = next((a for a in self._adapters if a.name == name), None)

        # 1) 优先用当前所选网卡的 IP
        subnet = ""
        if info is not None and info.ip and info.ip.count(".") == 3:
            subnet = ".".join(info.ip.split(".")[:3])

        # 2) 退路：任一「已连接且有 IP」的网卡（VMware 虚拟网卡通常排在前面，
        #    但它们也能给出一个可扫网段，比空着好）
        if not subnet:
            for a in self._adapters:
                if a.ip and a.ip.count(".") == 3 and a.is_up:
                    subnet = ".".join(a.ip.split(".")[:3])
                    break

        # 3) 再退路：询问系统默认网关（网卡解析失败时的兜底）
        if not subnet:
            subnet = self._netsh.guess_local_subnet()

        if not subnet:
            return

        current = self._subnet_combo.currentText().strip()
        if force or not current or not is_valid_subnet_prefix(current):
            self._subnet_combo.setCurrentText(subnet)
        self._grid.set_prefix(subnet)

    def _current_subnet(self) -> str:
        """当前网段前三段（去空白）。"""
        return self._subnet_combo.currentText().strip()

    # ------------------------------------------------------------------ #
    # 扫描控制
    # ------------------------------------------------------------------ #
    def on_toggle(self) -> None:
        """「开始 / 停止」合并按钮的统一点击入口。"""
        if self._scanner.running:
            self.on_stop()
        else:
            self.on_start()

    def _set_running_ui(self, running: bool) -> None:
        """按运行状态切换合并按钮的外观与文案（需求：一个按钮两态）。

        ⚠️ 这里**只能**引用确有创建的控件。历史教训（P0 崩溃）：
        合并按钮时删掉了 `_start_btn`/`_stop_btn` 的创建，却漏改本方法，
        一按「开始」就抛 AttributeError。
        `tests/test_intent_audit.py::test_set_running_ui_only_touches_existing_widgets`
        会静态扫描本方法体内引用的 `self._xxx` 是否都在 `__init__`/`_build_ui` 里赋值过。
        """
        if running:
            self._toggle_btn.setText(self._TOGGLE_TEXT_RUNNING)
            self._toggle_btn.setObjectName("dangerBtn")
        else:
            self._toggle_btn.setText(self._TOGGLE_TEXT_IDLE)
            self._toggle_btn.setObjectName("primaryBtn")
        # objectName 变了必须刷新样式，否则 QSS 不会重新匹配
        self._toggle_btn.style().unpolish(self._toggle_btn)
        self._toggle_btn.style().polish(self._toggle_btn)
        # 运行中禁止改动网段/网卡/重置，避免扫描参数被中途换掉
        self._reset_btn.setEnabled(not running)
        self._subnet_combo.setEnabled(not running)
        self._adapter_combo.setEnabled(not running)

    def on_start(self) -> None:
        """开始扫描：校验 → 重置网格 → 提交扫描。"""
        if self._scanner.running:
            return
        subnet = self._current_subnet()
        # 需求 3：网段为空 → 自动取本机当前网段，不打断用户
        if not subnet:
            self._auto_fill_subnet(force=True)
            subnet = self._current_subnet()
        if not subnet:
            self.status_message.emit(MSG_SCAN_BAD_SUBNET, "warning")
            return
        if not is_valid_subnet_prefix(subnet):
            # 区分格式错与范围错
            parts = subnet.split(".")
            if len(parts) != 3:
                self.status_message.emit(MSG_SCAN_BAD_SUBNET, "warning")
            else:
                self.status_message.emit(MSG_SCAN_RANGE, "warning")
            return

        concurrency = int(self._concurrency_combo.currentText())
        timeout_ms = int(self._timeout_combo.currentData())

        self._online.clear()
        self._grid.set_prefix(subnet)
        self._grid.start_pulse()
        self._refresh_online_table()
        self._sync_stats(ScanStats(total=SCAN_TOTAL_ADDRESSES, running=True))
        self._set_running_ui(True)

        self._scanner.start(subnet, concurrency, timeout_ms)
        self.status_message.emit(f"正在扫描 {subnet}.1 ~ {subnet}.254 …", "info")

    def on_stop(self) -> None:
        """停止扫描。"""
        if not self._scanner.running:
            return
        self._scanner.stop()
        self._grid.stop_pulse()
        self._set_running_ui(False)
        self.status_message.emit(MSG_SCAN_STOPPED, "warning")

    def on_reset(self) -> None:
        """重置网格与统计（运行中禁用）。"""
        if self._scanner.running:
            return
        self._grid.reset_all()
        self._online.clear()
        self._refresh_online_table()
        self._sync_stats(ScanStats(total=SCAN_TOTAL_ADDRESSES))
        self._clear_detail()

    # ------------------------------------------------------------------ #
    # 扫描回调
    # ------------------------------------------------------------------ #
    def on_batch(self, results: list[ScanResult]) -> None:
        """收到一批扫描结果：更新网格 + 在线集合。"""
        self._grid.update_cells(results)
        changed = False
        for r in results:
            if r.status == "online":
                if r.ip not in self._online:
                    self._online[r.ip] = r.latency_ms
                    changed = True
        if changed:
            self._refresh_online_table()

    def update_stats(self, stats: ScanStats) -> None:
        """更新统计条与进度条。"""
        self._sync_stats(stats)

    def _sync_stats(self, stats: ScanStats) -> None:
        self._scanned_label.setText(f"已扫描 {stats.scanned} / {SCAN_TOTAL_ADDRESSES}")
        self._online_label.setText(f"在线 {stats.online}")
        self._offline_label.setText(f"空闲 {stats.offline}")
        self._time_label.setText(f"耗时 {stats.elapsed_s:.1f}s")
        self._progress.setValue(min(stats.scanned, SCAN_TOTAL_ADDRESSES))

    def on_scan_finished(self, stats: ScanStats) -> None:
        """扫描结束：停动画、恢复控件、提示。"""
        self._grid.stop_pulse()
        self._set_running_ui(False)
        self._sync_stats(stats)
        if stats.online == 0 and stats.scanned > 0:
            self.status_message.emit(
                f"{MSG_SCAN_DONE.format(s=stats.elapsed_s)}；{MSG_SCAN_ALL_OFFLINE}", "warning"
            )
        else:
            self.status_message.emit(MSG_SCAN_DONE.format(s=stats.elapsed_s), "success")

    # ------------------------------------------------------------------ #
    # 格子交互
    # ------------------------------------------------------------------ #
    def on_cell_clicked(self, ip: str) -> None:
        """单击：更新详情。"""
        if not ip:
            return
        self._show_detail(ip)

    def on_hover(self, ip: str) -> None:
        """悬停：可选状态栏提示（此处仅记录，不打扰）。"""
        _ = ip

    def on_cell_double_clicked(self, ip: str) -> None:
        """双击：在线 → 打开共享；非在线 → 状态栏黄字轻提示。"""
        cell = self._grid.cell_by_ip(ip)
        if cell is None:
            return
        if cell.status == "online":
            self.open_share(ip)
        elif cell.status == "pending":
            # 扫描中/未出结果的格子：无动作
            return
        else:
            self.status_message.emit(MSG_SHARE_NOT_ONLINE, "warning")

    # ------------------------------------------------------------------ #
    # 右键菜单
    # ------------------------------------------------------------------ #
    def show_context_menu(self, ip: str, global_pos) -> None:
        """右键菜单：打开共享 / 快速 Ping / 复制 IP（非在线格前两项禁用）。"""
        cell = self._grid.cell_by_ip(ip)
        online = cell is not None and cell.status == "online"

        menu = QMenu(self)
        act_share = menu.addAction("打开共享")
        act_share.setEnabled(online)
        act_share.triggered.connect(lambda: self.open_share(ip))

        act_ping = menu.addAction("快速 Ping")
        act_ping.setEnabled(online)
        act_ping.triggered.connect(lambda: self.quick_ping(ip))

        menu.addSeparator()
        act_copy = menu.addAction("复制 IP")
        act_copy.triggered.connect(lambda: self._copy_text(ip))

        menu.exec(global_pos)

    # ------------------------------------------------------------------ #
    # 打开共享（F2-13，主线程 Popen，不预探 445）
    # ------------------------------------------------------------------ #
    def open_share(self, ip: str) -> None:
        """打开 `\\\\<ip>` 网络共享（主线程直接 Popen，不等待，不预探 445）。"""
        if not is_ipv4(ip):
            self.status_message.emit(MSG_SHARE_BAD_IP, "danger")
            return
        self.status_message.emit(MSG_SHARE_OPENING.format(ip=ip), "info")
        try:
            popen_silent(["explorer.exe", f"\\\\{ip}"], shell=False)  # noqa: S603
        except OSError as e:
            self.status_message.emit(MSG_SHARE_FAILED.format(err=e), "danger")
            return
        self.status_message.emit(MSG_SHARE_REQUESTED.format(ip=ip), "normal")

    def on_open_share_detail(self) -> None:
        """详情区「打开共享」按钮。"""
        if self._detail_ip:
            self.open_share(self._detail_ip)

    # ------------------------------------------------------------------ #
    # 快速 Ping（F2-12 跨页跳转）
    # ------------------------------------------------------------------ #
    def quick_ping(self, ip: str) -> None:
        """请求主窗口跳转到 Ping 页并自动执行。"""
        if not ip:
            return
        self.quick_ping_requested.emit(ip)

    def on_quick_ping_detail(self) -> None:
        if self._detail_ip:
            self.quick_ping(self._detail_ip)

    # ------------------------------------------------------------------ #
    # 复制
    # ------------------------------------------------------------------ #
    def on_copy_detail_ip(self) -> None:
        if self._detail_ip:
            self._copy_text(self._detail_ip)

    def on_copy_all(self) -> None:
        """复制全部在线 IP（换行分隔）。"""
        if not self._online:
            return
        text = "\n".join(sorted(self._online.keys(), key=self._ip_sort_key))
        self._copy_text(text)

    def _copy_text(self, text: str) -> None:
        """写剪贴板并提示。"""
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self.status_message.emit(MSG_COPIED, "normal")

    @staticmethod
    def _ip_sort_key(ip: str) -> tuple[int, int, int, int]:
        """IP 排序键。"""
        try:
            parts = [int(x) for x in ip.split(".")]
            if len(parts) == 4:
                return (parts[0], parts[1], parts[2], parts[3])
        except ValueError:
            pass
        return (999, 999, 999, 999)

    # ------------------------------------------------------------------ #
    # 详情 / 在线列表
    # ------------------------------------------------------------------ #
    def _show_detail(self, ip: str) -> None:
        """更新详情区。"""
        cell = self._grid.cell_by_ip(ip)
        self._detail_ip = ip
        self._detail_ip_label.setText(ip)
        if cell is None:
            self._detail_status.setText("状态：未检测")
            self._detail_latency.setText("延迟：--")
            self._detail_time.setText("检测时间：--")
            self._open_share_btn.setEnabled(False)
            return

        status_cn = STATUS_TEXT.get(cell.status, "未检测")
        color_key = {"online": "success", "offline": "danger"}.get(cell.status, "text_secondary")
        self._detail_status.setText(f"状态：{status_cn}")
        self._detail_status.setStyleSheet(f"color: {PALETTE[color_key]};")
        latency = f"{cell.latency_ms} ms" if cell.latency_ms is not None else "--"
        self._detail_latency.setText(f"延迟：{latency}")
        self._detail_time.setText(f"检测时间：{cell.checked_at or '--'}")
        self._open_share_btn.setEnabled(cell.status == "online")

    def _clear_detail(self) -> None:
        self._detail_ip = ""
        self._detail_ip_label.setText("—")
        self._detail_status.setText("状态：未检测")
        self._detail_status.setStyleSheet("")
        self._detail_latency.setText("延迟：--")
        self._detail_time.setText("检测时间：--")
        self._open_share_btn.setEnabled(False)

    def _refresh_online_table(self) -> None:
        """重建在线列表。"""
        items = sorted(self._online.keys(), key=self._ip_sort_key)
        self._online_title.setText(f"在线列表 ({len(items)})")
        self._online_table.setRowCount(len(items))
        for row, ip in enumerate(items):
            ip_item = QTableWidgetItem(ip)
            latency = self._online[ip]
            lat_item = QTableWidgetItem(f"{latency}ms" if latency is not None else "--")
            self._online_table.setItem(row, 0, ip_item)
            self._online_table.setItem(row, 1, lat_item)

    def _on_online_row_double(self, row: int, _col: int) -> None:
        """在线列表双击行 → 打开共享。"""
        item = self._online_table.item(row, 0)
        if item is not None:
            self.open_share(item.text())

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def on_enter(self) -> None:
        """进入页面：若尚未加载网卡则加载。"""
        if not self._adapters:
            self.load_adapters()

    def shutdown(self) -> None:
        """关闭时停止扫描、动画与线程池。"""
        if self._scanner.running:
            self._scanner.stop()
        self._grid.stop_pulse()
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._pool.shutdown(wait=False)
