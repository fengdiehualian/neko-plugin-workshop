# 🐱 猫娘课程表

> 猫娘陪你管理课表喵~ 通用课程表工具，周重复 + 例外时间模型。

---

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| 📅 学期管理 | 创建多个学期，自动种入默认节次作息，随时切换 |
| 📚 课程录入 | 手动录入，或从 **Excel / JSON / CSV / ICS** 文件一键导入 |
| 🎓 教务系统同步 | 喜鹊儿 / 青果教务一键拉取课表（纯 Python DES 加密，零外部依赖） |
| 📊 智能表格解析 | .xlsx / .xls（含教务系统常见的 HTML 伪 xls）**合并单元格自动展开**（EasyExcel 语义），双课表布局识别 + 垃圾过滤器防错乱 |
| 🔁 幂等导入 | 同名课程自动复用、相同课时自动去重，重复导入不产生重复数据 |
| ⏱ 上课时间自定义 | 每节课起止时间按学期自定义（上午/下午/晚上分段），周课表角标与上课提醒同步生效 |
| ⏰ 上课提醒 | 自动检查即将开始的课程，提前 N 分钟推送通知 |
| 📝 作业 / 📋 考试 | 截止日期、关联课程、完成标记、逾期自动识别 |
| 🤖 AI 对话 | 自然语言查询今日课表、下节课、周数进度 |
| 📊 倒计时 | 距下次考试 / 下次作业 / 学期结束还有多少天 |
| 🌏 多语言 | 中文 / 英文 i18n |
| 🎨 TSX 面板 | N.E.K.O Hosted UI：周课表 / 今日课表 / 本周概览、一键导入、预览诊断、数据管理 |

---

## 📥 安装教程

### 方式一：从 N.E.K.O 插件市场安装（推荐）

1. 打开 N.E.K.O → **插件管理** → **插件市场**
2. 搜索 **"猫娘课程表"**
3. 点击安装 → 等待完成 → 启用
4. 在 **仪表盘** 或插件面板中点击进入开始使用

### 方式二：手动安装（适合开发者 / 预览最新版）

```bash
# 克隆插件仓库
git clone https://github.com/lingtongxinyv/n.e.k.o_plugin_course_schedule.git

# 将整个目录复制到 N.E.K.O 插件目录下
# Windows:
#   C:\Users\<你的用户名>\AppData\Local\N.E.K.O\plugins\course_schedule\
# macOS / Linux:
#   ~/.local/share/N.E.K.O/plugins/course_schedule/

# 重启 N.E.K.O（或在插件面板点击"重载"）
```

目录结构必须是：
```
plugins/course_schedule/   ← 插件文件夹名（就是 plugin.toml 里的 id）
├── plugin.toml            ← 插件配置（必需）
├── __init__.py            ← 主插件代码（必需）
├── routers/               ← 功能模块
├── ui/                    ← TSX 面板
└── data/                  ← 运行时数据库（自动创建）
```

### 插件市场发布流程（给开发者）

```bash
# 1. 在 N.E.K.O 源码根目录运行检查
uv run neko-plugin check course_schedule
uv run neko-plugin check course_schedule --release

# 2. 修改 plugin.toml 的 version 字段（如 0.2.0 → 0.2.1）
# 3. git commit + push
# 4. 打 tag 触发 release workflow
git tag v0.2.1
git push origin v0.2.1
# 5. 在 GitHub Release 页面编辑 release notes → 发布
# 6. 回到插件市场提交审核
```

---

## 🚀 快速开始

### Step 1：创建你的第一个学期

打开 N.E.K.O，进入插件面板 → **猫娘课程表**：

- 学期名：如 `2026秋季`
- 开始日期：学期第一天（如 `2026-09-01`）
- 结束日期：学期最后一天
- 总周数：一般 18~20 周

点「添加学期」，猫娘会自动为你种入默认的 **11 节作息时间**（第 1 节 8:00-8:45，第 2 节 8:55-9:40 …）。

