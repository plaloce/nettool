"""NetTool 全局常量（**所有魔法值的唯一来源**）。

约定（见架构设计 13.2）：其余模块一律 `from app.constants import ...`，
不得在任何其他文件里硬编码色值、上限、默认值或 netsh 关键词。
"""

# --------------------------------------------------------------------------- #
# 应用元信息
# --------------------------------------------------------------------------- #
APP_NAME: str = "NetTool"
# 副标题（tagline）：对外唯一的定位描述，窗口标题 / 侧栏 / README / exe 属性
# 全部由它派生，改文案只改这一行（守卫 tests/test_naming.py 锁死一致性）。
APP_TAGLINE: str = "网络配置切换与连通性诊断"
APP_VERSION: str = "1.2"
APP_TITLE: str = f"{APP_NAME} · {APP_TAGLINE}"

# 主窗口默认/最小尺寸
WINDOW_DEFAULT_W: int = 1100
WINDOW_DEFAULT_H: int = 720
WINDOW_MIN_W: int = 960
WINDOW_MIN_H: int = 640

# 侧边导航栏固定宽度
SIDEBAR_WIDTH: int = 200

# 全局字体
FONT_FAMILY: str = "Microsoft YaHei UI"
FONT_SIZE_PT: int = 13
FONT_MONO: str = "Consolas"

# --------------------------------------------------------------------------- #
# 语义化色板（PRD 8.2 的 18 个语义色，键名用下划线形式以适配 string.Template）
# --------------------------------------------------------------------------- #
PALETTE: dict[str, str] = {
    "bg_root": "#0D1117",          # 背景·最深（主窗口）
    "bg_surface": "#161B22",       # 背景·次深（侧栏/面板）
    "bg_card": "#1C2128",          # 背景·卡片
    "bg_hover": "#21262D",         # 背景·悬停
    "bg_cell_idle": "#2D333B",     # 背景·未扫描格
    "border_default": "#30363D",   # 边框·常规
    "border_focus": "#388BFD",     # 边框·强调
    "text_primary": "#E6EDF3",     # 文字·主
    "text_secondary": "#8B949E",   # 文字·次
    "text_disabled": "#484F58",    # 文字·禁用
    "primary": "#388BFD",          # 主色
    "primary_hover": "#58A6FF",    # 主色·悬停
    "success": "#3FB950",          # 成功·绿
    "success_bg": "#1F3B26",       # 成功·绿·暗底
    "info_bg": "#12283F",          # 信息·蓝·暗底（「国内」组标签的填充底）
    "danger": "#F85149",           # 危险·红
    "danger_bg": "#3D1D1E",        # 危险·红·暗底
    "warning": "#D29922",          # 警告·黄
    "info": "#58A6FF",             # 信息·蓝
    # 交互态补充（PRD 8.5 / 原型 2.4）
    "primary_pressed": "#1F6FEB",  # 主按钮按下
    "danger_hover": "#FF6A63",     # 危险按钮悬停
    "grid_text_on_color": "#0D1117",  # 网格在线/离线格上的深色文字
}

# 状态栏消息级别 -> 语义色 key
STATUS_LEVEL_COLORS: dict[str, str] = {
    "info": "info",
    "warning": "warning",
    "danger": "danger",
    "normal": "text_secondary",
    "success": "success",
}

# 网格状态 -> 填充色 key（未检测态另用 border_default 描边）
GRID_STATE_COLOR: dict[str, str] = {
    "online": "success",
    "offline": "danger",
    "pending": "bg_cell_idle",
}

# --------------------------------------------------------------------------- #
# 日志 / 上限
# --------------------------------------------------------------------------- #
LOG_MAX_LINES: int = 500              # F3-08：日志最多 500 行
NETSH_ERROR_MAX_LEN: int = 500        # 回传 netsh 原始错误文本的截断长度

