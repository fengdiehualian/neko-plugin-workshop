# AUDIT —— 外部内容入库审计记录

审计方法：文件清单 → 许可证 → 脚本行为静态扫描（网络/写盘/执行/删除）→ SKILL 正文提示注入检查 → 结论。
2026-11-14 起增加机械分诊一步：`skills/plugin-forge/scripts/security_audit.py`，规则见文末「例行安全审计」。
审计人：plugin-forge-docs（文档工程师）流程执行，2026-11-11。

| 对象 | 版本 | 许可证 | 行为 | 结论 |
|---|---|---|---|---|
| blader/humanizer | v4.0.0 | MIT | SKILL.md 纯改写规则，1 个 Wikipedia 外链；scripts/validate-package.py 只读本包文件做结构校验 | 通过。规则已并入 references/style-anti-slop.md，未整包 vendor |
| op7418/Humanizer-zh | main | MIT | SKILL.md 中文模式库；tests/check_structure.py 只读比对两份文本 | 通过。同上 |
| woosal1337 asd-ste100 kit | 2.0.5 | MIT（kit；ASD-STE100 规范本身版权属 ASD） | ste-lint.py 本地 lint；hooks 为 Claude Code 专用；无网络 | 通过。规则摘要并入 style-anti-slop.md；本仓库未复制其代码文件，仅引用与摘要 |
| neko-plugin-sandbox/scripts/sandbox.py | 本仓库自有 | — | 全 127.0.0.1；git/uv/pip 子进程仅用于本地沙箱；taskkill 先核对进程身份；rmtree 只删 `plugin/plugins/<id>` 下的目录 | 通过（2026-11-14 复核）。依据源码 @645da54 的契约断言已在 0984249 复验。**后两条是有条件的，2026-11-11 那轮逐行复核时并不成立**：删除面靠 `plugin-add`/`plugin-rm` 先把 id 按 `^[A-Za-z_][A-Za-z0-9_]*$` 校验（`plugin-add` 与 `cmd_plugin_rm` 各校验一次 id）才关在 plugins 目录内；杀进程面靠 `_pid_cmdline` 读命令行与存活、`pid_verdict` 判身份、`stop_action` 定走向，`cmd_stop` 调用它们；旧版这里写的是行号，已按本文件「不写行号」的口径换成符号引用（2026-11-15：当时写的 585/616/385/420 全部漂了），命令行读不到而进程还在时拒杀，除非明确 `--force`（2026-11-15 改；2026-11-16 由 `should_kill` 换成 `stop_action`，判定与执行拆开，自检 `stop 判定` 跑满 verdict×alive×force 十二种组合）。边界：任一校验被删或被放宽，本行结论即刻失效，须重新逐行复核 |
| session_capability_refresh.py | 本仓库自有 | — | 只读扫描 + stdout JSON，零第三方依赖，异常全捕获 exit 0 | 通过。示例路径已脱敏为 ~（**教训**：那次脱敏把 `ROOT` 的字面 `~` 留成未展开路径，脚本一度恒报「路径缺失」；2026-11-14 改为 `os.path.expanduser` 读取、只在输出侧脱敏） |
| avatars/*.jpg/png（14） | — | — | EXIF 扫描 | 无 EXIF，通过（avatars 已随 v2.0.0 移除，见整合记录） |
| intro.html（2） | — | — | 无 <script>/外链 src/onclick | 通过（intro.html 已随 v2.0.0 移除，见整合记录） |
| references/delivery-self-check.md（外部行为闸门全文 136 行，含 3.11/yzg 单机示例） | 未标注版本 | **来源与许可未确立**（frontmatter 名为 `delivery-no-pseudoblock`，2026-11-14 本机检索无上游出处） | 纯文档，无可执行代码；行为级提示词 | 暂保留但**不设为强制**：来源确立前不得作为交付闸门接线（SKILL.md §6 现在把它列为可选参考，强制口径是 §6 正文自己写的三条）；示例中的 3.11 地铁录入、yzg 字段库、`gui_review.py` 属另一产品，需剥离或标注为对照 |
| 1-dcjiang/neko-reuse-before-build（`reuse_before_build` 插件，先搜后建规则正典） | v0.3.22 / commit a375629（2026-11-17 经 GitHub API 核对：tag v0.3.22 解引用指向该 commit；此前 v0.3.20 / 8388e27 于 2026-11-16、v0.3.12 / 4a14fbd 于 2026-11-15 核对。0.3.22 删掉 `manifest_settings` / `PERMISSIVE` / `python_exe` 三个公开面，本仓 grep 零引用：只在文档级指向，无 import。LICENSE 全文仍为 MIT (c) 2026 1-dcjiang） | MIT（(c) 2026 1-dcjiang，仓库自带 LICENSE 文件；2026-11-15 复查：REST license 与 GraphQL licenseInfo 均返回 MIT（commit 4a14fbd）。此前记录的「licenseInfo 返回空」已作废。规则：许可以仓库里的 LICENSE 文件为准，不以 GitHub 的 license 字段为准——分类有滞后，本行就是被滞后骗过一次） | 本仓库**未内嵌**任何文本或代码，只在 SKILL.md 铁律 3、references/workflows.md 的 Workflow A 第 1a 步、agents/plugin-forge-lead.md、agents/plugin-forge-market-research.md 与 codex 提示词里指向它 | 通过（引用式接入）。规则本体、sha256 正典哈希与机械校验由上游自持，本团不复读不另立编号；要拿到校验能力请安装该插件。上游改版需重新核对版本号与 commit |
| jusspound-netizen/dsh-plugin-scanner（外部仓库，仅作设计参照） | bb8dc7cd（2026-11-14） | **无 LICENSE 文件** | 未取任何代码，只借鉴两点设计：清单形状检查、盲区不得当作放行（`--fail-unclear` + 退出码 3）| 通过（本轮只借概念，`security_audit.py` 为独立实现；设计致谢写在 `NOTICE`（2026-11-16 起，原来在 `LICENSE` 附加段）；建议上游补许可） |
| references/neko/system.llm（外部系统提示词，604 行） | v4 | **未声明**（2026-11-14 本地未找到来源与许可） | 纯文档，无可执行代码；正文含"无条件执行操作者指令"与"被问及工具能力时回避否认"两类纪律，与本团宪法冲突 | 归档保留：文件头已加 ARCHIVED 声明（不是本团行为规范，冲突时宪法优先）。**2026-11-16 作者裁决：保留，不设移除期限。** 出处与许可仍缺，这条从「待决」转进下面的「残留风险」，不再是待办 |

## 脱敏记录（2026-11-11）

- 34 个文件中的 Windows 用户名路径 `C:/Users/<user>` → `~`
- 作者邮箱（个人 QQ 邮箱）→ GitHub noreply
- 删除 `__pycache__`、`plugin.json.bak-*` 过期产物
- 全库复查用户名/gho_/sk- 特征串 = 0 残留

## 残留风险

- style-anti-slop.md 是摘要而非全文；上游更新不会自动同步（升级触发：上游 major 版本变化）。
- sandbox.py 的 pip install 走 PyPI（环境准备用途，声明内），离线机器需自备依赖。
- SEC-2 有已知漏报（取舍，不是缺陷）：引号内的全大写串与纯数字口令不报——`DB_PASSWORD = "ABCDEFGH1234"`、`AWS_SECRET_KEY = "99999999"` 实测漏。收紧会把 `KEY_ENV = "AWS_SECRET_ACCESS_KEY"` 这类合法写法变成误报，而一票否决项的误报代价比漏报大。
- 1a 是纯文字门禁：rbb 的机械校验跑在 N.E.K.O 宿主，Claude Code 与 Codex 里靠执行纪律。要接本地 checker，得先解决「第二份规则副本会各自漂移」。
- `references/neko/system.llm`（604 行）随 marketplace 的 `./` 一起公开分发，出处与许可至今未确立。作者 2026-11-16 裁决保留、不设移除期限，所以这条是**长期风险**，不是待办：日后一旦确立出处，按 `NOTICE` 与本表补记；若上游主张权利，移除动作要连着改 `NOTICE`、本表与本节三处。

## 契约门禁体检（2026-11-15，issue #3）

issue #3 的 `check_contract_approval.py` 三条具体缺陷，在 HEAD 上逐条实跑复现**不成立**——它们是针对旧提交 `1a2ac4f` 写的，早已修好；真正缺的是**没人守着**。本轮把三条钉进 `--self-test`，每条再做一次反向对照（把修复撤掉，确认自检报错）：

| issue 条目 | HEAD 实测 | 钉住的测试 | 反向对照 |
|---|---|---|---|
| 目标按 cwd 解析、rel 按准奏目录算，子目录运行会误拒 | 不复现。在 `sub/` 里跑相对路径：`src/a.py` ALLOW，`sub/b.py` 因 scope 拒绝 | 子目录相对目标须解析成 `sub/nested.py` 并 ALLOW | 撤掉 `_git` 的 `cwd=root` → 报 `SELF-TEST FAIL(git)` |
| `--changed` 漏未跟踪新文件 | 不复现。`git ls-files --others` 已并入，实测新建文件进名单 | 空仓库与有提交仓库两种状态都验 | 撤掉 `ls-files --others` → 两条 FAIL（名单少了新文件） |
| 跨盘符 `relpath` 抛 ValueError 不 REFUSE | 不复现。异常被吞并转成「路径在项目根之外」，退出 1 | `Z:/` 根 + `C:/` 目标：Windows 走 ValueError 分支，Linux 走 `../` 分支，结论必须一致 | 撤掉 try/except → `ValueError: path is on mount 'C:', start on mount 'Z:'` 冒出来，自检抓到 |

issue 里标「未核实」的一条也核了：`plan_sha256` **确实**被检查。填对摘要放行，改过 `PLAN.md` 后同一份准奏变成 `REFUSE …（plan_sha256 记录 310c3b1f…，实际 97141b78…）`；填了摘要没填 `plan_file` 也拒绝。

同 issue 的其他条目，现状：`session_capability_refresh.py` 已改 `os.path.expanduser`（输出侧才脱敏，`replace('~/','~/')` 那种空操作已消失）；`generate_roster_tables.py` 的 `write()` 跟随目标文件原行尾；README 已称 profile 为**本地**权威并写明 ⓪ 陈旧门禁会降级；`neko-sources.md` 的工作坊条目已指向主仓 `fengdiehualian/neko-plugin-workshop`；profile §3/§7 于 2026-11-15 按 `acfbdbc50` 逐条重核。仍未动：`issued_by` 是自证字符串（要签名设施才能修），`.codebuddy-plugin/plugin.json` 清单缺项在工坊侧。

本轮另加两处小改：缺 `expires_at` 时打一行 WARN（不改判定，长期有效的旧准奏仍放行，只是让人看得见）；README 里「基线 0984249」一句改成两个基线的分工说明。反向对照脚本 `pf-report/gate_negative_controls.py` 留在仓库外：它靠锚定被测源码的字面文本工作，进了仓库就会变成一条天天误报的脆检查。

- **我自己的误判，记下来防复发**：写第一条测试时 `git diff` 退 129，我判成「空仓库（无提交）时 git diff 会失败」，并据此加了 `_has_head()` 分支走另一条名单。反向对照发现那个分支什么都测不到，回头实测：真·无提交仓库里 `git diff` 退 0、`ls-files --others` 正常列出文件，129 来自我的测试**忘了跑 `git init`**。分支已删除。教训：先复现故障再写修复；测试自己坏了也会长成产品缺陷的样子。

## 外部深审 bd9c9d8 的复核（2026-11-15，报告 A472）

8.6/10（上次 8.3）。报告列 8 组问题，逐条实跑核对**全部属实**，其中两条是 `bd9c9d8` 自己引进的。

| 条目 | 核对与处理 |
|---|---|
| 三.1 自检里写了两个完整的私钥头字面量 | 属实，是我上一轮加的端到端用例。实测：不带 `--allow` 扫本仓库 → SEC-2 3 处、总分 40、阻断。已拆写。教训与本文件既有的「夹具一律拆写」规则冲突，说明**加检查的同时要重跑那个检查对自身的扫描** |
| 三.2 自检临时目录不清理 | 属实，本机累积 6 个 `forge-audit-e2e-*`。已加 `finally` 里的 `rmtree`，并断言目录消失 |
| 三.4 一票否决名单三处不一致 | 属实。代码里有 `server.key`，注释却写「不含 .key」；`.npmrc` / `.jks` / `.keystore` 计否决与「误报代价比漏报大」相反。已收窄（见 `security-checklist.md` 新增的「文件名判据」），`.npmrc` / `.pypirc` 改成按内容判，`.jks` / `.keystore` 退回真盲区，`id_*` 改前缀匹配 |
| 二.1 小改动与 Workflow B 不经过 1a | 属实。`task-size-routing.md` 精简流与 Workflow B 都没有快速档这一步，SKILL 铁律 3 也没写执行者。已指名：lead 派 market-research 跑快速档；纯文档 / 文案不触发；升档到「中」以上时 1a 升级为 full lane |
| 三.5 宪法第 4 条没同步 | 属实。`constitution.md:18` 仍是「外部不可用就跳过」，而 1a 写「不豁免」。已补 1a 例外。我上一轮在 AUDIT 写的「不再对撞」是过头话，已改成事实 |
| 三.3 delivery-self-check 残留 | 属实，四处：SKILL 的那句「任何宿主都以读取该文件为加载」与「不要求执行」矛盾；AUDIT 指着一个已不存在的「§6 待决项」；`doc-writing-playbook.md` 五处仍把它当必经阶段。全部改为「可选参考，必经的是 SKILL §6 三条」 |
| 三.6 `plugin-rm` 删不掉悬空软链 | 属实。`is_dir()` 在前，悬空软链被判「不存在」，`is_symlink()` 分支走不到。已抽 `classify_plugin_path()`，先认软链 |
| 三.7 AUDIT 行号与计数过期 | 属实。本文件两处已换符号引用，豁免 1 的命中数改成实测口径 |
| 追加发现 A（反向对照抓出来的） | **CI 的「Sandbox self-test」这一步永远不会红**。入口写的是 `if __name__ == "__main__": main()`，`main()` 把 `--self-test` 的返回码丢掉，所以自检打印 FAIL 也退 0。已改成只把整数当退出码（命令路径的 `cmd_*` 返回值不算失败码）。反向对照：撤掉 `classify_plugin_path` 的软链优先判断 → 自检打印 FAIL 且退 1 |
| 追加发现 B | 自检的临时 git 仓库删不掉，本机攒了 15 个 `forge-gate-git-*`。原因：Windows 上 git 把对象文件写成只读，`unlink` 报 WinError 5，而 `shutil.rmtree(..., ignore_errors=True)` 把失败吞了；`onerror` 参数自 Python 3.12 起弃用（文档写 3.14 换成 `onexc`），但本机 3.15.2 实测它仍被接受、也不告警，所以参数名不是第一版失败的原因：真正的错在我那个处理函数只 chmod、没有重试失败的操作。已加 `_rm_tree()`：`onexc` 里 chmod 后重试，并兼容只有 `onerror` 的旧版本。清理失败只打 WARN，不判失败（防软可能临时锁住对象文件） |

### 追加：沙箱环境变量过滤的名字缺口（2026-11-15，由 sabotage 讨论引出）

反向对照那次 sabotage 用 `NOT_A_SECRET_XYZ` 想让自检失败，结果它反而被当成凭据——过滤器是**按字样**判的。
顺着这条实测出缺口：`COOKIE`、`AUTH`、`AUTHORIZATION`、`SESSION`、`SESSION_ID`、`BEARER`、`JWT`、
`DSN`、`SSHKEY`、`CONN_STR` 这 16 个名字当时会直接漏进沙箱（`DATABASE_URL`、`PASSWD` 其实已被值规则
和子串规则接住，那两个例子是误报的误报）。

处理：`SECRET_ENV_RE` 加 COOKIE/AUTH/AUTHORIZATION/SESSION/BEARER/JWT/DSN/SSHKEY/KEYCHAIN；
`SECRET_ENV_SUB` 加粘连写法 COOKIE/BEARER/JWT/SSHKEY/KEYCHAIN；新增 `CONN_SECRET_RE` 接
`Password=...` 这类非 URL 连接串。代价是 `SSH_AUTH_SOCK` 会被命中，所以写进 `KEEP_EXACT` 显式保留
（它不是口令，删了 git over SSH 就找不到 agent）。`SESSIONNAME`、`OAUTH_SCOPE`、`AUTHOR_NAME` 这类
按边界规则仍被保留。本机实测多删的是 `CLAUDE_CODE_SESSION_ID` / `WT_SESSION` 这一类会话号，
N.E.K.O 服务与 git/uv/pip 都不读它们。自检名单随之扩充（项数以 `--self-test` 实跑输出为准，本文件不钉数字）。反向对照：撤掉名字扩充 → 4 条断言报错、退 1。

边界规则不误伤 `LICENSES`、`LICENSING`、`OAUTH_SCOPE`、`AUTHOR_NAME`、`SESSIONNAME`；`LICENCE` 与
`LICENSE` 两种拼写都按名字剔掉（实测 `OPEN_LICENSE_DIR` 也剔）。

**SESSION 一类会被剔掉。** 插件若靠 `SESSION_ID`、`WT_SESSION` 这类变量做会话关联，在沙箱里读不到——
本机实测新剔的就是 4 个会话号。要关联就把值当参数显式传给插件，别依赖环境继承。这句同样写在
`sandbox.py` 的注释里，两处都有记录。

已知残留：粘连的 `LICEN[CS]E` 写法（`MYLICENCE`）不删；值里是短占位串（`<token>`、`$VAR`）时不判，避免把模板当凭据。

### 2026-11-15 二次补：粘连 KEY 与值里的 PEM 私钥

上一版把「粘连写法不删」记成已知残留，但 `KEY` 这一族漏得最狠：`PRIVATEKEY`、
`TLS_PRIVATEKEY`、`ENCRYPTIONKEY`、`SIGNINGKEY`、`MASTERKEY`、`SESSIONKEY` 六个名字
在值就是整段私钥时仍判「不是凭据」，本机实测复现。第三方插件在沙箱里能读到宿主私钥。

处理：`SECRET_ENV_SUB` 补 `KEY`；新增 `PEM_PRIV_RE`，值里出现 PEM 私钥头就算凭据。
代价是 `KEYBOARD`、`KEYMAP` 会被子串命中，所以写进 `KEEP_EXACT` 显式保留。
`LICEN[CS]E` 仍按边界判，不加子串：加了会误删 `LICENSES` 与 `LICENSING`，
那是上一版明确要保的。
自检加 2 条保留例与 7 条剔除例（夹具里的 PEM 头按 A472 口径拆写，不让本文件被自己否决）。
反向对照：在仓库外的副本里撤掉 `KEY` 与 `PEM_PRIV_RE` 两处 → 报
`ENCRYPTIONKEY`、`SIGNINGKEY`、`MASTERKEY`、`DEPLOY_BLOB` 四条断言失败。实跑 `--self-test` 的项数以命令输出为准。

## 外部深审 edc862d 的复核（2026-11-15，报告 A464）

报告给 8.3/10，列 14 条。逐条核完：13 条属实，1 条部分属实。这一轮暴露的不是代码问题，是**我把计划写成了结果**：提交 `69cde01` 的正文与 `AUDIT.md` 都写「新增两条规则形状：离线降级…与快速档」，实际手册里只有快速档。是外部审查看出来的，我自己的检查没看出来。已补条文（`references/workflows.md` 1a 节、SKILL 铁律 3、market-research 本职 4、codex 提示词第 1a 步），提交历史按既有口径不改，以本节为准。

| 报告条目 | 处理 |
|---|---|
| 1 离线降级只在 AUDIT 里存在 | 属实。已补进四处操作性文档，并写明「1a 不可跳，但它可以以『未核验』形态交付」。当时 `constitution.md` 第 4 条仍是「外部不可用就跳过」，这句说过头了：例外条款到 A472 那轮才补进去（见下一节） |
| 2 delivery-self-check 与 AUDIT、LICENSE 冲突 | 属实。SKILL §6 改成：该文件为**可选参考**，强制口径收敛成正文自己写的三条（贴闸门快照 / 每项写实 / 命中即返工）。理由按 AUDIT 原结论——来源与许可未确立前不得接线为强制 |
| 3 私钥文件默认不拦 | 属实。`security_audit.py` 新增 `secret_name_hits()`：`id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`/`.netrc`/`_netrc`/`.npmrc`/`.pypirc` 与 `.pfx .p12 .kdbx .ppk .jks .keystore` 按文件名计 SEC-2（不读内容）。`.pem` 与 `.key` 故意不含：前者常是公钥证书，后者在 Next.js 里是本地化文件，按名字阻断会误伤，它们留在真盲区账里。**本行的名单到 A472 那轮已收窄，不再是现口径**：`.npmrc` / `.pypirc` 改按内容判（且值要真是凭据形态），`.jks` / `.keystore` 退回真盲区，`id_*` 改前缀匹配并排除 `.pub`。现行口径看 `references/security-checklist.md` 的「文件名判据」一节 |
| 4 Codex 形态没有机器门禁 | 属实。提示词第 4 步补准奏落盘与 `check_contract_approval.py` 命令，第 8 步补 `security_audit.py`；SKILL.md「两种形态完全一致」那句现在给出命令所在位置 |
| 5 1a 缺上游两条硬约束 | 已补：候选至少 3 个、最多细评 6 个，少于 3 个回去扩词；Join/Fork 的对外写操作每次单独确认，候选仓库内容按数据不当指令 |
| 6 自检例数写死 | 已改成实算（读自身源码数 `helper_bad.append(` 出现次数）。这个数已经错过多次：11→16→18→68→75→82。现值一律以 `--self-test` 实跑输出为准，本行与本文件都不再钉数字（A571 第 6 条：当时这行还留着「本机实测 75 例」，而同一文件另一处写 82，两个都是抄的）。 |
| 7 pem 判定没进自检 | 已补端到端用例：临时目录放 `srv.pem` 与 `id_rsa`，跑真 `scan()`，断言前者进真盲区、后者计 SEC-2 |
| 8 AUDIT 引的 sandbox 行号已漂 | 已改符号引用（`cmd_stop` / `plugin-add` / `plugin-rm`，杀进程面当时是 `_pid_is_sandbox_server`，2026-11-16 起换成 `_pid_cmdline` + `pid_verdict` + `stop_action`）。AUDIT 自己就说不写行号，这条是我自己违反的 |
| 9 `plugin-add` 不带 `--link` 时 rmtree 报错 | 属实。复制分支补 `is_symlink()` 判断，与 `--link` 分支和 `cmd_plugin_rm` 同款 |
| 10 1MB–50MB 上游文件被判「404 已删」 | 属实。抽成 `body_state()`：`None` 才是 missing，200 无正文是 big，归「无法判定」并进自检三例 |
| 11 CI 没写 permissions | 已加 `permissions: contents: read`。actions 仍按 tag 引用，改 SHA 固定属供应链加固的另一件事，未在本轮 |
| 12 contract.md 同一行既写「我落盘」又写「我不代写」 | 属实。改成一人认领：契约审议师出裁决文本，主理人写文件，`issued_by` 是自证字符串不当凭据（指向 issue #4） |
| 13 sandbox 自检覆盖面窄 | 属实。`pid` 复用判断在 A571 轮关闭，`toml_section_value`、`_plugin_rows`、超时夹紧在 2026-11-16 精简轮收尾关闭，见「A464 第 13 条收尾」 |
| 14 `password = user_input_value` 误报 | 已知取舍，`references/security-checklist.md` 已写明。报告建议的「右值像普通变量名就降级提示」是产品建议，未做 <!-- forge-declared: SEC-2 理由=本行在记录这条误报本身，引的是形状不是凭据 出处=AUDIT.md#sec-declared 复审=2026-12-15 --> |

反向对照（撤掉修复看自检是否报错）：撤掉 `secret_name_hits` 的接入 → `SELF-TEST FAIL: id_rsa 按文件名没计进 SEC-2，入库私钥仍会被放行`。实跑口径：含 `id_rsa` 与 `server.pfx` 的目录 → SEC-2 2 处、85/100、阻断退出 2；只含 `cert.pem` 与 `app.key` 的目录 → 不阻断，但真盲区 2，`--fail-unclear` 退 3。

## 外部深审 ac9af4d 的复核（2026-11-16，报告 A571）

8.7/10（上次 8.6）。报告列 8 组新问题，逐条对源码核对**全部属实**，没有一条需要驳回。报告自己声明是
增量只读审、没跑代码。这一轮每一条都由我实跑复核后再改。

| 条目 | 核对与处理 |
|---|---|
| 1 `is_protected` 的目录旁证按绝对路径做子串匹配 | 属实，中。`check_targets` 传进去的是绝对路径，于是项目放在 `C:\tools\proj`、`~/scripts/x` 下面时，每个无扩展名文件（`LICENSE`、`.gitignore`、`Makefile`）都被判成受保护代码，没准奏就拒。改成按**项目根相对路径逐段**匹配（新增 `_hint_segments` + `_dir_hint_hit`，`is_protected(path, root=None)`）。调用方没给 root 时保守到只认紧邻父目录。自检真造一个 `<root>/scripts/proj` 当项目根钉这条。同一函数的反向缺口一起补：`.svg` 进 `CODE_EXTS`，`Makefile` / `Dockerfile` / `Containerfile` / `justfile` 进新增的 `BUILD_FILE_NAMES`，`hooks.json` 进新增的 `HOST_EXEC_NAMES`。踩到一处自己的坑：第一版用 `os.path.relpath` 的结果直接按 `/` 切段，Windows 上 relpath 给反斜杠，旁证整条失效，是自检把它判红的 |
| 2 `stop` 的保护在 macOS 上永远读不到 | 属实，中。非 Windows 分支只读 `/proc`，macOS 没有 → 判定恒 None → 每一次 `stop` 都要 `--force`。POSIX 那半抽成 `_posix_cmdline(pid, read_proc, run_ps)`，取数函数由调用方注入，于是 macOS 的形状能在任何平台上自检（`ps -p <pid> -o command=` 兜底）。另一半是同一份「读不到」被混用：pid 已不存在（服务崩了、`start` 超时后 terminate）也要 `--force` 才清得掉状态文件。现在 `_pid_cmdline` 返回 `(cmd, alive)`，判定拆成 `pid_verdict` 与 `stop_action`（kill / gone / reuse / refuse 四走向），`should_kill` 由 `stop_action` 取代。`--force` 不越 `reuse`：force 的意思是「读不到命令行也杀」，不是「确认是别人的进程还照杀」。`cmd_stop` 末尾那句无条件的「已停止 pid=…」改成按走向分别打印。`neko-plugin-sandbox/SKILL.md` 补「stop 的四种走向」一节与 `--force` 说明（报告点的那条缺 `--force` 已补）。真进程实测四种走向见下 |
| 3 `id_rsa.pub` 被当私钥一票否决 | 属实。前缀匹配连公钥一起命中，与本文件既有的「误报代价比漏报大」相反。加 `PUBLIC_KEY_SUFFIX` 排除，自检补一例，并补一例反向（排除 `.pub` 以后 `id_ed25519.bak` 仍要命中） |
| 4 `.npmrc` 写 `${NPM_TOKEN}` 也被否决 | 属实，而且那是 npm 官方推荐写法。`auth_marker` 现在逐行取值判形态：空值、`${...}` 环境变量引用、占位符都不计，只有真凭据形态才计。自检三例（引用 / 占位符 / 真令牌）钉住 |
| 5 `--allow` 在文件名那一遍没归一化 | 属实。`./tests/fixtures/` 与反斜杠写法豁免得了内容扫描、豁免不了夹具里的 `id_rsa` → 假阻断。抽 `norm_allow` / `is_allowed` 两处共用，自检四种写法各跑一遍，另加一例反向（豁免写错目录时仍要报） |
| 6 文档漂移 8 处 | 属实，逐条改：SEC-3 名单补 `marshal` / `yaml.unsafe_load` / `os.popen` / `subprocess.getoutput` / `getstatusoutput` 五条。「三处常量一致」改成实际的六处并点出各自用处。`profile_drift.py` 文件头改成 urllib 直连。门禁规则段列出四条判据（原来只写扩展名）。`task-size-routing.md` 小标题去掉「只到 dev + tdd」旧口径、「full lane 才免」改写成可执行的免跑条件。lead 与 codex 的宪法要点版补 1a 例外。codex 短任务路径补 1a 快速档、第 1a 步补「最多细评 6 个」。报告没点到的两处同口径残留一起改了：`agents/plugin-forge-lead.md` 与 `references/workflows.md` 也写着「小改动只到 dev + tdd」 |
| 7 `共 (\d+) 人` 是空转模式 | 属实。`--check` 实跑证明全仓库没有这句（grep 亦为零命中），只有自检的合成用例。而它作用于四份文档，将来任何无关的「会上共 3 人」都会被 `--write` 改成编制人数。锚定成 `共 (\d+) 人编制`，自检补一例反向：「会上共 3 人发言」不许被改 |
| 8 `LICENSE` 附加段较长 | 属实。标准正文后接 17 行第三方署名，licensee 可能识别成 Other。署名整段挪到新增的 `NOTICE`，`README` 目录树与那句许可说明、本文件 `AUDIT.md` 引用 `LICENSE` 致谢的一处同步改。**挪的时候发现正文本身有缺陷**：`LICENSE` 的 MIT 句子在 `NONINFRINGEMENT.` 就断了，缺 `IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE …` 一整段——即少了责任限制那半条。已按 MIT 标准全文补回，这不是我改风格，是原文不完整。`system.llm` 的来源与许可仍未确立，作者已裁决保留，见下面「裁决与遗留」 |

反向对照（在仓库外的副本上撤掉修复，看自检是否变红）：12 组控制**全部变红**，没有一组是空转。
逐条为——公钥排除（`id_rsa.pub 是公钥，不该按私钥一票否决`）、`.npmrc` 值形态（引用与占位符两例）、
`--allow` 归一化（`./`、反斜杠、尾斜杠三种写法各报一条）、目录旁证按段匹配（`上级目录名 scripts 被当旁证`）、
名单扩充（`Makefile` / `Dockerfile` / `hooks.json` 三例）、`gone` 走向（6 条组合变红）、
macOS 的 ps 兜底（撤掉兜底后 `POSIX 分支：verdict=None 期望 verdict=True`）、
人数模式锚定（「会上共 3 人发言」被改成 14），加上 stop 确认那四条：`_wait_gone` 恒报已停、状态字符不校验、僵尸算还活着、Windows 用密格子。控制脚本留在 `~/forge-scratch/`，不进仓库。

真进程实测 Windows（本机 `sandbox.py stop`，2026-11-16）：命令行里带 `user_plugin_server.py` 的活进程
→ 打印「已停止 pid=…」，随后该进程确实没了。无关活进程带 `--force` → 判复用、不杀，该进程仍活着。
不存在的 pid → 打印「进程已不存在…只清理状态文件」，状态文件删除且不需要 `--force`。

真进程实测 Linux（WSL Debian 13 trixie，内核 6.18.58.1-microsoft-standard-WSL2，Python 3.13.5，
2026-11-16，作者指定用 WSL 验这一条）：`--self-test` 在同一台机器上退 0。四种走向的结论与 Windows 一致。
这轮实测**抓出一个新缺陷**：`_kill_pid` 发完 SIGTERM 就返回，`cmd_stop` 随即打印「已停止」，
而抓住 SIGTERM 不做收尾的进程根本不退。真实场景就在眼前——uvicorn 会接 SIGTERM 做优雅关闭，
收尾卡住时端口还被占着，用户读到的却是「已停止」。
修法：发信号后按 `KILL_WAIT`（3.0 秒）轮询确认真没了才报「已停止」。没确认为了退出就留着状态文件，
并写明端口可能还被占。`--force` 才升级到 SIGKILL。确认探测里僵尸态（`Z`）算已退出，
它是 SIGTERM 成功后最常见的形状。Windows 的确认探测改用稀疏格子（0.4 / 1.0 / 2.0 秒），
因为一次探测要起一个 PowerShell，密格子把 `stop` 拖到 4 秒以上。
`SKILL.md` 的走向表因此从四种改成五种（多出来的就是「发了信号但没退」）。

macOS **不验**：作者 2026-11-16 指定「Macos 不管」。那里的 `ps` 兜底只有离线用例撑着。

A464 第 13 条留的「`pid` 复用判断仍无用例」这一项，本轮起关闭：判定表 12 组、POSIX 5 组、
状态解析 5 组、存活翻译 6 组、`_wait_gone` 3 组，全在自检里。

实跑口径（2026-11-16，本机）：五个脚本 `--self-test` 全绿。例数一律以实跑输出为准（本文件不钉数字）。
对本仓库真跑扫描（三条豁免、CI 同口径）为 SEC-1 1 处、SEC-2 0 处、总分 95/100、放行。
`--check` 报名册与人数声明一致。

### 裁决与遗留（2026-11-16）

- `references/neko/system.llm`：**作者已裁决保留，不设移除期限**（2026-11-16，用户在本次修复回奏里定的）。
  本文件原来写「补不上则从公开仓库移除」，那句从今天起不再是待办。出处与许可仍然缺，所以这条改记在上文
  「残留风险」一节：长期风险，随时可被上游的主张推翻。日后若确立出处，改三处——`NOTICE`、本表第 19 行、残留风险那一条。
- `NOTICE` 的位置：本仓库把它放在仓库根。若以后走包分发，需要确认打包面（`files` / 插件包）把 `NOTICE` 一起带上，
  否则署分会随打包丢失。致谢项的合规要求比 `LICENSE` 更长。



## 代码精简轮（2026-11-16，ponytail + simplify-codebase）

范围。作者指定用 ponytail 与 simplify-codebase 精简代码。目标面是六个 Python 脚本共 3522 行，其中自检 667 行。
摸底用一条工作流：7 个 finder 分头读，每条候选过两个对抗镜头（可达性反证、门禁保权），最后一名批评员复核覆盖面。
68 个 agent 跑完，30 条原始发现，20 条双镜头通过，7 条有争议，3 条被驳回。
基线（改动前实跑）：五个 `--self-test` 全绿，`roster --check` OK，真实扫描 95/100 放行。

砍掉的东西，每条都有反证或探针：

| # | 位置 | 砍掉的 | 依据与后果 |
|---|---|---|---|
| 1 | sandbox.py `cmd_serve` | Popen 之前那次 `save_state(pid=os.getpid())` | os.execve 时代的化石。那份状态里的 pid 指向包装进程而不是服务进程。Popen 抛异常时它还留在那里等 pid 复用。删掉以后无状态窗口只报「没有状态文件」，不可能再打到无关进程 |
| 2 | sandbox.py `prepare` | `--repo-url` 与 `--ref` 两个旗标 | 全机器零调用者，SKILL.md 只教 `--repo` 挂载。要测 fork 或钉版本：自己 clone 再用 `--repo` 指过来，`sync` 读的是那个仓库自己的 origin。探针 P1/P2 确认两个旗标现在退 2 |
| 3 | sandbox.py `cmd_status` | `b.get("items")` 与 `b.get("data")` 两个猜测键 | 上游三条路由的列表键就是 STATUS_ROUTES 的第二个字段。实测外形是 `{plugins,message}`、`{plugins,time}`、`{messages,count,time}`。猜键保护不了任何人：键真被改掉，行解析器同样要跟着改 |
| 4 | check_contract_approval.py `compile_glob` | `?` 与 `[...]` 两类通配 | docstring 与 schema 从未承诺它们，零夹具零文档使用。更要紧的是 `[...]` 把 scope 文本原样塞进正则：`src/[b-a].py` 抛 PatternError，整轮判定跟着消失。这正是本文件第 26-27 行自己立的铁律要防的形状。现在两个字符走 re.escape 按字面匹配，失败方向是少覆盖、多拒。新增 5 条 glob 用例钉住，控制 C1/C2 变红 |
| 5 | security_audit.py `score()` | 从没被读过的 `scanned` 形参 | 判定逐字节不变。`scan()` 仍返回 scanned，main 用得上 |
| 6 | security_audit.py 自检 | 手写的第二份 id 到表 的映射 | 改成 `dict(TABLES)`。旧写法在新增一档时会静默漏测。控制 C3 故意写错一档，自检立刻变红，证明这条路径真被跑到 |
| 7 | 四处 `getattr(args, "x", False)` | 对 argparse 保证存在的属性做防御 | 边界作秀：属性一定在。改成直接取名字，接线错了当场报 AttributeError，而不是静默按 False 走下去 |
| 8 | sandbox.py `cmd_doctor` | `[c for c in checks[:1] if not c[1]]` | 假循环，外加对 checks[0] 的位置耦合。改成先算 `py_ok` 再判它 |
| 9 | sandbox.py 自检夹具 | 函数内的 `import pathlib` | 模块层已经有 `from pathlib import Path`。`tempfile` 只有这一处用，保留它的局部导入 |
| 10 | profile_drift.py | `--limit` 与它的截断 | 全机器零调用者，CI 只传 `--strict`。匿名限额那一小时 60 次由 24 小时缓存与 CI token 承担。探针 P3 确认退 2 |
| 11 | roster.json | 14 行 `profession_zh` | 零读者。运行副本在 agents/*.md 的 frontmatter，那一份由 `--check` 逐字段核对。roster 里这一份没人比，正是第 115 行记过的 displayName 同款隐患 |
| 12 | 文档里的数字钉死 | 16 处「完整 11 条」与 4 处「23 份笔记」 | 数字今天都对，但没有任何检查器覆盖它们，改一次宪法要手改 16 个文件。只留归属那一份：constitution.md 的标题与 neko-sources.md 的清单表。口径同 A571 第 7 条 |
| 13 | 本文件第 83 行 | 钉死的「61 项」自检计数 | 本文件在 119、174、227 三处宣布不钉数字，这一行自己还钉着，而且已经过期 |

补上去的说明与文档。这些是加行，不是砍行：STATUS_ROUTES 上方钉住三条路由的返回外形。CODE_EXTS 上方写明本仓库两份扩展名名单用途不同，别当成同一件事。
`install --python` 的帮助文本改对：uv 走 `--py-version`，pip 走当前解释器，旧话只说了一个默认。
workflows.md 补上 `--staged` 与 `--changed` 两种自动化用法，这两个旗标代码里早就有、文档从没写过。
generate_roster_tables.py 的头两处少报了自己的写范围与模式清单。neko-plugin-sandbox/SKILL.md 流程图那行注释跟上「五种走向」。

决定不砍的，以及理由：
- serve 与 start 的启动序列合并。删掉第 1 条之后只剩两处三行的状态字典，差别只有 mode 与 pid 来源。抽 helper 要新造一个六值返回的接缝，行数打平，动的又正是写 pid 那一段。pid 写错就是杀错进程，而 Windows 的 CI 抓不到。工作流的批评员把这条列为最危险的一项。留。
- `_utf8_stdout` 与 `_utf8` 四份、四套自检 harness、`load_config` 与 `save_state` 两份。单文件可运行是这些脚本的交付前提，README 与每个文件头都这么承诺。合并要引入共享模块，把每条安装路径变成两文件原子拷贝。留。
- `env_is_secret`（sandbox）与 `KEY_WORDS`（security_audit）不合并。前者是运行时无损广删，后者是门禁按名字否决。共享词表会把审计豁免带进过滤器，或者把过滤器的宽度带进否决。两个方向都动检测力。留。
- sandbox.py 两处 rmtree 守护的插件 id 字面量不提取成常量。AUDIT.md 对该行有「可 grep」的要求，提取后守护条件不再能一眼数清，而自检里还有同字面量的第三、第四份。留。
- `BASELINE` 常量与两份文档副本。部署副本单独跑在 `~/.workbuddy/hooks/`，反向读不到 md。md 那份带同意日期与哈希，是控制链记录。留。
- install 的 `--no-uv`。`--help` 就列着它，是公开选项，而且 pip 路径的可控回退只在它给出时才对 SSL 故障生效。留。
- session_capability_refresh.py 的 `inp`（解析 stdin 却没人读）与 `build_context` 的 `truncated` 形参。两处都是真死代码，各一行能改完。这个文件是每次会话都要跑的 SessionStart hook，没有自检，CI 只做语法检查，部署副本另在 `~/.workbuddy/hooks/`，而它自己的 §6 降级契约文本受解冻窗口纪律管。本轮不动，等作者点名开那个窗口。

盲区，照实说：
- `cmd_serve`、`cmd_start`、`cmd_doctor`、`cmd_status` 四个命令体没有自检覆盖（要真起进程或读环境）。第 1、3、8 条改在这里，已在 Windows 与 Linux(WSL) 两端实测，见下面「双端对照探针」。它们调用的辅助函数（`_server_env` 的超时夹紧、`_plugin_rows`、`toml_section_*`）现在有用例了。`cmd_start` 的 `_wait_health` 与 DETACHED 分支仍只有读证。
- `_pid_really_alive` 是 stop 路径上唯一没有用例的探针。CI 跑 ubuntu，Windows 分支不跑它，自检注入的是别的函数。本轮没补：补它要先给它加注入接缝，那是加东西而不是精简。记在这里待办。
- roster.json 的「零读者」只在本仓库与本机可证。团队外部工具若读 `profession_zh`，这次删除会打断它。本仓库 66 个文件、三面装机副本、pf-report 工具都搜过，无命中。
- 装机三面本轮已同步：三面各写 22 个文件，第二遍预演报 0 写入，三面的门禁、扫描器、沙箱自检各自跑通。回滚备份 `pf-report/install-backup-20261116T161829`。`references/profile-dsh.md` 按本机独有保留，未推送。

### 沙箱真机实测（2026-11-16 21:42，Windows 11 26200，本机）

作者要求先看清 `cmd_serve` 少写状态这件事，再谈往副仓搬。

- 沙箱根 `~/forge-sandbox-serve`。挂载既有 checkout `~/neko_host`（HEAD `b20a9f8`）。
  本机绝对路径一律写成 `~`：这份文件会随插件分发，用户名也是信息（SEC-1 那条规矩同样管我们自己的文档）。
  跑前跑后那个 checkout 的 `git status` 都干净，沙箱没写它。解释器用那 checkout 自己的 `.venv`，没装新依赖。
- `serve` 起来：uvicorn 在 127.0.0.1:43117 打 `Application startup complete`，`/health` 回 200。
  数据根落在沙箱内 `state/LocalAppData`。上游内置插件 11 个被扫到，只有 `web_search` 因 `auto_start = true` 进 running。
  用户已装插件一个都没被拉进来，隔离这条守住。
- pid 身份：`sandbox.json` 记的 pid 是真服务进程（那个 venv 的 python 跑 `plugin/user_plugin_server.py`），
  不是 `sandbox.py serve` 包装进程。这正是砍掉 Popen 前那次写入换来的结果。
- `status` 在真返回体上跑通删掉猜测键后的计数：`/plugin/status` items=11，`/plugin/messages` items=0。
- `stop` 花 5.2 秒，先确认再打「已停止 pid=36888」，删状态文件，后台任务随进程树一起退 0。
  事后核查：相关服务进程 0 个，43117 无应答。
- Popen 失败分支的反向对照：在仓库外把「Popen 前先写状态」加回一份拷贝，同一探针下旧写法留下一个
  pid 等于包装进程自己的状态文件，改后的代码什么都不留。脚本 `forge-scratch/popen_fail.py`。

### 双端对照探针（2026-11-16 22:05，Windows 3.15.2 与 WSL Debian python3 3.13.5）

上面那次真机跑的是 Windows。作者上轮指定 Linux 走 WSL，所以同样的三件事在两端各跑一遍。
脚本在仓库外：`forge-scratch/popen_fail2.py`，它不需要 N.E.K.O 依赖（假入口加注入的失败 Popen）。

| 探针 | Windows (nt) | Linux (posix) |
|---|---|---|
| Popen 抛异常，本轮改完的 `cmd_serve` | 什么都不留 | 什么都不留 |
| Popen 抛异常，把旧写法加回去 | 留下 pid=包装进程自己的状态文件 | 同样留下（pid=268） |
| `cmd_doctor` 撞见假 3.9 解释器 | die，退出码 2 | die，退出码 2 |

旧写法那一行就是本轮砍掉的东西：包装进程自己的 pid 被写成服务 pid，进程树一断它就等 pid 复用。
两端同结果，说明这条修正不依赖平台分支。doctor 那行同时证明砍掉 `checks[:1]` 之后判定不再靠位置。

### A464 第 13 条收尾（2026-11-16 22:40，本机）

上面 A464 第 13 条欠的四项里，`pid` 复用在 A571 轮关闭，其余三项本轮补完。用例形状取自
21:42 那次真机跑回来的实际返回体，不是照代码想象。

- `toml_section_value` 9 例。钉住「别按行首是 id 就认」这个老坑：`[other]` 段的 `id` 不答，
  `[plugin.ui]` 段的 `id_panel` 不答 `[plugin].id`，反过来也不答。带引号、单引号、行尾注释都验。
- `toml_section_flag` 6 例。`true/True/1` 判 True，`false/0` 判 False，**没写返回 None**。
  省略与显式 false 在上游语义里同为不启动，但调用方要分得开，所以不回落到 False。
- `_plugin_rows` 9 例。两种真外形都进：`/plugins` 的 list 与 `/plugin/status` 的 id 键 dict。
  另验 `runtime_load_state` 兜底、裸字符串值、空 dict、`plugins=null`、payload 不是 dict。
- 超时夹紧 6 例，走 `_server_env` 真函数：命令行 360 夹到 300。环境带进 45 以 45 为准。
  环境带进 600 也夹到 300。环境值读不成数字时退回命令行值再夹。`isolate_home` 建出
  `state/LocalAppData`。`NEKO_PLUGIN_HOSTED_BY_AGENT` 被清掉（这条先把变量带进环境才验得出）。

反向对照 6 组全部变红（`forge-scratch/run_controls3.py`）：忽略段名、rows 只认 list、不夹紧、
省略回落 False、不清嵌入标志、不建数据根，每种破坏都被咬住。
沙箱自检从 106 项升到 136 项。其余四个脚本的用例数不变。老控制组 12 组与精简轮 3 组复跑仍全部变红。

### 往 coopanion-forge 搬的结论

沙箱这一件没有可搬的东西。那仓的内核清单 18 个目标里没有沙箱件，`find` 搜不到 `sandbox.py`，
`skills/coopanion-forge/scripts/` 只有门禁与扫描器两件。N.E.K.O 沙箱是本仓的平台资产，不在内核同步面里。

门禁那条 glob 修正相反，它是 delta 目标，已随基线 `2d658a7` 落地（那边当时 HEAD `65364c5`，已推，CI 过）。
本条写完之后作者点头推了 tag：远端现在有 `v2.7.5` → `4f04414`，此前远端一个 tag 都没有。
那边随后自己又走了一步（HEAD 现为 `8c88381`，CI 过），并在 deltas 里留下两条等 v2.7.5 的临时钉法。
那一轮没在那边跑 `--apply`：它当时报「一致 15 / 需搬 1 / 缺件 0 / 替换失败 2」，而 `--apply` 不是原子的，死锚仍会写别的文件，那个会话还在里面干活。
作者清场之后补跑了。那边先删掉 3 条等 v2.7.5 的临时改写（`gen_deltas.py` 重生成，21 处降到 18 处），再 `sync_kernel.py --apply --ref 5f7a9f9` 搬 NOTICE 与 workflows.md，`--port-done` 记 ack，`--check` 回到「一致 18 / 需搬 0 / 缺件 0 / 替换失败 0」。那边提交 `73c3ac6` 已推，CI 过。本仓的沙箱件仍然不在它的内核清单里，所以 `cmd_serve` 那件没有过去。
本轮核过的证据：那边的 `compile_glob` 函数体已无 `?` 与 `[` 分支，docstring 与 5 条新用例都在，
门禁 `--self-test` PASS，`sync_kernel.py --check` 报「一致 18 / 需搬 0 / 缺件 0 / 替换失败 0」，
`tools/gen_deltas.py --check` 报 7 个目标 18 处替换一致。

复跑（2026-11-16，本机）。六个文件 `py_compile` 全过。五个 `--self-test` 全绿，例数以实跑输出为准。
`roster --check` OK。真实扫描仍 95/100 放行。本轮 3 组反向对照全部变红，4 个删除旗标探针全部符合预期。
上轮 A571 的 12 组控制复跑仍全部变红，说明本轮没有削掉任何测试的咬合力。控制脚本在仓库外：`forge-scratch/run_controls2.py` 与 `run_controls.py`。

## 检测面与覆盖收尾轮（2026-11-17）

作者连着说「继续」，把上一轮留给他的两项里能自己定的做完，另一项仍交回给他。

### 扫描器池子补上四类扩展名

`security_audit.py` 的 CODE_EXTS 加了 `.htm`、`.svg`、`.m`、`.jl`。动因是上一轮查出的另一半缺口：
契约门禁把那四类当受保护文件，扫描器却不读它们的内容，写进那四类文件里的密钥与动态执行等于不存在。

- 前后差实测（`forge-scratch/pool_probe.py`，同一组夹具跑两份实现）：改前读进 0 个文件、报 100/100 放行。
  改后读进 4 个，报 SEC-2 明文口令 1、SEC-3 eval 1、SEC-4 网络取回 2，总分 75/100 阻断。
- 自检加 3 条断言钉住，例数从 90 升到 93。控制 E1 把那四类退回池子外，自检立刻变红。
- 对本仓自己的分数没影响：`git ls-files` 里这四类文件是 0 个，真实扫描仍 95/100 放行、真盲区 0。
- 对用户的后果要说清：这是**变严**。别人仓库里那四类文件从此进内容扫描，图标 SVG 带外链脚本、
  `.htm` 里留 eval 都会开始报。`looks_minified` 与 `MAX_BYTES` 两道降噪还在，噪声可控但不为零。
- CODE_EXTS 上方注释跟着改口：两份名单从 2026-11-17 起对这四类一致，其余成员仍不等价。

### `_wait_health` 补用例，start 那条路不再是读证

上一轮记录写着「`cmd_start` 的 `_wait_health` 与 DETACHED 分支仍只有读证」。前半个补上了，走临时替换
`http_get` 的做法，`_wait_health` 本体一个字没改。三例：就绪要立刻返回 200。`timeout=0` 要一次都不探。
服务提前退出要把日志尾巴与退出码一起带出来，还要走 die。沙箱自检从 136 项升到 141 项，整套跑 0.8 秒。
控制 E2、E3、E4 分别破坏这三条，自检全部变红。

DETACHED 那半个仍没覆盖：它只在 Windows 真起进程时才走得到，CI 是 ubuntu，本机真机跑的是 `serve` 那条路。
补它要真起一次服务，属于运行时验证，不是自检能表达的形状。留着。

### `.m` 进池之后立刻查到的漏网：`@"..."` 字面量

`.m` 进池只是让人去看那些文件。真读一遍才发现取值的模式根本不认 Objective-C 与 C# 的 `@"..."`。
`NSString *token = @"真实口令"` 以前不报，因为值字符类在引号前先撞上 `@`，取到的值只剩一个字符，够不上
`MIN_SECRET_LEN`。修法是一个字符：值的引号位前加 `@?`。

- 前后实测（`secret_assignments` 单行）：`NSString *token = @"abcdefghijkl12"` 从 False 变 True。 <!-- forge-declared: SEC-2 理由=Obj-C 测试字面量，记录判据前后行为用，不是凭据 出处=AUDIT.md#sec-declared 复审=2026-12-15 -->
  `char *password = "..."` 本来就报，不受影响。短值 `@"sh"` 仍不报，没有把它放宽。
- 自检加 2 例（一真一假），例数从 93 升到 95。控制 E5 把 `@?` 撤掉，那条真例立刻变红。
- 本仓扫描不变：仍 SEC-1 1 处、SEC-2 0 处、95/100 放行，没带来新误报。
- 对下游同样是变严：Objective-C 与 C# 仓库里密钥名配 `@"..."` 的写法从此会被报出来。

### 宿主真在跑时的第三轮实测（2026-11-17 07:20，Windows，用户的 N.E.K.O 桌面端在线）

作者把宿主起好了。这一轮补两条以前只有读证的路，并且第一次在有真实第三方服务进程的机器上验守护。
先记一件事实：装好的宿主跑的是 `E:\Program Files\Neko\resources\bin\projectneko_server.exe`，
命令行里**没有** `user_plugin_server.py`。那个文件名只出现在源码 checkout 起的服务上。

1. **`start` 那条路（DETACHED 分支）第一次真跑。** 沙箱根 `forge-sandbox-start`，挂 `neko_host` 的
   `.venv`。15.5 秒就绪，`/health` 200，状态文件记 `mode=start` 与真服务 pid。
2. **坑 #4 的说法被实测改正。** 父命令退出以后，那个 detached 服务**还活着**，端口照样应答。
   旧句子写成「DETACHED_PROCESS 在 job object 下也救不了」，容易被读成「start 一律留不住进程」。
   真实边界是：普通父进程退出挡得住，带 job object 的受管任务挡不住。SKILL.md 第 4 坑已按这句改。
3. **`stop` 对 start 模式的服务。** 4.9 秒确认真的退出，删状态文件，事后 pid 查无、端口释放。
4. **守护对着无害进程。** 拿一个 `python -c "time.sleep(600)"` 冒充沙箱服务写进状态文件。
   `stop` 判成 pid 复用，不杀该进程，只清状态（1.0 秒）。再 `stop --force` 时状态文件已经没了，
   退 2 报「未找到 sandbox.json」。无害进程全程存活，最后由测试自己收尾。
5. **守护对着用户的真宿主。** 只读取数不动手：pid 71100（桌面端主服务）与 45924（其 fork 子进程）
   的判定都是 `False`，走向算出来是 `reuse`，带上 `force=True` 也还是 `reuse`。
   也就是说 `stop` 在这台机器上永远不会杀掉用户正在跑的 N.E.K.O。本轮没对那两个 pid 发过任何信号。

顺带核对：这一轮 `serve` 与 `start` 两种模式下，沙箱读到的都是上游内置插件 11 个、只有 `web_search`
在 running，用户已装插件没有被拉进来。宿主在线不影响隔离，因为数据根各自独立。

沙箱根 `forge-sandbox-start` 与 `forge-sandbox-refuse` 留着，随时可整个删。

## N.E.K.O 对齐轮（2026-11-17，上游 main `db81c1be0`，当天又前进到 `e14f61963`）

作者给的口径两句：warnings 当 errors，每一次都要跟 `Project-N-E-K-O/N.E.K.O` 对齐。
本轮把 CI 里三条「只报不拦」的步骤改成阻塞，并先把探针本身修到值得阻塞。

### CI 的三条 report-only 步骤改成阻塞

`--fail-unclear`、`--strict-placeholder`、`profile_drift.py --strict` 现在都会红。
三条在本机实跑退 0 / 0 / 0，所以改成阻塞不会天天假红。
探针取不到上游时退 2；CI 改成重试三次（15、30、45 秒），三次仍取不到才红。
限流与断网既不该冒充结论，也不该被当成核过。

### 探针补的判据（每条都有反向对照）

- 空行：锚点行落在空行上时，旧版因为「空行也算在定义体内」判成对得上。收紧后当场抓到 3 处假绿
  （`validate_cmd.py` 185→189、`plugin_toml_semantics.py` 130-140→136-150、`base.py` 116-123→118-121）。控制 H1、H2 变红。
- 1MB 以上的文件改走 raw 端点：Contents API 对 `plugin/settings.py` 返回 200 但不带正文，旧版算 unreachable。
  CI 一改成阻塞，这种文件会让探针天天红却什么都没说。控制 H3、H4、H5、H6 变红。
- 路由字面量不再当符号：`GET /plugin/metrics*` 里 IDENT_RE 会抓到 metrics，并把它安到前一个路径头上，
  于是报假漂移。控制 H7 变红。
- `check()` 也要能注入：以前自检只测 `_all_blank` 这类纯函数，接线断了照样绿。控制 H8 变红。
- `--strict` 现在把 weak 也算失败。「没有可比对的符号」是我们自己的写法问题，静默通过就是在门禁里留假绿。
- PATH_RE 补 `utils/` 与 `brain/`。**不要加 `scripts/`**：那是我们自己的仓路径，加进去之后探针把本文提到的
  `scripts/profile_drift.py` 当成上游锚点，报出一条假 404。本轮真踩过一次，已写进注释。
- 自检从 20 例升到 33 例。

### 对齐结果

- 锚点从 53 条涨到 71 条。全部在 `db81c1be0` 上逐条读对上，当天新 head `e14f61963` 复跑仍是 71 条、漂移 0。
- 改正文档 19 处。跑了一条 27 个 agent 的工作流（清点 → 逐条核对 → 对抗复核），0 处被反证推翻。
  行号要点：`http_app.py` 268→280，超时 raise `settings.py` 788-791→766-769，`market_bridge.py` 90→91 且四段各 +1，
  `generator.py` 824-836→859-871，`sdk-reference.md` 225→246 与 132/141→134/143，`settings.py` 555-557→533-536，
  `plugin_projects.py` 50→53，`host.py` 131-135→138-143，`query_service.py` 885→887-890 与 831→833-836，
  `plugins.py` 两个函数行 118→119 与 208→209。
- 事实改正两条。内置插件从 12 个变 11 个（`wechat_integration` 已移除），且 11 个全部显式写 `auto_start`，
  只有 `web_search` 是 `true`（`plugin/plugins/web_search/plugin.toml:23`）。
  `config_dir` 上游已改成 `plugin_dir` 的兼容别名，profile 里补了一句「别往里写」。
- 路由表整段重写：每个号都带全路径与函数名。裸文件名与光杆行号探针看不见，这一改让探针多核 18 条。
- sha 标记按硬判据改口 30 行：一行里探针能看到的每个锚点都在当前 main 上命中，才改那行的 `读于`。
  仍留 `acfbdbc50` 的锚点标记只剩第 68、75、148 三行，因为探针在那几行看不到锚点。

### 沙箱脚本的覆盖补齐

- `_pid_really_alive` 加注入接缝，补 12 条分支用例，含 Windows 那一支（`on_nt` 让 ubuntu 也能验它）。
- `cmd_stop` 加 pid 类型与取值校验。`int(1.5)` 会静默截成 1，然后拿 pid=1 去问操作系统；
  非数字 pid 以前一路撞到 `"%d"` 才抛 ValueError，整轮 stop 一句人话都没有。控制 E1-E8 全部变红。
- `_wait_health` 补三条：就绪立刻返回 200、`timeout=0` 一次都不探、服务提前退出要把日志尾巴与退出码带出来并走 die。
  第一条断言起初读 `str(SystemExit)`，读错了地方——die 把消息打到 stderr，异常只带退出码，改成捕 stderr。
- 沙箱自检 141 → 161 项，整套 0.8 秒，没有睡进 1 秒粒度。

### 能力刷新 hook

- 新增 49 项离线自检，CI 加一条阻塞步骤。这个脚本每个会话都跑，以前 CI 只做 `py_compile`，等于零覆盖。
- 删掉两处死代码：`build_context` 的 `truncated` 形参，以及 `main` 里没人读的 `json.loads`。
  stdin 仍然排空——宿主不关管道时不读就会挂。
- `build_context` 加 `read` / `exists` 注入接缝，自检因此不碰用户目录。自检只在系统临时目录建两个夹具
  测 `_read` 的 256KB 上限与 BOM，hook 的运行路径照旧一个文件都不写。这条例外写进 docstring 的「已知例外 2」。
- 真机跑一次（2026-11-17 08:05 前后）：输出合法单行 JSON，顶层只有 `hookSpecificOutput`，
  ctx 1281 字符，stderr 空，报「有变更（10 项）」。

### 仍没清掉的

- `session-refresh-sop.md` 第 98 行那句「解析 payload 字段」本轮已按代码改对（见下面两处文档-代码不符那一节）。
  第 162 行「输出「部分扫描」结果并标注结论非全量」与第 241 行那条引用它的告警行仍未改：
  代码走的是固定单行降级，不输出部分结果。那两处属于 §6 降级契约的对外披露文本，改它要走作者的窗口。
- §7.1 的 147、149、157、164，第 85 行的四个活动字段，第 101 行的常量块名，第 178 行的 docs 清单：
  探针看不见，本轮没逐条重读语义，引用前先重核。
- DETACHED 分支的实测只到注入那一层。真起进程时父任务被 job object 回收的那一半仍未量过。
- 沙箱根 `forge-sandbox-start` 与 `forge-sandbox-refuse` 留在本机，随时可整删。

### 坑 #4 的两半都量完了（2026-11-17 09:20，Windows）

上一轮我只量了「普通父命令退出」那一半。这一轮把另一半也量了：把 `serve` 交给受管后台任务，
等服务起来以后终止那个任务。

- 起服务：包装进程 64424（父是宿主），服务子进程 74768（父是包装进程），`sandbox.json` 记的正是 74768。
  uvicorn `Application startup complete`，`/health` 200。
- 终止受管任务：包装进程与服务子进程**一起消失**。进程表里查无 74768，43117 无应答，残留服务进程 0 个。
  这就是 `DETACHED_PROCESS` 挡不住 job object 那句话的实测依据。
- 状态文件留在了那里。紧接着跑一次 `stop`，它走的是 `gone` 分支：
  「pid=74768 的进程已不存在（服务已退出或中途崩了），只清理状态文件」，不发信号、不碰任何进程，删掉状态文件。
- 结论落到 SKILL.md 第 4 坑那一行：要常驻就 `serve` 交给受管任务；任务被终止就等于服务被回收，
  不需要额外收尾，下一次 `stop` 会自动把留下的状态清干净。

### §7.1 与 SOP 的两处文档-代码不符

- `profile-neko.md` §7.1 原来只用符号锚点，并写明「本节不写行号」。那句话让它整节落在探针之外：
  行号漂了没人知道。现在四个符号都带上了在 `e14f61963` 上核对过的行号（`export_package` def 435、
  体内 mkdir 441 与 `zipfile.ZipFile(..., compression=ZIP_DEFLATED)` 447；`class BuildResult` 277、
  `_validate_layout` 292；`install-plan` 路由 350、`install` 路由 327；`on_conflict` 300/438、
  `confirmation_token` 59/305、`must be inside` 210）。探针覆盖数因此从 71 涨到 75 条，全对。
  顺带改正一处引用不实：文中写的是 `zipfile.ZipFile(package_path, "w")`，上游那句带 `compression=zipfile.ZIP_DEFLATED`。
- `session-refresh-sop.md` 第 98 行写「读 stdin：解析 `session_id / source / hook_event_name`」。
  代码从不使用 payload 里的任何字段，本轮又把那份没人读的 `json.loads` 删了，于是把这句改成
  「读 stdin 并排空」，并写明读的唯一理由是不读会等 EOF 挂住。这不是改契约，是把一句写错的事实改对：
  降级路径与 exit 0 的纪律没动过。

### 「行号 64、95、182」这种写法也收进门禁（2026-11-17 10:20）

profile-neko.md 第 68 行原来写「`[FACT] decorators.py: …（裸行号 64、95、182 读于 acfbdbc50…）`」。
两处让它完全在门禁之外：路径是裸文件名（PATH_RE 要求白名单前缀），数字是顿号列（旧 NUM_PATTERNS 不认）。
`flip_strict.py` 当时报「探针看不见这一行」，就是这一类。

改了两侧：

- 文档侧：路径写全 `plugin/sdk/plugin/decorators.py`，符号按上游那一行的真名写 `kind: EntryKind`，
  数字改成「行号 64、95、182」与「行号 263、302、391」。语义没变：三处 kind 默认 action/action/custom，
  镜像三处仍是 action/custom。
- 探针侧：新增一种形状 `行号\s*(NNN、NNN…)`,  并带一个反面约束。文档里另有一句专门列
  「旧行号 1055-1150、1693-2049 已失效」，那是要失效的历史数字，一旦被当成锚点，探针会天天报假漂移。
  第一版用 `(?!\s*[-–])` 挡不住：`\d{1,5}` 会回溯成「105」绕过先行断言。现在改成 `(?![\d\-–])`，
  数字后面紧跟数字或连字符就不算，实测两组都按预期。

结果：探针覆盖 93 → 94 条，全对，weak 0。CI run 44001910294（提交 5f4e8d4）在冷缓存的 ubuntu runner 上核的正是这 94 条，那一步现在阻塞。自检 47 → 50 例。反向对照 H11（删掉新模式）变红，
整套 11 组全部变红。profile-neko.md 里剩下的 `acfbdbc50` 只有三处，全是历史叙述（第 13、15、27 行），
按规矩不改。

### 符号锚也进了门禁（2026-11-17 09:40，`e14f61963`）

profile 第 26 行定的规矩是「每条能力面结论带 `文件:符号名`，裸行号随上游 PR 漂移」。
写法没错，但探针以前只抽「带行号」的锚点，于是这类符号声明一条都不在门禁里。批评员数过：
光 profile-neko.md 就有约六十处探针看不见的锚点。

现在加了两件事：

- 区间行号也抽。`实测为 105-113 行` 这种写法以前整条抽不出来，因为数字前的连字符被 lookbehind 挡掉。
  不拆 `-` 的全局切分：文档里有一句专门写「旧行号 1055-1150、1693-2049 已失效」，
  那种历史数字一旦被当成锚点，探针会天天报假漂移。所以只新增「NNN-NNN 行」一个模式。
- 符号锚按存在性核。只认文档自己的两种写法（`文件: 符号` 与 `` 文件` 的 `符号` ``），散文里的词不算。
  点号形式（`TaskAgentManager._brief`）按每一段分别核，因为上游正文写的是类里 `def _brief`，
  没有那串点号。符号在上游文件里找不到就报漂移。

覆盖数因此从 75 条涨到 93 条（其中 18 条是符号锚），93 条全对、漂移 0、核不了 0。
自检从 33 例升到 47 例。反向对照 H7、H8、H9、H10 全部变红：区间模式删掉、冒号写法不认、
符号消失不报、collect 不产符号锚，四种破坏都被咬住。

第一遍实现时我用「整段里像代码的词」当符号，报出四条假漂移：`llm_tool`（文档明说它不在 decorators.py）、
`PluginManifestSchema`（文档说那个类「不叫」这个名字）、`latest_user_request`（挂在另一条链上）、
`input_schema`（同句里的下一个从句）。所以改成只按文档规定的锚点形状取符号，散文一律不看。
这条教训写在 `_anchor_symbol_span` 的 docstring 里。

第 75 行的 `acfbdbc50` 标记同步改口：符号存在性今天由探针核过，语义实读仍标注 2026-11-15 那一次。

### SOP 的 §6 措辞按代码改正（2026-11-17）

第 162 行原来写「超时后输出「部分扫描」结果并标注结论非全量」。代码不是这么做的：`main` 里
`truncated` 为真时直接 `emit(FALLBACK_CONTEXT)` 并返回，一句部分结果都不输出。
脚本头的「超时安全」那句本来就写着「超时固定降级输出，不输出部分结果」，所以文档与自己要核的代码两处对不上。

改的是第 162 行、第 241 行的告警字样，以及第 165 行补一句实测来源。
**降级、不阻塞会话、输出合法 JSON、`exit 0` 这四项契约没动**。
§6 属于对外披露文本，改它的措辞按 §8 那条规矩本该先开窗口；本轮是作者把「warnings 当 errors、
清除潜在风险」设成本轮目标之后做的，属于把一句写错的事实改对，不是重谈契约。
要还原只需 revert 那一行。

断言在哪：`session_capability_refresh.py --self-test` 里那条「截止时不输出部分结果」，
以及反向对照 F6（把降级分支改成永不触发）——自检立刻变红。

### 部署副本曾经悄悄落后 5 天

`~/.workbuddy/hooks/session_capability_refresh.py` 的 mtime 是 2026-11-15 09:30，与仓库副本 md5 不同。
也就是说这三天每个会话注入的那段能力底座文本，跑的不是仓库里这一份。`install_sync.py` 管不到这个路径，
它是第三份副本，只有手动替换才会跟上。

处置（2026-11-17 09:11）：先备份到 `session_capability_refresh.py.bak-20261117T091120`，再用仓库副本替换。
替换后 md5 与仓库一致。部署件自检 49 项通过。再按 SessionStart 的真实形态喂一个带
`source: "compact"` 的 payload 跑一次：退出码 0，stdout 一行合法 JSON，顶层只有 `hookSpecificOutput`，
stderr 空，ctx 1281 字符，报「有变更（10 项）」。

教训写在这里：说「三处装机面已同步」之前要先确认同步脚本覆盖的几个面。宿主 hook 那条路径不在覆盖面里，
它同样是副本，同样会落后。

### 仍交回作者的一项

`session_capability_refresh.py` 的两处死代码没动。改动本身各一行，但它牵连三件事：那个文件是每会话都跑的
SessionStart hook，没有自检，部署副本另在 `~/.workbuddy/hooks/`，而 SOP 第 98 行明写「读 stdin：解析
session_id / source / hook_event_name」。删掉解析就同时改了一条对外披露的降级契约，那要作者开窗口。

### 复跑（2026-11-17，本机）

六个文件 `py_compile` 全过。五个 `--self-test` 全绿（例数以实跑为准）。`roster --check` OK。
真实扫描仍 95/100 放行。四套反向对照 25 组全部变红：本轮 4 组、A464 收尾 6 组、精简轮 3 组、A571 的 12 组。

## 更正记录（2026-11-15）

- 提交 `d42b092` 正文有一句被 shell 打断：「探针确认 52 条锚点全部仍对得 afterwards 上」，读作「…全部仍对得上」。同一条命令还把 CI 探针步骤的 `run:` 改坏过，那处在 `f000526` 已修并有实测输出。正文不改写：改它要 force-push 两个已推送的提交，一句措辞不值这个代价，以本条为准。
- 提交 `0583635` 把上游 rbb 的规则形状转述错了两处，本版改正：三轮检索按**来源**分（GitHub 仓库与代码 / 包生态与清单 / 人已经提过吗），不是「功能原词→泛化词→场景词」那三层词，上游的关键词扩展是检索前的独立步骤；判决是 Join / Extend / Fork / Build 四档，先过可改性红黄绿硬闸（红不许 Join、黄只 Extend），不是三选一。依据：上游 `skills/reuse-before-build/SKILL.md` 第 45-49 行与第 65-74 行，读于 commit 4a14fbd（2026-11-15）。`SKILL.md` 铁律 3、`references/workflows.md` 1a 节、两个 agent 与 codex 提示词同步改正。
- 同一版还写了「本团不复读条文」，紧接着自己复读两条，自相矛盾。现在改成「执行要点摘要」，并给上游小节标题（Step 1 — Discover / Step 2 — Evaluate、zh-guide 第 1 步）与读到的 sha 作出处锚。
- 当时写「新增两条规则形状：离线降级…与小改动走上游快速档」。**只有快速档落地了**：离线降级在 `0583635`/`69cde01` 的任何操作性文档里都不存在，本行属于把计划写成结果。2026-11-15 由外部深审（报告 A464）指出，条文已补进 `references/workflows.md` 1a 节、SKILL 铁律 3、market-research 本职 4 与 codex 提示词。提交历史不改，以本节与下一节为准。
- 转述上游规则要连许可自述一起校：`LICENSE` 原写 `copies none of its text, rules or code`，而本版沿用上游的判决词与交付物标记 `不可复用原因 / Not reusable because:`，那句话已改成「不复制条文与代码，只复用判决词表与交付物标记」，不让它继续说过头。
- 探针与扫描器同时补了三个洞：`profile_drift.py` 把 404 与限流/断网分账（旧版全压成 None，一次限流会被读成「上游删了文件」）、当前 main 的 sha 缓存加 24 小时过期、行号形状加「带标签的 id 212、name 213」一类（覆盖由 52 条升到 53 条，实测无假漂移）；CI 给该步传 `GITHUB_TOKEN`，并把 `profile_drift.py` 加进 py_compile 与非网络自检。`security_audit.py` 把密钥材料文件（.pem .key .pfx .p12 .kdbx .ppk .jks .keystore 与 id_rsa .netrc .npmrc .pypirc 等）从「设计内排除」改判真盲区：旧版满是私钥的仓库也报「真盲区 0」。实测 fixture 一个只含 `srv.pem` 与 `cert.key` 的目录，现在报真盲区 2、加 `--fail-unclear` 退 3。
- 装机的 Codex 提示词曾经不在同步面里：`~/.codex/prompts/plugin-forge.md` 停在 2026-11-10，缺 Workflow G 风格段与整个 1a 步，而同一台机器上的 skill 树看着是新的。`pf-report/install_sync.py` 已加 `codex/prompts` 这一路（按文件名字面前缀取 plugin-forge*，整字节复制）。教训：说「三处副本已同步」之前要先确认同步脚本覆盖的面，覆盖不到的面不算同步过。

## 整合记录（2026-11-11，v2.0.0）

- 三团队并为一团：ops 12 角色与 court 朝议角色由 plugin-forge 14 人编制承接；`neko-plugin-ops/`、`neko-plugin-court/` 目录退役，其 avatars（14 张，无 EXIF）、intro.html（无脚本）、settings.json 一并移除——git 历史可找回。
- 平台资产保留：23 份 references → `skills/plugin-forge/references/neko/`；沙箱 skill → `skills/neko-plugin-sandbox/`；刷新脚本 → `scripts/`。
- 退役依据：court 的核心机制（门下省否决 + 审计留痕）已由 plugin-forge-contract 门禁 + 宪法承接，无功能损失。

## 例行安全审计（脚本化，2026-11-14 起）

- 规则来源：`skills/plugin-forge/references/security-checklist.md`——四项检查（SEC-1 路径范围 / SEC-2 明文密钥 / SEC-3 任意执行 / SEC-4 静默下载），每项 0–25 分；SEC-2 任一真实命中一票否决；总分 ≥90 放行、50–89 修完再放、其余阻断。
- 命令：`python skills/plugin-forge/scripts/security_audit.py <repo> [--allow 前缀,...] [--include-docs]`。`--allow` 只用于"已逐行复核且用途已声明"的文件，每条豁免在本节记依据。

本仓库首跑（2026-11-14，本机 Python 3.15；CI 用 3.11，被扫树 08e3c77。本节的分数是那次实跑的记录，换环境不保证复现，复核以重跑 `--json` 为准）：

| 跑法 | 范围 | 发现 | 得分 | 结论 |
|---|---|---|---|---|
| 分诊跑 | 无豁免，文件数看 `--json` 的 `scanned_files` | 分流见下 | 70（历史值） | 修完再放 |
| 复跑 | 同范围，豁免下表 3 项 | 0 处 | 95（2026-11-15 实测，见下） | 放行（exit 0） |
| 文档扫 | 加 `--include-docs`，同三条豁免 | SEC-3 6 处、SEC-4 22 处 | 60（历史值） | 见下方「文档扫扫的是缓存」一条 |

- 分诊跑 10 处发现全部落在两个文件：`skills/plugin-forge/scripts/security_audit.py`（正则定义与自述句——扫描器定义，非行为）；`skills/neko-plugin-sandbox/scripts/sandbox.py`（127.0.0.1 探活、pip 装依赖、git fetch——2026-11-11 已逐行复核，声明用途见上文与残留风险节）。即三条豁免的依据。SEC-1、SEC-2 零发现，全仓库无明文密钥与越界写入。
- 文档扫 28 处命中集中在审计与调研文档自身（security-checklist.md、neko 调研笔记、本文件、沙箱 SKILL.md），是规则描述与调研记录，不是可执行行为；SEC-4 的"已声明"判定以人工对照本文件为准，不靠脚本给满分。逐文件分布以重跑 `--json` 的输出为准，本节不再手抄。
- 盲区提醒：本节的 SEC-1 / SEC-2 数字出自放宽前的规则——旧 SEC-1 匹配不到反斜杠 `C:\Users\<名>` 路径，旧 SEC-2 匹配不到 JSON `"api_key": "..."` 与 `.env KEY=value`。放宽规则后复跑，本仓库结果不变（SEC-1 1 处、SEC-2 0 处、95/100、放行，实测）。
- 2026-11-14 二次复跑（SEC-2 覆盖面重做）：旧式 `` 在下划线处失配，`OPENAI_API_KEY=`、`DB_PASSWORD=`、`AWS_SECRET_ACCESS_KEY=`、`MY_SECRET=` 四种形状全部漏报。先改成只看字母边界并允许 `NAME_` 前缀与 `_WORD` 后缀段，复核又指出裸 token、驼峰、粘连名与 URL 内嵌凭据仍漏，且变量引用型右值会误报——一票否决项误报的代价比漏报更大。现最终形态是**按标识符拆词**判定：键名切 `_` `.` `-` 与驼峰后比对词表，右值是属性路径、全大写常量、类型标注、纯数字、URL、自引用或函数调用时排除。自检例数看 `--self-test` 输出（本文件不钉数字，见 A571 第 6 条）（含误报反例与盲区分类），CI 除自检外还对本仓库真跑一次扫描。本仓库仍是 95/100、SEC-2 0 处（实测）。
- 复扫触发：仓库脚本变更 ≥5 个文件、或新增外部内容入库时重跑，结果追加进本节。

### 2026-11-15 复跑（P4/P6/P7 之后）

- 命令：`python skills/plugin-forge/scripts/security_audit.py . --allow ` + 三条豁免（逗号分隔，`--allow` 现在也可重复传）。
- 实跑结果：SEC-1 1 处、SEC-2 0 处、SEC-3 0 处、SEC-4 0 处，总分 95/100，放行。
- 自检计数一律不抄：四个脚本各跑 `--self-test`，以实跑输出为准，本文件不钉数字。
  （旧版这里抄了 82 / 76 / 20 三个数，同文件另一处还留着更早的 75，两处互相打脸，
  而且原则就写在同一节里。2026-11-16 按 A571 第 6 条删掉钉死的数。）
- 文档扫的新账（2026-11-15）：`--include-docs` 会把 `scripts/.drift-cache/` 里缓存的上游源码一起扫，
  当时 194 处 SEC-4 命中里 163 处来自那堆缓存文本，分数量的是缓存不是本仓文档。
  **2026-11-17 已修**：`.drift-cache` 进 `SKIP_DIRS` 默认排除，扫描器自检钉住这条（撤掉就变红）。
  更正一句：那句话里「它是 CI 的 report-only 步骤」当时就不成立 —— `sync-check.yml` 从来没有过文档扫这一步，
  `--include-docs` 只在手工命令里出现过。本轮复跑（默认排除缓存后）：`--include-docs` 仍不干净，
  65 个文件里 SEC-1 6 / SEC-2 9 / SEC-3 12 / SEC-4 38，绝大多数是 AUDIT.md 与
  security-checklist.md 里引用的**示例文本**。所以这一步还是不接进 CI：接了就是把分诊器当裁判，
  而那 38 处要逐条人工判过才能豁免。缺口记在这里，不当已解决。
### sec-declared · 点位声明登记（2026-11-17，从 --json 的 declared_ok_list 实取）

共 33 处（去重后 24 行）。理由列就是标记里的 `理由=`，复审列就是 `复审=`。到期后声明自动失效，下一轮 CI 会把命中重新计回账上。

| 位置 | 理由 | 复审 |
|---|---|---|
| scripts/profile_drift.py:101 | 只读拉取 GitHub API 的 UA 头 | 2026-12-15 |
| scripts/profile_drift.py:102 | 只读拉取 api.github.com | 2026-12-15 |
| scripts/profile_drift.py:110 | 只读拉取 api.github.com | 2026-12-15 |
| scripts/profile_drift.py:165 | 只读拉取 raw.githubusercontent.com | 2026-12-15 |
| scripts/profile_drift.py:166 | 只读拉取 raw.githubusercontent.com | 2026-12-15 |
| scripts/profile_drift.py:174 | 只读拉取 raw.githubusercontent.com | 2026-12-15 |
| scripts/session_capability_refresh.py:46 | 按设计只读单机 CLI 配置做能力快照 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:36 | 健康探测用 urllib，只打 127.0.0.1 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:148 | 本地回环探测绕过系统代理 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:153 | 只读拉取 GitHub API | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:271 | git fetch 官方仓库到沙箱 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:673 | 读 /proc 的 pid 命令行 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:787 | 读 /proc 的 pid 状态 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:1046 | 自检夹具的假口令形状 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:1047 | 自检夹具的假口令形状 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:1048 | 自检夹具的假口令形状 | 2026-12-15 |
| skills/neko-plugin-sandbox/scripts/sandbox.py:1483 | 自检夹具里的越界 id | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:119 | 检测规则的正则字面量本身 | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:131 | 检测规则的正则字面量本身 | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:136 | 检测规则的正则字面量本身 | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:930 | 自检夹具里的网络字面量 | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:932 | 自检夹具里的网络字面量 | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:935 | 自检夹具里的网络字面量 | 2026-12-15 |
| skills/plugin-forge/scripts/security_audit.py:937 | 自检夹具里的网络字面量 | 2026-12-15 |

撤掉文件级 --allow 的那一轮（2026-11-17 第 4 项任务）：三条 --allow 全部取消，改为「能改代码消掉的消掉，消不掉的逐点声明」。消掉的方式：
- 检测规则自己的说明文字改成不会被自己命中的写法（`subprocess shell=True` → `子进程 shell 开关被打开`）
- 自检夹具里的触发词按本仓口径拆写（`"shel" + "l=True"`）
- 正则字面量躲不掉的，逐点声明并注明「检测规则的正则字面量本身」

裸扫结果（不加 --allow）：`SEC-1 0 / SEC-2 0 / SEC-3 0 / SEC-4 0`，总分 100/100，退出码 0；点位声明 33 处，畸形或过期声明 0 处。上面这些数字都以实跑为准，别抄进新结论。

## 例行复跑（2026-11-14，扫描器扩面后）

- 扫描器本轮三处变更：不再把行截断到 500 字符；无扩展名文件与 `.env*` 进扫描池；被跳过的条目写进 `skips` 清单并在结尾报数。前两条**扩大**发现面，所以旧记录的 100 分与本次不可直接对比。
- 复跑命令（沿用同三条豁免）：`python skills/plugin-forge/scripts/security_audit.py . --allow skills/plugin-forge/scripts/security_audit.py,skills/neko-plugin-sandbox/scripts/sandbox.py,scripts/profile_drift.py`
- 结果：SEC-1 1 处、SEC-2 / SEC-3 / SEC-4 各 0 处，总分 **95/100，放行**。SEC-5（不计分）本仓库 0 处。未扫描项数看 `--json` 的 `skipped_files`（2026-11-15 实测 84：49 个 .md 不在池内、29 个 .txt 缓存、4 个无扩展名、1 个 .llm、3 个豁免）——这些位置本次没有证据，不记为通过；完整清单看 `--json` 的 `skips` 字段。
- SEC-1 命中：`scripts/session_capability_refresh.py:43` `CODEBUDDY = os.path.expanduser("~/.codebuddy")`，规则「指向敏感家目录」。**依据**：该脚本按设计只读单机 CLI 配置做能力快照，不写、不删、不外传，输出侧另行脱敏。上一版没报这一条，是因为 `ROOT` 的 `~` 从未展开、脚本恒报「路径缺失」，而该规则要匹配 `expanduser(`；把路径修对之后规则才第一次真的看见它。**边界**：只读性质一旦改变（新增写入、上传或把内容拼进请求），本依据即刻失效，须重新逐行复核。
- 上一节的 100/100 保留，作为截断与扩展名过滤仍在时的历史值。

## 2026-11-17 轮（外部校验 A734 与终检 sweep 的落地记录）

一句话：这轮把「探针看不见的写法」和「自检跑不到的分支」两类缺口收掉，
其余是文档与机器状态的真话。全部改动都有反向对照，脚本在仓库外（`~/forge-scratch/run_controls*.py`）。

### 接进来的门禁

- **漂移探针扩面**：默认一次核对两份文档（`profile-neko.md` 与沙箱 `SKILL.md`）。
  新增判据「写法核不了」：行号前只写文件名或半截目录的锚点，直接阻塞。
  这一条的来源就是本轮缺陷本身——`config/network.py` 的端口行号被我写错过一次，当时门禁看不见。
  为什么不建裸文件名映射表：上游 5 个 `messages.py`、6 个 `query_service.py`，猜错会报「对得上」，比不检更坏。
- **扫描器池子补齐**：`.html` 从文档池移进代码池（门禁早就是当受保护代码，扫描器却不读）；
  两边同时补 `.vbs` `.ksh` `.mk`；`.drift-cache` 进 `SKIP_DIRS`。
- **门禁认带后缀的构建文件**：`Dockerfile.dev`、`Makefile.am` 以前两边都不管。
  反向用例钉住「`docs/notes.dockerfile` 仍算文档」，免得把讲 Dockerfile 的文章钉成代码。
- **自检真跑退出码**：`session_capability_refresh.py` 的 `--self-test` 以前包在 S10 兜底 try 里，
  脚本自己崩了也退 0。现在自检走那条 except 之外，红就是红。
- **`_kill_pid` 加注入点**（`nt` / `kill` / `call` / `wait`）：ubuntu CI 第一次能跑满 Windows 腿、
  SIGTERM/SIGKILL 与两处 `PermissionError` 共 8 组。
- **探针自检覆盖 `resolve_head` 与 `main()`**：缓存新鲜、过期重取、取不到 sha、
  以及 main 的 0/1/2 三种退出码（`main(argv=...)` 与 `resolve_head(cache_dir=, get=)` 就是为这个开的口）。

### 文档改口的地方（都带日期与出处）

- 沙箱 `SKILL.md`：契约表 10 格补齐符号名；`stop` 走向表按平台拆开写
  （POSIX 只发信号给主 pid，不收子进程；`--force` 补发 SIGKILL 只在 POSIX 有效），
  并新增 `PermissionError` 一行；历史行号（`268→280` 那类）改成散文，不再冒充锚点。
- `profile-neko.md` 文首：删掉「71 条」「还留 `acfbdbc50` 的三行」「探针看不见的那几组」——
  三句都已与实跑不符，现在只留一处能复现的口径，并明写「这几个数随文档变，跑命令自己取」。
- `README.md`：不再复述基线 sha（每轮都要跟着改，已经错过两次）。
- `capability-baseline.md` / `session-refresh-sop.md`：按今天实测改写四处——
  SessionStart 已挂载（本机 3 条之一就是我们这个脚本）、检测点 #4 不含 `pluginConfigs`、
  刷新命令只认仓库那份脚本（本机三份副本 sha256 互不相同，会话里跑的那份比仓库旧 8 小时）、
  幂等句的日期取脚本常量 `BASELINE_DATE`。今天的 10 项差异登记为新发现的未记账漂移，**没有**刷 `BASELINE`。
- `security-checklist.md`：SEC-2 的常量名单写全（不再数「几处」），分档补回 10 分与 3 处归零那两档，
  并改正「SEC-2 不参与总分」——`score()` 明明把它算进总分。

### 计数口径（A734 的 O4）

本文件里所有「N 例 / N 项」都是**当轮实跑的历史快照**，不是现值。现值只认命令：
`python <脚本> --self-test` 与 `python scripts/profile_drift.py --strict`。
以后新记录只写「本轮新增 X 组，实跑输出为准」，不再把某个数钉成事实。

### 本轮我自己造成的两次事故（都记下来）

1. **把 `sandbox.py` 写成 0 字节。** 一个补用例的脚本里写了 `io.open(P, "w", newline="\n")`：
   `open(..., "w")` 先把文件截断，`newline` 的校验才抛异常，于是内容没了。
   恢复路径：HEAD 版 + 本轮临时副本 → `git checkout HEAD -- 该文件` → `patch` 回 N2/O5 那段
   （补丁脚本 `~/forge-scratch/restore_n2o5.patch`）→ 再依次重跑 `_kill_pid` 注入点、
   `cmd_stop` 平台化提示、`kill_cases`、例数实算、`plugin-add/rm` 用例三张脚本。
   恢复后 `--self-test` 187 项通过，12 步 CI 等价流程全绿。
   **改法（已写进习惯）**：先在全内存里拼好整份内容，写 `路径+.tmp`，再 `os.replace`。
   这条本文件早就记过一次，这次是我自己没照做。
2. **反向对照脚本自己烂掉了。** `run_controls.py` 的 A1b、`run_controls4.py` 的 E1、
   `run_controls6.py` 的 G1 三处锚点在本轮改动后就再也匹配不上，那三组一条断言都没跑。
   这三张脚本本来就把「命中 0 次」计进失败并退 1（不是我写的宽松版），所以没有假绿；
   但连续几轮我只看「未变红 0 组」这一行，差点把 SKIP 当成通过。
   **改法**：三处锚点跟上现在的代码，重跑后 12 / 5 / 8 组全部真变红；
   以后每次动完代码都要重跑全部控制组，并且数一遍「组数 = 变红数 + 1×失败」，不只看尾巴那行。

### 收尾两笔（同一轮，实跑核对）

- `capability-baseline.md` 里「改完脚本要用 `install_sync.py --apply` 同步」这句是我自己写错的：
  该脚本只管 `.codex` / `.claude` / `.zcode` 三棵 skill 树，**不碰 `~/.workbuddy/hooks/` 挂载副本，
  也不碰 marketplace 安装态**。已按实测覆盖面改写，并给出量三处 sha256 的命令；那三个哈希值不写进正文。
- 扫描器自检补上「超过 2 MiB 的文件也要在未读账上点名」这一例（`_run(["--fail-unclear"])`）。
  例数 110 → 112，实跑通过。

## 2026-11-17 晚 · 「全部推进」第 1–2 条（验收命令进契约 / 干装载）

第 1 条 `verify_commands`（提交 2ee255f）
- schema 加必填字段；门禁逐条核命令可解析、引用文件真在；核不过对所有目标一致拒绝；
  缺字段的旧记录只 WARN（硬拒会把历史准奏全打断，这条取舍写在 schema 描述里）
- 三个自己踩到的坑：① `shlex` 在 Windows 上吃掉反斜杠，绝对路径全被判成「找不到」；
  ② 参数用「含点号」判文件会把 `--level 2.5` 误报成缺文件，改成后缀表；
  ③ 我在补文档的脚本里把 `rep()` 写成不接返回值，整段静默空转，
  靠「锚点必须命中 1 次」的断言与实跑暴露出来
- 反向对照 11 组（`~/forge-scratch/run_controls12.py`）全部咬住

第 2 条 `dry_load.py`（新脚本 640 行，含 25 例自检）
- 真装载调上游 `plugin/core/host.py:649` 的 `_import_plugin_module`，不自己模拟 import：
  模拟版会放行一些宿主其实装不进的插件
- 实测跑通：内置 `web_search` @ neko_host `b20a9f8` + 它的 .venv →
  模式 `real-import`，结果行 `symbol_found=true, symbol_type=type, is_class=true`，退 0
- 降级模式在输出里明写「证明不了能不能真的 import」与原因；宿主侧不可用不算插件问题
- 判据都核对过上游原文：目录名==id、`entry` 形状、`plugin.plugins.<id>` 包写法
  （内置 11 件全用它，`host.py:664` 要求第二段等于目录名）、类入口带 `@neko_plugin`
  （内置 11 件实测计数）、`auto_start` 缺省按 false（`registry.py:1293`）、
  `[plugin.host]` 已移除（`schema.py:18、233-235`）
- 真装载那条腿每轮自检都跑到：假 SDK 里放同签名 `_import_plugin_module`，
  覆盖 stderr 噪音、import 期抛异常、入口符号改名三种形状
- 反证 13 组（`~/forge-scratch/run_controls13.py`）10 项全部符合预期；
  其中「结果行退回取最后一行」这一项第一次没咬住——因为噪音当时只打 stdout，
  结果行本来就是最后一行。改成 stderr 噪音后才真钉住，这条教训记在这里
- CI 加两步：`dry_load --self-test`、`dry_load --demo-check`，都阻塞
- 扫描分数从 90 回到 95：新夹具里的 `sh -c` 字面量把自己报成 SEC-3，
  按本仓「夹具拆写」口径改掉，没有加第 4 条豁免


第 3 条 交付证据硬格式
- 规范句：`验收证据 = 命令 + 退出码 + 输出末 20 行；三者缺一按半道交付打回。`
- 唯一来源写在 `skills/plugin-forge/SKILL.md` §6；lead / docs / sandbox 人设与
  `delivery-self-check.md` 的 D2 `executed` 定义逐字引用那一句
- 检查接进 `generate_roster_tables.py --check`（它现在是仓库文档一致性门禁：表格、人数声明、交付口径）：
  缺一句报、同一句写两遍也报（写两遍时改口径只改一处，另一处变成假的「已对齐」）
- §6 原来那句「就三条 / 上面三条」改成不数条数——数过一次就要改三处，那几个数字已经错过
- 反向对照 `~/forge-scratch/run_controls14.py`：3 组加对照组全部符合预期

第 5 条 回归集 `tests/`（新增 37 例 pytest）
- 三个测试文件：`test_roster_docs_gate.py`（表格与人数声明边界、交付口径、main 接线）、
  `test_contract_gate.py`（scope/glob/过期/根外/宿主执行面/verify_commands/plan_sha256）、
  `test_dry_load.py`（九种坏形状 + 内置件的包写法 + 真装载那条腿）
- 唯一的第三方依赖是 pytest，且只用于开发：`requirements-dev.txt` + `pytest.ini`；
  产品脚本仍然零第三方依赖，README 的安装节写明这点
- 接线用例是新加的一类：以前所有测试都直接调函数，「函数还在、没人调用」这种断线它们全绿。
  现在有一条 subprocess 跑副本仓库 `--check`，专门盯「口径核对有没有被接进 main」
- 反向对照 `~/forge-scratch/run_controls16.py`：把九个历史 bug 一个个塞回代码，
  九组全部被 pytest 拦下（第一版漏了一组——`errors += check_report_formula()` 被短路时测试全绿，
  于是补了上面那条接线用例才拦住）

### 配对判据的第二版（终检 sweep 第 2 号的账）

上一轮我把配对符号「加进」候选列表，等于只放宽不收紧：把两个行号的符号互换，
每号的窗口里总能命中对方符号，照样报「对得上」。这次改成：

- 配上的词要**同时**满足两条才算这一号的符号：在该文件里真出现过（where_symbol 非空）、
  且以定义形状出现（`_definition_pattern`）
- 满足 → 这一号只认它（互换、张冠李戴都会红）；不满足 → 退回整条 tokens（散文词不误杀）
- 新判据一上，profile 自己先红了 4 处：那几行把「一串符号 + 一串号」并排列，
  号跟谁配全靠运气。已改成每个号紧跟自己的符号，并顺手把 `_set_declared_result_fields`
  与 `_router_entry_preview`、`__getattr__`/`__dir__`、`_find_available_port` 三个调用点、
  market_bridge 四段路由的号逐个对齐到 e14f61963 原文
- 已失效的旧号从锚点句里挪出去，单列成「只作历史」的一句，不再冒充声明
- 反向对照：`run_controls11.py` 第 1 项改成撤掉 hard 判据（自检必须红），
  新增第 12 项纯输入攻击——两个号互换，`--strict` 退 1；12 组 + 对照 8 步全部符合预期

### 工单 W01 + W02：大小写折叠误删与联接点盲区（本轮实跑复现后修掉）

- W01 复现（本机 NTFS，`wf-out/attackB_case.py`）：先装 `demo_plugin`，再装 `DEMO_PLUGIN`，
  两条命令都退 0，而 A 的 `aaa_secret_work.py` 没了；`plugin-rm --id DEMO_PLUGIN` 也退 0，
  把实际装着的那个删掉。根因是 `dest.exists()` 在折叠文件系统上把两个不同 id 当同一目录。
- 修法：`resolve_plugin_entry` 按目录里的真名解析，只差大小写就 die 并说明；
  折叠与否用**探测**（造一个目录再用别种大小写问它在不在），不信 `os.name`。
- W02 复现：`mklink /J` 造的 junction，`Path.is_symlink()` False、`os.path.islink` True、
  `exists()` False → add 撞 traceback、rm 谎报「不存在」，插件树里永留脏条目。
- 修法：`is_reparse_point` 读 `lstat` 的 `FILE_ATTRIBUTE_REPARSE_POINT(0x400)`；
  摘链接统一 `remove_link`（`unlink` 失败退到 `rmdir`）。悬空 junction 现在 rm 退 0 且真摘掉。
- 自检 +9 例（`_casefold_cases`、`_junction_cases`；非 Windows 或造不出 junction 时打印 SKIP）：
  沙箱自检 187 项 → 196 项，退出码 0
- 反向对照 `~/forge-scratch/run_controls17.py`：三臂行为验证（第二次装退 2 且 A 内容仍在、
  rm 打错大小写退 2 且插件仍在、悬空 junction 退 0 且已摘除）+ 两组撤修复（各自让自检变红）+
  对照组绿 —— 全部符合预期
- SKILL.md 的坑表补第 8、9 条；标题里的「七个」去掉，改成不数条数的说法

### 工单 W03：`_server_env` 就地污染宿主环境的回归，现在有断言拦了

- 复现（撤修复）：把 `env = os.environ.copy()` 改成 `env = os.environ`，旧自检 187 项照过；
  新自检报 `FAIL: _server_env 就地改了宿主的 os.environ（少了 4 项、变了 8 项）`，退出码 1 [实测]
- 进程内直验（臂3）：`SNAPSHOT_UNCHANGED True`、`OUT_OF_CHILD True`、`STILL_IN_HOST True` [实测]
  ——子进程环境里没带 planted token，宿主自己的环境一字未动，这才是「副本被清」的正确形状
- 死断言纠正：`ZZ_TEST_PWD_LIKE` 名字里有 PWD 词根，`env_is_secret` 实测回 True，
  旧用例却把它放进 keep 名单（那条断言永远不可能成立）。现在它进 drop 名单，
  名单退回旧写法时自检立刻变红（臂2）[实测]
- 沙箱自检 196 项 → 201 项，退出码 0；反向对照 `~/forge-scratch/run_controls18.py` 四臂全部符合预期
- 一处工具教训：反证脚本原本把「日志里出现 PermissionError」当失败信号——那是 kill 用例的预期输出。
  改成只认自检自己的 `FAIL:` 行与 SELF-TEST 汇总行

### 工单 W07：`_kill_pid` 的三个放走形状

- 三条都在自检里钉死了，撤一条就红（实跑三臂各退 1，理由逐条对得上）：
  M1 撤 `if not force` → `没带 --force 也发了 SIGKILL(9)`；
  M2 Windows 腿丢 `steps=` → `Windows 腿丢了 steps=(0.4, 1.0, 2.0)`；
  M3 `taskkill` 失败改回 `return True` → `taskkill 起不来时返回 True，应当 False`
- 顺带修一处真缺陷：`taskkill` 起不来（没装、被挡）原本把 `OSError` 冒到 `cmd_stop`，
  现在捕获、说明原因并回 `False`——既不谎报「已停止」也不崩
- 新增判据：`wait` 收到的 `timeout` 必须是 `KILL_WAIT`；POSIX 腿不许带 `steps`
- 沙箱自检 201 → 202 项；SKILL.md 走向表补一行「taskkill 起不来」并写明 SIGKILL 的两条形条件

### 工单 W04 + O2：监控器对「值」失明

- 旧自检只比键集合。把 `_ext_settings` 改成 `return {"hooks": {}, "enabledPlugins": {}}`
  （键全在、值抽干），66 项全绿，而「有人往 settings.json 塞新 SessionStart hook」
  这类漂移对监控完全失明——hook 集合正是这个监控器唯一要看的东西
- 补两条正证：抽取器整值比较（`_ext_settings 留两项且值原样带出`）；
  受控文件逐个改一次内容，对应指纹必须变（`值穿透: …`，共 5 条）
- O2 是同型的弱断言：`ctx2.count("异常详") <= 8` 在只列 3 条时也为真，等于没断言。
  改成整值断言，并把「条数上限」与「整段 2000 字上限」两条护栏拆成两个夹具
  （原来同一个夹具测两条，短异常那组被字数护栏抢先截断，计数根本测不到）
- 反证 19（`forge-scratch/run_controls19.py`）四臂实跑：
  臂1 抽干值 rc=1，红在 `_ext_settings 抽干值要能被发现` + `值穿透: 改 settings.json…`；
  臂2 marketplace 扫描恒量化 rc=1，红在 `值穿透: 改 marketplace 清单…`；
  臂3a `notes[:3]` 与臂3b `notes[:20]` 各 rc=1（两个方向都能咬，旧的 `<= 8` 只咬得住 3b）；
  臂4 未改动 rc=0
- 能力刷新自检 66 → 72 项，全绿

### 工单 W05：名单外扩展名里藏真凭据，扫描器与盲区账两边全瞎

- 实测形状（外部审 13-gate-pool §3，本轮在 `forge-scratch/w05-atk/` 重放）：
  `API_KEY = "ghp_…"` 写进 `settings.py.bak` 或 `report.wsf` → SEC-2=0、总分 100、`--fail-unclear` 也不响
- 两个原因分开修：
  ① 池子按字面扩展名判，`.bak` / `.wsf` 不在名单 → 文件整个不读。
     现在判池前先剥一层备份后缀（`BACKUP_EXTS`，只剥一层），并把 Windows 脚本宿主
     `.wsf .wsh .vbe`、服务端网页脚本 `.asp .aspx .jsp .jspx .cfm`、构建与配置载体
     `.xml .csproj` 补进代码池；`.csv .tsv .lock .sum .log .diff .patch` 补进配置池
  ② 「扩展名不在池」记成设计内排除 → 真盲区恒 0。现在只有 `ASSET_EXTS`（图片、字体、
     压缩包、二进制）与文档仍算设计内排除，**未分类扩展名改记真盲区**
- 口径转向：白名单永远可以漏下一个后缀。漏一个后缀的代价从「凭据被藏住」变成「CI 红一次」
- 反证 20 四臂（`forge-scratch/run_controls20.py`）：撤后缀剥离 / 撤 .wsf 家族 /
  未分类记回设计内 / 素材记成盲区，各退 1 且红在对应那条；对照组绿
- 本仓实测：`scanned 26 / skipped 58 / by_design 58 / unclear 0`，裸跑（无 `--allow`）
  `--fail-unclear --strict-placeholder --strict-declared` 仍 100/100 退 0
  （`references/neko/system.llm` 以前落「不在池」，现归 DOC_EXTS）
- 扫描器自检 126 → 130 例
- 未做：`--include-docs` 接 CI 仍在待办（文档里示例文本多，需逐条判）；
  攻击侧只补了证据里点名的家族，其余同类后缀按 13 §6 属 [未核实]，靠新的盲区账兜住

### 工单 W06（含 W18.2 那一格）：构建入口文件不要准奏，讲构建文件的文档别再误钉

- 攻击形状（证据 14，本机 GNU make 实跑 `GNUmakefile-won`）：`is_protected` 的
  `base = name.split(".")[0]` 只认 makefile/dockerfile/containerfile/justfile 四个词，
  `GNUmakefile` 拆出 `gnumakefile` 不命中 → 判成「非代码」→ ALLOW
- 修法只动判据顺序，不加新机制：整名名单 → 已知文档扩展名短路 → base 拆名
  - `BUILD_FILE_NAMES` 补 `gnumakefile / rakefile / sconstruct / sconscript / workspace`
  - 新增 `BUILD_FILE_WHOLE`（只认整名）：`cmakelists.txt / meson.build / build / build.bazel / workspace.bazel / .bazelrc`
    —— 这几个的点号后部分是文件名的一部分，塞进 base 名单会连 `build.log` 都要准奏
  - 新增 `DOC_EXTS` 短路：`docs/Makefile.md` 是散文不是构建入口（W18.2 反向用例，本轮一起收）
- 明写不覆盖（docstring 点名 + 自检用 `False` 期望钉住，不装全覆盖）：
  `meson_options.txt`、`settings.gradle` 这类 `*.gradle` 子文件、Gemfile、Podfile
- 反证 21（`forge-scratch/run_controls21.py`）六臂实跑：
  臂0 现码四个构建入口逐条 REFUSE、三个良性文件 ALLOW；
  臂0b 工单前的仓库版同一组输入 → 四个入口全 ALLOW 且 `docs/Makefile.md` 反倒 REFUSE（两个缺陷同时复现）；
  臂1 撤 `gnumakefile` / 臂2 撤文档短路 / 臂3 撤整名名单 / 臂4 把整名判据挪到文档短路之后
  —— 各退 1 且红在对应期望行；臂5 对照组绿
- 未实测项照原样标着：cmake / meson / rake / scons / bazel 本机都没装，
  「工具真读取并执行这些文件」是工具文档口径 [读证]，只有 GNU make 那条是本机 [实测]

### 探针补洞：冒号区间的尾号（外部审 40 号 FG-5、FG-4 复验）

- 形状表 `NUM_PATTERNS` 里区间只在「NNN-NNN 行」这一种写法下收两个号；
  `:14-36`、`:500-503`、`:253-256` 这类**冒号区间**只收头不收尾。
  实测：`window_nums("src/core/blobs.ts:14-36、:85")` 旧值 `[14, 85]`，尾号 36 掉线
  → 尾号怎么写都不会红。Cortico/TypeScript 正文就是这个写法，coopanion 侧要新增的
  B-2（`:14-36`）与 U-4/T-2 锚全部踩在这条上
- 修法：新增一条形状 `:(\d{1,5})\s*[-–]\s*(\d{1,5})(?![\d%])`，头尾都进号组，
  放在旧的单号冒号形状之前；不带冒号也不带「行」字的 `1055-1150、1693-2049`
  仍按原样不收（既有两用例钉住）
- 反向对照：把新形状删掉重跑自检 → 99 例里 6 例失败（4 条形状例 + `:2-5` 的号组 +
  `:2-5` 越界的 check() 判词）；补回来 100 例全过。
  同号组那条 `collect()` 用例不依赖新形状，它是去重键的钉子，不计进这 6 例
- 新增判据：`check()` 层一条端到端用例（`:2-5`，尾号落在夹具文件外 → 必须 drift）；
  `collect()` 层一条同号组用例（两行同 path 同 nums 不同符号 → 两条都要在账上，钉住去重键
  里的 line/syms/pairs 三项）
- 现网复跑：`--fresh --strict` → 锚点 129 条、漂移 0、写法核不了 0，退 0
  （判据变严没有把历史锚逼红）
- FG-1/FG-2（号配符号）上一轮已由 pairs + hard 规则封住，本轮 `swap_rc` 用例仍在红；
  FG-3（enclosing 顶格锚定对 TS 缩进方法不设边界）属 Python 面向的判据，
  TypeScript 侧要不要移植 = 49 号 W13，留给 云玩家

### 工单 W09：部署副本一致性现在有机器检查了

- 新脚本 `scripts/verify_installs.py`：字典 `PAIRS` 列「仓库文件 → 仓外真执行副本」，
  逐字节比 sha256，任一处不同或缺失就退 1 并打印两侧 sha 与路径
- 覆盖面只包含 `install_sync.py` 够不着的那一类：挂载态 SessionStart hook 与
  marketplace 安装态副本（共 2 处）。skill 树、agents、prompts 三面仍由 install_sync 按目录展开，
  本脚本不重复它的清单——重复两份清单就是两处各说各话
- 本机实跑 `--check`：2 处一致、0 处问题、退 0（今晚 21:48 那次再部署之后才一致；
  在那之前挂载态停在 `ad195c115825`，仓库已是 `3769e2347aa0`，正是 W09 说的复发漂移）
- CI 只跑 `--self-test`（runner 上没有这两个面），`py_compile` 清单同步加上这一份
- 自检 7 项 + 反证 24 六臂（`forge-scratch/run_controls24.py`）：
  臂1 造出「仓库改了、副本没 cp」的真实局面（源多一字节、副本用本机真副本）→ 报 2 处且两侧 sha 不同；
  臂2 撤「副本缺失也算红」→ 红；臂3 撤「源文件缺失也算红」→ 红；臂4 把字节比较短路成永真 → 三条判据一起红；
  臂5 对照组绿；臂5b 核 CI 里确有 `--self-test` 与 `py_compile` 两处接线
- 已知上限（写在文件头的 shortcut 里）：`PAIRS` 是硬清单，漏加一个新部署面就漏检一次。
  出现第三类需要按目录展开的副本时，改成读 install_sync 的清单

### JSON 解析步的覆盖面收窄（姊妹仓 CI 假红带出的本仓同型缺陷）

coopanion-forge 那边 `pnpm install` 进工作区以后，`JSON artifacts parse` 的递归 glob
开始解析第三方包的 JSONc，CI 退 1。本仓那一步是同一句 kernel 写法，同一个洞：
`glob.glob('**/*.json', recursive=True)` 不认 vendored 目录。本仓现在没有 npm 安装步，
所以没炸，属未爆的雷。

