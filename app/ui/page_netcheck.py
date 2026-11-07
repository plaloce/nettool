"""NetTool UI 层：页面四 · 网络检查（PRD-网络检查页）。

参考 ip.skk.moe 的「本机IP查询」，只读展示本机对外出口情况：
    A 工具行      —— 刷新按钮 + 状态 + 最后检测时间
    B 公网 IP · 多源校验 —— **2026-09-21 由「本机 IP」+「多源交叉验证」两张卡合并**：
                     上半身 IPv4 / IPv6 双列大字（角色同旧 B 卡），
                     下半身 4 个源**各一行**：源名 + ✓/✗ + 归属地 + 国内/国际标签，
                     标题行带**独立「🔄 刷新」**（只重查这 4 个源，不动其它板块）。
                     ★ 合并理由：旧 B 卡的 IP 本就是从这 4 个源里挑「最佳」得来的，
                       两者同源，拆成两张卡是同一份信息摆两遍（用户反馈「重复」）。
                     ★ 源详情（该源实际看到的 IP / ASN / 耗时）进 tooltip ——
                        ✗ 时靠它追查「这一源到底说看到了什么」。
    C 本机网络信息 —— 网卡 / IP / 掩码 / 网关（复用 NetshService）
    D 站点连通性   —— **行首大幅组标签**（国内/国际）+ 组内 4 站点：
                     图标 + 名称 / 延迟 + 彩点条；国内一行 4 个、国际一行 4 个
                     ★ 卡片标题行带**独立「🔄 刷新」按钮**：只重探这 8 个站点，
                       不动公网 IP / 多源交叉验证 / 网卡（用户要求「只刷新站点连通状态」）
    E 页脚        —— 一行「更多数据 https://ip.skk.moe/」（URL 可点击外链）

（板块字母在 B/C 合并后已重编号：A 工具行 / B 合并卡 / C 网卡 / D 站点 / E 页脚）

★ 版式硬约束（实测，务必遵守；⚠️ 度量必须在**注入 QSS 后**做 ——
   tools/audit_netcheck.py、tools/shot_netcheck_live.py 已注入，旧版截图白底不可信）
    窗口 1100×720 → QStackedWidget = 900×695（页面可用区，状态栏占 25px）
    本页**不允许**出现纵向滚动条 —— 所有分区必须装进 695px
    （2026-09-20 实测 hint=679px、余量 16px；未注入 QSS 时假度量曾误报 700px 装得下）
    2026-09-21 B/C 合并后 hint 进一步下降，纵向预算更安全（改动后务必重跑 audit）。
    ⚠️ 判断排版只认 `win.grab()` 真实截图：
       offscreen 平台缺 "Microsoft YaHei UI"，CJK 字宽被算成真实值 1.85 倍。

★ 线程契约（项目铁律）
    工作线程**禁止**用 `QTimer.singleShot` 回主线程（会静默失效，P0 级坑）。
    一律用类级 `Signal` + `emit()` —— 见 `_check_done`。

★ 流式渲染（2026-09-20 用户要求「哪个有结果就显示哪个，不用等别人」）
    服务层 `check_all(on_source/on_site/on_adapter)` 用 `as_completed` 按完成顺序回调，
    本页把回调 `emit` 回主线程（`_src_ready` / `_site_ready` / `_net_ready`）逐条渲染；
    `_check_done` 只做收尾（一致性结论、耗时、状态栏）。
    ⚠️ 流式渲染**不得**把"还没返回"的条目写成失败：只更新已有的那一条。

★ 国际站点超时是**常态**（GitHub/YouTube 实测必超时），不得弹错误窗或写状态栏红字（PRD §3.3）。
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    MSG_NETCHECK_ALL_FAIL,
    MSG_NETCHECK_COPY_IP,
    MSG_NETCHECK_DONE,
    MSG_NETCHECK_FOOTER,
    MSG_NETCHECK_LAST_TIME,
    MSG_NETCHECK_NEVER,
    MSG_NETCHECK_NO_ADAPTER,
    MSG_NETCHECK_NO_IP_TO_COPY,
    MSG_NETCHECK_NO_IPV4,
    MSG_NETCHECK_NO_IPV4_HINT,
    MSG_NETCHECK_NO_IPV6,
    MSG_NETCHECK_NO_IPV6_HINT,
    MSG_NETCHECK_PUBLIC_IP_TITLE,
    MSG_NETCHECK_RUNNING,
    MSG_NETCHECK_SITES_FAIL,
    MSG_NETCHECK_SITES_REFRESH,
    MSG_NETCHECK_SITES_REFRESHING,
    MSG_NETCHECK_SITES_TIP,
    MSG_NETCHECK_SOURCES_AGREE,
    MSG_NETCHECK_SOURCES_DIFFER,
    MSG_NETCHECK_SOURCES_FAIL,
    MSG_NETCHECK_SOURCES_REFRESH,
    MSG_NETCHECK_SOURCES_REFRESHING,
    MSG_NETCHECK_SOURCES_TIP,
    MSG_NETCHECK_UNAVAILABLE,
    MSG_NETCHECK_UNREACHABLE,
    NETCHECK_DOT_BANDS,
    NETCHECK_DOT_COUNT,
    NETCHECK_INTL_COLS,
    NETCHECK_IPV6_DISPLAY_LEN,
    NETCHECK_LATENCY_DANGER_MS,
    NETCHECK_LATENCY_WARN_MS,
    NETCHECK_REFERENCE_URL,
    NETCHECK_SITE_GROUPS,
    NETCHECK_SITES,
    NETCHECK_SOURCE_GROUPS,
    NETCHECK_SOURCES,
    NETCHECK_VALUE_DASH,
    PALETTE,
)
from app.netcheck_service import (
    AdapterInfo,
    IpInfo,
    NetCheckResult,
    NetCheckService,
    SiteProbe,
    SourceResult,
    fetch_favicon_bytes_chain,
)
from app.netsh_service import NetshService
from app.ui.flow_row import fit_button
from app.ui.widgets import Toast

# 延迟三色 → PALETTE 键
_LEVEL_KEY: dict[str, str] = {
    "success": "success",
    "warning": "warning",
    "danger": "danger",
    "muted": "text_secondary",
}


def _dot_count(ms: int | None, ok: bool) -> int:
    """延迟 → 点亮数量（**延迟条形图**：越小越少、越大越多）。

    ★ 2026-09-20 用户反馈推翻了旧画法：原「历史留痕」（每次检测追加 1 个彩点）
      首跑只有 1 个实点、其余 9 个空心 —— 用户问「这么多圈起什么作用」。
      现改为**当次延迟的条形图**：阈值与三色分级对齐（300 黄线 / 1000 红线），
      不可达 0 点（全空心）。
    """
    if not ok or ms is None:
        return 0
    for limit, n in NETCHECK_DOT_BANDS:
        if ms <= limit:
            return n
    return NETCHECK_DOT_COUNT


def _latency_level(ms: int | None, ok: bool) -> str:
    """延迟分级（PRD §3.3）：<300 绿 / 300~1000 黄 / >1000 红 / 失败灰。

    阈值已用真实数据校验过（2026-09-20）：微信 89ms 绿、百度 216ms 绿、
    Cloudflare 1058ms 红、jsDelivr 1975ms 红、GitHub 超时灰 —— 四档均有实测命中。
    """
    if not ok or ms is None:
        return "muted"
    if ms > NETCHECK_LATENCY_DANGER_MS:
        return "danger"
    if ms > NETCHECK_LATENCY_WARN_MS:
        return "warning"
    return "success"


def _lock_label_height(lbl: QLabel, pad: int = 2) -> None:
    """把 QLabel 的最小高度钉在「其字体行高 + pad」。

    ★ 这是本页踩过的一个**静默渲染缺陷**，务必理解：

    QSS 里给 `#ncBigIp` 设了 `font-size: 16px`（实际行高 19px），
    但当卡内纵向空间被 stretch 挤压时，布局会把 QLabel 压到 **15px**。
    此时：`text()` 正确、`color` 正确、`isVisible()` 为 True、
    `win.grab()` 截图上**一个字都看不见** —— 因为 15px 装不下 19px 的字形，
    文本被裁到不可见，而 Qt **不会报错、不会警告**。

    ⚠️ 断言 `label.text()` 是**抓不住**这个 bug 的（文本是对的），
       必须同时断言 `minimumHeight()` ≥ 字体行高。

    pad 给 2px 余量，防止不同 DPI/字体微调时再次贴边裁切。
    """
    lbl.ensurePolished()
    lbl.style().unpolish(lbl)
    lbl.style().polish(lbl)
    lbl.ensurePolished()
    lbl.setMinimumHeight(lbl.fontMetrics().height() + pad)


class PageNetCheck(QWidget):
    """页面四：网络检查（本机对外出口，全只读）。"""

    status_message = Signal(str, str)
    # ⚠️ 工作线程 → 主线程的回传通道。请勿改回 QTimer.singleShot（见模块 docstring）。
    _check_done = Signal(object)
    # ★ 流式回传（2026-09-20 用户要求「哪个有结果哪个就显示」）：
    #   每完成 1 个源 / 1 个站点 / 网卡读取，服务层在工作线程里回调 → 这里 emit 回主线程。
    _src_ready = Signal(object)     # (SourceResult, IpInfo, IpInfo)
    _site_ready = Signal(object)    # SiteProbe（主检测与「只刷站点」共用，勿再造新号）
    _net_ready = Signal(object)     # list[AdapterInfo]
    # ★ D 板块（站点连通性）独立刷新的收尾信号：("ok", list[SiteProbe]) / ("error", str)
    _sites_done = Signal(object)
    # ★ B 合并卡源区独立刷新（只查 4 个数据源）的收尾信号：("ok", list[SourceResult]) / ("error", str)
    _sources_refresh_done = Signal(object)
    # ★ 站点 favicon 异步加载完毕：("站点名", 字节)。加载失败 → 字节为空，UI 保留文字占位符。
    _favicon_loaded = Signal(str, bytes)

    def __init__(
        self,
        netsh_service: NetshService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._netsh = netsh_service
        self._service = NetCheckService()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="netcheck-page")
        # ★ 「只刷站点」走独立线程池：主检测池 max_workers=1，若共用会把站点刷新
        #   排到主检测后面（最长等 5s+）；站点数 8 → 一批全部并发发出。
        self._site_pool = ThreadPoolExecutor(
            max_workers=max(1, len(NETCHECK_SITES)), thread_name_prefix="netcheck-sites"
        )
        # ★ 「只刷源」走独立线程池：与站点池同理，避免排到主检测池后面等 5s+；
        #   源数 = 4（恰为 NETCHECK_SOURCES 长度）。
        self._src_pool = ThreadPoolExecutor(
            max_workers=max(1, len(NETCHECK_SOURCES)), thread_name_prefix="netcheck-src"
        )
        # favicon 异步下载池（UI 工作线程外拉取，主线程再 loadFromData，不阻塞渲染）
        self._favicon_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="netcheck-fav")

        self._running: bool = False
        self._sites_running: bool = False
        self._src_running: bool = False
        self._result: NetCheckResult | None = None
        self._last_time: str = ""
        # 流式渲染计数：站点名 → 是否可达（用于「可达 n / 已探测 m」的即时汇总）
        self._site_status: dict[str, bool] = {}
        # 流式期间已拿到的公网 IP（不必等全部源返回即可「复制 IP」）
        self._live_ipv4: str = ""
        self._live_ipv6: str = ""
        # 站点名 → 图标 QLabel（favicon 异步加载成功后替换文字占位符）
        self._site_icons: dict[str, QLabel] = {}

        self._build_ui()
        self._connect_signals()
        self._render_idle()

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # 页边距 上下 16+10 共 26px（★带 QSS 实测 hint 727 > 可用 695，收紧边距换余量，
        # 版式预算见 docs/PRD-网络检查页.md §5.2 —— 改动任何间距后必须重跑 tools/audit_netcheck.py）
        root.setContentsMargins(24, 16, 24, 10)
        root.setSpacing(9)
        # ---- 标题 + A 工具行（合并成一行，省纵向空间）----
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("网络检查")
        title.setObjectName("pageTitle")
        head.addWidget(title)

        self._time_label = QLabel(MSG_NETCHECK_NEVER)
        self._time_label.setObjectName("hintLabel")
        head.addWidget(self._time_label)

        self._status_label = QLabel("")
        self._status_label.setObjectName("ncStatus")
        head.addWidget(self._status_label)

        head.addStretch(1)

        self._copy_btn = QPushButton("📋 复制 IP")
        self._copy_btn.setObjectName("ghostBtn")
        fit_button(self._copy_btn)
        self._copy_btn.setEnabled(False)
        head.addWidget(self._copy_btn)

        self._refresh_btn = QPushButton("🔄 重新检测")
        self._refresh_btn.setObjectName("primaryBtn")
        fit_button(self._refresh_btn)
        head.addWidget(self._refresh_btn)
        root.addLayout(head)

        # 顶部这 4 个标签同样要钉高（否则会被压到不可见）
        for _lbl in (title, self._time_label, self._status_label):
            _lock_label_height(_lbl)

        # ---- ① 本机网络信息（2026-09-21 用户要求：提到第一行）----
        # ⚠️ 余量分配的实测口径（勿凭推理改）：
        #    三卡内容 hint ≈ 102 / 245 / 128（真实数据下实测），底部**不放** stretch 项，
        #    余量按 **1:2:1** 回流给三张卡，卡内 `addStretch(1)` 转为底部留白。
        #    若把尾部再放一个 stretch，余量会被它全部吃掉（实测 108px 全堆页脚下方）。
        self._net_card = self._build_network_card()
        root.addWidget(self._net_card, 1)

        # ---- ② 公网 IP · 多源校验（原 B 本机 IP + C 多源交叉验证 合并而成）----
        self._pub_card = self._build_public_ip_card()
        root.addWidget(self._pub_card, 2)

        # ---- ③ 常用站点连通性 ----
        self._site_card = self._build_sites_card()
        root.addWidget(self._site_card, 1)

        # ---- ④ 页脚：数据来源 + 可点击超链接 ----
        root.addWidget(self._build_footer())
        # ★ 尾部**不放** stretch（2026-09-21 第三版排版实测结论）：
        #   三张卡的 `sizePolicy` 都是 Preferred（不主动吸高）。一旦尾部放一个 stretch 项，
        #   QBoxLayout 会把**全部**余量塞给它 —— 实测 108px 全堆在页脚下方，空一大块。
        #   去掉后余量按 1:2:1 回流给三张卡，各卡内部的 `addStretch(1)` 把它变成
        #   「卡内底部留白」——整页填满、观感更整齐（实测页面内容 588 → 填满 695）。
        #   ⚠️ 别改回 `root.addStretch(1)`：差别就是「底部空一大块」vs「三卡均匀舒展」。

        # Toast 浮层（复制反馈）
        self._toast = Toast(self)

    # ------------------------------------------------------------------ #
    def _build_public_ip_card(self) -> QFrame:
        """合并卡「公网 IP · 多源校验」= 原 B 板块（IPv4/IPv6 双列）+ 原 C 板块（4 源）。

        ★ 为什么合并（2026-09-21 用户反馈「两卡重复」）：
          原 B 卡的 IP 本来就**是从** C 卡那 4 个源里挑「最佳」得来的 —— 两者同源，
          拆成两张卡等于同一份信息摆两遍。合并后上半段是 IP 双列（大字，主角），
          下半段是 4 个源**按类别分行**（国内一行 / 国际一行，行首大幅组标签），
          每源三行：`源名 + ✓/✗` / `IP: xxx` / 归属地 + 运营商，
          一眼看出「谁受访了、各自看到什么、大家口供一不一致」。

        ★ 源区为什么是「每源三行」而不是「每源一行」（2026-09-21 用户第二轮要求）：
          第一版把 IP 压进 tooltip 只留一行状态 —— 用户反馈「悬浮显示等于看不到」，
          明确要求**直接铺开**：源名称、IP 地址、归属地址。故每源三行；
          为避免 12 行把整页顶爆（实测 769px > 可用 695），排成网格。

        ★ 为什么类别用「行首组标签」而不是「逐源药丸标签」（2026-09-21 用户第四轮要求）：
          4 个源只有国内/国际两个类别，逐源各打一个标签既冗余又零散；
          改为**一行一类 + 行首大幅组标签**，与 E「常用站点连通性」完全同款
          （直接复用 `ncGroupCN` / `ncGroupINT`，视觉必然一致）。
        """
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(6)

        # ---- 标题行（含一致结论 +「只刷源」按钮）----
        header = QHBoxLayout()
        header.setSpacing(8)
        t = QLabel(MSG_NETCHECK_PUBLIC_IP_TITLE)
        t.setObjectName("blockTitle")
        _lock_label_height(t)
        header.addWidget(t)
        header.addStretch(1)
        self._agree_label = QLabel("")
        self._agree_label.setObjectName("ncAgree")
        _lock_label_height(self._agree_label)
        header.addWidget(self._agree_label)
        # ★ 独立刷新按钮：**只**重查这 4 个数据源（用户要求），
        #   2026-09-21 随「多源交叉验证」卡并入本卡（板块重编号见模块 docstring）。
        #   小号样式 ncMiniBtn —— 与 E 卡同款，避免 ghostBtn 把标题行撑高撑破版式。
        self._src_refresh_btn = QPushButton(MSG_NETCHECK_SOURCES_REFRESH)
        self._src_refresh_btn.setObjectName("ncMiniBtn")
        fit_button(self._src_refresh_btn)
        self._src_refresh_btn.setToolTip(MSG_NETCHECK_SOURCES_TIP)
        header.addWidget(self._src_refresh_btn)
        lay.addLayout(header)

        # ---- IPv4 / IPv6 双列 ----
        cols = QHBoxLayout()
        cols.setSpacing(20)

        # ---- IPv4 列 ----
        v4 = QVBoxLayout()
        v4.setSpacing(2)
        t4 = QLabel("IPv4")
        t4.setObjectName("ncColTag")
        v4.addWidget(t4)
        self._v4_ip = QLabel(NETCHECK_VALUE_DASH)
        self._v4_ip.setObjectName("ncBigIp")
        self._v4_ip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v4.addWidget(self._v4_ip)
        self._v4_loc = QLabel("")
        self._v4_loc.setObjectName("ncMeta")
        v4.addWidget(self._v4_loc)
        self._v4_isp = QLabel("")
        self._v4_isp.setObjectName("ncMeta")
        v4.addWidget(self._v4_isp)
        v4.addStretch(1)
        cols.addLayout(v4, 1)

        # ---- IPv6 列 ----
        v6 = QVBoxLayout()
        v6.setSpacing(2)
        t6 = QLabel("IPv6")
        t6.setObjectName("ncColTag")
        v6.addWidget(t6)
        self._v6_ip = QLabel(NETCHECK_VALUE_DASH)
        self._v6_ip.setObjectName("ncBigIp")
        self._v6_ip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v6.addWidget(self._v6_ip)
        self._v6_loc = QLabel("")
        self._v6_loc.setObjectName("ncMeta")
        v6.addWidget(self._v6_loc)
        self._v6_isp = QLabel("")
        self._v6_isp.setObjectName("ncMeta")
        v6.addWidget(self._v6_isp)
        v6.addStretch(1)
        cols.addLayout(v6, 1)

        # ★ 钉住所有信息标签的高度 —— 缺了这步，IP 文字会被压成不可见（详见 _lock_label_height）
        for _lbl in (t4, self._v4_ip, self._v4_loc, self._v4_isp,
                     t6, self._v6_ip, self._v6_loc, self._v6_isp):
            _lock_label_height(_lbl)

        # ---- 主体 ①：IPv4 / IPv6 双列（占整行）----
        # ★ 2026-09-21 排版第三版（用户要求源信息铺开显示：源名 / IP / 归属地）：
        #   版本一「源区接在 IP 下面」：4 源 × 3 行 = 12 行 → 整页 769px，溢出 74（实测）；
        #   版本二「左右分栏」：总高够了，但左栏仅 3 行、右栏 12 行 ⇒ 左半张卡
        #     空出 ~140px 死区（真实截图实锤，正面看很扎眼）；
        #   版本三（本版）：「IP 双列整行 + 源区 2×2 网格」—— 卡片比版本二矮 24px，
        #     死区消失，且每个源块拿到 ~400px 宽（39 字符 IPv6 也放得下）。
        lay.addLayout(cols)

        # ---- 分割线：把上组「公网 IP」与下组「多源校验」分开（2026-09-21 用户要求）----
        #   1px `QFrame.HLine` + `#ncDivider`（色值取 PALETTE.border_default）。
        #   间距用**外层 QWidget 的 contentsMargins** 钉死（最终留白 = 卡内 spacing 6 + margins 4
        #   = 线上下各 10px）—— 不用 `addSpacing()`：它是插一个 spacer 项，与 layout spacing
        #   叠加后的实际留白不易预估，不如 margins 这样一眼能算出确定数值。
        div_wrap = QWidget()
        div_lay = QVBoxLayout(div_wrap)
        div_lay.setContentsMargins(0, 4, 0, 4)
        div_lay.setSpacing(0)
        divider = QFrame()
        divider.setObjectName("ncDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        div_lay.addWidget(divider)
        lay.addWidget(div_wrap)

        # ---- 主体 ②：4 个数据源，**按类别分行**（国内一行 / 国际一行）----
        # ★ 2026-09-21 第四版（用户要求）：「4 个源只有国内/国际两个类别，逐源各打一个
        #   药丸标签既冗余又零散 —— 合并成**行首大幅组标签**，与 E「常用站点连通性」同款」。
        #   ⇒ 组标签用 `ncGroupCN` / `ncGroupINT`（与站点卡**完全同一套 QSS**，视觉必然一致），
        #     每行一个；源块内不再有 `srcTag*`。
        #   网格：col0 = 组标签（不伸缩），col1..2 = 该类别的源块（等分余量）。
        #   每块三行：源名 + ✓/✗ ／ `IP: xxx` ／ 归属地 + 运营商。
        #   ⚠️ `self._src_blocks` 的**键名与顺序保持不变**（仍按 NETCHECK_SOURCES 顺序、
        #   仍是 name/status/ip/loc 四个键）⇒ `_render_sources`、`_reset_src_cells`
        #   与本卡的渲染测试**零改动**。
        src_grid = QGridLayout()
        src_grid.setHorizontalSpacing(28)
        src_grid.setVerticalSpacing(8)
        # col0 = 组标签列，不参与伸缩；数据列在下面的循环里逐列 `setColumnStretch(_i+1, 1)`
        # —— 与 E 卡同款写法，这样某类别多于 2 个源时新增列同样能等分余量
        # （QA 2026-09-21 指出：写死 col1/col2 会让第 3 列 stretch=0 被压窄）。
        src_grid.setColumnStretch(0, 0)

        # 按 NETCHECK_SOURCE_GROUPS 的行序取源（同站点卡的稳健写法，不依赖清单顺序）
        grouped = [(g, [s for s in NETCHECK_SOURCES if s[2] == g])
                   for g in NETCHECK_SOURCE_GROUPS]

        self._src_blocks: list[dict] = []
        for _row, (group, items) in enumerate(grouped):
            # ---- 行首：大幅组标签（与 E 卡同款 objectName ⇒ 同款样式）----
            tag = QLabel(group)
            tag.setObjectName("ncGroupCN" if group == "国内" else "ncGroupINT")
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _lock_label_height(tag)
            src_grid.addWidget(tag, _row, 0)

            for _i, (name, _url, _grp) in enumerate(items):
                src_grid.setColumnStretch(_i + 1, 1)
                block = QVBoxLayout()
                block.setSpacing(0)

                head = QHBoxLayout()
                head.setSpacing(6)
                nm = QLabel(name)
                nm.setObjectName("ncSrcName")
                _lock_label_height(nm)
                head.addWidget(nm)

                status_lbl = QLabel("–")
                status_lbl.setObjectName("ncSrcVal")
                _lock_label_height(status_lbl)
                head.addWidget(status_lbl)
                head.addStretch(1)      # 类别已由行首组标签表达，源名后不再逐源打标签

                ip_lbl = QLabel("IP: " + NETCHECK_VALUE_DASH)
                ip_lbl.setObjectName("ncSrcVal")
                ip_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                _lock_label_height(ip_lbl)

                loc_lbl = QLabel("")
                loc_lbl.setObjectName("ncSrcLoc")
                _lock_label_height(loc_lbl)

                block.addLayout(head)
                block.addWidget(ip_lbl)
                block.addWidget(loc_lbl)
                src_grid.addLayout(block, _row, _i + 1)

                self._src_blocks.append({
                    "name": name, "status": status_lbl, "ip": ip_lbl, "loc": loc_lbl,
                })

        lay.addLayout(src_grid, 1)

        return card

    # ------------------------------------------------------------------ #
    def _build_network_card(self) -> QFrame:
        """C 板块：本机网络信息（只读）。

        ★ 版式（2026-09-21 三版）：
          ① 4 项从「竖排 4 行」改 **2×2 网格**（用户要求本卡提到第一行后，竖排会让右半边空一片）；
          ② 2×2 网格承接本卡**全部富余高度**（`addLayout(grid, 1)`，**不放** `addStretch`）
             —— 否则富余高度全堆在卡底，形成用户反馈的「卡片下部空白空间」；
          ③ 提示行 `_lock_label_height`（QSS `#hintLabel` 无 min-height，不钉高会踩静默裁切）。
          `_net_rows` 的顺序与结构保持 4 对 (键标签, 值标签) 不变 ⇒ `_render_network` 零改动。
        """
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(4)

        t = QLabel("本机网络信息")
        t.setObjectName("blockTitle")
        lay.addWidget(t)

        self._net_rows: list[tuple[QLabel, QLabel]] = []
        # ★ 本卡独占整行后改 **2×2 网格**（2026-09-21 用户要求把本卡提到第一行）：
        #   4 项竖排会让右半边空掉一大片，横排两列后宽度利用率明显更好。
        #   `_net_rows` 的**顺序与结构保持不变**（仍是 4 对 (键标签, 值标签)），
        #   故 `_render_network` 的渲染逻辑**无需任何改动**。
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(4)
        for idx, field_name in enumerate(("网卡", "内网 IP", "网关", "掩码")):
            r, c = divmod(idx, 2)
            cell = QHBoxLayout()
            cell.setSpacing(8)
            k = QLabel(field_name)
            k.setObjectName("ncKey")
            k.setFixedWidth(58)
            cell.addWidget(k)
            v = QLabel(NETCHECK_VALUE_DASH)
            v.setObjectName("ncVal")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cell.addWidget(v)
            cell.addStretch(1)
            grid.addLayout(cell, r, c)
            self._net_rows.append((k, v))
        # 4 项信息区承接本卡的**全部富余高度**（`lay.addLayout(grid, 1)`）——
        # 用户要求「优化本机网络信息卡片中信息分布，现在卡片下部还有空白空间」：
        # 卡末原来的 `addStretch(1)` 会把分配到的富余高度（实测 44px）全堆在卡底形成空白；
        # 交给 2×2 网格后，两行自然撑开、提示行落到卡片底部，整张卡从上到下填满。
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid, 1)

        self._net_hint = QLabel("")
        self._net_hint.setObjectName("hintLabel")
        _lock_label_height(self._net_hint)
        lay.addWidget(self._net_hint)
        return card

    # ------------------------------------------------------------------ #
    def _build_sites_card(self) -> QFrame:
        """D 板块：站点连通性（借鉴 ip.skk.moe 卡片）。

        每个站点两行：
            图标 + 站点名
            22ms  ●●●●●●●●●●    ← 延迟三色 + 彩点条（历史留痕）

        ★ 分组标签是**每行一个**（行首大幅色块），组内站点**不再逐站点打标签**
          —— 用户明确要求（2026-09-20）：同一行本来就是一个组，逐站点标签既冗余
          又显得小气；行首那块要**大**（字号 14px + 实心暗底 + 圆角块）。
        硬约束：**国内一行 4 个、国际一行 4 个**（组标签列 + 4 列 × 2 行）。
        """
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(5)

        header = QHBoxLayout()
        t = QLabel("常用站点连通性")
        t.setObjectName("blockTitle")
        _lock_label_height(t)
        header.addWidget(t)
        header.addStretch(1)
        self._site_summary = QLabel("")
        self._site_summary.setObjectName("hintLabel")
        _lock_label_height(self._site_summary)
        header.addWidget(self._site_summary)

        # ★ 独立刷新按钮：**只**重探这 8 个站点（用户要求）。
        #   小号样式 ncMiniBtn —— ghostBtn 的 padding(8px 16px) 会把卡片标题行撑高
        #   约 15px，本页纵向余量只有 16px（实测 679/695），改用小号按钮只涨 ~2px。
        self._sites_refresh_btn = QPushButton(MSG_NETCHECK_SITES_REFRESH)
        self._sites_refresh_btn.setObjectName("ncMiniBtn")
        fit_button(self._sites_refresh_btn)
        self._sites_refresh_btn.setToolTip(MSG_NETCHECK_SITES_TIP)
        header.addWidget(self._sites_refresh_btn)
        lay.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        # 第 0 列 = 组标签（不参与伸缩，保持自身宽度）；第 1~4 列 = 各组 4 个站点，等分余量
        grid.setColumnStretch(0, 0)
        lay.addLayout(grid)

        # 按 NETCHECK_SITE_GROUPS 的行序取站点（不依赖清单顺序的稳健写法）
        ordered = [(g, [s for s in NETCHECK_SITES if s.get("group") == g])
                   for g in NETCHECK_SITE_GROUPS]

        self._site_cells: dict[str, dict[str, QLabel]] = {}
        self._sites_order: list[str] = []
        self._group_tags: dict[str, QLabel] = {}
        for row, (grp, sites) in enumerate(ordered):
            # ---- 行首：大幅组标签 ----
            tag = QLabel(grp)
            tag.setObjectName("ncGroupCN" if grp == "国内" else "ncGroupINT")
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _lock_label_height(tag)
            grid.addWidget(tag, row, 0)
            self._group_tags[grp] = tag

            for idx, site in enumerate(sites[:NETCHECK_INTL_COLS]):
                col = idx + 1
                grid.setColumnStretch(col, 1)
                cell = QVBoxLayout()
                cell.setSpacing(1)

                top = QHBoxLayout()
                top.setSpacing(6)
                # ★ favicon：先用站点名首字做文字占位符，异步加载真实图标后替换
                #   （favicon 下载走 _favicon_pool，加载失败则保留文字占位符，绝不崩）。
                icon = QLabel(site["name"][0])
                icon.setObjectName("ncSiteIcon")
                icon.setFixedSize(18, 18)
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._site_icons[site["name"]] = icon
                nm = QLabel(site["name"])
                nm.setObjectName("ncSiteName")
                top.addWidget(icon)
                top.addWidget(nm)
                top.addStretch(1)

                bottom = QHBoxLayout()
                bottom.setSpacing(6)
                lat = QLabel(NETCHECK_VALUE_DASH)
                lat.setObjectName("ncSiteLat")
                lat.setToolTip(site["url"])
                dots = QLabel("")
                dots.setObjectName("ncDots")
                # 彩点条要逐点上色 → RichText + 内联 span
                dots.setTextFormat(Qt.TextFormat.RichText)
                bottom.addWidget(lat)
                bottom.addWidget(dots)
                bottom.addStretch(1)

                for _l in (icon, nm, lat, dots):
                    _lock_label_height(_l)

                cell.addLayout(top)
                cell.addLayout(bottom)
                grid.addLayout(cell, row, col)

                self._site_cells[site["name"]] = {"lat": lat, "dots": dots}
                self._sites_order.append(site["name"])

        # 卡片构建完成后异步拉取所有站点 favicon（工作线程下载，不阻塞 UI 构造）
        self._load_favicons()
        return card

    # ------------------------------------------------------------------ #
    def _paint_dots(self, name: str, lit: int = 0, color: str | None = None) -> None:
        """画彩点条 = **延迟条形图**：左起 `lit` 个实心（延迟色），其余空心。

        `lit` 由 `_dot_count()` 算出（延迟越小越少、越大越多；不可达 = 0）。
        """
        lit = max(0, min(NETCHECK_DOT_COUNT, lit))
        dim = PALETTE["text_disabled"]
        solid = color or PALETTE["text_secondary"]
        spans = [f'<span style="color:{solid};">●</span>'] * lit
        spans += [f'<span style="color:{dim};">○</span>'] * (NETCHECK_DOT_COUNT - lit)
        self._site_cells[name]["dots"].setText("".join(spans))

    # ------------------------------------------------------------------ #
    def _build_footer(self) -> QWidget:
        """F 板块：页脚 —— 仅一行「更多数据 https://ip.skk.moe/」（URL 可点击）。

        ★ 用户明确要求（2026-09-20 改版）：原先那串
          「数据来源 a · b · c（点击站点名可在浏览器中打开）· IPv6 探测 …」
          信息密度低、喧宾夺主，**去掉**，换成一行简单明了的参考站点链接。
        技术方案：`QLabel` + RichText + `setOpenExternalLinks(True)`（已实测可行）。
        """
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(0)

        url = NETCHECK_REFERENCE_URL
        link = f'<a href="{url}" style="color:{PALETTE["info"]};text-decoration:none;">{url}</a>'
        foot = QLabel(MSG_NETCHECK_FOOTER.format(link=link))
        foot.setObjectName("ncFooter")
        foot.setTextFormat(Qt.TextFormat.RichText)
        # ★ 关键：允许点击外部链接（否则 RichText 里的 <a> 只是摆设）
        foot.setOpenExternalLinks(True)
        foot.setToolTip(f"在浏览器中打开 {url}")
        lay.addWidget(foot)
        lay.addStretch(1)
        return wrap

    # ------------------------------------------------------------------ #
    # 信号
    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._refresh_btn.clicked.connect(self.on_refresh)
        self._copy_btn.clicked.connect(self.on_copy_ip)
        self._sites_refresh_btn.clicked.connect(self.on_refresh_sites)
        self._src_refresh_btn.clicked.connect(self.on_refresh_sources)
        self._check_done.connect(self._on_check_done)
        self._sites_done.connect(self._on_sites_done)
        self._sources_refresh_done.connect(self._on_sources_refresh_done)
        # ★ 流式：单个结果到位就渲染（源的回调带 3 个参数，走 _emit_source 打包）
        self._src_ready.connect(self._on_src_ready)
        self._site_ready.connect(self._on_site_ready)
        self._net_ready.connect(self._on_net_ready)
        self._favicon_loaded.connect(self._on_favicon_loaded)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def on_enter(self) -> None:
        """进入页面自动检测（F4-08）；**防重入** —— 上次还在跑就不重复发起。"""
        if self._running:
            return
        if self._result is None:
            self.on_refresh()

    def shutdown(self) -> None:
        """关闭时停止线程池，无残留进程。"""
        for pool in (self._pool, self._site_pool, self._src_pool, self._favicon_pool):
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:               # 兜底：老版本 ThreadPoolExecutor 无 cancel_futures
                pool.shutdown(wait=False)

    # ------------------------------------------------------------------ #
    # 检测
    # ------------------------------------------------------------------ #
    def on_refresh(self) -> None:
        """手动/自动触发检测。

        ★ 防重入**双向**：`_running`（主检测在跑）、`_sites_running`（只刷站点在跑）、
        `_src_running`（只刷源在跑）任一为真，都直接返回。后两者挡的是
        **程序化调用路径** —— `on_enter()` 会在 `_result is None` 时直调本方法，
        绕开按钮的禁用状态（按钮路径已由 `_sync_refresh_buttons()` 挡住）。
        两套并发会让"谁后返回谁赢"的竞态成真，详见 `_sync_refresh_buttons` 的注释。

        ⚠️ 被挡时必须**零副作用**：不改按钮状态、不写状态栏、不 emit 任何消息，
        也不要在这里"排队等待刷新结束再补跑" —— 刷新结束后 `_result`
        仍为 None，用户下一次进入本页时 `on_enter()` 会自然补发起，无需排队。
        """
        if self._running or self._sites_running or self._src_running:
            return
        self._running = True
        self._copy_btn.setEnabled(False)
        # 互斥（双向）：主检测一起跑 → 两个刷新按钮都禁用
        self._sync_refresh_buttons()
        # 流式重跑：清掉上一轮的站点汇总计数（格子里的旧值会被新结果逐个覆盖）
        self._site_status.clear()
        self._live_ipv4 = ""
        self._live_ipv6 = ""
        self._set_status(MSG_NETCHECK_RUNNING, "info")
        self.status_message.emit(MSG_NETCHECK_RUNNING, "info")
        self._pool.submit(self._worker)

    def _worker(self) -> None:
        """工作线程：同步执行检测，结果经信号回主线程。

        ⚠️ 这里**绝不能**用 `QTimer.singleShot` 回主线程 —— 工作线程无事件循环，
        回调会被静默丢弃（项目 P0 级坑，见 app/ui/thread_bridge.py）。
        """
        try:
            result = self._service.check_all(
                netsh_service=self._netsh,
                on_source=self._emit_source,       # 每源一回调（含此刻最佳 IP）
                on_site=self._site_ready.emit,     # 每站点一回调
                on_adapter=self._net_ready.emit,   # 网卡读完一回调
            )
        except Exception as e:                      # 兜底：绝不让线程静默死掉
            result = NetCheckResult()
            result.sources = []
            self._check_done.emit(("error", str(e)))
            return
        self._check_done.emit(("ok", result))

    def _emit_source(self, sr: SourceResult, v4: IpInfo, v6: IpInfo) -> None:
        """服务层回调（工作线程）：3 个参数打包成一个 tuple 再 emit。"""
        self._src_ready.emit((sr, v4, v6))

    # ------------------------------------------------------------------ #
    # 「只刷站点」—— D 板块独立刷新（不查公网 IP / 不做交叉验证 / 不读网卡）
    # ------------------------------------------------------------------ #
    def on_refresh_sites(self) -> None:
        """只重新探测 8 个站点的连通性（防重入：站点刷新中 / 源刷新中 / 主检测中均直接返回）。"""
        if self._sites_running or self._src_running or self._running:
            return
        self._sites_running = True
        self._sites_refresh_btn.setText(MSG_NETCHECK_SITES_REFRESHING)
        # 互斥（双向）：站点刷新一起跑 → 主「🔄 重新检测」也禁用（见 _sync_refresh_buttons）
        self._sync_refresh_buttons()
        self._site_status.clear()
        # ⚠️ 只重置站点格子 —— **不要**调 `_render_idle()`（会连带清掉 IP 卡/网卡/源块）
        self._reset_site_cells()
        self._site_summary.setText(MSG_NETCHECK_RUNNING)
        self._site_pool.submit(self._sites_worker)

    def _sites_worker(self) -> None:
        """工作线程：只探站点，收尾经 `_sites_done` 回主线程。

        ⚠️ 同 `_worker`：这里**绝不能**用 `QTimer.singleShot` 回主线程
        （工作线程无事件循环，回调会被静默丢弃）。
        """
        try:
            # 单个站点的流式上屏复用已有的 `_site_ready` 信号
            probes = self._service.check_sites(on_site=self._site_ready.emit)
        except Exception as e:                      # 兜底：绝不让线程静默死掉
            self._sites_done.emit(("error", str(e)))
            return
        self._sites_done.emit(("ok", probes))

    def _sync_refresh_buttons(self) -> None:
        """统一算三个刷新按钮的可用状态 —— **互斥：任一流程在跑，三个按钮都禁用**。

        ★ 为什么要集中到这一个方法：
        `_running`（主检测）/ `_sites_running`（只刷站点）/ `_src_running`（只刷源）
        三个状态位要交叉影响三个按钮，若散落在 `on_refresh` / `on_refresh_sites` /
        `on_refresh_sources` / `_on_check_done` / `_on_sites_done` / `_on_sources_refresh_done`
        四处各自 `if`，迟早漏掉一条路径。集中后，任何一处状态变化只要调一次本方法即可。

        ★ 为什么必须**双向**禁用（不是洁癖，是真实竞态）：
        两套流程末尾都会「用自己那份列表幂等重渲各自板块」，谁**后返回**谁赢，
        跟发起顺序无关。若只禁用单向，用户可以 t=0 点源刷新、t=1 点站点刷新，
        t=6 站点先渲染、t=7 源刷新返回 → 用旧那份结果覆盖刚上的新数据。
        三个刷新按钮任一在跑 → 三个都禁用（主检测/站点刷新/源刷新相互都互斥）。

        ★ 按钮层挡住了点击，但**程序化调用**（`on_enter()` → `on_refresh()`）
        不走按钮：`on_refresh()` 入口已统一拦截 `_sites_running` / `_src_running`
        （2026-09-20 补），故两条路径都已封死，本页不存在两套检测并行的窗口。
        """
        idle = not (self._running or self._sites_running or self._src_running)
        self._refresh_btn.setEnabled(idle)
        self._sites_refresh_btn.setEnabled(idle)
        self._src_refresh_btn.setEnabled(idle)

    def _on_sites_done(self, payload: object) -> None:
        """主线程：站点刷新收尾 —— **只**动站点格子与汇总，其余板块一律不碰。"""
        self._sites_running = False
        self._sites_refresh_btn.setText(MSG_NETCHECK_SITES_REFRESH)
        # 主检测仍在跑 → 主按钮保持禁用（不抢着恢复）
        self._sync_refresh_buttons()

        kind, data = payload          # type: ignore[misc]
        if kind == "error":
            # 国际站点超时是常态，不弹窗；这里只把汇总降级为「检测失败」
            self._site_summary.setText(MSG_NETCHECK_SITES_FAIL)
            return
        # 用完整列表**幂等重渲染**一次（覆盖任何漏网的流式更新）
        self._site_status = {s.name: bool(s.ok) for s in data}
        self._render_sites(list(data))

    # ------------------------------------------------------------------ #
    # 「只刷源」—— B 合并卡源区独立刷新（只查 4 个数据源，不查公网 IP / 站点 / 网卡）
    # ------------------------------------------------------------------ #
    def on_refresh_sources(self) -> None:
        """只重新查询 4 个数据源（防重入：源刷新中 / 站点刷新中 / 主检测中均直接返回）。"""
        if self._src_running or self._sites_running or self._running:
            return
        self._src_running = True
        self._src_refresh_btn.setText(MSG_NETCHECK_SOURCES_REFRESHING)
        # 互斥（双向）：源刷新一起跑 → 主「🔄 重新检测」与站点刷新按钮都禁用（见 _sync_refresh_buttons）
        self._sync_refresh_buttons()
        self._reset_src_cells()           # 只重置源块占位符，勿调 _render_idle()
        self._src_pool.submit(self._sources_worker)

    def _sources_worker(self) -> None:
        """工作线程：只查源，收尾经 `_sources_refresh_done` 回主线程。

        ⚠️ 同 `_worker` / `_sites_worker`：这里**绝不能**用 `QTimer.singleShot` 回主线程
        （工作线程无事件循环，回调会被静默丢弃）。
        """
        try:
            # 单源的流式上屏复用已有的 `_src_ready` 信号（与 check_all 同款 3 参数回调）
            sources = self._service.check_sources(on_source=self._src_ready.emit)
        except Exception as e:                      # 兜底：绝不让线程静默死掉
            self._sources_refresh_done.emit(("error", str(e)))
            return
        self._sources_refresh_done.emit(("ok", sources))

    def _on_sources_refresh_done(self, payload: object) -> None:
        """主线程：源刷新收尾 —— **只**动合并卡的源区（含一致结论），其余一律不碰。

        ⚠️「其余」也包括本卡的 IP 大字：虽然它和源区现在同属一张合并卡，
        但「🔄 刷新」的契约是「只刷新多源交叉验证」，IP 大字不得被牵连清空
        （用户是看着 IP 点刷新的，清掉会让主页瞬时空白）。
        """
        self._src_running = False
        self._src_refresh_btn.setText(MSG_NETCHECK_SOURCES_REFRESH)
        # 其它刷新仍在跑 → 对应按钮保持禁用（不抢着恢复）
        self._sync_refresh_buttons()

        kind, data = payload          # type: ignore[misc]
        if kind == "error":
            # 国际源超时/降级是常态，不弹窗；这里只把一致结论降级为「交叉验证失败」
            self._agree_label.setText(MSG_NETCHECK_SOURCES_FAIL)
            return
        # 用完整列表**幂等重渲染**一次（覆盖任何漏网的流式更新 + 重算一致结论）
        self._render_sources(list(data), final=True)

    def _reset_src_cells(self) -> None:
        """把 4 个源块重置为占位符（– / -- / 空）。**只**动合并卡的源区。

        ★ 与 `_render_idle()` 的区别：后者会连带清掉 IP 大字 / 站点格 / 网卡。
          「只刷源」开头只需要清源块，故抽出本方法（镜像 `_reset_site_cells`）。
        """
        for blk in self._src_blocks:
            blk["status"].setText("–")
            blk["status"].setStyleSheet(f"color: {PALETTE['text_secondary']};")
            blk["ip"].setText("IP: " + NETCHECK_VALUE_DASH)
            blk["ip"].setStyleSheet(f"color: {PALETTE['text_secondary']};")
            blk["loc"].setText("")
            blk["loc"].setStyleSheet(f"color: {PALETTE['text_secondary']};")
        self._agree_label.setText("")
    # ------------------------------------------------------------------ #
    # 站点 favicon 异步加载（UI 工作线程外下载字节，主线程 loadFromData）
    # ------------------------------------------------------------------ #
    def _load_favicons(self) -> None:
        """异步拉取每个站点的 favicon（工作线程下载字节，主线程 loadFromData）。

        ⚠️ 离线/测试环境会加载失败 → 保留文字占位符，绝不崩、绝不影响其它板块。
        ★ `site["favicon"]` 现在是**逗号分隔的候选链**（常量里定义），不再单个 URL。
        """
        for site in NETCHECK_SITES:
            chain = site.get("favicon")
            if chain:
                self._favicon_pool.submit(self._favicon_worker, site["name"], chain)

    def _favicon_worker(self, name: str, chain: str) -> None:
        """工作线程：按候选链逐级尝试下载 favicon 字节，经 `_favicon_loaded` 回主线程。"""
        try:
            data = fetch_favicon_bytes_chain(chain)
        except Exception:
            data = b""
        self._favicon_loaded.emit(name, data)

    def _on_favicon_loaded(self, name: str, data: bytes) -> None:
        """主线程：favicon 字节到手 → 替换文字占位符为图标；失败则保留文字占位符。"""
        lbl = self._site_icons.get(name)
        if lbl is None or not data:
            return
        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            lbl.setText("")   # 清掉文字占位符
            lbl.setPixmap(pix.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))

    # ------------------------------------------------------------------ #
    # 流式槽函数（主线程）
    # ------------------------------------------------------------------ #
    def _on_src_ready(self, payload: object) -> None:
        """单个源返回：**只**更新这一源的三行块 + 顺带刷新 IP 卡（若已有可用 IP）。"""
        sr, v4, v6 = payload            # type: ignore[misc]
        self._render_sources([sr])
        if v4.ok:
            self._live_ipv4 = v4.ip
            self._render_ip(self._v4_ip, self._v4_loc, self._v4_isp, v4, is_v6=False)
        if v6.ok:
            self._live_ipv6 = v6.ip
            self._render_ip(self._v6_ip, self._v6_loc, self._v6_isp, v6, is_v6=True)
        if self._live_ipv4 or self._live_ipv6:
            self._copy_btn.setEnabled(True)     # 有 IP 就能复制，不必等全部源

    def _on_site_ready(self, payload: object) -> None:
        """单个站点探测完：只更新这一格（延迟 + 彩点条）+ 汇总计数。"""
        self._render_sites([payload])   # type: ignore[arg-type]

    def _on_net_ready(self, payload: object) -> None:
        """网卡信息读完：立即填「本机网络信息」卡（netsh 是本地命令，通常最先返回）。"""
        self._render_network(payload)   # type: ignore[arg-type]

    def _on_check_done(self, payload: object) -> None:
        """主线程：渲染结果。"""
        self._running = False
        # 站点刷新仍在跑 → 站点按钮保持禁用（对称：不抢着恢复）
        self._sync_refresh_buttons()

        kind, data = payload          # type: ignore[misc]
        if kind == "error":
            self._set_status(f"检测失败：{data}", "danger")
            self.status_message.emit(f"检测失败：{data}", "danger")
            return

        self._result = data
        self._last_time = datetime.now().strftime("%H:%M:%S")
        self._render(data)

        if not data.any_source_ok:
            self._set_status(MSG_NETCHECK_ALL_FAIL, "danger")
            self.status_message.emit(MSG_NETCHECK_ALL_FAIL, "danger")
        else:
            text = MSG_NETCHECK_DONE.format(s=data.elapsed_s)
            self._set_status(text, "success")
            self.status_message.emit(text, "success")

        self._live_ipv4 = data.ipv4.ip
        self._live_ipv6 = data.ipv6.ip
        self._copy_btn.setEnabled(bool(data.ipv4.ip or data.ipv6.ip))

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render_idle(self) -> None:
        """初始态：全占位符。"""
        self._v4_ip.setText(NETCHECK_VALUE_DASH)
        self._v4_loc.setText(MSG_NETCHECK_NO_IPV4_HINT)
        self._v4_isp.setText("")
        self._v6_ip.setText(NETCHECK_VALUE_DASH)
        self._v6_loc.setText(MSG_NETCHECK_NO_IPV6_HINT)
        self._v6_isp.setText("")
        for blk in self._src_blocks:
            blk["status"].setText("–")
            blk["status"].setStyleSheet(f"color: {PALETTE['text_secondary']};")
            blk["ip"].setText("IP: " + NETCHECK_VALUE_DASH)
            blk["ip"].setStyleSheet(f"color: {PALETTE['text_secondary']};")
            blk["loc"].setText("")
            blk["loc"].setStyleSheet(f"color: {PALETTE['text_secondary']};")
        self._agree_label.setText("")
        for _k, v in self._net_rows:
            v.setText(NETCHECK_VALUE_DASH)
        self._net_hint.setText("")
        self._site_status.clear()
        self._reset_site_cells()
        self._site_summary.setText("")

    def _reset_site_cells(self) -> None:
        """把 8 个站点格子重置为占位符（`--` + 0 点）—— **只**动 D 板块。

        ★ 与 `_render_idle()` 的区别：后者会连带清掉 IP 卡 / 源块 / 网卡。
          「只刷站点」开头只需要清站点格子，故抽出本方法。
        """
        for name in self._sites_order:
            lat = self._site_cells[name]["lat"]
            lat.setText(NETCHECK_VALUE_DASH)
            lat.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            self._paint_dots(name, 0)

    def _render(self, r: NetCheckResult) -> None:
        """把检测结果刷到各板块。"""
        # ---- B 合并卡：IP 双列 + 源区 ----
        self._render_ip(self._v4_ip, self._v4_loc, self._v4_isp, r.ipv4, is_v6=False)
        self._render_ip(self._v6_ip, self._v6_loc, self._v6_isp, r.ipv6, is_v6=True)
        self._render_sources(r.sources, final=True)

        # ---- C 本机网络信息 ----
        self._render_network(r.adapters)

        # ---- D 站点连通性 ----
        self._site_status = {s.name: s.ok for s in r.sites}
        self._render_sites(r.sites)

        # ---- A 工具行 ----
        self._time_label.setText(MSG_NETCHECK_LAST_TIME.format(t=self._last_time))

    # ------------------------------------------------------------------ #
    def _render_sources(self, sources: list[SourceResult], final: bool = False) -> None:
        """合并卡源区：每源**三行块** —— 源名(+✓/✗+组标签) / IP / 归属地。

        ★ 用户明确要求（2026-09-21）：源信息**直接铺开显示**，不要藏在 hover tooltip 里
          —— 此前为压高度把 IP 移进 tooltip，用户反馈"看不到就等于没有"。
          tooltip 仍保留，但只作为 ASN / 耗时等**次要**细节的补充。

        只更新 `sources` 里出现的源 —— 流式渲染时先回来的源先上屏，
        其余块保持原状（不写「✗」，避免把"还没返回"误显示成"失败"）。

        ★ 流式（final=False）与收尾（final=True）的 ✓/✗ 语义差别：
          · final=False：✓ 只表示「该源已返回」，此刻还不知道它跟别人一不一致；
          · final=True ：按**本族成功 IP 集合大小**判 —— 族内唯一（或只有它自己）才 ✓，
            出现 ≥2 个不同 IP 则**全族 ✗**（本就没有共识，谁也不能自称正确）。
            ⚠️ 「共识为空 → 按一致处理」是错的：分歧恰恰让共识为空 ⇒ 分歧时反而全 ✓。
        """
        # ---- 先算各族的「成功 IP 集合」（final=True 时据此判该源 ✓/✗）----
        v4s = {s.ip for s in sources if s.ok and s.ip and not s.ipv6}
        v6s = {s.ip for s in sources if s.ok and s.ip and s.ipv6}

        by_name = {s.name: s for s in sources}
        for blk in self._src_blocks:
            s = by_name.get(blk["name"])
            status_lbl: QLabel = blk["status"]
            ip_lbl: QLabel = blk["ip"]
            loc_lbl: QLabel = blk["loc"]

            # ★ 还没返回的源 → **保持原状**，绝不写成「✗ 不可用」。
            #   流式阶段 `_on_src_ready` 每次只送一个源，若把 None 当失败，
            #   先返回的那个源会把其余**尚未返回**的源统统刷成「✗ 不可用」，
            #   等它们返回再翻回 ✓ —— 用户看到的是「先红后绿」的闪烁与误导。
            if s is None:
                continue

            if not s.ok:
                status_lbl.setText("✗")
                status_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
                ip_lbl.setText("IP: " + NETCHECK_VALUE_DASH)
                ip_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
                loc_lbl.setText(s.err if s.err else MSG_NETCHECK_UNAVAILABLE)
                loc_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
                continue

            # ---- ✓ / ✗：final 时按「本族是否唯一」判定 ----
            if not final:
                ok_mark, mark_key = "✓", "success"
            else:
                fam = v6s if s.ipv6 else v4s
                if len(fam) <= 1:
                    ok_mark, mark_key = "✓", "success"
                else:
                    ok_mark, mark_key = "✗", "warning"
            status_lbl.setText(ok_mark)
            status_lbl.setStyleSheet(f"color: {PALETTE[mark_key]};")

            # ---- 第二行：IP（直接铺开，不藏 tooltip）----
            ip_text = "IP: " + s.ip
            ip_lbl.setText(ip_text)
            ip_lbl.setStyleSheet(f"color: {PALETTE['text_primary']};")
            # ★ 防横向裁切（静默坑）：按实际文字宽钉最小宽（长 IPv6 也放得下）
            ip_lbl.setMinimumWidth(ip_lbl.fontMetrics().horizontalAdvance(ip_text) + 6)

            # ---- 第三行：归属地 + 运营商 ----
            line3 = " ".join(p for p in (s.loc, s.provider) if p)
            loc_lbl.setText(line3)
            loc_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            loc_lbl.setMinimumWidth(loc_lbl.fontMetrics().horizontalAdvance(line3) + 6)

            # ASN / 耗时等次要细节仍可 hover 查看（主信息已铺开，tooltip 只作补充）
            tip = " · ".join(
                p for p in (s.ip, s.loc, s.provider, s.asn, f"{s.elapsed_s}s") if p
            )
            ip_lbl.setToolTip(tip)
            loc_lbl.setToolTip(tip)
            status_lbl.setToolTip(tip)

        # 「几个源一致」这类**全局结论**只在全部源返回后（final=True）才下
        if final:
            if not sources:
                self._agree_label.setText("")
            elif self._sources_agree(sources):
                self._agree_label.setText(
                    MSG_NETCHECK_SOURCES_AGREE.format(n=sum(1 for s in sources if s.ok))
                )
                self._agree_label.setStyleSheet(f"color: {PALETTE['success']};")
            else:
                self._agree_label.setText(MSG_NETCHECK_SOURCES_DIFFER)
                self._agree_label.setStyleSheet(f"color: {PALETTE['warning']};")

    @staticmethod
    def _sources_agree(sources: list[SourceResult]) -> bool:
        """v4 / v6 **分族**判定是否一致（沿用服务层 P0 修复口径，见 PRD §3.5）。

        v4 集合与 v6 集合各自内部必须唯一；混算会把「4 个 v4 + 1 个 v6」误判成分歧。
        """
        ok = [s for s in sources if s.ok and s.ip]
        if not ok:
            return False
        v4 = {s.ip for s in ok if not s.ipv6}
        v6 = {s.ip for s in ok if s.ipv6}
        return len(v4) <= 1 and len(v6) <= 1

    # ------------------------------------------------------------------ #
    def _render_ip(
        self, ip_lbl: QLabel, loc_lbl: QLabel, isp_lbl: QLabel, info, is_v6: bool
    ) -> None:
        """渲染一个协议族的 IP 信息。"""
        if not info.ok:
            ip_lbl.setText(NETCHECK_VALUE_DASH)
            ip_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            loc_lbl.setText(
                MSG_NETCHECK_NO_IPV6_HINT if is_v6 else MSG_NETCHECK_NO_IPV4_HINT
            )
            loc_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            isp_lbl.setText("")
            return

        text = info.display_ip(NETCHECK_IPV6_DISPLAY_LEN) if is_v6 else info.ip
        ip_lbl.setText(text)
        ip_lbl.setStyleSheet(f"color: {PALETTE['text_primary']};")
        ip_lbl.setToolTip(info.ip)                  # 截断后仍可看全（IPv6）

        loc_lbl.setText(info.location or NETCHECK_VALUE_DASH)
        loc_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")

        bits = [b for b in (info.provider, info.asn) if b]
        isp_lbl.setText(" · ".join(bits) if bits else "")
        isp_lbl.setStyleSheet(f"color: {PALETTE['text_secondary']};")

    # ------------------------------------------------------------------ #
    def _render_network(self, adapters: list[AdapterInfo]) -> None:
        """C 板块：网卡信息。多网卡时优先展示有网关的那个（真实出口）。"""
        if not adapters:
            for _k, v in self._net_rows:
                v.setText(NETCHECK_VALUE_DASH)
                v.setStyleSheet(f"color: {PALETTE['text_secondary']};")
            self._net_hint.setText(MSG_NETCHECK_NO_ADAPTER)
            return

        # 挑真实出口：有网关优先，其次排除虚拟网卡（与 NetshService 同一策略）
        def rank(a) -> int:
            s = 0
            if a.gateway:
                s += 4
            if a.is_up:
                s += 2
            name = a.name.lower()
            if name.startswith("vmware") or "vethernet" in name:
                s -= 3
            return s

        best = max(adapters, key=rank)
        values = [best.name, best.ip or NETCHECK_VALUE_DASH,
                  best.gateway or NETCHECK_VALUE_DASH, best.mask or NETCHECK_VALUE_DASH]
        for (_k, v), text in zip(self._net_rows, values):
            v.setText(text)
            v.setStyleSheet(f"color: {PALETTE['text_primary']};")
            # ★ 防横向裁切：网卡名/IP 可能较长（"VMware Network Adapter VMnet1"），
            #   若卡宽被挤压，QLabel 会裁掉尾部且不报错。这里按其**实际文字宽度**
            #   钉一个最小宽度，保证整串可见（与 _lock_label_height 同一类静默坑）。
            v.setMinimumWidth(v.fontMetrics().horizontalAdvance(text) + 6)

        others = len(adapters) - 1
        self._net_hint.setText(
            f"另有 {others} 个网卡（已折叠）" if others > 0 else ""
        )

    # ------------------------------------------------------------------ #
    def _render_sites(self, sites: list[SiteProbe]) -> None:
        """D 板块：延迟三色 + 彩点条（**延迟条形图**）。

        ⚠️ 国际站点超时是常态（GitHub/YouTube 实测必超时），**只把该格标灰，
        不弹窗、不写状态栏红字** —— 否则用户会以为工具坏了。
        彩点条 = 当次延迟的条形图：延迟越小点越少、越大点越多、不可达 0 点
        （`_dot_count`，2026-09-20 用户要求，取代旧的"历史留痕"画法）。
        只更新 `sites` 里出现的站点 —— 流式渲染时先探测完的先上屏。
        """
        for s in sites:
            cell = self._site_cells.get(s.name)
            if cell is None:
                continue
            lat = cell["lat"]
            if s.ok and s.latency_ms is not None:
                level = _latency_level(s.latency_ms, True)
                color = PALETTE[_LEVEL_KEY[level]]
                lat.setText(f"{s.latency_ms}ms")
                lat.setStyleSheet(f"color: {color};")
                self._paint_dots(s.name, _dot_count(s.latency_ms, True), color)
            else:
                lat.setText(MSG_NETCHECK_UNREACHABLE)
                lat.setStyleSheet(f"color: {PALETTE['text_secondary']};")
                self._paint_dots(s.name, 0)
            self._site_status[s.name] = bool(s.ok)

        done = len(self._site_status)
        reach = sum(1 for v in self._site_status.values() if v)
        self._site_summary.setText(f"可达 {reach} / {done}")

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def on_copy_ip(self) -> None:
        """复制当前公网 IP 到剪贴板。"""
        # 优先用流式期间已拿到的 IP（不必等全部源返回即可复制），否则退回最终结果
        ips = [i for i in (self._live_ipv4, self._live_ipv6) if i]
        if not ips and self._result is not None:
            ips = [i for i in (self._result.ipv4.ip, self._result.ipv6.ip) if i]
        if not ips:
            self._toast.show_message(MSG_NETCHECK_NO_IP_TO_COPY, "warning")
            return
        if not ips:
            self._toast.show_message(MSG_NETCHECK_NO_IP_TO_COPY, "warning")
            return
        text = "\n".join(ips)
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        shown = ips[0] if len(ips) == 1 else f"{len(ips)} 个地址"
        self._toast.show_message(MSG_NETCHECK_COPY_IP.format(ip=shown), "success")
        self.status_message.emit(MSG_NETCHECK_COPY_IP.format(ip=shown), "info")

    # ------------------------------------------------------------------ #
    def _set_status(self, text: str, level: str = "normal") -> None:
        """行内状态文案（与状态栏解耦，互不干扰）。"""
        self._status_label.setText(text)
        key = {"info": "info", "success": "success", "danger": "danger",
               "warning": "warning"}.get(level, "text_secondary")
        self._status_label.setStyleSheet(f"color: {PALETTE[key]};")
