"""网络检查服务层测试（离线，无 GUI，无真实网络）。

覆盖：
- 数据模型（IpInfo / NetCheckResult 的派生结论）
- ★ P0 回归守护：`agree` 必须 **IPv4 / IPv6 分开判定**（曾混入同一集合误报「分歧」）
- `_fetch` 绝不抛异常（任何网络错误都收敛成 ok=False）
- `_probe_latency` HEAD→GET 回退（微信 405 实测必走此分支）
- `check_all` 在 monkeypatch 源/站点/网卡后**完全确定性**、不碰真实网络
- 服务层**不 import Qt** 的契约守卫（保证 pytest 无 GUI 单测）
"""

from __future__ import annotations

import base64
import urllib.error
import urllib.request
from dataclasses import replace

import pytest

from app.netcheck_service import (
    IpInfo,
    NetCheckResult,
    NetCheckService,
    SourceResult,
    SiteProbe,
    _parse_ip9,
    _parse_pconline,
)


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
def test_ipinfo_location_and_provider():
    info = IpInfo(ip="1.2.3.4", country="中国", region="浙江", city="宁波",
                  isp="电信", org="Chinanet", asn="AS4134")
    assert info.location == "中国 浙江 宁波"
    # provider 优先 isp
    assert info.provider == "电信"
    # org 兜底
    assert IpInfo(ip="9.9.9.9", org="Cloudflare").provider == "Cloudflare"
    assert not IpInfo().ok            # 空 ip → 无效


def test_ipinfo_display_ip_truncates_long_v6():
    long_v6 = "240e:391:abcd:1234:5678:9abc:def0:1234"
    d = IpInfo(ip=long_v6).display_ip(26)
    assert "…" in d and len(d) == 26
    # 短 IP 不截断
    assert IpInfo(ip="1.2.3.4").display_ip(26) == "1.2.3.4"


# --------------------------------------------------------------------------- #
# ★ agree：IPv4 / IPv6 分开判定的 P0 回归守护
# --------------------------------------------------------------------------- #
def _src(name: str, ip: str, is_v6: bool = False, ok: bool = True) -> SourceResult:
    return SourceResult(name=name, url="http://x", ok=ok, ip=ip, ipv6=is_v6)


def test_agree_true_when_each_family_consistent():
    """真实复现场景：4 个源报同一 IPv4 + 1 个源报同一 IPv6 → 应判「一致」。

    早期 bug：把 v4/v6 混进同一集合比长度 → 误得到 2 个不同值 → 误报「分歧」。
    """
    r = NetCheckResult(
        sources=[
            _src("a", "183.132.224.185"),
            _src("b", "183.132.224.185"),
            _src("c", "183.132.224.185"),
            _src("d", "183.132.224.185"),
            _src("e6", "240e:391:1::2", is_v6=True),
        ]
    )
    assert r.agree is True
    assert r.agree_count == 5
    assert r.v4_ips == ["183.132.224.185"]
    assert r.v6_ips == ["240e:391:1::2"]


def test_agree_false_when_v4_diverges():
    """两个源报了不同 IPv4 → 该协议族内部不一致 → 判「分歧」。"""
    r = NetCheckResult(
        sources=[_src("a", "1.1.1.1"), _src("b", "2.2.2.2")]
    )
    assert r.agree is False
    assert len(r.v4_ips) == 2


def test_agree_false_when_v6_diverges_but_v4_ok():
    """仅 IPv6 分歧、IPv4 一致 → 仍判「分歧」（分族严格）。"""
    r = NetCheckResult(
        sources=[
            _src("a", "183.132.224.185"),
            _src("e6", "240e:391:1::2", is_v6=True),
            _src("f6", "240e:391:9::9", is_v6=True),
        ]
    )
    assert r.agree is False


def test_agree_ignores_failed_sources():
    """失败源不应污染一致性判定。"""
    r = NetCheckResult(
        sources=[_src("a", "1.1.1.1"), _src("b", "", ok=False)]
    )
    assert r.agree is True
    assert r.agree_count == 1