- 排除 vendored 目录，并且先归一化分隔符：第一版只判 `'/node_modules/' not in '/'+p`，
  Linux 上对、Windows 上 glob 给反斜杠就不认，同一判据两边结论相反
- 验收用 yml 里那一行原样命令，在 `forge-scratch/jsontest` 夹具上跑：
  本仓产物放坏 JSON 退 1；只留 `node_modules/tslib/package.json`（带注释）退 0

### 工单 W01 收尾：大小写冲突判据要看文件系统的真实行为（CI 在 Linux 上红过一次）

- 上一个版本把「只差大小写」直接判成冲突。Windows 上对：两种写法是同一个目录，
  rmtree 会抹掉另一个插件。Linux（CI 的 ubuntu-latest）上不折叠，那是两个合法目录，
  于是 plugin-add / plugin-rm 被误杀。CI run `44115796508` 实测：
  `FAIL: 大小写敏感的文件系统上却报了冲突`
- 修法：命中「只差大小写」以后再问两句——请求的名字在这台机器上真的存在吗（`isdir`），
  两个写法指向同一个目录吗（`samefile`）。任一为否就不是冲突
- 探测不动时按冲突算：宁可拒装，也不许把别人的工作内容 rmtree 掉。这条方向是安全优先，
  不是「保守放行」
