# connection_onebot — 副本出处

宿主第一方连接器包 `utils.connection.onebot` 的副本，用作"宿主还没带这个包"时的回退。
解析入口：`connector_seam.py`。

## 出处

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/zhaijiunknow/N.E.K.O.git`（`origin`） |
| 副本基线 | 分支 `QQ` @ `4e83ac50e89dc9f4a6ea78af8bcde3a6280671d2`（2026-09-12，作者分支的最后一个 commit） |
| 上游 PR | `Project-N-E-K-O/N.E.K.O#2996`（`feat(qq): 适配器从插件中拆除并合入本体`）—— **已合并**（2026-09-28T06:19:42Z，merge commit `3618e75fe9`，base `main` ← head `QQ`） |
| 合并后维护者追加 | `dc1890bd5`（onebot 注释/文档）、`fa4305b73`（恢复 503/925 个宿主测试函数；`test_qq_auto_reply_forward_client.py` → `test_onebot_client_forward.py`）、`c66368d79`（把 `utils/connection/` 拆成 `base` / `onebot` / `qq`） |
| 对照的宿主版本 | `88e733a8e49517a935a4f28c3b5573377f29eec8`（2026-09-28 14:25 +0800，即拆分已落地的 `main`） |

**合并后的宿主布局**（`c66368d79` 之后）：

```
utils/connection/base.py                     ConnectionBase / ChatConnector / InboundMessage
utils/connection/onebot/                     OneBotClient + NapCatActionsMixin(70 个扩展动作)
                                             + factory.py + onebot_connection.py
                                             + qq_open_plat.py（57 行薄 re-export，不再是实现）
utils/connection/qq/open_platform.py         QQOpenPlatformConnection
utils/connection/qq/factory.py               create_qq_connection
```

拆分时**刻意保留**了插件在找的那 9 个名字（`OneBotClient` / `QQOpenPlatformConnection` /
`create_qq_connection` …），并且是**同一批对象**（不是重新导出），所以 `connector_seam` 的
解析名字一个都不用改 —— 宿主侧用 `tests/unit/test_connection_compat_surface.py`（20 条）
把这个兼容面钉住了。

维护者 `wehos` 的两条提示（2026-09-28 comment）：

* 插件在找的 `utils.connection.onebot.qq_open_platform_media` 是**插件自撰**的模块，上游没有；
  插件以后若要依赖宿主里**新的内部模块**，那个模块得落在 `onebot/` 下，或者宿主提供兼容入口；
* 他拿本插件 `d7cbc89` 的测试套在**拆分前/后**两份宿主上各跑了一遍：都是 **1462 passed**。

### 副本 ↔ 拆分后宿主的差异（2026-09-28 复核）

按方法名逐字对照（`qq_open_plat.py` ↔ `qq/open_platform.py`，脚本
`.dsh-artifacts/cmp-openplat-funcs.py`）：**逐字相同 99 ｜ 有差异 12 ｜ 只在副本 1 ｜ 只在宿主 0**。
把 docstring/注释剥掉之后再比，**只有下面 3 处是真差异**，全是本仓库的 `LOCAL-PATCH`：

* `send_group_image` / `_upload_group_image`：副本转发到 `qq_open_platform_media`，宿主仍是内联的旧式直传；
* `send_private_image`：副本新增，宿主没有；
* `_extract_attachments`：副本多带一个 `name`。

其余 9 处差异只是注释改写或空行，`onebot_client.py` 那边同理（134 个函数逐字相同，6 处差异
全是注释/空行 + 两个函数内多余的 `import time` 被删）。**结论：拆分没有带来任何上游行为变化，
副本也没有落后于拆分** —— 所以这次不需要重新同步，只需记下基线换了。

副本一共 6 个文件，其中 5 个来自 `utils/connection/onebot/`，1 个是插件自撰：

```
__init__.py  factory.py  onebot_client.py  onebot_connection.py  qq_open_plat.py   ← 上游 5 个
qq_open_platform_media.py                                                          ← 插件自撰，上游**没有**这个文件
```

（上游同目录下的 `utils/connection/__init__.py` 只有 docstring、零 re-export，无需复制。）