# --------------------------------------------------------------------------- #
# _pick_best 评分
# --------------------------------------------------------------------------- #
def test_pick_best_prefers_richest_info():
    svc = NetCheckService()
    infos = [
        IpInfo(ip="1.1.1.1"),                                       # 0 分
        IpInfo(ip="2.2.2.2", country="中国"),                        # 3 分
        IpInfo(ip="3.3.3.3", country="中国", region="浙江", isp="电信", asn="AS4134"),  # 6 分
    ]
    best = svc._pick_best(infos)
    assert best.ip == "3.3.3.3"
    # 空列表返回空 IpInfo
    assert not svc._pick_best([]).ok


# --------------------------------------------------------------------------- #
# _fetch 绝不抛异常
# --------------------------------------------------------------------------- #
def test_fetch_never_raises_on_urlerror(monkeypatch):
    from app.netcheck_service import _fetch

    def _boom(req, timeout=None):
        raise urllib.error.URLError("连接失败：名字解析失败")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ok, body, elapsed, err = _fetch("http://unreachable.example")
    assert ok is False
    assert body == ""
    assert err                       # 错误信息非空
    assert elapsed >= 0


def test_fetch_captures_ssl_error(monkeypatch):
    from app.netcheck_service import _fetch
    import ssl

    def _boom(req, timeout=None):
        raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ok, _, _, err = _fetch("https://bad.example")
    assert ok is False
    assert "SSL" in err


# --------------------------------------------------------------------------- #
# 2026-09-21 favicon 三级候选链（旧源 icons.duckduckgo.com 被墙，图标清一色退化成占位符）
# --------------------------------------------------------------------------- #
# 一张**真实可解码**的 1×1 PNG（已用 QPixmap.loadFromData 验证通过），
# 用于「第一级就该命中」的用例 —— 避免手搓字节导致测试自身不可信。
_MIN_PNG: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADklEQV"
    "R4nGP4z8DAAMQACf4B/4PiLjgAAAAASUVORK5CYII="
)
# 字节跳动官网 `/favicon.ico` 的实测形态：21 字节的 text/plain 假图（下面是其典型内容）
_PLAIN_TEXT_FAKE: bytes = b"not an icon"


def test_is_image_bytes_accepts_real_formats():
    """魔数校验必须放行 favicon 实际会出现的 5 种格式。"""
    from app.netcheck_service import _is_image_bytes

    assert _is_image_bytes(_MIN_PNG[:8] + b"x" * 20)       # PNG 头
    assert _is_image_bytes(b"\xff\xd8\xff\xe0" + b"x" * 20)  # JPEG
    assert _is_image_bytes(b"GIF89a" + b"x" * 20)            # GIF
    assert _is_image_bytes(b"\x00\x00\x01\x00" + b"x" * 20)  # ICO
    assert _is_image_bytes(b"RIFF\x1a\x00\x00\x00WEBP" + b"x" * 20)  # WebP


def test_is_image_bytes_rejects_garbage_and_short():
    """魔数校验必须拦掉 text/plain 假图与过短数据。

    ★ 这是本次换源的关键防线：字节跳动官网 `/favicon.ico` 实测返回 21 字节
      的 `text/plain`，若照单全收，QPixmap 会 loadFromData 失败、图标位置留白，
      而代码**不会报错**（典型的静默失败）。
    """
    from app.netcheck_service import _is_image_bytes

    assert not _is_image_bytes(_PLAIN_TEXT_FAKE)
    assert not _is_image_bytes(b"")
    assert not _is_image_bytes(b"\x89PNG")            # 只有 4 字节，太短
    # RIFF 容器但不是 WEBP（.wav/.avi 同魔数）→ 必须拦掉
    assert not _is_image_bytes(b"RIFF\x1a\x00\x00\x00WAVE" + b"x" * 20)