# --------------------------------------------------------------------------- #
# 扫描（页面二）参数 —— 架构 9.3
# --------------------------------------------------------------------------- #
SCAN_FLUSH_INTERVAL_MS: int = 100        # 结果合批刷新间隔
SCAN_DEFAULT_CONCURRENCY: int = 32       # 默认并发
SCAN_CONCURRENCY_CHOICES: list[int] = [8, 16, 32, 64]
SCAN_DEFAULT_TIMEOUT_MS: int = 1000      # 单地址超时
SCAN_TIMEOUT_CHOICES: list[int] = [500, 1000, 2000]
SCAN_TOTAL_ADDRESSES: int = 254          # 固定 1..254

# --------------------------------------------------------------------------- #
# 网格（自绘控件）参数 —— 架构 6.3
# --------------------------------------------------------------------------- #
GRID_COLS: int = 16
# 格子尺寸由「横向预算」反推，不是随便定的，改前请先算一遍：
#   窗口最小宽 960 → 页面视口 = 960 - SIDEBAR_WIDTH(200) = 760
#   内容 = 页边距(48) + 网格(sizeHint) + spacing(16) + 右栏(SCAN_RIGHT_PANEL_MIN_W)
#   要求 ≤ 760  →  网格 + 右栏 ≤ 696
#   取右栏下限 256、网格 352 → 608 ≤ 696 ✔（留 88px 余量，宽窗口下全部给网格/右栏舒展）
#   网格 352 = 16 列 × (CELL_W + GAP) - GAP + 2×PAD_X
#            = 16 × (17 + 4) - 4 + 12 = 336 - 4 + 12 = 344 … 不够 352，故 CELL_W 取 18：
#            = 16 × (18 + 4) - 4 + 12 = 352 - 4 + 12 = 360
#   ⇒ 本布局下网格最小 360，配合右栏下限 256 合计 616 ≤ 696 ✔
#   格子像素可点面积 = 18×18（≥16px 的最小可点标准）；再缩小会出现「点不中」。
GRID_CELL_W: int = 18
GRID_CELL_H: int = 18
# 自适应放大上限：宽度充裕时格子可放大到此值（视觉更饱满、更好点），
# 但不要无限放大，否则失去"254 格密网"的整体观感。
GRID_CELL_MAX_W: int = 34
GRID_CELL_GAP: int = 4
GRID_CELL_RADIUS: float = 3.0
GRID_PULSE_INTERVAL_MS: int = 80         # 呼吸动画帧间隔
# 网格控件内边距：paintEvent 从 (PAD_X, PAD_Y) 起画第一格，
# sizeHint 必须把这两项算进去，否则最后一列/行会溢到不可见区。
GRID_PAD_X: int = 6
GRID_PAD_Y: int = 6

# 页面二（局域网扫描）右栏宽度区间。
# 横向预算（窗口最小宽 960 → 页面视口 760）：
#   内容 = 页边距(48) + 网格(360) + spacing(16) + 右栏
#   要求 ≤ 760  →  右栏 ≤ 336。MIN 取 256（卡片仍能放下"192.168.1.254"与
#   "打开共享"按钮），保证**最小窗口下网格区也不需要横向滚动条**。
#   MAX 取 360，让宽窗口下右栏能舒展，同时不挤压网格区。
SCAN_RIGHT_PANEL_MIN_W: int = 256        # 保底宽度（最小窗口下仍不挤压网格）
SCAN_RIGHT_PANEL_MAX_W: int = 360        # 宽窗口下的舒展上限

# 网卡下拉的宽度上限：网卡名可能有 "VMware Network Adapter VMnet1"（约 348px）这类超长值，
# 不设上限会让它独占大半行宽、把「重置」按钮挤到第二行。
# 上限内显示不下的部分靠 tooltip + 展开下拉看全名。
ADAPTER_COMBO_MAX_W: int = 200

# --------------------------------------------------------------------------- #
# Ping（页面三）参数
# --------------------------------------------------------------------------- #
PING_SINGLE_COUNT: int = 4               # Q4：单次 ping 4 包
PING_INTERVAL_CHOICES: list[float] = [0.5, 1.0, 2.0, 5.0]
PING_DEFAULT_INTERVAL_S: float = 1.0
PING_DEFAULT_TIMEOUT_MS: int = 1000

