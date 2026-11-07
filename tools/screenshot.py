"""渲染主窗口三页截图到 tools/shots/ 供人工核对（不用 offscreen 字形方块判断排版）。

用法：
    python tools/screenshot.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.constants import FONT_FAMILY, FONT_SIZE_PT  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.styles import build_qss  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))
    app.setStyleSheet(build_qss())
    os.makedirs(OUT, exist_ok=True)

    win = MainWindow()
    win.resize(1100, 720)
    win.show()

    names = {0: "config", 1: "scan", 2: "ping"}
    for idx, name in names.items():
        win.goto_page(idx)
        app.processEvents()
        QTimer.singleShot(0, lambda: None)
        app.processEvents()
        path = os.path.join(OUT, f"{name}-1100x720.png")
        win.grab().save(path)
        print("saved", path)

    # 只看两页控制条区域（裁切），便于放大观察是否单行
    for idx, name in ((1, "scan"), (2, "ping")):
        win.goto_page(idx)
        app.processEvents()
        page = win._pages.widget(idx)  # noqa: SLF001
        pix = page.grab()
        path = os.path.join(OUT, f"{name}-page.png")
        pix.save(path)
        print("saved", path)

    QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