- 自检加三条注入夹具（`_isdir` / `_samefile` 两个接缝），本机与 CI 各覆盖折叠与不折叠一条腿。
  沙箱自检 202 → 205 项
- 反证 25（`forge-scratch/run_controls25.py`）三臂：撤 `isdir` 守卫、撤 `samefile` 判别、
  探测失败改放行 —— 各退 1 且红在对应那条；对照组绿

### JSON 产物解析步的判据用错方言（姊妹仓 CI 红了三次才定位）

现象：coopanion-forge 的 `JSON artifacts parse` 连红三次，每次只给一句
`Expecting property name enclosed in double quotes: line 2 column 3`，不点名文件。
逐层排除后真凶是本仓那份 `sync-check.yml` 的同一条判据：三份模板的 `tsconfig.json`
是 JSONC（TypeScript 按带注释的 JSON 读它），本步却用严格 `json.load` 判它。
判据用错方言，不是产物坏了。

- 本步现在：排除 vendored 目录、排除 JSONC 家族、失败时逐个点名文件再退 1
- 分隔符先归一化。前版只认正斜杠：Linux 对，Windows 上 glob 给反斜杠就不认，同一条判据两边相反
- 反证（`forge-scratch/jsonneg/`）：夹具同时放真坏文件、vendored 包、JSONC 三种，
  输出只点名真坏那一个并退 1
