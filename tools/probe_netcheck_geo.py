"""网络检查页 —— 逐控件几何实测（真实数据态，输出到 tools/probe_netcheck_geo.txt）。

用法：
    <venv>\\Scripts\\python.exe tools/probe_netcheck_geo.py     # 需要联网（会真跑一次检测）

★ 为什么需要它（2026-09-21 两轮实拍教训）：
    `tools/audit_netcheck.py` 只看 `page.sizeHint()`，而该 hint 是**空态**算出来的 ——
    · 空态报「装得下 613/695」，真实数据下左栏 IPv6 大字被挤到 234px 宽；
    · 空态第二行彩点是空串，真实数据下彩点行紧贴卡片底边。
  本工具**等真实检测完成后**逐个 dump 卡/标签的 x/y/w/h/hint 与「文本所需宽度 - 实际宽度」，
  并标出越过卡片底边的控件 ⇒ 溢出与静默裁切无处可藏。

★ 必须从 tools/screenshot.py 复制骨架（含 setFont + setStyleSheet）：
  不注入 QSS 时字体度量与 QWidget chrome 全是错的，量出来的结论不可信。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.constants import (  # noqa: E402
    FONT_FAMILY,
    FONT_SIZE_PT,
    WINDOW_DEFAULT_H,
    WINDOW_DEFAULT_W,
)
from app.ui.styles import build_qss  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_netcheck_geo.txt")
BUF: list[str] = []


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))
    app.setStyleSheet(build_qss())

    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(WINDOW_DEFAULT_W, WINDOW_DEFAULT_H)
    win.show()
    win.goto_page(3)
    app.processEvents()

    page = win._page_netcheck  # noqa: SLF001
    state = {"n": 0, "t0": time.monotonic()}

    def dump() -> None:
        BUF.append(f"window {WINDOW_DEFAULT_W}x{WINDOW_DEFAULT_H} platform={app.platformName()}")
        BUF.append(f"central={win.centralWidget().height()} statusBar={win.statusBar().height()}")
        BUF.append(f"stack={win._pages.width()}x{win._pages.height()}")  # noqa: SLF001
        BUF.append(f"page h={page.height()} hint={page.sizeHint().height()} "
                   f"whint={page.sizeHint().width()}")
        for nm in ("_net_card", "_pub_card", "_site_card"):
            w = getattr(page, nm)
            g = w.geometry()
            BUF.append(f"{nm:12} y={g.y():4} h={g.height():4} w={g.width():4} "
                       f"hint={w.sizeHint().height():4} whint={w.sizeHint().width():4}")

        BUF.append("-- 大字 IP（真实数据）--")
        for nm in ("_v4_ip", "_v4_loc", "_v4_isp", "_v6_ip", "_v6_loc", "_v6_isp"):
            w = getattr(page, nm)
            need = w.fontMetrics().horizontalAdvance(w.text())
            BUF.append(f"  {nm:9} x={w.x():4} w={w.width():4} y={w.y():4} h={w.height():3} "
                       f"需={need:4} 溢出={need - w.width():+4} text={w.text()!r}")

        BUF.append("-- 本机网络信息卡（真实数据；`底`= 相对卡片顶部的绝对底边）--")
        net = page._net_card  # noqa: SLF001
        BUF.append(f"  card h={net.height()} (下边距 10 ⇒ 内容底不该超过 {net.height() - 10})")
        for k_lbl, v_lbl in page._net_rows:  # noqa: SLF001
            BUF.append(f"  {k_lbl.text():>6} k.y={k_lbl.y():4} v.y={v_lbl.y():4} "
                       f"v.h={v_lbl.height():3} 底={max(k_lbl.y() + k_lbl.height(), v_lbl.y() + v_lbl.height()):4}")
        hint = page._net_hint  # noqa: SLF001
        BUF.append(f"  hint  y={hint.y():4} h={hint.height():3} 底={hint.y() + hint.height():4} "
                   f"→ 卡底留白={net.height() - (hint.y() + hint.height()):4}px "
                   f"text={hint.text()!r}")

        BUF.append("-- 源块（真实数据）--")
        for blk in page._src_blocks:  # noqa: SLF001
            for key in ("ip", "loc"):
                w = blk[key]
                need = w.fontMetrics().horizontalAdvance(w.text())
                BUF.append(f"  {blk['name']:>11}.{key:<3} x={w.x():4} y={w.y():4} "
                           f"w={w.width():4} h={w.height():3} 需={need:4} "
                           f"溢出={need - w.width():+4} text={w.text()!r}")

        card = page._site_card  # noqa: SLF001
        card_top = card.geometry().top()
        BUF.append(f"-- 站点卡（真实数据）卡 top={card_top} bottom={card.geometry().bottom()} --")
        for grp, tag in page._group_tags.items():  # noqa: SLF001
            g = tag.geometry()
            BUF.append(f"  group {grp} y={g.y():4} h={g.height():3} "
                       f"hint={tag.sizeHint().height()} minh={tag.minimumHeight()} "
                       f"底={card_top + g.y() + g.height()}")
        for name in page._sites_order:  # noqa: SLF001
            cell = page._site_cells[name]  # noqa: SLF001
            icon = page._site_icons[name]  # noqa: SLF001
            for key, w in (("icon", icon), ("lat", cell["lat"]), ("dots", cell["dots"])):
                g = w.geometry()
                BUF.append(f"  {name:>11}.{key:<4} y={g.y():4} h={g.height():3} "
                           f"hint={w.sizeHint().height():3} minh={w.minimumHeight():3} "
                           f"底={card_top + g.y() + g.height():4}"
                           f"{'  ⚠️超出卡底' if card_top + g.y() + g.height() > card.geometry().bottom() else ''}")

        text = "\n".join(BUF)
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(text)

    def poll() -> None:
        state["n"] += 1
        if not page._running and page._result is not None:  # noqa: SLF001
            # 再等 3 拍让布局落定
            for _ in range(5):
                app.processEvents()
            dump()
            app.quit()
            return
        if state["n"] > 400:
            BUF.append("!! 检测超时")
            dump()
            app.quit()
            return
        QTimer.singleShot(100, poll)

    QTimer.singleShot(100, poll)
    app.exec()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
