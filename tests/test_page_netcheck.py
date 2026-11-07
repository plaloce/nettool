"""网络检查页 UI 测试（qapp / offscreen）。

⚠️ 不量宽、不量高：offscreen 平台 CJK 字宽算成真实值 1.85 倍，
   几何判定只信 `win.grab()` 真实截图（见 tools/audit_netcheck.py）。
   这里只验证：**构造无异常、信号接线正确、渲染逻辑无崩溃、线程契约守住**。

契约守卫（项目铁律，违反即 P0）：
- 工作线程**禁止**用 `QTimer.singleShot` 回主线程 → 必须用类级 `Signal` + `emit()`。
- 页脚超链接必须 `setOpenExternalLinks(True)`（否则 RichText 的 <a> 只是摆设）。
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton


def _make_result() -> "object":
    from app.netcheck_service import IpInfo, NetCheckResult, SourceResult, SiteProbe

    src = SourceResult(name="IP9", url="u", group="国内", ok=True,
                       elapsed_s=0.2, ip="183.132.224.185")
    src.loc, src.provider, src.asn = "中国 浙江 宁波", "电信", ""
    r = NetCheckResult(
        ipv4=IpInfo(ip="183.132.224.185", country="中国", region="浙江",
                    city="宁波", isp="电信", asn="AS4134"),
        sources=[src],
        sites=[SiteProbe(name="微信", url="https://weixin.qq.com", group="国内",
                         ok=True, latency_ms=216)],
        adapters=[],
        elapsed_s=2.0,
    )
    return r


class _FakePool:
    """线程池替身：`submit` 只记账不真跑（离线单测，避免真联网/真线程）。"""

    def __init__(self) -> None:
        self.jobs: list = []

    def submit(self, fn, *args, **kwargs):  # noqa: ANN002, ANN003
        self.jobs.append(fn)
        return None

    def shutdown(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


@pytest.fixture(autouse=True)
def _no_favicon_network(monkeypatch):
    """阻断 favicon 真实联网：站点图标异步加载在构造期就会下载，测试里全部走占位符。

    ★ `page_netcheck` 用 `from app.netcheck_service import fetch_favicon_bytes_chain` 绑定了名字，
      故必须打 `app.ui.page_netcheck.fetch_favicon_bytes_chain`（而非服务层模块属性）才生效。
    """
    import app.ui.page_netcheck as pn

    monkeypatch.setattr(pn, "fetch_favicon_bytes_chain", lambda chain, timeout=2.0: b"")


def _stub_check_all(canned):
    """桩：兼容 `check_all(netsh_service, on_source, on_site, on_adapter)` 签名。

    ★ 2026-09-20 流式改版后，`check_all` 多了三个回调参数。旧桩
      `lambda netsh_service=None: canned` 会在关键字调用下抛 TypeError，
      被 `_worker` 的兜底 except 吞掉 → emit ("error") → UI 不渲染，
      表现为 `assert '--' == '183.132.224.185'`（**静默失败，最坑**）。
      这里不仅接受回调，还**真的逐个回调**，让单测覆盖流式渲染路径。
    """
    def _check_all(netsh_service=None, on_source=None, on_site=None, on_adapter=None):
        if on_adapter is not None:
            on_adapter(list(canned.adapters))
        for s in canned.sites:
            if on_site is not None:
                on_site(s)
        for s in canned.sources:
            if on_source is not None:
                on_source(s, canned.ipv4, canned.ipv6)
        return canned

    return _check_all


# --------------------------------------------------------------------------- #
# 构造
# --------------------------------------------------------------------------- #
def test_construct_page_netcheck(qapp):
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    assert page.objectName() == "" or page is not None
    # 初始空闲态不崩
    assert page._v4_ip.text() in ("--", "")
    page.shutdown()


def test_footer_is_single_reference_link(qapp):
    """F 板块契约（用户改版要求）：页脚只有一行「更多数据 <ip.skk.moe>」，URL 可点击。

    ★ 旧版是「数据来源 a·b·c…（点击站点名可在浏览器中打开）· IPv6 探测 …」——已废弃。
    """
    from app.constants import MSG_NETCHECK_FOOTER, NETCHECK_REFERENCE_URL
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    foot = page.findChild(QLabel, "ncFooter")
    assert foot is not None, "未找到页脚 QLabel(ncFooter)"
    assert foot.openExternalLinks() is True
    # RichText 才能渲染 <a>
    assert foot.textFormat() == Qt.TextFormat.RichText
    assert NETCHECK_REFERENCE_URL in foot.text()
    # 文案以「更多数据」开头
    assert MSG_NETCHECK_FOOTER.split("{")[0] in foot.text()
    assert "数据来源" not in foot.text()
    assert "点击站点名" not in foot.text()
    assert "IPv6 探测" not in foot.text()
    page.shutdown()


# --------------------------------------------------------------------------- #
# 版式契约（用户指定的卡片式排版，借鉴 ip.skk.moe）
# --------------------------------------------------------------------------- #
def test_sources_card_four_blocks_with_group_tags(qapp):
    """合并卡源区：恰好 4 个源块（2 国内 + 2 国际），每块**三行** = 源名+状态 / IP / 归属地。

    ★ 2026-09-21 用户要求：源信息（源名 / IP / 归属地）必须**直接铺开显示**，
      不要藏在 hover tooltip 里 —— 故源块从「一行」恢复为「三行」，
      键名相应变成 {"name","status","ip","loc"}（原为 {"name","status","loc"}）。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    assert len(page._src_blocks) == 4
    # 每块 4 个键，且 status / ip / loc 都是真 QLabel（键名改错会在此暴露）
    for blk in page._src_blocks:
        assert set(blk) == {"name", "status", "ip", "loc"}
        assert isinstance(blk["status"], QLabel)
        assert isinstance(blk["ip"], QLabel)
        assert isinstance(blk["loc"], QLabel)
    # 类别标签 = **行首大幅组标签**（2026-09-21 用户第四轮：逐源药丸标签已删）；
    # 本卡各 1 个（国内行 / 国际行），与 E「常用站点连通性」同款 objectName
    assert len(page._pub_card.findChildren(QLabel, "ncGroupCN")) == 1
    assert len(page._pub_card.findChildren(QLabel, "ncGroupINT")) == 1
    # 初始态（_render_idle 之后）：状态位「–」、IP 行占位符、归属地位空白
    for blk in page._src_blocks:
        assert blk["status"].text() == "–"
        assert blk["ip"].text() == "IP: --"
        assert blk["loc"].text() == ""
    page.shutdown()


