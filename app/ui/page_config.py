"""NetTool UI 层：页面一 · 网络配置管理（PRD 5.1 / 原型 1）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config_service import ConfigService
from app.constants import (
    MSG_APPLY_FAIL,
    MSG_APPLY_OK,
    MSG_CONFIG_CORRUPT,
    MSG_CONFIG_DELETED,
    MSG_CONFIG_SAVED,
    MSG_NEED_ADMIN,
    MSG_REFRESH_OK,
)
from app.models import AdapterInfo, Profile
from app.netsh_service import NetshService
from app.ui.widgets import ProfileCard, ResultBanner, Toast

# 后台执行器（配置读取 / netsh 变更）——复用单例线程池
from concurrent.futures import ThreadPoolExecutor


class PageConfig(QWidget):
    """页面一：方案列表 + 编辑表单 + 保存/应用/DHCP/导入导出/刷新状态。"""

    apply_finished = Signal(bool)   # 应用是否成功（供主窗口联动徽标）
    # 工作线程 → 主线程的回传通道。
    # ⚠️ 不要改回 QTimer.singleShot：从工作线程调用会静默失效
    #    （定时器挂在没有事件循环的线程上，回调永不执行）。详见 app/ui/thread_bridge.py。
    _apply_finished_internal = Signal(bool, str)
    _refresh_ready = Signal(list, dict)

    def __init__(
        self,
        config_service: ConfigService,
        netsh_service: NetshService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = config_service
        self._netsh = netsh_service
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cfg")
        self._cards: dict[str, ProfileCard] = {}
        self._selected_id: str = ""
        self._adapters: list[AdapterInfo] = []
        self._editing: bool = False
        self._busy: bool = False
        # 本次刷新是否为「自动触发」（自动触发不弹"状态已刷新"横幅）
        self._quiet_refresh: bool = False

        self._build_ui()
        # 跨线程回传信号（必须在 build_ui 之后连接）
        self._apply_finished_internal.connect(self._finish_apply)
        self._refresh_ready.connect(self._finish_refresh)
        # 加载配置（若加载过程中做了备份，提示用户）
        self._cfg.load()
        self._refresh_list()
        # 进入页面自动刷新一次状态（5.1.6 时机①）。
        # quiet=True：这是程序自己发起的刷新，不该弹"状态已刷新"打扰用户
        # （用户反馈"一打开软件就有这个提示，不知道刷了什么"）。
        QTimer.singleShot(200, lambda: self.on_refresh_status(quiet=True))

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # 页头
        header = QHBoxLayout()
        title = QLabel("网络配置")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)

        self._refresh_btn = QPushButton("🔄 刷新状态")
        self._refresh_btn.setObjectName("ghostBtn")
        self._refresh_btn.clicked.connect(lambda: self.on_refresh_status())
        header.addWidget(self._refresh_btn)

        self._import_btn = QPushButton("⬇ 导入")
        self._import_btn.setObjectName("ghostBtn")
        self._import_btn.clicked.connect(self.on_import)
        header.addWidget(self._import_btn)

        self._export_btn = QPushButton("⬆ 导出")
        self._export_btn.setObjectName("ghostBtn")
        self._export_btn.clicked.connect(self.on_export)
        header.addWidget(self._export_btn)
        root.addLayout(header)

        # 主体：左列表 + 右表单
        body = QHBoxLayout()
        body.setSpacing(16)

        # ---- 左：方案列表 ----
        left = QWidget()
        left.setFixedWidth(380)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(12)
        self._list_layout.addStretch(1)
        self._list_scroll.setWidget(self._list_host)
        left_layout.addWidget(self._list_scroll, 1)

        self._empty_label = QLabel("暂无配置方案，点击下方按钮新建")
        self._empty_label.setObjectName("hintLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._empty_label)

        self._add_btn = QPushButton("＋ 新增方案")
        self._add_btn.setObjectName("dashedBtn")
        self._add_btn.clicked.connect(self.on_add)
        left_layout.addWidget(self._add_btn)

        body.addWidget(left)

        # ---- 右：编辑表单 ----
        self._editor = QFrame()
        self._editor.setObjectName("card")
        self._editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # 外层：表单区（可滚动） + 结果条 + 按钮行（始终可见）
        outer = QVBoxLayout(self._editor)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(12)

        # 表单区放进 QScrollArea，避免窗口变矮时输入框被纵向压缩到看不清文字
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        form_host = QWidget()
        form = QVBoxLayout(form_host)
        form.setContentsMargins(0, 0, 8, 0)   # 右侧留 8px 给滚动条
        form.setSpacing(12)
        form_scroll.setWidget(form_host)

        # 方案名称
        form.addWidget(self._field_label("方案名称"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("方案名称")
        form.addWidget(self._name_input)

        # 网卡
        form.addWidget(self._field_label("网卡"))
        self._adapter_combo = QComboBox()
        self._adapter_combo.currentIndexChanged.connect(self._on_adapter_changed)
        form.addWidget(self._adapter_combo)

        # 获取方式
        form.addWidget(self._field_label("获取方式"))
        mode_row = QHBoxLayout()
        self._static_radio = QRadioButton("静态")
        self._dhcp_radio = QRadioButton("自动 (DHCP)")
        self._static_radio.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._static_radio)
        self._mode_group.addButton(self._dhcp_radio)
        self._static_radio.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self._static_radio)
        mode_row.addWidget(self._dhcp_radio)
        mode_row.addStretch(1)
        form.addLayout(mode_row)

        # 地址字段
        self._ip_input = self._labeled_input(form, "IP 地址", "192.168.1.100")
        self._mask_input = self._labeled_input(form, "子网掩码", "255.255.255.0")
        self._gw_input = self._labeled_input(form, "默认网关", "192.168.1.1")
        self._dns1_input = self._labeled_input(form, "主 DNS", "8.8.8.8")
        self._dns2_input = self._labeled_input(form, "备 DNS", "114.114.114.114")
        self._desc_input = self._labeled_input(form, "描述", "备注（可选）")

        form.addStretch(1)
        outer.addWidget(form_scroll, 1)

        # 结果条
        self._banner = ResultBanner()
        outer.addWidget(self._banner)

        # 操作按钮
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("保存方案")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.clicked.connect(self.on_save)
        btn_row.addWidget(self._save_btn)

        self._apply_btn = QPushButton("应用此方案 ▸")
        self._apply_btn.setObjectName("primaryBtn")
        self._apply_btn.clicked.connect(self.on_apply)
        btn_row.addWidget(self._apply_btn)

        self._dhcp_btn = QPushButton("⚡ 恢复自动获取 DHCP")
        self._dhcp_btn.setObjectName("ghostBtn")
        self._dhcp_btn.clicked.connect(self.on_restore_dhcp)
        btn_row.addWidget(self._dhcp_btn)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        body.addWidget(self._editor, 1)
        root.addLayout(body, 1)

        # Toast 浮层
        self._toast = Toast(self)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        """表单字段标签。"""
        label = QLabel(text)
        label.setObjectName("hintLabel")
        return label

    def _labeled_input(self, parent_layout: QVBoxLayout, label: str, placeholder: str) -> QLineEdit:
        """在表单里加一行「标签 + 输入框」，返回输入框。"""
        parent_layout.addWidget(self._field_label(label))
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        parent_layout.addWidget(edit)
        return edit

    # ------------------------------------------------------------------ #
    # 列表刷新
    # ------------------------------------------------------------------ #
    def _refresh_list(self) -> None:
        """按内存中的方案重建卡片列表。"""
        # 清空旧卡片（保留末尾 stretch）
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()

        profiles = self._cfg.all()
        self._empty_label.setVisible(not profiles)
        for p in profiles:
            card = ProfileCard(p, last_applied=(p.id == self._cfg.last_applied_id))
            card.clicked.connect(self._on_card_clicked)
            card.edit_requested.connect(self._on_card_edit)
            card.delete_requested.connect(self.on_delete)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
            self._cards[p.id] = card

        # 维持选中高亮
        for pid, card in self._cards.items():
            card.set_selected(pid == self._selected_id)

    def _on_card_clicked(self, pid: str) -> None:
        """选中方案并载入表单。"""
        self._selected_id = pid
        for card_id, card in self._cards.items():
            card.set_selected(card_id == pid)
        profile = self._cfg.get(pid)
        if profile is not None:
            self._load_to_form(profile)
            self._editing = True

    def _on_card_edit(self, pid: str) -> None:
        """编辑图标：等同选中并聚焦名称输入。"""
        self._on_card_clicked(pid)
        self._name_input.setFocus()

    # ------------------------------------------------------------------ #
    # 表单 <-> 方案
    # ------------------------------------------------------------------ #
    def _load_to_form(self, p: Profile) -> None:
        """把方案填进表单。"""
        self._name_input.setText(p.name)
        # 网卡下拉：若列表中无该网卡，则临时加入
        idx = self._adapter_combo.findText(p.adapter)
        if idx < 0 and p.adapter:
            self._adapter_combo.addItem(p.adapter)
            idx = self._adapter_combo.findText(p.adapter)
        if idx >= 0:
            self._adapter_combo.setCurrentIndex(idx)
        self._static_radio.setChecked(not p.dhcp)
        self._dhcp_radio.setChecked(p.dhcp)
        self._ip_input.setText(p.ip)
        self._mask_input.setText(p.mask)
        self._gw_input.setText(p.gateway)
        self._dns1_input.setText(p.dns_primary)
        self._dns2_input.setText(p.dns_secondary)
        self._desc_input.setText(p.description)
        self._on_mode_changed()

    def _form_to_profile(self, p: Profile) -> None:
        """把表单值写进方案对象。"""
        p.name = self._name_input.text().strip()
        p.adapter = self._adapter_combo.currentText().strip()
        p.dhcp = self._dhcp_radio.isChecked()
        if p.dhcp:
            p.ip = p.mask = p.gateway = p.dns_primary = p.dns_secondary = ""
        else:
            p.ip = self._ip_input.text().strip()
            p.mask = self._mask_input.text().strip()
            p.gateway = self._gw_input.text().strip()
            p.dns_primary = self._dns1_input.text().strip()
            p.dns_secondary = self._dns2_input.text().strip()
        p.description = self._desc_input.text().strip()

    def _clear_form(self) -> None:
        """清空表单（新增态）。"""
        self._name_input.clear()
        self._ip_input.clear()
        self._mask_input.clear()
        self._gw_input.clear()
        self._dns1_input.clear()
        self._dns2_input.clear()
        self._desc_input.clear()
        self._static_radio.setChecked(True)
        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        """静态/DHCP 切换：DHCP 时禁用地址输入框。"""
        is_static = self._static_radio.isChecked()
        for w in (
            self._ip_input, self._mask_input, self._gw_input,
            self._dns1_input, self._dns2_input,
        ):
            w.setEnabled(is_static)

    def _on_adapter_changed(self, _idx: int) -> None:
        """网卡下拉切换：静态模式下若非空则回填网段建议。"""
        if not self._static_radio.isChecked():
            return
        adapter = self._adapter_combo.currentText().strip()
        if not adapter:
            return
        info = next((a for a in self._adapters if a.name == adapter), None)
        if info is None or not info.ip:
            return
        parts = info.ip.split(".")
        if len(parts) == 4 and not self._ip_input.text().strip():
            self._ip_input.setText(f"{'.'.join(parts[:3])}.100")
            self._mask_input.setText(info.mask or "255.255.255.0")
            self._gw_input.setText(info.gateway or f"{'.'.join(parts[:3])}.1")

    # ------------------------------------------------------------------ #
    # 新增 / 保存 / 删除
    # ------------------------------------------------------------------ #
    def on_add(self) -> None:
        """新增方案：清空表单并聚焦名称。"""
        self._selected_id = ""
        for card in self._cards.values():
            card.set_selected(False)
        self._clear_form()
        self._editing = False
        self._banner.clear()
        self._name_input.setFocus()
        self._refresh_adapter_combo()

    def on_save(self) -> None:
        """保存方案：校验 → 写内存与 JSON → 刷新列表。"""
        if self._selected_id:
            profile = self._cfg.get(self._selected_id)
            if profile is None:
                profile = Profile(id="", name="", adapter="")
        else:
            profile = Profile(id="", name="", adapter="")

        self._form_to_profile(profile)

        errors = ConfigService.validate(profile)
        if errors:
            self._banner.show_result(False, "；".join(errors))
            return
        if self._cfg.name_exists(profile.name, exclude_id=profile.id):
            self._banner.show_result(False, f"方案名称「{profile.name}」已存在")
            return

        if profile.id:
            self._cfg.update(profile)
            self._selected_id = profile.id
        else:
            profile = self._cfg.add(profile)
            self._selected_id = profile.id

        self._refresh_list()
        self._banner.show_result(True, MSG_CONFIG_SAVED)
        self._toast.show_message(MSG_CONFIG_SAVED, "success")
        self._editing = False

    def on_delete(self, pid: str) -> None:
        """删除方案（二次确认）。"""
        profile = self._cfg.get(pid)
        if profile is None:
            return
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除方案「{profile.name}」？此操作不可撤销",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cfg.remove(pid)
        if self._selected_id == pid:
            self._selected_id = ""
            self._clear_form()
        self._refresh_list()
        self._banner.show_result(True, MSG_CONFIG_DELETED)

    # ------------------------------------------------------------------ #
    # 应用 / 恢复 DHCP / 刷新状态
    # ------------------------------------------------------------------ #
    def on_apply(self) -> None:
        """应用选中方案（二次确认 → 后台执行 → 结果回 UI）。"""
        if self._busy:
            return
        if not self._selected_id:
            self._banner.show_result(False, "请先在左侧选择要应用的方案")
            return
        profile = self._cfg.get(self._selected_id)
        if profile is None:
            self._banner.show_result(False, "方案不存在，请刷新后重试")
            return
        errors = ConfigService.validate(profile)
        if errors:
            self._banner.show_result(False, "；".join(errors))
            return

        answer = QMessageBox.question(
            self,
            "确认切换",
            f"即将将「{profile.adapter}」切换为「{profile.name}」，期间网络可能短暂中断，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._banner.show_result(True, "正在执行，请稍候…")

        pid = profile.id
        self._pool.submit(self._apply_worker, pid)

    def _apply_worker(self, pid: str) -> None:
        """工作线程：执行 netsh 变更。"""
        profile = self._cfg.get(pid)
        if profile is None:
            self._on_apply_done(False, "方案不存在")
            return
        result = self._netsh.apply(profile)
        if result.ok:
            self._cfg.mark_applied(pid)
            msg = MSG_APPLY_OK.format(adapter=profile.adapter, name=profile.name)
            self._on_apply_done(True, msg)
        else:
            err = result.brief() or MSG_NEED_ADMIN
            msg = MSG_APPLY_FAIL.format(err=err)
            self._on_apply_done(False, msg)

    def _on_apply_done(self, ok: bool, msg: str) -> None:
        """**可能由工作线程调用** → 通过信号安全地跳回主线程更新 UI。

        ⚠️ 不要改回 `QTimer.singleShot(0, ...)`：从工作线程调用时定时器会挂在
        没有事件循环的线程上，回调**静默丢弃**（不报错）。详见 `app/ui/thread_bridge.py`。
        """
        self._apply_finished_internal.emit(ok, msg)

    def _finish_apply(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        self._banner.show_result(ok, msg)
        self.apply_finished.emit(ok)
        if ok:
            self._toast.show_message(msg, "success")
            # 应用成功后自动重新比对（5.1.6 时机②）。
            # quiet=True：用户已经看到"切换成功"toast，再叠一条"状态已刷新"是冗余；
            # 卡片上的「生效中」标签会静默更新，这才是用户要看的结果。
            self.on_refresh_status(quiet=True)
        else:
            self._toast.show_message("切换失败", "danger")

    def on_restore_dhcp(self) -> None:
        """恢复自动获取 DHCP（F1-12）。"""
        if self._busy:
            return
        adapter = self._adapter_combo.currentText().strip()
        if not adapter:
            self._banner.show_result(False, "请先选择网卡")
            return
        answer = QMessageBox.question(
            self,
            "确认恢复",
            f"即将把「{adapter}」恢复为自动获取（DHCP），是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._banner.show_result(True, "正在执行，请稍候…")
        self._pool.submit(self._restore_worker, adapter)

    def _restore_worker(self, adapter: str) -> None:
        result = self._netsh.restore_dhcp(adapter)
        if result.ok:
            self._on_apply_done(True, MSG_APPLY_OK.format(adapter=adapter, name="自动获取(DHCP)"))
        else:
            self._on_apply_done(False, MSG_APPLY_FAIL.format(err=result.brief() or MSG_NEED_ADMIN))

    def _set_busy(self, busy: bool) -> None:
        """执行期间禁用关键按钮。"""
        self._busy = busy
        for btn in (self._apply_btn, self._save_btn, self._dhcp_btn, self._refresh_btn):
            btn.setEnabled(not busy)

    def on_refresh_status(self, *, quiet: bool = False) -> None:
        """刷新状态：后台读取各网卡配置 → compute_active_profile_ids → 刷新卡片。

        参数 `quiet=True` 用于**自动触发**的刷新（进入页面、应用成功后），
        此时不弹「状态已刷新」横幅 —— 用户没点任何东西却看到提示会困惑
        （用户反馈：一打开软件就提示"状态已刷新"，不知道刷了什么）。
        手动点「刷新状态」仍保留提示，作为操作反馈。

        为什么这个刷新不能省：`生效中` 标记**不持久化**（PRD 5.1.6 / F1-10），
        每次都由 `netsh interface ip show config` 实时比对算出 —— 用户可能在
        **应用之外**改过网卡（系统设置面板、其它工具），缓存的标记会失真。
        所以进入页面必须自动刷新一次，这是「生效中」标签正确的唯一保证。
        """
        if self._busy:
            return
        self._quiet_refresh = quiet
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("🔄 刷新中…")
        self._pool.submit(self._refresh_worker)

    def _refresh_worker(self) -> None:
        """工作线程：读网卡列表 + 配置，做自动比对。"""
        adapters = self._netsh.list_adapters()
        configs = self._netsh.read_all_configs()
        self._refresh_ready.emit(adapters, configs)

    def _finish_refresh(self, adapters: list[AdapterInfo], configs: dict) -> None:
        """主线程：更新网卡下拉 + 生效标记。"""
        self._adapters = adapters
        self._refresh_adapter_combo()

        self._cfg.compute_active_profile_ids(configs)
        # 仅更新已有卡片的生效标签（避免整列表重建丢失选中）
        for pid, card in self._cards.items():
            profile = self._cfg.get(pid)
            if profile is None:
                continue
            card._active_tag.setVisible(bool(profile.active))
            card._active_tag.setProperty(
                "conflict", "true" if profile.active_conflict else "false"
            )
            card._active_tag.style().unpolish(card._active_tag)
            card._active_tag.style().polish(card._active_tag)
            card._dot.setVisible(pid == self._cfg.last_applied_id)

        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("🔄 刷新状态")
        # 自动刷新不弹提示（用户没操作就不该有"已刷新"的噪音）；
        # 手动刷新给一条成功反馈。两者都已完成实际的「生效中」比对。
        if not self._quiet_refresh:
            self._banner.show_result(True, MSG_REFRESH_OK)
        self._quiet_refresh = False

    def _refresh_adapter_combo(self) -> None:
        """把系统网卡填入下拉（保留当前选择）。"""
        current = self._adapter_combo.currentText().strip()
        self._adapter_combo.blockSignals(True)
        self._adapter_combo.clear()
        for info in self._adapters:
            self._adapter_combo.addItem(info.display())
        # 下拉项存的是 display 文本，需回退为「纯网卡名」——改为只显示网卡名
        self._adapter_combo.clear()
        for info in self._adapters:
            self._adapter_combo.addItem(info.name)
        if current:
            idx = self._adapter_combo.findText(current)
            if idx >= 0:
                self._adapter_combo.setCurrentIndex(idx)
        self._adapter_combo.blockSignals(False)

    # ------------------------------------------------------------------ #
    # 导入 / 导出
    # ------------------------------------------------------------------ #
    def on_export(self) -> None:
        """导出全部方案为 JSON。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", "nettool_profiles.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            self._cfg.export_to(path)
            self._banner.show_result(True, f"已导出到 {path}")
        except OSError as e:
            self._banner.show_result(False, f"导出失败：{e}")

    def on_import(self) -> None:
        """导入 JSON 并合并（重名时询问覆盖/跳过）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配置", "", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            count = self._cfg.import_from(path, self._ask_conflict)
        except (OSError, ValueError) as e:
            self._banner.show_result(False, f"导入失败：{e}")
            return
        self._refresh_list()
        self._banner.show_result(True, f"导入完成，共处理 {count} 条方案")

    def _ask_conflict(self, name: str) -> str:
        """冲突回调：询问用户「覆盖」还是「跳过」。"""
        answer = QMessageBox.question(
            self,
            "方案重名",
            f"方案「{name}」已存在，是否覆盖？\n（选「否」则跳过该条）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return "overwrite" if answer == QMessageBox.StandardButton.Yes else "skip"

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def on_enter(self) -> None:
        """进入页面时自动刷新一次（5.1.6 时机①）。

        quiet=True：页面切换是用户的导航动作，不是"刷新"意图，
        不应弹"状态已刷新"横幅（用户反馈该提示在未操作时就出现，令人困惑）。
        """
        if not self._busy:
            self.on_refresh_status(quiet=True)

    def shutdown(self, wait: bool = False) -> None:
        """关闭线程池（窗口关闭时）。"""
        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._pool.shutdown(wait=wait)
