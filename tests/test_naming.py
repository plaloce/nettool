"""项目命名一致性守卫（开源准备，2026-11-07）。

背景：`NetTool` 作为品类通用名偏弱，开源前统一为「名 + 副标题」：
    显示名 APP_NAME    = "NetTool"
    副标题 APP_TAGLINE = "网络配置切换与连通性诊断"
    标题   APP_TITLE   = "NetTool · 网络配置切换与连通性诊断"

凡是**对外可见**的名字都必须由 `app/constants.py` 派生，禁止各写一份：
窗口标题、侧栏品牌区、README 标题、exe 版本资源（版本号也要与 APP_VERSION 同步）。

本文件锁死这些一致性；改动文案只应改 `constants.py` 一行，其余由守卫逼着同步。
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.constants import APP_NAME, APP_TAGLINE, APP_TITLE, APP_VERSION

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    # utf-8-sig：部分文件带 BOM（styles.py 历史遗留）
    return (ROOT / rel).read_text(encoding="utf-8-sig")


# --------------------------------------------------------------------------- #
# 1) 常量本身
# --------------------------------------------------------------------------- #
def test_name_and_tagline_are_composed() -> None:
    assert APP_NAME == "NetTool", "项目名已定为 NetTool，改名需同步全仓（含 %APPDATA% 目录迁移）"
    assert APP_TAGLINE.strip(), "副标题不能为空"
    assert "NetTool" not in APP_TAGLINE, "副标题不应重复项目名"
    assert APP_TITLE == f"{APP_NAME} · {APP_TAGLINE}", "APP_TITLE 必须由名字 + 副标题派生"


def test_version_is_plain_semver_prefix() -> None:
    major_minor = APP_VERSION.split(".")
    assert len(major_minor) == 2 and all(p.isdigit() for p in major_minor), (
        f"APP_VERSION 期望 major.minor 两段数字，实际 {APP_VERSION!r}"
    )


# --------------------------------------------------------------------------- #
# 2) README 标题与副标题
# --------------------------------------------------------------------------- #
def test_readme_h1_is_app_title() -> None:
    lines = _read("README.md").lstrip().splitlines()
    assert lines, "README 为空"
    assert lines[0] == f"# {APP_TITLE}", (
        f"README 一级标题必须是 '# {APP_TITLE}'，实际为 {lines[0]!r}"
    )


# --------------------------------------------------------------------------- #
# 3) 打包配置：exe 名 + 版本资源
# --------------------------------------------------------------------------- #
def _spec_exe_kwargs() -> dict[str, object]:
    tree = ast.parse(_read("NetTool.spec"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "EXE":
            return {
                kw.arg: kw.value.value
                for kw in node.keywords
                if kw.arg and isinstance(kw.value, ast.Constant)
            }
    raise AssertionError("NetTool.spec 里找不到 EXE(...) 调用")


def test_spec_names_exe_after_app_name() -> None:
    kwargs = _spec_exe_kwargs()
    assert kwargs.get("name") == APP_NAME, f"exe 名应为 {APP_NAME}，实际 {kwargs.get('name')!r}"
    assert kwargs.get("console") is False, "必须保持 windowed（无控制台黑窗）"
    assert kwargs.get("version") == "version_info.txt", (
        "EXE 必须挂上版本资源，否则 exe 属性页产品名是空白"
    )


def _version_info_strings() -> dict[str, str]:
    """把 version_info.txt 里所有 StringStruct(k, v) 收集成 dict（纯 AST，不依赖 PyInstaller）。"""
    tree = ast.parse(_read("version_info.txt"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "StringStruct":
            key, value = node.args
            out[key.value] = value.value
    return out


def test_version_info_matches_app_metadata() -> None:
    info = _version_info_strings()
    assert info.get("ProductName") == APP_NAME
    assert info.get("InternalName") == APP_NAME
    assert info.get("OriginalFilename") == f"{APP_NAME}.exe"
    assert info.get("FileDescription") == APP_TITLE, (
        "exe 属性里的文件描述必须与 APP_TITLE 一致"
    )
    major, minor = APP_VERSION.split(".")
    assert info.get("FileVersion") == f"{major}.{minor}.0.0"
    assert info.get("ProductVersion") == f"{major}.{minor}.0.0"


def test_version_info_fixedfileinfo_matches_app_version() -> None:
    tree = ast.parse(_read("version_info.txt"))
    tuple_versions: dict[str, tuple[int, ...]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FixedFileInfo":
            for kw in node.keywords:
                if kw.arg in {"filevers", "prodvers"} and isinstance(kw.value, ast.Tuple):
                    tuple_versions[kw.arg] = tuple(el.value for el in kw.value.elts)
    assert tuple_versions, "version_info.txt 缺少 FixedFileInfo(filevers/prodvers)"
    major, minor = (int(p) for p in APP_VERSION.split("."))
    for key, value in tuple_versions.items():
        assert value[:2] == (major, minor), (
            f"{key} 的前两段应为 ({major}, {minor})，实际 {value}"
        )


# --------------------------------------------------------------------------- #
# 4) UI 必须引用常量，不许自己写一份
# --------------------------------------------------------------------------- #
def test_sidebar_renders_shared_tagline() -> None:
    src = _read("app/ui/sidebar.py")
    tree = ast.parse(src)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.constants":
            imported |= {a.name for a in node.names}
    assert {"APP_NAME", "APP_TAGLINE", "APP_TITLE"} <= imported, (
        "侧栏品牌区必须从 app.constants 取名字/副标题/标题"
    )

    label_args = [
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "QLabel"
        and node.args
        and isinstance(node.args[0], ast.Name)
    ]
    assert "APP_TAGLINE" in label_args, "侧栏没有渲染 APP_TAGLINE 副标题"

    # 副标题标签必须挂上 QSS 规则名，否则深色主题下可能白底白字
    assert 'setObjectName("brandSub")' in src, "副标题标签缺少 objectName=brandSub"
    assert "#brandSub" in _read("app/ui/styles.py"), "#brandSub 缺少 QSS 规则"


def test_window_title_uses_app_title() -> None:
    src = _read("app/ui/main_window.py")
    assert "setWindowTitle(APP_TITLE)" in src, "主窗口标题必须用 APP_TITLE"


# --------------------------------------------------------------------------- #
# 5) 启动脚本里的提示标识
# --------------------------------------------------------------------------- #
def test_launcher_bats_mention_app_name() -> None:
    for rel in ("启动.bat", "打包.bat"):
        assert APP_NAME in _read(rel), f"{rel} 里应出现项目名 {APP_NAME}，便于用户确认在跑哪个程序"


# --------------------------------------------------------------------------- #
# 6) 旧副标题不得残留（防半改）
# --------------------------------------------------------------------------- #
def test_no_stale_tagline_left_behind() -> None:
    stale = "网络测试" + "管理工具"  # 拼接避免本文件自命中
    suspects = [
        "README.md",
        "NetTool.spec",
        "version_info.txt",
        "app/constants.py",
        "app/ui/sidebar.py",
        "app/ui/main_window.py",
    ]
    hits = [rel for rel in suspects if stale in _read(rel)]
    assert not hits, f"旧副标题仍残留在：{hits}（改名必须一次改到底）"