⚠️ **行数相同不代表内容相同**：副本与上游的 `qq_open_plat.py` 恰好都是 1126 行（功能性改动
加的行与减的行正好抵消），逐字核对必须用 `git diff --no-index`，看行数会得出错误结论。

## 标记约定：`LOCAL-PATCH`

副本里任何**非 lint** 的差异，都必须在文件头留一行 `# LOCAL-PATCH: <commit> <摘要>`：

```bash
grep -rn "LOCAL-PATCH" _vendor/connection_onebot/
```

`tests/test_qq_connector_seam.py::test_vendored_local_patches_are_marked_and_kept` 会核对：
标记还在、下面的接线还在、文件名清单与本文件对得上。

## 相对上游的本地改动（一）：功能性改动

**这一节是 2026-09-27 补的。** 2026-09-26 的
`4302a9ea6280954929b644fe9404adebc69f10a2`（「开放平台三处缺口：私聊发图 / 不再白烧 TTS /
入站文件附件」）改了副本里的 `qq_open_plat.py`（5 个 hunk）并新增了
`qq_open_platform_media.py`，**却没有动本文件** —— 于是"副本不是逐字一致"这件事在文档里
只剩 lint 那一半，功能性差异一点痕迹都没有。补这一节 + 文件头标记就是补这个缺口。

| 位置（副本） | 上游 | 副本里改成了什么 | 真机是否生效 |
|---|---|---|---|
| `qq_open_plat.py` import 块 | 无 | `from . import qq_open_platform_media` | 仅回退部署（见下） |
| `QQOpenPlatformConnection.send_private_message` | 只把图当文字 `[图片]` 发 | 单聊真发图：`qq_open_platform_media.send_private_image(...)`（`msg_type=7`），上传失败才退回文字 | 仅回退部署 |
| `QQOpenPlatformConnection.send_private_image`（新方法） | 不存在 | 薄转发到 `qq_open_platform_media.send_private_image` | 仅回退部署（插件**刻意不用**它，见下） |
| `QQOpenPlatformConnection._upload_group_image` | 内联的旧式直传（56 行） | 薄转发到 `qq_open_platform_media.upload_image(scope="groups", …)`：旧式直传仍是首选，文档里的 URL / 分片作回退 | 仅回退部署 |
| `QQOpenPlatformConnection._extract_attachments` | 只产出 `{"type", "url"}` | 平台给了文件名就带 `"name"`（消费方回退到 URL 尾巴） | 仅回退部署 → **真机上拿不到文件名**，见下 |
| `qq_open_platform_media.py`（整个文件） | **不存在** | 上传流程写成**适配层**形状：`QQOpenPlatformMediaMixin`（混进连接类就是那三个动作）+ 三个模块级包装函数（`upload_image(conn, …)` 等，把 mixin 绑到任意连接对象上，供宿主那份连接器使用） | ✅ **真机生效**（群图 + 单聊图都走它） |

### 真机生效面（2026-09-29 复核：宿主适配器优先）

插件现在**优先用宿主适配器**：宿主连接对象混入 `QQOpenPlatformMediaMixin` 之后自带
`upload_image` / `send_private_image`，`media_seam.py` 就调它的方法；宿主还没带那个 mixin 时
（比如 Steam 上已发布的旧包）才回退到本目录这份副本。判据是**能力**（`upload_image` 只有那个
mixin 有；`send_private_image` 不能当判据 —— OneBot 侧也有同名方法），启动时日志会写明跑的是哪份：

```
[QQ] 连接器来源: host (utils.connection.onebot)
[QQ] 富媒体来源: 宿主适配器（连接对象自带 upload_image）     ← 或：内置副本（…qq_open_platform_media）
```

2026-09-29 02:14 真机（开放平台正式环境）已确认第一行形态。所以本目录这份副本现在的定位是
**回退实现**，不再是唯一实现 —— 上表最后一行"真机生效"要按宿主版本读：宿主带 mixin 时它不生效。

`qq_open_plat.py` 那 5 个 hunk 依旧只在"宿主没带连接器"的回退部署里跑到。

