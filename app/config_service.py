"""NetTool 业务层：`profiles.json` 读写（原子写）、CRUD、导入导出、生效比对计算。

约定（架构 13.3）：业务层绝不 print / 弹框；失败返回结构化结果或抛给调用方自行处理。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Callable

from app.constants import (
    CONFIG_SCHEMA_VERSION,
    PROFILE_DESC_MAX_LEN,
    PROFILE_NAME_MAX_LEN,
    PROFILES_BACKUP_SUFFIX,
)
from app.models import NetshConfig, Profile
from app.paths import profiles_path

logger = logging.getLogger(__name__)


def _now_id() -> str:
    """生成方案 id：`p_` + 时间戳 + 4 位随机，避免同一秒内撞号。"""
    return f"p_{int(time.time())}_{uuid.uuid4().hex[:4]}"


class ConfigService:
    """方案配置的持久化与查询服务。"""

    def __init__(self, path: str | None = None) -> None:
        self._path: str = path or profiles_path()
        self._profiles: list[Profile] = []
        self._last_applied_id: str | None = None

    # ------------------------------------------------------------------ #
    # 读 / 写
    # ------------------------------------------------------------------ #
    def load(self) -> list[Profile]:
        """从磁盘读取方案列表。

        - 文件不存在 → 返回空列表（不报错）。
        - JSON 解析失败 → 备份为 `profiles.json.bak` 并重建空配置。
        """
        self._profiles = []
        self._last_applied_id = None
        if not os.path.exists(self._path):
            return self._profiles
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("配置文件读取失败，将备份并重建：%s", e)
            self._backup_corrupt()
            return self._profiles

        try:
            self._last_applied_id = (
                data.get("active_profile_id") if isinstance(data, dict) else None
            )
            raw_profiles = data.get("profiles", []) if isinstance(data, dict) else []
            self._profiles = [
                Profile.from_dict(item)
                for item in raw_profiles
                if isinstance(item, dict)
            ]
        except Exception as e:  # 容错：结构异常也做备份重建
            logger.warning("配置结构异常，已备份并重建：%s", e)
            self._backup_corrupt()
            self._profiles = []
        return self._profiles

    def _backup_corrupt(self) -> None:
        """把损坏的配置文件备份为 `<路径>.bak`（尽力而为，跟随实例路径）。"""
        try:
            if os.path.exists(self._path):
                os.replace(self._path, self._path + PROFILES_BACKUP_SUFFIX)
        except OSError as e:
            logger.warning("备份损坏配置失败：%s", e)

    def save(self) -> None:
        """原子写：先写 `.tmp` 再 `os.replace`（PRD 6.2 / 架构 T02）。"""
        payload = {
            "version": CONFIG_SCHEMA_VERSION,
            "active_profile_id": self._last_applied_id,
            "profiles": [p.to_dict() for p in self._profiles],
        }
        tmp_path = self._path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.error("保存配置失败：%s", e)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def all(self) -> list[Profile]:
        """返回内存中的全部方案（列表本身可直接遍历）。"""
        return self._profiles

    def get(self, pid: str) -> Profile | None:
        """按 id 查方案，不存在返回 None。"""
        for p in self._profiles:
            if p.id == pid:
                return p
        return None

    def add(self, p: Profile) -> Profile:
        """新增方案；若 id 为空则自动生成，随后持久化。"""
        if not p.id:
            p.id = _now_id()
        self._profiles.append(p)
        self.save()
        return p

    def update(self, p: Profile) -> None:
        """按 id 就地替换方案字段并持久化。"""
        for i, old in enumerate(self._profiles):
            if old.id == p.id:
                self._profiles[i] = p
                self.save()
                return
        # 未找到则视为新增
        self.add(p)

    def remove(self, pid: str) -> None:
        """按 id 删除方案并持久化。"""
        before = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.id != pid]
        if len(self._profiles) != before:
            if self._last_applied_id == pid:
                self._last_applied_id = None
            self.save()

    def name_exists(self, name: str, exclude_id: str = "") -> bool:
        """判断方案名是否已存在（排除指定 id，用于编辑自身时的校验）。"""
        target = (name or "").strip()
        return any(
            p.name.strip() == target and p.id != exclude_id for p in self._profiles
        )

    # ------------------------------------------------------------------ #
    # 校验（供 UI 复用，纯逻辑）
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate(profile: Profile) -> list[str]:
        """校验方案字段，返回错误信息列表（空列表 = 通过）。

        规则见 PRD 5.1.2 / 5.1.4。
        """
        from app.validators import is_ipv4, is_subnet_mask, same_subnet

        errors: list[str] = []
        name = (profile.name or "").strip()
        if not name:
            errors.append("请填写方案名称")
        elif len(name) > PROFILE_NAME_MAX_LEN:
            errors.append(f"方案名称不能超过 {PROFILE_NAME_MAX_LEN} 个字符")

        if not (profile.adapter or "").strip():
            errors.append("请选择网卡")

        if len(profile.description or "") > PROFILE_DESC_MAX_LEN:
            errors.append(f"描述不能超过 {PROFILE_DESC_MAX_LEN} 个字符")

        if not profile.dhcp:
            if not is_ipv4(profile.ip):
                errors.append("IP 格式不正确，请输入如 192.168.1.100")
            if not is_subnet_mask(profile.mask):
                errors.append("子网掩码格式不正确")
            if not is_ipv4(profile.gateway):
                errors.append("默认网关格式不正确")
            elif is_ipv4(profile.ip) and is_subnet_mask(profile.mask):
                if not same_subnet(profile.ip, profile.gateway, profile.mask):
                    errors.append(
                        f"网关 {profile.gateway} 与 IP {profile.ip} 不在同一网段"
                    )
            if profile.dns_primary and not is_ipv4(profile.dns_primary):
                errors.append("主 DNS 格式不正确")
            if profile.dns_secondary and not is_ipv4(profile.dns_secondary):
                errors.append("备 DNS 格式不正确")
            if profile.dns_secondary and not profile.dns_primary:
                errors.append("填写备 DNS 前请先填写主 DNS")
        return errors

    # ------------------------------------------------------------------ #
    # 导入 / 导出
    # ------------------------------------------------------------------ #
    def export_to(self, path: str) -> None:
        """导出全部方案为 JSON（含 schema 版本）。

        Raises:
            OSError: 写文件失败由调用方处理。
        """
        payload = {
            "version": CONFIG_SCHEMA_VERSION,
            "active_profile_id": self._last_applied_id,
            "profiles": [p.to_dict() for p in self._profiles],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def import_from(self, path: str, on_conflict: Callable[[str], str]) -> int:
        """从 JSON 文件导入方案并按名称去重合并。

        Args:
            path: JSON 文件路径。
            on_conflict: 冲突回调，入参为冲突的方案名，返回 "overwrite" 或 "skip"。

        Returns:
            实际新增（或覆盖）的方案数量。

        Raises:
            ValueError: 文件结构非法。
            OSError: 读文件失败。
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise ValueError("导入文件结构非法：缺少 profiles 数组")

        changed = 0
        for item in data["profiles"]:
            if not isinstance(item, dict):
                continue
            incoming = Profile.from_dict(item)
            if not incoming.name.strip():
                continue
            existing = next(
                (p for p in self._profiles if p.name.strip() == incoming.name.strip()),
                None,
            )
            if existing is None:
                incoming.id = _now_id()
                self._profiles.append(incoming)
                changed += 1
            else:
                if on_conflict(incoming.name) == "overwrite":
                    incoming.id = existing.id
                    self.update(incoming)
                    changed += 1
        if changed:
            self.save()
        return changed

    # ------------------------------------------------------------------ #
    # 「上次应用」弱提示（PRD v1.3 C2）
    # ------------------------------------------------------------------ #
    @property
    def last_applied_id(self) -> str | None:
        """上次成功应用的方案 id（仅作卡片弱提示，**不参与「生效中」判定**）。"""
        return self._last_applied_id

    def mark_applied(self, pid: str | None) -> None:
        """记录「上次成功应用」的方案 id 并持久化。"""
        self._last_applied_id = pid
        self.save()

    # ------------------------------------------------------------------ #
    # Q6 自动比对（PRD 5.1.6，v1.3 明确不使用 active_profile_id）
    # ------------------------------------------------------------------ #
    def compute_active_profile_ids(self, configs: dict[str, NetshConfig]) -> None:
        """**就地**更新每个 Profile 的 `active` / `active_conflict`。

        configs: `{网卡名: NetshConfig}`。解析失败（parsed_ok=False）的网卡不参与判定，
        该网卡下的方案不被打标记（静默降级）。

        规则（PRD 5.1.6）：
        - 网卡名必须完全一致；
        - `dhcp=True` 方案：只要系统该网卡 DHCP 已启用即生效，不比对地址；
        - `dhcp=False` 方案：IP、掩码、网关、主 DNS 四项全员一致才算生效；
        - 备 DNS：只要出现在系统 DNS 列表中即算匹配（不要求数量/顺序完全一致）；
        - 同一网卡多方案同时匹配 → 全部标记 + `active_conflict=True`。
        """
        # 先全部清零
        for p in self._profiles:
            p.active = False
            p.active_conflict = False

        # 按网卡分组统计匹配数，用于判断 conflict
        matched_by_adapter: dict[str, list[Profile]] = {}

        for p in self._profiles:
            cfg = configs.get(p.adapter)
            if cfg is None or not cfg.parsed_ok:
                continue
            if self._is_profile_active(p, cfg):
                p.active = True
                matched_by_adapter.setdefault(p.adapter, []).append(p)

        # 同一网卡多个方案同时匹配 → 全标 + 警告
        for _adapter, matched in matched_by_adapter.items():
            if len(matched) > 1:
                for p in matched:
                    p.active_conflict = True

    @staticmethod
    def _is_profile_active(p: Profile, cfg: NetshConfig) -> bool:
        """单个方案是否与网卡实际配置匹配（PRD 5.1.6 ②）。"""
        # DHCP 方案：系统 DHCP 已启用即算生效
        if p.dhcp:
            return cfg.dhcp_enabled is True

        # 静态方案：系统必须处于静态（DHCP 未启用）
        if cfg.dhcp_enabled is not False:
            return False

        # IP / 掩码 / 网关 / 主 DNS 四项一致
        if (p.ip or "").strip() != (cfg.ip or "").strip():
            return False
        if (p.mask or "").strip() != (cfg.mask or "").strip():
            return False
        if (p.gateway or "").strip() != (cfg.gateway or "").strip():
            return False

        dns_pri = (p.dns_primary or "").strip()
        if dns_pri:
            if not cfg.dns_servers or cfg.dns_servers[0] != dns_pri:
                return False
        else:
            # 方案未填主 DNS：要求系统也无 DNS（避免误标）
            if cfg.dns_servers:
                return False

        # 备 DNS：只要出现在系统 DNS 列表中即匹配
        dns_sec = (p.dns_secondary or "").strip()
        if dns_sec and dns_sec not in cfg.dns_servers:
            return False

        return True
