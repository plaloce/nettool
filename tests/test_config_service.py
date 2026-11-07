"""P0-3 · `ConfigService` CRUD / 原子写 / 损坏恢复 / 生效比对（PRD 5.1.6）。

全部使用临时目录，避免污染真实 %APPDATA%\\NetTool。
"""

from __future__ import annotations

import json
import os

import pytest

from app.config_service import ConfigService
from app.models import NetshConfig, Profile


@pytest.fixture()
def svc(tmp_path):
    """每个用例独立的 ConfigService（独立临时 profiles.json）。"""
    path = os.path.join(str(tmp_path), "profiles.json")
    return ConfigService(path=path)


def _mk(name="A", adapter="以太网", **kw) -> Profile:
    return Profile(id="", name=name, adapter=adapter, **kw)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def test_add_and_get(svc):
    p = svc.add(_mk(name="方案1"))
    assert p.id  # 自动生成 id
    assert svc.get(p.id) is p
    assert len(svc.all()) == 1


def test_update_in_place(svc):
    p = svc.add(_mk(name="方案1"))
    p.name = "方案1改"
    svc.update(p)
    assert svc.get(p.id).name == "方案1改"
    assert len(svc.all()) == 1


def test_update_missing_becomes_add(svc):
    p = Profile(id="ghost", name="X", adapter="以太网")
    svc.update(p)
    assert svc.get("ghost") is not None


def test_remove(svc):
    p = svc.add(_mk(name="方案1"))
    svc.remove(p.id)
    assert svc.get(p.id) is None
    assert svc.all() == []


def test_remove_clears_last_applied(svc):
    p = svc.add(_mk(name="方案1"))
    svc.mark_applied(p.id)
    assert svc.last_applied_id == p.id
    svc.remove(p.id)
    assert svc.last_applied_id is None


def test_name_exists(svc):
    p = svc.add(_mk(name="方案1"))
    assert svc.name_exists("方案1") is True
    assert svc.name_exists("方案1", exclude_id=p.id) is False
    assert svc.name_exists("  方案1  ") is True   # 去空白
    assert svc.name_exists("不存在") is False


