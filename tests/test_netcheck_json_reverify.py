"""独立复验：国内两源由 IP138 / IP.cn 换成 **IP9 / PCOnline**（2026-09-21 改版）。

本文件不依赖既有 test_netcheck_service.py，用**任务书给定的精确样本体**逐项复验，
确保不是「橡皮图章」。只改测试文件，不动业务源码。

★ 选型背景（写进注释，防止后人改回去）：
  · IP138 用免费测试端点 test.ip138.com（官方 api.ip138.com 要付费 token）→ 实测拿不到结果；
  · IP.cn 的 api 端点 → 实测同样拿不到结果；
  · 二者均弃用，换成 IP9(`https://ip9.com.cn/get`) 与
    PCOnline(`https://whois.pconline.com.cn/ipJson.jsp?ip=&json=true`)；
  · **首要理由**：本卡交叉验证的是公网 **IPv4**，而 IP9 / PCOnline 是**纯 IPv4 端点**
    （大量国际源实测只回 IPv6，无法做 IPv4 对照）。
"""

from __future__ import annotations

import re

import pytest

import app.netcheck_service as svc
from app.constants import NETCHECK_SOURCES
from app.netcheck_service import (
    NetCheckService,
    _parse_ip9,
    _parse_pconline,
)

# --------------------------------------------------------------------------- #
# 任务书精确样本体（真实抓包形状，与实现约定一一对应）
# --------------------------------------------------------------------------- #
_IP9_JSON_OK = (
    '{"ret":200,"data":{"ip":"183.132.224.185","country":"中国","country_code":"cn",'
    '"prov":"浙江","city":"宁波","area":"","post_code":"315100","area_code":"0574",'
    '"isp":"中国电信","lng":"121.55","lat":"29.82","big_area":"华东"},"qt":0.001}'
)
_IP9_JSON_AREA_OK = (
    '{"ret":200,"data":{"ip":"183.132.224.185","country":"中国","country_code":"cn",'
    '"prov":"浙江","city":"宁波","area":"海曙","post_code":"315100",'
    '"area_code":"0574","isp":"中国电信"},"qt":0.002}'
)
_IP9_JSON_FAIL = '{"ret":500,"data":{}}'
_IP9_JSON_EMPTY = '{"ret":200,"data":{"ip":""}}'
# ⚠️ PCOnline 真实响应体前后堆了大量空行 —— 这条样本专门「练」body.strip()
_PCONLINE_JSON_OK = (
    '\n\n\n{"ip":"183.132.224.185","pro":"浙江省","proCode":"330000","city":"宁波市",'
    '"cityCode":"330200","region":"","regionCode":"0","addr":"浙江省宁波市 电信",'
    '"regionNames":"","err":""}\n\n\n'
)
_PCONLINE_JSON_ERR = '{"ip":"","err":"参数错误"}'
_CF_TRACE = "ip=240e:1:2::3\nfl=SJC\nts=123\n"
_IPINFO_JSON = '{"ip":"1.2.3.4","country":"US","region":"CA","city":"SF","org":"AS123 X","asn":"AS123"}'


# =========================================================================== #
# 1. 解析器正确性（变异测试精神：断言精确字段，破坏特性必失败）
# =========================================================================== #
def test_reverify_parse_ip9_normal():
    ip, extra = _parse_ip9(_IP9_JSON_OK)
    assert ip == "183.132.224.185"
    assert extra["country"] == "中国"
    assert extra["region"] == "浙江"
    assert extra["city"] == "宁波"
    assert extra["isp"] == "中国电信"


def test_reverify_parse_ip9_appends_area():
    """area 非空必须拼到 city 上（宁波 + 海曙 → 宁波海曙）。"""
    ip, extra = _parse_ip9(_IP9_JSON_AREA_OK)
    assert ip == "183.132.224.185"
    assert extra["city"] == "宁波海曙"


def test_reverify_parse_ip9_fail_ret_raises():
    with pytest.raises(ValueError):
        _parse_ip9(_IP9_JSON_FAIL)


def test_reverify_parse_ip9_empty_ip_raises():
    with pytest.raises(ValueError):
        _parse_ip9(_IP9_JSON_EMPTY)


def test_reverify_parse_pconline_normal_with_blank_lines():
    ip, extra = _parse_pconline(_PCONLINE_JSON_OK)
    assert ip == "183.132.224.185"
    assert extra["country"] == "中国"
    assert extra["region"] == "浙江省"
    assert extra["city"] == "宁波市"
    assert extra["isp"] == "电信"


