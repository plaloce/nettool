"""P1-6 / P1-7 · 意图审查：代码与 PRD 决策一致性 + 打包配置检查。

多为"静态代码审计 + 少量可执行断言"，覆盖"代码看起来对但需求没实现"的盲区。
"""

from __future__ import annotations

import ast
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _imported_names(src: str) -> set[str]:
    """返回源码中所有 `from xxx import yyy` / `import yyy` 的**被导入名**。

    为什么不用字符串匹配：导入语句经常带括号换行 ——

        from app.ui.flow_row import (
            FlowRow,
            fit_combo,
        )

    此时 `"from app.ui.flow_row import FlowRow" in src` 为 False，
    测试会**假失败**（本项目已踩过两次）。AST 解析与格式无关，才是可靠做法。
    """
    names: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _strip_comments_and_docstrings(src: str) -> str:
    """返回去除注释与 docstring 后的源码（仅保留可执行代码文本）。

    用途：静态契约断言应针对**实际代码**，而不是文档/注释里偶然出现的名字。
    例如 `paths.py` 的 docstring 写「天然避开 sys._MEIPASS」是合法说明，
    不应因出现字符串 `_MEIPASS` 就误判为「使用了它」。

    实现基于 `ast.unparse`：解析 AST 后丢弃所有 `ast.Expr(value=Constant(str))`
    （即模块/类/函数级 docstring），并丢弃注释（AST 本就不含注释）。
    若源码无法解析，退化为原文（宁可漏判也不误判成「违规」）。
    """
    import ast

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src

    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        # 丢弃前导 docstring 表达式语句
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:]

    try:
        return ast.unparse(tree)
    except Exception:  # pragma: no cover - 极端兜底
        return src


# --------------------------------------------------------------------------- #
# Q4：单次 ping 4 包
# --------------------------------------------------------------------------- #
def test_q4_single_count_is_4():
    from app.constants import PING_SINGLE_COUNT
    assert PING_SINGLE_COUNT == 4


def test_q4_single_count_reaches_ping_n():
    """PING_SINGLE_COUNT 真的被传给 ping -n。"""
    src = _read("app/ui/page_ping.py")
    assert re.search(r"ping_once_async\(\s*host\s*,\s*PING_SINGLE_COUNT", src)


# --------------------------------------------------------------------------- #
# Q5：持续 ping 间隔可选 0.5/1/2/5 秒，0.5 -> 500ms
# --------------------------------------------------------------------------- #
def test_q5_interval_choices():
    from app.constants import PING_INTERVAL_CHOICES
    assert PING_INTERVAL_CHOICES == [0.5, 1.0, 2.0, 5.0]


def test_q5_timer_interval_is_ms():
    """间隔秒数必须 *1000 转为毫秒传给 QTimer。"""
    src = _read("app/ui/page_ping.py")
    assert re.search(r"_timer\.start\(\s*int\(\s*interval_s\s*\*\s*1000\s*\)\s*\)", src)


def test_q5_interval_combo_has_all_choices():
    src = _read("app/ui/page_ping.py")
    assert "PING_INTERVAL_CHOICES" in src


# --------------------------------------------------------------------------- #
# Q8：用时提权（启动时不提权）
# --------------------------------------------------------------------------- #
def test_q8_no_elevation_at_startup():
    """main.py 的 GUI 启动路径不应在启动时调用 run_elevated_netsh。"""
    src = _read("main.py")
    gui_part = src[src.index("def _run_gui"):]
    assert "run_elevated_netsh" not in gui_part
    assert "ShellExecuteW" not in gui_part


def test_q8_elevation_only_in_service():
    """提权调用只出现在 netsh_service.run_cmds 与 elevation 自身。"""
    svc = _read("app/netsh_service.py")
    assert "run_elevated_netsh" in svc


# --------------------------------------------------------------------------- #
# Q9：无托盘 / 无自启
# --------------------------------------------------------------------------- #
def test_q9_no_system_tray():
    for rel in ("main.py", "app/ui/main_window.py", "app/ui/sidebar.py"):
        assert "QSystemTrayIcon" not in _read(rel)
    # 全项目扫描
    for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, "app")):
        for fn in files:
            if fn.endswith(".py"):
                with open(os.path.join(dirpath, fn), "r", encoding="utf-8") as f:
                    assert "QSystemTrayIcon" not in f.read(), f"{fn} 含托盘代码"


# --------------------------------------------------------------------------- #
# Q11：打开共享用主线程 Popen
# --------------------------------------------------------------------------- #
def test_q11_open_share_main_thread_popen():
    src = _read("app/ui/page_scan.py")
    # 必须是 Popen 语义（不是 run / 线程池 submit）。
    # 2026-09-21：改为走 app.procutil.popen_silent（Popen + CREATE_NO_WINDOW），
    # 否则打包成无控制台 GUI 后 explorer 之外的控制台程序会闪黑窗。
    idx = src.index("def open_share")
    body = src[idx:src.index("def ", idx + 10)]
    assert "popen_silent" in body
    assert "subprocess.run" not in body
    assert "submit" not in body
    assert "shell=False" in body
    assert "is_ipv4" in body            # 白名单校验
    assert "OSError" in body            # 捕获异常


# --------------------------------------------------------------------------- #
# Q13：右键菜单三项
# --------------------------------------------------------------------------- #
def test_q13_context_menu_three_items():
    src = _read("app/ui/page_scan.py")
    idx = src.index("def show_context_menu")
    body = src[idx:src.index("def ", idx + 10)]
    assert "打开共享" in body
    assert "快速 Ping" in body
    assert "复制 IP" in body
    # 非在线格前两项置灰
    assert body.count("setEnabled(online)") >= 2


# --------------------------------------------------------------------------- #
# F3-08：日志 500 行上限
# --------------------------------------------------------------------------- #
def test_f3_08_log_max_lines():
    from app.constants import LOG_MAX_LINES
    assert LOG_MAX_LINES == 500
    src = _read("app/ui/page_ping.py")
    assert "setMaximumBlockCount(LOG_MAX_LINES)" in src


