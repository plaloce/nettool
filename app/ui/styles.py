"""NetTool UI 层：QSS 模板 + 主题变量替换。

QSS 不支持 CSS 变量，故用 `string.Template` + `constants.PALETTE` 做占位符替换：
- **色值只在 `constants.PALETTE` 一处定义**（键名用下划线形式，如 `bg_root`）。
- `build_qss()` 是运行时唯一 QSS 来源（避免「改了 qss 没改 py」的漂移）。

objectName 清单（与代码中 setObjectName 一一对应，务必保持同步）：
    sidebar, brandTitle, brandSub, permBadge,
    githubLink,
    navBtn, pageTitle, card, controlBar,
    primaryBtn, ghostBtn, dangerBtn, iconBtn, dashedBtn, chipBtn, openShareBtn,
    profileCard, cardTitle, cardIp, cardMeta, activeTag, lastAppliedDot,
    resultSuccess, resultError, statOnline, statOffline, statTime,
    statSent, statRecv, statLoss, statAvg, statMin, statMax,
    scanProgress, logView, logCounter, blockTitle, toast, hintLabel,
    ncStatus, ncColTag, ncBigIp, ncMeta, ncAgree, ncSrcName, ncSrcVal,
    ncSrcLoc, ncDivider, ncKey, ncVal,
    ncGroupCN, ncGroupINT, ncSiteIcon, ncSiteName,
    ncSiteLat, ncDots, ncMiniBtn, ncFooter
"""

from __future__ import annotations

from string import Template

from app.constants import FONT_MONO, PALETTE