# --------------------------------------------------------------------------- #
# 网络检查（页面四）参数 —— PRD-网络检查页 §6
# --------------------------------------------------------------------------- #
# 超时：每个源/站点独立计时，超时只标记该源不可用，**不阻塞其它源**（F4-10）。
# 实测基线（2026-09-20）：ipip.net 0.14s / icanhazip 0.85s / ip-api 1.33s / ipinfo 1.40s
#   → 5s 对国内源余量极大，对国际源（jsDelivr 1600ms、Cloudflare 1142ms）也够。
NETCHECK_TIMEOUT_S: int = 5              # 单源查询超时（秒）
NETCHECK_LATENCY_TIMEOUT_S: int = 5      # 单站点延迟探测超时（秒）

# 延迟颜色分级阈值（PRD §3.3，参考 ip.skk.moe 经验值）
NETCHECK_LATENCY_WARN_MS: int = 300      # > 此值转黄
NETCHECK_LATENCY_DANGER_MS: int = 1000   # > 此值转红

# 列数 / 并发
NETCHECK_INTL_COLS: int = 4              # 站点连通性每行列数（国内一行 4 + 国际一行 4）
# ★ 并发数必须与「一次检查的总任务数」匹配：4 源 + 8 站点 + 2 IPv6 探测 + 1 网卡 = 15 个。
#   低于 15 会让后半批任务排队（8 → 两批 × 5s = 10s+），改为 16 后**一批全部发出**，
#   总耗时 ≈ 最慢的单个任务（标称 5s），不再叠加批次。
NETCHECK_MAX_WORKERS: int = 16

# IPv6 地址较长（实测 240e:391:1a60:...），超此长度中间省略显示
NETCHECK_IPV6_DISPLAY_LEN: int = 26

# 站点连通性「彩点条」= **延迟条形图**（2026-09-20 用户改版）：
#   延迟越小 → 点越少；延迟越大 → 点越多；不可达 → 0 点（全空心）。
#   ⚠️ 旧画法是「历史留痕」（每次检测追加 1 点），首跑只有 1 个彩点、其余空心，
#      用户明确反馈「这么多圈起什么作用」——已废弃，勿改回。
NETCHECK_DOT_COUNT: int = 10             # 彩点条总长（满格 = 最慢）
# (延迟上限 ms, 点亮数)：阈值与三色分级对齐（300 黄线 / 1000 红线）。
# ★ 1500 这一档是实测加的：Cloudflare 1136ms 与 jsDelivr 1746ms 同为红色时，
#   若都给满格就区分不开「更慢」——拆成 9 / 10 两档。
NETCHECK_DOT_BANDS: tuple[tuple[int, int], ...] = (
    (100, 2),    # 极快
    (300, 4),    # 快（≤300 仍是绿色）
    (600, 6),    # 偏慢
    (1000, 8),   # 慢（≤1000 仍是黄色）
    (1500, 9),   # 红色初段
    (10**9, 10),  # 红色深段 + 满格
)

# 占位符
NETCHECK_VALUE_DASH: str = "--"