def test_sources_form_category_rows_with_row_leader_tags(qapp):
    """源区版式契约：4 个源**按类别分行** —— 国内一行、国际一行，行首一个大幅组标签。

    ★ 用户第四轮要求（2026-09-21）：「4 个节点其实只有国内/国际两个类别，把标签合并，
      国内一行、国际一行，与常用站点连通性一样分类别显示」⇒ 行首组标签（`ncGroupCN` /
      `ncGroupINT`，与 E 卡同一套样式），源块内**不再**逐源打药丸标签。

    ★ 为什么还要同时锁「2 行 × 2 列」这个形状（前两轮的实拍教训）：
      · 竖排（1 列 × 4 行）= 12 行 → 整页 769px，溢出可用区 695（实测）；
      · 与 IP 双列「左右分栏」= 左栏仅 3 行、右栏 12 行 ⇒ 左半张卡空 140px 死区（实拍）。
    """
    from PySide6.QtWidgets import QGridLayout

    from app.constants import NETCHECK_SOURCES
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    ip_of = {blk["name"]: blk["ip"] for blk in page._src_blocks}

    # 找到同时装着 4 个源块的那个 QGridLayout，并记录每个源块的位置
    found = None
    for grid in page._pub_card.findChildren(QGridLayout):
        pos: dict[str, tuple[int, int]] = {}
        for i in range(grid.count()):
            sub = grid.itemAt(i).layout()
            if sub is None:
                continue
            widgets = {sub.itemAt(j).widget() for j in range(sub.count())}
            for name, lbl in ip_of.items():
                if lbl in widgets:
                    pos[name] = grid.getItemPosition(i)[:2]
        if len(pos) == 4:
            found = (grid, pos)
    assert found is not None, "4 个源块没有被放进同一个 QGridLayout"
    grid, pos = found

    # ① 形状：2 行 × 2 个数据列（col0 留给行首组标签）
    assert sorted(pos.values()) == [(0, 1), (0, 2), (1, 1), (1, 2)], (
        f"源块应是 2 行 × 2 列 + 行首标签列，实测 {sorted(pos.values())}"
    )

    # ② 每行行首必须有大幅组标签，且样式与 E 卡同款
    row_tag = {}
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w is None:
            continue
        r, c, _rs, _cs = grid.getItemPosition(i)
        if c == 0:
            row_tag[r] = w
    assert set(row_tag) == {0, 1}, f"两行都要有行首组标签，实测 {sorted(row_tag)}"
    assert (row_tag[0].objectName(), row_tag[0].text()) == ("ncGroupCN", "国内")
    assert (row_tag[1].objectName(), row_tag[1].text()) == ("ncGroupINT", "国际")

    # ③ 行内容必须与类别匹配：国内那行只放国内源，国际那行只放国际源
    group_of = {name: grp for name, _url, grp in NETCHECK_SOURCES}
    for name, (r, _c) in pos.items():
        expect_row = 0 if group_of[name] == "国内" else 1
        assert r == expect_row, (
            f"{name}（{group_of[name]}）应在第 {expect_row} 行，实测第 {r} 行"
        )

    # ④ 旧的逐源药丸标签不许再出现（回归防线）
    assert page._pub_card.findChildren(QLabel, "srcTagCN") == []
    assert page._pub_card.findChildren(QLabel, "srcTagINT") == []

    # ⑤ **一个源都不能被静默丢弃**：分组用的是
    #    `[s for s in NETCHECK_SOURCES if s[2] == g]`，若某个源的 group 不在
    #    NETCHECK_SOURCE_GROUPS 里，它会被漏掉且**不报任何错**（QA 2026-09-21 指出的脆弱点）。
    assert set(ip_of) == {name for name, _u, _g in NETCHECK_SOURCES}, (
        f"源清单与卡上源块不一致：卡上 {sorted(ip_of)}"
    )
    page.shutdown()


def test_no_card_pools_slack_at_the_bottom(qapp):
    """版式守卫：三张卡都**不许**在布局末尾放 stretch 项（底部空白空间的根因）。

    ★ 用户连续两轮反馈（2026-09-21）：
      · 「公网IP和多源校验卡片…现在卡片下部还有空白空间」
      · 「优化本机网络信息卡片中信息分布，现在卡片下部还有空白空间」
      根因是同一个 —— 卡末的 `addStretch(1)` 会把该卡分配到的富余高度**全堆在卡底**，
      内容却挤在卡片上半部分。正确做法：把富余交给承载内容的子布局
      （`lay.addLayout(grid, 1)`），让内容自然撑满整卡。

    实现口径：卡布局的**最后一项不得是 spacer**（`QLayoutItem.spacerItem()` 判空）。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    cards = (
        ("本机网络信息", page._net_card),
        ("公网 IP · 多源校验", page._pub_card),
        ("常用站点连通性", page._site_card),
    )
    for label, card in cards:
        lay = card.layout()
        assert lay is not None and lay.count() > 0, f"{label}：卡布局为空"
        last = lay.itemAt(lay.count() - 1)
        assert last.spacerItem() is None, (
            f"{label}：卡布局末尾有 stretch 项 —— 富余高度会堆成「卡片下部空白空间」"
        )
    page.shutdown()


def test_sites_grid_one_group_tag_per_row(qapp):
    """E 板块：每行**一个**大幅组标签（行首），组内站点不再单独打标签。

    ★ 用户明确要求（2026-09-20）：同一行就是一个组，逐站点标签冗余又小气。
      → 组标签 objectName 从 srcTagCN/INT 换成 ncGroupCN/ncGroupINT（更大样式）。

    ⚠️ 断言必须**圈定在本卡内**（2026-09-21）：合并卡的源区也改用了同一套
      `ncGroupCN`/`ncGroupINT`（用户要求"与常用站点连通性一样"），页面级
      `findChildren` 会同时数到那 2 个，得到 2 而非 1。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    assert list(page._group_tags) == ["国内", "国际"]
    assert len(page._site_card.findChildren(QLabel, "ncGroupCN")) == 1
    assert len(page._site_card.findChildren(QLabel, "ncGroupINT")) == 1
    for tag in page._group_tags.values():
        assert tag.alignment() & Qt.AlignmentFlag.AlignCenter
    page.shutdown()


def test_sites_grid_four_per_row_with_icons_and_dots(qapp):
    """E 板块：8 站点、每行 4 个（国内一行 + 国际一行），带图标与彩点条。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    assert len(page._site_cells) == 8
    assert len(page._sites_order) == 8
    # 国内在前 4 位、国际在后 4 位（一行四个的排布前提）
    from app.constants import NETCHECK_SITES
    assert [s["name"] for s in NETCHECK_SITES if s["group"] == "国内"] == page._sites_order[:4]
    # 每站点都有彩点条（RichText）+ 延迟标签
    for name, cell in page._site_cells.items():
        assert cell["dots"].textFormat() == Qt.TextFormat.RichText
        assert "lat" in cell
    # 初始彩点条：10 个空心圆
    dots_text = page._site_cells[page._sites_order[0]]["dots"].text()
    assert dots_text.count("○") == 10
    page.shutdown()


# --------------------------------------------------------------------------- #
# 信号接线（★ 关键路径）：_worker → Signal → _on_check_done → 渲染
# --------------------------------------------------------------------------- #
def test_worker_signal_renders_result(qapp, monkeypatch):
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    canned = _make_result()
    # 用桩替换真实网络检测（离线）；桩会真的触发 on_source / on_site 回调
    page._service.check_all = _stub_check_all(canned)

    # 直接在主线程跑 _worker：emit 是直连 → 同步触发 _on_check_done
    page._running = False
    page._worker()

    # 渲染结果已落到 UI（证明信号接线真的把结果传到了主线程渲染）
    assert page._v4_ip.text() == "183.132.224.185"
    # 合并卡源区第一行：状态位 ✓（已返回且符合共识）、归属地位含归属地+运营商
    blk = page._src_blocks[0]
    assert blk["status"].text() == "✓"
    assert blk["loc"].text() == "中国 浙江 宁波 电信"
    # ★ IP 不再上卡面，但必须进 tooltip（否则「分歧」无处追查）
    assert "183.132.224.185" in blk["status"].toolTip()
    assert "183.132.224.185" in blk["loc"].toolTip()
    # E 板块：微信 216ms → 绿字；彩点条 = 延迟条形图（216ms 落 101~300 档 → 4 实点）
    assert page._site_cells["微信"]["lat"].text() == "216ms"
    assert page._site_cells["微信"]["dots"].text().count("●") == 4
    # 行内状态回到「完成」（非 running）
    assert page._running is False
    assert page._refresh_btn.isEnabled() is True
    page.shutdown()


def test_refresh_sets_running_then_resets(qapp, monkeypatch):
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    canned = _make_result()
    page._service.check_all = _stub_check_all(canned)

    # on_refresh 应置 running + 禁用按钮（防重入）
    page.on_refresh()
    assert page._running is True
    assert page._refresh_btn.isEnabled() is False
    # 把 worker 同步跑完（绕过线程池，直接验证收尾逻辑）
    page._worker()
    assert page._running is False
    assert page._refresh_btn.isEnabled() is True
    page.shutdown()


def test_on_enter_is_idempotent(qapp, monkeypatch):
    """进入页面自动检测；running 中再次进入不应重复发起。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    calls = {"n": 0}
    orig = page.on_refresh

    def counting_refresh():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(page, "on_refresh", counting_refresh)
    page._result = None
    page.on_enter()
    assert calls["n"] == 1
    # 已在 running → 再次进入不重复
    page._running = True
    page.on_enter()
    assert calls["n"] == 1
    page.shutdown()


