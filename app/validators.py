"""IP / 掩码 / 网段 / 域名的正则与语义校验（**纯函数，零副作用**）。

约定：所有校验函数对非法输入**只返回 False / -1，绝不抛异常**。
"""

from __future__ import annotations

import re

# IPv4 白名单正则（仅数字与点，严格遵守 0–255）
_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)

# 网段前三段（"192.168.1"）
_SUBNET_PREFIX_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){2}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)

# 主机名 / 域名（RFC1123 简化版）
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$"
)


def is_ipv4(s: str | None) -> bool:
    """是否为合法 IPv4 点分十进制字符串。"""
    if not isinstance(s, str) or not s:
        return False
    return bool(_IPV4_RE.match(s.strip()))


def is_valid_subnet_prefix(s: str | None) -> bool:
    """是否为合法网段前三段，如 "192.168.1"（不含第 4 段）。"""
    if not isinstance(s, str) or not s:
        return False
    return bool(_SUBNET_PREFIX_RE.match(s.strip()))


def is_subnet_mask(s: str | None) -> bool:
    """校验子网掩码：必须是合法 IPv4，且为连续 1 后接连续 0 的形式。

    例如 255.255.255.0 合法；255.0.255.0 / 255.255.0.255 非法。
    """
    if not is_ipv4(s):
        return False
    assert isinstance(s, str)
    octets = [int(x) for x in s.strip().split(".")]
    binary = "".join(f"{o:08b}" for o in octets)
    # 连续 1 后连续 0：不允许出现 "01" 子串（即 0 之后又出现 1）
    if "01" in binary:
        return False
    # 全 0（0.0.0.0）不是合法掩码
    if binary.count("1") == 0:
        return False
    return True


def is_hostname(s: str | None) -> bool:
    """是否为合法主机名 / 域名（也接受直接是 IP 的情况）。"""
    if not isinstance(s, str) or not s:
        return False
    s = s.strip()
    if is_ipv4(s):
        return True
    if len(s) > 253:
        return False
    return bool(_HOSTNAME_RE.match(s))


def to_prefix(mask: str | None) -> int:
    """点分掩码 -> 前缀长度。如 `255.255.255.0` -> 24；非法返回 -1。"""
    if not is_subnet_mask(mask):
        return -1
    assert isinstance(mask, str)
    return sum(f"{int(o):08b}".count("1") for o in mask.strip().split("."))


def from_prefix(n: int | None) -> str:
    """前缀长度 -> 点分掩码。如 24 -> `255.255.255.0`；非法返回空串。"""
    if not isinstance(n, int) or n < 0 or n > 32:
        return ""
    bits = ("1" * n).ljust(32, "0")
    return ".".join(str(int(bits[i : i + 8], 2)) for i in range(0, 32, 8))


def _ip_to_int(ip: str) -> int:
    """IPv4 字符串 -> 32 位整数（调用前需保证合法）。"""
    parts = [int(x) for x in ip.strip().split(".")]
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def same_subnet(ip: str | None, gw: str | None, mask: str | None) -> bool:
    """判断 `ip` 与 `gw` 在给定掩码下是否处于同一网段（整数与运算实现）。

    任一参数非法返回 False。掩码为 0 前缀（网络位为 0）时，同网段判定仅以
    掩码网络位为准（此时任何两地址都同网段——由调用方语义决定是否允许）。
    """
    if not (is_ipv4(ip) and is_ipv4(gw) and is_subnet_mask(mask)):
        return False
    assert isinstance(ip, str) and isinstance(gw, str) and isinstance(mask, str)
    m = _ip_to_int(mask)
    return (_ip_to_int(ip) & m) == (_ip_to_int(gw) & m)