# --------------------------------------------------------------------------- #
# 多源交叉验证数据源（借鉴 ip.skk.moe 卡片：源名 + 国内/国际标签 + IP + 归属地）。
# ★ 4 个「带归属地」的源：**2 国内（IP9 / PCOnline）+ 2 国际（Cloudflare / IPinfo.io）**。
#   纯 IP 源（icanhazip / ident.me / ip.sb）拿不到归属地，退出交叉验证展示。
# (显示名, 官网链接[用于页脚超链接], 分组标签)；API 端点见 netcheck_service._SOURCE_SPECS。
#
# 演进：2026-09-20 由 ipip.net / ip-api.com / ipwho.is 换成「国内 JSON 源 + 国际 Cloudflare」；
#   ★ 2026-09-21 国内两源**再次更换**为 IP9 / PCOnline —— 更早的两个国内源经用户
#     真机实测双双拿不到结果，且其中一个的官方 API 端点需付费 token（零配置本地 app
#     不可用），均已弃用。
#   · IP9        国内  https://ip9.com.cn/get                           中文归属地（免 key JSON，实测 136ms）
#   · PCOnline   国内  https://whois.pconline.com.cn/ipJson.jsp         中文归属地（免 key JSON，实测 164ms）
#   · Cloudflare 国际  https://www.cloudflare.com/cdn-cgi/trace         纯文本 trace，**只回 IP 不回归属地**（属预期，仅作 IP 交叉验证）
#   · IPinfo.io  国际  https://ipinfo.io/json                           英文归属地 + ASN（沿用旧源，已验证可用）
#   ⚠️ **关键选型理由**：本卡片交叉验证的是「公网 **IPv4**」。实测大量国际源只回 IPv6
#      （Cloudflare / ip.sb / icanhazip 等），而 IP9 与 PCOnline **是纯 IPv4 端点**，
#       这正是选它们的首要原因。
# --------------------------------------------------------------------------- #
NETCHECK_SOURCES: list[tuple[str, str, str]] = [
    ("IP9", "https://www.ip9.com.cn", "国内"),
    ("PCOnline", "https://www.pconline.com.cn", "国内"),
    ("Cloudflare", "https://www.cloudflare.com", "国际"),
    ("IPinfo.io", "https://ipinfo.io", "国际"),
]

# 组顺序：国内一行、国际一行（顺序即 UI 网格的行序，行首一个大幅组标签）
NETCHECK_SOURCE_GROUPS: tuple[str, ...] = ("国内", "国际")
NETCHECK_SITE_GROUPS: tuple[str, ...] = ("国内", "国际")
# IPv6 地址时由服务层并发探测，填充「本机公网 IP」卡的 IPv6 列（含页脚来源标注）。
NETCHECK_IPV6_PROBES: list[str] = [
    "https://ipv6.icanhazip.com",
    "https://v6.ident.me",
]