# --------------------------------------------------------------------------- #
# 线程契约守卫（静态源码检查，P0）
# --------------------------------------------------------------------------- #
def test_worker_uses_signal_not_qtimer():
    """_worker 内必须 `self._check_done.emit(...)`，且**不得**出现 QTimer.singleShot。"""
    import os
    import importlib
    mod = importlib.import_module("app.ui.page_netcheck")
    path = os.path.join(os.path.dirname(mod.__file__), "page_netcheck.py")
    src = open(path, encoding="utf-8").read()

    # 抽取 _worker 方法体
    m = re.search(r"def _worker\(self.*?(?=\n    def )\n", src, re.S)
    assert m, "未找到 _worker 方法"
    body = m.group(0)
    assert "self._check_done.emit" in body, "_worker 必须经由 Signal emit 回主线程"
    # 仅禁止**实际调用** `QTimer.singleShot(`（docstring 里的警示性提及不算）。
    # 工作线程无事件循环，QTimer 回调会静默失效（项目 P0 级坑）。
    assert "QTimer.singleShot(" not in body, "_worker 禁止实际调用 QTimer.singleShot（会静默失效）"
    # ★ 流式（2026-09-20）：_worker 必须把三个回调传进服务层，否则又退化成"等全部"。
    for cb in ("on_source=", "on_site=", "on_adapter="):
        assert cb in body, f"_worker 必须传 {cb} 回调（用户要求「哪个有结果哪个就显示」）"


# --------------------------------------------------------------------------- #
# 彩点条 = 延迟条形图（2026-09-20 用户改版）
# --------------------------------------------------------------------------- #
def test_dot_count_grows_with_latency():
    """延迟越小点越少、越大点越多；不可达 = 0 点（全空心）。

    ★ 用户原话：「延迟小的显示少，延迟大的，显示多」。
      旧画法是「历史留痕」（每次检测追加 1 点），首跑只有 1 个实点、
      其余 9 个空心，用户问「这么多圈起什么作用」——已废弃，勿改回。
    """
    from app.constants import NETCHECK_DOT_COUNT
    from app.ui.page_netcheck import _dot_count

    levels = [_dot_count(ms, True) for ms in (30, 200, 500, 800, 3000)]
    assert levels == sorted(levels), f"点亮的个数必须随延迟单调不减，实际 {levels}"
    assert levels[0] < levels[-1], "最快与最慢必须拉开差距（否则条形图无意义）"
    assert 1 <= levels[0], "有延迟就至少亮 1 点（否则与「不可达」无法区分）"
    assert levels[-1] == NETCHECK_DOT_COUNT, "最慢一档应铺满"
    # 不可达 / 无延迟 → 0 点
    assert _dot_count(None, True) == 0
    assert _dot_count(5000, False) == 0
    # ★ 实测样本回归（2026-09-20）：同为红色时，"更慢"必须"更多"（9 < 10 满格）
    assert _dot_count(1136, True) == 9, "Cloudflare 1136ms 应为 9 点"
    assert _dot_count(1746, True) == 10, "jsDelivr 1746ms 应为满格 10 点"


def test_paint_dots_draws_bar_from_left():
    """彩点条画法：左起 lit 个实心 + 其余空心，总长恒为 NETCHECK_DOT_COUNT。"""
    from app.constants import NETCHECK_DOT_COUNT
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    name = page._sites_order[0]
    page._paint_dots(name, 3)
    txt = page._site_cells[name]["dots"].text()
    assert txt.count("●") == 3
    assert txt.count("○") == NETCHECK_DOT_COUNT - 3
    # 实心必须**聚在左侧**（条形图，而非散点）
    assert txt.index("●") < txt.rindex("●") < txt.index("○")
    # 越界保护
    page._paint_dots(name, 999)
    assert page._site_cells[name]["dots"].text().count("●") == NETCHECK_DOT_COUNT
    page._paint_dots(name, -5)
    assert page._site_cells[name]["dots"].text().count("●") == 0
    page.shutdown()


# --------------------------------------------------------------------------- #
# 流式渲染（2026-09-20 用户改版：「哪个有结果哪个就显示，不用等别人」）
# --------------------------------------------------------------------------- #
def test_streaming_renders_one_site_without_wiping_others(qapp):
    """单个站点回调后：该格立刻上屏，**其余格保持原状**（不得把"未返回"写成失败）。"""
    from app.constants import NETCHECK_VALUE_DASH
    from app.netcheck_service import SiteProbe
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    first, second = page._sites_order[0], page._sites_order[1]

    page._site_ready.emit(
        SiteProbe(name=first, url="u", group="国内", ok=True, latency_ms=89)
    )

    # 先回来的那格已上屏（89ms → 最快档 2 点）
    assert page._site_cells[first]["lat"].text() == "89ms"
    assert page._site_cells[first]["dots"].text().count("●") == 2
    # ★ 其余格**仍是占位符**，不能被提前判成「不可达」
    assert page._site_cells[second]["lat"].text() == NETCHECK_VALUE_DASH
    # 汇总按已探测数走（1/1），不是 8/8
    assert page._site_summary.text() == "可达 1 / 1"
    page.shutdown()


def test_streaming_source_enables_copy_ip_early(qapp):
    """一个源返回即可「复制 IP」，不必等全部源与全部站点。"""
    from app.netcheck_service import IpInfo, SourceResult
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    # 桩掉服务层，避免 on_refresh 真的联网（离线单测）
    page._service.check_all = _stub_check_all(_make_result())
    page.on_refresh()
    assert page._copy_btn.isEnabled() is False      # 检测中先禁用

    sr = SourceResult(name="ipip.net", url="u", group="国内", ok=True,
                      elapsed_s=0.2, ip="183.132.224.185")
    v4 = IpInfo(ip="183.132.224.185")
    page._src_ready.emit((sr, v4, IpInfo(ip="")))

    assert page._v4_ip.text() == "183.132.224.185"
    assert page._copy_btn.isEnabled() is True
    assert page._live_ipv4 == "183.132.224.185"
    page.shutdown()


