"""NetTool 数据流层：Ping 封装 + 日志行生成 + 统计累计（页面三）。

- `ping_once` 用系统 `ping.exe`，兼容中英文输出解析（`时间=2ms TTL=64` / `time=2ms TTL=64`）。
- `PingService(QObject)` 带 `_inflight` 计数，防「ping 耗时 > 间隔」时任务堆积。
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from app.elevation import decode_console_bytes
from app.models import PingResult
from app.procutil import run_silent

logger = logging.getLogger(__name__)

# ping 单行结果解析（中英文兼容）
_LATENCY_RE = re.compile(r"(?:时间|time)\s*[=<]\s*(\d+)\s*ms", re.IGNORECASE)
_TTL_RE = re.compile(r"TTL\s*=\s*(\d+)", re.IGNORECASE)
_UNRESOLVED_MARKERS = (
    "找不到主机", "无法解析", "Ping 请求找不到主机",
    "could not find host", "unknown host", "name or service not known",
    "找不到指定的主机",
)
_TIMEOUT_MARKERS = (
    "请求超时", "超时", "timed out", "timeout", "一般故障", "general failure",
)


def _now_ts() -> str:
    """当前本地时间，24 小时制，精确到秒。"HH:MM:SS"."""
    return datetime.now().strftime("%H:%M:%S")


def _is_resolvable_error(text_lower: str) -> bool:
    """输出是否为「域名无法解析」。"""
    return any(m.lower() in text_lower for m in _UNRESOLVED_MARKERS)


def parse_ping_output(text: str, host: str) -> list[PingResult]:
    """解析一次 ping 命令的完整输出，返回每个包的 `PingResult` 列表。

    - 每行含"时间/time"的行 → 成功包；
    - 每行含"超时/timed out"的行 → 超时包；
    - 域名无法解析 → 返回单条 error="unresolved" 的结果。
    """
    ts = _now_ts()
    text_lower = text.lower()

    if _is_resolvable_error(text_lower):
        return [PingResult(host=host, ok=False, error="unresolved", timestamp=ts)]

    results: list[PingResult] = []
    for line in text.splitlines():
        low = line.lower()
        m_lat = _LATENCY_RE.search(line)
        if m_lat:
            latency = int(m_lat.group(1))
            m_ttl = _TTL_RE.search(line)
            ttl = int(m_ttl.group(1)) if m_ttl else None
            results.append(
                PingResult(
                    host=host, ok=True, latency_ms=latency, ttl=ttl,
                    error="", timestamp=ts,
                )
            )
            continue
        if any(m.lower() in low for m in _TIMEOUT_MARKERS):
            results.append(
                PingResult(host=host, ok=False, error="timeout", timestamp=ts)
            )
            continue

    if not results:
        # 无法解析任何行 → 记为失败
        results.append(PingResult(host=host, ok=False, error="failed", timestamp=ts))
    return results


def ping_once(host: str, count: int = 1, timeout_ms: int = 1000) -> list[PingResult]:
    """执行一次（count 个包）ping，返回每个包的结果列表。

    用 `ping -n <count> -w <timeout_ms> <host>`，`shell=False`。
    发生 OSError / 异常时返回单条 error="failed"。
    """
    try:
        proc = run_silent(
            ["ping", "-n", str(count), "-w", str(timeout_ms), str(host)],
            capture_output=True,
            shell=False,
        )
    except OSError as e:
        logger.warning("ping 执行失败：%s", e)
        return [PingResult(host=host, ok=False, error="failed", timestamp=_now_ts())]

    text = decode_console_bytes(proc.stdout) + decode_console_bytes(proc.stderr)
    return parse_ping_output(text, host)


def ping_one_probe(ip: str, timeout_ms: int = 1000) -> tuple[bool, int | None]:
    """扫描用轻量探测：单包 ping，返回 (是否在线, 延迟ms|None)。"""
    results = ping_once(ip, count=1, timeout_ms=timeout_ms)
    for r in results:
        if r.ok:
            return True, r.latency_ms
    return False, None


def format_log_line(r: PingResult) -> tuple[str, str]:
    """把 PingResult 转成日志文本与级别。

    Returns:
        (文本, level)：level ∈ {"success", "danger"}，供 UI 着色。
    """
    from app.constants import LOG_FMT_PING

    if r.ok:
        result_text = "成功"
        latency = f"{r.latency_ms}ms" if r.latency_ms is not None else "--"
        ttl = str(r.ttl) if r.ttl is not None else "--"
        level = "success"
    else:
        if r.error == "unresolved":
            result_text = "无法解析"
        elif r.error == "timeout":
            result_text = "超时"
        else:
            result_text = "失败"
        latency = "--"
        ttl = "--"
        level = "danger"

    line = LOG_FMT_PING.format(
        ts=r.timestamp or _now_ts(),
        host=r.host,
        result=result_text,
        latency=latency,
        ttl=ttl,
    )
    return line, level


class PingService(QObject):
    """Ping 业务服务（QObject + 信号）。

    - `ping_once`：同步执行（供工作线程调用）。
    - `ping_once_async`：把一次 ping 提交到内部线程池，完成后发 `one_result`；
      每次任务进入时 `_inflight += 1`，结束 `-= 1`。调用方（页面三）据此防堆积。
    - `cancel()`：置 `_cancelled=True`，稍后返回的结果被丢弃。
    """

    one_result = Signal(object)   # PingResult
    done = Signal(list)           # list[PingResult]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._inflight: int = 0
        self._cancelled: bool = False
        self._pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=4)

    @property
    def inflight(self) -> int:
        """当前进行中的 ping 任务数。"""
        return self._inflight

    def reset_cancel(self) -> None:
        """开始新一轮 ping 前清除取消标志。"""
        self._cancelled = False

    def ping_once(self, host: str, count: int = 1, timeout_ms: int = 1000) -> list[PingResult]:
        """同步执行一次 ping（工作线程内调用）。"""
        return ping_once(host, count=count, timeout_ms=timeout_ms)

    def ping_once_async(self, host: str, count: int = 1, timeout_ms: int = 1000) -> None:
        """异步执行一次 ping（持续模式每个 tick 调用）。

        结果逐个通过 `one_result` 发回主线程，全部完成后再发 `done`。
        若已取消，则丢弃结果不再发信号。
        """
        if self._cancelled:
            return
        self._inflight += 1
        try:
            self._pool.submit(self._run_task, host, count, timeout_ms)
        except RuntimeError as e:  # 线程池已关闭
            self._inflight = max(self._inflight - 1, 0)
            logger.warning("提交 ping 任务失败：%s", e)

    def _run_task(self, host: str, count: int, timeout_ms: int) -> None:
        """线程池任务体：执行 ping 并通过信号回传。"""
        try:
            results = ping_once(host, count=count, timeout_ms=timeout_ms)
        except Exception as e:  # 绝不因异常中断
            logger.warning("ping 任务异常：%s", e)
            results = [
                PingResult(host=host, ok=False, error="failed", timestamp=_now_ts())
            ]
        finally:
            self._inflight = max(self._inflight - 1, 0)

        if self._cancelled:
            return
        for r in results:
            self.one_result.emit(r)
        self.done.emit(results)

    def cancel(self) -> None:
        """停止持续 ping：置取消标志（在途结果将被丢弃）。"""
        self._cancelled = True

    def shutdown(self, wait: bool = False) -> None:
        """关闭内部线程池（窗口关闭时调用）。"""
        self._cancelled = True
        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._pool.shutdown(wait=wait)