def test_reverify_parse_pconline_err_raises():
    with pytest.raises(ValueError):
        _parse_pconline(_PCONLINE_JSON_ERR)


def test_reverify_pconline_strips_before_json_loads():
    """PCOnline 响应体前后堆空行 → 契约要求**先 strip() 再 json.loads**。

    ⚠️ 诚实说明变异覆盖：CPython 的 `json.loads` 本身**容忍**首尾空白，所以单纯删掉
    `body.strip()` 并不会让这条样本解析失败 —— runtime 断言抓不住该退化。
    故这里改用**源码契约做静态守卫**（守住的是「防服务端偶发脏前缀/BOM」这条防御性
    约定），再配一条 runtime 断言保证大段空白样本确实能被正确解析。
    """
    src = open(svc.__file__, encoding="utf-8").read()
    m = re.search(r"def _parse_pconline\(.*?\n(?=\ndef |\n# =)", src, re.S)
    assert m, "未找到 _parse_pconline 函数体"
    assert "json.loads(body.strip())" in m.group(0), \
        "契约要求 _parse_pconline 必须先 body.strip() 再 json.loads"
    # runtime：带大量首尾空白的真实样本仍能正确解析
    assert _PCONLINE_JSON_OK != _PCONLINE_JSON_OK.strip()
    assert not _PCONLINE_JSON_OK.startswith("{")
    ip, extra = _parse_pconline(_PCONLINE_JSON_OK)
    assert ip == "183.132.224.185" and extra["isp"] == "电信"


# --------------------------------------------------------------------------- #
# 变异探针：证明测试真的在查「解析器抽字段」，而非恒真断言
# 把解析器临时替换成「返回常量」的坏实现 → 集成结果必须暴露该退化
# --------------------------------------------------------------------------- #
def test_reverify_mutation_parser_returning_constants_fails(monkeypatch):
    """变异探针：把 IP9 绑定的解析器换成「返回常量」的坏实现 →
    check_sources 的集成断言必须抓出该退化（ip/loc 不再是真实值）。
    ★ 注意：_SOURCE_SPECS 在模块加载时就直接持有解析器引用，
       故必须替换 dict 里的元组（而非 monkeypatch 模块属性）才能注入坏实现；
       monkeypatch.setattr 会在测试后自动还原 _SOURCE_SPECS，不污染其它用例。"""
    specs = dict(svc._SOURCE_SPECS)
    url, _old_parser, is_v6, group = specs["IP9"]
    specs["IP9"] = (
        url,
        lambda b: ("常量IP", {"country": "X", "region": "", "city": "",
                              "isp": "", "org": "", "asn": ""}),
        is_v6, group,
    )
    monkeypatch.setattr(svc, "_SOURCE_SPECS", specs)

    def fake_fetch(url, timeout=5):
        return True, _IP9_JSON_OK, 0.1, ""

    monkeypatch.setattr(svc, "_fetch", fake_fetch)
    results = NetCheckService().check_sources()
    ip9 = next(s for s in results if s.name == "IP9")
    # 坏解析器把真实 IP/归属地换成常量 → 断言抓取该退化
    assert ip9.ip != "183.132.224.185", "变异测试失败：坏解析器未被断言抓住"
    assert ip9.loc != "中国 浙江 宁波", "变异测试失败：坏解析器未被断言抓住"


# =========================================================================== #
# 2. _SOURCE_SPECS 端点确为 IP9 / PCOnline + 旧源解析器已删除
# =========================================================================== #
def test_reverify_source_specs_are_new_cn_endpoints():
    specs = svc._SOURCE_SPECS
    assert specs["IP9"][0] == "https://ip9.com.cn/get"
    assert specs["PCOnline"][0] == \
        "https://whois.pconline.com.cn/ipJson.jsp?ip=&json=true"
    # ★ 国际两源完全不动（任务书硬约束）
    assert specs["Cloudflare"][0] == "https://www.cloudflare.com/cdn-cgi/trace"
    assert specs["IPinfo.io"][0] == "https://ipinfo.io/json"
    # 解析器绑定正确
    assert specs["IP9"][1] is _parse_ip9
    assert specs["PCOnline"][1] is _parse_pconline
    # 旧源必须彻底下架：不得再出现在 4 源清单里
    assert "IP138" not in specs and "IP.cn" not in specs
    # 源名/分组 = IP9/PCOnline（国内）+ Cloudflare/IPinfo.io（国际）
    assert [n for n, _u, _g in NETCHECK_SOURCES] == \
        ["IP9", "PCOnline", "Cloudflare", "IPinfo.io"]
    # 分组标签仍是 2 国内 + 2 国际
    assert [g for _n, _u, g in NETCHECK_SOURCES] == ["国内", "国内", "国际", "国际"]