**为什么副本当初是"mixin + 包装函数"而不是纯自由函数**：宿主类不可能被插件加上基类，所以混入
这条路在**插件侧**走不通，必须有"对任何连接对象都能跑"的入口；但如果这个入口自己实现一遍上传
流程，就会和 mixin 各漂各的。现在的分工是 mixin 独占流程、包装函数只负责把 mixin 绑上去
（`_MediaAdapter`：mixin 的方法优先，其余属性转给连接对象，**不往连接对象上挂任何东西**）。
`tests/test_qq_open_platform_media.py` 里那组"适配层形状"测试盯的就是这个：包装体里出现第二个
实现、或覆盖方法的签名吃不下来被覆盖者的调用形状（`sub_type=`），都会红。
**宿主哪天自己混入（就是现在这个 PR 的方向），包装函数自动让路** —— `_adapter()` 对已混入的
对象原样返回。

**真机证据（2026-09-27 03:19 / 2026-09-29 02:04，开放平台正式环境）**::

    03:19:39  WARNING - [QQOpenPlatform] 图片直传上传未拿到 file_info
    03:19:41  INFO    - [QQOpenPlatform] 图片上传成功(分片): wIFo43EanZwsn01Ru9mCJ4t6
    02:04:51  WARNING - [QQOpenPlatform] 图片直传上传未拿到 file_info
    02:04:53  INFO    - [QQOpenPlatform] 图片上传成功(分片): wIFo43EanZwsn01Ru9mCJwOp

结论是：**旧式直传在真机上已经失效，文档里的分片上传是活的** —— 这正是"两条都试"要回答的问题
（09-26 那次实测同结论，见 `reply_delivery_node._send_sticker` 的 docstring）。三次独立复现。

**已知缺口**：`_extract_attachments` 那个 `"name"` 只在回退部署里生效，用宿主连接器时
`enrichment._attachment_files` 只能拿 URL 尾巴当标签（只影响 prompt 里那行标签，不影响
内容）。原始 `att` 字段在连接器归一化后就没了，插件侧接不住 —— 要真修得推宿主 PR
（**宿主那份 `qq/open_platform.py` 现在也没补这一处**，改它属于上游的活）。

**丢了会怎样**（这正是要标记的原因 —— 重新同步上游会**静默**回退，不报错）：

1. `qq_open_platform_media.py` 被删或被改名：宿主带 mixin 时**真机不受影响**（走宿主那条），
   但旧宿主上会坏 —— 文件层面 `media_seam` 一解析就抛 `ModuleNotFoundError`，
   函数层面是表情包投递 `AttributeError`；
2. `qq_open_plat.py` 那 5 个 hunk 被上游覆盖回去：**真机不变**（连的是宿主那份），
   但回退部署丢能力 —— 单聊发图退回只发 `[图片]`、群图退回只试（真机已失效的）旧式直传、
   附件文件名丢失；
3. 最糟的一种：只拷 5 个上游文件、顺手 `rm` 掉 `qq_open_platform_media.py` ——
   同第 1 条，而且这次是"以为自己在做正确的事"。

### 重新同步上游的顺序

1. `git log --oneline -- _vendor/connection_onebot/` 加上面那条 grep，先看清有哪些本地改动；
2. 拷上游的 `__init__ / factory / onebot_client / onebot_connection / qq_open_plat`，
   **保留** `qq_open_platform_media.py`（上游没有它）；
3. 按上表把功能性改动重新应用到新版本上（冲突以上游为准，语义保持）；
4. 重跑下面那段 lint 修复；
5. 跑 `pytest plugin/plugins/qq_auto_reply/tests/test_qq_connector_seam.py`（两组守卫）。

## 相对上游的本地改动（二）：lint

（lint 之外还有上面那一节的功能性改动。）副本**不是逐字一致**的：为了让 CI 的 ruff 步骤过，
跑了一遍自动修复。
CI 的调用是 `ruff check --ignore-noqa --isolated --target-version py311 --line-length 120
--select E4,E7,E9,F,I --exclude vendor plugin-repo` —— 注意 `--isolated` 让插件自己的
`ruff.toml` 失效、`--ignore-noqa` 让 `# noqa` 失效，所以副本必须自己干净。

