# NetTool · Windows 本地网络测试管理工具

一款 Windows 桌面端的轻量网络运维助手，把「改 IP/DNS、扫局域网、ping 诊断」三件日常网络调试高频操作，收进一个界面美观、操作极简的单文件小程序。

- **技术栈**：Python 3.13 + PySide6 (Qt6) + QSS 深色主题
- **唯一运行依赖**：`PySide6`
- **四页结构**：网络配置切换 / 局域网扫描 / Ping 工具 / 网络检查

---

## 1. 快速开始（开发期）

**最简单：直接双击 `启动.bat`** —— 它会自动找到已装 PySide6 的 Python 解释器并启动。

手动运行：

```bash
# 1) 安装依赖（建议使用虚拟环境）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2) 运行
python main.py
```

> ⚠️ **注意解释器**：`python` 必须是**已安装 PySide6** 的那个。
> 若本机 PATH 里的 `python` 没装 PySide6，会报 `ModuleNotFoundError: No module named 'PySide6'`。
> 此时有两种做法：
> 1. 用虚拟环境（推荐）：建好 `.venv` 后，`启动.bat` 会自动优先使用它；
> 2. 在 `启动.bat` 同级目录新建一个 **`python.path`** 文件，里面只写一行——你那个已装 PySide6 的
>    `python.exe` 完整路径。该文件已被 `.gitignore` 忽略，不会随仓库发布。
>
> 装 PySide6 若官方源过慢，可用国内镜像：
> `pip install PySide6 -i https://mirrors.aliyun.com/pypi/simple/`

`启动.bat` / `打包.bat` 的解释器查找顺序固定为：
**`python.path` → `.venv\Scripts\python.exe` → PATH 里的 `python`**，
并且会逐个实际 `import PySide6` 验证，只采用真正可用的那个；全都不行时打印处理指引。

---

## 2. 打包为单文件 exe

双击 **`打包.bat`**，或手动执行：

```bash
pip install "pyinstaller>=6.0"
python -m PyInstaller --noconfirm --clean NetTool.spec
```

产物：`dist\NetTool.exe`（onefile + windowed，双击即可运行）。

> ⚠️ 打包必须用**已装 PySide6 的解释器**，否则 PyInstaller 分析阶段找不到 PySide6 会直接失败。
> `打包.bat` 已内置：先校验 PySide6、再按需安装 PyInstaller（装进同一解释器），然后打包。

> 配置文件写入 `%APPDATA%\NetTool\profiles.json`，**与 exe 所在目录无关**，
> 因此 onefile 打包后不会写进临时解压目录。

---

## 3. 功能概览

### 页面一 · 网络配置管理
- 保存多套网络配置方案（名称 / 网卡 / 静态或 DHCP / IP / 掩码 / 网关 / 主备 DNS / 描述）。
- 一键「应用此方案」，需要时按需触发 UAC 提权（用时提权，不启动即提权）。
- 「生效中」标记**自动比对**系统网卡实际配置得出（数量可为 0 / 1 / 多个），支持手动「刷新状态」。
- 方案导入 / 导出（JSON）、恢复 DHCP 快捷按钮。

### 页面二 · 局域网扫描
- 对 `x.x.x.1 ~ x.x.x.254` 共 254 个地址并发 ping 探测（默认 32 并发，1s 超时）。
- 16×16 自绘网格三态可视化：在线绿 / 离线红 / 未检测背景色。
- 悬停 tooltip、单击查看详情、**双击在线格打开网络共享** `\\IP`、右键菜单（打开共享 / 快速 Ping / 复制 IP）。
- 在线列表、统计与进度条、可随时停止并保留已完成结果。

### 页面三 · Ping 工具
- 单次 ping（4 包 + 汇总）与持续 ping（间隔可选 0.5 / 1 / 2 / 5 秒）。
- 日志带时间戳 / 延迟 / TTL，成功绿、失败红，最多保留 500 行。
- 统计：发送 / 接收 / 丢失(丢包率) / 平均 / 最小 / 最大。
- 常用地址快捷按钮（网关 / 8.8.8.8 / 114.114.114.114 / 本机）。

### 页面四 · 网络检查
- 本机网络信息（网卡 / 内网 IP / 网关 / 掩码）自动读取。
- 公网 IPv4 / IPv6 实时获取；公网 IPv4 经四源交叉校验（国内 IP9、PCOnline；国际 Cloudflare、IPinfo.io），
  结果按「国内 / 国际」分行展示，并标注归属地与运营商。