def test_is_image_bytes_accepts_svg():
    """★ SVG 必须放行 —— 这是 GitHub 图标在**真机**上唯一的活路。

    2026-09-21 实测链路：`favicon.cccyun.cc/github.com` 返回的是 SVG
    （`image/svg+xml`，959B），而链② `github.com/favicon.ico` 直连在境内
    **必超时**（只有挂代理的沙箱能通）。若魔数表拒 SVG，GitHub 图标在用户
    机器上永远是文字占位符。本环境 Qt 带 svg 插件，`QPixmap.loadFromData`
    实测可渲染（32×32 → 缩 18×18 正常）。

    ⚠️ 但必须**严格限定 `<svg` 字面量**：普通 HTML 404 页同样是纯文本、
    同样以 `<` 开头，不能当图标放行（否则图标位置留白且代码不报错，属静默失败）。
    """
    from app.netcheck_service import _is_image_bytes

    assert _is_image_bytes(b'<svg width="32" height="32"></svg>')
    assert _is_image_bytes(b'  \n<svg xmlns="http://www.w3.org/2000/svg"/>')       # 首部空白
    assert _is_image_bytes(b'\xef\xbb\xbf<svg viewBox="0 0 32 32"/>')              # UTF-8 BOM
    assert _is_image_bytes(b'<?xml version="1.0"?>\n<svg viewBox="0 0 16 16"/>')   # XML 声明
    # 反面：HTML 404 页 / 非 SVG 的 XML → 必须拦掉
    assert not _is_image_bytes(b"<!DOCTYPE html><html><body>404</body></html>")
    assert not _is_image_bytes(b"<html><head><title>404</title></head></html>")
    assert not _is_image_bytes(b'<?xml version="1.0"?><plist></plist>' + b"x" * 20)


def test_favicon_chain_accepts_svg_at_first_level(monkeypatch):
    """★ GitHub 场景：链①（镜像）返回 SVG → 必须**一级命中**，不再请求链②。

    链② 是 github.com 直连，在用户真机境内必超时 —— 多等一个 2s 超时是白费。
    变异验证：把 `_is_image_bytes` 里的 SVG 分支删掉 → 本用例失败（会请求到链②）。
    """
    from app import netcheck_service as nsvc

    calls: list[str] = []
    svg = b'<svg width="32" height="32" viewBox="0 0 32 32"></svg>'

    def fake_fetch(url, timeout=5):
        calls.append(url)
        return svg

    monkeypatch.setattr(nsvc, "fetch_favicon_bytes", fake_fetch)
    got = nsvc.fetch_favicon_bytes_chain(
        "https://favicon.cccyun.cc/github.com,https://github.com/favicon.ico"
    )
    assert got == svg
    assert calls == ["https://favicon.cccyun.cc/github.com"], (
        "链① 已返回 SVG（Qt 可渲染），不该再请求链② 的 github.com 直连"
    )


def test_favicon_chain_stops_at_first_real_image(monkeypatch):
    """★ 候选顺序：第一级返回真图字节 → 必须**立刻返回**，不得再请求第二级。

    变异验证：把 `if _is_image_bytes(data): return data` 改成无条件继续循环
    → 第二级被调用 → `len(calls) == 1` 断言失败。
    """
    from app import netcheck_service as nsvc

    calls: list[str] = []

    def fake_fetch(url, timeout=5):
        calls.append(url)
        return _MIN_PNG

    monkeypatch.setattr(nsvc, "fetch_favicon_bytes", fake_fetch)
    got = nsvc.fetch_favicon_bytes_chain("first.url,second.url")
    assert got == _MIN_PNG
    assert calls == ["first.url"], "第一级就命中了真图，不该再请求第二级"


def test_favicon_chain_falls_through_on_fake_image(monkeypatch):
    """★ 魔数校验不过 → 必须换下一级。这正是字节「text/plain 假图」要走的路径。"""
    from app import netcheck_service as nsvc

    calls: list[str] = []

    def fake_fetch(url, timeout=5):
        calls.append(url)
        return _PLAIN_TEXT_FAKE if len(calls) == 1 else _MIN_PNG

    monkeypatch.setattr(nsvc, "fetch_favicon_bytes", fake_fetch)
    got = nsvc.fetch_favicon_bytes_chain(
        "https://www.bytedance.com/favicon.ico,https://favicon.cccyun.cc/www.bytedance.com"
    )
    assert got == _MIN_PNG, "第一级是假图，必须回退到第二级"
    assert calls == [
        "https://www.bytedance.com/favicon.ico",
        "https://favicon.cccyun.cc/www.bytedance.com",
    ]


