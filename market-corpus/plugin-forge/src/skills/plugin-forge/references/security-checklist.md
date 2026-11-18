# 安全检查清单与评分（security-checklist）

适用场景：① 外部内容入库审计（Workflow G 第 0 步，结论记 AUDIT.md）；② 交付前安全门禁（plugin-forge-security）；③ 本仓库定期复扫（AUDIT.md）。

工具命令（`${FORGE_HOME}` = skill 安装目录，定义见 SKILL.md §5）：

```
python ${FORGE_HOME}/scripts/security_audit.py <repo> [--allow 前缀1,前缀2] [--include-docs] [--json] [--quiet]
```

默认只扫代码与配置文件；`--include-docs` 追加扫描 md/html/txt（查个人路径与密钥残留）。
## 豁免口径：点位声明，不用文件级 --allow（2026-11-17 改）

`--allow <前缀>` 是整份文件豁免：被豁免的文件里再出新问题没人看见。本仓的三条 --allow 已全部撤掉，
CI 改成裸扫。豁免改用**行尾点位声明**：

```
urllib.request.urlopen(req)  # forge-declared: SEC-4 理由=只读拉取 api.github.com 出处=AUDIT.md#sec-declared 复审=2026-12-15
```

- 四段全部必填：类别、理由、出处（必须含 `AUDIT.md` 的锚点）、复审日期（`YYYY-MM-DD`）。
- 缺一段、格式不对、或日期已过 → 声明失效，命中重新计回账上（fail-closed）。
- `--strict-declared` 把畸形标记直接判成问题，CI 跑这一步。
- **出处必须在被审仓库里查得到**（2026-11-18 加）。声明里的 `出处=AUDIT.md#<锚点>` 现在会去被审仓库读那本账，锚点作为整词找不到就当没有声明，命中回到账上，并在 `--strict-declared` 下记一条「出处查无记录」。为什么：外部审 A791 的 CRITICAL 实测过——拿扫描器审第三方插件仓时，被审方在真 token 那行末尾自写一条格式完全合法的注释，旧判据只看出处字符串里含不含 `AUDIT.md` 这个文件名，于是 SEC-2 一票否决被抹成 rc=0 放行。方向是 fail-closed：宁可多报，不许被被审对象自己的注释免掉。本仓 35 条声明不受影响（锚点 `sec-declared` 在 AUDIT.md 里真存在，自检里也留了一条反向用例：抹掉账本里的锚点，同一声明立刻失效）。
- 声明的类别要与命中一致：写 `SEC-1` 的声明豁免不了 `SEC-4` 的命中。
- 注释语法两种都认：脚本与 yml 用 `# forge-declared: …`，文档与 HTML 用
  `<!-- forge-declared: … -->`（工单 W14）。文档没有 `#` 注释，以前等于没有点位声明这条路：
  教学示例与历史条目只能改写文字才能过门禁，而改写 AUDIT 的历史记录等于造假。
  认这一种写法之后，`--include-docs` 下的文档 SEC-2 走声明，不走整份 `--allow`。
- 明文赋值的右值在反引号与中日韩标点处截断（`、。「」（）…` 与全角字符）。
  以前只认 ASCII 空白和 `,;{}()`：文档里「`max_tokens=MAX_TOKENS`）、类型标注」会一路吃到
  下一个 ASCII 空格，取到的值带着反引号与汉字，全大写常量的排除当场失效，
  同一行相邻的 `OPENAI_API_KEY=`、`DB_PASSWORD=` 也会被并成一个长值吞掉（实测 6 处误报的根因）。
- 判据在 `security_audit.py` 的 `DECLARE_RE` / `_declaration()` / `_covers()`，自检各有正反用例。
- 全部在册声明逐条登记在 `AUDIT.md` 的 `sec-declared` 一节，来源是 `--json` 的 `declared_ok_list`，不手抄。

`--allow` 仍可给外部仓库整份豁免（前缀匹配）；给别人的仓做分诊时，**每条豁免必须把依据写进该仓的审计记录**，没有依据的豁免按发现处理。

## 四项检查

### SEC-1 路径范围

要求：写/删只落在项目目录内，或 profile 声明的数据目录；不留个人绝对路径。
机器查：写/删调用行里的硬编码绝对路径；`expanduser` 打到 `.ssh` 等敏感目录；`C:\Users\<名>`、`/home/<名>` 残留；经 `../` 越界的写入。
修法：改项目根相对路径或宿主声明的数据目录；提交前把个人路径脱敏为 `~`。

### SEC-2 明文密钥

**文件名判据（2026-11-15 新增，来源：外部深审 A464 第 3 条 / A472 第 4 条；2026-11-16 按 A571 第 3、4、5 条收窄）**
- 一票否决（名字本身即泄露）：`id_rsa` / `id_dsa` / `id_ecdsa` / `id_ed25519`（前缀匹配，`id_rsa.bak` 也算）、
  `.netrc` / `_netrc` / `private_key`，以及扩展名 `.pfx` / `.p12` / `.kdbx` / `.ppk`。