- 两仓实跑：本仓 5 个产物退 0；姊妹仓 15 个退 0，CI run `44119780658` 与 `44120167080` 全绿
- 教训写进口径：门禁报错必须点名到文件。只给一行异常文本的判据，坏了三次也定位不到根因

### 假绿修复：dry_load 放过上游装不进的入口写法，夹具自己就是那种写法

触发点是我把强腿真跑起来（本机有 `D:/N.E.K.O/.venv`，以前只在 CI 上跑降级腿）。
好样例 `demo_ok` 用 `entry = \"main:DemoOkPlugin\"`，强腿直接报
`ModuleNotFoundError No module named 'main'`——**它在真宿主下根本装不进**，
而 `--demo-check` 一直退 0，因为 CI 没有宿主，只跑 AST 腿。

对上游 `plugin/core/host.py:649 _import_plugin_module` 实测（临时插件目录
`installs/plugins/demo_ok`，符号在 `__init__.py`；so = source_only）：

| 模块写法 | so=False | so=True |
|---|---|---|
| `plugin.plugins.demo_ok` | ModuleNotFoundError | OK，取到符号 |
| `plugins.demo_ok` | OK，取到符号 | OK，取到符号 |
| `main` | ModuleNotFoundError | ValueError「Source-only plugin module must match its source directory」 |