def test_favicon_chain_all_fail_returns_empty(monkeypatch):
    """★ 全失败返回 b""（UI 侧保留文字占位符 —— 绝不崩、绝不上网失败就炸）。"""
    from app import netcheck_service as nsvc

    monkeypatch.setattr(nsvc, "fetch_favicon_bytes", lambda url, timeout=5: _PLAIN_TEXT_FAKE)
    assert nsvc.fetch_favicon_bytes_chain("a.url,b.url") == b""

    monkeypatch.setattr(nsvc, "fetch_favicon_bytes", lambda url, timeout=5: b"")
    assert nsvc.fetch_favicon_bytes_chain("a.url,b.url") == b""


def test_favicon_chain_skips_empty_segments(monkeypatch):
    """★ 空段必须跳过：尾随逗号 / 注释留白都不该导致多发一次网络请求或直接返回空。"""
    from app import netcheck_service as nsvc

    calls: list[str] = []

    def fake_fetch(url, timeout=5):
        calls.append(url)
        return _MIN_PNG

    monkeypatch.setattr(nsvc, "fetch_favicon_bytes", fake_fetch)
    got = nsvc.fetch_favicon_bytes_chain(" , real.url , , ")
    assert got == _MIN_PNG
    assert calls == ["real.url"], "空段必须跳过，不能把空串当 URL 去请求"


def test_netcheck_sites_favicon_is_a_chain_with_two_levels():
    """★ 常量契约：每个站点的 favicon 必须是「两级候选链」，且顺序符合分组策略。

    · 国内组 → **直连优先**（实测淘宝/B站/Cloudflare/jsDelivr 直连可用）
    · 国际组 → **镜像优先**（github.com / youtube.com 直连在境内不通）
    ⚠️ 且任何一级都不得再出现被墙的 icons.duckduckgo.com。
    """
    from app.constants import NETCHECK_SITES

    for site in NETCHECK_SITES:
        parts = [p.strip() for p in site["favicon"].split(",") if p.strip()]
        assert len(parts) == 2, f"{site['name']} 的 favicon 必须是两级候选链"
        assert "icons.duckduckgo.com" not in site["favicon"], "旧被墙图标源残留"
        host = site["url"].split("://", 1)[1]
        direct = f"https://{host}/favicon.ico"
        mirror = f"https://favicon.cccyun.cc/{host}"
        assert set(parts) == {direct, mirror}, f"{site['name']} 候选链内容不对"
        if site["group"] == "国内":
            assert parts[0] == direct, f"{site['name']}（国内）应直连优先"
        else:
            assert parts[0] == mirror, f"{site['name']}（国际）应镜像优先"


# --------------------------------------------------------------------------- #
# _probe_latency：HEAD→GET 回退（微信 405 实测必走）
# --------------------------------------------------------------------------- #
class _FakeResp:
    def read(self, n=0):
        return b""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_probe_latency_head_falls_back_to_get(monkeypatch):
    from app.netcheck_service import _probe_latency

    def _fake_urlopen(req, timeout=None):
        # HEAD 被拒 → 回退 GET；GET 成功
        if req.method == "HEAD":
            raise urllib.error.HTTPError(req.full_url, 405, "Method Not Allowed", None, None)
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    ok, ms, err = _probe_latency("https://weixin.qq.com")
    assert ok is True
    assert isinstance(ms, int)
    assert err == ""


def test_probe_latency_get_failure_reported(monkeypatch):
    from app.netcheck_service import _probe_latency

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    ok, ms, err = _probe_latency("https://github.com")
    assert ok is False
    assert ms is None
    assert "超时" in err or "timed out" in err


