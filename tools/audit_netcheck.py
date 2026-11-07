"""网络检查页（页面四）—— 真实渲染版式审计（只查纵向溢出，不查横向）。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from app.constants import (  # noqa: E402
    FONT_FAMILY,
    FONT_SIZE_PT,
    WINDOW_DEFAULT_H,
    WINDOW_DEFAULT_W,
)
from app.ui.styles import build_qss  # noqa: E402

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    # 同上：必须在构造 MainWindow 之前注入 QSS，否则截图是系统默认白底白字，
    # 卡片背景/字体颜色全部失真，版式审计结论不可信
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))
    app.setStyleSheet(build_qss())

    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(WINDOW_DEFAULT_W, WINDOW_DEFAULT_H)
    win.show()

    print("=" * 74)
    print(f"窗口 {WINDOW_DEFAULT_W}x{WINDOW_DEFAULT_H}  platform={app.platformName()}")
    print("=" * 74)

    pages = win._pages  # noqa: SLF001
    central = win.centralWidget()
    print(f"centralWidget 高 = {central.height()}px   "
          f"状态栏高 = {win.statusBar().height()}px")
    print(f"页面数 = {pages.count()}   侧栏按钮 = {len(win._sidebar._buttons)}")  # noqa: SLF001
    print("-" * 74)

    # 页面可用宽高（QStackedWidget 视口）
    print(f"QStackedWidget 尺寸 = {pages.width()}x{pages.height()}")
    print("-" * 74)

    for idx in range(pages.count()):
        win.goto_page(idx)
        app.processEvents()
        page = pages.widget(idx)
        name = page.__class__.__name__
        ph = page.height()
        print(f"[{idx}] {name:<16} 页高={ph}px  (可用 {pages.height()}px)")

        for sc in page.findChildren(QScrollArea):
            vp = sc.viewport()
            content = sc.widget()
            ch = content.sizeHint().height() if content is not None else 0
            ratio = f"{ch / vp.height():.2f}x" if vp.height() else "n/a"
            flag = "✘ 溢出" if ch > vp.height() else "✔ 装得下"
            print(f"      scroll 视口={vp.height():>4}px  "
                  f"内容hint={ch:>4}px  溢出={ch - vp.height():>+4}px  "
                  f"{ratio}  {flag}")

        # 无滚动区的页面，直接比页面高与栈高
        if not page.findChildren(QScrollArea):
            flag = "✘ 溢出" if page.sizeHint().height() > pages.height() else "✔ 装得下"
            print(f"      无滚动区  内容hint={page.sizeHint().height()}px  {flag}")

        pix = win.grab()
        path = os.path.join(SHOT_DIR, f"audit_{idx}_{name}.png")
        pix.save(path)
        print(f"      截图 -> {path}")

    print("=" * 74)
    QTimer.singleShot(0, app.quit)
    app.exec()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