# --------------------------------------------------------------------------- #
# F2-05：网格 254 格
# --------------------------------------------------------------------------- #
def test_f2_05_total_addresses():
    from app.constants import SCAN_TOTAL_ADDRESSES
    assert SCAN_TOTAL_ADDRESSES == 254


# --------------------------------------------------------------------------- #
# main.py 双入口（--exec-netsh 在 GUI 初始化之前处理）
# --------------------------------------------------------------------------- #
def test_main_dual_entry_order():
    src = _read("main.py")
    # main() 中先判断 ELEV_FLAG，再调用 _run_gui
    main_body = src[src.index("def main("):]
    i_flag = main_body.index("ELEV_FLAG in argv")
    i_gui = main_body.index("_run_gui")
    assert i_flag < i_gui, "--exec-netsh 分支必须在 GUI 初始化之前"


def test_main_exec_netsh_no_qapplication():
    """提权子进程分支不应创建 QApplication。"""
    src = _read("main.py")
    child = src[src.index("def _run_elevated_child"):src.index("def _run_gui")]
    assert "QApplication(" not in child


# --------------------------------------------------------------------------- #
# P1-7 打包 & 路径
# --------------------------------------------------------------------------- #
def test_spec_onefile_windowed():
    spec = _read("NetTool.spec")
    assert "console=False" in spec           # windowed
    assert "hiddenimports" in spec
    assert "PySide6.QtWidgets" in spec
    # onefile：EXE 内联了 binaries/datas（未走 COLLECT）
    assert "COLLECT" not in spec


def test_paths_no_qt_and_no_exe_dir():
    """架构 §5.4「决策记录（2026-09）」修订后的契约：

    - `app/paths.py` **禁止 import 任何 Qt**（业务层/工具层零 Qt 依赖，
      保证可在无 GUI 环境下被 pytest 单测），改用标准库 `os.environ["APPDATA"]`；
    - 配置目录一律为 `%APPDATA%\\NetTool`，与 exe 所在目录无关。

    重要：本用例是**防止 Qt 依赖重新污染的守卫** —— 若有人把 `paths.py`
    改回 `QStandardPaths`（旧契约），本测试**应当失败**。
    """
    src = _read("app/paths.py")

    # 「代码」= 去掉注释与 docstring 后的可执行部分；契约约束的是**实际代码**，
    # 而非文档里提到某个名字（如 docstring 描述「避开 sys._MEIPASS」是合法的）。
    code = _strip_comments_and_docstrings(src)

    # 1) 零 Qt 依赖（架构根基）：不得出现任何 Qt 引用
    assert "PySide6" not in code, "架构 §5.4：paths.py 禁止 import 任何 Qt（PySide6）"
    assert "QStandardPaths" not in code, (
        "架构 §5.4 决策记录：paths.py 不用 QStandardPaths，改用 os.environ['APPDATA']"
    )
    assert "QCoreApplication" not in code, "架构 §5.4：paths.py 禁止 import QCoreApplication"

    # 2) 仍正确使用 %APPDATA% 与标准库 os.environ
    assert "APPDATA" in src, "paths.py 应通过 APPDATA 环境变量定位配置目录"
    assert "os.environ" in code, "paths.py 应使用标准库 os.environ 读取 APPDATA"

    # 3) 可执行代码里任何情况下都不能落到 exe 同目录 / onefile 临时目录
    assert "sys.executable" not in code, "paths.py 不得用 sys.executable 定位 exe 同目录"
    assert "sys.argv[0]" not in code, "paths.py 不得用 sys.argv[0] 定位 exe 同目录"
    assert "_MEIPASS" not in code, "paths.py 不得使用 onefile 临时目录 sys._MEIPASS"


def test_start_bat_runs_main():
    bat = _read("启动.bat")
    # 启动脚本必须能拉起 main.py（解释器可用变量指定，故只断言会执行 main.py）
    assert "main.py" in bat
    # 不能指向 exe 同目录之外的绝对固定路径而不留回退（含回退即可）
    assert "%NETTOOL_PY%" in bat or "python" in bat


def test_pack_bat_calls_pyinstaller_spec():
    bat = _read("打包.bat")
    assert "NetTool.spec" in bat
    assert "PyInstaller" in bat


# --------------------------------------------------------------------------- #
# UI 布局契约（2026-09 用户实测反馈后固化，防止「输入框被压扁」「网格需横向滚动」
# 这两类问题再次回归）
# --------------------------------------------------------------------------- #
def test_scan_page_right_panel_is_shrinkable():
    """扫描页右栏必须**可收缩**：绝不能用 setFixedWidth。

    用户报过「16/32/48 列要往右拉滚动条才看得见」。当时的根因之一是
    右栏写死 `setFixedWidth(380)` 撑破窗口，把网格区挤到出滚动条。
    """
    src = _read("app/ui/page_scan.py")
    code = _strip_comments_and_docstrings(src)

    assert "right.setMaximumWidth" in code, "右栏应设 maximumWidth（可收缩上限）"
    assert "SCAN_RIGHT_PANEL_MIN_W" in code, "右栏应使用 SCAN_RIGHT_PANEL_MIN_W 常量"

    # 右栏不得固定宽度
    assert "right.setFixedWidth" not in code, (
        "扫描页右栏禁止 setFixedWidth：会撑破窗口并逼出网格区横向滚动条"
    )


