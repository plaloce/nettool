"""P0-2 · `ScanGridWidget` 自绘控件坐标换算单元测试（核心算法）。

重点：`index_at` 与 `_cell_rect` 必须互为逆运算（自绘控件最易错处）。
不依赖真实网络；需要 QApplication（用 conftest 的 `qapp` fixture）。
"""

from __future__ import annotations

from app.constants import GRID_COLS, SCAN_TOTAL_ADDRESSES
from app.ui.scan_grid import ScanGridWidget


# --------------------------------------------------------------------------- #
# 基本映射
# --------------------------------------------------------------------------- #
def test_index_zero_is_top_left(qapp):
    g = ScanGridWidget()
    r = g._cell_rect(0)
    # 左上角第一格的左上角坐标
    assert g.index_at(r.left(), r.top()) == 0


def test_index_last_cell(qapp):
    g = ScanGridWidget()
    last = SCAN_TOTAL_ADDRESSES - 1  # 253
    r = g._cell_rect(last)
    assert g.index_at(r.left(), r.top()) == last


def test_gap_returns_minus_one(qapp):
    """间隙（格子右边 1~GAP 像素）应返回 -1。"""
    g = ScanGridWidget()
    # 第 0 格右上角之后、第 1 格左侧之前的间隙
    r0 = g._cell_rect(0)
    gap_x = r0.right() + 1  # 进入间隙
    assert g.index_at(gap_x, r0.top()) == -1


def test_out_of_bounds_returns_minus_one(qapp):
    g = ScanGridWidget()
    # 超出网格总宽/总高
    assert g.index_at(g._grid_width() + 50, 0) == -1
    assert g.index_at(0, g._grid_height() + 50) == -1


def test_negative_coords_return_minus_one(qapp):
    g = ScanGridWidget()
    assert g.index_at(-1, 0) == -1
    assert g.index_at(0, -1) == -1
    assert g.index_at(-5, -5) == -1


# --------------------------------------------------------------------------- #
# 逆运算一致性（核心）
# --------------------------------------------------------------------------- #
def test_index_at_inverse_of_cell_rect(qapp):
    """对每个 idx，取矩形中心点喂给 index_at 应返回同一个 idx。"""
    g = ScanGridWidget()
    for idx in range(SCAN_TOTAL_ADDRESSES):
        rect = g._cell_rect(idx)
        cx = rect.center().x()
        cy = rect.center().y()
        assert g.index_at(cx, cy) == idx, f"idx={idx} 中心反算失败"


def test_cell_rect_row_col_layout(qapp):
    """格子布局：第 n 列在第 n 列，行号 = idx // COLS（含内边距偏移）。"""
    g = ScanGridWidget()
    for idx in (0, GRID_COLS, GRID_COLS * 2, SCAN_TOTAL_ADDRESSES - 1):
        rect = g._cell_rect(idx)
        col = idx % GRID_COLS
        row = idx // GRID_COLS
        assert rect.left() == g.PAD_X + col * (g.CELL_W + g.GAP)
        assert rect.top() == g.PAD_Y + row * (g.CELL_H + g.GAP)


def test_padding_offset_is_inside_size_hint(qapp):
    """`sizeHint` 必须把 `PAD_X/PAD_Y` 算进去。

    回归守卫：用户报过「网格右侧的格子（16/32/48 列）要往右拉滚动条才看得见」。
    根因是 `sizeHint` 只返回网格几何、漏了内边距，导致最后一列/行落在
    控件可见区之外。若有人把 PAD 从 sizeHint 里去掉，本用例应当失败。
    """
    g = ScanGridWidget()
    sh = g.sizeHint()
    assert sh.width() >= g._grid_width() + 2 * g.PAD_X
    assert sh.height() >= g._grid_height() + 2 * g.PAD_Y

    # 最后一格的右下角必须严格落在控件范围内
    last = g._cell_rect(SCAN_TOTAL_ADDRESSES - 1)
    assert last.right() < sh.width(), "最后一列格子溢出了 sizeHint 宽度"
    assert last.bottom() < sh.height(), "最后一行格子溢出了 sizeHint 高度"