# --------------------------------------------------------------------------- #
# check_all：完全离线、确定性
# --------------------------------------------------------------------------- #
def test_check_all_offline(monkeypatch):
    from app.models import AdapterInfo

    def _fake_src(name):
        sr = SourceResult(name=name, url="http://x", ok=True, elapsed_s=0.1, ip="183.132.224.185")
        info = IpInfo(ip="183.132.224.185", country="中国", region="浙江",
                      city="宁波", isp="电信", asn="AS4134", source=name)
        return sr, info

    def _fake_site(site):
        return SiteProbe(name=site["name"], url=site["url"],
                         group=site.get("group", "国内"), ok=True, latency_ms=120)

    monkeypatch.setattr("app.netcheck_service._query_source", _fake_src)
    monkeypatch.setattr("app.netcheck_service._probe_site", _fake_site)
    # IPv6 兜底探测也会发请求 → 一并离线化（探测失败，ipv6 保持空）
    monkeypatch.setattr("app.netcheck_service._fetch",
                        lambda url, timeout=5: (False, "", 0.0, "离线测试"))

    class _FakeNetsh:
        def list_adapters(self):
            return [AdapterInfo(name="以太网", ip="192.168.1.100",
                                mask="255.255.255.0", gateway="192.168.1.1", is_up=True)]

    svc = NetCheckService(max_workers=2)
    r = svc.check_all(netsh_service=_FakeNetsh())

    # 所有源成功 → IPv4 一致
    assert r.any_source_ok
    assert r.ipv4.ip == "183.132.224.185"
    assert r.ipv4.location == "中国 浙江 宁波"
    assert r.agree is True
    # 网卡经由 netsh 读入
    assert r.adapters and r.adapters[0].name == "以太网"
    # 站点全部可达
    assert r.reachable_count == len(r.sites)
    assert r.elapsed_s >= 0


def test_check_all_sources_carry_location_fields(monkeypatch):
    """交叉验证 4 源必须带回「归属地 / 运营商」（卡片三行式的数据来源）。"""
    from app.models import AdapterInfo  # noqa: F401

    def _fake_src(name):
        sr, info = _canned_source(name)
        return sr, info

    monkeypatch.setattr("app.netcheck_service._query_source", _fake_src)
    monkeypatch.setattr("app.netcheck_service._probe_site",
                        lambda site: SiteProbe(name=site["name"], url=site["url"], ok=False))
    monkeypatch.setattr("app.netcheck_service._fetch",
                        lambda url, timeout=5: (False, "", 0.0, "离线测试"))

    r = NetCheckService().check_all(netsh_service=None)
    ok_sources = [s for s in r.sources if s.ok]
    assert ok_sources, "至少一个源成功"
    for s in ok_sources:
        assert s.loc, f"{s.name} 缺归属地"
        assert s.provider, f"{s.name} 缺运营商"
        assert s.group in ("国内", "国际")


def _canned_source(name: str):
    """按源名返回带归属地的 canned 结果（测试复用）。"""
    if name == "ipip.net":
        sr = SourceResult(name=name, url="u", group="国内", ok=True, ip="183.132.224.185")
        sr.loc, sr.provider, sr.asn = "中国 浙江 宁波", "电信", ""
        return sr, IpInfo(ip="183.132.224.185", country="中国", region="浙江",
                          city="宁波", isp="电信", source=name)
    sr = SourceResult(name=name, url="u", group="国际", ok=True, ip="183.132.224.185")
    sr.loc, sr.provider, sr.asn = "CN Zhejiang Ningbo", "Chinanet", "AS4134"
    return sr, IpInfo(ip="183.132.224.185", country="CN", region="Zhejiang",
                      city="Ningbo", isp="Chinanet", asn="AS4134", source=name)


def test_check_all_ipv6_fallback_probe(monkeypatch):
    """4 个源都只回 IPv4 时，IPv6 列由兜底探测端点填充（双栈用户不丢 IPv6 显示）。"""
    def _fake_src(name):
        sr, info = _canned_source(name)     # 全部 IPv4
        sr.ipv6 = False
        return sr, info

    monkeypatch.setattr("app.netcheck_service._query_source", _fake_src)
    monkeypatch.setattr("app.netcheck_service._probe_site",
                        lambda site: SiteProbe(name=site["name"], url=site["url"], ok=False))
    # 兜底探测：第一个端点失败、第二个成功返回 IPv6
    def _fake_fetch(url, timeout=5):
        if "icanhazip" in url:
            return False, "", 0.1, "超时"
        return True, "240e:391:1a60:2ee0:c1b6:9af1:7ef3:eec1\n", 0.2, ""
    monkeypatch.setattr("app.netcheck_service._fetch", _fake_fetch)

    r = NetCheckService().check_all(netsh_service=None)
    assert r.ipv6.ok
    assert r.ipv6.ip == "240e:391:1a60:2ee0:c1b6:9af1:7ef3:eec1"
    # v6 兜底结果**不进**交叉验证源列表（保持 4 源语义）
    assert all(not s.ipv6 for s in r.sources)


