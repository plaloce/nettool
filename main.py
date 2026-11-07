"""NetTool 入口（**双入口**：GUI 模式 + 提权子进程模式）。

- 无 `--exec-netsh` 参数 → 正常 GUI 模式（QApplication + MainWindow）。
- 有 `--exec-netsh` 参数 → 提权子进程模式：不创建 GUI，执行 netsh 后写结果文件并退出。

另外负责：设置高 DPI、全局字体、注入 QSS、安装全局异常钩子。
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

# 保证以 `python main.py`（脚本目录）方式运行时能找到 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.constants import APP_NAME, APP_VERSION, ELEV_FLAG

# --------------------------------------------------------------------------- #
# 全局异常钩子（PRD 6.2：未处理异常不直接崩溃，弹框 + 写日志）
# --------------------------------------------------------------------------- #
_logger = logging.getLogger("nettool")


def _setup_logging() -> None:
    """把日志同时写入文件与 stderr（文件放配置目录）。"""
    try:
        from app.paths import log_path

        path = log_path()
    except Exception:
        path = os.path.join(os.path.expanduser("~"), "nettool.log")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _install_excepthook() -> None:
    """安装全局异常钩子：写日志 + 弹 QMessageBox，不直接闪退。"""
    import traceback as tb

    def _hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(tb.format_exception(exc_type, exc_value, exc_tb))
        _logger.error("未处理异常：\n%s", text)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    f"{APP_NAME} 发生错误",
                    f"程序遇到一个未处理的错误：\n\n{exc_value}\n\n"
                    f"详细信息已写入日志。",
                )
        except Exception:
            # 兜底：连弹框都失败时也不能再抛
            pass

    sys.excepthook = _hook


# --------------------------------------------------------------------------- #
# 提权子进程模式
# --------------------------------------------------------------------------- #
def _run_elevated_child(argv: list[str]) -> int:
    """处理 `--exec-netsh` 子进程模式；返回退出码。"""
    try:
        from app.elevation import run_as_elevated_child

        return run_as_elevated_child(argv)
    except Exception as e:  # 子进程内任何异常都要写回结果文件，避免父进程超时
        _logger.error("提权子进程异常：%s", e)
        try:
            if "--result-file" in argv:
                path = argv[argv.index("--result-file") + 1]
                from app.elevation import ExecResult, _write_child_result

                _write_child_result(
                    path, ExecResult(ok=False, error=f"提权子进程异常：{e}")
                )
        except Exception:
            pass
        return 1


# --------------------------------------------------------------------------- #
# GUI 模式
# --------------------------------------------------------------------------- #
def _run_gui() -> int:
    """启动 GUI 主程序。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    # 高 DPI 处理（Qt6 默认启用缩放；此处显式设置策略，避免模糊）
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    from app.constants import FONT_FAMILY, FONT_SIZE_PT
    from app.paths import ensure_dirs

    ensure_dirs()  # 提前建好配置目录

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # ⚠️ 不要调用 setApplicationDisplayName()！
    # Qt 会把 displayName 追加到每个窗口标题后面（形如 "… - NetTool"），
    # 而我们的标题（APP_WINDOW_TITLE）里已经含有项目名 ⇒ 标题栏会出现两个
    # "NetTool"（2026-11-08 用户实拍反馈）。窗口标题由 MainWindow 自己负责。
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # 全局字体
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE_PT))

    # 注入 QSS（运行时唯一来源）
    try:
        from app.ui.styles import build_qss

        app.setStyleSheet(build_qss())
    except Exception as e:
        _logger.error("加载样式失败：%s", e)

    _install_excepthook()

    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


def main() -> int:
    """程序总入口。"""
    _setup_logging()
    argv = sys.argv[1:]

    # 提权子进程模式（不创建 GUI / QApplication）
    if ELEV_FLAG in argv:
        return _run_elevated_child(sys.argv)

    if sys.platform != "win32":
        _logger.warning("NetTool 主要面向 Windows，当前平台：%s", sys.platform)

    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