# 站点连通性目标集（借鉴 ip.skk.moe 卡片：图标 + 名称 + 延迟 + 彩点条）。
# 排布硬约束：**国内一行 4 个、国际一行 4 个**（页面四 E 卡 = 组标签列 + 4 列 × 2 行），
# 因此清单顺序必须「先国内后国际、每组分恰 4 个」——顺序即 UI 网格排布顺序。
# group 字段不用于逐站点打标签，只用于**分组**（行首一个大幅组标签即可，用户要求）。
# ⚠️ weixin.qq.com 对 HEAD 返回 405 → 服务层必须回退 GET（已实现）。
# ⚠️ github.com / youtube.com 超时是**常态**（境内网络实测），只标灰不报错不弹窗。
# ⚠️ 实测延迟（2026-09-20，真实渲染）：淘宝 199 / 字节 199 / Bilibili 235 / 微信 72
#    / Cloudflare 835 / jsDelivr 1456 / GitHub 超时 ⇒ 三色分级均有真实样本命中。
#
# ★ 2026-09-21 图标源改版：`favicon` 由**单个 URL** 改为**逗号分隔的候选链** ——
#   旧源 `icons.duckduckgo.com` 在境内被墙（实测 8 个站点全部超时，图标清一色
#   退化成文字占位符）。候选链按从左到右的顺序尝试，取第一个通过图片魔数校验的结果
#   （校验见 `netcheck_service._is_image_bytes`），全失败才退化为文字占位符。
#   ① **站点本尊** `https://{host}/favicon.ico` —— 最权威，
#      ⚠️ 但字节跳动官网这条**返回 21 字节的 `text/plain` 假图**（魔数校验会拦掉，
#         绝不能跳过校验直接用），届时自动落到第 2 级。
#   ② **第三方镜像** `https://favicon.cccyun.cc/{host}` —— 返回 `image/webp`，
#      实测 114×114；已验证本机 QPixmap.loadFromData 能吃下 webp 字节。
#      ⚠️ 别加 `.ico` 后缀：`https://favicon.cccyun.cc/{host}.ico` 实测 **HTTP 403**。
#   ③ 文字占位符（沿用既有降级路径）。
#   顺序差异是有意为之：
#   · **国内组直连优先** —— 实测淘宝 / B站 / Cloudflare / jsDelivr 直连可用，免第三方往返；
#   · **国际组镜像优先** —— github.com / youtube.com 直连在境内不通，放第一级纯属白等超时。
NETCHECK_SITES: list[dict[str, str]] = [
    {"name": "字节跳动", "favicon": "https://www.bytedance.com/favicon.ico,https://favicon.cccyun.cc/www.bytedance.com", "url": "https://www.bytedance.com", "group": "国内"},
    {"name": "Bilibili", "favicon": "https://www.bilibili.com/favicon.ico,https://favicon.cccyun.cc/www.bilibili.com", "url": "https://www.bilibili.com", "group": "国内"},
    {"name": "微信", "favicon": "https://weixin.qq.com/favicon.ico,https://favicon.cccyun.cc/weixin.qq.com", "url": "https://weixin.qq.com", "group": "国内"},
    {"name": "淘宝", "favicon": "https://www.taobao.com/favicon.ico,https://favicon.cccyun.cc/www.taobao.com", "url": "https://www.taobao.com", "group": "国内"},
    {"name": "GitHub", "favicon": "https://favicon.cccyun.cc/github.com,https://github.com/favicon.ico", "url": "https://github.com", "group": "国际"},
    {"name": "jsDelivr", "favicon": "https://favicon.cccyun.cc/cdn.jsdelivr.net,https://cdn.jsdelivr.net/favicon.ico", "url": "https://cdn.jsdelivr.net", "group": "国际"},
    {"name": "Cloudflare", "favicon": "https://favicon.cccyun.cc/www.cloudflare.com,https://www.cloudflare.com/favicon.ico", "url": "https://www.cloudflare.com", "group": "国际"},
    {"name": "YouTube", "favicon": "https://favicon.cccyun.cc/www.youtube.com,https://www.youtube.com/favicon.ico", "url": "https://www.youtube.com", "group": "国际"},
]

# --------------------------------------------------------------------------- #
# 提权（路径B）参数 —— 架构 8
# --------------------------------------------------------------------------- #
ELEV_FLAG: str = "--exec-netsh"
ELEV_RESULT_FLAG: str = "--result-file"
ELEV_TIMEOUT_S: int = 60                 # 提权子进程总超时
ELEV_POLL_INTERVAL_MS: int = 200         # 父进程轮询间隔
ELEV_SHELL_SUCCESS_MIN: int = 32         # ShellExecuteW 返回值 > 32 视为成功

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
CONFIG_DEBOUNCE_MS: int = 0
CONFIG_SCHEMA_VERSION: int = 1
PROFILES_FILENAME: str = "profiles.json"
PROFILES_BACKUP_SUFFIX: str = ".bak"
PROFILE_NAME_MAX_LEN: int = 30
PROFILE_DESC_MAX_LEN: int = 100