def test_service_streams_in_completion_order(monkeypatch):
    """服务层契约：`check_all` 按**完成顺序**回调（快的先回），而非提交顺序。

    ★ 这是「哪个有结果哪个就显示」的底层保证：若改成 for 顺序 `fut.result()`，
      本测试会立刻失败（回调顺序退化成 慢→快）。
    """
    import inspect
    import time

    import app.netcheck_service as svc
    from app.netcheck_service import NetCheckService, SiteProbe, SourceResult

    sig = inspect.signature(NetCheckService.check_all)
    for p in ("on_source", "on_site", "on_adapter"):
        assert p in sig.parameters, f"check_all 必须支持 {p} 回调"

    # ---- 免联网：清空源 / IPv6 探测，站点换成「快慢可控」的假探测 ----
    monkeypatch.setattr(svc, "NETCHECK_SOURCES", [])
    monkeypatch.setattr(svc, "NETCHECK_IPV6_PROBES", [])
    fake_sites = [
        {"name": "慢", "url": "https://slow.test", "group": "国内"},
        {"name": "快", "url": "https://fast.test", "group": "国内"},
        {"name": "中", "url": "https://mid.test", "group": "国内"},
    ]
    monkeypatch.setattr(svc, "NETCHECK_SITES", fake_sites)
    delay = {"https://slow.test": 0.30, "https://mid.test": 0.15, "https://fast.test": 0.0}

    def fake_probe(site):
        time.sleep(delay[site["url"]])
        return SiteProbe(name=site["name"], url=site["url"], group="国内",
                         ok=True, latency_ms=1)

    monkeypatch.setattr(svc, "_probe_site", fake_probe)

    order: list[str] = []
    r = NetCheckService().check_all(on_site=lambda p: order.append(p.name))

    # 回调顺序 = 完成顺序（快 → 中 → 慢），不是声明顺序（慢 → 快 → 中）
    assert order == ["快", "中", "慢"], f"回调必须按完成顺序触发，实际 {order}"
    # 但最终结果仍按常量声明顺序（as_completed 会打乱 append 顺序，必须重排）
    assert [s.name for s in r.sites] == ["慢", "快", "中"]
    # 清空源后不产生任何源（证明免联网生效）
    assert all(isinstance(s, SourceResult) for s in r.sources)


# --------------------------------------------------------------------------- #
# E 板块独立刷新按钮（2026-09-20 用户要求：「此按钮只用于刷新站点连通状态」）
# --------------------------------------------------------------------------- #
def test_sites_card_has_refresh_button(qapp):
    """E 卡片标题行内有「🔄 刷新」按钮：objectName 合法、走过 `fit_button`、tooltip 到位。"""
    from app.constants import MSG_NETCHECK_SITES_REFRESH, MSG_NETCHECK_SITES_TIP
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    btn = page._sites_refresh_btn
    assert btn.text() == MSG_NETCHECK_SITES_REFRESH
    assert "刷新" in btn.text()
    # objectName 必须是已登记样式（ghostBtn 或本项目新增的 ncMiniBtn）
    assert btn.objectName() in ("ghostBtn", "ncMiniBtn")
    assert btn.toolTip() == MSG_NETCHECK_SITES_TIP
    # 按钮必须挂在**站点卡片内**（不是顶部工具行）
    assert any(b is btn for b in page._site_card.findChildren(QPushButton))
    # `fit_button` 被调用过的证据：最小宽 ≥ 文本宽度（否则窄窗口下按钮文字会被裁）
    assert btn.minimumWidth() >= btn.fontMetrics().horizontalAdvance(btn.text())
    page.shutdown()


def test_sites_refresh_button_is_wired(qapp, monkeypatch):
    """点击「🔄 刷新」真的发起站点刷新（接线没漏）。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    fake = _FakePool()
    monkeypatch.setattr(page, "_site_pool", fake)
    page._sites_refresh_btn.click()
    assert page._sites_running is True
    assert len(fake.jobs) == 1, "点击后必须往 _site_pool 提交一次任务"
    page.shutdown()


def _snapshot_non_site_fields(page) -> dict:
    """拍下**所有**非站点字段，供「只刷站点」前后逐字段比对。

    ★ 为什么必须全量（QA 探针 M4 已证伪"只盯 4 类字段"的旧版守卫）：
      旧断言只比 `v4_ip` / `time_label` / `net_rows` / `src_blocks["ip"]`，
      于是在 `on_refresh_sites()` 里篡改 `v6_ip` / `v6_loc` / `v4_isp` /
      `_agree_label` / `_net_hint` 时，23 个用例**全绿**——守卫是空壳。
      E 板块之外的字段一个都不能漏，否则就是给回归留后门。
    """
    return {
        "v4_ip": page._v4_ip.text(),
        "v4_loc": page._v4_loc.text(),
        "v4_isp": page._v4_isp.text(),
        "v6_ip": page._v6_ip.text(),
        "v6_loc": page._v6_loc.text(),
        "v6_isp": page._v6_isp.text(),
        "time": page._time_label.text(),
        "status": page._status_label.text(),
        "agree": page._agree_label.text(),
        "net_rows": [v.text() for _k, v in page._net_rows],
        "net_hint": page._net_hint.text(),
        "src_status": [blk["status"].text() for blk in page._src_blocks],
        "src_loc": [blk["loc"].text() for blk in page._src_blocks],
        "copy_enabled": page._copy_btn.isEnabled(),
    }


def _snapshot_excluding_sources(page) -> dict:
    """拍下**除源块以外**的所有字段（供「只刷源」前后比对：源块本就该被重置）。

    额外拍下 8 个站点格子的「延迟/点阵」文本，作为变异探针——若「只刷源」
    误伤了站点卡（清空或改写），此处即可证伪。
    """
    s = _snapshot_non_site_fields(page)
    s.pop("src_status", None)
    s.pop("src_loc", None)
    s.pop("agree", None)  # 「只刷源」开头会清空 agree_label，本就不该比对
    s["site_cells"] = {
        n: (c["lat"].text(), c["dots"].text()) for n, c in page._site_cells.items()
    }
    return s


def test_refresh_sites_touches_only_site_cells(qapp, monkeypatch):
    """★ 核心契约「只刷站点」：E 板块之外的**每一个**字段都保持原值，
    只有 8 个站点格子被重置为占位符；且全程**不往状态栏推任何消息**。

    变异验证：
    · 改成调 `_render_idle()`（连带清掉 IP 卡 / 网卡 / 源块）→ 失败（M2）
    · 顺手篡改 `v6_ip` / `v6_loc` / `v4_isp` / `_agree_label` / `_net_hint`
      → 失败（M4，QA 探针证伪过的空壳缺口）
    · 往状态栏 `emit(..., "danger")` → 失败（M5，守卫铁律「国际站点超时不写红字」）
    """
    from app.constants import MSG_NETCHECK_RUNNING, NETCHECK_VALUE_DASH
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    # ---- 先跑一轮完整检测，把各板块填上真实值 ----
    page._service.check_all = _stub_check_all(_make_result())
    page._worker()
    before = _snapshot_non_site_fields(page)
    assert before["v4_ip"] == "183.132.224.185", "前置条件：IPv4 卡已被填上真实 IP"
    assert before["time"].startswith("最后检测"), "前置条件：时间标签已被填上"
    # 前置：被盯的字段确实非空（空字符串比对不出"被清空"以外的篡改）
    assert before["v6_loc"] and before["net_hint"]

    # 状态栏 spy：只刷站点全程不得推任何消息（尤其不得有 danger 红字）
    emitted: list[tuple[str, str]] = []
    page.status_message.connect(lambda text, level: emitted.append((text, level)))

    fake = _FakePool()
    monkeypatch.setattr(page, "_site_pool", fake)
    page.on_refresh_sites()

    # ---- 其余板块一个字都不能动（全字段比对）----
    after = _snapshot_non_site_fields(page)
    assert after == before, f"只刷站点却改动了非站点字段：{after} != {before}"
    # ---- 只有站点格子被重置成占位符 ----
    for name in page._sites_order:
        assert page._site_cells[name]["lat"].text() == NETCHECK_VALUE_DASH
        assert page._site_cells[name]["dots"].text().count("●") == 0
    assert page._site_summary.text() == MSG_NETCHECK_RUNNING
    # ---- 按钮进入 running 态 ----
    assert page._sites_running is True
    assert page._sites_refresh_btn.isEnabled() is False
    assert "检测中" in page._sites_refresh_btn.text()
    assert len(fake.jobs) == 1

    # ---- 收尾链路（桩掉服务层免联网）同样不得碰状态栏 ----
    page._service.check_sites = lambda on_site=None: []
    page._sites_worker()
    assert page._sites_running is False
    assert _snapshot_non_site_fields(page) == before
    assert emitted == [], f"只刷站点不得写状态栏，实际收到：{emitted}"
    assert not any(lv == "danger" for _t, lv in emitted), "站点刷新不得写状态栏红字"
    page.shutdown()


def test_on_refresh_blocked_by_sites_refresh_without_side_effects(qapp, monkeypatch):
    """★ `on_refresh()` 入口拦截：站点刷新在跑 → 主检测**不得**发起，且**零副作用**。

    为什么入口也要拦（QA 探针 A 实测复现）：`on_enter()` 在 `_result is None` 时
    会**直调** `on_refresh()`，绕开按钮的禁用状态。首轮以 error 收尾（`_result`
    仍 None）时点站点刷新 → 切走再切回 → 两套检测并发，正是「谁后返回谁赢」竞态。

    变异验证：
    · 删掉 `or self._sites_running` → 第 1 段断言失败；
    · 把 return 改成"排队等待站点刷新结束后补跑" → 第 2 段断言失败。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    pool = _FakePool()
    monkeypatch.setattr(page, "_pool", pool)
    monkeypatch.setattr(page, "_site_pool", _FakePool())
    emitted: list[tuple[str, str]] = []
    page.status_message.connect(lambda text, level: emitted.append((text, level)))

    # ---- 1) 站点刷新进行中 → on_refresh() 直接返回，且不碰任何状态 ----
    page.on_refresh_sites()
    assert page._sites_running is True
    status_before = page._status_label.text()
    btn_main_before = page._refresh_btn.isEnabled()

    page.on_refresh()
    assert page._running is False, "站点刷新期间不得发起主检测"
    assert len(pool.jobs) == 0, "站点刷新期间不得往主线程池提交任务"
    # 零副作用：状态文案 / 按钮 / 状态栏都没被动过
    assert page._status_label.text() == status_before
    assert page._refresh_btn.isEnabled() is btn_main_before
    assert emitted == [], f"被拦下的 on_refresh 不得写状态栏：{emitted}"

    # ---- 2) 站点刷新结束后也不得"补跑"（证明不是排队等待，而是真的放弃这次）----
    page._on_sites_done(("ok", []))
    assert page._running is False
    assert len(pool.jobs) == 0, "不得把被拦下的主检测排队到站点刷新之后补跑"

    # ---- 3) 站点刷新彻底结束后，主检测可正常发起 ----
    page.on_refresh()
    assert page._running is True
    assert len(pool.jobs) == 1
    page.shutdown()