现场佐证：本机 23 个安装态插件与仓库内 11 个内置件**全部**用 `plugin.plugins.<目录名>:符号`，
没有一个用裸模块名。`neko-plugin-dev` 的脚手架也生成包写法。

落地的四处改动：

1. `check_manifest` 新增一条：入口模块路径不以 `plugin.plugins.` / `plugins.` 开头就报，
   理由带上面那张表的两种失败形状。旧说法「`main` 与包写法两种都合法」作废，
   那是我没实测就写进判据的话
2. 强腿按宿主的两档模式各试一次（`source_only=True` 先、False 后）。以前固定传 False，
   于是把出厂写法 `plugin.plugins.<id>` 全报成 import 失败——那是假红
3. 假上游（自检与 pytest 的夹具宿主）改成照上游规矩解析包名，不再用 `sys.path` 兜底；
   兜底就是当初放过裸模块名的原因
4. 夹具与用例换成包写法：`tests/fixtures/plugins/demo_ok`、`demo_broken` 的入口与源码位置都改，
   `tests/test_dry_load.py` 补 `bare_module_entry`、`pkg_prefix_mismatch`、
   `test_真装载对裸模块名写法必须报装不进` 三条

### 与上游对齐时新发现的规则（不是我这轮改的，是上游 main 新加的）

