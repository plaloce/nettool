"""NetTool 业务层：网络检查（页面四）—— 本机公网 IP、归属地、多源交叉验证、站点连通性。

设计边界（PRD-网络检查页 §1）：
- **只读**：本模块不发任何修改类请求，不碰 netsh 写入。
- **不 import Qt**：保证 pytest 可在无 GUI 环境下直接单测（项目铁律）。
- **零新增依赖**：HTTP 一律走 stdlib `urllib.request`（`requirements.txt` 仅 PySide6）。

并发模型：
    `check_all()` 用 `ThreadPoolExecutor` 并发发起三类任务（IPv4 源 / 站点探测 / 本机网卡），
    每类任务**独立超时**，任一源失败不影响其他源（PRD F4-10）。
    调用方（UI 层）负责把结果通过 Qt Signal 回主线程 —— 本模块**只做同步计算**。

★ 流式（2026-09-20 用户要求「哪个有结果就显示哪个」）：
    `check_all()` 内部用 `as_completed` 按**完成顺序**消费 future，并通过可选回调
    `on_source` / `on_site` / `on_adapter` 把**单个结果即时抛给调用方**，
    快的国内源/站点 0.3s 就能上屏，不必等 GitHub/YouTube 超时（5s）。
    ⚠️ 回调运行在**工作线程**：UI 侧必须 `Signal.emit()` 回主线程，禁止直接改控件。

★ 只刷站点（2026-09-20 用户要求「刷新按钮只用于刷新站点连通状态」）：
    `check_sites(on_site)` 只探 `NETCHECK_SITES` —— 不查 4 个数据源、不查 IPv6 兜底
    端点、不读网卡，供 E 板块标题行的独立「🔄 刷新」按钮使用。

数据源可用性（2026-09-20 改版 → 2026-09-21 改走 JSON 接口 → 2026-09-21 国内两源更换）：
    交叉验证 4 源（2 国内 + 2 国际，均带归属地）：
        IP9(国内, 中文) / PCOnline(国内, 中文)
        / Cloudflare(国际, trace 仅回 IP) / IPinfo.io(国际, 英文+ASN)
    ★ 2026-09-21 国内两源**再次更换**：此前的两个国内源经用户真机实测**双双拿不到
      结果**（其中一个的官方付费端点还需 token，零配置本地 app 不可用），已弃用
      （换源依据与旧源清单详见 PRD §6.2）。
      替代选型（已实抓验证，见 `tools/probe_ip_sources_out.txt`）：
        · IP9      → `https://ip9.com.cn/get`                      136ms，免 key JSON
        · PCOnline → `https://whois.pconline.com.cn/ipJson.jsp`    164ms，免 key JSON
       两者均回**真 IPv4 + 中文归属地**（省/市/运营商），均为纯 JSON 接口。
    ⚠️ 关键理由：本卡片交叉验证的是「公网 **IPv4**」。实测大量国际源只回 IPv6
       （Cloudflare / ip.sb / icanhazip 等），而 **IP9 与 PCOnline 是纯 IPv4 端点**，
       这是选用它们的首要原因。二者均走 JSON（utf-8），`_fetch` 的 charset 感知
       解码保留不动（向后兼容）。
    ⚠️ Cloudflare 的 cdn-cgi/trace 只回 IP（loc 字段是国家代码，非 IP 注册归属地），
       故该源只参与「IP 是否一致」交叉验证，不贡献归属地文案（属预期）。
    IPv6 兜底探测（不进卡片）：ipv6.icanhazip.com(0.78s) / v6.ident.me(~2.2s)
    排除：api.ipify.org / www.ipify.org（SSL UNEXPECTED_EOF，整族弃用）
          / ipapi.co(403) / cip.cc(HTML) / ipip.net / ip-api.com / ipwho.is（旧版源，本次改版下线）
          / 纯 IP 源 icanhazip / ident.me / ip.sb（无归属地，退出交叉验证展示）

★ 站点 favicon（2026-09-21 三级候选链，见 `fetch_favicon_bytes_chain`）：
    旧图标源 DDG（icons.duckduckgo.com）**境内被墙**，8 站点全部超时 → 图标清一色
    退化成文字占位符。现改为按链降级：① 站点本尊 `/favicon.ico` → ② 第三方镜像
    `favicon.cccyun.cc/{host}` → ③ 文字占位符（既有降级，绝不崩）。
    ⚠️ 每级结果必须过 `_is_image_bytes` **魔数校验** —— 站点本尊经常回一小段
       `text/plain` 假图（Content-Type 也撒谎），照单全收会让图标位置静默留白。
    ⚠️ favicon 全走 stdlib `urllib`（本模块**绝不 import Qt**，QPixmap 解码在 UI 侧）。
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from app.constants import (
    NETCHECK_IPV6_PROBES,
    NETCHECK_LATENCY_TIMEOUT_S,
    NETCHECK_MAX_WORKERS,
    NETCHECK_SITES,
    NETCHECK_SOURCES,
    NETCHECK_TIMEOUT_S,
)
from app.models import AdapterInfo

# --------------------------------------------------------------------------- #
# 统一 UA —— 部分源对空 UA 直接 403（`ipapi.co` 即因此被排除）
# --------------------------------------------------------------------------- #
_UA: str = "NetTool/1.3 (+local network diagnostics)"

# IPv6 地址的正则（用于判断一个字符串是不是 IPv6）
_IPV6_RE = re.compile(r"^[0-9a-fA-F:]{3,}:[0-9a-fA-F:]+$")


# =========================================================================== #
# 数据模型（运行时不落盘，故不放进 app/models.py）
# =========================================================================== #
@dataclass
class IpInfo:
    """单个 IP 的归属信息（IPv4 / IPv6 共用）。"""

    ip: str = ""
    country: str = ""
    region: str = ""
    city: str = ""
    isp: str = ""
    org: str = ""
    asn: str = ""
    source: str = ""            # 该信息来自哪个源

    @property
    def ok(self) -> bool:
        """是否拿到了有效 IP。"""
        return bool(self.ip)

    @property
    def location(self) -> str:
        """归属地展示文案，如 `中国 浙江 宁波市`。"""
        parts = [p for p in (self.country, self.region, self.city) if p]
        return " ".join(parts)

    @property
    def provider(self) -> str:
        """运营商展示文案，优先 ISP，其次 org。"""
        return self.isp or self.org

    def display_ip(self, max_len: int = 0) -> str:
        """IP 展示文案；`max_len>0` 时超长中间省略（IPv6 常超 26 字符）。"""
        if not self.ip:
            return ""
        if max_len <= 0 or len(self.ip) <= max_len:
            return self.ip
        head = max_len // 2 - 1
        tail = max_len - head - 1
        return f"{self.ip[:head]}…{self.ip[-tail:]}"


@dataclass
class SourceResult:
    """单个数据源的探测结果（供「多源交叉验证」卡片：源名 + 标签 + IP + 归属地）。"""

    name: str
    url: str = ""
    group: str = "国内"         # 国内 / 国际（UI 标签配色：蓝 / 绿）
    ok: bool = False
    elapsed_s: float = 0.0
    ip: str = ""                # 该源看到的公网 IP
    ipv6: bool = False          # 该 IP 是否为 IPv6
    loc: str = ""               # 归属地一行，如「中国 浙江 宁波」/「CN Zhejiang Ningbo」
    provider: str = ""          # 运营商一行（ISP 优先，org 兜底），如「电信」
    asn: str = ""               # 如「AS4134」（进 tooltip，不上卡片）
    err: str = ""


@dataclass
class SiteProbe:
    """单个站点的连通性探测结果。"""

    name: str
    url: str = ""
    group: str = "国内"
    ok: bool = False
    latency_ms: int | None = None
    err: str = ""


@dataclass
class NetCheckResult:
    """一次完整检查的汇总结果。"""

    ipv4: IpInfo = field(default_factory=IpInfo)
    ipv6: IpInfo = field(default_factory=IpInfo)
    sources: list[SourceResult] = field(default_factory=list)
    sites: list[SiteProbe] = field(default_factory=list)
    adapters: list[AdapterInfo] = field(default_factory=list)
    elapsed_s: float = 0.0

    # ---- 派生结论 ------------------------------------------------------ #
    @property
    def any_source_ok(self) -> bool:
        """是否至少有一个源成功。"""
        return any(s.ok for s in self.sources)

    @property
    def v4_ips(self) -> list[str]:
        """各源报告的 **IPv4** 公网 IP（去重、保持顺序）。"""
        seen: list[str] = []
        for s in self.sources:
            if s.ok and s.ip and not s.ipv6 and s.ip not in seen:
                seen.append(s.ip)
        return seen

    @property
    def v6_ips(self) -> list[str]:
        """各源报告的 **IPv6** 公网 IP（去重、保持顺序）。"""
        seen: list[str] = []
        for s in self.sources:
            if s.ok and s.ip and s.ipv6 and s.ip not in seen:
                seen.append(s.ip)
        return seen

    @property
    def agree(self) -> bool:
        """多源是否一致 —— **IPv4 与 IPv6 分开判定**。

        ⚠️ 这里曾写错：早期把 v4/v6 混在一个集合里比长度，必然得出「分歧」。
        实测就是活证据 —— 4 个源报 IPv4 `183.132.224.185`、1 个源报 IPv6
        `240e:391:...`，混在一起是 2 个不同值 → 误报「源之间存在分歧」。
        实际上**各协议族内部完全一致**，应该报「一致」。
        判定规则：每个协议族各自 ≤1 个不同值即视为一致。
        """
        return len(self.v4_ips) <= 1 and len(self.v6_ips) <= 1

    @property
    def agree_count(self) -> int:
        """参与"一致"判定的源数量（成功的源数），用于文案 `✅ {n} 个源结果一致`。"""
        return sum(1 for s in self.sources if s.ok)

    @property
    def native_ips(self) -> list[str]:
        """各源报告的公网 IP（去重、保持顺序），v4 在前。"""
        return self.v4_ips + self.v6_ips

    @property
    def intl_sites(self) -> list[SiteProbe]:
        """国际站点子集。"""
        return [s for s in self.sites if s.group == "国际"]

    @property
    def cn_sites(self) -> list[SiteProbe]:
        """国内站点子集。"""
        return [s for s in self.sites if s.group != "国际"]

    @property
    def reachable_count(self) -> int:
        """可达站点数。"""
        return sum(1 for s in self.sites if s.ok)


# =========================================================================== #
# HTTP 底层
# =========================================================================== #
def _fetch(url: str, timeout: int = NETCHECK_TIMEOUT_S) -> tuple[bool, str, float, str]:
    """GET 一个 URL，返回 `(ok, body, elapsed_s, err)`。

    **绝不抛异常** —— 所有网络/协议错误都收敛成 `ok=False`。
    仅用 stdlib，不引入 requests（项目零新增依赖约束）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        elapsed = time.monotonic() - t0
        # ★ charset 感知解码（2026-09-20 改版关键修复，★ **现役两源仍依赖**）：
        #   国内站的 IP 查询页普遍声明 `charset=gbk/gb2312`，旧写法 `raw.decode("utf-8")`
        #   会把中文归属地解成乱码 → 取不到正确的省/市。先按响应声明的 charset 解，
        #   没声明时再退而求其次：utf-8 优先，失败才用 gbk 兜底（向后兼容）。
        #   2026-09-21 国内源换成 IP9 / PCOnline（JSON 走 utf-8）后本段保留不动。
        charset = resp.headers.get_content_charset()
        if charset:
            body = raw.decode(charset, errors="replace")
        else:
            try:
                body = raw.decode("utf-8")
            except UnicodeDecodeError:
                body = raw.decode("gbk", errors="replace")
        return True, body, elapsed, ""
    except urllib.error.HTTPError as e:
        return False, "", time.monotonic() - t0, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            return False, "", time.monotonic() - t0, "超时"
        return False, "", time.monotonic() - t0, f"连接失败：{reason}"
    except (socket.timeout, TimeoutError):
        return False, "", time.monotonic() - t0, "超时"
    except ssl.SSLError as e:
        return False, "", time.monotonic() - t0, f"SSL 错误：{e}"
    except Exception as e:  # 兜底：绝不因单个源拖垮整体
        return False, "", time.monotonic() - t0, f"{type(e).__name__}: {e}"