def test_check_all_partial_failure_isolated(monkeypatch):
    """单个源失败不应拖垮整体（F4-10）。"""
    from app.models import AdapterInfo

    def _fake_src(name):
        if name.startswith("ip-api"):
            return SourceResult(name=name, url="http://x", ok=False, err="超时"), None
        sr = SourceResult(name=name, url="http://x", ok=True, elapsed_s=0.1, ip="183.132.224.185")
        return sr, IpInfo(ip="183.132.224.185", source=name)

    monkeypatch.setattr("app.netcheck_service._query_source", _fake_src)
    monkeypatch.setattr("app.netcheck_service._probe_site",
                        lambda site: SiteProbe(name=site["name"], url=site["url"],
                                               ok=False, err="超时"))
    monkeypatch.setattr("app.netcheck_service.NetCheckService._read_adapters",
                        staticmethod(lambda ns: []))
    # IPv6 兜底探测离线化（探测失败即可，不影响断言）
    monkeypatch.setattr("app.netcheck_service._fetch",
                        lambda url, timeout=5: (False, "", 0.0, "离线测试"))

    svc = NetCheckService()
    r = svc.check_all(netsh_service=None)
    # 仍有成功源
    assert any(s.ok for s in r.sources)
    # 站点全失败但整体不崩
    assert r.reachable_count == 0
    assert r.elapsed_s >= 0


# --------------------------------------------------------------------------- #
# 契约守卫：服务层绝不 import Qt（保证无 GUI 单测）
# --------------------------------------------------------------------------- #
def test_service_layer_does_not_import_qt():
    import re
    path = __import__("os").path.join(
        __import__("os").path.dirname(__import__("app.netcheck_service").__file__),
        "netcheck_service.py",
    )
    src = open(path, encoding="utf-8").read()
    assert not re.search(r"^\s*(from|import)\s+PySide|QtCore|QtWidgets|QtGui",
                         src, re.M), "netcheck_service.py 禁止 import Qt"


# --------------------------------------------------------------------------- #
# 2026-09-21 国内两源更换：IP138 / IP.cn → IP9 / PCOnline（变异测试守卫）
# --------------------------------------------------------------------------- #
# 样本响应体（项目铁律：用样本体做单测，不实拉真实网络）
_IP9_JSON_OK = (
    '{"ret":200,"data":{"ip":"183.132.224.185","country":"中国","country_code":"cn",'
    '"prov":"浙江","city":"宁波","area":"","post_code":"315100","area_code":"0574",'
    '"isp":"中国电信","lng":"121.55","lat":"29.82","big_area":"华东"},"qt":0.001}'
)
# 区县非空的样本（area 实战常为空，但非空时必须参与拼串）
_IP9_JSON_AREA_OK = (
    '{"ret":200,"data":{"ip":"183.132.224.185","country":"中国","country_code":"cn",'
    '"prov":"浙江","city":"宁波","area":"海曙","post_code":"315100",'
    '"area_code":"0574","isp":"中国电信"},"qt":0.002}'
)
_IP9_JSON_FAIL = '{"ret":500,"data":{}}'
_IP9_JSON_EMPTY_IP = '{"ret":200,"data":{"ip":""}}'
# ⚠️ PCOnline 真实响应体**前后堆了大量空行** → 解析器必须先 strip() 才能 json.loads。
#    这条样本专门用来「练」strip()，去掉空行就练不到那一行代码。
_PCONLINE_JSON_OK = (
    '\n\n\n{"ip":"183.132.224.185","pro":"浙江省","proCode":"330000","city":"宁波市",'
    '"cityCode":"330200","region":"","regionCode":"0","addr":"浙江省宁波市 电信",'
    '"regionNames":"","err":""}\n\n\n'
)
_PCONLINE_JSON_ERR = '{"ip":"","err":"参数错误"}'


def test_parse_ip9_normal():
    """IP9 正常样本 → 正确抽 IP / 国家 / 省 / 市 / 运营商。
    ★ 变异：漏判 ret 字段、把 prov 当 city、丢掉 isp → 本测试必失败。"""
    ip, extra = _parse_ip9(_IP9_JSON_OK)
    assert ip == "183.132.224.185"
    assert extra["country"] == "中国"
    assert extra["region"] == "浙江"
    assert extra["city"] == "宁波"
    assert extra["isp"] == "中国电信"


