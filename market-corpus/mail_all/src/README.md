# 🐱 猫娘邮件插件 (catgirl_mail)

一个遵循 **N.E.K.O 插件开发标准** 的邮件插件，让猫娘帮你**收发邮件**。

- ✅ 支持**任意**邮箱服务商：只要给得出 IMAP / POP3 / SMTP 地址即可（QQ、163、Gmail、Outlook、企业邮、自建邮服……）
- ✅ 支持 **IMAP** 与 **POP3** 两种收件协议，收件自动识别未读
- ✅ 支持 **SMTP** 发件，含抄送、HTML 正文、附件
- ✅ 可配置**多个账号**，分别收发
- ✅ **定时查信**：默认每 5 分钟检查一次，发现新邮件会「喵~」一声推给主人
- ✅ 账号凭据存于插件 `PluginStore`，随插件持久化

---

## 安装

把你本地的 N.E.K.O 仓库的 `plugin/plugins/` 目录下，放入本插件的 `catgirl_mail/` 文件夹，
确保路径为：

```
<N.E.K.O>/plugin/plugins/catgirl_mail/__init__.py
<N.E.K.O>/plugin/plugins/catgirl_mail/mail_core.py
<N.E.K.O>/plugin/plugins/catgirl_mail/plugin.toml
<N.E.K.O>/plugin/plugins/catgirl_mail/i18n/...
```

启动 N.E.K.O 后，插件会在 `startup` 生命周期自动加载并启用 `PluginStore`。

## 配置第一个邮箱（以 QQ 邮箱为例）

QQ 邮箱需要在「设置 → 账户」里开启 IMAP/SMTP 并生成**授权码**（不是登录密码）。

对猫娘说：

> 帮我添加一个邮箱账号：名字「我的QQ邮箱」，邮箱 `123456@qq.com`，收件用 imap.qq.com:993(SSL)，发件用 smtp.qq.com:465(SSL)，授权码填 `xxxxxx`

插件会调用 `add_account` 并保存配置。之后就可以：

- 「帮我用我的QQ邮箱发邮件给 `friend@xx.com`，主题『喵呜』，正文『在吗』」
- 「检查一下我的新邮件」
- 「看看收件箱摘要」

常见邮箱服务器参考：

| 服务商 | 收件(IMAP) | 收件(POP3) | 发件(SMTP) |
|--------|-----------|-----------|-----------|
| QQ | imap.qq.com:993 | pop.qq.com:995 | smtp.qq.com:465 |
| 163 | imap.163.com:993 | pop.163.com:995 | smtp.163.com:465 |
| Gmail | imap.gmail.com:993 | pop.gmail.com:995 | smtp.gmail.com:465(SSL) / 587(STARTTLS) |
| Outlook | outlook.office365.com:993 | — | smtp.office365.com:587(STARTTLS) |

> 端口说明：SSL 一般用 465（SMTP）/ 993·995（收件）；STARTTLS 用 587。
> `recv_ssl` / `smtp_ssl` 设为 `true` 表示使用 SSL。

## 提供的入口（@plugin_entry）

| id | 作用 |
|----|------|
| `add_account` | 配置一个邮箱账号 |
| `list_accounts` | 列出已配置账号（密码隐藏） |
| `remove_account` | 删除账号 |
| `send_mail` | 发邮件（支持 cc / HTML / 附件） |
| `check_mail` | 立即检查新邮件 |
| `read_mail` | 读取指定邮件全文 |
| `inbox_summary` | 汇总所有账号未读情况 |

另有 `@timer_interval` 的 `auto_check` 每 5 分钟自动查信并推送提醒。

## 安全说明

账号的密码 / 授权码以**明文**保存在插件的 `PluginStore` 中（与 N.E.K.O 其他插件一致）。
建议仅在可信的本地环境使用；不要将这个存储文件提交到公开的代码仓库。
如需更高安全级别，可改用 N.E.K.O 的 Profile / 加密配置机制二次封装。

## 技术实现

- 纯 Python 标准库：`imaplib` / `poplib` / `smtplib` / `email`，**零第三方依赖**
- 阻塞式网络调用在异步入口中用 `asyncio.to_thread` 包裹，不阻塞事件循环
- 遵循 N.E.K.O SDK：`@neko_plugin` + `NekoPluginBase`、`@plugin_entry`、`@lifecycle`、`@timer_interval`、返回 `Ok` / `Err(SdkError)`、通过 `self.ctx.push_message` 推送

## 目录结构

```
catgirl_mail/
├── __init__.py      # 插件主类 CatgirlMailPlugin
├── mail_core.py     # IMAP/POP3/SMTP 底层协议
├── plugin.toml      # 插件清单
└── i18n/            # 中英文案
```
