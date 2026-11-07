"""NetTool UI 层：页面三 · Ping 工具（PRD 5.3 / 原型 3）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    LOG_FMT_SUMMARY,
    LOG_MAX_LINES,
    MSG_PING_EMPTY_HOST,
    PALETTE,
    PING_DEFAULT_INTERVAL_S,
    PING_DEFAULT_TIMEOUT_MS,
    PING_INTERVAL_CHOICES,
    PING_SINGLE_COUNT,
)
from app.models import PingResult, PingStats
from app.netsh_service import NetshService
from app.ping_service import PingService, format_log_line
from app.ui.flow_row import (
    COMBO_CHROME_W,
    FlowRow,
    fit_button,
    fit_button_multi,
    fit_combo,
    fit_edit,
)
from app.ui.widgets import ChipButton

# 模式
_MODE_SINGLE = "单次 ping"
_MODE_CONTINUOUS = "持续 ping"


class PagePing(QWidget):
    """页面三：单次 / 持续 ping，日志 + 统计。"""

    status_message = Signal(str, str)
    # 工作线程 → 主线程的回传通道（见 app/ui/thread_bridge.py 的说明）。
    # ⚠️ 请勿改回 QTimer.singleShot：从工作线程调用它会静默丢弃回调。
    _gateway_ready = Signal(str)

    # 起停合并按钮的两态文案（定宽时按较长者算，防止切换态被裁）
    _TOGGLE_TEXT_IDLE = "▶ 开始 Ping"
    _TOGGLE_TEXT_RUNNING = "■ 停止"

    def __init__(
        self,
        ping_service: PingService,
        netsh_service: NetshService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ping = ping_service
        self._netsh = netsh_service
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ping-page")
        self._stats = PingStats()
        self._running: bool = False
        self._line_count: int = 0
        self._auto_start_pending: bool = False

        # 持续 ping 计时器（主线程）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.on_tick)

        self._build_ui()
        self._connect_signals()
        self._sync_stats()
        self._on_mode_changed(0)

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Ping 工具")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # ---- 控制卡 ----
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        # 控制条放进可换行容器：普通 QHBoxLayout 不允许换行，其最小宽 = 子控件最小宽
        # 之和，一旦窗口收窄就会把「目标地址」输入框压到「模式」标签上（用户截图所见）。
        row = FlowRow(h_gap=8, v_gap=6)
        card_layout.addWidget(row)

        # ------------------------------------------------------------------ #
        # 控件宽度**一律运行时按自身文本量**，不写死像素
        # ------------------------------------------------------------------ #
        # 文本像素宽取决于实际生效字体（`main.py::setFont` + QSS `font-size` 共同决定），
        # 离线估算偏差可达 40%+（离线 "IP 或域名, 如 8.8.8.8" = 289px，运行态 221px）。
        # 故统一走 app/ui/flow_row.py 的 fit_combo/fit_edit/fit_button*。
        # 用户反馈：清空日志按钮被挤到第二行 → 缩短目标输入框，让整行同排。
        # ------------------------------------------------------------------ #

        # 标签用「目标」而非「目标地址」：占位符已说明是地址，加「地址」二字纯冗余，
        # 却要多占约 30px 横向空间，是控制条折行的直接推手。
        row.addWidget(self._hint("目标"))
        self._host_input = QLineEdit()
        self._host_input.setPlaceholderText("IP 或域名, 如 8.8.8.8")
        # 用户要求「缩短目标输入框」→ 定宽基准从"占位符全长 + 28px 余量"改回
        # **恰好等于占位符**（fit_edit 默认行为，extra=0）。
        # ⚠️ 不要传负 extra：实测 `extra=-10` 就把占位符尾巴省略成 "8.8..."，
        # 用户看不到完整示例 IP —— 那不是"缩短"，是丢信息。
        # 「清空日志」能否回到同一行不靠这里抠 10px，而靠 `_ensure_polished`
        # 让所有 fit_* 按**真实字体**量宽（见 flow_row.py 的说明）。
        fit_edit(self._host_input)
        self._host_input.returnPressed.connect(self.on_toggle)
        row.addWidget(self._host_input)

        row.addWidget(self._hint("模式"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([_MODE_SINGLE, _MODE_CONTINUOUS])
        self._mode_combo.setCurrentText(_MODE_CONTINUOUS)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        # 必须关掉"按最长选项算最小宽"的行为，否则 minimumSizeHint 会盖掉 setMaximumWidth
        self._mode_combo.setMinimumContentsLength(0)
        self._mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        fit_combo(self._mode_combo)
        row.addWidget(self._mode_combo)

        row.addWidget(self._hint("间隔"))
        self._interval_combo = QComboBox()
        for sec in PING_INTERVAL_CHOICES:
            label = f"{sec:g} 秒"
            self._interval_combo.addItem(label, sec)
        self._interval_combo.setCurrentIndex(
            PING_INTERVAL_CHOICES.index(PING_DEFAULT_INTERVAL_S)
        )
        self._interval_combo.setMinimumContentsLength(0)
        self._interval_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        fit_combo(self._interval_combo)
        row.addWidget(self._interval_combo)

        # 起停按钮**合并为一个**：空闲态「▶ 开始 Ping」，工作中切换为「■ 停止」。
        # 与扫描页保持同一交互范式（一处学会，两处通用）。
        #
        # ⚠️ 这里**故意按当前态文案定宽**（而不是两态取长），原因：
        # 空闲态 "▶ 开始 Ping" 需 131px，工作中 "■ 停止" 只需 80px —— 差 51px。
        # 若按长者定宽，等于在空闲态白占 51px，正是把「清空日志」挤到第二行的推手。
        # 切成 "■ 停止" 时按钮**自然收窄**，FlowRow 会因总宽变小而保持单行，
        # 观感上是"按钮随状态收敛"，不是缺陷。切换后重新 fit 一次即可。
        self._toggle_btn = QPushButton(self._TOGGLE_TEXT_IDLE)
        self._toggle_btn.setObjectName("primaryBtn")
        fit_button(self._toggle_btn)
        self._toggle_btn.clicked.connect(self.on_toggle)
        row.add_item(self._toggle_btn)

        self._clear_btn = QPushButton("🗑 清空日志")
        self._clear_btn.setObjectName("ghostBtn")
        fit_button(self._clear_btn)
        self._clear_btn.clicked.connect(self.on_clear)
        row.add_item(self._clear_btn)

        # 全部 item 就位后做首次排版（此后由 resizeEvent 自动重排）
        row.apply_style()

        # 快捷地址（同样放进可换行容器：4 个 chip 在窄窗口下会顶破卡片）
        quick = FlowRow(h_gap=8, v_gap=6)
        quick.addWidget(self._hint("快捷："))
        self._chip_gateway = ChipButton("网关")
        self._chip_gateway.clicked.connect(self._fill_gateway)
        self._chip_8888 = ChipButton("8.8.8.8")
        self._chip_8888.clicked.connect(lambda: self._host_input.setText("8.8.8.8"))
        self._chip_114 = ChipButton("114.114.114.114")
        self._chip_114.clicked.connect(lambda: self._host_input.setText("114.114.114.114"))
        self._chip_local = ChipButton("本机")
        self._chip_local.clicked.connect(lambda: self._host_input.setText("127.0.0.1"))
        for chip in (self._chip_gateway, self._chip_8888, self._chip_114, self._chip_local):
            quick.addWidget(chip)
        quick.apply_style()
        card_layout.addWidget(quick)

        root.addWidget(card)

        # ---- 统计条 ----
        stats_card = QFrame()
        stats_card.setObjectName("card")
        stats_layout = QHBoxLayout(stats_card)
        stats_layout.setContentsMargins(16, 12, 16, 12)
        stats_layout.setSpacing(24)

        self._stat_sent = QLabel("发送 0")
        self._stat_sent.setObjectName("statSent")
        self._stat_recv = QLabel("接收 0")
        self._stat_recv.setObjectName("statRecv")
        self._stat_loss = QLabel("丢失 0 (0%)")
        self._stat_loss.setObjectName("statLoss")
        self._stat_avg = QLabel("平均 --")
        self._stat_avg.setObjectName("statAvg")
        self._stat_min = QLabel("最小 --")
        self._stat_min.setObjectName("statMin")
        self._stat_max = QLabel("最大 --")
        self._stat_max.setObjectName("statMax")
        for lbl in (
            self._stat_sent, self._stat_recv, self._stat_loss,
            self._stat_avg, self._stat_min, self._stat_max,
        ):
            stats_layout.addWidget(lbl)
        stats_layout.addStretch(1)
        root.addWidget(stats_card)

        # ---- 日志卡 ----
        log_card = QFrame()
        log_card.setObjectName("card")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_title = QLabel("日志")
        log_title.setObjectName("blockTitle")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        self._log_counter = QLabel(f"上限 {LOG_MAX_LINES} 行 · 当前 0 行")
        self._log_counter.setObjectName("logCounter")
        log_header.addWidget(self._log_counter)
        log_layout.addLayout(log_header)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(LOG_MAX_LINES)  # F3-08：500 行上限
        log_layout.addWidget(self._log_view, 1)

        root.addWidget(log_card, 1)

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hintLabel")
        return label

    # ------------------------------------------------------------------ #
    # 信号
    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._ping.one_result.connect(self.on_ping_result)
        self._ping.done.connect(self.on_batch_done)
        # 默认网关查询：工作线程 → 主线程
        self._gateway_ready.connect(self._set_host)

    # ------------------------------------------------------------------ #
    # 模式
    # ------------------------------------------------------------------ #
    def _on_mode_changed(self, _idx: int) -> None:
        """持续模式下间隔可选，单次模式下禁用间隔下拉。"""
        continuous = self._mode_combo.currentText() == _MODE_CONTINUOUS
        self._interval_combo.setEnabled(continuous and not self._running)

    def _is_continuous(self) -> bool:
        return self._mode_combo.currentText() == _MODE_CONTINUOUS

    # ------------------------------------------------------------------ #
    # 开始 / 停止 / 清空
    # ------------------------------------------------------------------ #
    def on_toggle(self) -> None:
        """合并按钮的唯一入口：空闲 → 开始；工作中 → 停止。

        与扫描页 `PageScan.on_toggle` 同一范式。好处不只是省一个按钮：
        单按钮不存在"该按哪个"的歧义，也不会出现两按钮同时可用/同时禁用的中间态。
        """
        if self._running:
            self.on_stop()
        else:
            self.on_start()

    def on_start(self) -> None:
        """开始 ping（单次或持续）。"""
        if self._running:
            return
        host = self._host_input.text().strip()
        if not host:
            self.status_message.emit(MSG_PING_EMPTY_HOST, "warning")
            return

        self._ping.reset_cancel()
        self._running = True
        self._set_running_ui(True)

        if self._is_continuous():
            interval_s = float(self._interval_combo.currentData())
            # 立即执行一次，然后按间隔重复（U3）
            self._ping.ping_once_async(host, 1, PING_DEFAULT_TIMEOUT_MS)
            self._timer.start(int(interval_s * 1000))
        else:
            # 单次：4 包，一次提交
            self._ping.ping_once_async(host, PING_SINGLE_COUNT, PING_DEFAULT_TIMEOUT_MS)

    def on_stop(self) -> None:
        """停止持续 ping。"""
        if not self._running:
            return
        self._timer.stop()
        self._ping.cancel()
        self._running = False
        self._set_running_ui(False)
        self.status_message.emit("已停止", "normal")

    def on_clear(self) -> None:
        """清空日志与统计（运行中禁用）。"""
        if self._running:
            return
        self._log_view.clear()
        self._line_count = 0
        self._stats.reset()
        self._sync_stats()
        self._update_counter()

    def _set_running_ui(self, running: bool) -> None:
        """按运行状态切换合并按钮的外观与文案（需求：一个按钮两态）。

        ⚠️ 这里**只能**引用确有创建的控件。历史教训（扫描页 P0 崩溃）：
        合并按钮时删掉了 `_start_btn`/`_stop_btn` 的创建，却漏改本方法，
        一按「开始」就抛 `AttributeError`。
        `tests/test_intent_audit.py::test_set_running_ui_only_touches_existing_widgets`
        会静态扫描本方法体内引用的 `self._xxx` 是否都在 `__init__` 里赋值过。
        """
        if running:
            self._toggle_btn.setText(self._TOGGLE_TEXT_RUNNING)
            self._toggle_btn.setObjectName("dangerBtn")
        else:
            self._toggle_btn.setText(self._TOGGLE_TEXT_IDLE)
            self._toggle_btn.setObjectName("primaryBtn")
        # objectName 变了必须刷新样式，否则 QSS 不会重新匹配（按钮还是旧配色）
        self._toggle_btn.style().unpolish(self._toggle_btn)
        self._toggle_btn.style().polish(self._toggle_btn)
        # 文案变了要重新量宽：本按钮按**当前态**定宽（见 _build_ui 的说明），
        # "■ 停止" 比 "▶ 开始 Ping" 短 51px，重排后按钮自然收窄、整行仍单排。
        fit_button(self._toggle_btn)

        # 运行中禁止改动目标/模式/间隔，避免 ping 参数被中途换掉
        self._host_input.setEnabled(not running)
        self._mode_combo.setEnabled(not running)
        self._interval_combo.setEnabled((not running) and self._is_continuous())
        self._clear_btn.setEnabled(not running)

    # ------------------------------------------------------------------ #
    # 持续 ping tick
    # ------------------------------------------------------------------ #
    def on_tick(self) -> None:
        """持续模式每个间隔触发：派发一次 ping（防堆积）。"""
        if not self._running:
            self._timer.stop()
            return
        host = self._host_input.text().strip()
        if not host:
            self.on_stop()
            return
        # 防堆积：上一次未返回则跳过本次
        if self._ping.inflight > 0:
            return
        self._ping.ping_once_async(host, 1, PING_DEFAULT_TIMEOUT_MS)

    # ------------------------------------------------------------------ #
    # 结果回调
    # ------------------------------------------------------------------ #
    def on_ping_result(self, result: PingResult) -> None:
        """单包结果：累加统计 + 追加日志。"""
        if not self._running:
            # 停止后到达的结果丢弃（单次模式除外，单次模式 running 为 True 直到 done）
            if self._is_continuous():
                return
        self._stats.add(result)
        line, level = format_log_line(result)
        self.append_log(line, level)
        self._sync_stats()

    def on_batch_done(self, results: list[PingResult]) -> None:
        """一批（单次 4 包 或 持续 1 包）完成。"""
        if not self._is_continuous():
            # 单次模式：输出汇总行后结束
            summary = LOG_FMT_SUMMARY.format(
                ts=(results[0].timestamp if results else ""),
                sent=self._stats.sent,
                recv=self._stats.received,
                loss=self._stats.loss,
                loss_pct=f"{self._stats.loss_pct:g}",
                avg=(f"{self._stats.avg_ms:g}" if self._stats.avg_ms is not None else "--"),
                min=(self._stats.min_ms if self._stats.min_ms is not None else "--"),
                max=(self._stats.max_ms if self._stats.max_ms is not None else "--"),
            )
            self.append_log(summary, "summary")
            self._running = False
            self._set_running_ui(False)
            self._sync_stats()

    def _sync_stats(self) -> None:
        """刷新统计条。"""
        s = self._stats
        self._stat_sent.setText(f"发送 {s.sent}")
        self._stat_recv.setText(f"接收 {s.received}")
        self._stat_loss.setText(f"丢失 {s.loss} ({s.loss_pct:g}%)")
        self._stat_avg.setText(f"平均 {s.avg_ms:g}ms" if s.avg_ms is not None else "平均 --")
        self._stat_min.setText(f"最小 {s.min_ms}ms" if s.min_ms is not None else "最小 --")
        self._stat_max.setText(f"最大 {s.max_ms}ms" if s.max_ms is not None else "最大 --")

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def append_log(self, line: str, level: str = "normal") -> None:
        """追加一行着色日志并自动滚动。

        level ∈ {success, danger, summary, normal}
        颜色映射：success→绿, danger→红, summary→灰, normal→主文字。
        """
        color_map = {
            "success": PALETTE["success"],
            "danger": PALETTE["danger"],
            "summary": PALETTE["text_secondary"],
            "normal": PALETTE["text_primary"],
        }
        color = color_map.get(level, PALETTE["text_primary"])
        escaped = (
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        html = f'<span style="color:{color};white-space:pre;">{escaped}</span>'
        self._log_view.appendHtml(html)

        # 自动滚动到底部
        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log_view.setTextCursor(cursor)
        self._log_view.ensureCursorVisible()

        self._line_count += 1
        self._update_counter()

    def _update_counter(self) -> None:
        """更新「当前 N 行」计数（实时行数以控件 blockCount 为准）。"""
        current = self._log_view.blockCount()
        self._log_counter.setText(f"上限 {LOG_MAX_LINES} 行 · 当前 {current} 行")

    # ------------------------------------------------------------------ #
    # 快捷
    # ------------------------------------------------------------------ #
    def _fill_gateway(self) -> None:
        """填入当前默认网关（异步取一个网卡的网关）。"""
        self._pool.submit(self._gateway_worker)

    def _gateway_worker(self) -> None:
        """工作线程：查询默认网关，回主线程填入。"""
        gateway = ""
        try:
            adapters = self._netsh.list_adapters()
            for info in adapters:
                if info.gateway:
                    gateway = info.gateway
                    break
        except Exception:
            gateway = ""
        # ⚠️ 必须用信号而非 QTimer.singleShot：从工作线程调用会静默失效（回调永不执行）。
        #    详见 app/ui/thread_bridge.py。
        self._gateway_ready.emit(gateway)

    def _set_host(self, host: str) -> None:
        if host:
            self._host_input.setText(host)
        else:
            self.status_message.emit("未获取到默认网关，请手动输入", "warning")

    # ------------------------------------------------------------------ #
    # 跨页调用（F2-12：跳转 + 填入 + 切单次 + 自动执行）
    # ------------------------------------------------------------------ #
    def start_with(self, host: str, single_mode: bool = True, auto_run: bool = True) -> None:
        """由主窗口调用：填入地址、切模式、（可选）自动执行。"""
        if self._running:
            self.on_stop()
        self._host_input.setText(host)
        self._mode_combo.setCurrentText(_MODE_SINGLE if single_mode else _MODE_CONTINUOUS)
        self._stats.reset()
        self._sync_stats()
        if auto_run and host:
            self.on_start()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        """关闭时停止计时器、ping 任务与线程池。"""
        self._timer.stop()
        self._ping.cancel()
        self._ping.shutdown(wait=False)
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._pool.shutdown(wait=False)
