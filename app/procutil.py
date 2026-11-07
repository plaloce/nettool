"""NetTool 业务层：子进程**静默**执行工具（本模块是唯一入口）。

背景（2026-09-21 实机反馈）：打包为「无控制台 GUI」onefile 之后，**每一次**
`subprocess.run` / `Popen` 拉起控制台程序（netsh / ping / ipconfig …），
系统都会为它分配一个新控制台 —— 用户看到**一个黑窗闪一下又自动关闭**。
打开软件（网络检查页自动读网卡）、局域网扫描（每 IP 一次 ping）、
Ping 页、网关按钮、切换方案（提权子进程内的 netsh）全线中招。

根因：GUI 子系统进程拉起控制台程序时，不带 `CREATE_NO_WINDOW` 就会新建控制台窗口。
修复：**所有**子进程调用必须带 `creationflags=CREATE_NO_WINDOW`（仅 Windows 有此常量，
非 Windows 置 0 不影响）。

★ 架构约定（有守卫测试 `tests/test_silent_subprocess.py` 强制）：
`app/` 内**禁止**散落裸 `subprocess.run / Popen / call / check_output / check_call`，
一律走本模块的 `run_silent` / `popen_silent` / `SILENT_FLAGS`。
"""

from __future__ import annotations

import os
import subprocess

# Windows 下 CREATE_NO_WINDOW = 0x08000000；非 Windows 平台该常量不存在，置 0。
SILENT_FLAGS: int = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run_silent(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """`subprocess.run` 的静默版：强制带 `CREATE_NO_WINDOW`，不弹控制台窗口。"""
    kw.setdefault("creationflags", SILENT_FLAGS)
    return subprocess.run(cmd, **kw)


def popen_silent(cmd: list[str], **kw) -> subprocess.Popen:
    """`subprocess.Popen` 的静默版：强制带 `CREATE_NO_WINDOW`。"""
    kw.setdefault("creationflags", SILENT_FLAGS)
    return subprocess.Popen(cmd, **kw)