def test_scan_page_uses_flow_widgets():
    """控制条与详情按钮组必须放进可换行容器。

    实测数据：控制条 8 个控件横向不换行时 minHint = 884px；
    详情卡 3 个按钮并排时 minHint = 358px（会覆盖右栏 minimumWidth）。

    注：2026-09 起 `FlowRow` 的实现抽到了 `app/ui/flow_row.py`（Ping 页复用），
    故这里断言「使用 FlowRow」+「从共享模块导入」，实现本身由
    `test_flowrow_is_shared_module` / `test_flowrow_item_width_*` 守卫。
    """
    src = _read("app/ui/page_scan.py")

    # ⚠️ 不要断言 `"from app.ui.flow_row import FlowRow" in src`：
    # 导入多带括号换行（`from app.ui.flow_row import (\n    FlowRow,\n    fit_combo,\n)`），
    # 单行字符串匹配会**假失败**（本测试踩过）。改为解析 AST，检查真实导入来源。
    imported = _imported_names(src)
    assert "FlowRow" in imported, (
        f"page_scan.py 应从 app/ui/flow_row.py 导入 FlowRow，实际导入：{sorted(imported)}"
    )
    assert "FlowRow(" in src, "控制条/按钮组应使用 FlowRow"
    # 必须有首次排版入口，否则构造后首帧可能空白
    assert "apply_style()" in src, "应在 item 添加完毕后调用 apply_style()"


def test_grid_size_hint_width_matches_budget():
    """网格 sizeHint 宽度必须与常量推导一致（防手改常量后不自洽）。

    360 = 16 × (18 + 4) - 4 + 2 × 6
    """
    from app.constants import (
        GRID_CELL_GAP,
        GRID_CELL_W,
        GRID_COLS,
        GRID_PAD_X,
    )

    expected = GRID_COLS * (GRID_CELL_W + GRID_CELL_GAP) - GRID_CELL_GAP + 2 * GRID_PAD_X
    assert expected == 360, (
        f"网格 sizeHint 宽应为 360（当前推导得 {expected}）。"
        "改了 GRID_CELL_W/GAP/PAD_X 就必须同步重算 §6.3.2 的横向预算与 "
        "SCAN_RIGHT_PANEL_MIN_W，否则最小窗口下网格区会重新出现横向滚动条。"
    )


def test_grid_cell_is_clickable():
    """格子可点面积不得小于 16px（自绘控件点击命中率下限）。"""
    from app.constants import GRID_CELL_H, GRID_CELL_W

    assert GRID_CELL_W >= 16, f"格子宽 {GRID_CELL_W}px < 16px，会出现点不中"
    assert GRID_CELL_H >= 16, f"格子高 {GRID_CELL_H}px < 16px，会出现点不中"


def test_input_widgets_have_min_height():
    """输入类控件必须有 min-height，否则窗口变矮时文字被压扁。

    用户报过「网络配置，方案编辑输入框高度被压缩，显示不全」。
    Qt 的 QSS `min-height` 语义与 CSS 不同（含 padding/border），
    这里断言其存在且取值足以容纳一行文字。
    """
    import re

    qss = build_qss_for_test()
    block = re.search(r"QLineEdit,\s*QComboBox,\s*QSpinBox\s*\{(.*?)\}", qss, re.S)
    assert block is not None, "QSS 应包含 QLineEdit/QComboBox/QSpinBox 规则块"

    m = re.search(r"min-height:\s*(\d+)px", block.group(1))
    assert m is not None, "输入控件规则块必须声明 min-height（防纵向压缩）"
    assert int(m.group(1)) >= 18, f"min-height={m.group(1)}px 过小，文字会被压扁"


def build_qss_for_test() -> str:
    """构造 QSS（供静态断言使用，不依赖 QApplication）。"""
    from app.ui.styles import build_qss

    return build_qss()


# --------------------------------------------------------------------------- #
# 线程回传契约（2026-09 用户实测「网卡获取为空」后固化）
# --------------------------------------------------------------------------- #
def test_no_qtimer_in_worker_threads():
    """**工作线程内禁止用 `QTimer.singleShot` 回主线程**（P0 级回归守卫）。

    根因：`QTimer.singleShot(0, cb)` 会把定时器挂到**调用它的那个线程**上。
    从 `ThreadPoolExecutor` 工作线程调用时，该线程**没有事件循环**，
    回调**永远不执行、且不抛任何异常**（静默失效）。

    离线对照实验（固定为回归基准）：
        - 工作线程内 `QTimer.singleShot(0, cb)` → 回调 **0 次**
        - 工作线程内 `signal.emit(...)`        → 回调 **1 次**，且已回到主线程

    症状是「网卡下拉框为空」——曾同时影响 4 处调用点，排查成本极高。
    正确做法：定义类级 `Signal`，在工作线程 `emit`，Qt 会跨线程自动排队
    （`QueuedConnection`）到接收者线程的事件循环。
    """
    offenders: list[str] = []
    for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, "app")):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), _ROOT).replace("\\", "/")
            src = _read(rel)
            # ⚠️ 必须用**去注释/docstring 后的代码**做扫描：
            #   这些函数的 docstring 里正好写着「不要用 QTimer.singleShot」的警告，
            #   若扫原始文本会把自己的警告当成违规（自证陷阱）。
            code = _strip_comments_and_docstrings(src)

            # 只看名字里含 worker/Worker 的函数体
            for m in re.finditer(r"def\s+(\w*[Ww]orker\w*)\s*\(", code):
                name = m.group(1)
                body_start = m.end()
                # 取该函数体：到下一个同级 def（缩进 4 空格）为止
                nxt = re.search(r"\n    def\s+\w+\s*\(", code[body_start:])
                body = (
                    code[body_start : body_start + nxt.start()] if nxt else code[body_start:]
                )
                if "QTimer.singleShot" in body:
                    offenders.append(f"{rel}::{name}")

    assert not offenders, (
        "以下工作线程函数内使用了 QTimer.singleShot，会静默失效（回调永不执行）：\n  "
        + "\n  ".join(offenders)
        + "\n请改用类级 Signal.emit 回主线程（见 app/ui/thread_bridge.py 的说明）。"
    )