def fetch_favicon_bytes(url: str, timeout: int = NETCHECK_TIMEOUT_S) -> bytes:
    """下载站点 favicon 的原始字节（供 UI 在**主线程外**调用，主线程再 `loadFromData`）。

    失败一律返回 `b""` —— UI 端保留文字占位符，绝不崩、绝不影响其它板块。
    favicon 是二进制，不走 `_fetch`（它返回 str）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(200_000)
    except Exception:
        return b""


# favicon 图片魔数表：(前缀, 额外校验位置)。零第三方依赖，纯字面量比对。
# ⚠️ 为什么要校验：字节跳动官网的 `/favicon.ico` 实测返回 **21 字节的 text/plain**
#   假图（Content-Type 也撒谎），不校验就会把一张废图 loadFromData 到 QPixmap，
#   表现为图标位置空白但代码"没报错"——典型的静默失败。
_PNG_MAGIC: bytes = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC: bytes = b"\xff\xd8\xff"
_GIF_MAGIC: bytes = b"GIF8"
_ICO_MAGIC: bytes = b"\x00\x00\x01\x00"
_RIFF_MAGIC: bytes = b"RIFF"
_WEBP_MAGIC: bytes = b"WEBP"
_SVG_MAGIC: bytes = b"<svg"


def _is_image_bytes(data: bytes) -> bool:
    """按**文件头魔数**判断 `data` 是否是一张真图片（不看 Content-Type，它经常撒谎）。

    支持 PNG / JPEG / GIF / ICO / WebP / SVG —— favicon 实际会出现的就这几种。
    `b""` 或过短的数据一律 False。
    """
    if not data or len(data) < 8:
        return False
    if data.startswith(_PNG_MAGIC):
        return True
    if data.startswith(_JPEG_MAGIC):
        return True
    if data.startswith(_GIF_MAGIC):
        return True
    if data.startswith(_ICO_MAGIC):
        return True
    # WebP：容器头 "RIFF"，且偏移 8 处的 FourCC 必须是 "WEBP"
    # （仅匹配 RIFF 会把 .wav/.avi 一并放行）
    if data.startswith(_RIFF_MAGIC) and data[8:12] == _WEBP_MAGIC:
        return True
    # SVG：文本格式，没有二进制魔数 —— 按「首部（跳过 BOM/空白）以 <svg 开头」
    # 或「XML 声明 + 前 512 字节内含 <svg」判定。
    # ★ 为什么必须放行 SVG（2026-09-21 实测）：`favicon.cccyun.cc` 对 github.com
    #   返回的是 SVG（`image/svg+xml`，959B），而本环境 Qt 的 svg 插件能直接渲染
    #   （`QPixmap.loadFromData` 实测 True，32×32 缩 18×18 正常）。
    #   不放行的话 GitHub 图标在**真机**上永远出不来 —— 链②的 github.com 直连
    #   在境内必超时（只有挂代理的沙箱能通），链①是它唯一的活路。
    # 严格限定 `<svg` 字面量：普通 HTML 404 页（`<!DOCTYPE html>` / `<html>`）
    # 不得被误放行为图标。
    head = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    head = head.lstrip()
    if head.startswith(_SVG_MAGIC):
        return True
    if head.startswith(b"<?xml") and _SVG_MAGIC in data[:512]:
        return True
    return False


def fetch_favicon_bytes_chain(chain: str, timeout: float = 2.0) -> bytes:
    """按**逗号分隔的候选链**依次尝试下载 favicon，返回第一张**真的是图片**的字节。

    ★ 为什么要有「候选链」（2026-09-21 用户报的 bug）：
      旧图标源 `icons.duckduckgo.com` 在境内被墙，8 个站点全部超时 →
      图标清一色退化成文字占位符。改成本函数按链降级：站点本尊 → 第三方镜像 → 占位符。

    链路语义：
    - 逐段 `strip()`，**空段跳过**（写注释或尾随逗号不应炸）；
    - 每段都过 `_is_image_bytes` 魔数校验，**校验不过立刻换下一级**；
    - 全部不过返回 `b""` —— UI 侧保留文字占位符（既有降级路径，绝不崩）。

    ⚠️ 单个 favicon 超时参数独立给 2.0s（常量默认 5s 太长）：8 站点 × 多级候选，
       每级都等 5s 会把首屏拖到十几秒。
    """
    for url in chain.split(","):
        url = url.strip()
        if not url:
            continue
        data = fetch_favicon_bytes(url, timeout=timeout)
        if _is_image_bytes(data):
            return data
    return b""


def _probe_latency(url: str, timeout: int = NETCHECK_LATENCY_TIMEOUT_S) -> tuple[bool, int | None, str]:
    """探测站点延迟（毫秒），返回 `(ok, latency_ms, err)`。

    ⚠️ 先用 HEAD 省流量；但**部分站点对 HEAD 返回 405**（实测 `weixin.qq.com`），
    故 405 / 501 时**必须回退 GET**。
    """
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, headers={"User-Agent": _UA}, method=method)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(0) if method == "HEAD" else resp.read(1024)
            return True, int((time.monotonic() - t0) * 1000), ""
        except urllib.error.HTTPError as e:
            # 405/501 = 方法不被允许 → 换 GET 重试；其它 HTTP 码（403/404）说明站点活着
            if method == "HEAD" and e.code in (405, 501, 400):
                continue
            return True, int((time.monotonic() - t0) * 1000), ""
        except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError) as e:
            if method == "HEAD":
                continue        # HEAD 被拒时也给 GET 一次机会
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
                return False, None, "超时"
            return False, None, f"{reason}"
        except Exception as e:
            return False, None, f"{type(e).__name__}: {e}"
    return False, None, "不可达"


# =========================================================================== #
# 单个数据源的解析器（各自独立，互不影响）
# =========================================================================== #
def _parse_ipinfo(body: str) -> tuple[str, dict[str, str]]:
    """`ipinfo.io` JSON → (ip, {...})。注意 `region` 是省份代码，`org` 形如 `AS4134 Chinanet`。"""
    d = json.loads(body)
    org_raw = str(d.get("org", ""))
    asn, _, isp = org_raw.partition(" ")
    return str(d.get("ip", "")), {
        "country": str(d.get("country", "")),
        "region": str(d.get("region", "")),
        "city": str(d.get("city", "")),
        "isp": isp.strip(),
        "org": "",
        "asn": asn.strip(),
    }


def _parse_plain_ip(body: str) -> tuple[str, dict[str, str]]:
    """`icanhazip` / `ident.me` / `ip.sb` 返回裸 IP 字符串。"""
    ip = body.strip().splitlines()[0].strip() if body.strip() else ""
    if not _is_ipish(ip):
        raise ValueError("返回内容不是 IP")
    return ip, {}


def _parse_ip9(body: str) -> tuple[str, dict[str, str]]:
    """`https://ip9.com.cn/get` JSON → (ip, {...})。

    真实响应：
    {"ret":200,"data":{"ip":"183.132.224.185","country":"中国","country_code":"cn",
     "prov":"浙江","city":"宁波","area":"","post_code":"315100","area_code":"0574",
     "isp":"中国电信","lng":"121.55","lat":"29.82","big_area":"华东"},"qt":0.001}
    ⚠️ ret 必须为 "200" 才算成功；data 可能缺失；**area（区县）常为空字符串**。
    ⚠️ 精度到区县级：area 非空时拼到 city 上（如 city="海曙" → "宁波海曙"），提升信息量。
    """
    d = json.loads(body)
    if str(d.get("ret")) != "200":
        raise ValueError(f"接口返回失败：ret={d.get('ret')}")
    data = d.get("data") or {}
    ip = str(data.get("ip", "")).strip()
    if not ip:
        raise ValueError("返回空 IP")
    city = str(data.get("city", "")).strip()
    area = str(data.get("area", "")).strip()
    if area and area != city:
        city = f"{city}{area}"
    return ip, {
        "country": str(data.get("country", "")).strip(),
        "region": str(data.get("prov", "")).strip(),
        "city": city,
        "isp": str(data.get("isp", "")).strip(),
        "org": "",
        "asn": "",
    }


def _parse_pconline(body: str) -> tuple[str, dict[str, str]]:
    """`https://whois.pconline.com.cn/ipJson.jsp?ip=&json=true` JSON → (ip, {...})。

    真实响应（⚠️**响应体前后堆了大量空行**，必须先 strip() 才能 json.loads）：
    {"ip":"183.132.224.185","pro":"浙江省","proCode":"330000","city":"宁波市",
     "cityCode":"330200","region":"","regionCode":"0","addr":"浙江省宁波市 电信",
     "regionNames":"","err":""}
    ⚠️ err 非空表示失败，必须抛错。
    ⚠️ pro/city 已自带「省/市」后缀；addr 是综合串（省+市+运营商），
       用它反推运营商：把 pro 和 city 从 addr 里剔掉，剩余部分即运营商。
    ⚠️ 该接口不返回 country 字段，pro 非空时 country 记为「中国」。
    """
    d = json.loads(body.strip())
    err = str(d.get("err", "")).strip()
    if err:
        raise ValueError(f"接口返回失败：{err}")
    ip = str(d.get("ip", "")).strip()
    if not ip:
        raise ValueError("返回空 IP")
    pro = str(d.get("pro", "")).strip()
    city = str(d.get("city", "")).strip()
    addr = str(d.get("addr", "")).strip()
    isp = ""
    if addr:
        tail = addr.replace(pro, "").replace(city, "").strip()
        isp = tail
    return ip, {
        "country": "中国" if pro else "",
        "region": pro,
        "city": city,
        "isp": isp,
        "org": "",
        "asn": "",
    }


def _parse_cf_trace(body: str) -> tuple[str, dict[str, str]]:
    """`cloudflare.com/cdn-cgi/trace` 纯文本 key=value → (ip, {})。

    只有 IP（trace 里的 `ip=` 字段）；`loc` 是国家代码（如 CN），**非** IP 注册归属地，
    故不把它当归属地 —— 仅作 IP 交叉验证用途。其余字段（colo/ts 等）对卡片无信息量。
    """
    ip = ""
    for line in body.splitlines():
        if line.startswith("ip="):
            ip = line.split("=", 1)[1].strip()
            break
    if not ip:
        raise ValueError("未匹配到 IP")
    return ip, {}


def _is_ipish(s: str) -> bool:
    """粗略判断是否为 IPv4 / IPv6 字面量。"""
    if not s or " " in s:
        return False
    if s.count(".") == 3:
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in s.split("."))
    return bool(_IPV6_RE.match(s))


# 源名 → (API 端点, 解析器, 是否 IPv6, 分组标签)
# ★ 4 个「带归属地」的源（2 国内 + 2 国际，借鉴 ip.skk.moe 卡片）。
#   纯 IP 源（icanhazip / ident.me / ip.sb）与 ipify 全族不再进交叉验证：
#   · ipify 全族实测 SSL UNEXPECTED_EOF（两次复现），彻底弃用；
#   · 纯 IP 源拿不到归属地，在新卡片里没有信息量。
#   IPv6 兜底改由 NETCHECK_IPV6_PROBES 承担（见 check_all）。
#   2026-09-20 用户改版：ipip.net / ip-api.com / ipwho.is 下线，国内/国际源改按
#   「带归属地的 JSON 接口 + 国际 trace」重组；此后国内源又换过一次：
#   ★ 2026-09-21 国内两源定为 IP9 / PCOnline —— 更早的两个国内源经用户真机实测
#     双双拿不到结果、且其中一个的官方端点需付费 token，已弃用（详见 PRD §6.2）：
#     · IP9      → `https://ip9.com.cn/get`                     免 key JSON，实测 136ms
#     · PCOnline → `https://whois.pconline.com.cn/ipJson.jsp`   免 key JSON，实测 164ms
#   ⚠️ 首要选型理由：本卡交叉验证的是**公网 IPv4**，而这两个是**纯 IPv4 端点**；
#      实测大量国际源（Cloudflare trace / ip.sb / icanhazip 等）只回 IPv6，
#      无法充当 IPv4 交叉验证的对照源。
#   ⚠️ 二者均为 JSON + 中文归属地（省/市/运营商），统一走 json.loads；
#      `_fetch` 的 charset 感知解码保留不动（向后兼容，且 PCOnline 响应体
#      前后堆了大量空行 → `_parse_pconline` 必须先 strip() 再 json.loads）。
_SOURCE_SPECS: dict[str, tuple[str, Any, bool, str]] = {
    "IP9": ("https://ip9.com.cn/get", _parse_ip9, False, "国内"),
    "PCOnline": ("https://whois.pconline.com.cn/ipJson.jsp?ip=&json=true", _parse_pconline, False, "国内"),
    "Cloudflare": ("https://www.cloudflare.com/cdn-cgi/trace", _parse_cf_trace, False, "国际"),
    "IPinfo.io": ("https://ipinfo.io/json", _parse_ipinfo, False, "国际"),
}


def _query_source(name: str) -> tuple[SourceResult, IpInfo | None]:
    """查询单个源。返回 `(SourceResult, 可选的 IpInfo)`。"""
    url, parser, is_v6, group = _SOURCE_SPECS[name]
    sr = SourceResult(name=name, url=url, group=group)
    ok, body, elapsed, err = _fetch(url)
    sr.elapsed_s = round(elapsed, 2)
    if not ok:
        sr.err = err
        return sr, None
    try:
        ip, extra = parser(body)
    except Exception as e:
        sr.err = f"解析失败：{e}"
        return sr, None
    if not ip:
        sr.err = "返回空"
        return sr, None
    sr.ok = True
    sr.ip = ip
    sr.ipv6 = is_v6 or bool(_IPV6_RE.match(ip))
    # 归属地 / 运营商 / ASN（供卡片三行式展示；缺省留空，UI 显示占位符）
    sr.loc = " ".join(
        p for p in (extra.get("country"), extra.get("region"), extra.get("city")) if p
    )
    sr.provider = extra.get("isp") or extra.get("org") or ""
    sr.asn = extra.get("asn") or ""
    return sr, IpInfo(ip=ip, source=name, **extra)


def _probe_site(site: dict[str, str]) -> SiteProbe:
    """探测单个站点。"""
    ok, ms, err = _probe_latency(site["url"])
    return SiteProbe(
        name=site["name"],
        url=site["url"],
        group=site.get("group", "国内"),
        ok=ok,
        latency_ms=ms,
        err="" if ok else err,
    )


# =========================================================================== #
# 服务类
# =========================================================================== #
class NetCheckService:
    """网络检查服务（只读，线程安全，不 import Qt）。"""

    def __init__(self, max_workers: int = NETCHECK_MAX_WORKERS) -> None:
        self._max_workers = max(1, max_workers)

    # ------------------------------------------------------------------ #
    def check_all(
        self,
        netsh_service: Any = None,
        on_source: Callable[[SourceResult, IpInfo, IpInfo], None] | None = None,
        on_site: Callable[[SiteProbe], None] | None = None,
        on_adapter: Callable[[list[AdapterInfo]], None] | None = None,
    ) -> NetCheckResult:
        """执行一次完整检查（阻塞，请放工作线程）。

        `netsh_service` 传入时顺带读取本机网卡信息（含 DNS）；传 None 则跳过该板块。
        所有子任务独立超时，任一失败不影响其它（F4-10）。

        ★ 流式回调（2026-09-20 用户要求「哪个有结果就显示哪个，不用等别人」）：
        - `on_source(sr, ipv4, ipv6)`：每完成**一个源**就回调一次，`ipv4/ipv6` 是
          **截至此刻**已收集结果里的最佳 IP（用于让 IP 卡片也逐源更新）
        - `on_site(probe)`：每完成**一个站点**就回调一次
        - `on_adapter(adapters)`：网卡读取完成后回调一次
        ⚠️ 回调在**工作线程**执行：UI 侧必须用 `Signal.emit()` 回主线程，禁止直接改控件。
        """
        t0 = time.monotonic()
        result = NetCheckResult()

        ipv4_infos: list[IpInfo] = []
        ipv6_infos: list[IpInfo] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            # ---- 并发发起：所有源 + 所有站点 + IPv6 兜底探测 + 网卡读取 ----
            src_names = [n for n, _u, _g in NETCHECK_SOURCES if n in _SOURCE_SPECS]
            # ★ 统一放进一张 future 表，用 as_completed 按**完成顺序**消费 ——
            #   这样快的任务（国内源/站点）不必等慢的（GitHub/YouTube 超时）就能先回调。
            jobs: dict[Any, tuple[str, Any]] = {}
            for n in src_names:
                jobs[pool.submit(_query_source, n)] = ("source", n)
            for s in NETCHECK_SITES:
                jobs[pool.submit(_probe_site, s)] = ("site", s)
            # IPv6 兜底探测：与源并发发起（仅当没有任何源带回 IPv6 时才采用结果）
            for u in NETCHECK_IPV6_PROBES:
                jobs[pool.submit(_fetch, u)] = ("v6", u)
            if netsh_service:
                jobs[pool.submit(self._read_adapters, netsh_service)] = ("adapter", None)

            v6_probes: list[tuple[Any, str]] = []

            for fut in concurrent.futures.as_completed(jobs):
                kind, payload = jobs[fut]
                try:
                    if kind == "source":
                        sr, info = fut.result()
                        result.sources.append(sr)
                        if info is not None:
                            (ipv6_infos if sr.ipv6 else ipv4_infos).append(info)
                        if on_source is not None:
                            on_source(sr, self._pick_best(ipv4_infos),
                                      self._pick_best(ipv6_infos))
                    elif kind == "site":
                        probe = fut.result()
                        result.sites.append(probe)
                        if on_site is not None:
                            on_site(probe)
                    elif kind == "adapter":
                        adapters = fut.result()
                        result.adapters = adapters
                        if on_adapter is not None:
                            on_adapter(adapters)
                    else:                                   # v6 兜底，最后统一处理
                        v6_probes.append((fut, payload))
                except Exception as e:                      # 兜底，绝不因单项炸掉整体
                    if kind == "source":
                        sr = SourceResult(name=payload, err=f"{type(e).__name__}: {e}")
                        result.sources.append(sr)
                        if on_source is not None:
                            on_source(sr, self._pick_best(ipv4_infos),
                                      self._pick_best(ipv6_infos))
                    elif kind == "site":
                        probe = SiteProbe(
                            name=payload["name"], url=payload["url"],
                            group=payload.get("group", "国内"),
                            err=f"{type(e).__name__}: {e}",
                        )
                        result.sites.append(probe)
                        if on_site is not None:
                            on_site(probe)
                    elif kind == "adapter":
                        result.adapters = []
                        if on_adapter is not None:
                            on_adapter([])

            # ---- IPv6 兜底：4 个源都没带回 IPv6 时，采用探测端点的结果 ----
            if not ipv6_infos:
                for fut, url in v6_probes:
                    try:
                        ok, body, _els, _err = fut.result()
                    except Exception:
                        continue
                    if not ok:
                        continue
                    try:
                        ip, _extra = _parse_plain_ip(body)
                    except Exception:
                        continue
                    if _IPV6_RE.match(ip):
                        ipv6_infos.append(IpInfo(ip=ip, source=url))
                        break

        # ---- 归并出首选 IPv4 / IPv6 ----
        result.ipv4 = self._pick_best(ipv4_infos)
        result.ipv6 = self._pick_best(ipv6_infos)

        # 保持展示顺序与常量清单一致（as_completed 的完成顺序是随机的，不能直接展示）
        order = {n: i for i, (n, _u, _g) in enumerate(NETCHECK_SOURCES)}
        result.sources.sort(key=lambda s: order.get(s.name, 999))
        site_order = {s["name"]: i for i, s in enumerate(NETCHECK_SITES)}
        result.sites.sort(key=lambda s: site_order.get(s.name, 999))

        result.elapsed_s = round(time.monotonic() - t0, 2)
        return result

    # ------------------------------------------------------------------ #
    def check_sites(
        self,
        on_site: Callable[[SiteProbe], None] | None = None,
    ) -> list[SiteProbe]:
        """**只**探测站点连通性（阻塞，请放工作线程）—— E 板块「🔄 刷新」按钮专用。

        与 `check_all()` 的区别（用户明确要求「刷新按钮只刷新站点连通状态」）：
        - **不查**多源交叉验证的 4 个数据源（`_query_source`）
        - **不查** IPv6 兜底端点（`NETCHECK_IPV6_PROBES`）
        - **不读**本机网卡（`_read_adapters` / netsh）—— 完全不发本地命令

        并发模型与 `check_all()` 一致：`ThreadPoolExecutor` 一次全部发出，
        用 `as_completed` 按**完成顺序**消费，逐个回调 `on_site(probe)`
        （快的站点 0.1s 上屏，不必等 GitHub/YouTube 超时）。

        ⚠️ 回调运行在**工作线程**：UI 侧必须 `Signal.emit()` 回主线程，禁止直接改控件。
        ⚠️ `as_completed` 的完成顺序是随机的，返回值**必须**按 `NETCHECK_SITES`
           声明顺序重排，否则 UI 展示顺序会乱（与 `check_all` 末尾同款 sort）。
        ⚠️ 单个站点异常在内部兜底（产出 `ok=False` 的 SiteProbe 并照常回调），
           绝不因一个站点炸掉整批。
        """
        sites: list[SiteProbe] = []
        workers = max(1, min(len(NETCHECK_SITES), NETCHECK_MAX_WORKERS))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            jobs: dict[Any, dict[str, str]] = {
                pool.submit(_probe_site, s): s for s in NETCHECK_SITES
            }
            for fut in concurrent.futures.as_completed(jobs):
                site = jobs[fut]
                try:
                    probe = fut.result()
                except Exception as e:                  # 兜底：绝不因单项炸掉整批
                    probe = SiteProbe(
                        name=site["name"],
                        url=site["url"],
                        group=site.get("group", "国内"),
                        ok=False,
                        err=f"{type(e).__name__}: {e}",
                    )
                sites.append(probe)
                if on_site is not None:
                    on_site(probe)

        # 展示顺序必须回到常量清单顺序（as_completed 会打乱 append 顺序）
        site_order = {s["name"]: i for i, s in enumerate(NETCHECK_SITES)}
        sites.sort(key=lambda p: site_order.get(p.name, 999))
        return sites

    # ------------------------------------------------------------------ #
    def check_sources(
        self,
        on_source: Callable[[SourceResult, IpInfo, IpInfo], None] | None = None,
    ) -> list[SourceResult]:
        """**只**查询多源交叉验证（阻塞，请放工作线程）—— C 卡「🔄 刷新」按钮专用。

        与 `check_all()` 的区别（用户要求「该按钮只刷新此卡片内的数据」）：
        - **不查**站点连通性（`_probe_site`）
        - **不查** IPv6 兜底端点（`NETCHECK_IPV6_PROBES`）
        - **不读**本机网卡（`_read_adapters` / netsh）

        并发模型与 `check_all()` 一致：`ThreadPoolExecutor` 一次全部发出，用
        `as_completed` 按**完成顺序**消费，逐个回调
        `on_source(sr, ipv4, ipv6)`（快的源先上屏，不必等慢的源超时）。

        ⚠️ 回调运行在**工作线程**：UI 侧必须 `Signal.emit()` 回主线程，禁止直接改控件。
        ⚠️ `as_completed` 的完成顺序是随机的，返回值**必须**按 `NETCHECK_SOURCES`
           声明顺序重排，否则 UI 展示顺序会乱（与 `check_all` 末尾同款 sort）。
        ⚠️ 单个源异常在内部兜底（产出 `ok=False` 的 SourceResult 并照常回调），
           绝不因一个源炸掉整批。
        """
        src_names = [n for n, _u, _g in NETCHECK_SOURCES if n in _SOURCE_SPECS]
        results: list[SourceResult] = []
        v4: list[IpInfo] = []
        v6: list[IpInfo] = []
        workers = max(1, min(len(src_names), NETCHECK_MAX_WORKERS))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = {pool.submit(_query_source, n): n for n in src_names}
            for fut in concurrent.futures.as_completed(jobs):
                name = jobs[fut]
                try:
                    sr, info = fut.result()
                except Exception as e:                  # 兜底：绝不因单项炸掉整批
                    sr = SourceResult(name=name, err=f"{type(e).__name__}: {e}")
                    info = None
                results.append(sr)
                if info is not None:
                    (v6 if sr.ipv6 else v4).append(info)
                if on_source is not None:
                    on_source(sr, self._pick_best(v4), self._pick_best(v6))

        # 展示顺序必须回到常量清单顺序（as_completed 会打乱 append 顺序）
        order = {n: i for i, (n, _u, _g) in enumerate(NETCHECK_SOURCES)}
        results.sort(key=lambda s: order.get(s.name, 999))
        return results

    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_adapters(netsh_service: Any) -> list[AdapterInfo]:
        """读取本机网卡（复用既有 `NetshService.list_adapters()`，零新增系统调用）。"""
        try:
            adapters = netsh_service.list_adapters()
        except Exception:
            return []
        return [a for a in adapters if a.is_up and a.ip]

    @staticmethod
    def _pick_best(infos: list[IpInfo]) -> IpInfo:
        """从多个源的 IP 信息中挑「信息最全」的一条作为主展示。

        评分：有归属地 +3 / 有 ISP +2 / 有 ASN +1。
        全是空则返回空 IpInfo（UI 显示「未获取到」）。
        """
        if not infos:
            return IpInfo()

        def score(i: IpInfo) -> int:
            s = 0
            if i.location:
                s += 3
            if i.provider:
                s += 2
            if i.asn:
                s += 1
            return s

        return max(infos, key=score)
