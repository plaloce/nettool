"""NetTool 业务层：构造 netsh 命令、读写网卡配置、执行配置变更。

要点：
- `build_static_cmds` / `build_dhcp_cmds` 返回 **`list[list[str]]`**（参数列表），
  一律 `shell=False` 执行（架构 8.4 注记 / U6）。
- 子进程输出统一经 `elevation.decode_console_bytes` 解码（GBK/UTF-8 兼容）。
- 提权与否由本模块内部判断：已提权 → 本地执行；未提权 → `run_elevated_netsh`。
"""

from __future__ import annotations

import logging
import re

from app.elevation import (
    ExecResult,
    decode_console_bytes,
    is_admin,
    run_elevated_netsh,
    run_local_netsh,
)
from app.models import AdapterInfo, NetshConfig, Profile
from app.netsh_parser import parse_set_result, parse_show_config
from app.procutil import run_silent

logger = logging.getLogger(__name__)

# netsh 可执行文件名（参数列表首元素）
_NETSH: str = "netsh"

# `netsh interface show interface` 行解析：状态列可能为「已连接 / 已断开 / 已禁用」
_IFACE_HEADER_TOKENS = ("管理状态", "Admin State", "状态", "State")


class NetshService:
    """封装 netsh 的读取与写入。"""

    def __init__(self) -> None:
        self._elevated_session: bool = False

    # ------------------------------------------------------------------ #
    # 会话提权状态（供侧栏徽标联动）
    # ------------------------------------------------------------------ #
    @property
    def elevated_session(self) -> bool:
        """本次会话内是否成功提权过（用于侧栏徽标切换）。"""
        return self._elevated_session

    @property
    def admin_now(self) -> bool:
        """当前进程是否本身已提权。"""
        return is_admin()

    def _mark_elevated(self) -> None:
        self._elevated_session = True

    # ------------------------------------------------------------------ #
    # 读取网卡
    # ------------------------------------------------------------------ #
    def list_adapters(self) -> list[AdapterInfo]:
        """列出系统网卡（含 IP/掩码/网关/DHCP 状态）。

        组合 `netsh interface show interface`（启用/禁用的接口名）与
        `netsh interface ip show config`（各接口地址）。失败返回空列表，不抛异常。
        """
        names = self._list_interface_names()
        adapters: list[AdapterInfo] = []
        for name, is_up in names:
            cfg = self.read_config(name)
            adapters.append(
                AdapterInfo(
                    name=name,
                    description="",
                    ip=cfg.ip,
                    mask=cfg.mask,
                    gateway=cfg.gateway,
                    is_up=is_up,
                    is_dhcp=(cfg.dhcp_enabled is True),
                )
            )
        return adapters

    def _list_interface_names(self) -> list[tuple[str, bool]]:
        """调用 `netsh interface show interface`，返回 [(接口名, 是否已连接/启用)]。

        兼容中英文输出；解析失败返回空列表。
        """
        result: list[tuple[str, bool]] = []
        try:
            proc = run_silent(
                [_NETSH, "interface", "show", "interface"],
                capture_output=True,
                shell=False,
            )
        except OSError as e:
            logger.warning("列出网卡失败：%s", e)
            return result

        text = decode_console_bytes(proc.stdout) + decode_console_bytes(proc.stderr)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # 跳过表头
            if any(tok in stripped for tok in _IFACE_HEADER_TOKENS):
                continue
            # 跳过纯分隔行
            if set(stripped) <= set("-="):
                continue
            # 判定启用状态
            # 注意中文 Windows 的实际用词是「已断开连接」（不是「已断开」），
            # 早期只匹配「已断开」会漏掉 WLAN —— 用子串包含判定更稳。
            is_up = not (
                ("已禁用" in stripped)
                or ("已断开" in stripped)          # 覆盖「已断开」「已断开连接」
                or ("Disabled" in stripped)
                or ("Disconnected" in stripped)
            )
            # 接口名取行内最后一个「非状态」字段：netsh 列顺序为
            # 管理状态 | 状态 | 类型 | 接口名称  （接口名可能含空格）
            # 状态列取值：已连接 / 已断开连接（中文），Connected / Disconnected（英文）。
            # ⚠️ 必须按「长词在前」排列备选，否则 `已断开` 会先匹配掉 `已断开连接`
            #    的头部，把 `连接` 当作接口名（曾经踩过）。
            m = re.search(
                r"(?:已启用|已禁用|Enabled|Disabled)\s+"
                r"(?:已断开连接|已断开|Disconnected|已连接|Connected)\s+"
                r"(?:\S+)\s+(.+)$",
                stripped,
                re.IGNORECASE,
            )
            if m:
                name = m.group(1).strip()
                if name and all(name != n for n, _ in result):
                    result.append((name, is_up))
        return result

    def read_config(self, adapter: str) -> NetshConfig:
        """读取指定网卡的实际配置（供 Q6 比对）。解析失败返回 parsed_ok=False。"""
        try:
            proc = run_silent(
                [_NETSH, "interface", "ip", "show", "config", f"name={adapter}"],
                capture_output=True,
                shell=False,
            )
        except OSError as e:
            logger.warning("读取网卡配置失败：%s", e)
            return NetshConfig(parsed_ok=False)
        text = decode_console_bytes(proc.stdout) + decode_console_bytes(proc.stderr)
        cfg = parse_show_config(text)
        if not cfg.adapter:
            cfg.adapter = adapter
        return cfg

    def read_all_configs(self) -> dict[str, NetshConfig]:
        """读取所有网卡的配置，返回 `{网卡名: NetshConfig}`（供 Q6 批量比对）。"""
        configs: dict[str, NetshConfig] = {}
        for name, _is_up in self._list_interface_names():
            configs[name] = self.read_config(name)
        return configs

    def guess_subnet(self, adapter: str) -> str:
        """根据网卡当前 IP 推断网段前三段（如 `192.168.1`）。无则返回空串。"""
        cfg = self.read_config(adapter)
        if cfg.ip and cfg.ip.count(".") == 3:
            return ".".join(cfg.ip.split(".")[:3])
        return ""

    def guess_local_subnet(self) -> str:
        """不指定网卡时推断「本机当前网段」前三段。

        优先级：
        1. 已连接、有 IP 且**有网关**的网卡（最可能是真实出口，排除 VMware 虚拟网卡）；
        2. 已连接且有 IP 的网卡；
        3. 任一有 IP 的网卡。

        用于「网段留空则自动使用本机当前网段」（减少用户操作）。
        """
        candidates: list[tuple[int, str]] = []
        for name, is_up in self._list_interface_names():
            cfg = self.read_config(name)
            if not cfg.ip or cfg.ip.count(".") != 3:
                continue
            ip = cfg.ip
            # 跳过回环与 APIPA（169.254.x.x 是"没拿到 DHCP"的地址，扫它没意义）
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            subnet = ".".join(ip.split(".")[:3])
            score = 0
            if is_up:
                score += 2
            if cfg.gateway:
                score += 4          # 有网关 = 真实出口，权重最高
            if name.lower().startswith("vmware") or "vethernet" in name.lower():
                score -= 3          # 虚拟网卡降权
            candidates.append((score, subnet))

        if not candidates:
            return ""
        candidates.sort(key=lambda t: t[0], reverse=True)
        return candidates[0][1]

    # ------------------------------------------------------------------ #
    # 构造命令（返回参数列表，shell=False）
    # ------------------------------------------------------------------ #
    @staticmethod
    def build_static_cmds(p: Profile) -> list[list[str]]:
        """构造静态方案的 netsh 参数列表组。

        命令模板（PRD 5.1.5）：
            netsh interface ip set address name="X" static <ip> <mask> <gw>
            netsh interface ip set dns name="X" static <dns1> primary
            netsh interface ip add dns name="X" <dns2> index=2
        """
        cmds: list[list[str]] = [
            [
                _NETSH, "interface", "ip", "set", "address",
                f"name={p.adapter}",
                "static", p.ip, p.mask, p.gateway,
            ]
        ]
        if p.dns_primary:
            cmds.append(
                [
                    _NETSH, "interface", "ip", "set", "dns",
                    f"name={p.adapter}", "static", p.dns_primary, "primary",
                ]
            )
        if p.dns_secondary:
            cmds.append(
                [
                    _NETSH, "interface", "ip", "add", "dns",
                    f"name={p.adapter}", p.dns_secondary, "index=2",
                ]
            )
        return cmds

    @staticmethod
    def build_dhcp_cmds(adapter: str) -> list[list[str]]:
        """构造恢复 DHCP 的 netsh 参数列表组（PRD 5.1.5）。"""
        return [
            [_NETSH, "interface", "ip", "set", "address", f"name={adapter}", "source=dhcp"],
            [_NETSH, "interface", "ip", "set", "dns", f"name={adapter}", "source=dhcp"],
        ]

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def apply(self, p: Profile) -> ExecResult:
        """应用方案（静态或 DHCP），内部决定是否走提权。**同步阻塞，UI 需放工作线程。**"""
        if p.dhcp:
            cmds = self.build_dhcp_cmds(p.adapter)
        else:
            cmds = self.build_static_cmds(p)
        return self.run_cmds(cmds)

    def restore_dhcp(self, adapter: str) -> ExecResult:
        """将网卡恢复为 DHCP（F1-12）。"""
        return self.run_cmds(self.build_dhcp_cmds(adapter))

    def run_cmds(self, cmds: list[list[str]]) -> ExecResult:
        """统一执行入口：已提权走本地，否则走提权子进程。

        - 已提权：`run_local_netsh`（`shell=False`）。
        - 未提权：`run_elevated_netsh`（路径B）。成功后 `_mark_elevated()`。

        Raises: 不抛异常，失败返回 ExecResult(ok=False)。
        """
        try:
            if is_admin():
                result = run_local_netsh(cmds)
            else:
                result = run_elevated_netsh(cmds)
                if result.ok:
                    self._mark_elevated()
            return result
        except Exception as e:  # 业务层兜底，绝不崩溃
            logger.error("执行 netsh 命令异常：%s", e)
            return ExecResult(ok=False, error=f"执行异常：{e}")

    # ------------------------------------------------------------------ #
    # 便捷：单条命令执行 + 结果判定（供子进程以外的场景）
    # ------------------------------------------------------------------ #
    @staticmethod
    def judge(text: str, returncode: int) -> tuple[bool, str]:
        """对 netsh 文本输出做成功/失败判定（转发 netsh_parser）。"""
        return parse_set_result(text, returncode)
