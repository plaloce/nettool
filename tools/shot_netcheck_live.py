"""网络检查页 —— 真实数据端到端渲染验证（等检测完成后截图）。

用法：python tools/shot_netcheck_live.py
"""

from __future__ import annotations

import os
import sys

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

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")

# favicon 异步加载的等待参数（poll 间隔 100ms → 1 次 = 0.1s）
# ⚠️ 保底 8s 的理由：候选链每级 timeout=2.0s、每站点最多 2 级，最坏约 4s+，
#    再留一倍余量；上限 25s 防网络异常时脚本挂死。
FAV_MIN_WAIT_N: int = 80
FAV_MAX_WAIT_N: int = 250


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    # 必须在构造 MainWindow 之前注入：否则截图是系统默认白底白字，
    # 会让人误判成"卡片没渲染 / 大字不可见"（真机上有 main.py 注入，不会白底）
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))
    app.setStyleSheet(build_qss())

    import time

    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(WINDOW_DEFAULT_W, WINDOW_DEFAULT_H)
    win.show()

    # 切到网络检查页 → 触发 on_enter → 自动检测
    win.goto_page(3)
    app.processEvents()

    page = win._page_netcheck  # noqa: SLF001
    state = {"n": 0, "t0": time.monotonic(), "first": None,
             "phase": "detect", "fav_last": -1, "fav_stable": 0, "fav_done_n": 0}

    def poll() -> None:
        state["n"] += 1
        # ★ 流式打点：第一个结果上屏的时刻（用户要的就是「不等别人」）
        if state["first"] is None and (
            page._site_status or page._v4_ip.text() not in ("--", "")  # noqa: SLF001
        ):
            state["first"] = time.monotonic() - state["t0"]
            print(f"[流式] 首个结果上屏：+{state['first']:.2f}s"
                  f"（站点已回 {sum(1 for _ in page._site_status)} 个）")
        # ⚠️ 必须带 phase 判断：主检测完成后 `_running` 一直是 False、`_result` 一直非 None，
        #    不判断就会在「等 favicon」阶段每 100ms 把上面整段重打印一遍。
        if state["phase"] == "detect" and not page._running and page._result is not None:  # noqa: SLF001
            r = page._result  # noqa: SLF001
            print("=" * 74)
            print(f"[流式] 全部完成：+{time.monotonic() - state['t0']:.2f}s"
                  f"（首屏提前了 {time.monotonic() - state['t0'] - (state['first'] or 0):.2f}s）")
            print(f"检测完成，耗时 {r.elapsed_s}s")
            print(f"  IPv4 : {r.ipv4.ip}  |  {r.ipv4.location}  |  {r.ipv4.provider}")
            print(f"         ASN={r.ipv4.asn}  来源={r.ipv4.source}")
            print(f"  IPv6 : {r.ipv6.ip}  |  {r.ipv6.location}  来源={r.ipv6.source}")
            print(f"  v4 集合={r.v4_ips}")
            print(f"  v6 集合={r.v6_ips}")
            print(f"  一致={r.agree}  成功源数={r.agree_count}/{len(r.sources)}")
            print(f"  站点可达 {r.reachable_count}/{len(r.sites)}")
            for s in r.sites:
                ms = f"{s.latency_ms}ms" if s.latency_ms is not None else "不可达"
                print(f"    {s.group} {s.name:<12} {ms}")
            print(f"  网卡数={len(r.adapters)}")
            print("=" * 74)

            # 读回 UI 上真实显示的文本（验证渲染链路，不只是数据层）
            print("\n【UI 实际显示】")
            print(f"  时间标签   : {page._time_label.text()}")
            print(f"  行内状态   : {page._status_label.text()}")
            print(f"  IPv4 大字  : {page._v4_ip.text()}")
            print(f"  IPv4 归属  : {page._v4_loc.text()}")
            print(f"  IPv4 运营商: {page._v4_isp.text()}")
            print(f"  IPv6 大字  : {page._v6_ip.text()}")
            print(f"  IPv6 归属  : {page._v6_loc.text()}")
            print(f"  一致性结论 : {page._agree_label.text()}")
            for blk in page._src_blocks:  # noqa: SLF001
                # ★ 2026-09-21 B/C 合并：源区改成「一行」，键名 ip → status；
                #   IP 改从 tooltip 取（卡面只留状态位 + 归属地）。
                print(f"    {blk['name']:<12} [{blk['status'].text()}] {blk['loc'].text()}"
                      f"   tip={blk['status'].toolTip()}")
            for (k, v) in page._net_rows:  # noqa: SLF001
                print(f"    {k.text():<8} {v.text()}")
            for name in page._sites_order:  # noqa: SLF001
                cell = page._site_cells[name]  # noqa: SLF001
                print(f"    {name:<12} {cell['lat'].text():<10} 彩点={cell['dots'].text()}")
            print(f"  复制按钮可用: {page._copy_btn.isEnabled()}")
            # ★ favicon 统计此刻通常**还没落定**（它走独立线程池，不在主检测链路里），
            #   这里只记快照，最终结论在下方「等 favicon 落定」阶段重算。
            got = [n for n, l in page._site_icons.items() if not l.pixmap().isNull()]
            print(f"  favicon 已加载 {len(got)}/{len(page._site_icons)} 个（检测完成瞬间）：{got}")
            print("  → 进入「等 favicon 落定」阶段")
            state["phase"] = "favicon"
            state["fav_done_n"] = state["n"]
            QTimer.singleShot(100, poll)
            return

        # ---- 阶段二：主检测已完成，等 favicon 异步加载稳定后再截图 ----
        if state["phase"] == "favicon":
            got = [n for n, l in page._site_icons.items() if not l.pixmap().isNull()]
            if len(got) == state["fav_last"]:
                state["fav_stable"] += 1
            else:
                state["fav_last"] = len(got)
                state["fav_stable"] = 0
            waited_n = state["n"] - state["fav_done_n"]
            # ⚠️ 为什么要有 floored wait（别只信"计数不变"）：
            #    候选链每级 timeout=2.0s、每站点最多 2 级 → 最坏 ~4s+ 才出结果。
            #    若只用「计数不变」判定，请求还在飞时计数就已经不变了，会提前收图
            #    （2026-09-21 实测：1.5s 就判稳定，GitHub 被误报成"加载失败"）。
            #    故这里叠加一个 **8s 保底等待**，再要求连续 15 次（≈1.5s）计数不变。
            settled = state["fav_stable"] >= 15 and waited_n >= FAV_MIN_WAIT_N
            if settled or waited_n > FAV_MAX_WAIT_N:
                missing = [n for n in page._site_icons if page._site_icons[n].pixmap().isNull()]
                print(f"\n【favicon 最终落图】{len(got)}/{len(page._site_icons)}"
                      f"  等待 {waited_n * 0.1:.1f}s")
                print(f"  已落图  : {got}")
                print(f"  仍占位符: {missing}")
                path = os.path.join(SHOT_DIR, "netcheck_live.png")
                win.grab().save(path)
                print(f"\n截图 -> {path}")
                app.quit()
                return
            QTimer.singleShot(100, poll)
            return

        if state["n"] > 400:            # 40s 上限（主检测阶段）
            print("!! 超时：检测未在 40s 内完成")
            win.grab().save(os.path.join(SHOT_DIR, "netcheck_timeout.png"))
            app.quit()
            return
        QTimer.singleShot(100, poll)

    QTimer.singleShot(100, poll)
    app.exec()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
