"""开源仓库卫生守卫（2026-11-08 首次 push 到 GitHub 后补）。

目的：把"不该出现在公开仓库里的东西"变成会失败的测试，而不是靠人记。
覆盖三类：
1. **本机绝对路径**（`C:\\code\\...`、`C:\\Users\\...`）—— 会暴露作者目录结构；
   注意 `C:\\Windows\\System32\\netsh.exe` 这类系统路径是**合法**的，不在禁止之列。
2. **开源协议与忽略清单**必须齐全，否则 clone 下来会带上构建产物/内部资料。
3. LICENSE 里的版权人与年份。

背景：首次 push 后匿名抓取仓库页面发现 README 目录树写了 `C:\\code\\network\\`，
说明"扫过一遍没命中"不等于零风险（当时的扫描只查了 `C:\\Users\\`）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 本机专属路径前缀（系统路径不在此列）
FORBIDDEN = ("C:\\code\\", "C:\\Users\\", "/c/code/", "/Users/")

# 不参与扫描的目录：内部资料 / 构建产物 / 缓存 / 版本库 / 渲染输出
SKIP_DIRS = {".git", "internal", "build", "dist", "__pycache__", ".workbuddy",
             "shots", ".venv", ".pytest_cache"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".ico", ".pyc", ".pyd", ".exe", ".pkg", ".zip"}
# 按文件名跳过：
#   python.path      —— 本机自建的 gitignore 文件，里面本来就该是本机解释器路径
#   test_repo_hygiene —— 本文件自身，FORBIDDEN 常量里就写着这些 needle
SKIP_NAMES = {"python.path", "test_repo_hygiene.py"}


def _iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_NAMES:
            continue
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        yield rel, path


def test_no_local_absolute_paths_in_source() -> None:
    hits: list[str] = []
    for rel, path in _iter_text_files():
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for needle in FORBIDDEN:
                if needle in line:
                    hits.append(f"{rel.as_posix()}:{lineno}  含 {needle!r}")
    assert not hits, "公开仓库里不得出现本机绝对路径：\n" + "\n".join(hits)


def test_license_present_and_mit() -> None:
    lic = ROOT / "LICENSE"
    assert lic.is_file(), "缺少 LICENSE（开源协议）"
    text = lic.read_text(encoding="utf-8")
    assert text.startswith("MIT License"), "LICENSE 应为 MIT 协议"
    assert "Copyright (c) 2026 plaloce" in text, "版权人/年份应与项目约定一致"


def test_gitignore_covers_artifacts_and_internal() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required = [
        "build/", "dist/", "internal/", ".workbuddy/",
        "python.path", "tools/shots/", ".venv/", "__pycache__/",
    ]
    missing = [item for item in required if item not in ignore]
    assert not missing, f".gitignore 缺少这些条目：{missing}"


def test_readme_does_not_advertise_gitignored_tree_as_present() -> None:
    """README 目录树若列 internal/，必须紧跟"不开源/已忽略"的说明，避免读者以为 clone 后能找到。"""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for line in readme.splitlines():
        if "internal/" in line and ("├──" in line or "└──" in line):
            assert "gitignore" in line or "不开源" in line, (
                f"README 目录树列了 internal/ 但没说明它不开源：{line.strip()}"
            )
