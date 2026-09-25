# 📧 neko_163mail - 网易邮箱助手

网易163邮箱管理插件，为 N.E.K.O-1 提供邮件读取、发送、回复、转发、附件管理等功能。清新诗意的蓝白色调前端界面，猫娘风格交互体验。

## ✨ 功能特性

- 📬 **邮件读取** - 支持未读邮件、全部邮件、文件夹浏览、分页加载
- ✉️ **邮件发送** - 支持发送邮件（含附件，支持中文文件名）
- ↩️ **邮件回复** - 回复发件人/回复所有人，支持附件
- ↪️ **邮件转发** - 转发邮件给其他人，可保留原附件
- 📎 **附件管理** - 发送附件、下载附件到本地
- 🔍 **邮件搜索** - 按关键词搜索邮件，支持分页
- ✅ **标记已读** - 单封/批量/全部标记已读
- 🗑️ **批量删除** - 批量删除邮件
- 🏷️ **智能分类** - 自动分类邮件（验证码、作业、安全通知、订阅、项目、财务、社交等）
- 📊 **监控面板** - 操作时间线、重要邮件日志、分类统计、待处理事项
- 🔔 **新邮件轮询** - 自动检测新邮件并推送通知（支持自定义间隔）
- 📊 **每日简报** - 每日邮件摘要，供早安播报插件联动
- 👤 **称呼配置** - 猫娘称呼可自定义

## 📦 安装

### 方式一：插件市场安装（推荐）

在 N.E.K.O 插件市场搜索 `neko_163mail` 并安装。

### 方式二：手动安装

1. 下载插件包 `neko_163mail.nekopkg`
2. 在 N.E.K.O 管理界面选择"安装插件"并选择下载的插件包

## ⚙️ 配置说明

安装后需要在 `plugin.toml` 中配置以下参数：

```toml
[neko_163mail]
# 网易163邮箱地址 (必填)
email_addr = "your_email@163.com"

# 网易163邮箱授权码 (必填)
# 获取方式：登录163邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启IMAP/SMTP服务 → 生成授权码
auth_code = "your_auth_code_here"

# IMAP 服务器 (可选，默认值如下)
imap_server = "imap.163.com"
imap_port = 993

# SMTP 服务器 (可选，默认值如下)
smtp_server = "smtp.163.com"
smtp_port = 465

# 高优先级发件人 (可选，逗号分隔，匹配发件人地址中的任意子串)
# 例如: "boss@company.com,hr@company.com"
high_priority_senders = ""

# 要忽略的文件夹 (可选，逗号分隔)
ignore_folders = ""

# 猫娘对用户的称呼 (可选，默认: "主人")
master_name = "主人"

# 猫娘的自称 (可选，默认: "喵喵")
catgirl_name = "喵喵"
```

### 获取授权码步骤