# --------------------------------------------------------------------------- #
# netsh 关键词 / 正则表（架构 7.3）—— 中英文双保险
# 说明：先"语言探测"（是否出现中文关键词），再按对应语言的正则抽值。
# --------------------------------------------------------------------------- #
KEYWORDS: dict[str, list[str]] = {
    "dhcp_on": [
        r"DHCP\s*已启用\s*[:：]\s*(?:是|Yes|yes)",
        r"DHCP\s*enabled\s*[:：]\s*(?:Yes|yes)",
    ],
    "dhcp_off": [
        r"DHCP\s*已启用\s*[:：]\s*(?:否|No|no)",
        r"DHCP\s*enabled\s*[:：]\s*(?:No|no)",
    ],
    "ip": [
        r"IP\s*地址\s*[:：]\s*([\d.]+)",
        r"IP\s*Address\s*[:：]\s*([\d.]+)",
    ],
    "prefix": [
        r"子网前缀\s*[:：]\s*([\d.]+)\s*/\s*(\d+)",
        r"Subnet\s*Prefix\s*[:：]\s*([\d.]+)\s*/\s*(\d+)",
    ],
    "mask": [
        r"掩码\s*[:：]?\s*([\d.]+)",
        r"Mask\s*[:：]?\s*([\d.]+)",
    ],
    "gateway": [
        r"默认网关\s*[:：]\s*([\d.]+)",
        r"Default\s*Gateway\s*[:：]\s*([\d.]+)",
    ],
    "dns": [
        r"DNS\s*服务器\s*[:：]",
        r"DNS\s*servers\s*[:：]",
    ],
    # 接口名可能含空格（如 "以太网 2"），故用「引号优先，否则吃到行尾」的双分支。
    "adapter_cn": [
        r"接口\s*\"([^\"]+)\"\s*的配置",
        r"接口\s*([^\"\s].*?)\s*的配置",
        r"配置\s*for\s*接口\s*\"([^\"]+)\"",
        r"配置\s*for\s*接口\s*([^\"\s].*?)\s*$",
    ],
    "adapter_en": [
        r"Configuration\s*for\s*interface\s*\"([^\"]+)\"",
        r"Configuration\s*for\s*interface\s*([^\"\s].*?)\s*$",
    ],
}

# netsh set 执行结果中的错误关键词（架构 7.5）
NETSH_ERROR_MARKERS: list[str] = [
    "错误", "失败", "找不到", "拒绝访问", "参数错误", "未找到", "无法",
    "Access is denied", "The parameter is incorrect", "not recognized",
    "failed", "The filename, directory name", "No more data is available",
]

# --------------------------------------------------------------------------- #
# 状态文案模板（共享提示统一在此，避免页面硬编码中文）—— 架构 13.2
# --------------------------------------------------------------------------- #
MSG_SHARE_NOT_ONLINE: str = "该地址不在线，无法打开共享"
MSG_SHARE_OPENING: str = "正在打开 \\\\{ip} …该主机可能未开放共享"
MSG_SHARE_REQUESTED: str = "已请求打开 \\\\{ip}"
MSG_SHARE_FAILED: str = "打开共享失败：{err}"
MSG_SHARE_BAD_IP: str = "IP 格式非法，无法打开共享"
MSG_PING_EMPTY_HOST: str = "请输入目标地址"
MSG_SCAN_BAD_SUBNET: str = "请输入正确的网段前三段，如 192.168.1"
MSG_SCAN_RANGE: str = "网段前三段每段需在 0–255 之间"
MSG_SCAN_DONE: str = "扫描完成，耗时 {s} 秒"
MSG_SCAN_ALL_OFFLINE: str = "该网段可能无设备或不可达"
MSG_SCAN_STOPPED: str = "扫描已停止，已完成结果已保留"
MSG_CONFIG_SAVED: str = "方案已保存"
MSG_CONFIG_DELETED: str = "方案已删除"
MSG_APPLY_OK: str = "切换成功：{adapter} → {name}"
MSG_APPLY_FAIL: str = "切换失败：{err}"
MSG_NEED_ADMIN: str = "需管理员权限，操作已取消"
MSG_REFRESH_OK: str = "状态已刷新"
MSG_CONFIG_CORRUPT: str = "配置文件损坏，已备份为 {bak} 并重建空配置"
MSG_COPIED: str = "已复制"
MSG_NO_ADAPTER: str = "未检测到可用网卡，请手动输入网段"

