#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
IP 交叉验证源可用性探测器
=========================

用途
----
在**真实网络环境**下批量探测候选 IP 交叉验证源，输出一张「能不能用 / 快不快 /
有没有归属地」的表格，用来决定网络检查页 C 卡（多源交叉验证）最终选用哪 4 个源。

为什么需要它
------------
开发沙箱只有一个 localhost 代理出口（127.0.0.1:12986），该代理放行国际站点
但对 ip.cn / ip138.com 等国内端点一律 502，导致无法在沙箱内验证国内源。
本项目只用了 stdlib，无第三方依赖。

用法（在你自己的命令行里跑，不要用代理环境）：
    python tools\\probe_ip_sources.py
或在项目里任选解释器：
    C:\\Program Files\\Python311\\python.exe tools\\probe_ip_sources.py

输出
----
屏幕打印表格，同时写入 tools/probe_ip_sources_out.txt（UTF-8），方便贴回给 AI。

判定口径
--------
- 可用：HTTP 200 且能从响应体里正则抓到 IPv4（IPv6 单独标注）
- 有归属地：响应体含中文，或含 country/region/city/isp/address/data 等键
- 延迟：单次 GET 往返耗时，仅供参考（受网络抖动影响）
"""

from __future__ import annotations

import io
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NetTool/1.0"}
TIMEOUT = 6

IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f]{1,4}\b")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LOC_KEYS = ("country", "region", "city", "isp", "address", "data", "loc", "province", "area")

# (显示名, 分组, URL, 可选 Referer)
CANDIDATES: list[tuple[str, str, str, str | None]] = [
    # ---------------- 国内 ----------------
    ("IP.cn(api)",     "国内", "https://www.ip.cn/api/index?ip&type=0",            None),
    ("IP.cn(短)",       "国内", "https://api.ip.cn",                                 None),
    ("IP138(test)",    "国内", "https://test.ip138.com/query/?datatype=json",        None),
    ("IP138(test-http)", "国内", "http://test.ip138.com/query/?datatype=json",        None),
    ("IP138(官方需token)", "国内", "https://api.ip138.com/query/?datatype=json",      None),
    ("CSDN",           "国内", "https://searchplugin.csdn.net/api/v1/ip/get?ip=",   None),
    ("PCOnline",       "国内", "https://whois.pconline.com.cn/ipJson.jsp?ip=&json=true", None),
    ("UserAgentInfo",  "国内", "https://ip.useragentinfo.com/json?ip=",             None),
    ("ipchaxun",       "国内", "https://2025.ipchaxun.com/",                         None),
    ("IPPlus360",      "国内", "https://www.ipplus360.com/getIP",                    None),
    ("网易",           "国内", "http://ip.ws.126.net/ipquery?ip=",                   None),
    ("搜狐",           "国内", "http://pv.sohu.com/cityjson",                        None),
    ("IPIP(freeapi)",  "国内", "http://freeapi.ipip.net/",                           None),
    ("ip.la",          "国内", "https://www.ip.la/",                                 None),
    ("IP138(www页)",   "国内", "https://www.ip138.com/",                             None),
    ("IP.cn(www页)",   "国内", "https://www.ip.cn/",                                 None),
    # ↓ 2026-09-20 二轮新增：国内免 key、精度到区县级
    ("IP9.com.cn",     "国内", "https://ip9.com.cn/get",                             None),
    ("百度opendata",   "国内", "http://opendata.baidu.com/api.php?query=&co=&resource_id=6006&oe=utf8", None),
    ("VORE",           "国内", "https://api.vore.top/api/IPdata?ip=",                 None),
    # ↓ 2026-09-20 用户推荐：注意 /myip.html 是 JS 渲染页，轻量端点 myip.ipip.net 才可用
    ("ipip.net/myip.html", "国内", "https://www.ipip.net/myip.html",                  None),
    ("myip.ipip.net",  "国内", "https://myip.ipip.net",                               None),

    # ---------------- 国际 ----------------
    ("IPinfo.io",      "国际", "https://ipinfo.io/json",                             None),
    ("Cloudflare",     "国际", "https://www.cloudflare.com/cdn-cgi/trace",           None),
    ("ipapi.co",       "国际", "https://ipapi.co/json",                              None),
    ("ipwho.is",       "国际", "https://ipwho.is/json",                               None),
    ("ip-api.com(中文)", "国际", "http://ip-api.com/json/?lang=zh-CN",                None),
    ("ipify",          "国际", "https://api.ipify.org?format=json",                   None),
    ("ip.sb",          "国际", "https://api.ip.sb/jsonip",                            None),
    ("icanhazip",      "国际", "https://icanhazip.com",                               None),
    ("ident.me",       "国际", "https://ident.me",                                    None),
    ("ifconfig.me",    "国际", "https://ifconfig.me/all.json",                        None),
    ("myip.wtf",       "国际", "https://myip.wtf/json",                               None),
    # ↓ 2026-09-20 二轮新增：国际免 key 新源
    ("HackMyIP",       "国际", "https://hackmyip.com/api/v1/ip",                      None),
    ("WorldIP.io",     "国际", "https://worldip.io/",                                 None),
    ("country.is",     "国际", "https://api.country.is/",                             None),
    ("country.is(全字段)", "国际", "https://api.country.is/?fields=city,subdivision,asn", None),
    ("IPGEO",          "国际", "https://api.techniknews.net/ipgeo/",                  None),
    ("ipdata.info",    "国际", "https://ipdata.info/json/",                           None),
    # ↓ 2026-09-20 用户推荐
    ("myip.la(首页)",   "国际", "https://myip.la/",                                    None),
    ("api.myip.la/json", "国际", "https://api.myip.la/json",                           None),
]


def probe(url: str, referer: str | None = None) -> dict:
    """GET 一个候选源，返回结构化结果。绝不抛异常。"""
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            latency = int((time.monotonic() - t0) * 1000)
            charset = resp.headers.get_content_charset()
            body = ""
            for enc in (charset, "utf-8", "gbk"):
                if not enc:
                    continue
                try:
                    body = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if not body:
                body = raw.decode("utf-8", errors="replace")

            # 优先从 JSON 的 ip 字段取值（含 data.ip 嵌套），避免正文提示语里的示例 IP 造成误判
            # （实测 WorldIP.io 正文含 "Could not determine a public IPv4" 且带 8.8.8.8 示例，
            #   纯正则会误判成 IPv4 可用；它实际返回的是 IPv6。）
            ip = ""
            try:
                d = json.loads(body)
                if isinstance(d, dict):
                    inner = d.get("data", d)
                    if isinstance(inner, dict):
                        ip = str(inner.get("ip") or inner.get("ip_addr")
                                 or inner.get("query") or "")
                    else:
                        ip = str(d.get("ip") or "")
            except Exception:
                ip = ""
            if not ip:
                m4 = IPV4_RE.search(body)
                m6 = IPV6_RE.search(body)
                ip = m4.group(1) if m4 else (m6.group(0) if m6 else "")
            has_loc = bool(CJK_RE.search(body)) or any(k in body.lower() for k in LOC_KEYS)
            return {
                "ok": True,
                "latency": latency,
                "code": resp.status,
                "ip": ip,
                "v6": (":" in ip),
                "loc": has_loc,
                "err": "",
                "preview": body[:130].replace("\n", " ").replace("\r", " "),
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "latency": int((time.monotonic() - t0) * 1000),
                "code": e.code, "ip": "", "v6": False, "loc": False,
                "err": f"HTTP {e.code}", "preview": ""}
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError) as e:
        reason = getattr(e, "reason", e)
        return {"ok": False, "latency": int((time.monotonic() - t0) * 1000),
                "code": "-", "ip": "", "v6": False, "loc": False,
                "err": f"{reason}"[:60], "preview": ""}
    except Exception as e:
        return {"ok": False, "latency": int((time.monotonic() - t0) * 1000),
                "code": "-", "ip": "", "v6": False, "loc": False,
                "err": f"{type(e).__name__}: {e}"[:60], "preview": ""}


def main() -> int:
    out = io.StringIO()

    def w(s: str = "") -> None:
        print(s)
        out.write(s + "\n")

    proxy_note = []
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        if os.environ.get(k):
            proxy_note.append(f"{k}={os.environ[k]}")
    w("=" * 96)
    w("IP 交叉验证源探测   " + time.strftime("%Y-%m-%d %H:%M:%S"))
    w("代理环境: " + (", ".join(proxy_note) if proxy_note else "无（直连，结果可用）"))
    w("=" * 96)
    w(f"{'源':<18}{'组':<6}{'结果':<6}{'延迟':>8}  {'IP':<20}{'类型':<7}{'归属地':<7}备注")
    w("  ※ 类型=回 IPv4 还是 IPv6。要交叉验证「公网 IPv4」，只有回 IPv4 的源才有信息量。")
    w("-" * 96)

    results = []
    for name, group, url, ref in CANDIDATES:
        r = probe(url, ref)
        results.append((name, group, url, r))
        status = "可用" if r["ok"] and r["ip"] else ("通无IP" if r["ok"] else "失败")
        loc = "有" if r["loc"] else "无"
        ver = "IPv6" if r["v6"] else ("IPv4" if r["ip"] else "-")
        w(f"{name:<18}{group:<6}{status:<6}{r['latency']:>7}ms  {r['ip'][:20]:<20}{ver:<7}{loc:<7}{r['err']}")
        if r["ok"] and r["preview"]:
            w(f"{'':18}└─ {r['preview'][:78]}")
    w("-" * 96)

    # ---- 自动推荐：可用 + 有归属地，按延迟排序 ----
    for grp in ("国内", "国际"):
        good = [x for x in results if x[1] == grp and x[3]["ok"] and x[3]["ip"]]
        good.sort(key=lambda x: x[3]["latency"])
        v4 = [x for x in good if not x[3]["v6"] and x[3]["loc"]]
        v6 = [x for x in good if x[3]["v6"]]
        w(f"\n【{grp}】★ 推荐：回 IPv4 且带归属地（按延迟升序）：")
        if v4:
            for name, _g, url, r in v4:
                w(f"   {r['latency']:>5}ms  {name:<18} {url}")
        else:
            w("   （无）")
        if v6:
            w("   —— 仅回 IPv6（交叉验证 IPv4 时信息量低）：")
            for name, _g, url, r in v6:
                w(f"   {r['latency']:>5}ms  {name:<18} {url}")

    w("\n提示：把本文件同目录下的 probe_ip_sources_out.txt 内容贴给 AI，即可据此定源。")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "probe_ip_sources_out.txt"), "w", encoding="utf-8") as f:
        f.write(out.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