# --------------------------------------------------------------------------- #
# 持久化 / 原子写
# --------------------------------------------------------------------------- #
def test_save_and_reload(svc, tmp_path):
    svc.add(_mk(name="方案1", ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1"))
    path = svc._path
    svc2 = ConfigService(path=path)
    svc2.load()
    assert len(svc2.all()) == 1
    assert svc2.all()[0].name == "方案1"
    assert svc2.all()[0].ip == "192.168.1.100"


def test_active_flags_not_persisted(svc):
    p = svc.add(_mk(name="方案1"))
    p.active = True
    p.active_conflict = True
    svc.save()
    with open(svc._path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stored = data["profiles"][0]
    assert "active" not in stored
    assert "active_conflict" not in stored


def test_atomic_write_uses_tmp_and_replace(svc, monkeypatch):
    """验证 save() 走 `.tmp` + os.replace。"""
    calls: dict[str, object] = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        calls["src"] = src
        calls["dst"] = dst
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    svc.add(_mk(name="方案1"))
    assert str(calls.get("src", "")).endswith(".tmp")
    assert calls.get("dst") == svc._path
    # 正常写完后 tmp 不应残留
    assert not os.path.exists(svc._path + ".tmp")


# --------------------------------------------------------------------------- #
# 损坏恢复
# --------------------------------------------------------------------------- #
def test_corrupt_json_backed_up_and_rebuilt(svc):
    with open(svc._path, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ]")
    profiles = svc.load()               # 不应抛异常
    assert profiles == []
    assert os.path.exists(svc._path + ".bak")   # 备份为 .bak


def test_missing_file_ok(svc):
    assert svc.load() == []             # 文件不存在 → 空列表，不报错


def test_wrong_structure_backed_up(svc):
    with open(svc._path, "w", encoding="utf-8") as f:
        json.dump({"profiles": "not-a-list"}, f)
    # profiles 非 list：load 会把它当空列表处理（结构异常也容错）
    result = svc.load()
    assert isinstance(result, list)


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def test_validate_static_ok():
    p = Profile(id="", name="X", adapter="以太网", dhcp=False,
                ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1")
    assert ConfigService.validate(p) == []


def test_validate_static_gateway_mismatch():
    p = Profile(id="", name="X", adapter="以太网", dhcp=False,
                ip="192.168.1.100", mask="255.255.255.0", gateway="10.0.0.1")
    errs = ConfigService.validate(p)
    assert any("同一网段" in e for e in errs)


def test_validate_dhcp_skips_address():
    p = Profile(id="", name="X", adapter="以太网", dhcp=True,
                ip="bad", mask="bad", gateway="bad")
    assert ConfigService.validate(p) == []


def test_validate_missing_name_and_adapter():
    p = Profile(id="", name="", adapter="")
    errs = ConfigService.validate(p)
    assert any("方案名称" in e for e in errs)
    assert any("网卡" in e for e in errs)


# --------------------------------------------------------------------------- #
# compute_active_profile_ids（PRD 5.1.6 全量规则）
# --------------------------------------------------------------------------- #
def _cfg(**kw) -> NetshConfig:
    base = dict(adapter="以太网", parsed_ok=True)
    base.update(kw)
    return NetshConfig(**base)


def test_dhcp_profile_active_when_system_dhcp(svc):
    p = svc.add(Profile(id="", name="D", adapter="以太网", dhcp=True))
    svc.compute_active_profile_ids({"以太网": _cfg(dhcp_enabled=True)})
    assert svc.get(p.id).active is True


def test_dhcp_profile_inactive_when_system_static(svc):
    p = svc.add(Profile(id="", name="D", adapter="以太网", dhcp=True))
    svc.compute_active_profile_ids({"以太网": _cfg(dhcp_enabled=False, ip="192.168.1.5")})
    assert svc.get(p.id).active is False


def test_static_profile_active_when_all_match(svc):
    p = svc.add(Profile(
        id="", name="S", adapter="以太网", dhcp=False,
        ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1",
        dns_primary="8.8.8.8",
    ))
    cfg = _cfg(dhcp_enabled=False, ip="192.168.1.100", mask="255.255.255.0",
               gateway="192.168.1.1", dns_servers=["8.8.8.8"])
    svc.compute_active_profile_ids({"以太网": cfg})
    assert svc.get(p.id).active is True


def test_static_profile_inactive_on_gateway_mismatch(svc):
    p = svc.add(Profile(
        id="", name="S", adapter="以太网", dhcp=False,
        ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1",
    ))
    cfg = _cfg(dhcp_enabled=False, ip="192.168.1.100", mask="255.255.255.0",
               gateway="192.168.1.254")   # 网关不等
    svc.compute_active_profile_ids({"以太网": cfg})
    assert svc.get(p.id).active is False


def test_secondary_dns_in_system_list_matches(svc):
    """备 DNS 出现在系统 DNS 列表中即算匹配（不要求顺序/数量一致）。"""
    p = svc.add(Profile(
        id="", name="S", adapter="以太网", dhcp=False,
        ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1",
        dns_primary="8.8.8.8", dns_secondary="114.114.114.114",
    ))
    cfg = _cfg(dhcp_enabled=False, ip="192.168.1.100", mask="255.255.255.0",
               gateway="192.168.1.1", dns_servers=["8.8.8.8", "114.114.114.114"])
    svc.compute_active_profile_ids({"以太网": cfg})
    assert svc.get(p.id).active is True


def test_secondary_dns_absent_is_inactive(svc):
    p = svc.add(Profile(
        id="", name="S", adapter="以太网", dhcp=False,
        ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1",
        dns_primary="8.8.8.8", dns_secondary="1.1.1.1",
    ))
    cfg = _cfg(dhcp_enabled=False, ip="192.168.1.100", mask="255.255.255.0",
               gateway="192.168.1.1", dns_servers=["8.8.8.8"])
    svc.compute_active_profile_ids({"以太网": cfg})
    assert svc.get(p.id).active is False


def test_conflict_when_multiple_match_same_adapter(svc):
    p1 = svc.add(Profile(id="", name="D1", adapter="以太网", dhcp=True))
    p2 = svc.add(Profile(id="", name="D2", adapter="以太网", dhcp=True))
    svc.compute_active_profile_ids({"以太网": _cfg(dhcp_enabled=True)})
    assert svc.get(p1.id).active is True
    assert svc.get(p2.id).active is True
    assert svc.get(p1.id).active_conflict is True
    assert svc.get(p2.id).active_conflict is True


def test_no_conflict_when_single_match(svc):
    p = svc.add(Profile(id="", name="D1", adapter="以太网", dhcp=True))
    svc.compute_active_profile_ids({"以太网": _cfg(dhcp_enabled=True)})
    assert svc.get(p.id).active_conflict is False


def test_parsed_not_ok_silently_skipped(svc):
    p = svc.add(Profile(id="", name="D", adapter="以太网", dhcp=True))
    svc.compute_active_profile_ids({"以太网": _cfg(parsed_ok=False)})
    assert svc.get(p.id).active is False


def test_unknown_adapter_skipped(svc):
    p = svc.add(Profile(id="", name="D", adapter="不存在的网卡", dhcp=True))
    svc.compute_active_profile_ids({"以太网": _cfg(dhcp_enabled=True)})
    assert svc.get(p.id).active is False


def test_dhcp_none_does_not_mislabel_static(svc):
    """dhcp_enabled=None（解析不出）→ 静态方案不得被误标。"""
    p = svc.add(Profile(
        id="", name="S", adapter="以太网", dhcp=False,
        ip="192.168.1.100", mask="255.255.255.0", gateway="192.168.1.1",
    ))
    cfg = _cfg(parsed_ok=True, dhcp_enabled=None, ip="192.168.1.100",
               mask="255.255.255.0", gateway="192.168.1.1")
    svc.compute_active_profile_ids({"以太网": cfg})
    assert svc.get(p.id).active is False


def test_compute_does_not_use_active_profile_id(svc):
    """PRD v1.3 C2：生效判定不依赖 active_profile_id 字段。"""
    p = svc.add(Profile(id="", name="D", adapter="以太网", dhcp=True))
    svc.mark_applied(p.id)                # 记录 last_applied
    # 系统 DHCP 未启用 → 即使 last_applied 是它，也不应标记为生效
    svc.compute_active_profile_ids({"以太网": _cfg(dhcp_enabled=False, ip="192.168.1.5")})
    assert svc.get(p.id).active is False