# ---- 网络检查页（页面四）—— PRD-网络检查页 §6 ---- #
MSG_NETCHECK_RUNNING: str = "检测中…"
MSG_NETCHECK_DONE: str = "检测完成，耗时 {s} 秒"
MSG_NETCHECK_UNAVAILABLE: str = "不可用"
MSG_NETCHECK_PARSE_FAIL: str = "解析失败"
MSG_NETCHECK_NO_IPV4: str = "未获取到 IPv4 地址"
MSG_NETCHECK_NO_IPV4_HINT: str = "请检查网络连接后重试"
MSG_NETCHECK_NO_IPV6: str = "未获取到 IPv6 地址"
MSG_NETCHECK_NO_IPV6_HINT: str = "当前网络可能未启用 IPv6，或该站点不支持 IPv6 访问"
MSG_NETCHECK_ALL_FAIL: str = "所有数据源均不可用，请检查网络"
MSG_NETCHECK_SOURCES_AGREE: str = "✅ {n} 个源结果一致"
MSG_NETCHECK_SOURCES_DIFFER: str = "⚠ 源之间存在分歧，可能受代理/CDN 影响"
MSG_NETCHECK_NO_ADAPTER: str = "未检测到可用网卡"
MSG_NETCHECK_LAST_TIME: str = "最后检测 {t}"
MSG_NETCHECK_NEVER: str = "尚未检测"
MSG_NETCHECK_UNREACHABLE: str = "不可达"
MSG_NETCHECK_COPY_IP: str = "已复制 {ip}"
MSG_NETCHECK_NO_IP_TO_COPY: str = "暂无可复制的 IP 地址"

# ★ 2026-09-21 改版：B「本机公网 IP」与 C「多源交叉验证」合并为一卡 ——
#   两者本就同源（B 卡的 IP 正是从 C 卡的 4 个源里挑「最佳」得来），拆成两张是重复信息。
#   合并后卡标题取此常量，源区每源**只留一行**（状态 + 归属地 + 组标签）。
MSG_NETCHECK_PUBLIC_IP_TITLE: str = "公网 IP · 多源校验"

# E 板块「常用站点连通性」卡片内的**独立刷新按钮**（只重探 8 个站点）：
# 用户明确要求（2026-09-20）：「此按钮只用于刷新站点连通状态」——
# 不重新查询公网 IP、不做多源交叉验证、不读网卡。
MSG_NETCHECK_SITES_REFRESH: str = "🔄 刷新"
MSG_NETCHECK_SITES_REFRESHING: str = "🔄 检测中…"
MSG_NETCHECK_SITES_TIP: str = "只重新探测站点连通性，不重新查询公网 IP / 网卡"
MSG_NETCHECK_SITES_FAIL: str = "站点检测失败"

# C 板块「多源交叉验证」卡片内的**独立刷新按钮**（只重查 4 个数据源）：
# 用户要求：此按钮只刷新此卡片内的数据（不重新查询公网 IP / 站点 / 网卡）。
MSG_NETCHECK_SOURCES_REFRESH: str = "🔄 刷新"
MSG_NETCHECK_SOURCES_REFRESHING: str = "🔄 检测中…"
MSG_NETCHECK_SOURCES_TIP: str = "只重新查询多源交叉验证，不重新查询公网 IP / 站点 / 网卡"
MSG_NETCHECK_SOURCES_FAIL: str = "交叉验证失败"

# 页脚：只留一行「更多数据 https://ip.skk.moe/」（URL 带超链接）
# 用户明确要求（2026-09-20）：去掉原「数据来源 …（点击站点名可在浏览器中打开）· IPv6 探测 …」，
# 那串文案信息密度低且喧宾夺主。参考站点统一指向 ip.skk.moe。
MSG_NETCHECK_FOOTER: str = "更多数据 {link}"
NETCHECK_REFERENCE_URL: str = "https://ip.skk.moe/"

# 日志前缀 / 格式（PRD 5.3.3）
LOG_FMT_PING: str = "[{ts}] 目标 {host}  结果={result}  延迟={latency}  TTL={ttl}"
LOG_FMT_SUMMARY: str = "[{ts}] 汇总 发送={sent} 接收={recv} 丢失={loss}({loss_pct}%) 平均={avg}ms 最小={min}ms 最大={max}ms"

# 状态中文名
STATUS_TEXT: dict[str, str] = {
    "online": "在线",
    "offline": "离线",
    "pending": "未检测",
}