复现方式：

```bash
ruff check --fix --unsafe-fixes --ignore-noqa --isolated --target-version py311 \
  --line-length 120 --select E4,E7,E9,F,I _vendor/connection_onebot/
```

自动修复（6 处）：

- `qq_open_plat.py`：import 块排序（`import re as _re` 归位）；`f"[QQOpenPlatform] token 已获取"`
  去掉无占位符的 `f` 前缀；`import os, mimetypes` 拆成两行。
- `onebot_client.py`：删掉 `_handle_group_ban_notice` 与 `_cleanup_sent_message_cache`
  里冗余的函数内 `import time`（模块级 `import time` 已存在）。

手工修复（5 处，该 ruff 版本对 E701/E702 不提供自动修复）：`qq_open_plat.py` 里两处
`try: await self._ws.close()` / `except Exception: pass` 拆行，以及一处
`self._ws = None; self.ws = None` 拆成两行。

**建议把上面这些同样修到 PR #2996 的分支上** —— 它们本来就是真的 lint 问题，
宿主侧 CI 迟早也会红；修完之后本副本可以恢复逐字复制。

## 运行时依赖

`websockets`（两个 client）与 `httpx`（`qq_open_plat.py`）。包内只有 stdlib + 这两个第三方，
全是相对 import，**不引用宿主任何东西** —— 这正是它可以被复制出来的原因。
插件的 `pyproject.toml` 里 `dependencies = []`，这两个包靠宿主运行环境提供（与副本存在与否无关）。

## 什么时候删掉这个目录

**前提（两件都到位）**：

1. 宿主带 `utils.connection.onebot`（PR #2996 已合并 ✔）—— 连接器那半；
2. 宿主带**媒体 mixin**（`utils/connection/qq/open_platform_media.py` 的
   `QQOpenPlatformMediaMixin` 混进 `QQOpenPlatformConnection`，于是连接对象自带
   `upload_image`）—— 富媒体那半。PR 见 `docs/SESSION-HANDOFF.md` §37。

再叠一个**发布条件**：插件声明的"最低支持宿主版本"已经包含这两件（Steam 上发的包里有）。
在那之前删掉本目录，Steam 上的旧宿主就会在表情包投递时 `ModuleNotFoundError`。

**删的时候做什么**（现在的代码已经为这一步铺好，回退只在一处）：

0. 确认宿主那两半都在（`[QQ] 连接器来源: host …` + `[QQ] 富媒体来源: 宿主适配器…` 两行日志）；
1. 删整个 `_vendor/`；
2. `media_seam.py` 里删 `VENDORED_MODULE`、`_vendored()` 与三个函数里的回退分支
   （**富媒体对副本的唯一引用点就是它**，`tests/test_qq_media_seam.py` 有一条守卫盯着这点）；
3. `connector_seam.py` 收敛成一行 re-export（或直接改回
   `from utils.connection.onebot import …`）—— 富媒体的解析 2026-09-28 已从这里搬走，
   现在只剩连接器本身；
4. 把 `tests/test_qq_connector_seam.py` 里"回退可用"那条与漂移守卫、以及
   `tests/test_qq_media_seam.py` 最后那条"副本仍随包发布"一起清掉；
5. `PROVENANCE.md` 本身随目录一起消失；引用它的地方（`docs/UPSTREAM-LINEAGE.md`、
   `SESSION-HANDOFF.md` 的 §35/§37）标成历史。

> 注：`_extract_attachments` 的 `"name"`（附件文件名）**没有**进宿主那份 PR ——
> 也就是说删副本之后那条能力就没了（现状：用宿主连接器时本来就拿不到文件名）。
> 要保住它得另开一个宿主 PR（`docs/SESSION-HANDOFF.md` §37 里标成"留给上游"）。

在这之前，`tests/test_qq_connector_seam.py` 的漂移守卫会在宿主存在时比对
`OneBotConnector` 协议的成员集合，副本与宿主对不上就红 —— 那是拆分时的安全网；
`test_vendored_local_patches_are_marked_and_kept` 则盯住本文件与副本里那些本地改动。
拆 `_vendor/` 时这两条一起删。