# --------------------------------------------------------------------------- #
# IP <-> 索引
# --------------------------------------------------------------------------- #
def test_index_for_ip(qapp):
    g = ScanGridWidget()
    g.set_prefix("192.168.1")
    assert g._index_for_ip("192.168.1.1") == 0
    assert g._index_for_ip("192.168.1.254") == 253


def test_index_for_ip_wrong_subnet(qapp):
    g = ScanGridWidget()
    g.set_prefix("192.168.1")
    assert g._index_for_ip("192.168.2.1") == -1


def test_index_for_ip_edge_cases(qapp):
    g = ScanGridWidget()
    g.set_prefix("192.168.1")
    assert g._index_for_ip("") == -1
    assert g._index_for_ip("192.168.1") == -1          # 只有三段
    assert g._index_for_ip("192.168.1.0") == -1        # 第 4 段 0 非法
    assert g._index_for_ip("192.168.1.255") == -1      # 第 4 段 255 非法
    assert g._index_for_ip("192.168.1.abc") == -1      # 非数字


def test_set_prefix_rebuilds_cells(qapp):
    g = ScanGridWidget()
    g.set_prefix("10.0.0")
    assert len(g._cells) == SCAN_TOTAL_ADDRESSES
    assert all(c.status == "pending" for c in g._cells)
    assert g._cells[0].ip == "10.0.0.1"
    assert g._cells[253].ip == "10.0.0.254"


def test_cell_at_bounds(qapp):
    g = ScanGridWidget()
    assert g.cell_at(0) is not None
    assert g.cell_at(253) is not None
    assert g.cell_at(254) is None
    assert g.cell_at(-1) is None


# --------------------------------------------------------------------------- #
# PageScan 的事件过滤器必须能扛住「C++ 对象已销毁、Python 引用还在」
# --------------------------------------------------------------------------- #
def test_page_scan_event_filter_survives_deleted_scroll_area(qapp):
    """回归守卫（2026-11-08 CI 事故）。

    现象：GitHub 的 Windows runner 上测试进程堆损坏退出（0xc0000374），
    日志里反复出现
        RuntimeError: libshiboken: Internal C++ object (PySide6.QtWidgets.QScrollArea) already deleted
    根因：`PageScan.eventFilter` 只用 `is not None` 判断 `_grid_scroll`，
    拦不住「Python 包装对象还在、C++ 对象已删」的状态 —— 页面被回收后
    Qt 仍会向旧 viewport 的过滤器派发事件。

    这里用 `shiboken6.delete()` 强制只销毁 C++ 侧，精确复现该状态。

    注意：传给 eventFilter 的 `obj` 用一个**有效**控件（这里用 page 自身）——
    真实场景中 Qt 派发的 obj 一定有效（事件来自它），失效的是 `self._grid_scroll`。
    若把已删对象当 obj 传进去，连 `QWidget.eventFilter` 自己都会抛异常，
    那测的就不是我们要修的那条路径了。
    """
    from PySide6.QtCore import QEvent
    from shiboken6 import delete, isValid

    from app.netsh_service import NetshService
    from app.scanner import ScannerService
    from app.ui.page_scan import PageScan

    page = PageScan(ScannerService(), NetshService())
    scroll = page._grid_scroll
    assert isValid(scroll), "构造后滚动区应有效"

    delete(scroll)                      # 只删 C++ 对象，Python 引用保留
    assert not isValid(scroll), "delete() 后 isValid 必须为 False"

    # 修复前：这一行在访问 self._grid_scroll.viewport() 时抛 RuntimeError，
    # 而异常从 Qt 的 C++ 回调穿出去会造成堆损坏、进程直接崩。
    page.eventFilter(page, QEvent(QEvent.Type.Resize))
    page.eventFilter(page, QEvent(QEvent.Type.Show))
