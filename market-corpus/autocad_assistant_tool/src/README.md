# CAD辅助绘图 — N.E.K.O 插件

> 即插即用的 N.E.K.O 插件，让 AI 角色通过自然语言对话驱动 AutoCAD（及中望CAD/浩辰CAD）完成 2D/3D 绘图与建模。零系统依赖，开箱即用。

## ✨ 功能特性

- **🎨 2D 工程绘图** — 直线、矩形、圆、弧、椭圆、多段线、正多边形、文字、标注、填充
- **🧊 3D 实体建模** — 立方体、圆柱、球、锥、圆环、楔体、棱锥、3D面、3D多段线
- **🔧 实体编辑** — 移动、复制、删除、改色、布尔运算（交/并/差）、拉伸、旋转
- **📚 图层管理** — 创建图层、切换激活图层、按层改色、按层删除
- **💬 对话驱动** — AI 角色理解你的绘图意图，逐笔把图形落到 CAD 画布
- **🚀 零系统依赖** — pywin32 编译产物完整 vendored，无需用户安装任何 Python 库
- **📦 即插即用** — 复制到 N.E.K.O 插件目录即可使用

## 📋 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11（64位） |
| N.E.K.O | 已安装（源码版或发行版） |
| CAD 软件 | AutoCAD 2018~2026 / 中望CAD / 浩辰CAD（完整版，需注册 COM ProgID） |

> ⚠️ **注意**：绿色便携版 CAD 可能未注册 COM ProgID，无法连接。

## 🚀 安装方法

### 方法一：一键安装脚本（推荐）

1. 将整个 `autocad_assistant_tool` 文件夹复制到目标电脑
2. 进入文件夹，**双击 `install.bat`**（或右键 `install-plugin.ps1` → 使用 PowerShell 运行）
3. 等待安装完成，重启 N.E.K.O

### 方法二：手动复制

把整个文件夹复制到：
```
%LOCALAPPDATA%\N.E.K.O\plugins\autocad_assistant_tool\
```

然后重启 N.E.K.O。

## 🎯 使用方法

### 基本流程

1. **打开 AutoCAD** — 必须先手动启动 CAD
2. **在 N.E.K.O 中与 AI 角色对话**：

```
你：连接 CAD
AI：正在连接... 连接成功

你：画一个 100x50 的矩形，左下角在原点
AI：好的，正在绘制矩形...

你：在矩形中心画一个半径 20 的圆
AI：正在绘制圆...

你：切换到等轴测视图
AI：好的，正在切换视图...
```

### 可用绘图命令（38个）

| 分类 | 命令 |
|------|------|
| **2D 图元** | line, rectangle, circle, arc, ellipse, polyline, polygon, text, dimension, hatch |
| **图层管理** | create_layer, set_active_layer, set_layer_color, color_by_layer, delete_by_layer |
| **实体编辑** | change_entity_color, move_entity, copy_entity, delete_entity |
| **视图/文件** | zoom_extents, change_view, undo, save, clear |
| **3D 实体** | create_box, create_cylinder, create_sphere, create_cone, create_torus, create_wedge, create_pyramid, create_3dface, create_3dpolyline |
| **布尔/变换** | extrude_entity, revolve_entity, boolean_union, boolean_subtract, boolean_intersect |

### 与角色对话示例

```
画一个 50x30 的矩形，左下角 (0,0)，放到"轮廓线"图层，颜色红色
→ AI: create_layer("轮廓线", 红色) → rectangle(x1=0, y1=0, x2=50, y2=30, layer="轮廓线")

创建一个半径 15 的圆柱，高度 30，中心 (0,0,0)
→ AI: create_cylinder(radius=15, height=30, center=[0,0,0])

把矩形和圆柱做并集
→ AI: boolean_union(solid1_handle=..., solid2_handle=...)
```

## 📂 项目结构