_QSS_TEMPLATE = Template(
    """
/* ======================= 基础 ======================= */
QWidget {
    color: $text_primary;
    font-size: 13px;
    background: transparent;
}
QMainWindow { background: $bg_root; }
QToolTip {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 6px;
    padding: 6px 8px;
}

/* ======================= 侧栏 ======================= */
#sidebar {
    background: $bg_surface;
    border-right: 1px solid $border_default;
}
#brandTitle { color: $text_primary; font-size: 18px; font-weight: 600; }
#brandSub { color: $text_secondary; font-size: 11px; min-height: 15px; }
#permBadge { color: $text_secondary; font-size: 12px; }
#permBadge[elevated="true"] { color: $success; }
/* 状态栏右下角的项目主页链接：链接色由内联 style 决定（富文本链接不吃 QSS color），
   这里只锁字号与右侧留白，避免贴着窗口边缘和尺寸手柄 */
#githubLink { font-size: 12px; padding-right: 4px; }

QPushButton#navBtn {
    background: transparent;
    color: $text_secondary;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: left;
    font-size: 14px;
}
QPushButton#navBtn:hover { background: $bg_hover; color: $text_primary; }
QPushButton#navBtn:checked {
    background: $bg_hover;
    color: $text_primary;
    border-left: 3px solid $primary;
    font-weight: 600;
}

/* ======================= 页面标题 / 卡片 ======================= */
#pageTitle { color: $text_primary; font-size: 20px; font-weight: 600; }
#blockTitle { color: $text_primary; font-size: 15px; font-weight: 600; }
#hintLabel { color: $text_secondary; font-size: 12px; }

#card, #profileCard, #controlBar {
    background: $bg_card;
    border: 1px solid $border_default;
    border-radius: 12px;
}
#controlBar { padding: 12px; }

/* 方案卡片 */
#profileCard:hover { background: $bg_hover; }
#profileCard[selected="true"] {
    background: $bg_hover;
    border-left: 3px solid $primary;
}
#cardTitle { color: $text_primary; font-size: 14px; font-weight: 600; }
#cardIp { color: $text_primary; font-family: "%(mono)s", Consolas, monospace; font-size: 13px; }
#cardMeta { color: $text_secondary; font-size: 12px; }
#activeTag {
    background: $success_bg;
    color: $success;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
}
#activeTag[conflict="true"] { background: $danger_bg; color: $warning; }
#lastAppliedDot { color: $text_secondary; font-size: 11px; }

/* ======================= 按钮 ======================= */
QPushButton#primaryBtn {
    background: $primary;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton#primaryBtn:hover { background: $primary_hover; }
QPushButton#primaryBtn:pressed { background: $primary_pressed; }
QPushButton#primaryBtn:disabled { background: $bg_hover; color: $text_disabled; }

QPushButton#openShareBtn {
    background: $primary;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 6px 16px;
    font-weight: 600;
}
QPushButton#openShareBtn:hover { background: $primary_hover; }
QPushButton#openShareBtn:disabled { background: $bg_hover; color: $text_disabled; }

QPushButton#ghostBtn {
    background: transparent;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#ghostBtn:hover { background: $bg_hover; }
QPushButton#ghostBtn:disabled { color: $text_disabled; border-color: $bg_hover; }

QPushButton#dangerBtn {
    background: $danger;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton#dangerBtn:hover { background: $danger_hover; }
QPushButton#dangerBtn:disabled { background: $bg_hover; color: $text_disabled; }

QPushButton#iconBtn {
    background: transparent;
    color: $text_secondary;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 14px;
}
QPushButton#iconBtn:hover { background: $bg_hover; color: $text_primary; }
QPushButton#iconBtn[danger="true"]:hover { color: $danger; }

QPushButton#dashedBtn {
    background: transparent;
    color: $text_secondary;
    border: 1px dashed $border_default;
    border-radius: 8px;
    padding: 10px;
}
QPushButton#dashedBtn:hover { background: $bg_hover; color: $text_primary; }

QPushButton#chipBtn {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 16px;
    padding: 4px 14px;
}
QPushButton#chipBtn:hover { background: $bg_hover; border-color: $primary; }

/* ======================= 输入控件 ======================= */
/* 高度声明务必用 `min-height`（带 px），不要用 `height`：
   Qt 的 QSS `height` 与 CSS 的 `height` 语义不同，实测会把控件撑得异常高。
   13px 字体的行高约 18px，配合 padding 7px 上下 → 视觉高度约 34~36px，
   既能完整显示一行中文，又保持紧凑。 */
QLineEdit, QComboBox, QSpinBox {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 8px;
    padding: 7px 12px;
    min-height: 20px;
    selection-background-color: $primary;
}
/* QComboBox 的默认 minimumSizeHint 会按**最长选项文本**算。
   本应用的网卡下拉含 "VMware Network Adapter VMnet1"（实测撑出 139px），
   远超调用方设置的 setMaximumWidth(96/72)，导致控制条总需求 859px > 可用 802px，
   把「重置」按钮挤到第二行。QSS 无法直接写 min-width，故用 Qt 侧的
   setMinimumContentsLength() 抑制，这里只需保证样式不额外抬高下限。 */
QComboBox { padding-right: 18px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 2px solid $border_focus; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    background: $bg_hover;
    color: $text_disabled;
}
QLineEdit:read-only { color: $text_secondary; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    selection-background-color: $primary;
    outline: none;
}

QRadioButton { color: $text_primary; spacing: 6px; }
QRadioButton:disabled { color: $text_disabled; }
QCheckBox { color: $text_primary; }

/* ======================= 列表 / 表格 / 滚动区 ======================= */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 2px;
}
QScrollBar::handle:vertical {
    background: $border_default; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $text_disabled; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $border_default; border-radius: 5px; min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QTableWidget, QListWidget {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 8px;
    gridline-color: $border_default;
    outline: none;
}
/* 行内边距必须收紧：Qt 的 QTableWidget 默认 wordWrap=true，行高 = 字体行高 + padding，
   右栏只有 256~360px 宽时若 padding 偏大，IP 列一换行就变成"一行只显示一个 IP"。
   padding 收到 4px 并配合 setWordWrap(False) 后，一行必然只占一行高。 */
QTableWidget::item, QListWidget::item {
    padding: 4px 8px;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background: $bg_hover;
    color: $text_primary;
}
QHeaderView::section {
    background: $bg_surface;
    color: $text_secondary;
    border: none;
    border-bottom: 1px solid $border_default;
    padding: 6px 8px;
}

/* ======================= 进度条 ======================= */
QProgressBar#scanProgress {
    background: $bg_hover;
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: $text_secondary;
}
QProgressBar#scanProgress::chunk {
    background: $primary;
    border-radius: 6px;
}

/* ======================= 日志 ======================= */
QPlainTextEdit#logView, QTextEdit#logView {
    background: $bg_root;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 8px;
    padding: 8px;
    font-family: "%(mono)s", Consolas, "Cascadia Mono", monospace;
    font-size: 13px;
    selection-background-color: $primary;
}
#logCounter { color: $text_secondary; font-size: 12px; }

/* ======================= 结果条 / 统计 ======================= */
#resultSuccess {
    background: $success_bg;
    color: $success;
    border-radius: 8px;
    padding: 10px 14px;
}
#resultError {
    background: $danger_bg;
    color: $danger;
    border-radius: 8px;
    padding: 10px 14px;
}
#statOnline { color: $success; font-family: "%(mono)s", Consolas, monospace; }
#statOffline { color: $danger; font-family: "%(mono)s", Consolas, monospace; }
#statTime { color: $text_secondary; font-family: "%(mono)s", Consolas, monospace; }
#statSent, #statRecv, #statLoss, #statAvg, #statMin, #statMax {
    color: $text_primary;
    font-family: "%(mono)s", Consolas, monospace;
}
#statLoss { color: $warning; }

/* ======================= 网络检查（页面四） ======================= */
/* ★ 每个标签都钉了 min-height = 字号行高 + 余量。
   原因（踩过的静默渲染缺陷）：卡内纵向空间被 stretch 挤压时，QLabel 会被压到
   小于其字体行高，文字**整行消失**却不报错（见 app/ui/page_netcheck.py::_lock_label_height）。
   这里用 QSS 统一兜底，比逐控件调 _lock_label_height 更不易遗漏。 */
#ncStatus { color: $text_secondary; font-size: 12px; min-height: 18px; }
#ncColTag {
    color: $text_secondary;
    font-size: 11px;
    font-weight: 600;
    min-height: 16px;
}
/* 公网 IP 是本页最重要的信息 —— 用等宽字体 + 放大字号，一眼可读 */
#ncBigIp {
    color: $text_primary;
    font-family: "%(mono)s", Consolas, monospace;
    font-size: 16px;
    font-weight: 600;
    min-height: 21px;
}
#ncMeta { color: $text_secondary; font-size: 12px; min-height: 17px; }
#ncAgree { font-size: 12px; min-height: 17px; }
/* 多源交叉验证：源名加粗（借鉴 ip.skk.moe 卡片），IP 用等宽，归属地次级色 */
#ncSrcName { color: $text_primary; font-size: 12px; font-weight: 600; min-height: 17px; }
#ncSrcVal {
    color: $text_primary;
    font-family: "%(mono)s", Consolas, monospace;
    font-size: 12px;
    min-height: 17px;
}
#ncSrcLoc { color: $text_secondary; font-size: 12px; min-height: 17px; }
/* ⚠️ 源块内的 `#srcTagCN` / `#srcTagINT` 小药丸标签**已删除**（2026-09-21 第四版）：
   用户要求「4 个源只有国内/国际两类，逐源打标签冗余」⇒ 改为行首大幅组标签，
   直接复用下方 `#ncGroupCN` / `#ncGroupINT`（与 E「常用站点连通性」同一套样式）。
   别再为源块新增 `srcTag*` 规则。 */
/* 合并卡内的分组分割线（2026-09-21 用户要求）：1px，色取 PALETTE.border_default。
   高度由代码 `setFixedHeight(1)` 钉死，这里只管颜色与去边框。 */
#ncDivider { background: $border_default; border: none; }
#ncKey { color: $text_secondary; font-size: 12px; min-height: 17px; }
#ncVal {
    color: $text_primary;
    font-family: "%(mono)s", Consolas, monospace;
    font-size: 12px;
    min-height: 17px;
}
/* 站点连通性：图标 + 名称 + 延迟（三色，运行时上色）+ 彩点条（RichText 逐点上色） */
/* 行首分组标签：每行一个（不是逐站点），要「大气」 —— 实心暗底 + 圆角块 + 14px 粗体。
   国内=蓝(info)、国际=绿(success)，与彩点条的语义色同源。
   ⚠️ min-height/min-width 是 content 区，padding 另计（Qt QSS 语义），别再混淆。 */
#ncGroupCN {
    background: $info_bg;
    color: $info;
    border: 1px solid $info;
    border-radius: 10px;
    padding: 6px 16px;
    font-size: 14px;
    font-weight: 700;
    min-height: 22px;
    min-width: 34px;
}
#ncGroupINT {
    background: $success_bg;
    color: $success;
    border: 1px solid $success;
    border-radius: 10px;
    padding: 6px 16px;
    font-size: 14px;
    font-weight: 700;
    min-height: 22px;
    min-width: 34px;
}
#ncSiteIcon { font-size: 13px; min-height: 17px; }
#ncSiteName { color: $text_primary; font-size: 12px; font-weight: 600; min-height: 17px; }
#ncSiteLat {
    font-family: "%(mono)s", Consolas, monospace;
    font-size: 12px;
    font-weight: 600;
    min-height: 17px;
}
#ncDots { font-size: 10px; min-height: 15px; }
/* 「只刷站点」小号刷新按钮（E 板块标题行，2026-09-20 加）：
   ghostBtn 的 padding(8px 16px) 会把卡片标题行撑高约 15px，而本页纵向余量只剩 16px
   （实测 679/695），故用小号按钮把增量压到 ~2px，避免撑破版面触发滚动条。 */
QPushButton#ncMiniBtn {
    background: transparent;
    color: $text_secondary;
    border: 1px solid $border_default;
    border-radius: 6px;
    padding: 2px 10px;
    font-size: 12px;
    min-height: 16px;
}
QPushButton#ncMiniBtn:hover { background: $bg_hover; color: $text_primary; }
QPushButton#ncMiniBtn:pressed { background: $bg_surface; }
QPushButton#ncMiniBtn:disabled { color: $text_disabled; border-color: $bg_hover; }
/* 页脚数据来源：链接色由 RichText 内联 style 决定，这里只管字号与间距 */
#ncFooter { color: $text_secondary; font-size: 11px; min-height: 16px; }

/* ======================= 状态栏 ======================= */
QStatusBar {
    background: $bg_surface;
    color: $text_secondary;
    border-top: 1px solid $border_default;
    min-height: 24px;
}
QStatusBar::item { border: none; }

/* ======================= Toast ======================= */
#toast {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 8px;
    padding: 10px 16px;
}
#toast[level="success"] { background: $success_bg; color: $success; }
#toast[level="danger"] { background: $danger_bg; color: $danger; }
#toast[level="warning"] { background: $bg_card; color: $warning; }
#toast[level="info"] { background: $bg_card; color: $info; }

/* ======================= 菜单 ======================= */
QMenu {
    background: $bg_card;
    color: $text_primary;
    border: 1px solid $border_default;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item { padding: 6px 22px 6px 16px; border-radius: 6px; }
QMenu::item:selected { background: $bg_hover; }
QMenu::item:disabled { color: $text_disabled; }
QMenu::separator { height: 1px; background: $border_default; margin: 4px 8px; }

/* ======================= 对话框 ======================= */
QDialog { background: $bg_root; }
QMessageBox { background: $bg_root; }
QMessageBox QLabel { color: $text_primary; }
"""
)


def build_qss() -> str:
    """从 `constants.PALETTE` 替换占位符，产出最终 QSS 字符串。"""
    # 先替换字体名，再替换色值（模板里字体名占位符用 %(mono)s，避免与 $ 冲突）
    template_str = _QSS_TEMPLATE.template % {"mono": FONT_MONO}
    return Template(template_str).substitute(PALETTE)