`plugin/core/host.py` 在 `04cc05709`（本机 checkout）与 main `e14f61963` 之间只差 4 处：
包名与目录名的比较换成了 `plugin_package_matches_directory`（`plugin/core/entry_points.py:47-62`）。
规矩是**单向**等价：目录可以 kebab（`my-cool-plugin`），包名必须 snake（`my_cool_plugin`），
因为包名是 Python 真要 import 的那一侧；反过来不成立。dry_load 以前按严格相等判，
会把 clone 出来的 kebab 目录判成装不进——假红。现在本仓接入同一个函数，
并在自检里钉住方向性（`(True, False, True, False)`）。
这条同时改了 `[plugin].id` 与目录名那条判据（上游 `host.py:533` 用的是同一个函数）。

验证：`dry_load.py --self-test` 26 → 29 例全过；`pytest -q` 39 项通过
（本机用 `neko_host/.venv` 的 python 跑，系统 python 没装 pytest）；
`--demo-check` 退 0（好样例通过、坏样例报 2 处）；
强腿用真宿主跑好样例：`模式：real-import`、`结论：通过`。
`profile_drift.py --strict`：锚点 129 → 131 条、漂移 0、核不了 0，退 0。
这一步当场又抓了我自己两次写锚点的错：先写成裸 `host.py:649`（探针不路由裸文件名），
再写成 `build.py:509、528` 而符号池被同段的 `plugins` 抢走——两条都按 SKILL §5 的
「path:NN 的 `SYMBOL`」写法改对才绿。判据在拦别人之前先拦了我，这正是它该有的样子。
### 工单 W10：探针「写法核不了」的四处漏检与一处误杀

