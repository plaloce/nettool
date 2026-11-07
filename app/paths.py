"""开发与打包统一的路径解析（架构 5.4，风险5）。

- 配置目录一律为 `%APPDATA%\\NetTool`，**不 import 任何 Qt**，保证工具层 / 业务层
  可在无 GUI 环境下被 pytest 单测（架构约定「工具层绝不 import Qt」）。
- 打包（onefile）后同样返回 `%APPDATA%\\NetTool`，与 exe 所在目录无关，
  天然避开 onefile 临时解压目录（`sys._MEIPASS`）。
- 极端情况（`%APPDATA%` 不可用）降级为 `~/.NetTool`。
"""

from __future__ import annotations

import os

from app.constants import APP_NAME

# 降级目录名（%APPDATA% 不可用时使用）
_FALLBACK_DIRNAME: str = ".NetTool"


def _fallback_dir() -> str:
    """%APPDATA% 失效时的降级配置目录：`~/.NetTool`。"""
    return os.path.join(os.path.expanduser("~"), _FALLBACK_DIRNAME)


def config_dir() -> str:
    """返回配置目录（绝对路径）：优先 `%APPDATA%\\NetTool`，失败降级 `~/.NetTool`。

    副作用：确保目录存在。
    """
    base: str = os.environ.get("APPDATA", "") or ""
    path: str = os.path.join(base, APP_NAME) if base else ""
    if not path:
        path = _fallback_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = _fallback_dir()
        os.makedirs(path, exist_ok=True)
    return path


def profiles_path() -> str:
    """返回 `profiles.json` 的绝对路径。"""
    from app.constants import PROFILES_FILENAME  # 局部导入避免循环

    return os.path.join(config_dir(), PROFILES_FILENAME)


def profiles_backup_path() -> str:
    """返回损坏配置备份文件（`profiles.json.bak`）的绝对路径。"""
    from app.constants import PROFILES_BACKUP_SUFFIX

    return profiles_path() + PROFILES_BACKUP_SUFFIX


def elev_result_path(token: str) -> str:
    """返回提权子进程结果文件的绝对路径（父进程决定并传给子进程）。

    Args:
        token: 唯一标识（一般用 `uuid4().hex`）。
    """
    return os.path.join(config_dir(), f"elev_result_{token}.json")


def log_path() -> str:
    """返回应用日志文件绝对路径（用于全局异常记录）。"""
    return os.path.join(config_dir(), "nettool.log")


def ensure_dirs() -> str:
    """确保配置目录存在，返回其路径。"""
    return config_dir()
