"""P0-4 · Ping 输出解析（中英文）+ `PingStats` 统计。

全部用固定文本样本，不真的 ping（避免慢/依赖网络）。
`parse_ping_output` / `format_log_line` / `PingStats` 均为纯逻辑。
"""

from __future__ import annotations

from app.models import PingResult, PingStats
from app.ping_service import format_log_line, parse_ping_output


# --------------------------------------------------------------------------- #
# 解析：成功
# --------------------------------------------------------------------------- #
def test_parse_chinese_success():
    text = "来自 192.168.1.1 的回复: 字节=32 时间=2ms TTL=64"
    results = parse_ping_output(text, "192.168.1.1")
    assert len(results) == 1
    r = results[0]
    assert r.ok is True
    assert r.latency_ms == 2
    assert r.ttl == 64


def test_parse_english_success():
    text = "Reply from 192.168.1.1: bytes=32 time=2ms TTL=64"
    results = parse_ping_output(text, "192.168.1.1")
    assert len(results) == 1
    r = results[0]
    assert r.ok is True
    assert r.latency_ms == 2
    assert r.ttl == 64


def test_parse_latency_less_than_marker():
    """`时间<1ms` 形式也应解析。"""
    text = "来自 127.0.0.1 的回复: 字节=32 时间<1ms TTL=128"
    results = parse_ping_output(text, "127.0.0.1")
    assert results[0].ok is True
    assert results[0].latency_ms == 1


def test_parse_multiple_packets():
    text = "\n".join([
        "来自 192.168.1.1 的回复: 字节=32 时间=2ms TTL=64",
        "来自 192.168.1.1 的回复: 字节=32 时间=3ms TTL=64",
        "来自 192.168.1.1 的回复: 字节=32 时间=4ms TTL=64",
        "来自 192.168.1.1 的回复: 字节=32 时间=5ms TTL=64",
    ])
    results = parse_ping_output(text, "192.168.1.1")
    assert len(results) == 4
    assert all(r.ok for r in results)


# --------------------------------------------------------------------------- #
# 解析：超时 / 失败
# --------------------------------------------------------------------------- #
def test_parse_chinese_timeout():
    results = parse_ping_output("请求超时。", "192.168.1.99")
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "timeout"


def test_parse_english_timeout():
    results = parse_ping_output("Request timed out.", "192.168.1.99")
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "timeout"


def test_parse_unresolved_hostname():
    results = parse_ping_output("Ping 请求找不到主机 nonexistent.invalid。", "nonexistent.invalid")
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "unresolved"


def test_parse_unparsable_text_is_failed():
    """无法解析的文本 → 记为 failed，不抛异常。"""
    results = parse_ping_output("some garbage output that means nothing", "x")
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "failed"


def test_parse_empty_text_is_failed():
    results = parse_ping_output("", "x")
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "failed"


# --------------------------------------------------------------------------- #
# format_log_line
# --------------------------------------------------------------------------- #
def test_format_log_line_success():
    r = PingResult(host="8.8.8.8", ok=True, latency_ms=12, ttl=118,
                   timestamp="14:32:05")
    line, level = format_log_line(r)
    assert level == "success"
    assert "8.8.8.8" in line
    assert "12ms" in line
    assert "118" in line
    assert "成功" in line


def test_format_log_line_timeout():
    r = PingResult(host="8.8.8.8", ok=False, error="timeout", timestamp="14:32:05")
    line, level = format_log_line(r)
    assert level == "danger"
    assert "超时" in line


def test_format_log_line_unresolved():
    r = PingResult(host="bad.host", ok=False, error="unresolved", timestamp="14:32:05")
    line, level = format_log_line(r)
    assert level == "danger"
    assert "无法解析" in line


# --------------------------------------------------------------------------- #
# PingStats 统计（含边界：0 接收不除零）
# --------------------------------------------------------------------------- #
def test_stats_all_success():
    s = PingStats()
    for lat in (2, 3, 4, 5):
        s.add(PingResult(host="h", ok=True, latency_ms=lat))
    assert s.sent == 4
    assert s.received == 4
    assert s.loss == 0
    assert s.loss_pct == 0.0
    assert s.avg_ms == 3.5
    assert s.min_ms == 2
    assert s.max_ms == 5


def test_stats_partial_loss():
    s = PingStats()
    s.add(PingResult(host="h", ok=True, latency_ms=10))
    s.add(PingResult(host="h", ok=False, error="timeout"))
    s.add(PingResult(host="h", ok=True, latency_ms=20))
    s.add(PingResult(host="h", ok=False, error="timeout"))
    assert s.sent == 4
    assert s.received == 2
    assert s.loss == 2
    assert s.loss_pct == 50.0
    assert s.avg_ms == 15.0
    assert s.min_ms == 10
    assert s.max_ms == 20


def test_stats_no_reply_no_divide_by_zero():
    """0 接收时平均值不能除零崩溃。"""
    s = PingStats()
    s.add(PingResult(host="h", ok=False, error="timeout"))
    assert s.sent == 1
    assert s.received == 0
    assert s.loss_pct == 100.0
    assert s.avg_ms is None
    assert s.min_ms is None
    assert s.max_ms is None


def test_stats_empty():
    s = PingStats()
    assert s.loss == 0
    assert s.loss_pct == 0.0
    assert s.avg_ms is None


def test_stats_reset():
    s = PingStats()
    s.add(PingResult(host="h", ok=True, latency_ms=5))
    s.reset()
    assert s.sent == 0 and s.received == 0
    assert s.avg_ms is None