def test_refresh_sites_is_reentrant_guarded(qapp, monkeypatch):
    """防重入：站点刷新中 / 主检测中，都不再发起；主检测会连带禁用刷新按钮。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    fake = _FakePool()
    monkeypatch.setattr(page, "_site_pool", fake)

    page.on_refresh_sites()
    assert len(fake.jobs) == 1
    page.on_refresh_sites()                 # _sites_running=True → 不重复发起
    assert len(fake.jobs) == 1

    page._sites_running = False
    page._running = True                    # 主检测进行中 → 也不发起
    page.on_refresh_sites()
    assert len(fake.jobs) == 1

    # 主检测期间站点按钮一并禁用，结束后恢复
    # ⚠️ 两个前置复位缺一不可，否则断言会「假绿」：
    #    · `_running` 还是 True → on_refresh() 直接 return，压根走不到置灰；
    #    · 按钮已被上面第一次 on_refresh_sites() 置灰 → 不复位的话恒为 False。
    #      （这两个坑都是变异测试实测抓出来的，勿删。）
    page._running = False
    page._sites_refresh_btn.setEnabled(True)
    page._service.check_all = _stub_check_all(_make_result())
    page.on_refresh()
    assert page._sites_refresh_btn.isEnabled() is False
    page._worker()
    assert page._sites_refresh_btn.isEnabled() is True
    page.shutdown()


def test_refresh_buttons_are_mutually_exclusive(qapp, monkeypatch):
    """★ 双向互斥：任一流程在跑，两个刷新按钮**都**禁用。

    为什么必须双向（不是洁癖，是真实竞态，2026-09-20 team-lead 拍板）：
    两套流程末尾都会「用自己那份列表幂等重渲站点格」，谁**后返回**谁赢，
    与发起顺序无关。只禁用单向的话，用户可以 t=0 点站点刷新、t=1 点主检测，
    t=7 站点刷新返回时用 t=0 那批**更早**的探测结果覆盖主检测刚上的新数据。

    变异验证：
    · 去掉 `on_refresh_sites()` 里的主按钮禁用 → 第一条断言失败；
    · 把 `_on_sites_done()` 的恢复改成无条件 `setEnabled(True)` → 第三条断言失败。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    fake = _FakePool()
    monkeypatch.setattr(page, "_site_pool", fake)

    # ---- 1) 站点刷新进行中 → 主「🔄 重新检测」必须禁用 ----
    page.on_refresh_sites()
    assert page._sites_running is True
    assert page._sites_refresh_btn.isEnabled() is False
    assert page._refresh_btn.isEnabled() is False, "反向互斥缺失：主检测按钮仍可点"

    # ---- 2) 站点刷新结束 → 两个按钮都恢复 ----
    page._on_sites_done(("ok", []))
    assert page._sites_running is False
    assert page._refresh_btn.isEnabled() is True
    assert page._sites_refresh_btn.isEnabled() is True

    # ---- 3) 主检测仍在跑时，站点刷新结束**不得**抢着恢复主按钮 ----
    page.on_refresh_sites()
    assert page._sites_running is True
    page._running = True                    # 模拟此刻主检测正在进行
    page._on_sites_done(("ok", []))
    assert page._sites_running is False
    assert page._refresh_btn.isEnabled() is False, "主检测没结束就恢复了主按钮"
    assert page._sites_refresh_btn.isEnabled() is False

    # ---- 4) 主检测结束 → 两个按钮一起恢复（对称）----
    page._on_check_done(("error", "模拟主检测异常收尾"))
    assert page._running is False
    assert page._refresh_btn.isEnabled() is True
    assert page._sites_refresh_btn.isEnabled() is True

    # ---- 5) 源刷新进行中 → 三个按钮**都**禁用（新增第三把按钮的对称互斥）----
    page.on_refresh_sources()
    assert page._src_running is True
    assert page._src_refresh_btn.isEnabled() is False
    assert page._sites_refresh_btn.isEnabled() is False
    assert page._refresh_btn.isEnabled() is False, "源刷新缺反向互斥：主/站点按钮仍可点"

    # ---- 6) 源刷新结束 → 三个按钮都恢复 ----
    page._on_sources_refresh_done(("ok", []))
    assert page._src_running is False
    assert page._refresh_btn.isEnabled() is True
    assert page._sites_refresh_btn.isEnabled() is True
    assert page._src_refresh_btn.isEnabled() is True

    # ---- 7) 主检测仍在跑时，源刷新结束**不得**抢着恢复其它按钮 ----
    page.on_refresh_sources()
    assert page._src_running is True
    page._running = True                    # 模拟此刻主检测正在进行
    page._on_sources_refresh_done(("ok", []))
    assert page._src_running is False
    assert page._refresh_btn.isEnabled() is False, "主检测没结束就恢复了主按钮"
    assert page._sites_refresh_btn.isEnabled() is False
    assert page._src_refresh_btn.isEnabled() is False

    # ---- 8) 主检测结束 → 三个按钮一起恢复（对称收尾）----
    page._on_check_done(("error", "模拟主检测异常收尾"))
    assert page._running is False
    assert page._refresh_btn.isEnabled() is True
    assert page._sites_refresh_btn.isEnabled() is True
    assert page._src_refresh_btn.isEnabled() is True
    page.shutdown()