```
autocad_assistant_tool/
├── __init__.py              # 插件入口类 AutoCADAssistantPlugin
├── _bootstrap.py            # vendor 路径注入（被 __init__.py 内联替代，保留供独立测试）
├── autocad_controller.py    # AutoCAD COM 控制器（38个绘图命令）
├── plugin.toml              # N.E.K.O 插件清单
├── config.example.toml      # 配置示例
├── pyproject.toml           # Python 项目元数据
├── ruff.toml                # Ruff lint 配置（N.E.K.O 标准模板）
├── install-plugin.ps1       # 一键安装脚本
├── install.bat              # 双击安装启动器
├── sync.ps1                 # 开发同步脚本（开发机 → N.E.K.O 插件目录）
├── vendor/                  # pywin32 纯 Python 部分（win32com/ + pythoncom.py + pywintypes.py）
├── vendor_cp311/            # Python 3.11 编译二进制（_win32sysloader.pyd + pythoncom311.dll）
├── vendor_cp312/            # Python 3.12 编译二进制
├── vendor_cp313/            # Python 3.13 编译二进制
├── static/                  # Hosted UI 状态面板
│   ├── index.html
│   └── script.js
├── docs/                    # 使用指南
│   └── guide.md
├── tests/                   # 基础测试
└── .github/workflows/       # N.E.K.O 标准 CI/CD
    ├── verify.yml
    └── release.yml
```

## 🔧 技术架构

### 零系统依赖原理

```
__init__.py 加载时
    ↓
内联 vendor 路径注入代码（无需 import _bootstrap）
    ↓
vendor_cp{major}{minor}/  →  sys.path[0]  (版本专属 .pyd/.dll)
vendor/                   →  sys.path[1]  (纯 Python)
    ↓
import win32com.client / pythoncom  ←  命中 vendored 文件
```

pywin32 的 `.pyd` 和 `.dll` 与 Python ABI 版本绑定，因此提供了 `vendor_cp311`、`vendor_cp312`、`vendor_cp313` 三个版本专属目录，运行时自动匹配。

### Hosted UI 异步调用

插件面板按钮通过 `/runs` 异步 API 调用入口点：
```
POST /runs → 拿到 run_id → 轮询 GET /runs/{id} → GET /runs/{id}/export → 解包结果
```

### N.E.K.O 插件 entry 路径

```
%LOCALAPPDATA%\N.E.K.O\plugins\autocad_assistant_tool\__init__.py
    ↓
entry = "plugins.autocad_assistant_tool:AutoCADAssistantPlugin"
    ↓
N.E.K.O 子进程 sys.path 包含 %LOCALAPPDATA%\N.E.K.O\
    ↓
plugins.autocad_assistant_tool 正确解析到插件目录
```

## 🐛 常见问题

### Q: 启动插件报错 "No module named '_bootstrap'"？
A: 已修复。新版 `__init__.py` 内联了 vendor 路径注入代码，不再依赖 `_bootstrap` 导入。请确保使用最新版本。

### Q: 点击"连接CAD"按钮没反应？
A: Hosted UI 必须走 `/runs` 异步 API，不是 `/plugin/trigger`。新版 `static/script.js` 已修复为正确的异步轮询调用。

### Q: 报错 "No module named 'autocad_assistant_tool'"？
A: `plugin.toml` 的 `entry` 必须是 `plugins.autocad_assistant_tool:AutoCADAssistantPlugin`（带 `plugins.` 前缀）。不要改成无前缀形式。

### Q: 报错 "Process crashed: pywin32 dependency load failed"？
A: 版本专属 `vendor_cp31X` 目录可能缺失或不匹配。检查 `vendor_cp311/_win32sysloader.pyd` 和 `vendor_cp311/pywin32_system32/pythoncom311.dll` 是否存在。

### Q: 连接CAD失败，提示找不到 ProgID？
A: 绿色便携版 CAD 未注册 COM ProgID。必须使用完整版 AutoCAD / 中望CAD / 浩辰CAD。

### Q: AI 角色说"未连接 CAD"但面板显示已连接？
A: 面板状态和 AI 入口是独立的。需要让 AI 角色调用 `connect` 入口：说"连接 CAD"即可。

## 📜 版本历史

### v1.0.0
- 首次发布：38个绘图命令，2D+3D全覆盖
- pywin32 312 完整 vendored（cp311/312/313 三版本）
- 内联 vendor 路径注入，消除 `_bootstrap` 导入依赖
- Hosted UI /runs 异步 API 调用
- 一键安装脚本（install.bat + install-plugin.ps1）

## 📄 许可证

MIT License

## 🔗 相关链接

- [N.E.K.O 插件开发文档](https://project-neko.online/zh-CN/plugins/)
- [N.E.K.O 插件市场](https://market.project-neko.cn/)
- [GitHub 仓库](https://github.com/lingtongxinyv/n.e.k.o_plugin_autocad_assistant_tool)