- 前缀匹配排除 `.pub` 结尾：`id_rsa.pub`、`id_ed25519.pub` 是公钥，常出现在 ansible、部署文档与测试夹具里，
  不该被一票否决。
- 按内容判：`.npmrc` / `.pypirc` 只在含 `_auth` / `_authToken` / `password` / `token` **且值是真凭据形态**时计。
  值为空、`${NPM_TOKEN}` 这类环境变量引用（npm 官方推荐写法）、`<your-token>` 这类占位符都不计。
  报告只出标记名不出值。
- 只算真盲区、不阻断：`.pem`（常是公钥证书）、`.key`（Next.js 本地化文件）、`.jks` / `.keystore`（调试库）、
  `.pypirc` 无凭据时。理由：一票否决项的误报代价比漏报大。
- `--allow` 的豁免值先归一化（去 `./` 前缀、去尾斜杠、反斜杠转正斜杠），内容那一遍与文件名那一遍
  **共用同一份归一化结果**（`norm_allow` / `is_allowed`）。旧版只在 `iter_files` 里归一化，
  `--allow tests/fixtures/` 豁免得了内容、豁免不了夹具里的 `id_rsa`，于是假阻断。
- 已知边界：把文件改名成 `foo.bin` 不覆盖；内容里没有明文头的加密容器不覆盖。
- 判据分散在 `security_audit.py` 的一组长常量，改一处要同步改其余（名字写全，不写「几处」——
  计数这句话已经错过一次）：一票否决用 `SECRET_FILE_HARD`（扩展名）与
  `SECRET_FILE_HARD_NAMES`、`SECRET_KEY_PREFIXES`（文件名）；按内容判用 `AUTH_GATED_NAMES` 加 `AUTH_MARKER_RE`，
  并用 `ENV_REF_RE` 放行 `${VAR}` 这种环境变量引用；只算盲区用 `SECRET_FILE_EXTS` 与 `SECRET_FILE_NAMES`；
  公钥排除是 `PUBLIC_KEY_SUFFIX`。


要求：仓库内不得出现任何可用凭据（token、API key、私钥、password 形态赋值）。
机器查：GitHub / OpenAI / AWS / Slack token 形态；`PRIVATE KEY` 块；URL 内嵌凭据（`scheme://user:pass@`）；明文赋值按**标识符拆词**判定 —— 键名按 `_` `.` `-` 与驼峰切词后命中 password / passwd / pwd / secret / token / credential / api_key / access_key / refresh_token 等，或整名含 password·secret·credential 这类高区分度子串（占位值已过滤）。
值是**代码引用**时不算密钥，这是有意的排除面：属性路径（`args.password`）、全大写常量（`max_tokens=MAX_TOKENS`）、类型标注（`Optional[str]`）、纯数字（`token_count = 1234567890`）、普通 URL 值、与键同名的自引用（`self.password = password`）、函数调用右值。
URL 内嵌凭据一条同样过滤模板占位：`${DB_PASSWORD}`、`<password>` 不算真口令。
已知边界：值不足 8 字符不报（`PASSWORD=myword` 6 位、`password = "abc"` 都漏）；`tokenizer` 这类同前缀词不误报；不含任何关键词的纯随机串扫不到，仍要人工抽查。纯字母且够长的右值仍会报（`db_password = secretvalue` 命中），所以「变量引用」的排除只针对属性路径、全大写常量、类型标注、纯数字、URL 与自引用这几种明确形态。占位值默认也被吃掉，`example1234567` 这种「占位词 + 数字」同样不报；要按真值对待就加 `--strict-placeholder`，它只收紧这一种情形，文档示例值会因此开始报。 <!-- forge-declared: SEC-2 理由=db_password = secretvalue 是本行用来讲命中形状的教学示例，非真凭据 出处=AUDIT.md#sec-declared 复审=2026-12-15 -->
修法：改环境变量或宿主安全存储（对照 profile §6）；**已提交的凭据立即轮换**，并清理历史。
判定：**任一真实命中 = 一票否决，直接阻断**（退出码 2）。SEC-2 的分值同时进总分，
但阻断不看总分——判据是「有没有真实命中」这一条，总分只是显示。

### SEC-5 清单形状（不计分）

要求：清单不夹带装包即执行的钩子，依赖走索引而不是直连远端包，必填字段齐。
清单文件无条件进扫描池（`requirements.txt` 属 `.txt`，旧版不加 `--include-docs` 就读不到，而直连依赖最常写在这里）。`plugin.toml` 的必填按 **`[plugin]` 段**判定：子段（如 `[plugin_runtime]`）里的同名键不再顶掉顶层缺项，别的段里的键也不再算已填。
机器查：`preinstall` / `postinstall` / `prepare` / `install` 钩子；`git+https://`、`.tar.gz`/`.whl`/`.zip` 直链；`cmdclass` / `setup_requires` 构建期执行；`plugin.toml` 缺 `id`/`name`/`version`/`entry`；`package.json` 缺 `license`/`repository`。
计分：**不计入 100 分**，单独一栏输出，`--json` 里是 `sec5`。缺字段是发布前置条件而不是漏洞，混进评分会让真正的密钥命中失去区分度。