def test_parse_ip9_appends_area_to_city():
    """IP9 area（区县）非空 → 拼到 city 上提升精度（宁波 + 海曙 → 宁波海曙）。
    ★ 变异：删掉 `if area and area != city: city = f"{city}{area}"` → 必失败。"""
    ip, extra = _parse_ip9(_IP9_JSON_AREA_OK)
    assert ip == "183.132.224.185"
    assert extra["city"] == "宁波海曙"
    # area 与 city 同值时**不得**重复拼接（守住了 `area != city` 这个去重分支）
    same = _IP9_JSON_AREA_OK.replace('"city":"宁波","area":"海曙"',
                                     '"city":"海曙","area":"海曙"')
    assert _parse_ip9(same)[1]["city"] == "海曙"


def test_parse_ip9_non_200_ret_raises():
    """IP9 ret != "200" → 抛 ValueError（走「解析失败」降级，不崩）。
    ★ 变异：把 `!= "200"` 放宽成「有 ip 就算成功」→ 必失败。"""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_ip9(_IP9_JSON_FAIL)


def test_parse_ip9_ret_not_200_raises_even_with_ip():
    """★ 锐化变异守卫：ret != "200" 且 data 里**带着合法 ip** → 仍必须抛 ValueError。

    为何单独加这条：`_IP9_JSON_FAIL = {"ret":500,"data":{}}` 的 ip 也是空的，
    即便把 ret 判断删掉也会因「空 IP」兜底抛错 —— 抓不住该退化（变异存活）。
    本用例让 ip 合法、只有 ret 不对，删掉 ret 判断就会立刻变红。
    """
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_ip9('{"ret":500,"data":{"ip":"183.132.224.185","prov":"浙江",'
                   '"city":"宁波","isp":"中国电信"}}')


def test_parse_ip9_empty_ip_raises():
    """IP9 data.ip 为空 → 抛 ValueError。"""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_ip9(_IP9_JSON_EMPTY_IP)


def test_parse_ip9_missing_data_does_not_keyerror():
    """IP9 连 data 都没有 → `or {}` 兜底后仍走「空 IP」抛 ValueError，绝不 KeyError。"""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_ip9('{"ret":200}')


def test_parse_pconline_normal_with_blank_lines():
    """PCOnline 正常样本（前后大量空行）→ strip() 后 json.loads，抽 IP/省/市/运营商。
    ★ 变异：删掉 `body.strip()` → json.loads 直接抛错 → 本测试必失败。"""
    ip, extra = _parse_pconline(_PCONLINE_JSON_OK)
    assert ip == "183.132.224.185"
    assert extra["country"] == "中国"       # PCOnline 不返回 country，由 pro 非空推得
    assert extra["region"] == "浙江省"
    assert extra["city"] == "宁波市"
    # 运营商靠 addr 反推：剔掉 pro / city 后剩余部分
    assert extra["isp"] == "电信"


def test_parse_pconline_err_raises_even_with_ip():
    """★ 锐化变异守卫：err 非空且 ip 合法 → 仍必须抛 ValueError。

    同理：`{"ip":"","err":"参数错误"}` 的 ip 为空，删掉 err 判断也会被「空 IP」
    兜住（变异存活）。本用例让 ip 合法、只留 err 非空，删掉 err 判断立刻变红。
    """
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_pconline('{"ip":"183.132.224.185","pro":"浙江省","city":"宁波市",'
                        '"addr":"浙江省宁波市 电信","err":"参数错误"}')


def test_parse_pconline_err_nonempty_raises():
    """PCOnline err 非空 → 抛 ValueError（走「解析失败」降级，不崩）。
    ★ 变异：删掉 err 判断 → 空 IP 分支虽也会兜住，但 err 语义丢失。"""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_pconline(_PCONLINE_JSON_ERR)


def test_parse_pconline_empty_ip_raises():
    """PCOnline err 为空但 ip 为空 → 抛 ValueError。"""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _parse_pconline('{"ip":"","pro":"浙江省","city":"宁波市",'
                        '"addr":"浙江省宁波市 电信","err":""}')


