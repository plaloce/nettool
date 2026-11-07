"""网络检查服务层 —— 真实网络联通测试（不 mock，打真实源）。

用法：python tools/test_netcheck_live.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.netcheck_service import NetCheckService  # noqa: E402


def main() -> int:
    print("=" * 78)
    print("网络检查服务层 · 真实联通测试")
    print("=" * 78)

    t0 = time.monotonic()
    svc = NetCheckService(max_workers=10)

    # 不带 netsh（先验证纯网络部分），再单独验证网卡部分
    result = svc.check_all(netsh_service=None)
    wall = time.monotonic() - t0

    print(f"\n【多源查询】内部耗时 {result.elapsed_s}s / 墙钟 {wall:.2f}s")
    print(f"{'源':<18} {'状态':<6} {'耗时':>7}  {'IP':<42} 备注")
    print("-" * 100)
    for s in result.sources:
        status = "✔ OK" if s.ok else "✘ FAIL"
        ip = s.ip or "--"
        note = s.err or ("IPv6" if s.ipv6 else "")
        print(f"{s.name:<18} {status:<6} {s.elapsed_s:>6.2f}s  {ip:<42} {note}")

    print(f"\n【站点连通性】可达 {result.reachable_count}/{len(result.sites)}")
    print(f"{'站点':<14} {'分组':<6} {'状态':<6} {'延迟':>8}  备注")
    print("-" * 70)
    for s in result.sites:
        status = "✔ OK" if s.ok else "✘ FAIL"
        ms = f"{s.latency_ms} ms" if s.latency_ms is not None else "--"
        print(f"{s.name:<14} {s.group:<6} {status:<6} {ms:>8}  {s.err}")

    print("\n【归并结论】")
    print(f"  IPv4 主展示 : {result.ipv4.ip or '(空)'}")
    print(f"    归属地    : {result.ipv4.location or '(空)'}")
    print(f"    运营商    : {result.ipv4.provider or '(空)'}")
    print(f"    ASN       : {result.ipv4.asn or '(空)'}")
    print(f"    来源      : {result.ipv4.source or '(空)'}")
    print(f"  IPv6 主展示 : {result.ipv6.ip or '(空)'}")
    print(f"    归属地    : {result.ipv6.location or '(空)'}")
    print(f"    来源      : {result.ipv6.source or '(空)'}")
    print(f"  各源 IP 集合: {result.native_ips}")
    print(f"  多源一致    : {result.agree}")
    print(f"  任一源成功  : {result.any_source_ok}")
    if result.ipv6.ip:
        print(f"  IPv6 截断显示: {result.ipv6.display_ip(26)}")

    # ---- 网卡部分（走真实 netsh）----
    print("\n【本机网卡】（走真实 netsh）")
    from app.netsh_service import NetshService

    netsh = NetshService()
    adapters = NetCheckService._read_adapters(netsh)
    if not adapters:
        print("  (无可用网卡)")
    for a in adapters:
        print(f"  {a.name:<20} ip={a.ip:<16} mask={a.mask:<16} "
              f"gw={a.gateway:<16} dhcp={a.is_dhcp}")

    print("\n" + "=" * 78)
    ok_src = sum(1 for s in result.sources if s.ok)
    print(f"结论：源 {ok_src}/{len(result.sources)} 可用，"
          f"站点 {result.reachable_count}/{len(result.sites)} 可达")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
