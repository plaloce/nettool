"""P0-5 · 提权模块（白名单校验 / 防注入 / 降级路径 / 编解码）。

不真的提权；只测纯逻辑与结构化失败返回。
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from app.constants import ELEV_FLAG, ELEV_RESULT_FLAG
from app.elevation import (
    ExecResult,
    _candidate_encodings,
    _decode_cmds,
    _encode_cmds,
    _validate_cmds,
    decode_console_bytes,
    is_admin,
    run_local_netsh,
)


# --------------------------------------------------------------------------- #
# is_admin
# --------------------------------------------------------------------------- #
def test_is_admin_returns_bool_no_raise():
    v = is_admin()
    assert isinstance(v, bool)


# --------------------------------------------------------------------------- #
# 白名单校验
# --------------------------------------------------------------------------- #
def test_validate_legal_netsh():
    _validate_cmds([["netsh", "interface", "ip", "set", "address",
                     "name=以太网", "static", "192.168.1.100",
                     "255.255.255.0", "192.168.1.1"]])  # 不抛异常即通过


def test_validate_accepts_netsh_exe_path():
    _validate_cmds([["C:\\Windows\\System32\\netsh.exe", "interface", "show", "interface"]])


@pytest.mark.parametrize("bad", [
    [["rm", "-rf", "/"]],
    [["calc.exe"]],
    [["powershell", "-Command", "whoami"]],
    [["cmd", "/c", "dir"]],
    [[]],
    [],
])
def test_validate_rejects_non_netsh(bad):
    with pytest.raises(ValueError):
        _validate_cmds(bad)


@pytest.mark.parametrize("danger", ["&", "|", ";", ">", "<", "`", "$", "\n", "\r"])
def test_validate_rejects_injection_chars(danger):
    cmd = ["netsh", "interface", "ip", "set", "address", f"name=a{danger}b"]
    with pytest.raises(ValueError):
        _validate_cmds([cmd])


def test_validate_rejects_ampersand_injection_real_case():
    with pytest.raises(ValueError):
        _validate_cmds([["netsh", "interface", "ip", "set", "address",
                         "name=1.1.1.1 & calc"]])


# --------------------------------------------------------------------------- #
# 编解码往返
# --------------------------------------------------------------------------- #
def test_encode_decode_roundtrip():
    cmds = [["netsh", "interface", "ip", "set", "address",
             "name=以太网 2", "static", "192.168.1.100", "255.255.255.0", "192.168.1.1"]]
    token = _encode_cmds(cmds)
    assert isinstance(token, str)
    assert _decode_cmds(token) == cmds


def test_decode_bad_token_raises():
    with pytest.raises(Exception):
        _decode_cmds("!!!not-base64!!!")


# --------------------------------------------------------------------------- #
# 降级路径：非法命令 → 结构化失败，不抛到调用方
# --------------------------------------------------------------------------- #
def test_run_local_netsh_rejects_illegal():
    r = run_local_netsh([["calc.exe"]])
    assert isinstance(r, ExecResult)
    assert r.ok is False
    assert r.error


def test_run_elevated_netsh_rejects_illegal_without_uac(monkeypatch):
    """非法命令应在提权之前被拦下，返回 ok=False，不弹 UAC。"""
    import app.elevation as elev

    called = {"shell": False}

    def fake_shellexecute(*a, **k):
        called["shell"] = True
        return 42

    monkeypatch.setattr(elev.ctypes.windll.shell32, "ShellExecuteW", fake_shellexecute)
    r = elev.run_elevated_netsh([["rm", "-rf", "/"]])
    assert r.ok is False
    assert called["shell"] is False      # 根本没调用 ShellExecuteW


def test_execresult_brief():
    assert ExecResult(ok=False, error="boom").brief() == "boom"
    assert ExecResult(ok=True, stdout=" done \n").brief() == "done"


# --------------------------------------------------------------------------- #
# 控制台解码
# --------------------------------------------------------------------------- #
def test_decode_utf8_multibyte():
    b = "中文测试".encode("utf-8")
    assert decode_console_bytes(b) == "中文测试"


def test_decode_gbk_bytes():
    b = "请求超时".encode("gbk")
    out = decode_console_bytes(b)
    assert "超时" in out or out  # 至少不抛异常且非空


def test_decode_empty():
    assert decode_console_bytes(b"") == ""
    assert decode_console_bytes(None) == ""


def test_candidate_encodings_non_empty():
    encs = _candidate_encodings()
    assert encs
    assert "utf-8" in encs


# --------------------------------------------------------------------------- #
# ★ 回归守卫：提权命令行拼装 + 子进程入口（2026-09-21 由实机 exe 验证暴露的 P0）
#
# 历史缺陷：`app/elevation.py` 使用了 `ELEV_FLAG` 却漏了 import —— 第 266/273 行
# （父进程拼 ShellExecuteW 参数）与第 300/302 行（子进程入口判参）全部 NameError，
# 也就是说**非管理员下的所有 netsh 写操作（应用方案/重置 DHCP/刷新 DNS）一律失败**。
#
# 全套测试为何没抓到：本节原唯一涉及 `run_elevated_netsh` 的用例
# （`test_run_elevated_netsh_rejects_illegal_without_uac`）传的是**非法命令**，
# 在 `_validate_cmds` 就 return 了，永远走不到那几行 f-string。
# ⇒ 守卫必须跑在**合法命令**路径上，否则就是抓不住回归的空壳。
# --------------------------------------------------------------------------- #
def test_run_elevated_netsh_legal_cmds_builds_params(monkeypatch):
    """合法命令要真的走到 ShellExecuteW，且命令行带上两个约定 flag 与命令载荷。

    原缺陷下本用例以 `NameError: name 'ELEV_FLAG' is not defined` 失败。
    """
    import app.elevation as elev

    captured = {}

    def fake_shellexecute(hwnd, verb, exe, params, cwd, show):
        captured["params"] = params
        captured["exe"] = exe
        return 42  # > ELEV_SHELL_SUCCESS_MIN(32) ⇒ 视为已成功拉起

    monkeypatch.setattr(elev.ctypes.windll.shell32, "ShellExecuteW", fake_shellexecute)
    monkeypatch.setattr(
        elev, "_poll_result_file", lambda path, timeout: ExecResult(ok=True, stdout="ok")
    )

    cmds = [["netsh", "interface", "show", "interface"]]
    r = elev.run_elevated_netsh(cmds)

    assert r.ok is True
    assert ELEV_FLAG in captured["params"]
    assert ELEV_RESULT_FLAG in captured["params"]
    assert _encode_cmds(cmds) in captured["params"]  # 命令载荷确实带上了


def test_run_as_elevated_child_writes_result_file(monkeypatch):
    """子进程入口完整往返：解码 → 执行 → 写 UTF-8 JSON 结果文件。

    原缺陷下第 300 行 `ELEV_FLAG not in argv` 直接 NameError，
    且该 NameError **不被** `except (IndexError, ValueError)` 捕获，会一路冒泡。
    """
    import app.elevation as elev

    canned = ExecResult(ok=True, returncode=0, stdout="已设置")
    monkeypatch.setattr(elev, "run_local_netsh", lambda cmds: canned)

    cmds = [
        ["netsh", "interface", "ip", "set", "address", "name=以太网",
         "static", "192.168.1.10", "255.255.255.0", "192.168.1.1"]
    ]

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "result.json")
        argv = ["NetTool.exe", ELEV_FLAG, _encode_cmds(cmds), ELEV_RESULT_FLAG, path]

        rc = elev.run_as_elevated_child(argv)

        assert rc == 0
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["ok"] is True
        assert payload["stdout"] == "已设置"  # ensure_ascii=False，中文不被转义


def test_run_as_elevated_child_without_flag_returns_2():
    """缺 `--exec-netsh` 时应返回 2（原缺陷下这里是 NameError 而不是 2）。"""
    import app.elevation as elev

    assert elev.run_as_elevated_child(["NetTool.exe"]) == 2