def test_parse_pconline_empty_pro_yields_empty_country():
    """PCOnline 无省（境外 IP）→ country 留空，不谎报「中国」。"""
    _ip, extra = _parse_pconline(
        '{"ip":"8.8.8.8","pro":"","city":"","addr":"","err":""}'
    )
    assert extra["country"] == ""


def test_check_sources_json_endpoints(monkeypatch):
    """★ 集成守卫：国内两源换成 IP9 / PCOnline 后，monkeypatch 各样本体，
    `check_sources` 必须返回 4 个 SourceResult（顺序 IP9/PCOnline/Cloudflare/IPinfo.io）、
    全部 ok、ip 正确、国内源带回归属地（卡片三行式数据来源）。

    变异验证：
    · 把 `_SOURCE_SPECS["IP9"]` 端点指错 → IP9 解析失败 → `all(ok)` 断言失败；
    · 把 `_parse_pconline` 里的 strip() 删掉 → PCOnline 的 ok 断言失败；
    · 把 `_parse_ip9` 的 ret 判断删掉 → IP9 的 ok 依然成立，故另有专门单测守它。
    """
    import app.netcheck_service as svc
    from app.constants import NETCHECK_SOURCES

    samples = {
        "ip9.com.cn": _IP9_JSON_OK,
        "pconline": _PCONLINE_JSON_OK,
        "cloudflare": "ip=240e:391:1a60:2ee0:c1b6:9af1:7ef3:eec1\nfl=SJC\nts=123\n",
        "ipinfo.io": '{"ip":"8.8.8.8","country":"US","region":"California",'
                     '"city":"Mountain View","org":"AS15169 Google LLC"}',
    }

    def fake_fetch(url, timeout=5):            # noqa: ANN002, ANN003
        for key, body in samples.items():
            if key in url:
                return True, body, 0.1, ""
        return False, "", 0.1, "未匹配端点"

    monkeypatch.setattr(svc, "_fetch", fake_fetch)

    results = NetCheckService().check_sources()
    assert len(results) == 4
    # 返回顺序必须 = NETCHECK_SOURCES 声明顺序
    assert [s.name for s in results] == [n for n, _u, _g in NETCHECK_SOURCES]
    assert [s.name for s in results] == ["IP9", "PCOnline", "Cloudflare", "IPinfo.io"]
    # 全部成功（国内端点即便真实环境波动，离线样本下必须全绿）
    assert all(s.ok for s in results), [(s.name, s.err) for s in results if not s.ok]
    # ip 正确
    ip_by_name = {s.name: s.ip for s in results}
    assert ip_by_name["IP9"] == "183.132.224.185"
    assert ip_by_name["PCOnline"] == "183.132.224.185"
    assert ip_by_name["Cloudflare"].startswith("240e:")
    assert ip_by_name["IPinfo.io"] == "8.8.8.8"
    # 国内源带回归属地 / 运营商（三行式数据来源）
    ip9 = next(s for s in results if s.name == "IP9")
    assert ip9.loc == "中国 浙江 宁波" and ip9.provider == "中国电信"
    pc = next(s for s in results if s.name == "PCOnline")
    assert pc.loc == "中国 浙江省 宁波市" and pc.provider == "电信"


def test_check_sources_all_fetch_fail_graceful(monkeypatch):
    """破坏性对照：_fetch 全部抛异常 → 不崩，4 个结果 ok 全 False 且 err 非空。

    守卫的正是「优雅降级铁律」：任一颗源炸掉都必须产出一条 `ok=False` 且带非空
    `err` 的结果，绝不能整批崩溃、也不能静默丢项。
    """
    import app.netcheck_service as svc

    def boom(url, timeout=5):                 # noqa: ANN002, ANN003
        raise RuntimeError("simulated network down")

    monkeypatch.setattr(svc, "_fetch", boom)

    results = NetCheckService().check_sources()
    assert len(results) == 4
    assert all(not s.ok for s in results)
    assert all(s.err for s in results), "优雅降级铁律：失败源必须带非空 err"
    assert [s.name for s in results] == ["IP9", "PCOnline", "Cloudflare", "IPinfo.io"]