def test_worker_callbacks_use_signals():
    """四处「工作线程 → 主线程」回传必须已改为信号（契约固化）。"""
    expect = {
        "app/ui/page_scan.py": ["_adapters_ready"],
        "app/ui/page_config.py": ["_apply_finished_internal", "_refresh_ready"],
        "app/ui/page_ping.py": ["_gateway_ready"],
    }
    for rel, sigs in expect.items():
        src = _read(rel)
        for sig in sigs:
            assert sig in src, f"{rel} 缺少回传信号 {sig}"
    # 信号必须被 connect 到处理函数，否则 emit 了也没人接
    for sig in sigs:
        assert f"{sig}.connect(" in src, f"{rel} 的 {sig} 未 connect"


def test_thread_bridge_exists_and_documented():
    """`thread_bridge.py` 应存在于 UI 层，且保留根因说明（防止被误删/误改）。"""
    src = _read("app/ui/thread_bridge.py")
    assert "class ThreadBridge" in src
    assert "def on_main" in src
    assert "def post" in src
    # 根因说明是给未来维护者的护栏，必须留着
    assert "singleShot" in src, "应保留 QTimer.singleShot 陷阱说明（防后来者误改）"


# --------------------------------------------------------------------------- #
# QComboBox 最小宽契约（2026-09 用户实测「重置按钮换行」后固化）
# --------------------------------------------------------------------------- #
def test_comboboxes_relax_minimum_contents_length():
    """控制条里的 QComboBox 必须调 `setMinimumContentsLength(0)`（换行根因守卫）。

    根因：`QComboBox.minimumSizeHint()` 默认按**最长选项文本**计算。
    网卡下拉含 `"VMware Network Adapter VMnet1"`，实测把它顶到 **139px**，
    而该值**会盖掉** `setMaximumWidth(160/220/72/96)`。
    结果控制条 10 个 item 的总需求 = 859px > 可用 802px，
    最后一个「重置」按钮被挤到第二行（用户截图所见）。

    实测数据（修复前后）：
        - 修复前：4 个 combo 各 139px，总需求 859px，差额 +57px → 换行
        - 修复后：combo 回落，总需求 580px，差额 -222px → 单行

    正确做法：`setMinimumContentsLength(0)` +
    `setSizeAdjustPolicy(AdjustToMinimumContentsLengthWithIcon)`，
    让最小宽不再依赖内容，宽度完全交给 min/maxWidth 决定。
    """
    src = _read("app/ui/page_scan.py")
    code = _strip_comments_and_docstrings(src)

    n = code.count("setMinimumContentsLength(0)")
    assert n >= 4, (
        f"扫描页控制条有 4 个 QComboBox（网段/网卡/并发/超时），"
        f"但只有 {n} 处调用了 setMinimumContentsLength(0)。"
        "缺失会让该 combo 的 minimumSizeHint 按最长选项算，重新把「重置」挤出第一行。"
    )
    assert code.count("AdjustToMinimumContentsLengthWithIcon") >= 4


def test_flowrow_item_width_respects_maxwidth():
    """`FlowRow._item_width` 必须尊重调用方设的 `maximumWidth`。

    否则「按最长选项算」的 `minimumSizeHint`（139px）会覆盖 `setMaximumWidth(96)`，
    分行计算失真，控制条被误判为放不下而换行。

    注：2026-09 起 `FlowRow` 已抽到 `app/ui/flow_row.py`（Ping 页也要用同一份），
    故本测试改为扫该共享模块。
    """
    src = _read("app/ui/flow_row.py")
    idx = src.index("def _item_width")
    body = src[idx : src.index("\n    def ", idx + 10)]
    assert "maximumWidth()" in body, "_item_width 应读取 maximumWidth 作为上界"
    assert "minimumSizeHint()" in body, "_item_width 应以下限 minimumSizeHint 兜底"


# --------------------------------------------------------------------------- #
# 在线列表行高契约（2026-09 用户实测「IP 一行只显示一个」后固化）
# --------------------------------------------------------------------------- #
def test_online_table_no_wrap_single_line():
    """在线列表必须关闭换行，且行高固定（防「一个 IP 占两行」回归）。

    根因：右栏仅 256~360px，IP 列用 `ResizeMode.Stretch` 会被压到不足
    `"192.168.2.100"` 的宽度，而 `QTableWidget` 默认 `wordWrap=True`，
    于是 IP 折成两行 —— 用户看到的就是「一行只显示一个 IP」。

    修复：`setWordWrap(False)` + `ElideNone` + 固定行高 30px +
    IP/延迟两列都按内容撑开（`ResizeToContents`）。
    """
    src = _read("app/ui/page_scan.py")
    code = _strip_comments_and_docstrings(src)

    assert "setWordWrap(False)" in code, "在线列表必须关闭 wordWrap"
    assert "setDefaultSectionSize(30)" in code, "在线列表应固定行高（默认 30px）"
    assert "ElideNone" in code, "IP 不应被省略号截断"
    # IP/延迟两列都应按内容撑开（ResizeToContents）。
    # 注意要排除 `setStretchLastSection(True)` —— 它是「让最后一列吃掉剩余空白」，
    # 与「把列压窄」的 Stretch 模式语义相反，是我们要保留的。
    tbl = code[code.index("_online_table = QTableWidget") :]
    tbl = tbl[: tbl.index("list_layout.addWidget")] if "list_layout.addWidget" in tbl else tbl
    resize_modes = re.findall(r"setSectionResizeMode\([^)]*\)", tbl, re.S)
    assert resize_modes, "在线列表应显式设置列宽模式"
    for rm in resize_modes:
        assert "Stretch" not in rm or "ResizeToContents" in rm, (
            f"列宽模式 {rm!r} 含 Stretch：窄右栏下会把 IP 列压窄导致折行。"
            "应使用 ResizeToContents 让列宽跟随内容。"
        )
    assert tbl.count("ResizeToContents") >= 2, "IP 与延迟两列都应按内容撑开"


