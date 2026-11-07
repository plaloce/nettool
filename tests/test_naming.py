"""项目命名与窗口「外壳」一致性守卫（开源准备）。

背景与约定：
    显示名   APP_NAME        = "NetTool"
    副标题   APP_TAGLINE     = "网络配置切换与连通性诊断"
    标题     APP_TITLE       = "NetTool · 网络配置切换与连通性诊断"（README H1 / exe 属性）
    窗口标题 APP_WINDOW_TITLE = "NetTool · 网络配置切换与连通性诊断 v1.2.1"

三条用户实拍反馈固化成守卫：
1. 标题栏曾出现**两个** "NetTool"（Qt 会把 applicationDisplayName 追加到标题后）
   ⇒ main.py 禁止调用 setApplicationDisplayName，窗口标题统一走 APP_WINDOW_TITLE。
2. 版本号曾同时出现在左下角和标题栏（重复）⇒ 侧栏不再渲染 versionLabel。
3. 状态栏右下角需要「GitHub 项目」链接 ⇒ 真实构造主窗口点一下，验证真的会开浏览器。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.constants import (
    APP_NAME,
    APP_TAGLINE,
    APP_TITLE,
    APP_VERSION,
    APP_WINDOW_TITLE,
    PROJECT_URL,
)

ROOT = Path(__file__).resolve().parents[1]

# 契约字面量：链接必须指向这个真实仓库地址。
# 故意写死而**不**从 constants 取 —— 否则把 PROJECT_URL 改错时，被测代码和断言
# 会一起变成错的（自洽假绿，2026-11-08 变异测试实锤抓到过一次）。
EXPECTED_PROJECT_URL = "https://github.com/plaloce/nettool"


def _read(rel: str) -> str:
    # utf-8-sig：部分文件带 BOM（styles.py 历史遗留）
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _version_tuple() -> tuple[int, int, int, int]:
    """APP_VERSION → 四段定长元组（Windows 版本资源要求 4 段，缺位补 0）。"""
    parts = [int(p) for p in APP_VERSION.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return parts[0], parts[1], parts[2], parts[3]


# --------------------------------------------------------------------------- #
# 1) 常量本身
# --------------------------------------------------------------------------- #
def test_name_and_tagline_are_composed() -> None:
    assert APP_NAME == "NetTool", "项目名已定为 NetTool，改名需同步全仓（含 %APPDATA% 目录迁移）"
    assert APP_TAGLINE.strip(), "副标题不能为空"
    assert "NetTool" not in APP_TAGLINE, "副标题不应重复项目名"
    assert APP_TITLE == f"{APP_NAME} · {APP_TAGLINE}", "APP_TITLE 必须由名字 + 副标题派生"


def test_version_is_three_segments() -> None:
    parts = APP_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"版本号必须是 x.y.z 三段（如 1.2.1），实际 {APP_VERSION!r}"
    )


def test_window_title_is_title_plus_version() -> None:
    assert APP_WINDOW_TITLE == f"{APP_TITLE} v{APP_VERSION}"
    # 标题栏里项目名只应出现一次（否则又是"两个 NetTool"）
    assert APP_WINDOW_TITLE.count(APP_NAME) == 1


def test_main_never_sets_application_display_name() -> None:
    """Qt 会把 displayName 追加到窗口标题后 ⇒ 标题栏出现第二个 "NetTool"。"""
    code = ast.parse(_read("main.py"))
    called = {
        node.func.attr
        for node in ast.walk(code)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "setApplicationDisplayName" not in called, (
        "main.py 不得调用 setApplicationDisplayName（会让标题栏重复显示项目名）"
    )


# --------------------------------------------------------------------------- #
# 2) README 与打包配置
# --------------------------------------------------------------------------- #
def test_readme_h1_is_app_title() -> None:
    lines = _read("README.md").lstrip().splitlines()
    assert lines, "README 为空"
    assert lines[0] == f"# {APP_TITLE}", (
        f"README 一级标题必须是 '# {APP_TITLE}'，实际为 {lines[0]!r}"
    )


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


# --------------------------------------------------------------------------- #
# 3) exe 版本资源（Windows 属性页）
# --------------------------------------------------------------------------- #
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
    assert info.get("FileDescription") == APP_TITLE, "exe 属性里的文件描述必须与 APP_TITLE 一致"
    expected = ".".join(str(p) for p in _version_tuple())
    assert info.get("FileVersion") == expected
    assert info.get("ProductVersion") == expected


def test_version_info_fixedfileinfo_matches_app_version() -> None:
    tree = ast.parse(_read("version_info.txt"))
    found: dict[str, tuple[int, ...]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FixedFileInfo":
            for kw in node.keywords:
                if kw.arg in {"filevers", "prodvers"} and isinstance(kw.value, ast.Tuple):
                    found[kw.arg] = tuple(el.value for el in kw.value.elts)
    assert found, "version_info.txt 缺少 FixedFileInfo(filevers/prodvers)"
    expected = _version_tuple()
    for key, value in found.items():
        assert value == expected, f"{key} 应为 {expected}，实际 {value}"


# --------------------------------------------------------------------------- #
# 4) 侧栏：品牌 + 副标题，但**不再有版本号**
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


def test_sidebar_no_longer_shows_version() -> None:
    """版本号已移到标题栏，左下角再显示就是重复（2026-11-08 用户要求）。"""
    assert "versionLabel" not in _read("app/ui/sidebar.py"), "侧栏不应再渲染版本号标签"
    assert "#versionLabel" not in _read("app/ui/styles.py"), "#versionLabel 规则应一并删除"


# --------------------------------------------------------------------------- #
# 5) 状态栏：主窗口标题 + 右下角 GitHub 链接（真实构造窗口验证）
# --------------------------------------------------------------------------- #
def test_window_title_uses_app_window_title() -> None:
    src = _read("app/ui/main_window.py")
    assert "setWindowTitle(APP_WINDOW_TITLE)" in src, "主窗口标题必须用 APP_WINDOW_TITLE"
    assert "setWindowTitle(APP_TITLE)" not in src, (
        "标题栏必须带版本号（APP_WINDOW_TITLE），不能只用 APP_TITLE"
    )


def test_statusbar_has_github_link_and_opens_browser(qapp, monkeypatch) -> None:
    """真造一个主窗口：链接存在、文案/URL 正确、点击真的会调系统浏览器。"""
    from app.ui import main_window as mw

    assert PROJECT_URL == EXPECTED_PROJECT_URL, (
        f"PROJECT_URL 应指向真实仓库 {EXPECTED_PROJECT_URL}，实际 {PROJECT_URL}"
    )

    window = mw.MainWindow()
    try:
        link = window._github_link
        assert link.objectName() == "githubLink"
        assert "GitHub 项目" in link.text(), "链接文案应为「GitHub 项目」"
        assert EXPECTED_PROJECT_URL in link.text(), "链接 href 必须是真实仓库地址"
        assert link.textFormat().name == "RichText", "必须是富文本链接才能点"
        assert link.parent() is not None, "链接必须被放进状态栏（否则不显示）"

        # 模拟真实点击：QLabel 点击时会发 linkActivated
        opened: list[str] = []

        class _Recorder:
            @staticmethod
            def openUrl(url) -> bool:  # noqa: N802 - 对齐 Qt 命名
                opened.append(url.toString())
                return True

        monkeypatch.setattr(mw, "QDesktopServices", _Recorder)
        link.linkActivated.emit(EXPECTED_PROJECT_URL)
        assert opened == [EXPECTED_PROJECT_URL], f"点击链接没有打开项目主页，实际：{opened}"
    finally:
        window.deleteLater()


# --------------------------------------------------------------------------- #
# 6) 启动脚本 / 旧文案残留
# --------------------------------------------------------------------------- #
def test_launcher_bats_mention_app_name() -> None:
    for rel in ("启动.bat", "打包.bat"):
        assert APP_NAME in _read(rel), f"{rel} 里应出现项目名 {APP_NAME}，便于用户确认在跑哪个程序"


@pytest.mark.parametrize("rel", ["README.md", "NetTool.spec", "version_info.txt",
                                 "app/constants.py", "app/ui/sidebar.py",
                                 "app/ui/main_window.py"])
def test_no_stale_tagline_left_behind(rel: str) -> None:
    stale = "网络测试" + "管理工具"  # 拼接避免本文件自命中
    assert stale not in _read(rel), f"旧副标题仍残留在 {rel}（改名必须一次改到底）"
