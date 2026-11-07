"""数据层：全部 dataclass 定义（架构第 3 章）。

- 落盘字段与运行时字段严格区分：`Profile.active` / `active_conflict` **不写盘**。
- 所有 `from_dict` 容错解析：缺字段用默认值，类型不符时强制转换或回退默认。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Any

# 仅落盘的字段集合（Profile.to_dict 使用）
_PROFILE_PERSIST_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "adapter",
    "dhcp",
    "ip",
    "mask",
    "gateway",
    "dns_primary",
    "dns_secondary",
    "description",
)


@dataclass
class AdapterInfo:
    """系统网卡信息（用于下拉与网段推断）。"""

    name: str
    description: str = ""
    ip: str = ""
    mask: str = ""
    gateway: str = ""
    is_up: bool = True
    is_dhcp: bool = False

    def display(self) -> str:
        """用于下拉展示的文案，如 `以太网 (192.168.1.100)`。"""
        if self.ip:
            return f"{self.name} ({self.ip})"
        if not self.is_up:
            return f"{self.name} (已禁用)"
        return self.name


@dataclass
class Profile:
    """一个网络配置方案（PRD 5.1.2 / 7.1）。"""

    id: str
    name: str
    adapter: str
    dhcp: bool = False
    ip: str = ""
    mask: str = ""
    gateway: str = ""
    dns_primary: str = ""
    dns_secondary: str = ""
    description: str = ""

    # ---- 运行时字段（不落盘，由自动比对填充）----
    active: bool = False           # Q6：是否「生效中」
    active_conflict: bool = False  # 同网卡多方案同时匹配 → 警告色

    def to_dict(self) -> dict[str, Any]:
        """只序列化落盘字段（不含 active / active_conflict）。"""
        full = asdict(self)
        return {k: full[k] for k in _PROFILE_PERSIST_FIELDS}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Profile":
        """容错解析：缺字段用默认值，类型不符时回退默认。"""
        d = d if isinstance(d, dict) else {}

        def _s(key: str, default: str = "") -> str:
            v = d.get(key, default)
            return v if isinstance(v, str) else ("" if v is None else str(v))

        dhcp_raw = d.get("dhcp", False)
        dhcp = bool(dhcp_raw) if isinstance(dhcp_raw, (bool, int, str)) else False
        if isinstance(dhcp_raw, str):
            dhcp = dhcp_raw.strip().lower() in ("1", "true", "yes", "是")

        return Profile(
            id=_s("id"),
            name=_s("name"),
            adapter=_s("adapter"),
            dhcp=dhcp,
            ip=_s("ip"),
            mask=_s("mask"),
            gateway=_s("gateway"),
            dns_primary=_s("dns_primary"),
            dns_secondary=_s("dns_secondary"),
            description=_s("description"),
        )


@dataclass
class NetshConfig:
    """`netsh interface ip show config` 的解析结果（Q6 比对数据源）。"""

    adapter: str = ""                       # 解析到的接口名
    dhcp_enabled: bool | None = None        # None = 未解析出
    ip: str = ""
    mask: str = ""                          # 已从「子网前缀 /24」换算为点分掩码
    prefix_len: int | None = None
    gateway: str = ""
    dns_servers: list[str] = field(default_factory=list)
    parsed_ok: bool = False                 # 是否整体解析成功


@dataclass
class ScanResult:
    """单个地址的扫描结果（PRD 7.3）。"""

    ip: str
    status: Literal["online", "offline", "pending"] = "pending"
    latency_ms: int | None = None
    checked_at: str = ""


@dataclass
class ScanStats:
    """扫描汇总统计（页面二统计条）。"""

    total: int = 254
    scanned: int = 0
    online: int = 0
    offline: int = 0
    elapsed_s: float = 0.0
    running: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PingResult:
    """单次 ping 的一个包结果。"""

    host: str
    ok: bool
    latency_ms: int | None = None
    ttl: int | None = None
    error: str = ""                # "timeout" | "unresolved" | "failed" | ""
    timestamp: str = ""            # "14:32:05"


@dataclass
class PingStats:
    """ping 统计累加器（页面三统计条）。"""

    sent: int = 0
    received: int = 0
    _latencies: list[int] = field(default_factory=list)

    def add(self, r: "PingResult") -> None:
        """累加一个 ping 结果（无论成败都计入 sent）。"""
        self.sent += 1
        if r.ok:
            self.received += 1
            if r.latency_ms is not None:
                self._latencies.append(int(r.latency_ms))

    def reset(self) -> None:
        """清空累计。"""
        self.sent = 0
        self.received = 0
        self._latencies.clear()

    @property
    def loss(self) -> int:
        """丢失包数。"""
        return max(self.sent - self.received, 0)

    @property
    def loss_pct(self) -> float:
        """丢包率（百分比，保留 1 位）。"""
        if self.sent <= 0:
            return 0.0
        return round(self.loss * 100.0 / self.sent, 1)

    @property
    def avg_ms(self) -> float | None:
        """平均延迟；无成功样本返回 None。"""
        if not self._latencies:
            return None
        return round(sum(self._latencies) / len(self._latencies), 1)

    @property
    def min_ms(self) -> int | None:
        """最小延迟。"""
        return min(self._latencies) if self._latencies else None

    @property
    def max_ms(self) -> int | None:
        """最大延迟。"""
        return max(self._latencies) if self._latencies else None
