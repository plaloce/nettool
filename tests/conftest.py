"""pytest 公共配置：把项目根加入 sys.path，并提供 Qt 应用 fixture。

注意：
- 项目以 `python main.py` 方式运行，故根目录需在 sys.path 上。
- GUI 相关用例统一用 `qapp` fixture，避免各用例各自创建 QApplication 导致崩溃。
"""

from __future__ import annotations

import os
import sys

# 项目根（tests 的上一级）加入导入路径
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import pytest
except Exception:  # pragma: no cover - pytest 一定存在
    pytest = None


if pytest is not None:

    @pytest.fixture(scope="session")
    def qapp():
        """返回全局 QApplication 单例（无头模式下可创建）。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    @pytest.fixture(autouse=True)
    def block_real_network(monkeypatch):
        """默认封禁真实 HTTP —— 测试不得依赖外网。

        为什么需要它（2026-11-08 CI 事故）：
        `PageNetcheck.on_enter()` 会真正发起检测，于是部分 UI 用例隐式访问外网。
        在国内开发机上这些请求几十毫秒就回（或走本机代理），全量 4.7 秒跑完；
        但 GitHub 的海外 runner 访问国内 IP 源会一直等到 socket 超时，
        整轮 job 卡到 20 分钟上限被强杀，日志里只留下一堆 SSL 线程栈。

        这里把 `urllib.request.urlopen` 换成**立即抛 AssertionError** 的桩：
        - 用例自己 `monkeypatch.setattr(urllib.request, "urlopen", ...)` 的照旧生效
          （autouse fixture 先执行，用例体内 patch 后执行，后者覆盖前者）；
        - 忘记 mock 却真的联网的用例会**当场失败并自报用例名**，
          而不是把 CI 拖成 20 分钟的超时;
        - 抛 AssertionError 而不是 URLError，是因为服务层只捕获
          `URLError / timeout / SSLError`，用 AssertionError 才不会被静默吞掉。
        """
        import urllib.request

        def _blocked(url, *args, **kwargs):
            target = getattr(url, "full_url", url)
            raise AssertionError(
                f"测试不得访问真实网络：{target}\n"
                "请在用例内用 monkeypatch 替换 urllib.request.urlopen"
            )

        monkeypatch.setattr(urllib.request, "urlopen", _blocked)
        yield

    @pytest.fixture(autouse=True)
    def cleanup_qt_widgets():
        """每个用例结束后清理它创建的 Qt 顶层控件。

        为什么需要（2026-11-08 CI 事故的根因）：
        UI 测试大量 `PageNetCheck(NetshService())` / `PageScan(...)` 后并不 close，
        页面连同内部线程池一直活到进程结束。等循环 GC 真正回收它们时，
        往往已经跑在**另一个用例**里 —— 于是崩溃点看起来毫无规律
        （实测：在只含 sleep 的 `test_service_streams_in_completion_order` 里退出），
        而日志只留下 `libshiboken: ... already deleted` 或 `0xc0000374` 堆损坏。

        这里统一 close + deleteLater + 派发 DeferredDelete，把"销毁"固定在
        用例边界上，既消除跨用例污染，也让事件过滤器之类的生命周期问题
        在本地就能暴露出来。

        ★ 顺序不能颠倒：必须**先 shutdown(wait=True) 停掉页面的线程池并等它退出**，
        再让控件被回收。反过来做（先 delete 再等线程自己结束）会让工作线程
        回调到已删除的 QWidget 上，轻则 `shiboken: already deleted`，
        重则 access violation —— 这正是 CI 上 `0xc0000374` 的来源。

        ★ 这里刻意**不**主动 close/deleteLater：实测与 pytest 的 `tmp_path`
        清理会互相踩（崩在 `tempfile.func_wrapper`）。控件留给进程退出时回收即可，
        测试进程本来就不需要"省内存"，只要保证没有活线程在往已销毁对象上 emit。
        """
        yield
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:                       # 该用例没碰过 Qt，无需清理
            return
        for widget in list(app.topLevelWidgets()):
            stop = getattr(widget, "shutdown", None)
            if callable(stop):
                try:
                    stop(wait=True)
                except TypeError:             # 兼容不带 wait 参数的实现
                    stop()
                except RuntimeError:          # 已被销毁的包装对象
                    pass