- 常用站点连通性与延迟彩点条；站点图标三级回退（站点 favicon → 镜像 → 文字占位符）。
- 右上角「重新检测」一键刷新，全部结果流式上屏、逐项落位。

### 快捷键
- `Ctrl+1` ~ `Ctrl+4`：在四个页面间快速切换。
- Ping 地址输入框支持回车直接执行。

---

## 4. 目录结构

```
C:\code\network\
├── main.py                  # 入口（GUI + 提权子进程双入口）
├── requirements.txt         # 依赖声明（仅 PySide6）
├── NetTool.spec             # PyInstaller 打包配置
├── 启动.bat / 打包.bat       # 一键启动 / 打包脚本
├── README.md / LICENSE      # 对外文档与开源协议
├── .gitignore               # 忽略构建产物、缓存与 internal/
├── python.path              # （本地自建，已忽略）可选，写一行解释器完整路径
├── app/
│   ├── constants.py         # 全部常量、语义色板、netsh 关键词表
│   ├── paths.py             # 统一路径（%APPDATA%\NetTool）
│   ├── validators.py        # 纯函数校验
│   ├── netsh_parser.py      # netsh 输出解析纯函数（零 Qt 依赖）
│   ├── models.py            # dataclass 数据模型
│   ├── config_service.py    # 方案 CRUD + 原子写 + 生效比对
│   ├── netsh_service.py     # netsh 命令构造与执行
│   ├── elevation.py         # 提权（路径B）+ 子进程执行器
│   ├── procutil.py          # 子进程静默执行（CREATE_NO_WINDOW）
│   ├── scanner.py           # 线程池扫描 + 100ms 节流合批
│   ├── ping_service.py      # ping 封装 + 日志 + 防堆积
│   ├── netcheck_service.py  # 网络检查（公网 IP / 多源校验 / 站点连通性）
│   └── ui/
│       ├── styles.py        # QSS 模板 + 变量替换（build_qss）
│       ├── widgets.py       # Toast / ResultBanner / ProfileCard / ChipButton
│       ├── flow_row.py      # FlowRow 与控件宽度自适应
│       ├── thread_bridge.py # 工作线程 → 主线程信号桥
│       ├── scan_grid.py     # 自绘扫描网格 ScanGridWidget
│       ├── sidebar.py       # 左侧导航
│       ├── page_config.py   # 页面一
│       ├── page_scan.py     # 页面二
│       ├── page_ping.py     # 页面三
│       ├── page_netcheck.py # 页面四
│       └── main_window.py   # 主窗口
├── tests/                   # 单元测试 + GUI 冒烟测试
├── tools/                   # 开发期辅助脚本（截图 / 几何探针 / 审计）
└── internal/                # 内部资料：PRD / 架构 / 原型 / 测试记录（已 gitignore，不开源）
```

---

## 5. 权限说明

- 程序启动时以**普通权限**运行，界面全部可浏览。
- 「应用此方案」「恢复 DHCP」等需管理员权限的操作，才按需弹 UAC。
- 拒绝 UAC 不会崩溃，状态栏/结果条提示「需管理员权限，操作已取消」。
- 「打开网络共享」使用普通用户凭据访问 UNC 路径，**不需要提权**。

---

## 6. 常见问题

| 问题 | 说明 |
| --- | --- |
| 双击 `启动.bat` 提示 `No Python interpreter with PySide6 was found` | 三个候选（`python.path` / `.venv` / PATH 里的 `python`）都不可用。按脚本打印的指引任选一种：新建 `.venv`、或把依赖装进 PATH 的 `python`、或在脚本同级新建 `python.path` 写一行解释器完整路径。 |
| 扫描结果全是红色（离线） | 目标网段无设备，或本机防火墙/网段选错；可降低并发重试。 |
| 打开共享弹出空白窗口 | 目标主机未开放共享，属系统行为；状态栏会提前提示。 |
| 切换方案失败 | 查看红色结果条中的 netsh 原始错误（如网卡名不存在 / 权限不足）。 |
| 配置文件损坏 | 自动备份为 `profiles.json.bak` 并重建空配置。 |

---

## 7. 许可证

本项目以 **MIT License** 开源，详见 [LICENSE](LICENSE)。

