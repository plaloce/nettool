"""发布前最终校验：exe 版本资源 + 业务链路 + 校验和。

用法（在项目根）：
    python tools/release_check.py [exe 路径，默认 dist/NetTool.exe]

校验项：
  1. PE 版本资源的 ProductName / FileDescription / FileVersion / ProductVersion / LegalCopyright
  2. 文件大小与 SHA256（与 GitHub Release 附件 digest 对照）
  3. `--exec-netsh` 子进程链路：合法命令应返回 ok:true + 退出码 0

注意：本脚本 import app.*，须用带 PySide6 的解释器跑（见 README / python.path）。
"""
import ctypes
import ctypes.wintypes as w
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.constants import ELEV_FLAG, ELEV_RESULT_FLAG  # noqa: E402
from app.elevation import _encode_cmds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "NetTool.exe")

if not os.path.isfile(EXE):
    raise SystemExit(f"找不到 exe：{EXE}（先执行 PyInstaller 打包）")

ver = ctypes.WinDLL("version")
size = ver.GetFileVersionInfoSizeW(EXE, None)
if not size:
    raise SystemExit(f"exe 无版本资源：{EXE}")
buf = ctypes.create_string_buffer(size)
assert ver.GetFileVersionInfoW(EXE, 0, size, buf), "读取版本资源失败"


def q(key: str) -> str:
    ptr = ctypes.c_void_p()
    length = w.UINT()
    ok = ver.VerQueryValueW(buf, "\\StringFileInfo\\080404B0\\" + key,
                            ctypes.byref(ptr), ctypes.byref(length))
    return ctypes.wstring_at(ptr, length.value).rstrip("\x00") if ok else "<缺失>"


for k in ("ProductName", "FileDescription", "FileVersion", "ProductVersion", "LegalCopyright"):
    print(f"{k:<16}= {q(k)!r}")

data = open(EXE, "rb").read()
print("SizeMB          =", round(len(data) / 1024 / 1024, 1))
print("SHA256          =", hashlib.sha256(data).hexdigest())

fd, path = tempfile.mkstemp(suffix=".json")
os.close(fd)
os.remove(path)
p = subprocess.run(
    [EXE, ELEV_FLAG, _encode_cmds([["netsh", "interface", "show", "interface"]]),
     ELEV_RESULT_FLAG, path],
    stdin=subprocess.DEVNULL, timeout=120,
)
res = json.load(open(path, encoding="utf-8"))
os.remove(path)
print("业务链路        = rc", p.returncode, "| ok", res.get("ok"),
      "| stdout 行数", len((res.get("stdout") or "").splitlines()))