def test_qss_table_item_has_tight_padding():
    """表格 item 的 padding 必须收紧（否则行高被撑大、一屏看不到几条）。"""
    qss = build_qss_for_test()
    m = re.search(r"QTableWidget::item,\s*QListWidget::item\s*\{(.*?)\}", qss, re.S)
    assert m is not None, "QSS 应包含 QTableWidget::item padding 规则"
    pm = re.search(r"padding:\s*(\d+)px", m.group(1))
    assert pm is not None, "表格 item 规则应声明 padding"
    assert int(pm.group(1)) <= 6, f"表格 padding={pm.group(1)}px 偏大，行高会被撑高"


# --------------------------------------------------------------------------- #
# 自适应网格契约（2026-09 用户实测「图标变小 + 右侧大片空位」后固化）
# --------------------------------------------------------------------------- #
def test_grid_adapts_to_available_width():
    """网格必须能按可用宽度放大格子，且**保持坐标互逆**。"""
    src = _read("app/ui/scan_grid.py")
    code = _strip_comments_and_docstrings(src)

    assert "def set_available_width" in code, "缺失自适应入口"
    assert "MIN_CELL" in code and "MAX_CELL" in code, "应有自适应上下限"
    assert "updateGeometry()" in code, "格子尺寸变化后必须 updateGeometry() 通知布局"

    # 余数补偿必须走内边距，不能改列间距（否则 index_at / _cell_rect 不再互逆）
    assert "EXTRA_PAD" in code, "应通过 EXTRA_PAD 补偿整除余数"
    assert "def _pad_x" in code, "_cell_rect / index_at 必须共用 _pad_x() 才能互逆"


