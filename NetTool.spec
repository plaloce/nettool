# -*- mode: python ; coding: utf-8 -*-
"""NetTool PyInstaller 打包配置（onefile + windowed）。

用法：
    pyinstaller NetTool.spec
产物：
    dist/NetTool.exe

说明：
- onefile 单文件；windowed 无控制台黑窗。
- 配置文件写入 %APPDATA%\\NetTool（app/paths.py 用 os.environ["APPDATA"] 拼路径，
  **故意不用 QStandardPaths**，见架构 §5.4；这样业务层零 Qt 依赖、可脱离 GUI 单测），
  与 exe 所在目录无关，exe 换位置不影响已有配置。
- 提权子进程用的是同一个 exe（main.py 检测 --exec-netsh 分支）。
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # PySide6 三大模块（入口脚本用到）
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        # app 包内模块：入口多为函数内延迟导入，显式列出以免个别模块被漏收
        'app',
        'app.constants',
        'app.paths',
        'app.validators',
        'app.models',
        'app.netsh_parser',
        'app.config_service',
        'app.netsh_service',
        'app.elevation',
        'app.scanner',
        'app.ping_service',
        # 2026-09-21 新增：静默子进程封装（CREATE_NO_WINDOW），app/ 内所有
        # subprocess 调用的唯一入口；漏收则打包后运行期 ImportError。
        'app.procutil',
        # ⚠️ 2026-09-21 补齐：网络检查页（页面四）及其依赖在 spec 写就之后才加进来，
        #    原清单漏了这 4 个模块。它们目前都走 main_window 的顶层 import，
        #    PyInstaller 静态分析能跟上，所以旧包在实机上「看起来」正常；
        #    但一旦以后改成函数内延迟导入就会静默漏收 ⇒ 一律显式列出。
        'app.netcheck_service',
        'app.ui',
        'app.ui.styles',
        'app.ui.widgets',
        'app.ui.scan_grid',
        'app.ui.sidebar',
        'app.ui.page_config',
        'app.ui.page_scan',
        'app.ui.page_ping',
        'app.ui.page_netcheck',
        'app.ui.flow_row',
        'app.ui.thread_bridge',
        'app.ui.main_window',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 精简体积：排除用不到的大模块
        'tkinter',
        'PySide6.QtNetwork',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.Qt3DCore',
        'PySide6.QtMultimedia',
        'PySide6.QtCharts',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NetTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # --windowed：无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 版本资源：exe 属性页显示统一的产品名 / 副标题（版本号须与 APP_VERSION 一致，
    # 守卫见 tests/test_naming.py）
    version='version_info.txt',
    # icon='resources/nettool.ico',   # 图标待设计，暂不设置（U8）
)