四条都 still-open（`git log -S 'SKIP_MARKS'` 只命中 `b07e435`，没人修过）。判据与用例由
外部核对工作流产出（`forge-scratch/wf-out3/W10-探针漏检.md` + `apply_w10_patch.py`），
我在真仓里重跑全部判据后才落地。

- W10.1 豁免按整行生效 → 表格里一行挤多个声明时，「本机」静音同行另一句的上游裸名锚点。
  现在按小句生效，边界是 `CLAUSE_SEPS = 。；;|！？，,`（不收顿号，收了会把
  「未核验、仅示意」这类句首声明切弱）
- W10.2 `_local_names()` 按 basename 豁免，结果上游歧义度最高的两个名（技能说明 36 份、
  README 28 份）反而全豁免。现在加 `AMBIG_UPSTREAM` 下限清单：撞本仓文件名也照报；
  指认本仓文件要写仓内相对路径（路径存在即豁免）
- W10.3 `LOOSE_RE` 自带 lookahead 只认两种形状，而 `collect()` 认七种：省目录的写法
  两头隐身。现在删掉自带 lookahead，有没有行号统一问 `window_nums`，形状表单源
- W10.4 讲解坏写法的例句被报（误杀）。现在加显式标记 `drift-ignore`，只豁免自己那半句
- 自检 100 → 109 例。反向对照四臂（`wf-out3/controls_w10_run.py`）各撤一个零件，
  分别红在 #71 / #72+#79 / #74-77+#79+#82 / #78，全退 1
- 判据变严的账：现行两份文档重跑 `--strict` 仍 131 条全对、漂移 0、写法核不了 0，退 0
- 我自己的句子被新判据绊倒一次：写完「本文件 105、SKILL.md 26」这句，探针立刻报
  `写法核不了 SKILL.md:NN`——数字紧跟裸名正是它要拦的形状。改成「沙箱 SKILL 那份 26 条」才绿。
  这条记在这儿，因为它是「判据先拦自己人」的实例，不是巧合
- 待拍板（不改判据方向，只改维护方式）：`AMBIG_UPSTREAM` 是硬清单，上游新增重名会过期。
  另一条路是一次 git tree API 调用现算交集并缓存，代价是 `unresolvable()` 从纯文本函数
  变成要 head 的取数函数

### 工单 W11：名册人数门禁的四处逃逸、--write 带病退 0、越界改字节面

来源：外部核对报告 `forge-scratch/wf-out3/W11-名册门禁.md`（六子项，A1/A3/A5/BYTES 为
still-open，A2 与 LEDGER 判为「结构上堵不尽 / 要拍板」）。补丁我在真仓逐块落地，
验收与反向对照在 `forge-scratch/run_controls26.py` 自己重跑一遍才提交。

已修：

- **A1 后缀逃逸**：`COUNT_SCAN_SUFFIXES` 之外换 `.txt` 或干脆不带后缀，声明检查整段跳过。
  实测同内容 `.txt` 退 0、改成 `.md` 退 1。现在补 `.txt .rst .yml .yaml .toml` 并扫无后缀文件。
  不改成「凡文件都试解码」：脚本源码与自检用例里就有五种受管句式的样例句，全量扫会常红误报
- **A3 豁免用 endswith 尾巴**：`docs/evil-profile-dsh.md` 整文件免掉，改一个字母才报。
  换成精确相对路径 `skills/plugin-forge/references/profile-dsh.md`；四处装机面的真实路径
  都是这一个相对名，精确比对不伤任何一处真豁免
- **A5 `--write` 带病退 0**：stray 与空转进的是 `drift`，旧 `return 1 if args.check else 0`
  让 `--write` 修不了也退 0，还打印「已修复，请复查 git diff」——与文件头注释直接矛盾。
  现在抽成 `unfixable(drift)`，命中就退 1 并说明要人工处理；识别字样由自检钉住
- **BYTES 越界改字节**：`read()` 文本模式折行尾、`utf-8-sig` 剥 BOM 不补；实测一次 `--write`
  让 BOM 消失、1 行裸 LF 被并进 CRLF。现在 `read` 用 `newline=""`，`write` 检测 BOM 后按同编码写回，
  `sync_tables` 按标记区现状渲染行尾。报告作者在这块自己翻车两次（只改 read 不够、
  只改 read 不改 write 会二次翻译行尾），两块必须一起上——我按这个形状落的

验收（`run_controls26.py` 实跑）：B1 .txt 与无后缀种句退 1；B2 冒名尾巴文件退 1、
真豁免路径退 0；B3 stray 时 `--write` 退 1、不再出现「已修复」、stray 文件 sha 不变；
B4 BOM 保留、CRLF 74→74、裸 LF 1→1、变化的两行只差数字（README 本来就有两处声明，
所以判据写成「每一处变化都只动数字」，不是「只许变一行」）、再跑一次 sha 不变；
B5 干净副本 `--check` 0 且 `--self-test` PASS。
撤零件臂 S1-S5 各撤一块，五种退化全部复现（`.txt` 回到退 0、冒名文件回到退 0、
stray 回到「已修复」退 0、行尾被归一化、BOM 消失）。
自检新增 W11-A1 / A3 / A5 / 字节面四组判据。

未修，等拍板：

- **A2 措辞逃逸**：五种句式之外换个说法（「专家 9 名、合计 10 人」）就藏住了，`--check` 退 0。
  这是正则白名单的固有边界，打完本轮补丁再种同一句仍退 0（实测）。建议 SOP：roster 变更时
  在未列入 `COUNT_FILES` 的文件上跑一遍 `grep -rE "[0-9]+ +(名|位) +(专家|人设|编制)"`；
  或者立一条规范句「编制人数只许用五种受管句式书写」。两条都要 云玩家 定
- **LEDGER 历史账本**：新增带日期的账本文件会被报「名单外」，照提示补进 `COUNT_FILES` 以后
  `--write` 会把历史数字改成现值（等于造假）；补进 `COUNT_EXEMPT` 才安全，但错误文案没提这条路。
  要拍的是：给账本一类文件一个模式化豁免（例如 `docs/decision-log-*.md` 或首行标记），
  还是改错误文案指明「历史账本请进 COUNT_EXEMPT」

### 工单 W14：CI 第一次读文档，根因是右值字符类，不是文档写错了

来源：外部核对报告 `forge-scratch/wf-out3/W14-W17-文档面门禁.md`。现状实测：CI 那一步
`security_audit.py .` 不带 `--include-docs`，`.md` 一个字都不读；带上以后 SEC-2 报 6 处，
**6 处全是教学示例，没有一处真凭据**。

根因在扫描器，不在文档（这一点报告只说对一半，我按根因修）：
`secret_assignments` 的右值字符类只排 ASCII 空白与 `,;{}()`，反引号与中日韩标点都算值字符。
于是文档里 `` `max_tokens=MAX_TOKENS`）、类型标注 `` 的右值一路吃到下一个 ASCII 空格，
带着反引号与汉字，`CODE_VALUE_RE` 的全大写排除失效；同一行相邻的
`` `OPENAI_API_KEY=`、`DB_PASSWORD=`、… `` 被并成一个长值吞掉。
实测逐条：修复前 6 处，修完右值字符类（加 `、。「」（）…`、全角区、反引号）之后只剩 3 处，
那 3 处是字面量形状的教学示例，改走点位声明。

第二条根因是机制缺口：文档没有 `#` 注释语法，`DECLARE_BAD_RE` 只认 `#`，
等于**文档没有点位声明这条路**。不补它就只能改历史条目文字来过门禁，那是造假。
现在 `#` 与 `<!-- -->` 两种都认，`_declaration` 直接问出来的三态验过：
合法 HTML 声明 (True)、缺出处 (False, 格式不合)、没写 (None)。

落地四条：

1. `security_audit.py`：右值字符类加中文标点/全角/反引号截断点；`DECLARE_BAD_RE` 认 HTML 注释
2. 自检补两条：HTML 注释声明有效、缺出处的 HTML 声明判畸形（130 例仍全过）
3. 三处教学示例加行尾声明：`AUDIT.md:127`（记的就是这条误报本身）、`AUDIT.md:335`
   （Obj-C 测试字面量）、`security-checklist.md:65`（讲命中形状的示例）。
   历史条目一个字没改
4. CI 新增一步「文档面密钥门禁」：`--include-docs --json` 落 `$RUNNER_TEMP`，
   只判 `findings.SEC-2` 非空即阻塞，SEC-1/3/4 的文档命中打 INFO 不阻塞。
   这一步**不能吃扫描器退出码**：文档散文的 SEC-3/SEC-4 计数是 7 与 28，
   整份文档扫描本身就退 1，拿它当门禁等于永久钉红

反证 27 四臂（跑的是 yml 里那一段 shell 原样正文，`forge-scratch` 内联脚本）：
T1 现状 SEC-2 合计 0 → 绿；T2 文档种一行 `api_token = "ghp_…"` → 红（合计 2）；
T3 撤掉 `security-checklist.md:65` 那条声明 → 红（合计 1）；
T4 把判据读错类别（`SEC-9`）→ KeyError 退 1，不静默绿。

顺手修一处卫生问题：自检夹具的四行里，同一条点位声明被历史脚本重复追加了 4 次
（`grep -c 自检夹具里的网络字面量` 每行 4 次）。已收成一条；收完自检与裸扫都仍绿。

### 声明机制的第二层假红：讲这条语法的说明文字被当成畸形声明

W14 落地以后带 `--include-docs` 重扫，`declared_bad` 报 2 条，两条都在
`security-checklist.md` 讲语法的那两行（`# forge-declared: …` 出现在反引号里）。
新认 HTML 注释以后，「注释位置」这一条把说明文字也算成了声明尝试。
这是我自己引入的假红，判据必须再问一层：只有长得像真声明（带 `理由=` 或 `复审=`）
才算尝试。说明文字里没有这两段，因此不再被计。
真尝试仍算：HTML 注释里缺出处的声明照样判畸形（自检新增两条钉住这两侧）。
自检 134 → 136 例，裸扫 100/100，`--include-docs` 下 declared_bad 归 0。

### W17 两条判定：都已成立，不动文档

- 「门禁夹具自撞 SEC-3」已修：`sh -c 'rm -rf /'` 早按仓规拆写成
  `("s" + "h -c 'rm -rf /'", …)`（`:802`），裸扫代码面 100/100、SEC-3=0。
  `git log -S` 指向 `3fe6b0f`。不需要再动 `AUDIT_ALLOW`
- 「AUDIT 当日数字过期」判不需改：当前状态记录（`:634`、`:854`）与实跑吻合；
  其余 `95/100` 都在带日期的历史小节里，且 `:641` 自己写明保留作历史值。
  批量改等于抹掉溯源
- 报告另提一条低危（我没动，留给下一轮）：`AUDIT.md:175`/`:640` 写「三条豁免、CI 同口径」，
  而现行 CI 是裸扫无 `--allow`。「CI 同口径」这句现在对不上命令，
  要么限定成「历史上带 --allow 的口径」，要么删括注


### 工单 W15、W16、W18.1：接线、措辞、后缀翻转

**W15 闸门快照零接线（已修）。** 实测 `grep -c '闸门快照'`：`agents/` 14 个人设全为 0，
`references/workflows.md` 也是 0。`SKILL.md` §6 写了强制口径，人设与验收清单里却没有这一勾，
按人设行事的 lead 可以交付而不触发 §6。姊妹仓 COO 的 lead 早已接上同一句（`grep` 命中），
PF 这条线是断的。
修法接两条：`agents/plugin-forge-lead.md` 三段回奏之后加一行引用块，
`references/workflows.md` 验收清单加一条勾项。
边界守住两处：`SKILL.md:118`「delivery-self-check.md 是可选参考，不是接线闸门」与
`AUDIT.md:16`「来源确立前不得作为交付闸门接线」都逐字保留，补丁只接 §6 正文自己写的三条。
机器杆另加一条：`tests/test_roster_docs_gate.py` 断言这两处文字在位，撤掉就红，
免得接线又被无声改回去。

**W16 verify_commands 口径剩两处（已修）。** 代码行为实测三种输入：缺字段只 WARN 退 0；
命令引用不存在的文件 → 对所有目标一致拒绝退 1；命令真能解析 → ALLOW 退 0。
两处文字与行为不符：`workflows.md:21` 只写「命令坏 → 一致拒绝」，读起来像缺字段也拒绝；
`agents/plugin-forge-contract.md:38` 前半句说必填、后半句的机械校验列表又没有 `verify_commands`。
两处都补齐。行为本身已由自检钉住（缺字段分支改成报错，自检立刻红），所以这轮只动文字。

**W18.1 同一段教程 .md 放行 / .html 阻断（已修）。** 副本实测：同一段纯文档文字
（AWS 官方示例密钥 + 一处 `eval(` 讲解），`docs/tutor.md` → 100/100 退 0；
`docs/tutor.html` → SEC-2 1 处、SEC-3 2 处、80/100 阻断退 2。翻转在 HEAD 复现。
根因是 `.html` 在 CODE_EXTS 而 `.md` 不在池里，两种后缀按不同口径判同一段文字。

改法按路径豁免，不按内容猜：`docs/` 下的 `.html` 与 `.htm` 在默认口径与 `.md` 同进退
（不评分），带 `--include-docs` 时两种后缀都读、真凭据照旧一票否决。
不动的设计：非 `docs/` 目录的面板页（`ui/panel.html`）仍进 CODE_EXTS——
文件头那条「宿主真加载的页，门禁也当受保护文件」不许被这次豁免冲掉，
自检里专门留了一条反向用例盯它。
自检 136 → 139 例。反证 31 两臂：撤豁免 → `docs/tutor.html` 回到被评分退 1；
把豁免扩到全部 `.html` → 「八类扩展名应读进 8 个文件」与 `page.htm` 的 SEC-3 两条一起红。


### 工单 W19 十二条：文档欠报、自相矛盾、过期指认（逐条判完并落地）

来源：外部核对报告 `forge-scratch/wf-out3/W19-文档逐条.md`。12 条全部 still-open，
本轮逐条自己在现文上复验（不照抄报告的行号，报告给的 :47/:49 一类已漂，我按现文重定位）。

已落地：

- **W19.1** 沙箱 SKILL 不再抄环境变量名单，改指 `SECRET_ENV_RE` / `SECRET_ENV_SUB` / `KEEP_EXACT`，
  并点名两个真实后果：`WT_SESSION`、`SESSION_ID` 会被删，`SSH_AUTH_SOCK`、`KEYBOARD`、`KEYMAP`、裸 `PWD` 保留。
  实测旧文本比代码少 15 个词根与 3 个保留名（报告的差集脚本我重跑过）
- **W19.2** `capability-baseline.md:169` 说 install_sync「只管三棵 skill 树」→ 改成四面口径
  （skill 树两棵、`agents/*.md`、`codex/prompts/`），并注明判覆盖面看预演输出别看本文
- **W19.3** SOP 首段「当前不挂任何 SessionStart hook」是旧状态。现读 `~/.workbuddy/settings.json`
  的 `hooks.SessionStart` 有 3 条，第 3 条就是本工程挂载份；`~/.codebuddy` 侧 1 条未挂。
  首段改成「已挂载，追认或回滚都须单独显式同意」，并把「最后核验」两处换成 `<BASELINE_DATE>`
  （代码常量 `BASELINE_DATE = "2026-11-07"`，抄死日期就是下一次过期的源头）
- **W19.4** 三处 `continue:true` 断言与实现矛盾。真跑 stdin 读顶层键：只有 `hookSpecificOutput`；
  `emit()`（`scripts/session_capability_refresh.py:277-281`）不写 `continue`/`suppressOutput`/`systemMessage`。
  三处都按实测改写。`:35` 那一行是官方文档结构表的引用，保留
- **W19.5** 三处仍指 marketplace 安装态：`--regen` 与 `py_compile` 改指挂载份 `~/.workbuddy/hooks/…`，
  与 `capability-baseline.md:150-152`「只认仓库份」不再打架
- **W19.6** 人设裸相对路径实测 **8 处**（报告说 6 处，我自己扫出另两处：contract:38 的前缀写在路径之后
  机器读不出、lead:111 是我本轮加 §6 接线时新引入的）。全部补 `${FORGE_HOME}` 前缀
- **W19.7** docs 人设删掉写死的 `$HOME/.claude/skills/asd-ste100/...`，改「路径随宿主与安装位置变」，
  保留没装就按清单自查的降级条款
- **W19.8** 安全人设补「先跑机械分诊再人工复核」一步，命令与三个严格档旗标写全
- **W19.9** NOTICE 的「every entry … in AUDIT.md」按实测改成按族落点（edict / mattpocock 三件 /
  N.E.K.O Apache-2.0 在 AUDIT.md 里确实零命中）。选择改承诺而不是补三行摘要：
  把别人文件的摘要当审计结论落盘，是第二次错
- **W19.10** README 目录树补 `.claude-plugin/`、`tests/`、`pytest.ini`、`requirements-dev.txt`
  （`tests` 那一族是 `468ec14` 加回归集时漏的）
- **W19.11** `profile-neko.md:195` 的「发布检查清单现落点待核」换成实锚：
  `docs/plugins/cli.md:309` 的 `One-page checklist` 与 `docs/plugins/best-practices.md:220` 的
  `Plugin checklist`。这两条新锚由 `profile_drift --strict` 对当前 main 核过