def test_on_sites_done_renders_all_cells_and_restores_button(qapp):
    """`_on_sites_done(("ok", [...]))`：8 格全有值、汇总「可达 n / 8」、按钮恢复可用，
    且**不**动 IP 卡与最后检测时间。"""
    from app.constants import (
        MSG_NETCHECK_NEVER,
        MSG_NETCHECK_SITES_REFRESH,
        NETCHECK_VALUE_DASH,
    )
    from app.netcheck_service import SiteProbe
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    probes = [
        SiteProbe(name=name, url="u", group="国内", ok=(i < 3),
                  latency_ms=60 if i < 3 else None)
        for i, name in enumerate(page._sites_order)
    ]
    page._sites_running = True
    page._sites_refresh_btn.setEnabled(False)
    page._on_sites_done(("ok", probes))

    assert page._sites_running is False
    assert page._sites_refresh_btn.isEnabled() is True
    assert page._sites_refresh_btn.text() == MSG_NETCHECK_SITES_REFRESH
    for name in page._sites_order:
        assert page._site_cells[name]["lat"].text() != NETCHECK_VALUE_DASH
    assert page._site_summary.text() == "可达 3 / 8"
    # ★ 只刷站点：IP 卡与最后检测时间必须还是初始态
    assert page._v4_ip.text() == NETCHECK_VALUE_DASH
    assert page._time_label.text() == MSG_NETCHECK_NEVER
    page.shutdown()


# --------------------------------------------------------------------------- #
# 线程契约守卫（静态源码检查，P0）——「只刷站点」这条新链路同样不许用 QTimer
# --------------------------------------------------------------------------- #
def test_sites_worker_uses_signal_not_qtimer():
    """`_sites_worker` 必须 `self._sites_done.emit(...)`，且不得出现 QTimer.singleShot。"""
    import importlib
    import os

    mod = importlib.import_module("app.ui.page_netcheck")
    path = os.path.join(os.path.dirname(mod.__file__), "page_netcheck.py")
    src = open(path, encoding="utf-8").read()

    m = re.search(r"def _sites_worker\(self.*?(?=\n    def )\n", src, re.S)
    assert m, "未找到 _sites_worker 方法"
    body = m.group(0)
    assert "self._sites_done.emit" in body, "_sites_worker 必须经由 Signal emit 回主线程"
    assert "QTimer.singleShot(" not in body, "_sites_worker 禁止实际调用 QTimer.singleShot"
    # 单站点流式上屏必须**复用**已有的 _site_ready 信号（不要另造一个）
    assert "on_site=self._site_ready.emit" in body, "站点流式上屏必须复用 _site_ready"


# --------------------------------------------------------------------------- #
# 服务层 `check_sites`（只探站点）
# --------------------------------------------------------------------------- #
def test_check_sites_skips_sources_ipv6_and_adapters(monkeypatch):
    """★ 服务层契约：`check_sites` **只**探站点 —— 不查源、不查 IPv6 兜底、不读网卡。

    变异验证：若在 `check_sites` 里加一句 `self._read_adapters(...)` 或把源塞进
    future 表，本测试立刻失败（计数器不是零）。
    """
    import app.netcheck_service as svc
    from app.netcheck_service import NetCheckService, SiteProbe, SourceResult

    calls = {"source": 0, "adapter": 0, "fetch": 0}

    def fake_query_source(name):
        calls["source"] += 1
        return SourceResult(name=name), None

    def fake_read_adapters(netsh_service):
        calls["adapter"] += 1
        return []

    def fake_fetch(url, timeout=0):
        calls["fetch"] += 1
        return False, "", 0.0, ""

    def fake_probe_site(site):
        return SiteProbe(name=site["name"], url=site["url"],
                         group=site.get("group", "国内"), ok=True, latency_ms=7)

    monkeypatch.setattr(svc, "_query_source", fake_query_source)
    monkeypatch.setattr(svc.NetCheckService, "_read_adapters", staticmethod(fake_read_adapters))
    monkeypatch.setattr(svc, "_fetch", fake_fetch)
    monkeypatch.setattr(svc, "_probe_site", fake_probe_site)

    probes = NetCheckService().check_sites()
    assert len(probes) == len(svc.NETCHECK_SITES) == 8
    assert calls == {"source": 0, "adapter": 0, "fetch": 0}, (
        f"check_sites 越界查询了非站点数据：{calls}")

    # ---- 正对照：同样的计数器下 check_all 必须**真的**调用它们 ----
    #      （否则本守卫可能是"计数器装错了"的假绿）
    NetCheckService().check_all(netsh_service=object())
    assert calls["source"] >= 1, "正对照失败：check_all 也未查源，计数器不可信"
    assert calls["adapter"] == 1, "正对照失败：check_all 也未读网卡，计数器不可信"
    assert calls["fetch"] >= 1, "正对照失败：check_all 也未探 IPv6，计数器不可信"


def test_check_sites_streams_and_returns_declared_order(monkeypatch):
    """`check_sites`：回调按**完成顺序**（流式），返回值按 `NETCHECK_SITES` 声明顺序。

    变异验证：删掉 `sites.sort(...)` 那一行 → 返回值顺序退化成完成顺序，本测试失败。
    """
    import time

    import app.netcheck_service as svc
    from app.netcheck_service import NetCheckService, SiteProbe

    fake_sites = [
        {"name": "慢", "url": "https://slow.test", "group": "国内"},
        {"name": "快", "url": "https://fast.test", "group": "国内"},
        {"name": "中", "url": "https://mid.test", "group": "国内"},
    ]
    monkeypatch.setattr(svc, "NETCHECK_SITES", fake_sites)
    delay = {"https://slow.test": 0.30, "https://mid.test": 0.15, "https://fast.test": 0.0}

    def fake_probe(site):
        time.sleep(delay[site["url"]])
        return SiteProbe(name=site["name"], url=site["url"], group="国内",
                         ok=True, latency_ms=1)

    monkeypatch.setattr(svc, "_probe_site", fake_probe)

    order: list[str] = []
    probes = NetCheckService().check_sites(on_site=lambda p: order.append(p.name))

    assert order == ["快", "中", "慢"], f"回调必须按完成顺序触发，实际 {order}"
    assert [p.name for p in probes] == ["慢", "快", "中"], (
        "返回值必须按常量声明顺序（as_completed 顺序不可直接展示）")