### Step 2：导入课程（三种方式任选）

#### 方式 A：上传 Excel 文件 ⭐ 最快

1. 点「**选择文件**」选中你的课表 `.xls` / `.xlsx`
2. 点「**开始导入**」
3. 猫娘自动解析课程名 / 教师 / 教室 / 周几 × 节次 / 周次范围 ✨

> 💡 **如果解析结果不对？** 点「高级工具 → 预览/诊断」先让猫娘看看文件里有什么。

#### 方式 B：教务系统一键拉取

输入学校教务地址 + 学号 + 密码，猫娘帮你抓全部课程。

#### 方式 C：表格粘贴导入

从 Excel 或教务网站直接**全选 → 复制** → 粘贴到文本框，一键导入。

### Step 3：查看课表 🎉

切换到「**周课表**」tab 看到完整周视图；
「**今日**」tab 看今天；
「**本周概览**」tab 看一周摘要。

猫娘还会主动告诉你**下一节课是什么**、**学期过了百分之几**、**离下次考试还有几天**。

---

## 🐱 关于猫娘

为什么叫"猫娘课程表"？因为开发者觉得猫娘更可爱喵~

> 本插件不收集任何个人数据。所有数据保存在你本机 `data/plugin.db` 中。

---

## 📖 高级：AI 对话使用

插件所有入口点都对 AI 暴露，直接自然语言问：

- "今天我有什么课？" → AI 帮你查今日课表
- "下周周三有课吗？" → AI 查询课程
- "距期末考试还有几天？" → AI 调用倒计时
- "我的作业有哪些没交？" → AI 列出逾期作业
- "帮我导入这个 Excel 课表：<上传文件>" → AI 触发文件导入

---

## 🗂 目录结构

```
course_schedule/
├── plugin.toml              ← 插件配置（Market 读取）
├── __init__.py              ← 主插件：初始化数据库、注册 routers
├── _matrix_parser.py        ← 矩阵级表格解析器（双布局识别 + 垃圾过滤）
├── _xls_parser.py           ← xls (BIFF8) / HTML 伪 xls 解析，合并单元格展开
├── _xlsx_parser.py          ← xlsx 解析（含 mergeCell 展开）
├── _repo.py                 ← 数据库访问层（aiosqlite）
├── _schema.py               ← SQLite schema
├── _time.py                 ← 时间工具（周数、节次、星期）
├── _academic_adapter.py    ← 教务适配器抽象基类
├── config.example.toml      ← 配置模板
├── routers/
│   ├── __init__.py
│   ├── manage.py            ← 学期 / 课程 CRUD
│   ├── query.py             ← 查询类入口
│   ├── tasks.py             ← 作业 / 考试 / 倒计时
│   ├── import_export.py     ← 文件导入 / 导出 + ClearData / Preview
│   └── academic.py          ← 教务系统入口
├── _adapters/
│   ├── __init__.py          ← 适配器注册表
│   ├── jkingo_des.py        ← 纯 Python DES 加密
│   └── xiqueer.py           ← 喜鹊儿 / 青果教务
├── ui/
│   └── panel.tsx            ← N.E.K.O Hosted UI 面板
├── i18n/
│   ├── zh-CN.json
│   └── en.json
├── docs/
│   └── guide.md             ← 面板内显示的使用指南
└── .github/workflows/
    ├── verify.yml           ← Market 验证 CI（ruff + plugin check）
    └── release.yml          ← Market Release CI
```

---

## 🐛 已知限制 / 后续改进

- [ ] 上课提醒默认关闭（需在 config 启用）
- [ ] 喜鹊儿适配器需要登录 cookie 支持更多学校
- [ ] 周课表渲染大课程数量时可能偏慢（纯 DOM 渲染）

欢迎提 Issue 和 PR！

---

**许可证**：MIT  
**作者**：lingtongxinyv  
**版本**：0.2.1
