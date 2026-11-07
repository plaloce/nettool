"""NetTool 业务层：提权（路径B）+ 子进程执行器（架构第 8 章，风险4）。

设计要点：
- **双入口**：`main.py` 既是 GUI 入口，也是提权子进程入口（靠 `--exec-netsh` 区分）。
- 父进程：`ShellExecuteW runas` 拉起本程序自身；子进程执行 netsh 后把结果写 JSON；
  父进程轮询读取（本模块提供**同步**版 `run_elevated_netsh`，UI 通过工作线程调用以避免阻塞）。
- 用户拒绝 UAC 会抛 `OSError`（WinError 1223），统一 `try/except` 捕获并降级，**不崩溃**。
- 命令必须先过 `_validate_cmds` 白名单校验；执行一律 `shell=False` + 参数列表。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.constants import (
    ELEV_FLAG,
    ELEV_POLL_INTERVAL_MS,
    ELEV_RESULT_FLAG,
    ELEV_SHELL_SUCCESS_MIN,
    ELEV_TIMEOUT_S,
    MSG_NEED_ADMIN,
)
from app.netsh_parser import parse_set_result
from app.paths import elev_result_path
from app.procutil import run_silent

# netsh 命令允许的第一个 token
_ALLOWED_CMD_HEAD: str = "netsh"
# 命令参数中禁止出现的危险字符（防注入；参数列表已规避，但仍二次校验）
_FORBIDDEN_CHARS: tuple[str, ...] = ("&", "|", ";", ">", "<", "`", "$", "\n", "\r")


@dataclass
class ExecResult:
    """netsh（或其它提权操作）的执行结果。"""

    ok: bool
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    error: str = ""          # 面向用户的可读错误

    def brief(self) -> str:
        """用于日志/提示的简要文本。"""
        if self.error:
            return self.error
        return (self.stdout or self.stderr or "").strip()


def is_admin() -> bool:
    """当前进程是否已提权（管理员）。异常时返回 False。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _validate_cmds(cmds: list[list[str]]) -> None:
    """白名单校验：每条命令必须是参数列表，且首元素为 `netsh`，参数不含危险字符。

    Raises:
        ValueError: 校验不通过。
    """
    if not isinstance(cmds, list) or not cmds:
        raise ValueError("命令列表为空")
    for cmd in cmds:
        if not isinstance(cmd, (list, tuple)) or not cmd:
            raise ValueError("命令必须是非空参数列表")
        head = str(cmd[0]).strip().lower()
        # 允许 "netsh" 或形如 "C:\\Windows\\System32\\netsh.exe"
        if head != _ALLOWED_CMD_HEAD and not head.endswith("netsh.exe"):
            raise ValueError(f"仅允许执行 netsh 命令，收到：{cmd[0]!r}")
        for token in cmd:
            token_s = str(token)
            if any(ch in token_s for ch in _FORBIDDEN_CHARS):
                raise ValueError(f"命令参数包含非法字符：{token_s!r}")


def _encode_cmds(cmds: list[list[str]]) -> str:
    """把命令列表编码为 base64(json)，避免命令行引号/中文/空格转义问题。"""
    payload = json.dumps(cmds, ensure_ascii=False)
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cmds(token: str) -> list[list[str]]:
    """解码 base64(json) 命令列表。"""
    raw = base64.b64decode(token.encode("ascii")).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("命令载荷格式非法")
    return [[str(x) for x in cmd] for cmd in data]


def _candidate_encodings() -> list[str]:
    """构造解码候选编码序列（Windows 上优先系统 ANSI/OEM 代码页）。

    重要：Python 3.13 在 Windows 上可能以 UTF-8 模式运行
    （`sys.flags.utf8_mode == 1`），此时 `locale.getpreferredencoding(False)`
    会返回 `'utf-8'`，而系统控制台/子进程实际使用 CP936(GBK)——若只用前者会乱码。
    因此这里**优先使用 Windows 的 OEM/ANSI 代码页**（子进程输出走管道时为 OEM 码页），
    再回退到 locale 首选编码、utf-8，最后 `errors="replace"` 兜底。
    """
    import locale

    candidates: list[str] = []

    # 1) Windows 代码页（最可靠）：OEM 码页 -> ANSI 码页
    try:
        import ctypes

        oem = int(ctypes.windll.kernel32.GetOEMCP())   # type: ignore[attr-defined]
        acp = int(ctypes.windll.kernel32.GetACP())     # type: ignore[attr-defined]
        for cp in (oem, acp):
            if cp and cp not in (65001,):  # 65001=utf-8，本就不是这里的目标
                candidates.append(f"cp{cp}")
    except Exception:
        pass

    # 2) locale 首选编码（非 Windows 或调用失败时）
    try:
        preferred = locale.getpreferredencoding(False)
        if preferred:
            candidates.append(preferred)
    except Exception:
        pass

    # 3) utf-8
    candidates.append("utf-8")
    # 4) 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for enc in candidates:
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(enc)
    return uniq


def decode_console_bytes(b: bytes | None) -> str:
    """按「UTF-8 严格探测 -> Windows OEM/ANSI 码页 -> locale -> 兜底」解码。

    见架构 7.4 / 13.4：**禁止** `text=True` + 默认编码，**禁止**硬编 utf-8 单语言。

    策略：先严格尝试 UTF-8（仅在含多字节序列时判定有效，避免把纯 ASCII 误判），
    有效的 UTF-8 优先采用；否则按系统代码页（GBK 等）解码。GBK 几乎不抛异常，
    故必须在它之前先排除「确为 UTF-8」的情况。
    """
    if not b:
        return ""

    # 1) 严格 UTF-8 探测：仅当存在多字节字符且能完整解码时才认为输出是 UTF-8
    try:
        has_multibyte = any(byte >= 0x80 for byte in b)
        if has_multibyte:
            decoded = b.decode("utf-8")
            return decoded
    except UnicodeDecodeError:
        pass

    # 2) 系统代码页 -> locale -> utf-8(replace)
    for enc in _candidate_encodings():
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", errors="replace")


