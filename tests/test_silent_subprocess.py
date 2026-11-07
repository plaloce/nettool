"""★ 守卫：app/ 内所有子进程调用必须走 `app.procutil` 静默封装。

背景（2026-09-21 用户实机反馈，P0 体验问题）：打包为「无控制台 GUI」onefile 后，
每一次 `subprocess.run` / `Popen` 拉起控制台程序（netsh / ping / ipconfig…），
系统都会弹出**一个黑窗闪一下又自动关闭** —— 打开软件、扫描、Ping、网关按钮、
网络检查、切换方案全线中招。修复 = 全部子进程调用带 `CREATE_NO_WINDOW`。

本测试防两类回归：
1. 有人新写裸 `subprocess.run/Popen/call/check_call/check_output`
   或 `from subprocess import …`（AST 静态扫描，除 `app/procutil.py` 外一律禁止）；
2. 已知调用点被改回裸调用（按函数级锚定断言，防"文件里别处还有"的空壳断言）。
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_FORBIDDEN_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}


def _app_py_files() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


# --------------------------------------------------------------------------- #
# 规则 1：app/ 内（除 procutil.py 自身）禁止任何裸 subprocess 调用/导入
# --------------------------------------------------------------------------- #
def test_no_bare_subprocess_calls_in_app():
    offenders: list[str] = []
    for path in _app_py_files():
        if path.name == "procutil.py":
            continue  # 封装模块本身允许裸调用（它在内部统一加 creationflags）
        # utf-8-sig：styles.py 等带 BOM 的文件，ast.parse 会把 \ufeff 当非法字符
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            # 1) `from subprocess import run, ...` 直接导入
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                offenders.append(f"{path.name}:{node.lineno} from subprocess import ...")
            # 2) `subprocess.run(...)` / `subprocess.Popen(...)` 等属性调用
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _FORBIDDEN_FUNCS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                offenders.append(
                    f"{path.name}:{node.lineno} subprocess.{node.func.attr}()"
                )
    assert not offenders, (
        "发现裸子进程调用 —— 打包成无控制台 GUI 后会闪黑窗，"
        "必须改走 app.procutil.run_silent / popen_silent：\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 规则 2：procutil 本身的契约
# --------------------------------------------------------------------------- #
def test_silent_flags_is_create_no_window_on_windows():
    from app.procutil import SILENT_FLAGS

    if os.name == "nt":
        assert SILENT_FLAGS == subprocess.CREATE_NO_WINDOW
        assert SILENT_FLAGS == 0x08000000
    else:
        assert SILENT_FLAGS == 0


def test_run_silent_forwards_creationflags(monkeypatch):
    import app.procutil as pu

    captured: dict = {}

    def fake_run(cmd, **kw):
        captured.update(kw)
        return "ok"

    monkeypatch.setattr(pu.subprocess, "run", fake_run)
    assert pu.run_silent(["netsh", "x"], capture_output=True, shell=False) == "ok"
    assert captured["creationflags"] == pu.SILENT_FLAGS
    assert captured["shell"] is False


def test_popen_silent_forwards_creationflags(monkeypatch):
    import app.procutil as pu

    captured: dict = {}

    def fake_popen(cmd, **kw):
        captured.update(kw)
        return "proc"

    monkeypatch.setattr(pu.subprocess, "Popen", fake_popen)
    assert pu.popen_silent(["explorer.exe"], shell=False) == "proc"
    assert captured["creationflags"] == pu.SILENT_FLAGS


# --------------------------------------------------------------------------- #
# 规则 3：已知调用点按函数级锚定（防"别处还有 run_silent"的空壳断言）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "get_func, expect_wrapper",
    [
        (lambda: __import__("app.elevation", fromlist=["run_local_netsh"]).run_local_netsh,
         "run_silent"),
        (lambda: __import__("app.netsh_service", fromlist=["NetshService"]).NetshService._list_interface_names,
         "run_silent"),
        (lambda: __import__("app.netsh_service", fromlist=["NetshService"]).NetshService.read_config,
         "run_silent"),
        (lambda: __import__("app.ping_service", fromlist=["ping_once"]).ping_once,
         "run_silent"),
        (lambda: __import__("app.ui.page_scan", fromlist=["PageScan"]).PageScan.open_share,
         "popen_silent"),
    ],
    ids=[
        "elevation.run_local_netsh",
        "netsh_service._list_interface_names",
        "netsh_service.read_config",
        "ping_service.ping_once",
        "page_scan.open_share",
    ],
)
def test_known_call_sites_use_silent_wrapper(get_func, expect_wrapper):
    src = inspect.getsource(get_func())
    assert f"{expect_wrapper}(" in src, (
        f"该调用点未使用 {expect_wrapper}（函数体内找不到 '{expect_wrapper}('）"
    )