- **W19.12** 刷新脚本两处「不读用户目录」的口径与实测相反：`open()` 探针记到 3 个真实路径
  （`~/.workbuddy/mcp.json`、`~/.workbuddy/settings.json`、安装态 `plugin.json`），
  来源是 S10 那段跑 `main()` 时 `build_context` 用真实 ROOT。改成如实写「只读、不写」

机器杆 `tests/test_docs_vs_code.py` 四条钉住 W19.1 / 19.9 / 19.10 / 19.12 这四类。
反证 32：把四处修法分别撤回去，四条断言各自变红。pytest 从 43 项涨到 47 项。
两条判据细节要说清：README 树判据不能按「含 roster.json」筛代码块，文末讲回归集那块也有
`roster.json` 与 `agents`，筛错会把一切都判成缺；`profile-neko.md` 的新句子里
「（2026-11-18 实读）」这种括号紧跟数字的写法会被 `window_nums` 认成行号，
`--strict` 当场报「2026 超出该文件行数」——判据先拦了我自己写的句子。

未落地两条：W19.6 与 W19.8 属人设文字，机器杆已有（`tests/test_agents_wiring.py`）；
W12 与 W13 那三条指纹漂移与 6 条悬空 hook 注册仍在等你拍板，见下面「仍未处理」。


### 工单 W12 选 B：判据从「写死 8 条路径」换成「扫注册表查悬空」

用户 2026-11-18 选了 B（承认 Hindsight 离场，删悬空注册），不是 A（重装）。
落地的不是删注册本身——`settings.json` 属用户域，执行器交给用户跑。

判据改动（`scripts/session_capability_refresh.py`）：

- `HOOK_SCRIPTS` 从 8 条缩到 1 条。剩下 7 条属于外部组件 Hindsight，
  整目录 `~/.hindsight/coding-agents` 实测不存在（`os.path.isdir` 为假）。
  把它们列在本工程名单里，等于每轮报「Hook 脚本缺失 7 条」却永远修不掉，
  还把别人的缺席报成本工程缺陷
- 新增 `dangling_hooks(settings, exists)`：直接读两根 `settings.json` 的
  `hooks.<event>[].hooks[].command`，把里面的脚本路径归一化反斜杠后问在不在。
  实测现机器出 6 条：workbuddy 3 条 + codebuddy 3 条，全是 hindsight 的 dist/*.js。
  这才是「每次会话三声 node 报错」的正面观测
- `HOOK_TARGET_RE` 的字符类不含反斜杠，所以匹配前必须把命令串里的 `\` 换成 `/`。
  第一版没换，报出来的行只剩最后一段文件名，读者没法对照 `settings.json` 删哪一条。
  这条是反证 33 第三臂「没咬住」暴露的——夹具只喂了正斜杠形状，等于没测 Windows
- `_read` 的 256 KiB 上限换成 `MAX_READ_BYTES = 8 MiB`。实测
  `workbuddy-connector-plugins-official/.codebuddy-plugin/marketplace.json` 是
  1 498 928 B、964 项，被旧上限判成 `<UNREADABLE>`，于是差异行报的是「读不动」
  而不是真实增长 350 → 964。自检加一条 1.5 MB 量级的夹具钉住
- `_path()`：路径行的消毒只去控制字符（Cc/Cf），保留 `/`、`:`、空格。
  `_safe` 会把分隔符换成下划线，报出来的路径读起来像乱码，
  而这一行正是要人拿去删注册的
- 自检 76 → 79 项。反证 33 五臂：撤悬空检测、撤上限、撤反斜杠归一化、
  把 hindsight 写回名单、对照组，全部符合预期
- 文档同轮改三处：`capability-baseline.md` 检查项 #10 与现值更正段、
  `session-refresh-sop.md` §2 第 4 步与 §5 报警条件

执行器（用户跑）：`python -I C:/Users/54301/pf-report/w12_remove_dangling_hooks.py`
预演已实跑：悬空 6 条、在位 2 条（haloflux-lab 的 session-beacon.mjs 与本工程挂载份），
加 `--apply` 才写盘，写盘前两份 settings.json 备份到 `pf-report/settings-backup-<时间戳>`。
硬约束照写进代码：只删不挂，绝不往 `~/.codebuddy` 加注册。

W13 的前置就是这条：B 案落地后 `settings.workbuddy` 与 `mcp.workbuddy` 指纹会再变一次，
BASELINE 等那时一起刷（用户口径）。


### A791 的 CRITICAL 修掉：点位声明的出处现在要核对真记录

外部增量深审（`Downloads/plugin-forge_11-17_晚更新深审_增量-A791.md`）报了一条 CRITICAL。
我自己在临时仓复现了：

```
被审目录只有一个 plugin.py，第 2 行是真 token 形状，行尾自己写一条格式合法的声明
python -I skills/plugin-forge/scripts/security_audit.py <该目录>
→ SEC-2：0 处（25 分）｜总分=95/100｜结论: 放行｜rc=0
```

一票否决被被审对象自己的一行注释抹掉了。根因：`_declaration` 只校验出处字符串里含 `AUDIT.md`
这个文件名，从不核对那本账里真写着这条记录；CLI 又总是把 `declared` 传进 `scan`，
没有「不信声明」的开关。

修法（方向 fail-closed）：

- 新增 `citation_ok(root, line, cache)`：按声明里的 `出处=<文件>#<锚点>` 去被审仓库读那本账，
  锚点要以整词形式出现才算数。文件读不到、锚点查无 → 当没有声明，命中回到账上
- 该情形同时记进 `declared["bad"]`，理由写「出处在被审仓库里查无记录」，
  `--strict-declared` 那一步会把它变成可见的问题，而不是静默降回无豁免
- 每轮扫描重置 `declared_cache`，跨仓不复用账本文本
- 自检 139 → 141 例：夹具仓库现在带自己的 `AUDIT.md`，并加一条反向用例——
  把账本里的锚点抹掉，同一声明必须失效，SEC-2 与 SEC-4 的命中都要回来
- 本仓自扫不受影响：35 处声明仍被认（`出处=AUDIT.md#sec-declared`，锚点在 AUDIT.md 里真存在），
  裸扫 100/100、三个严格档退 0

没做的一条要说清：A791 另建议「SEC-2/SEC-3 的声明一律进待人工账并让退出码非 0」或
「审外部仓时默认不认声明」。我选了核对出处这一条，因为它不新增 CLI 开关、
不改变自扫行为，又能让被审方无法自封豁免。若 云玩家 更想要「外部仓一律不信声明」，
那是另一个口径，改起来是一个默认值的差别。

### A791 第 03 组与 R1：dry_load 三条缺陷、声明空锚点残留、以及一条我自己踩到的编码坑

外部复验工作流（6 组里 5 组成文，`gate-commands` 那组因 API 订阅中断没跑完，它的两条我另外处理）
给了 paste-ready 补丁。每条我都自己在临时副本重跑过验收与反向对照，再落地。

**R1（空锚点声明仍然豁免）**：上一轮 `citation_ok` 我写成「不带锚点就当通过」，
留了一个洞：被审仓只要在行尾写 `出处=AUDIT.md`（不带 `#锚点`）并放一本非空 AUDIT.md 就豁免。
实测：收紧前 rc=0，收紧后 rc=2。全仓扫过，PF 51 处、COO 32 处声明全都带锚点，收紧零回归。
自检加一条断言钉住（142 例）。反向对照：撤回 `return False`，外部仓回到 rc=0 且自检红。

**dry#1（fail-open 静默 + HOSTED_BY 留值）**：`_isolated_env` 里
`from sandbox import env_is_secret` 失败被 `except Exception: pass` 吞掉，
凭据过滤整段跳过、一声不出。workshop 只拉 `dry_load.py` 不拉 `sandbox.py` 时就是这个形状。
现在出声，并把 `NEKO_PLUGIN_HOSTED_BY_AGENT` 改成 `pop`，与 `sandbox.py:581` 同口径
（上游已无读取点，留一个路径值只会在宿主日志里注入假的「嵌入式」身份）。

**dry#2（降级当通过）**：`TimeoutExpired` 是 `SubprocessError` 的子类，以前一律归成「环境缺」，
于是 import 期死循环的插件得到「结论：通过」退 0。现在超时单列成失败进 problems；
结论行按 `mode` 区分「通过」与「降级通过」，键取 mode 不取 degrade_reason，
因为没沙箱的常规降级没有 degrade_reason 也一样不许冒充真装载过。
模块 docstring 第 26 行早写了会明写降级，这两条让代码追上文档。

**dry#3（TOML 段头两处不一致）**：`[plugin]  # 说明` 因 `endswith("]")` 不成立而认不到段头，
而扫描器侧的 `plugin_toml_keys` 认得；`toml_has_section` 用子串匹配，缩进的
`  [plugin.host]` 漏。两处判据不一致会出假红。改法不跨 skill 目录 import
（dry_load 要能单文件拉取、零第三方依赖），只把口径对齐。
反向对照三条各自撤回旧写法，自检都红。

**我自己写测试时抓到的第四条**：给 3c 加断言那句（`id = \"a#b\"`）当场红了。
根因不在补丁说明里，在旧代码：`v.split(\"#\")[0]` 无条件剥注释，
把引号里的 `#` 也切掉，与上游 tomllib 的读法不一致。改成只在引号外剥。
这条是「写判据先拦自己」的第二次实例（前一次是 SKILL 锚点被探针拦）。

**顺带一条真缺陷（编码）**：警告本来就在打印，但 Windows 子进程的 stderr 默认本机代码页，
我的验证脚本按 UTF-8 解码，报出乱码，看起来像「警告没出声」。
两处都改：`main()` 里 stdout 旁边加 stderr 的 reconfigure；
`_isolated_env` 的警告路径自己先把 stderr 转 UTF-8，因为它经常在 `main()` 之外被调用。
验证脚本也改成按字节解码再 replace。

验收：dry_load 自检 29 → 33 例；扫描器自检 141 → 142 例；`--demo-check` 退 0；
pytest 47 项；裸扫 100/100；反证 34 九臂全部符合预期（`forge-scratch/run_controls34.py`）。

R2（被审方自带整本假账）没修：那是「信不信被审仓的账」的信任模型问题，
候选方案与代价写在复验报告与下面「仍未处理」。

### A791 复验工作流的结果落地：杀错进程、版本化名误拒、装机面漏装 dry_load

工作流 `a791-claim-verification`（7 代理、369 次工具调用、50 分钟）逐条复验 A791。
一组（契约门禁的两条）因 API 订阅中断没跑完，那两条我自己复现并修了。
落地四条：

1. **`pid_verdict` 只认脚本名，会杀掉用户本机真 N.E.K.O**（A791 标「NORMAL，可能」，
   复验实测坐实）。`sandbox.py` 现在要求命令行同时含状态文件里登记的 repo 路径；
   登记缺 repo 的老状态才退回只认脚本名。自检 205 → 208 项，新增三条形状：
   同名但 repo 不符必须 False、沙箱自己的路径 True、无 repo 的老登记 True。
   反向对照：把 repo 判别短路成永真，自检立刻红在「得到 True 期望 False」。
2. **`python3.11 -m pytest` 这类版本化程序名被误拒**（假红，我自己复现）：
   `command_problem` 用 `"." in last` 认路径，于是 `python3.11`、`pip3.12`、`node.exe`
   全被判成「命令指向的文件不在工作区」。现在只有带分隔符或 `./` 开头的才算路径，
   其余交给 `shutil.which`。自检加三条钉住方向：不许再出现「不在工作区」这个理由。
3. **装机面漏装整个 `dry_load.py`**（这是最实在的一条）：脚本进了仓库、进了 CI、
   进了人设指令，三棵宿主树里却都没有它——因为 `install_sync.py` 只在有人手动跑它时
   才对比。修法两层：`verify_installs.py` 加 `--trees`，把两棵 skill 树按文件逐个比
   （实测抓到 3 处漂移、复装后 116 处一致）；反向对照藏掉一个文件，检查退 1 并点名。
   自检 7 → 9 项。宿主清单 `HOSTS` 三家都在。
4. **`${FORGE_HOME}/../neko-plugin-sandbox/…` 这种路径写法**：装出来带 `..`，
   读的人要先心算一层。安装器加了第二个占位符 `${SANDBOX_HOME}`，
   文档与人设改用它；三棵宿主重装后实测路径存在。
   `SKILL.md` §6 的路径约定同时写明两棵树是隔壁关系。

另补一条口径：契约门禁**只验形状不执行**。`SKILL.md` 与 `task-size-routing.md` 原话
读起来像机器验过验收命令，实际它不跑（跑就是执行别人的代码）。两处都补上「只验形状不执行」，
并写明交付证据仍要按 §6 贴原生输出。`tests/test_docs_vs_code.py` 里那条近乎恒真的断言
（查两个常见词）改成点名具体文件与测量手段。

复验还留下两条没动的事实，见「仍未处理」：R2（被审方自带整本假 AUDIT.md）
与 06 组的发布阻断项。

### A791 四条收口：豁免信道分离、fail-closed、复发探针、rbb 升版

按下发顺序做，一条一个提交（`8871607` / `92e260f` / `af4f598` / `c051176`）。
每条都本地跑过验证与至少一次反向对照，再 commit。

**第 1 条 SEC-2 自豁免（CRITICAL）**：洞的形状是——被审仓在真 token 那行末尾挂一条
格式合法的 `forge-declared: SEC-2 …`，仓库里再放一本含该锚点的 `AUDIT.md`，旧判据 rc=0。
上一轮 R1 只堵了「空锚点」，锚点仍由被审方自己写，「出处真存在」等于「被审方自己写过这句话」。

改法选了「豁免依据移到审核方控制的清单」这条路，并且把行尾注释的信道也按「谁在审谁」切开：

- `_is_self_repo(root)`：判据不看路径长相，看被审仓里那份 `security_audit.py` 的 realpath
  是不是正在跑的这个文件。外部仓抄一份同名脚本进来也换不到信任。
- 本仓自扫：行尾声明照旧生效。实测 35 处声明、0 处畸形，零回归。
- 审外部仓：行尾声明一律不生效，只记一条「需审核方清单」。
- 新增 `--declared-list`：仓库外 JSON，条目按 `file + sec + sha256(line.strip())` 绑定，
  必填 `reviewer` 与 `expires`；缺一段、哈希截断、或已过期的条目整条丢弃，不猜不降级。

验收：`tests/fixtures/ext_repo_selfexempt/` 自带 `AUDIT.md` 锚点 + 合法行尾声明，
裸扫 `rc=2`、`declared_ok=0`；清单命中同一行才 `rc=0`；哈希不符仍 `rc=2`；缺字段不生效。
反向对照：把 `_is_self_repo` 短路成 `return True`，三条用例立刻红（假凭据被放行 `rc=0`），
恢复后 5 例全绿。

这条同时把上一轮没修的 R2 收口了：外部仓现在完全不读仓内任何账本，
「自带整本假 AUDIT.md」不再是一条通道。

**第 2 条③ dry_load 凭据过滤 fail-closed**：`_isolated_env` 里
`from sandbox import env_is_secret` 失败时上一轮只是「出声」，然后照跑——
子进程继承宿主全部环境变量，结论仍写「通过」。出声不等于设防。
现在抛 `EnvBlocked`：不起子进程、退出码 2、`mode=blocked`，结论行写
「环境阻断：这一趟没核过，不算通过」。既不走降级（降级是「没沙箱」，这是「没防线」），
也不进 problems 之外的计分路径。

**第 2 条①② 与第 3 条 复发探针进回归集**：上一轮把两处判据修了，但只有脚本自检与人工复现，
pytest 里一条能复发的都没有。新增：

| 文件 | 钉住什么 |
|---|---|
| `tests/test_security_audit.py`（5 例） | 外部仓不许自免；清单是唯一信道；哈希/字段缺失即失效；本仓 35 处声明零回归 |
| `tests/test_dry_load.py`（3 例） | 判据缺席要停跑；降级结论必须写「降级通过」；超时单列失败（打桩抛 `TimeoutExpired`，不真等 180 秒） |
| `tests/test_contract_gate.py`（1 例） | `python3.11 -m pytest` 不许被当工作区文件；exe 放宽不等于参数放宽 |
| `tests/test_docs_vs_code.py`（1 例） | rbb 钉版四处必须一致（反向验证：单独改 NOTICE 立刻红并报出两组值） |

pytest 47 → 58。CI 的 pytest 步骤改名点明覆盖哪三个文件，保持阻塞。

**第 4 条 rbb 升 0.3.22 / a375629**：上游 0.3.21→0.3.22 只有一个提交、9 个文件，
内容是删掉 `manifest_settings()` / `PERMISSIVE` / `python_exe` 三个公开面。
本仓 grep 三项零命中，对 rbb 只有文档级指向，无 import 无调用，所以没有需要适配的接口。
规则本体（三轮检索、四档判决、可改性硬闸）未变，摘要口径不重写。
四处版本号同步，并加了上面那条一致性机器杆。

**本轮实测**：pytest 58 passed；`security_audit` 自检 146 例 PASS、裸扫 32 文件
100/100 退 0、35 处声明 0 畸形；`dry_load` 自检 33 例 PASS、`--demo-check` 一好一坏退 0；
`check_contract_approval` 自检 PASS；`--include-docs` 面 SEC-2 = 0。

**还没做的两条，不当已解决**：
- `--declared-list` 的清单本身没有密码学签名，只有内容哈希。它能防「被审方改行后仍用旧豁免」，
  不能防「审核方自己的清单被换掉」——后者要靠清单文件的存放位置与 Git 历史来保证，
  是流程约束不是机器约束。
- `verify_commands` 仍只验形状不执行（本轮只补了文档口径）。`-r requirements-dev.txt`
  这类 `.txt` 参数连存在性都不核。

**冻版（2026-11-18）**：`plugin.json` 2.7.5 → **2.8.0**，tag `v2.8.0` 打在升版本的那个提交上。
选 minor 而不是 patch 的理由是三样**可观察的行为变化**：
1. 审外部仓时行尾声明不再生效（以前能豁免，现在不能）；
2. 新增 CLI 参数 `--declared-list`，外部仓豁免只剩这一条信道；
3. `dry_load` 多了一个退出码 2 的语义（环境阻断），与「用法错误」同码但 `mode=blocked` 可区分。

`v2.7.5` 仍指 `4f04414`，冻结前 HEAD 已领先 64 个提交。下游钉版要一起换：
`forge` 自身的锁文件由枫蝶那边改，本仓只负责 tag 可解引用。

### 仍未处理（写清楚，不算已解决）

- `--include-docs` 没接进 CI：本仓文档里有大量示例文本会撞 SEC-1/3/4，逐条人工判过才能豁免。
- 探针默认覆盖面仍是两份文档。`references/neko/` 那 21 份也带行号形状（`host.py:11`、`schema.py:107` 这类），
  本轮没纳入；要纳就得先把它们改成全路径写法。
- 上游 `main` 会一直前进。今天所有结论钉在 `e14f61963`；下一轮先跑 `--fresh`。
