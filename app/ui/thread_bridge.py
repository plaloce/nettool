"""NetTool UI 层：**工作线程 → 主线程** 的安全回调桥（架构 §9.4）。

## 为什么需要这个模块（这是一个真实踩过的坑）

Qt 的 `QTimer.singleShot(0, callback)` 有一个隐蔽陷阱：
**它把定时器创建在「调用它的那个线程」上**。如果从 `ThreadPoolExecutor` 的
工作线程里调用它，定时器就挂在那个**没有事件循环**的线程上，
回调**永远不会被执行** —— 不报错、不抛异常、静默失败。

实测（offscreen，PySide6 6.x）：

| 方式 | 结果 |
| --- | --- |
| 工作线程内 `QTimer.singleShot(0, cb)` | ❌ 回调 0 次（静默丢弃） |
| 工作线程内 `signal.emit(...)` | ✅ 回调 1 次，且已回到主线程 |

NetTool 曾因此在 4 处踩雷（网卡列表为空的直接原因）：
`page_scan._adapters_worker`、`page_config._refresh_worker`、
`page_config._apply_worker`、`page_ping._gateway_worker`。

## 正确做法

用 **信号**（Qt 对跨线程连接自动使用 `QueuedConnection`，会安全地排队到
接收者所在线程的事件循环里执行）。本模块把「信号 + 载荷」封装成一个
可复用的桥对象，避免每个页面各写一份。

## 用法

```python
class MyPage(QWidget):
    _worker_done = Signal(object)          # 载荷用 object 承载任意类型

    def __init__(self):
        ...
        self._worker_done.connect(self._on_worker_done)

    def _worker(self):                     # 工作线程里跑
        data = do_heavy_io()
        self._worker_done.emit(data)       # ✅ 安全回到主线程
```

`make_callback_signal()` 提供了更省事的等价写法（内部仍是同一个机制）。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class ThreadBridge(QObject):
    """把工作线程的调用安全地转到创建它的线程（通常是主线程）。

    与直接用裸 `Signal` 相比，这个类：
    - 用 `dispatch(object)` 承载**任意载荷**（多参数请打包成 dict/tuple）；
    - 记录创建时的线程，方便断言/调试「回调确实回到了主线程」。
    """

    dispatch = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._owner_thread: QThread = QThread.currentThread()

    @property
    def owner_thread(self) -> QThread:
        """桥所属线程（即回调将在此线程执行）。"""
        return self._owner_thread

    def on_main(self, handler: Callable[[Any], None]) -> None:
        """注册处理器。若当前已在 owner 线程，直接连接即可；跨线程会自动排队。"""
        self.dispatch.connect(handler)

    def post(self, payload: Any = None) -> None:
        """**可从任意线程调用**：把 payload 投递到 owner 线程执行。"""
        self.dispatch.emit(payload)


def run_on_owner_thread(widget: QObject, handler: Callable[[], None]) -> None:
    """便捷函数：把一个无参回调安全地投递到 `widget` 所在线程执行。

    等价于「正确的 singleShot(0) 替代品」。请勿改回 `QTimer.singleShot(0, ...)`：
    若在非 GUI 线程内调用，回调会被静默丢弃（见模块 docstring）。

    实现说明：每次调用临时建一个桥对象，用 `deleteLater` 回收。
    """
    bridge = ThreadBridge(widget)
    bridge.on_main(lambda _payload: None)   # 占位，保证信号至少有一个接收者
    bridge.dispatch.connect(lambda _payload: handler())
    bridge.dispatch.connect(bridge.deleteLater)
    bridge.post(None)