# --------------------------------------------------------------------------- #
# 服务层 `check_sources`（只查 4 个数据源）
# --------------------------------------------------------------------------- #
def test_check_sources_queries_only_sources(monkeypatch):
    """★ 服务层契约：`check_sources` **只**查源 —— 不探站点、不读网卡、不查 IPv6 兜底。

    变异验证：若在 `check_sources` 里加一句 `_probe_site(...)` 或 `_read_adapters(...)`，
    本测试立刻失败（计数器不是零）。
    """
    import app.netcheck_service as svc
    from app.netcheck_service import IpInfo, NetCheckService, SourceResult, SiteProbe

    calls = {"site": 0, "adapter": 0}

    def fake_query_source(name):
        sr = SourceResult(name=name, url="u", group="国内", ok=True, ip="183.132.224.185")
        return sr, IpInfo(ip="183.132.224.185", country="中国", source=name)

    def fake_read_adapters(netsh_service):
        calls["adapter"] += 1
        return []

    def fake_probe_site(site):
        calls["site"] += 1
        return SiteProbe(name=site["name"], url=site["url"], group="国内", ok=True, latency_ms=7)

    monkeypatch.setattr(svc, "_query_source", fake_query_source)
    monkeypatch.setattr(svc.NetCheckService, "_read_adapters", staticmethod(fake_read_adapters))
    monkeypatch.setattr(svc, "_probe_site", fake_probe_site)
    # IPv6 兜底探测也离线化，避免正对照里真联网
    monkeypatch.setattr(svc, "_fetch", lambda url, timeout=0: (False, "", 0.0, "离线"))

    from app.constants import NETCHECK_SOURCES

    order: list[str] = []
    sigs: list[tuple] = []
    results = NetCheckService().check_sources(
        on_source=lambda sr, v4, v6: (order.append(sr.name),
                                      sigs.append((type(sr), type(v4), type(v6))))
    )
    # 返回 4 个，按 NETCHECK_SOURCES 声明顺序
    assert len(results) == 4
    assert [s.name for s in results] == [n for n, _u, _g in NETCHECK_SOURCES]
    # 回调签名 (SourceResult, IpInfo, IpInfo)
    assert sigs and all(t == (SourceResult, IpInfo, IpInfo) for t in sigs), f"回调签名不符：{sigs}"
    # 不越界
    assert calls == {"site": 0, "adapter": 0}, f"check_sources 越界查询了非源数据：{calls}"

    # ---- 正对照：同样的计数器下 check_all 必须**真的**探站点 / 读网卡 ----
    #      （否则本守卫可能是"计数器装错了"的假绿）
    NetCheckService().check_all(netsh_service=object())
    assert calls["site"] >= 1, "正对照失败：check_all 也未探站点，计数器不可信"
    assert calls["adapter"] == 1, "正对照失败：check_all 也未读网卡，计数器不可信"


# --------------------------------------------------------------------------- #
# C 板块独立刷新按钮（只刷 4 个数据源）—— 镜像 E 板块「只刷站点」
# --------------------------------------------------------------------------- #
def test_sources_card_has_refresh_button(qapp):
    """合并卡标题行内有「🔄 刷新」按钮：objectName 合法、走过 `fit_button`、tooltip 到位。"""
    from app.constants import MSG_NETCHECK_SOURCES_REFRESH, MSG_NETCHECK_SOURCES_TIP
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    btn = page._src_refresh_btn
    assert btn.text() == MSG_NETCHECK_SOURCES_REFRESH
    assert "刷新" in btn.text()
    assert btn.objectName() in ("ghostBtn", "ncMiniBtn")
    assert btn.toolTip() == MSG_NETCHECK_SOURCES_TIP
    # 按钮必须挂在**合并卡内**（不是顶部工具行）
    assert any(b is btn for b in page._pub_card.findChildren(QPushButton))
    assert btn.minimumWidth() >= btn.fontMetrics().horizontalAdvance(btn.text())
    page.shutdown()


def test_sources_refresh_button_is_wired(qapp, monkeypatch):
    """点击合并卡「🔄 刷新」真的发起源刷新（接线没漏）。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    fake = _FakePool()
    monkeypatch.setattr(page, "_src_pool", fake)
    page._src_refresh_btn.click()
    assert page._src_running is True
    assert len(fake.jobs) == 1, "点击后必须往 _src_pool 提交一次任务"
    page.shutdown()


def _src_result(name, ip="", ok=True, ipv6=False, err="", loc="", provider=""):
    """造一个 SourceResult（合并卡源区测试共用）。"""
    from app.netcheck_service import SourceResult

    sr = SourceResult(name=name, url="u", ok=ok, ip=ip, ipv6=ipv6, err=err)
    sr.loc, sr.provider = loc, provider
    return sr


def test_render_sources_streaming_keeps_pending_rows_untouched(qapp):
    """★ 流式阶段：还没返回的源必须**保持原状**，不得被刷成「✗ 不可用」。

    真实场景：`_on_src_ready` 每回来一个源就调一次 `_render_sources([sr])`
    （final 默认 False）。若把 `s is None` 当成失败，先返回的那个源会把其余
    **尚未返回**的源全部刷成「✗ 不可用」，等它们返回再翻回 ✓ —— 用户看到的是
    「先红后绿」的闪烁与误导（2026-09-21 QA 探针实测复现：仅 IP9 返回时，
    PCOnline / Cloudflare / IPinfo.io 三行全变 '✗ 不可用'）。

    变异验证：把 `if s is None: continue` 改回 `if s is None or not s.ok:` → 本用例失败。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    for blk in page._src_blocks:                       # 初始态：4 行都是 '–'
        assert blk["status"].text() == "–"

    first = page._src_blocks[0]["name"]
    page._render_sources([_src_result(first, ip="1.1.1.1", loc="中国 浙江 宁波")])

    assert page._src_blocks[0]["status"].text() == "✓"
    pending = [b for b in page._src_blocks if b["name"] != first]
    assert pending, "前置：4 源里应还有未返回的"
    for blk in pending:
        assert blk["status"].text() == "–", (
            f"{blk['name']} 尚未返回，不得被刷成失败态（实际 {blk['status'].text()!r}）"
        )
        assert blk["loc"].text() == "", f"{blk['name']} 未返回时不该显示错误文案"


def test_render_sources_final_marks_whole_family_on_disagreement(qapp):
    """★ 收尾阶段（final=True）：本族出现 ≥2 个不同 IP → **全族标 ✗**。

    变异验证：把 final 分支的 `len(fam) <= 1` 判定改成恒 True（永远显示一致）
    → 本用例失败。这条路径此前**完全没有守卫**（2026-09-21 QA 变异 c 存活），
    而实现本身也是错的：老写法「共识为空 → 按一致处理」在分歧时必然触发
    （分歧恰恰让共识为空），结果分歧时每个源都显示 ✓ —— 功能等于没兑现。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    names = [b["name"] for b in page._src_blocks]
    assert len(names) >= 2

    page._render_sources(
        [_src_result(names[0], ip="1.1.1.1"), _src_result(names[1], ip="2.2.2.2")],
        final=True,
    )

    for i in (0, 1):
        assert page._src_blocks[i]["status"].text() == "✗", (
            f"{names[i]} 与另一个源报不同 IP，必须标 ✗"
            f"（实际 {page._src_blocks[i]['status'].text()!r}）"
        )
    assert "分歧" in page._agree_label.text()


def test_render_sources_final_marks_agreement_ok(qapp):
    """★ 收尾阶段（final=True）：本族 IP 唯一 → 全部 ✓，头部给「一致」结论。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    names = [b["name"] for b in page._src_blocks]

    page._render_sources([_src_result(n, ip="183.132.224.185") for n in names], final=True)

    for i in range(len(names)):
        assert page._src_blocks[i]["status"].text() == "✓"
    assert "一致" in page._agree_label.text()