def test_reverify_old_cn_parsers_removed():
    src = open(svc.__file__, encoding="utf-8").read()
    # 旧的两个解析器（ip138 / ip.cn）必须彻底删除
    assert not re.search(r"def _parse_ip138_json\b|def _parse_ipcn_json\b", src), \
        "旧 IP138 / IP.cn 解析器未删除"
    # 国内端点域名在 app 里零残留
    assert not re.search(r"test\.ip138\.com|api\.ip138\.com|www\.ip\.cn/api", src)
    # 新解析器依赖 json.loads
    assert "json.loads" in src


# =========================================================================== #
# 3. check_sources 集成守卫（精确样本体）
# =========================================================================== #
def test_reverify_check_sources_integration(monkeypatch):
    samples = {
        "ip9.com.cn": _IP9_JSON_OK,
        "pconline": _PCONLINE_JSON_OK,
        "cloudflare": _CF_TRACE,
        "ipinfo.io": _IPINFO_JSON,
    }

    def fake_fetch(url, timeout=5):
        for key, body in samples.items():
            if key in url:
                return True, body, 0.1, ""
        return False, "", 0.1, "未匹配端点"

    monkeypatch.setattr(svc, "_fetch", fake_fetch)

    results = NetCheckService().check_sources()
    assert len(results) == 4
    assert [s.name for s in results] == ["IP9", "PCOnline", "Cloudflare", "IPinfo.io"]
    assert all(s.ok for s in results), [(s.name, s.err) for s in results if not s.ok]

    ip_by_name = {s.name: s.ip for s in results}
    assert ip_by_name["IP9"] == "183.132.224.185"
    assert ip_by_name["PCOnline"] == "183.132.224.185"
    assert ip_by_name["Cloudflare"] == "240e:1:2::3"
    assert ip_by_name["IPinfo.io"] == "1.2.3.4"
    # 国内源带回归属地（卡片三行式数据来源）
    ip9 = next(s for s in results if s.name == "IP9")
    assert ip9.loc == "中国 浙江 宁波" and ip9.provider == "中国电信"
    pc = next(s for s in results if s.name == "PCOnline")
    assert pc.loc == "中国 浙江省 宁波市" and pc.provider == "电信"
    # ★ 两个国内源都必须是 IPv4（本卡交叉验证的就是公网 IPv4）
    assert ip9.ipv6 is False and pc.ipv6 is False


def test_reverify_check_sources_all_fetch_fail_graceful(monkeypatch):
    """破坏性对照：_fetch 全部抛异常 → 不崩，4 个结果 ok 全 False 且 err 非空。"""

    def boom(url, timeout=5):
        raise RuntimeError("simulated network down")

    monkeypatch.setattr(svc, "_fetch", boom)

    results = NetCheckService().check_sources()
    assert len(results) == 4
    assert all(not s.ok for s in results)
    assert all(s.err for s in results), "优雅降级铁律：失败源必须带非空 err"
    assert [s.name for s in results] == ["IP9", "PCOnline", "Cloudflare", "IPinfo.io"]


# =========================================================================== #
# 4. 服务层纯度 + 依赖纪律
# =========================================================================== #
def test_reverify_service_layer_no_qt_no_requests():
    src = open(svc.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(from|import)\s+(PySide|PyQt|qt)", src, re.I | re.M), \
        "netcheck_service.py 禁止 import Qt/PySide/PyQt"
    assert not re.search(r"(^|\s)import\s+requests", src), \
        "netcheck_service.py 禁止 import requests 或第三方 HTTP 库"
    # HTTP 只用 stdlib urllib
    assert "urllib.request" in src
    # py_compile 两个文件无错
    import py_compile

    py_compile.compile(svc.__file__, doraise=True)
    import app.constants as const_mod
    py_compile.compile(const_mod.__file__, doraise=True)