def test_grid_coord_roundtrip_is_exact():
    """254 格逐格校验 `index_at(_cell_rect(i)) == i`（含余数补偿生效的场景）。"""
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.constants import SCAN_TOTAL_ADDRESSES
    from app.ui.scan_grid import ScanGridWidget

    app = QApplication.instance() or QApplication([])
    grid = ScanGridWidget()

    checked = 0
    for avail in (300, 360, 400, 484, 520, 600, 620):
        grid.set_available_width(avail)
        for idx in range(SCAN_TOTAL_ADDRESSES):
            r = grid._cell_rect(idx)
            for px, py in (
                (r.left(), r.top()),
                (r.left() + r.width() // 2, r.top() + r.height() // 2),
                (r.right(), r.bottom()),
            ):
                assert grid.index_at(px, py) == idx, (
                    f"avail={avail} EXTRAPAD={grid.EXTRA_PAD} 格 {idx} "
                    f"坐标 ({px},{py}) 反查错格"
                )
                checked += 1
    assert checked > 0
    assert app is not None


def test_grid_fills_available_width_without_overflow():
    """可用宽度充裕（未触上限）时，网格绘制宽应**正好等于**可用宽度。"""
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.ui.scan_grid import ScanGridWidget

    QApplication.instance() or QApplication([])
    grid = ScanGridWidget()

    for avail in (400, 484, 560, 600, 620):
        grid.set_available_width(avail)
        got = grid.sizeHint().width()
        # 允许 1px 取整误差
        assert abs(got - avail) <= 1, (
            f"avail={avail} 时 sizeHint 宽 = {got}，网格没吃满面板"
            f"（CELL_W={grid.CELL_W}, EXTRA_PAD={grid.EXTRA_PAD}）"
        )


def test_grid_minimum_size_hint_uses_min_cell():
    """`minimumSizeHint` 必须按**下限格子**算，不能直接返回 sizeHint。

    否则格子被放大到 MAX_CELL 后，最小宽会被一起抬到 632px，
    反而把父母板顶宽 → QScrollArea 误判「放不下」→ 弹横向滚动条。
    """
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.constants import GRID_CELL_GAP, GRID_CELL_W, GRID_COLS, GRID_PAD_X
    from app.ui.scan_grid import ScanGridWidget

    QApplication.instance() or QApplication([])
    grid = ScanGridWidget()
    grid.set_available_width(900)          # 触到 MAX_CELL，sizeHint 变大

    expect_min = GRID_COLS * (GRID_CELL_W + GRID_CELL_GAP) - GRID_CELL_GAP + 2 * GRID_PAD_X
    assert grid.minimumSizeHint().width() == expect_min, (
        f"minimumSizeHint 应为 {expect_min}（按 MIN_CELL 算），"
        f"实得 {grid.minimumSizeHint().width()}。"
        "若这里等于 sizeHint（放大后的值），窄窗口会多出横向滚动条。"
    )
    assert grid.sizeHint().width() > expect_min, "可用宽度充裕时 sizeHint 应大于最小宽"


# --------------------------------------------------------------------------- #
# 网段自动补齐契约（2026-09 用户需求 3）
# --------------------------------------------------------------------------- #
def test_scan_page_auto_fills_subnet_when_empty():
    """需求 3：网段留空时自动用本机当前网段（减少操作），且不得卡死用户。"""
    src = _read("app/ui/page_scan.py")
    code = _strip_comments_and_docstrings(src)

    assert "def _auto_fill_subnet" in code, "缺失空网段自动补齐入口"
    # on_start 里校验前必须先补齐
    idx = code.index("def on_start")
    body = code[idx : code.index("def ", idx + 10)]
    assert "_auto_fill_subnet(force=True)" in body, (
        "on_start 应在校验前调用 _auto_fill_subnet(force=True)，"
        "否则空网段会直接被判为非法而拒绝启动"
    )
    # 应借助系统真实出口网段推断
    assert "guess_local_subnet" in code, "应使用 NetshService.guess_local_subnet() 兜底"


def test_adapter_default_picks_real_uplink():
    """网卡下拉默认应选「真实出口」网卡，而不是列表第一项。

    否则会默认落在 VMware/VirtualBox 虚拟网卡（实测填出 192.168.80），
    而用户真实网段是 192.168.2（以太网，带网关），与需求 3 的语义不符。
    """
    src = _read("app/ui/page_scan.py")
    code = _strip_comments_and_docstrings(src)

    assert "def _best_adapter_index" in code, "缺失真实出口网卡评分函数"
    idx = code.index("def _best_adapter_index")
    body = code[idx : code.index("def ", idx + 10)]
    assert "gateway" in body, "评分应把「有网关」作为真实出口的首要权重"
    assert "vmware" in body.lower(), "评分应对虚拟网卡降权"

    # _on_adapters_ready 必须用它，而不是写死 index 0
    idx2 = code.index("def _on_adapters_ready")
    body2 = code[idx2 : code.index("def ", idx2 + 10)]
    assert "_best_adapter_index()" in body2, "_on_adapters_ready 应使用评分结果选默认网卡"


def test_guess_local_subnet_excludes_loopback_and_apipa():
    """`guess_local_subnet` 必须排除回环 / APIPA 网段。"""
    src = _read("app/netsh_service.py")
    code = _strip_comments_and_docstrings(src)

    idx = code.index("def guess_local_subnet")
    body = code[idx : idx + 2500]
    assert "127." in body, "应排除回环 127.x"
    assert "169.254." in body, "应排除 APIPA 169.254.x"


def test_netsh_interface_regex_longest_first():
    """`_list_interface_names` 的正则备选必须「长词在前」。

    Windows 中文 `netsh` 输出的状态列实际用词是「**已断开连接**」。
    若把 `已断开` 排在前面，它会先吃掉 `已断开连接` 的前缀，
    把剩下的 `连接` 当成接口名 —— 曾导致 WLAN 被静默丢弃（4 个网卡只解析出 3 个）。
    """
    src = _read("app/netsh_service.py")
    code = _strip_comments_and_docstrings(src)

    idx = code.index("_list_interface_names")
    body = code[idx : idx + 2500]

    # 只取正则**字面量**来断言顺序。
    # 不能在整个函数体里 index("已断开")：`is_up` 判断里先出现的是「已禁用」「已断开」，
    # 会把位置比较带偏（本测试第一版就是这么误判的）。
    # 注意：`_strip_comments_and_docstrings` 走 `ast.unparse`，会把多行拼接的
    # re.search 压成单行 `re.search('...', stripped, re.IGNORECASE)`。
    m = re.search(r"re\.search\(\s*(['\"])(.+?)\1", body, re.S)
    assert m is not None, "未找到 _list_interface_names 里的 re.search 正则字面量"
    pattern = m.group(2)

    assert "已断开连接" in pattern, "正则必须覆盖「已断开连接」"
    i_long = pattern.index("已断开连接")
    after_long = pattern[i_long + len("已断开连接") :]
    assert "已断开" in after_long, (
        "正则应同时保留「已断开」（覆盖其它 Windows 版本可能输出的短词）"
    )
    i_short = i_long + len("已断开连接") + after_long.index("已断开")
    assert i_long < i_short, (
        "「已断开连接」必须排在「已断开」之前（长词在前），"
        "否则 `已断开` 会先匹配掉前缀，把「连接」误当作接口名。"
        f"\n实际正则片段：{pattern!r}"
    )


# --------------------------------------------------------------------------- #
# 合并按钮契约（2026-09 用户 P0 崩溃 + 需求 3 后固化）
#
# 用户报的崩溃：
#     File "app/ui/page_scan.py", line 808, in _set_running_ui
#         self._start_btn.setEnabled(not running)
#     AttributeError: 'PageScan' object has no attribute '_start_btn'
#
# 根因（两层，都要防）：
#   ① 合并起停按钮时删掉了 `_start_btn`/`_stop_btn` 的创建，却漏改 `_set_running_ui`；
#   ② 文件里存在**两个** `_set_running_ui` 定义，后面那个（引用 _start_btn 的旧版）
#      把前面那个（正确的新版）**覆盖**了 —— Python 后者胜出，静态看代码很容易被骗。
# --------------------------------------------------------------------------- #
_PAGES_USING_TOGGLE = ("app/ui/page_scan.py", "app/ui/page_ping.py")


def test_set_running_ui_only_touches_existing_widgets():
    """`_set_running_ui` 体内引用的 `self._xxx` 必须都在 `__init__` 里被赋值过。

    这直接对应上面那个 P0 崩溃：删了控件创建却留着引用，一点「开始」就炸。
    静态扫描能覆盖「代码看起来对但一跑就 AttributeError」的盲区。
    """
    import ast

    for rel in _PAGES_USING_TOGGLE:
        src = _read(rel)
        tree = ast.parse(src)

        # 收集**类级常量**名（`_TOGGLE_TEXT_IDLE = "..."` 这种）。
        # 它们写在 class 体里而非 `self.xxx = ...`，但 `self._TOGGLE_TEXT_IDLE`
        # 在运行期完全合法（走类属性查找）。不收进来的话会**假报**
        # 「引用了从未赋值的 self._TOGGLE_TEXT_IDLE」——本测试修正过一次。
        class_constants: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name):
                                class_constants.add(t.id)
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        class_constants.add(stmt.target.id)

        # 收集 __init__ 里所有 `self.xxx = ...` 的赋值名
        assigned: set[str] = set(class_constants)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef,)) and node.name == "__init__":
                for sub in ast.walk(node):
                    targets = []
                    if isinstance(sub, ast.Assign):
                        targets = sub.targets
                    elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
                        targets = [sub.target]
                    for t in targets:
                        if (
                            isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                        ):
                            assigned.add(t.attr)
            # 构造函数外的其它方法也可能补建控件（如延迟创建），一并收集
            if isinstance(node, ast.FunctionDef) and node.name in ("_build_ui",):
                for sub in ast.walk(node):
                    targets = []
                    if isinstance(sub, ast.Assign):
                        targets = sub.targets
                    elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
                        targets = [sub.target]
                    for t in targets:
                        if (
                            isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                        ):
                            assigned.add(t.attr)

        # 找出 _set_running_ui（可有多处，全都要查）
        found = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_set_running_ui"
        ]
        assert found, f"{rel} 应定义 _set_running_ui"

        for fn in found:
            # `self._is_continuous()` 这类**方法调用**也会表现为 Attribute(func=Attribute)，
            # 必须排除 —— 否则会把方法名当成控件名，产生误报（本测试第一版就踩了）。
            method_call_names: set[int] = set()
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    method_call_names.add(id(sub.func))

            for sub in ast.walk(fn):
                if not isinstance(sub, ast.Attribute):
                    continue
                if id(sub) in method_call_names:
                    continue      # 是方法调用，不是控件引用
                if not (isinstance(sub.value, ast.Name) and sub.value.id == "self"):
                    continue
                name = sub.attr
                # 只看「控件型」引用：下划线开头
                if not name.startswith("_"):
                    continue
                assert name in assigned, (
                    f"{rel}::{fn.name}() 引用了 `self.{name}`，"
                    f"但它从未在 __init__/_build_ui 里被赋值 —— "
                    f"这正是 P0 崩溃 `AttributeError: {name}` 的成因。"
                    "删控件时必须同步删掉这里的引用。"
                )