def run_local_netsh(cmds: list[list[str]]) -> ExecResult:
    """在当前进程（已提权时）顺序执行一组 netsh 命令（`shell=False` 参数列表）。"""
    try:
        _validate_cmds(cmds)
    except ValueError as e:
        return ExecResult(ok=False, error=str(e))

    out_parts: list[str] = []
    err_parts: list[str] = []
    rc = -1
    for cmd in cmds:
        try:
            proc = run_silent(cmd, capture_output=True, shell=False)  # noqa: S603
        except OSError as e:
            return ExecResult(
                ok=False, returncode=-1, stdout="".join(out_parts),
                stderr="".join(err_parts), error=f"执行失败：{e}",
            )
        out_parts.append(decode_console_bytes(proc.stdout))
        err_parts.append(decode_console_bytes(proc.stderr))
        rc = proc.returncode
        if rc != 0:
            break

    ok, snippet = parse_set_result("".join(out_parts) + "".join(err_parts), rc)
    result = ExecResult(
        ok=ok,
        returncode=rc,
        stdout="".join(out_parts),
        stderr="".join(err_parts),
        error="" if ok else snippet,
    )
    return result


def _poll_result_file(path: str, timeout_s: int) -> ExecResult:
    """轮询结果文件直到出现或超时（200ms 间隔）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                try:
                    os.remove(path)
                except OSError:
                    pass
                return ExecResult(
                    ok=bool(data.get("ok", False)),
                    returncode=int(data.get("returncode", -1)),
                    stdout=str(data.get("stdout", "")),
                    stderr=str(data.get("stderr", "")),
                    error=str(data.get("error", "")),
                )
            except (OSError, json.JSONDecodeError, ValueError):
                # 文件可能刚建、未写完，继续等
                pass
        time.sleep(ELEV_POLL_INTERVAL_MS / 1000.0)
    # 超时：尽力清理半成品文件
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    return ExecResult(ok=False, error="提权进程未在超时时间内返回结果")


def run_elevated_netsh(cmds: list[list[str]], timeout_s: int = ELEV_TIMEOUT_S) -> ExecResult:
    """以管理员权限执行一组 netsh 命令（父进程侧）。

    - `cmds` 必须全部是以 `netsh` 开头的参数列表（`_validate_cmds` 白名单）。
    - 通过 `ShellExecuteW runas` 拉起自身 `--exec-netsh`。
    - 轮询结果文件（200ms），总超时 `timeout_s`。
    - 用户拒绝 UAC（`OSError`/WinError 1223 或返回码 <= 32）→ 降级提示，不崩溃。

    注意：本函数**同步阻塞**，UI 层必须放到工作线程调用。
    """
    try:
        _validate_cmds(cmds)
    except ValueError as e:
        return ExecResult(ok=False, error=str(e))

    token = uuid.uuid4().hex
    result_path = elev_result_path(token)

    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = (
            f'{ELEV_FLAG} "{_encode_cmds(cmds)}" '
            f'{ELEV_RESULT_FLAG} "{result_path}"'
        )
    else:
        exe = sys.executable
        script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else "main.py"
        params = (
            f'"{script}" {ELEV_FLAG} "{_encode_cmds(cmds)}" '
            f'{ELEV_RESULT_FLAG} "{result_path}"'
        )

    try:
        rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", exe, params, None, 1  # SW_SHOWNORMAL
        )
    except OSError as e:
        # 典型：[WinError 1223] 操作已被用户取消
        return ExecResult(ok=False, error=f"{MSG_NEED_ADMIN}（{e}）")
    except Exception as e:
        return ExecResult(ok=False, error=f"提权失败：{e}")

    if int(rc) <= ELEV_SHELL_SUCCESS_MIN:
        return ExecResult(ok=False, error=MSG_NEED_ADMIN)

    return _poll_result_file(result_path, timeout_s)


def run_as_elevated_child(argv: list[str]) -> int:
    """在 `--exec-netsh` 子进程内执行；返回退出码（供 `main.py` 调用）。

    流程：解码命令 → 白名单校验 → 顺序执行 → 写 JSON 结果文件 → 返回 0/1。
    结果文件写入使用 **UTF-8**（我们自己写的文件，与控制台编码无关）。
    """
    try:
        if ELEV_FLAG not in argv:
            return 2
        cmds_token = argv[argv.index(ELEV_FLAG) + 1]
        if ELEV_RESULT_FLAG not in argv:
            return 2
        result_path = argv[argv.index(ELEV_RESULT_FLAG) + 1]
    except (IndexError, ValueError):
        return 2

    try:
        cmds = _decode_cmds(cmds_token)
        _validate_cmds(cmds)
    except (ValueError, json.JSONDecodeError) as e:
        _write_child_result(result_path, ExecResult(ok=False, error=f"命令校验失败：{e}"))
        return 2

    result = run_local_netsh(cmds)
    _write_child_result(result_path, result)
    return 0 if result.ok else 1


def _write_child_result(path: str, result: ExecResult) -> None:
    """把子进程执行结果写入指定 JSON 文件（UTF-8）。"""
    payload = {
        "ok": result.ok,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error": result.error,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
