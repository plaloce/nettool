"""在**真实主窗口**里量两页控制条的可用宽度与需求宽度。

⚠️ **绝不要设 `QT_QPA_PLATFORM=offscreen`**（本脚本刻意不设）：
    offscreen 平台没有 `Microsoft YaHei UI`，会退化成替代字体，把 CJK 文本
    宽高算成真实值的 **1.85 倍**。同一个串实测：

        "IP 或域名, 如 8.8.8.8"     offscreen=221  真实=120
        "▶ 开始 Ping"               offscreen=117  真实=68
        "1000 ms"                  offscreen=91   真实=54

    后果是 offscreen 会报"需求 905 > 可用 822，2 行"，而**真实渲染是 1 行**
    （见 `tools/screenshot.py` 出的图）。据此调宽度只会把控件越调越窄、
    最后把占位符都切掉 —— 这个坑已经踩过一次。
    **判断排版永远以真实截图为准，offscreen 只能用来查"有没有抛异常"。**

为什么必须 `goto_page()` 切页：
    `QStackedWidget` **不会**给隐藏页做 relayout —— 非当前页的 `width()` 是上次
    显示时的旧几何值。不切过去，量到的是过期数字。

用法：
    python tools/measure_mainwindow.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.constants import FONT_FAMILY, FONT_SIZE_PT  # noqa: E402
from app.ui.flow_row import FlowRow  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.styles import build_qss  # noqa: E402


def _dump(win: MainWindow, page_index: int, title: str) -> None:
    win.goto_page(page_index)
    app = QApplication.instance()
    app.processEvents()

    page = win._pages.widget(page_index)  # noqa: SLF001
    flow = page.findChild(FlowRow)
    if flow is None:
        print(f"  {title}: 未找到 FlowRow")
        return

    items = flow._items  # noqa: SLF001
    total = sum(flow._item_width(i) for i in range(len(items)))  # noqa: SLF001
    gaps = flow._h_gap * max(0, len(items) - 1)  # noqa: SLF001
    avail = flow.width()
    print(f"  {title}: FlowRow宽={avail} 内容={total} 间距={gaps} "
          f"需求={total + gaps} 余={avail - total - gaps} 行数={len(flow._rows)}")  # noqa: SLF001
    for i in range(len(items)):
        w = items[i]
        kind = type(w).__name__
        text = w.text() if hasattr(w, "text") else w.currentText()
        print(f"      {kind:<12} w={w.width():>4} item_width={flow._item_width(i):>4} "
              f"minW={w.minimumWidth():>4} maxW={w.maximumWidth():>6} {text!r}")


def main() -> None:
    global app
    app = QApplication(sys.argv)
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))
    app.setStyleSheet(build_qss())

    win = MainWindow()
    for size in ((1100, 720), (1000, 680), (960, 640), (1280, 800)):
        win.resize(*size)
        win.show()
        app.processEvents()
        print(f"\n=== 窗口 {size[0]}x{size[1]} ===")
        _dump(win, 1, "扫描页")
        _dump(win, 2, "Ping页")

    QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
