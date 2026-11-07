"""NetTool 数据流层：线程池并发 ping 扫描 + 100ms 节流合批（风险2）。

设计（架构 5.5 / 9）：
- 254 个任务完成时**不**逐个 emit，而是写入 `self._buffer`（`threading.Lock` 保护）。
- 内部 `QTimer(100ms)` 在**主线程**周期性取走 buffer，发一次 `batch_ready`。
- `stop()` 置 `_stopping` + `shutdown(cancel_futures=True)`；已完成结果保留。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from app.constants import (
    SCAN_FLUSH_INTERVAL_MS,
    SCAN_TOTAL_ADDRESSES,
)
from app.models import ScanResult, ScanStats
from app.ping_service import ping_one_probe


def _now_iso() -> str:
    """ISO 风格时间戳（精确到秒），如 2026-09-19T14:32:05。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class ScannerService(QObject):
    """局域网扫描服务（QObject + 线程池 + 节流合批）。"""

    batch_ready = Signal(list)     # list[ScanResult]
    stats_changed = Signal(object)  # ScanStats
    finished = Signal(object)      # ScanStats

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._buffer: list[ScanResult] = []
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._stopping: bool = False
        self._running: bool = False
        self._stats: ScanStats = ScanStats(total=SCAN_TOTAL_ADDRESSES)
        self._start_time: float = 0.0
        self._pending: int = 0            # 尚未返回的任务数
        self._pending_lock = threading.Lock()

        # 合批定时器（主线程）
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(SCAN_FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush)

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        """是否正在扫描。"""
        return self._running

    def start(self, subnet: str, concurrency: int, timeout_ms: int) -> None:
        """开始扫描 `subnet`（前三段）下的 1..254 共 254 个地址。

        Args:
            subnet: 网段前三段，如 "192.168.1"。
            concurrency: 线程池最大工作线程数。
            timeout_ms: 单地址 ping 超时（毫秒）。
        """
        if self._running:
            return

        prefix = (subnet or "").strip()
        self._stopping = False
        self._running = True
        self._buffer.clear()
        self._stats = ScanStats(total=SCAN_TOTAL_ADDRESSES, running=True)
        self._start_time = time.monotonic()
        self.stats_changed.emit(self._stats)

        max_workers = max(1, int(concurrency))
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan")
        self._pending = SCAN_TOTAL_ADDRESSES

        for idx in range(SCAN_TOTAL_ADDRESSES):
            ip = f"{prefix}.{idx + 1}"
            self._executor.submit(self._probe, ip, timeout_ms)

        self._flush_timer.start()

    def stop(self) -> None:
        """停止扫描：置标志 + 取消未开始任务；已完成结果保留。"""
        if not self._running:
            return
        self._stopping = True
        self._flush()                     # 先冲刷已完成结果
        self._flush_timer.stop()
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._executor.shutdown(wait=False)
            self._executor = None
        self._running = False
        self._stats.running = False
        self._stats.elapsed_s = round(time.monotonic() - self._start_time, 1)
        self.stats_changed.emit(self._stats)
        self.finished.emit(self._stats)

    def shutdown(self, wait: bool = False) -> None:
        """关闭服务（窗口关闭时调用），确保无残留线程。"""
        self._stopping = True
        self._flush_timer.stop()
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                self._executor.shutdown(wait=wait)
            self._executor = None
        self._running = False

    # ------------------------------------------------------------------ #
    # 工作线程
    # ------------------------------------------------------------------ #
    def _probe(self, ip: str, timeout_ms: int) -> None:
        """单个地址探测（工作线程）；结果写入 buffer。"""
        if self._stopping:
            self._dec_pending()
            return
        try:
            online, latency = ping_one_probe(ip, timeout_ms)
        except Exception:                 # 任何异常都记为离线，绝不抛出
            online, latency = False, None

        result = ScanResult(
            ip=ip,
            status="online" if online else "offline",
            latency_ms=latency if online else None,
            checked_at=_now_iso(),
        )
        if not self._stopping:
            with self._lock:
                self._buffer.append(result)
        self._dec_pending()

    def _dec_pending(self) -> None:
        with self._pending_lock:
            self._pending = max(self._pending - 1, 0)

    # ------------------------------------------------------------------ #
    # 主线程：合批刷新
    # ------------------------------------------------------------------ #
    def _flush(self) -> None:
        """取走 buffer 并发一次 `batch_ready`；必要时发 `stats_changed` / `finished`。"""
        with self._lock:
            if not self._buffer:
                drained: list[ScanResult] = []
            else:
                drained = self._buffer
                self._buffer = []

        if drained:
            for r in drained:
                self._stats.scanned += 1
                if r.status == "online":
                    self._stats.online += 1
                elif r.status == "offline":
                    self._stats.offline += 1
            self._stats.elapsed_s = round(time.monotonic() - self._start_time, 1)
            self.batch_ready.emit(drained)
            self.stats_changed.emit(self._stats)

        # 全部任务返回且 buffer 已空 → 正常结束
        with self._pending_lock:
            pending = self._pending
        if self._running and not self._stopping and pending == 0 and not self._buffer:
            self._flush_timer.stop()
            if self._executor is not None:
                try:
                    self._executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    self._executor.shutdown(wait=False)
                self._executor = None
            self._running = False
            self._stats.running = False
            self._stats.elapsed_s = round(time.monotonic() - self._start_time, 1)
            self.stats_changed.emit(self._stats)
            self.finished.emit(self._stats)