1. 登录 [163邮箱网页版](https://mail.163.com)
2. 点击右上角设置图标 → **POP3/SMTP/IMAP**
3. 开启 **IMAP/SMTP 服务**（需要手机验证）
4. 点击 **新增授权码**，按提示操作获取授权码
5. 将获取的授权码填入 `plugin.toml` 的 `auth_code` 字段

> ⚠️ **注意**：授权码不是邮箱密码！必须通过上述步骤单独生成。

## 🎯 LLM 工具

插件向 LLM 暴露以下工具（共 28 个）：

| 工具名 | 功能 |
|--------|------|
| `neko_163mail_get_summary` | 获取今日邮件摘要 |
| `neko_163mail_get_unread` | 获取未读邮件列表（分页） |
| `neko_163mail_get_all_emails` | 获取所有邮件列表（分页） |
| `neko_163mail_get_email_detail` | 获取单封邮件详情 |
| `neko_163mail_search` | 搜索邮件（分页） |
| `neko_163mail_mark_read` | 标记邮件已读 |
| `neko_163mail_batch_mark_read` | 批量标记已读 |
| `neko_163mail_mark_all_read` | 标记所有邮件已读 |
| `neko_163mail_send` | 发送邮件（支持附件） |
| `neko_163mail_reply` | 回复邮件（支持附件） |
| `neko_163mail_forward` | 转发邮件 |
| `neko_163mail_download_attachment` | 下载邮件附件到本地 |
| `neko_163mail_list_folders` | 列出邮箱文件夹 |
| `neko_163mail_check_new` | 检查新邮件 |
| `neko_163mail_batch_delete` | 批量删除邮件 |
| `neko_163mail_get_daily_briefing` | 获取每日邮件简报 |
| `neko_163mail_get_operation_logs` | 获取操作日志 |
| `neko_163mail_get_category_stats` | 获取分类统计 |
| `neko_163mail_get_pending_items` | 获取待处理事项 |
| `neko_163mail_get_important_emails` | 获取重要邮件日志 |
| `neko_163mail_get_overview` | 获取今日概览 |
| `neko_163mail_start_polling` | 启动邮件轮询（支持 interval_seconds） |
| `neko_163mail_stop_polling` | 停止邮件轮询 |
| `neko_163mail_get_polling_status` | 获取轮询状态 |
| `neko_163mail_get_names` | 获取称呼配置 |
| `neko_163mail_set_names` | 设置称呼配置 |

## 🎨 前端界面

清新明亮的蓝白色调，诗意浪漫的风格：
- 🌸 白、蓝、粉渐变色系
- ✨ 悬浮动画效果
- 📱 响应式设计
- 🐱 猫娘风格提示语
- 📬 7 个标签页：摘要、未读、全部、文件夹、搜索、发送、监控

## 📖 使用指南

### 基本使用

1. 安装插件并配置 `plugin.toml`
2. 启动 N.E.K.O，插件会自动连接邮箱并启动新邮件轮询（每 5 分钟检查一次）
3. 通过前端界面或 LLM 对话管理邮件

### 发送带附件邮件

通过 LLM 对话：
```
帮我发一封邮件给 example@qq.com，主题是"会议资料"，附件在 C:\Documents\会议纪要.docx
```

通过前端界面：
1. 切换到"发送"标签页
2. 填写收件人、主题、正文
3. 点击"选择附件"按钮添加文件
4. 点击"发送"按钮

### 搜索邮件

```
搜索包含"项目进度"的邮件
```

### 批量操作

```
把今天所有的未读邮件标记为已读
```

## 📁 文件结构

```
neko_163mail/
├── plugin.toml        # 插件配置
├── __init__.py        # 插件入口，LLM 工具定义（28 个工具）
├── plugin.py          # 插件主类（轮询、通知、业务逻辑）
├── client.py          # IMAP/SMTP 客户端（连接管理、邮件收发）
├── models.py          # 数据模型（Pydantic）
├── parser.py          # 邮件解析（HTML 转文本、优先级分类）
├── operation_log.py   # 操作日志
├── static/
│   └── index.html     # 前端界面
├── i18n/
│   ├── zh-CN.json     # 中文翻译
│   └── en-US.json     # 英文翻译
└── README.md          # 本文件
```

## 🔧 技术细节

- **IMAP 连接**：支持自动重连，连接池管理
- **SMTP 连接**：连接复用，避免频繁建立/断开连接触发 163 安全限制
- **附件编码**：RFC 2231 中文文件名编码，RFC 2045 base64 分行编码
- **邮件解析**：BeautifulSoup HTML 转文本，智能优先级分类
- **新邮件检测**：基于 UID 的增量检查，轮询间隔可配置

## 📝 版本历史

### v0.2.0 (2026-08-09)
- 新增：回复邮件（支持回复所有人、附件）
- 新增：转发邮件（可保留原附件）
- 新增：下载邮件附件到本地
- 新增：获取每日邮件简报（供早安播报联动）
- 新增：获取今日概览数据
- 新增：获取/设置称呼配置
- 优化：SMTP 连接复用，避免频繁连接触发安全限制
- 优化：base64 编码按 RFC 2045 分行，修复附件发送断连问题
- 优化：中文附件文件名使用 RFC 2231 编码
- 优化：轮询支持自定义 interval_seconds
- 优化：版本号统一为 0.2.0

### v0.1.0 (2026-08-08)
- 初始版本
- 基础邮件功能（读取/发送/搜索/删除）
- 附件支持
- 智能分类
- 监控面板
- 操作日志
- 新邮件轮询

## 🐛 问题反馈

如遇到问题，请在 N.E.K.O 项目 Issue 中反馈，并附上：
1. 错误日志（位于 N.E.K.O 日志目录）
2. 操作步骤
3. 期望结果与实际结果

## 📄 许可证

本插件遵循 N.E.K.O 项目许可证。