def test_toggle_button_defined_once_per_page():
    """起停按钮必须**合并为一个** `_toggle_btn`，且 `_set_running_ui` 只操作它。

    用户需求：「开始工作后按钮变停止，停止后恢复为开始」。
    两按钮方案（_start_btn/_stop_btn）不仅没实现该交互，还是上面 P0 崩溃的源头。
    """
    for rel in _PAGES_USING_TOGGLE:
        src = _read(rel)
        code = _strip_comments_and_docstrings(src)

        assert "_toggle_btn" in code, f"{rel} 应使用合并按钮 _toggle_btn"
        assert "_start_btn" not in code, (
            f"{rel} 不应再出现 _start_btn（已合并为 _toggle_btn）"
        )
        assert "_stop_btn" not in code, (
            f"{rel} 不应再出现 _stop_btn（已合并为 _toggle_btn）"
        )

        # 必须提供 on_toggle 且同时具备两条分支
        assert "def on_toggle" in code, f"{rel} 应提供 on_toggle() 两态入口"
        idx = code.index("def on_toggle")
        body = code[idx : code.index("def ", idx + 10)]
        assert "on_start()" in body and "on_stop()" in body, (
            "on_toggle 必须按运行态分别调用 on_start()/on_stop()"
        )

        # 两态文案 + objectName 切换
        idx2 = code.index("def _set_running_ui")
        body2 = code[idx2 : code.index("def ", idx2 + 10)]
        assert "setText(" in body2, "_set_running_ui 应切换按钮文案"
        assert "setObjectName(" in body2, "_set_running_ui 应切换按钮配色（objectName）"
        assert "unpolish" in body2 and "polish" in body2, (
            "objectName 变了必须 unpolish+polish，否则 QSS 不会重新匹配，按钮还是旧配色"
        )


def test_toggle_button_width_fits_its_labels():
    """合并按钮宽度必须按**自身文案实测**定宽，而不是写死像素。

    历史：这里曾写成「`setMinimumWidth(92)` 的 92 是魔法数，小于它就会裁字」。
    但 92 只对某个特定字体成立 —— 换字号/换系统字体就失准。
    现在统一改为运行时量宽（`fit_button`/`fit_button_multi`）。

    ⚠️ 断言必须落在**调用点**上，不能只判断"文件里出现过 fit_button"：
    变异测试证明，把调用点换成 `setMinimumWidth(92)` 后，
    若仅检查 `"fit_button" in code`，测试照样通过（文件别处还有 fit_button）
    —— 那是个**抓不住回归的空壳守卫**。所以这里解析 AST 找真实调用。
    """
    for rel in _PAGES_USING_TOGGLE:
        src = _read(rel)
        tree = ast.parse(src)

        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                calls.add(name)

        assert calls & {"fit_button", "fit_button_multi"}, (
            f"{rel} 的合并按钮应真正调用 fit_button/fit_button_multi 运行时量宽，"
            "而不是写死像素（字体一变就不准）。"
        )

        # 反向守卫：不得对 _toggle_btn 直接写死像素宽度
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("setMinimumWidth", "setFixedWidth")
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_toggle_btn"
            ):
                raise AssertionError(
                    f"{rel} 不应写死 `_toggle_btn.{node.func.attr}(像素)` —— "
                    "改用 fit_button/fit_button_multi 按真实字体量宽"
                )


def test_toggle_button_fits_longest_state_text():
    """合并按钮的定宽基准必须覆盖**两态文案中较长的那个**。

    这是"用户切到工作中，按钮文字被裁成 `■ 停…`"的防线。
    两种合规写法：
        ① `fit_button_multi(btn, [IDLE, RUNNING])` —— 内部取最长者（推荐）
        ② `fit_button(btn)` —— 按当前态定宽，但**必须**在文案变化后重新调用
           （否则切态后宽度不更新）
    因此判定：用了 `fit_button_multi` 直接通过；只用了 `fit_button` 的，
    必须在 `_set_running_ui` 里再次调用过它。
    """
    for rel in _PAGES_USING_TOGGLE:
        code = _strip_comments_and_docstrings(_read(rel))

        if "fit_button_multi" in code:
            continue    # 已按两态最长者定宽，合规

        assert "fit_button" in code, f"{rel} 的合并按钮缺少量宽调用"
        idx = code.index("def _set_running_ui")
        body = code[idx : code.index("\n    def ", idx + 10)]
        assert "fit_button" in body, (
            f"{rel} 只用 fit_button 按当前态定宽时，**必须**在 _set_running_ui 里"
            "重新调用一次 —— 否则切换到较长文案后按钮宽度不更新，文字被裁"
        )


