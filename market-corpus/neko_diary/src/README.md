# 🐱 猫娘日记插件 (Neko Diary Plugin)

一个清新可爱的日记插件，让猫娘陪你记录生活中的点点滴滴。

## ✨ 功能特点

- 📝 **日记管理**：创建、编辑、删除日记
- 🎨 **心情标记**：8 种心情状态，用可爱的表情符号记录每天的心情
- 🏷️ **标签系统**：为日记添加标签，方便分类和检索
- 🔍 **全文搜索**：快速查找过去的日记内容
- 📅 **时间线浏览**：按时间顺序浏览所有日记
- 🌍 **多语言支持**：支持中文、英文、日文、韩文
- 🎀 **清新界面**：白蓝粉渐变配色，诗意浪漫的设计风格

## 🎨 心情类型

- 😊 开心 (happy)
- 😢 难过 (sad)
- 😠 生气 (angry)
- 😰 焦虑 (anxious)
- 😌 平静 (calm)
- 🤩 兴奋 (excited)
- 😴 疲惫 (tired)
- 😐 一般 (neutral)

## 📦 安装

1. 将插件复制到 N.E.K.O 的插件目录：
   ```
   plugin/plugins/neko_diary/
   ```

2. 重启 N.E.K.O 应用

3. 在插件管理中启用"猫娘日记"插件

## 🔧 配置

在 `plugin.toml` 中可以配置：

```toml
[neko_diary]
# 数据库存储路径（留空则使用默认路径）
db_path = ""
# 猫娘对用户的称呼
master_name = "主人"
# 猫娘的自称
catgirl_name = "喵喵"
# 默认心情
default_mood = "neutral"
# 每页显示日记数量
page_size = 20
```

## 🎯 使用方法

### 通过 LLM 工具调用

猫娘可以使用以下工具来管理日记：

- `neko_diary_write` - 写一篇日记
- `neko_diary_browse` - 浏览日记时间线
- `neko_diary_search` - 搜索日记
- `neko_diary_get_today` - 获取今天的日记
- `neko_diary_get_mood_stats` - 获取心情统计
- `neko_diary_throwback` - 历史上的今天
- `neko_diary_delete` - 删除日记
- `neko_diary_get_names` - 获取称呼配置
- `neko_diary_set_names` - 设置称呼配置

### 通过前端界面

访问插件的前端页面，可以：
- 写新日记
- 浏览所有日记
- 按心情筛选
- 搜索日记内容

## 🗂️ 数据存储

日记数据存储在 SQLite 数据库中，默认位置：
```
plugin/plugins/neko_diary/data/neko_diary.db
```

数据库结构：
- `diary_entries` - 日记条目表
  - id: 唯一标识符
  - date: 日期
  - title: 标题
  - content: 内容
  - mood: 心情
  - tags: 标签（JSON 数组）
  - attachments: 附件（JSON 数组）
  - created_at: 创建时间
  - updated_at: 更新时间
  - deleted: 是否删除（软删除）

## 🎨 界面预览

前端采用清新明亮的设计风格：
- 渐变背景：白蓝粉配色
- 圆角卡片：柔和的阴影效果
- 心情选择器：可爱的表情符号
- 响应式布局：支持移动端和桌面端

## 📝 开发说明

### 技术栈

- **后端**：Python + SQLite
- **前端**：HTML + CSS + JavaScript
- **框架**：N.E.K.O Plugin SDK

### 文件结构

```
neko_diary/
├── __init__.py          # 插件入口
├── plugin.py            # 核心业务逻辑
├── plugin.toml          # 插件配置
├── README.md            # 说明文档
├── static/
│   └── index.html       # 前端界面
├── i18n/
│   ├── zh-CN.json       # 简体中文
│   ├── en.json          # 英文
│   ├── ja.json          # 日文
│   └── ko.json          # 韩文
└── data/
    └── neko_diary.db    # SQLite 数据库（自动生成）
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 💖 致谢

感谢所有为 N.E.K.O 项目做出贡献的开发者！

---

**让猫娘陪你记录生活的每一份美好~** 🌸
