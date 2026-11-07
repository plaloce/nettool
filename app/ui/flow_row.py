"""NetTool UI 层：可换行容器 `FlowRow`（Qt 无内置 FlowLayout，故自实现）。

为什么抽成独立模块：
    扫描页（`page_scan.py`）与 Ping 页（`page_ping.py`）的控制条都需要"装不下
    就换行"的能力，两页各写一份必然会漂移（一处修了另一处忘）。

为什么需要它：
    `QHBoxLayout` **不允许换行**，其最小宽 = 所有子控件最小宽之和。
    一旦控件多 / 窗口窄，就会撑破页面、逼出横向滚动条，或让相邻控件互相重叠
    （Ping 页「目标地址」输入框压住「模式」标签就是这个原因）。
    Qt 官方至今未提供 FlowLayout，故用 `QVBoxLayout` + 动态增删子布局实现：
    宽度变化时重新按行分组。

实现要点：
- 不实现复杂的 `heightForWidth`，改为在 `resizeEvent` 里主动重排 + 更新
  `setFixedHeight`，逻辑简单且不会与父布局的高度协商打架。
- 用**贪心**分组：依次累加子控件宽度，装不下就换行。
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

# Qt 的 QWIDGETSIZE_MAX —— maximumWidth() 返回该值表示"未设置上限"
QWIDGETSIZE_MAX: int = 16777215

# --------------------------------------------------------------------------- #
# 控件水平「边框开销」（文本之外的固定占用），实测值（Microsoft YaHei UI / 13px QSS）：
#     QComboBox   : 54  （padding 12×2 + drop-down 20 + border 2 + 内部留白）
#     QPushButton : 36  （primaryBtn/dangerBtn，padding 18×2）
#     QPushButton : 34  （ghostBtn，padding 16×2）
#     QLineEdit   : 30  （padding 12×2 + border 2 + 内部留白）
# 这些值只在「必须自己算宽度」时用作兜底 —— 更好的做法是直接用 `sizeHint()`，
# 那是 Qt 按真实字体 + QSS 算出的自然宽，天然正确（见下方 `*_natural_width`）。
# --------------------------------------------------------------------------- #
COMBO_CHROME_W: int = 54
EDIT_CHROME_W: int = 30
BUTTON_CHROME_W: int = 36


def _ensure_polished(w: QWidget) -> None:
    """确保控件已应用 QSS 后再量宽。

    ⚠️ 这是所有 `fit_*` 能算准的**前提**：控件刚 `new` 出来、还没被
    `setStyleSheet` 覆盖时，`fontMetrics()` 用的是「裸 QFont」；
    应用 QSS 后字体会变，字宽随之变化 ——

        裸字体下 `fit_button` 算出 "▶ 开始扫描" 需 138px（偏大）
        QSS 后                    实际只需 114px

    若不先 polish 就量，会把控件定得**过宽**，反而把同一行的按钮挤到第二行
    （这正是「重置/清空日志被挤到下一行」反复出现的根因）。
    `unpolish` + `polish` 强制让样式系统重算该控件的字体与内边距。
    """
    w.ensurePolished()
    w.style().unpolish(w)
    w.style().polish(w)
    w.ensurePolished()


def _relax_min_hint(w: QWidget) -> None:
    """拆掉控件自带的高 `minimumSizeHint`，让 `setMinimumWidth` 成为真正的下界。

    ⚠️ **这是控制条反复换行的最深层根因**，务必理解：

    `QComboBox` 的 `minimumSizeHint()` **不只看 `minimumContentsLength`** ——
    它还要求容得下当前显示文本。实测（Microsoft YaHei UI / QSS 13px）：

        当前文本        字宽    minimumSizeHint
        "192.168.2"     117        171
        "192.168.100"   143        197
        "持续 ping"       91        145
        "32"             26         80

    而 `FlowRow._item_width()` 的下界恒为
    `max(minimumWidth(), minimumSizeHint().width())`。
    于是 `fit_combo` 即便把 `setMinimumWidth(145)` 写对了，
    记账时仍会被 `minimumSizeHint()=171/197` 顶回去 —— 白算一场。

    后果：`持续 ping` 明明只显示 91px 文本，却让整行按 **145** 记账，
    每个 combo 虚高 13~54px，四个 combo 就是 100px+，
    这正是「明明算过宽度、实测却还是换行」的原因。

    修法：把 `minimumSizeHint` 用一个「高度保留、宽度极窄」的替身覆盖掉 ——
    宽度真正的下限由调用方的 `setMinimumWidth` 负责，
    高度沿用原 hint（否则控件会被压成一条线，布局彻底失真）。
    """
    hint = w.minimumSizeHint()
    # 替身：高度用原值，宽度给极小值（宽度下界交给 setMinimumWidth）
    w.setMinimumSize(QSize(0, hint.height()))


def _locked_width_fixes_min_hint(w: QWidget, width: int) -> None:
    """已确定宽度后，把 `minimumSizeHint` 彻底钉在该宽度上。

    与 `_relax_min_hint` 的区别：
    - `_relax_min_hint` 用于**量宽之前**（此时还不知道最终宽度，只求别被旧 hint 干扰）；
    - 本函数用于**宽度落定之后**，把 hint 与 `setMinimumWidth` 对齐，
      使 `FlowRow._item_width()` 的三条分支都取到同一个值，记账不再有偏差。
    """
    hint = w.minimumSizeHint()
    w.setMinimumSize(QSize(width, hint.height()))


def fit_combo(combo: QComboBox, extra: int = 0, lock: bool = True) -> int:
    """把 `combo` 的宽度设为「**当前显示文本** + 边框开销」（可加 `extra` 余量）。

    ⚠️ 为什么按「当前文本」而不是「最长选项」：
    **闭合的 QComboBox 只显示选中项**，其余候选只在展开时可见（展开层有自己的
    宽度，不受 combo 自身限制）。若按最长项定宽，会把控件撑得远超需要 ——
    实测把一个候选为 ["192.168.2", "192.168.100"] 的 combo 定成 205px，
    而它只显示 "192.168.2"（117px，+chrome=171），白占 34px。

    例外：可编辑 combo 允许用户键入，按「当前文本」即可（用户输入时文本会变长，
    由 `extra` 或光标滚动处理，不应为假想的输入预留宽度）。

    `lock=True` 时同时设 maximumWidth，避免被相邻控件挤窄导致文字被裁
    （QComboBox 被挤窄时会把文本右对齐显示，首字符滚出可视区 ——
    用户报的「网段显示 92.168.2」就是这么来的）。

    返回计算出的宽度，便于调用方累加预算。
    """
    _ensure_polished(combo)
    fm = combo.fontMetrics()
    text = combo.currentText()
    w = fm.horizontalAdvance(text) + COMBO_CHROME_W + extra
    combo.setMinimumWidth(w)
    if lock:
        combo.setMaximumWidth(w)
    # 关键：钉住 minimumSizeHint，否则它（按文本算出的 171/197…）会覆盖上面的宽度
    _locked_width_fixes_min_hint(combo, w)
    return w


def fit_edit(edit: QLineEdit, text: str = "", extra: int = 0, lock: bool = True) -> int:
    """把 `edit` 的宽度设为「`text`（默认取占位符）+ 边框开销」。

    占位符常被忽略，但它是用户第一眼看到的内容 —— 截断占位符等于信息丢失。
    `extra` 给负数可**主动收窄**（用户要求「缩短目标输入框」时用），
    收窄后输入更长的 IP 靠光标横向滚动查看，不影响功能。
    """
    _ensure_polished(edit)
    fm = edit.fontMetrics()
    reference = text or edit.placeholderText() or edit.text()
    w = max(60, fm.horizontalAdvance(reference) + EDIT_CHROME_W + extra)
    edit.setMinimumWidth(w)
    if lock:
        edit.setMaximumWidth(w)
    _locked_width_fixes_min_hint(edit, w)
    return w


def fit_button(btn: QPushButton, extra: int = 0, lock: bool = False) -> int:
    """把 `btn` 的宽度设为「按钮文本 + 边框开销」。

    `lock` 默认 False：按钮随文案两态切换（"开始 Ping" ↔ "停止"），
    锁死会在切换时裁字。改为设 minimumWidth，让它在必要时能自然变宽。
    """
    _ensure_polished(btn)
    fm = btn.fontMetrics()
    w = fm.horizontalAdvance(btn.text()) + BUTTON_CHROME_W + extra
    btn.setMinimumWidth(w)
    if lock:
        btn.setMaximumWidth(w)
        _locked_width_fixes_min_hint(btn, w)
    return w


def fit_button_multi(btn: QPushButton, texts: list[str], extra: int = 0) -> int:
    """按钮有多态文案时，按**最长**的那个定宽（防止切换态被裁）。

    典型：起停合并按钮空闲态 "▶ 开始 Ping" 比工作中 "■ 停止" 长得多。
    若按当前文案定宽，切换到短文案后按钮会缩，切回来又变宽 —— 视觉跳动。
    """
    _ensure_polished(btn)
    fm = btn.fontMetrics()
    w = max(fm.horizontalAdvance(t) for t in texts) + BUTTON_CHROME_W + extra
    btn.setMinimumWidth(w)
    _locked_width_fixes_min_hint(btn, w)
    return w


class FlowRow(QWidget):
    """将子控件按可用宽度**自动换行**排列的容器（窄窗口折叠成多行）。"""

    def __init__(self, h_gap: int = 8, v_gap: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._h_gap = h_gap
        self._v_gap = v_gap
        self._items: list[QWidget] = []
        self._item_gaps: list[int] = []    # 每个 item 右侧的额外间距（代替 stretch）
        self._out = QVBoxLayout(self)
        self._out.setContentsMargins(0, 0, 0, 0)
        self._out.setSpacing(v_gap)
        self._rows: list[QHBoxLayout] = []
        self._last_width = -1

    # ------------------------------------------------------------------ #
    def add_item(self, w: QWidget, right_gap: int = 0) -> None:
        """追加一个待排列控件；`right_gap` 用于制造「按钮组右对齐」的弹性空档。"""
        self._items.append(w)
        self._item_gaps.append(right_gap)

    def addWidget(self, w: QWidget, right_gap: int = 0) -> None:  # noqa: N802
        """`add_item` 的别名 —— 让调用处可以沿用 `layout.addWidget(...)` 的写法。"""
        self.add_item(w, right_gap)

    def _item_width(self, i: int) -> int:
        """第 i 个 item 参与分行计算时使用的宽度。

        取值优先级（这是"控制条为何还是换行"以及"文字仍被裁掉"的根因所在）：

        0. **若 `minimumWidth == maximumWidth`（宽度被锁死）**，直接返回该值。
           调用方写下 min==max 就是在声明"我的宽度就是它"，此时 `sizeHint` 与
           `minimumSizeHint` 都可能不可信 —— 典型场景是 QSS 尚未 apply 时
           `QComboBox.sizeHint()` 只有 29px，而实际被 min/max 顶到 132px。
           若跳过本分支，FlowRow 会按 29px 记账 → 预算严重低估（实测整行只算
           628px，可用 820px），于是相邻控件把空间全吃掉，被锁死的控件反而
           拿不到自己需要的宽度，文字被裁（"▶ 开始 Ping" 被切成 "▶ 开始 P…"）。

        1. 若设置了 `maximumWidth` 且它大于 `minimumSizeHint`，则以 `maximumWidth`
           为**上界** —— 调用方显式设了最大宽，就是在声明"我最多占这么宽"。
           否则 `QComboBox` 的 `minimumSizeHint()`（实测 139px，被内容/样式撑出）
           会**覆盖** `setMaximumWidth(160/220/72/96)`，让 4 个下拉框一律被算成 139px，
           总需求 859px > 可用 802px → 第 10 个控件（重置按钮）被迫换行。
        2. 否则退回 `sizeHint`。

        **下界恒为 `minimumWidth` 与 `minimumSizeHint` 的较大者** ——
        调用方写 `setMinimumWidth(124)` 时，`sizeHint` 可能只有 122，
        若按 122 记账，按钮就永远拿不到它声明的最小宽，「Ping」尾部会被裁。
        """
        w = self._items[i]
        floor = max(w.minimumWidth(), w.minimumSizeHint().width())
        mn = w.minimumWidth()
        mx = w.maximumWidth()
        # 0) 宽度被锁死：以锁定值为准（min/max 是调用方的显式声明，优先于 hint）
        if mn == mx and mn > 0:
            return mn
        # 1) 有最大宽上界：取 sizeHint 但不越过上界
        if mx < QWIDGETSIZE_MAX and mx > w.minimumSizeHint().width():
            return max(floor, min(w.sizeHint().width(), mx))
        # 2) 无约束：sizeHint 与下界取大者
        return max(floor, w.sizeHint().width())

    def _min_row_width(self) -> int:
        """最宽单个控件的宽 + 间距（换行后的下界）。"""
        if not self._items:
            return 0
        return max(self._item_width(i) for i in range(len(self._items)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._min_row_width(), 0)

    def sizeHint(self) -> QSize:  # noqa: N802
        total = sum(w.sizeHint().width() + g for w, g in zip(self._items, self._item_gaps))
        return QSize(max(self._min_row_width(), total), 0)

    # ------------------------------------------------------------------ #
    def _clear(self) -> None:
        """清空所有行布局。

        注意：`takeAt()` 只解除**布局项**的归属，子控件本身仍以 FlowRow 为父。
        因此 `deleteLater()` 掉旧布局后必须**立刻**重建并重新 `addWidget`，
        否则这些控件会有一瞬间失去布局管理（在重建前不可见）。
        本方法只负责拆，重建在同一轮 `_relayout()` 里紧跟着完成。
        """
        while self._out.count():
            item = self._out.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    lay.takeAt(0)
                lay.deleteLater()
        self._rows.clear()

    def apply_style(self) -> None:
        """外部在**所有** item 都 `add_item` 之后调用一次，完成首次排版。

        为什么需要显式调用：`resizeEvent` 只在控件尺寸真的变化时触发，
        而构造函数里 `add_item` 完时宽度可能尚未确定（或恰为 0），
        此时 `_relayout` 会直接 return，导致首帧空白。
        """
        w = self.width()
        if w <= 0:
            w = self.sizeHint().width()
        self._last_width = w
        self._relayout(w)

    def _relayout(self, width: int) -> None:
        """按 `width` 贪心分行。"""
        if width <= 0 or not self._items:
            return
        # 先算出每行分组（贪心）
        rows: list[list[int]] = [[]]
        used = 0
        for i, _w in enumerate(self._items):
            need = self._item_width(i)
            # 该 item 若带 right_gap，占位也要算进去，否则换行点判断偏乐观
            gap_after = self._item_gaps[i]
            extra = self._h_gap if rows[-1] else 0
            if rows[-1] and used + extra + need > width:
                rows.append([i])
                used = need + gap_after
            else:
                rows[-1].append(i)
                used += extra + need + gap_after
        rows = [r for r in rows if r]
        if not rows:
            return

        self._clear()
        for group in rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(self._h_gap)
            for i in group:
                row.addWidget(self._items[i])
                gap = self._item_gaps[i]
                if gap:
                    row.addSpacing(gap)
            row.addStretch(1)
            self._out.addLayout(row)
            self._rows.append(row)

        # 主动锁定高度，避免与外层的高度协商反复来回
        h = 0
        for r_i, group in enumerate(rows):
            h += max(self._items[i].sizeHint().height() for i in group)
            if r_i:
                h += self._v_gap
        self.setFixedHeight(h)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        w = self.width()
        if w != self._last_width:
            self._last_width = w
            self._relayout(w)
