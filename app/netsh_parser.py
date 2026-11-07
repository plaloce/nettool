"""netsh 输出解析**纯函数**（架构第 7 章，风险3）。

硬性约束：
- **零 Qt 依赖、零 subprocess、零系统调用**（便于 QA 在无管理员权限下做单测）。
- 输入 `str`，输出 dataclass。
- **解析失败绝不抛异常**：返回 `NetshConfig(parsed_ok=False)` / `(False, 文本)`。
- 中英文关键词双保险，见 `constants.KEYWORDS`。
"""

from __future__ import annotations

import re

from app.constants import KEYWORDS, NETSH_ERROR_MAX_LEN, NETSH_ERROR_MARKERS
from app.models import NetshConfig
from app.validators import from_prefix, is_ipv4, is_subnet_mask, to_prefix

# 续行缩进判定（DNS 多行）
_DNS_LINE_IP_RE = re.compile(r"^\s+([\d.]+)\s*$")


def _search(patterns: list[str], text: str) -> re.Match[str] | None:
    """按顺序尝试一组正则，返回首个匹配。"""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m
    return None


def _extract_adapter(text: str) -> str:
    """从输出首行提取接口名（中英文兼容）。"""
    m = _search(KEYWORDS["adapter_cn"], text)
    if m:
        return m.group(1).strip()
    m = _search(KEYWORDS["adapter_en"], text)
    if m:
        return m.group(1).strip()
    return ""


def _extract_dns_servers(text: str, lines: list[str]) -> list[str]:
    """提取 DNS 服务器列表（支持同行多值 + 缩进续行）。"""
    servers: list[str] = []
    dns_idx = -1
    for i, line in enumerate(lines):
        if _search(KEYWORDS["dns"], line):
            dns_idx = i
            break
    if dns_idx < 0:
        return servers

    # 1) 关键词行之后（含同行）同缩进的 IP
    # 关键词行本身：可能形如 "DNS 服务器: 8.8.8.8" 或 "DNS 服务器" 单独成行
    keyword_line = lines[dns_idx]
    for m in re.finditer(r"([\d]{1,3}(?:\.[\d]{1,3}){3})", keyword_line):
        candidate = m.group(1)
        if is_ipv4(candidate):
            servers.append(candidate)

    # 2) 后续缩进续行（遇到非缩进行即停止）
    for line in lines[dns_idx + 1 :]:
        if not line.strip():
            continue
        m = _DNS_LINE_IP_RE.match(line)
        if m:
            candidate = m.group(1)
            if is_ipv4(candidate):
                servers.append(candidate)
        else:
            # 续行区域结束
            break
    return servers


def parse_show_config(text: str) -> NetshConfig:
    """解析 `netsh interface ip show config name="以太网"` 的输出。

    同时兼容中文/英文 Windows；解析失败返回 `NetshConfig(parsed_ok=False)`。
    **永不抛异常**（内部 try/except 兜底）。
    """
    cfg = NetshConfig(parsed_ok=False)
    try:
        if not isinstance(text, str) or not text.strip():
            return cfg

        lines = text.splitlines()
        cfg.adapter = _extract_adapter(text)

        # ---- DHCP 状态 ----
        if _search(KEYWORDS["dhcp_on"], text):
            cfg.dhcp_enabled = True
        elif _search(KEYWORDS["dhcp_off"], text):
            cfg.dhcp_enabled = False

        # ---- IP 地址 ----
        m_ip = _search(KEYWORDS["ip"], text)
        if m_ip and is_ipv4(m_ip.group(1)):
            cfg.ip = m_ip.group(1)

        # ---- 子网前缀 / 掩码 ----
        m_prefix = _search(KEYWORDS["prefix"], text)
        if m_prefix:
            try:
                cfg.prefix_len = int(m_prefix.group(2))
            except (ValueError, IndexError):
                cfg.prefix_len = None
            if cfg.prefix_len is not None and 0 <= cfg.prefix_len <= 32:
                cfg.mask = from_prefix(cfg.prefix_len)

        # 前缀缺失/非法时，尝试直接抓「掩码」字段
        if not cfg.mask:
            m_mask = _search(KEYWORDS["mask"], text)
            if m_mask and is_subnet_mask(m_mask.group(1)):
                cfg.mask = m_mask.group(1)
                if cfg.prefix_len is None:
                    pl = to_prefix(cfg.mask)
                    cfg.prefix_len = pl if pl >= 0 else None

        # ---- 默认网关 ----
        m_gw = _search(KEYWORDS["gateway"], text)
        if m_gw and is_ipv4(m_gw.group(1)):
            cfg.gateway = m_gw.group(1)

        # ---- DNS ----
        cfg.dns_servers = _extract_dns_servers(text, lines)

        # ---- parsed_ok：能拿到 ip 或 dhcp_enabled 之一即视为解析成功 ----
        cfg.parsed_ok = bool(cfg.ip) or (cfg.dhcp_enabled is not None)
        return cfg
    except Exception:
        # 任何意外都降级为「未解析成功」，绝不抛出
        return NetshConfig(parsed_ok=False)


def parse_set_result(text: str, returncode: int) -> tuple[bool, str]:
    """解析 `netsh interface ip set address/dns ...` 的执行结果。

    返回 `(成功?, 用于展示的原始错误文本)`。
    判定：`returncode == 0` 且输出不含任何错误关键词 → 成功。
    """
    try:
        raw = "" if text is None else str(text)
        snippet = raw.strip()[:NETSH_ERROR_MAX_LEN]

        if returncode != 0:
            return False, snippet

        lowered = raw.lower()
        for marker in NETSH_ERROR_MARKERS:
            if marker.lower() in lowered:
                return False, snippet
        return True, snippet
    except Exception:
        return False, ""


# --------------------------------------------------------------------------- #
# 兼容别名（架构附录 B 同时列出 validators.to_prefix / from_prefix 与
# netsh_parser.to_prefix / from_prefix，此处转发到 validators 的同一实现）
# --------------------------------------------------------------------------- #
def to_prefix_len(mask: str) -> int:
    """点分掩码 -> 前缀长度（转发 validators.to_prefix），非法返回 -1。"""
    return to_prefix(mask)


def from_prefix_len(n: int) -> str:
    """前缀长度 -> 点分掩码（转发 validators.from_prefix），非法返回空串。"""
    return from_prefix(n)