### SEC-3 任意执行

要求：外部输入不得到达动态执行；子进程用固定 argv 列表，不开 shell。
机器查：`eval` / `exec` / `new Function`；`shell=True`、`os.system`、`os.popen`、`subprocess.getoutput` / `getstatusoutput`、`shell -c` 执行串；`curl … | sh`；`Invoke-Expression`；`pickle.load`、`marshal.load`、unsafe `yaml.load` 与 `yaml.unsafe_load`。（后五条 2026-11-15 补：旧名单整批漏这几种形状，实测喂进临时仓库报 SEC-3 0 处、100/100、放行。）
修法：白名单 argv；参数化 API；safe loader；删除管道执行。

### SEC-4 静默下载

要求：每处网络取回与包安装都要向用户声明用途（README / profile / 安装提示）；无声明的自动外联即"静默下载"。
机器查：`urlopen` / requests / fetch / curl / wget；`pip|npm|uv install`；`git clone|pull`；download 字样。
判定：每处须对应一条"声明"（在 AUDIT.md 记章节出处）；对不上的按未声明处理，修或阻断。
注意：机器只能列点位，**声明与否要人工核对**——这一项不允许纯靠脚本给满分。

## 评分与结论

| 项 | 分值随处数变（照 `security_audit.py` 的 `score()` 写） |
|---|---|
| SEC-1 / SEC-3 / SEC-4 | 0 处 25；1–3 处 20；4–10 处 10；>10 处 0 |
| SEC-2 | 0 处 25；1–2 处 10；3 处及以上 0。任一真实命中即阻断（退出码 2），分值同时计入总分 |

总分 = 四项之和（满分 100）。有效的点位声明与 `--allow` 的豁免文件都不计发现；点位声明逐条可在 `--json` 的 `declared_ok_list` 里看到。

- **阻断**（退出码 2）：SEC-2 有真实命中，或总分 < 50。
- **修完再放**（退出码 1）：总分 50–89。每条发现给 位置 + 判定（真问题/误报/已声明）+ 修法。
- **放行**（退出码 0）：总分 ≥ 90。
- **不确定**（退出码 3，需加 `--fail-unclear`）：会放行，但存在盲区。给 CI 用时以「不确定」收尾，而不是让盲区冒充 0 发现。
- 盲区分两本账，**只有真盲区能触发退出码 3**：
  - 设计内排除 `excluded`：构建/缓存/VCS 目录、扩展名不在池（默认不扫的 `.md` 等）、`--allow` 豁免、
    素材与归档 `ASSET_EXTS`（图片、字体、压缩包、二进制——行级规则读不动它们，不是漏检）。
    把它们算进盲区的话，任何有 `.git` 或 README 的仓库都会恒为「不确定」，等于没有信号。
  - 真盲区 `unread`：该读而没读到——超 2 MiB、含 NUL、`stat`/`open` 失败、`os.walk` 报错的目录，以及 `unclear`（读了但行级规则读不动：单行超长，或平均行长 >200 的压缩/混淆文件）。
- **扩展名不在任何名单里的文件记进 `unclear`，不再记进设计内排除**（工单 W05）。
  白名单永远可以漏下一个后缀：`ghp_` 写进 `report.wsf` 时，旧口径 SEC-2=0、真盲区恒 0、
  `--fail-unclear` 不响。默认变响以后，漏一个后缀的代价是 CI 红一次，要往池子里补哪一类由人判。
  判池前先剥一层备份后缀（`BACKUP_EXTS`）：`settings.py.bak` 按 `.py` 读，只剥一层。
- 代码池补进的家族（工单 W05）：Windows 脚本宿主 `.wsf` / `.wsh` / `.vbe`、服务端网页脚本
  `.asp` / `.aspx` / `.jsp` / `.jspx` / `.cfm`、配置与构建载体 `.xml` / `.csproj`；
  纯文本载体进配置池：`.csv` / `.tsv` / `.lock` / `.sum` / `.log` / `.diff` / `.patch`。
  门禁那份 `CODE_EXTS`（`check_contract_approval.py`）用途不同、成员不等价，改任何一份都要另核一遍两边对不对齐。
  - 一个文件都没读到（路径写错、扩展名池不对）直接判 3，不再给 100 分放行。

## 使用规则

1. 评分是分流器，不替代逐行复核：分数高仍要抽查 SEC-2（拼接藏匿的密钥扫不到）。
2. 误报要分类记录，不悄悄丢弃；反复误报的模式回写本清单修规则。
3. 扫描器本身（security_audit.py）的模式定义会命中它自己的说明文字与自检夹具：说明文字改成不会被自己命中的写法，夹具按拆写口径处理，正则字面量躲不掉的逐点声明，依据写「检测规则的正则字面量本身」。
4. 复扫节奏：上游或本仓库脚本变更 ≥5 个文件时重跑；结果追加进 AUDIT.md。