# --------------------------------------------------------------------------- #
# Ping 页控制条契约（2026-09 用户截图：「目标地址」压住「模式」）
# --------------------------------------------------------------------------- #
def test_ping_page_uses_flowrow():
    """Ping 页控制条必须用 FlowRow，不能用裸 QHBoxLayout。

    根因：`QHBoxLayout` 不允许换行，其最小宽 = 子控件最小宽之和。
    窗口一收窄，「目标地址」输入框（旧值 minimumWidth=200）就把「模式」标签压住
    —— 用户截图里两个字直接叠在输入框上，这就是本次修复要消除的现象。
    """
    src = _read("app/ui/page_ping.py")
    code = _strip_comments_and_docstrings(src)

    assert "FlowRow(" in code, "Ping 页控制条应使用 FlowRow 可换行容器"
    assert "FlowRow" in _imported_names(src), (
        "应复用扫描页抽出的 FlowRow（app/ui/flow_row.py），不要在页面内重造"
    )

    # 控制条内不得再出现裸 QHBoxLayout 承载输入控件
    idx = code.index("def _build_ui")
    body = code[idx : code.index("def ", idx + 10)]
    head, _, _tail = body.partition("card_layout.addWidget(quick)")
    assert "QHBoxLayout()" not in head, (
        "控制卡里不应再用裸 QHBoxLayout —— 它是「目标地址压住模式」的根因"
    )


def test_flowrow_is_shared_module():
    """`FlowRow` 必须是独立共享模块，两页用同一份实现。

    两页各写一份必然会漂移（一处修了 `_item_width` 另一处忘改），
    此前扫描页修好的「尊重 maximumWidth」若没同步到 Ping 页，
    重叠问题就会在 Ping 页复现。
    """
    src = _read("app/ui/flow_row.py")
    assert "class FlowRow" in src, "app/ui/flow_row.py 应定义 FlowRow"
    assert "def _item_width" in src, "应保留 _item_width 分行宽度计算"
    assert "QWIDGETSIZE_MAX" in src, "应以 QWIDGETSIZE_MAX 判断「未设上限」"

    # 两页都必须是「导入」而非「自带实现」
    for rel in _PAGES_USING_TOGGLE:
        code = _strip_comments_and_docstrings(_read(rel))
        assert "class FlowRow" not in code, (
            f"{rel} 不应再自带 FlowRow 实现（已抽到 app/ui/flow_row.py）"
        )


def test_flowrow_item_width_honours_locked_width():
    """`_item_width` 必须优先返回「min == max 锁死」的宽度。

    这是「文字仍被裁」的根因：QSS 尚未 apply 时 `QComboBox.sizeHint()` 只有 29px，
    而控件被 min/max 顶到 132px。若跳过本分支，FlowRow 按 29px 记账
    → 整行预算严重低估（实测 628px vs 可用 820px）
    → 相邻控件吃掉全部空间，被锁死的控件反而拿不到自己需要的宽度，
      「▶ 开始 Ping」被裁成「▶ 开始 P…」。
    """
    src = _read("app/ui/flow_row.py")
    idx = src.index("def _item_width")
    body = src[idx : src.index("\n    def ", idx + 10)]
    assert "mn == mx" in body or "minimumWidth() == maximumWidth()" in body, (
        "应对 min == max 做特判，直接返回锁定宽度"
    )
    assert "minimumWidth()" in body, "应读取 minimumWidth 作为下界"
    assert "maximumWidth()" in body, "应读取 maximumWidth 作为上界"


def test_combobox_widths_meet_text_requirement():
    """两页控制条里**显示文本**的 QComboBox，宽度必须容得下当前文本。

    用户报「并发/超时下拉选择后内容显示不完整」——就是因为宽度小于所需。

    ⚠️ 本测试是**运行态**断言，必须用真实平台量：
        offscreen 平台没有 `Microsoft YaHei UI`，CJK 文本会被算成真实值的
        **1.85 倍**（"IP 或域名, 如 8.8.8.8"：offscreen=221 / 真实=120）。
        用 offscreen 量会导致"到处都超宽"的假失败。
        故本测试**不设** `QT_QPA_PLATFORM`，由默认平台跑。

    ⚠️ 只检查 `currentText()` 非空的 combo：
        网段/网卡 combo 构造时还没有数据（要等 netsh 查完），文本为空，
        此时宽度由 `_resize_adapter_combo()` 在数据到达后重设，不该在这里断言。
    """
    from PySide6.QtWidgets import QApplication, QComboBox

    app = QApplication.instance() or QApplication([])

    from app.ui.flow_row import COMBO_CHROME_W
    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1100, 720)
    win.show()
    app.processEvents()

    problems: list[str] = []

    for idx in (1, 2):
        win.goto_page(idx)
        for _ in range(4):
            app.processEvents()
        page = win._pages.widget(idx)
        # ⚠️ 必须用 `findChildren(QComboBox)` 而不是 `dir(page)`：
        # `dir()` 会把**方法名**也列出来，本项目里恰好有 `_on_adapter_changed`
        # 之类以下划线开头的可调用对象，取 `getattr` 后会拿到 function，
        # 再调 `.currentText()` 就抛 `AttributeError`（本测试踩过）。
        for combo in page.findChildren(QComboBox):
            if not combo.isVisible():
                continue
            text = combo.currentText()
            if not text:
                continue
            need = combo.fontMetrics().horizontalAdvance(text) + COMBO_CHROME_W
            if combo.width() < need:
                problems.append(
                    f"page{idx}.{combo.objectName() or type(combo).__name__}: "
                    f"宽={combo.width()} < 文本{text!r}所需{need}"
                )

    assert not problems, (
        "以下下拉框宽度不足以完整显示当前文本（会遮挡/截断）：\n  "
        + "\n  ".join(problems)
        + f"\n修法：按「文本宽 + {COMBO_CHROME_W}」设 setMinimumWidth/setMaximumWidth。"
    )