def test_render_sources_tooltip_carries_the_source_own_ip(qapp):
    """★ 每个源的 tooltip 必须带上**它自己看到的 IP** —— 「分歧」结论唯一的信息出口。

    卡面已不显示逐源 IP（上半身大字已承担），若 tooltip 也丢，用户就无法追查
    到底是哪个源在说不同的话。
    """
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    first = page._src_blocks[0]["name"]
    page._render_sources(
        [_src_result(first, ip="1.1.1.1", loc="中国 浙江 宁波", provider="电信")]
    )

    tip = page._src_blocks[0]["status"].toolTip()
    assert "1.1.1.1" in tip, "tooltip 必须含该源看到的 IP"
    assert "中国 浙江 宁波" in tip
    assert page._src_blocks[0]["loc"].toolTip() == tip


def test_refresh_sources_touches_only_source_blocks(qapp, monkeypatch):
    """★ 核心契约「只刷源」：4 个源块被重置，其余字段（IP 卡/站点/网卡/时间/状态栏）全不变。

    变异验证：在 `on_refresh_sources()` 里篡改 `v4_ip` / `v6_ip` / `_site_cells` → 失败。
    """
    from app.constants import NETCHECK_VALUE_DASH
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    page._service.check_all = _stub_check_all(_make_result())
    page._worker()
    before = _snapshot_excluding_sources(page)
    assert before["v4_ip"] == "183.132.224.185", "前置：IPv4 卡已被填上真实 IP"
    assert before["time"].startswith("最后检测"), "前置：时间标签已被填上"
    assert before["v6_loc"] and before["net_hint"]

    # 状态栏 spy：只刷源全程不得推任何消息（尤其不得有 danger 红字）
    emitted: list[tuple[str, str]] = []
    page.status_message.connect(lambda text, level: emitted.append((text, level)))

    fake = _FakePool()
    monkeypatch.setattr(page, "_src_pool", fake)
    page.on_refresh_sources()

    # ---- 其余板块一个字都不能动（全字段比对，源块除外）----
    after = _snapshot_excluding_sources(page)
    assert after == before, f"只刷源却改动了非源字段：{after} != {before}"
    # ---- 只有源行被重置成占位符（状态位「–」+ 空归属地）----
    for blk in page._src_blocks:
        assert blk["status"].text() == "–"
        assert blk["loc"].text() == ""
    assert page._agree_label.text() == ""
    assert page._src_running is True
    assert page._src_refresh_btn.isEnabled() is False
    assert "检测中" in page._src_refresh_btn.text()
    assert len(fake.jobs) == 1

    # ---- 收尾链路（桩掉服务层免联网）同样不得碰状态栏 / 其它板块 ----
    page._service.check_sources = lambda on_source=None: []
    page._sources_worker()
    assert page._src_running is False
    assert _snapshot_excluding_sources(page) == before
    assert emitted == [], f"只刷源不得写状态栏，实际收到：{emitted}"
    assert not any(lv == "danger" for _t, lv in emitted), "源刷新不得写状态栏红字"
    page.shutdown()


def test_on_refresh_blocked_by_sources_refresh_without_side_effects(qapp, monkeypatch):
    """★ `on_refresh()` / `on_refresh_sites()` 入口拦截源刷新（零副作用）。"""
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    pool = _FakePool()
    monkeypatch.setattr(page, "_pool", pool)
    monkeypatch.setattr(page, "_site_pool", _FakePool())
    monkeypatch.setattr(page, "_src_pool", _FakePool())
    emitted: list[tuple[str, str]] = []
    page.status_message.connect(lambda text, level: emitted.append((text, level)))

    # ---- 1) 源刷新进行中 → on_refresh / on_refresh_sites 直接返回，且不碰任何状态 ----
    page.on_refresh_sources()
    assert page._src_running is True
    status_before = page._status_label.text()
    btn_main_before = page._refresh_btn.isEnabled()
    btn_site_before = page._sites_refresh_btn.isEnabled()

    page.on_refresh()
    page.on_refresh_sites()
    assert page._running is False, "源刷新期间不得发起主检测"
    assert page._sites_running is False, "源刷新期间不得发起站点刷新"
    assert len(pool.jobs) == 0, "源刷新期间不得往主线程池提交任务"
    assert page._status_label.text() == status_before
    assert page._refresh_btn.isEnabled() is btn_main_before
    assert page._sites_refresh_btn.isEnabled() is btn_site_before
    assert emitted == [], f"被拦下的调用不得写状态栏：{emitted}"

    # ---- 2) 源刷新结束后也不得"补跑"（证明不是排队等待，而是真的放弃这次）----
    page._on_sources_refresh_done(("ok", []))
    assert page._running is False
    assert page._sites_running is False
    assert len(pool.jobs) == 0, "不得把被拦下的调用排队到源刷新之后补跑"

    # ---- 3) 源刷新彻底结束后，主检测 / 站点刷新可正常发起 ----
    page.on_refresh()
    assert page._running is True
    assert len(pool.jobs) == 1
    page.shutdown()


def test_sources_worker_uses_signal_not_qtimer():
    """`_sources_worker` 必须 `self._sources_refresh_done.emit(...)`，且不得出现 QTimer.singleShot。"""
    import importlib
    import os

    mod = importlib.import_module("app.ui.page_netcheck")
    path = os.path.join(os.path.dirname(mod.__file__), "page_netcheck.py")
    src = open(path, encoding="utf-8").read()

    m = re.search(r"def _sources_worker\(self.*?(?=\n    def )\n", src, re.S)
    assert m, "未找到 _sources_worker 方法"
    body = m.group(0)
    assert "self._sources_refresh_done.emit" in body, "_sources_worker 必须经由 Signal emit 回主线程"
    assert "QTimer.singleShot(" not in body, "_sources_worker 禁止实际调用 QTimer.singleShot"
    # 单源流式上屏必须**复用**已有的 _src_ready 信号（不要另造一个）
    assert "on_source=self._src_ready.emit" in body, "源流式上屏必须复用 _src_ready"


# --------------------------------------------------------------------------- #
# 站点 favicon 异步加载
# --------------------------------------------------------------------------- #
def test_site_cells_have_favicon_icon_label(qapp):
    """E 板块每个站点格的 icon QLabel：objectName 为 `ncSiteIcon`，且 NETCHECK_SITES 含 favicon 键。"""
    from app.constants import NETCHECK_SITES
    from app.netsh_service import NetshService
    from app.ui.page_netcheck import PageNetCheck

    page = PageNetCheck(NetshService())
    assert len(page._site_icons) == len(NETCHECK_SITES) == 8
    for name, lbl in page._site_icons.items():
        assert lbl.objectName() == "ncSiteIcon"
        # 初始为文字占位符（站点名首字），favicon 加载成功后才替换
        assert lbl.text() == name[0]
    for s in NETCHECK_SITES:
        assert "favicon" in s, "站点清单缺少 favicon 键"
        assert s["name"] in page._site_icons
    page.shutdown()
