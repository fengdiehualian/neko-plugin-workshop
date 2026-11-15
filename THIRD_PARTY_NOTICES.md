# 第三方内容许可声明(THIRD_PARTY_NOTICES)

本文件由 `node workbench/collect_notices.cjs` 生成,记录仓库内**第三方来源**的许可情况。
改内容后请重跑生成脚本,别手改数字——issue #8/#13 抱怨过「声明与事实不符」。

## 0. 重要:市场语料已不进安装包

自 v1.2.64 起,`.gitattributes` 对 `market-corpus/**` 设了 `export-ignore`,
**`git archive` 产出的安装载荷不再包含这份语料**(`tools/check_package.ps1` 会核对载荷里没有 `market-corpus`)。
原因:这些是第三方插件源码,其中相当一部分没有可识别的许可声明(见下表),
由我们打包再分发并不合适。语料保留在开发仓库里作为**本地学习材料**;
需要的人在自己的机器上跑 `node workbench/fetch_corpus.cjs` 自行拉取。

> 老用户不受影响:覆盖安装只解包写入载荷里存在的文件,不会删除本机已有语料。

## 1. 市场语料 `market-corpus/` 的许可判定

共 **128 个插件源码目录**,取自官方市场接口(2026-10-26 抓取)。
判定顺序:仓库里的 LICENSE/COPYING 正文(含 `src/` 层级与大小写变体)→ `plugin.toml` 的 license 字段。

| 判定结果 | 目录数 |
|---|---|
| 未声明(仓库里没有可识别的 LICENSE) | 84 |
| Apache-2.0 | 23 |
| MIT | 11 |
| GPL | 5 |
| AGPL | 4 |
| LGPL | 1 |

两类「不是许可」的情况要分开看:

- **有 LICENSE 文件但未识别条款(0 个)**:文件在,但正文是我们没匹配上的写法。
  这类**不代表无许可**,需要逐个肉眼确认;确认前同样按不分发处理。
- **未声明(84 个)**:仓库里根本没有可识别的 LICENSE 文件。
  按「版权所有、未授予再分发权利」对待:只作本地阅读参考,不复制其代码进生成物,不打包分发。

<details><summary>无 LICENSE 文件的 84 个</summary>

- `agent_bridge`
- `ai_learns_to_drive`
- `anysearch`
- `apibook`
- `app_launcher`
- `autocad_assistant_tool`
- `better_web_search`
- `bililearn_bridge`
- `bili_viewer`
- `browser_skill`
- `call_mom`
- `card_master`
- `catgirl_daily_planner`
- `civ6_buddy`
- `claude_code_adapter`
- `codex_adapter`
- `code_sandbox`
- `course_schedule`
- `development_aide`
- `dsh_bridge`
- `forever_companion`
- `from_the_heart`
- `galgame_plugin`
- `genshin_game_buddy`
- `luogu`
- `media_generator`
- `meeting_insight`
- `miao_miao_bump`
- `minesweeper`
- `mini_games`
- `mock_stock_meow`
- `mood_state`
- `mo_realistic_adjutant`
- `neko_163mail`
- `neko_assessment`
- `neko_calabiqiu`
- `neko_clipboard_watcher`
- `neko_community_chat`
- `neko_diary`
- `neko_done_bridge`
- `neko_fishpower`
- `neko_jam_band`
- `neko_ledger`
- `neko_live_album`
- `neko_mail`
- `neko_mail_agently`
- `neko_mcp_serve`
- `neko_natural_command`
- `neko_sous`
- `neko_study_copilot`
- `neko_survivors`
- `neko_tlm`
- `neko_translate`
- `neko_video`
- `neko_voice_bridge`
- `neko_warthunder`
- `neko_weather`
- `plugin_database`
- `plugin_knowledge_base`
- `plugin_skill_manager`
- `pvz_agent`
- `qqbot_bridge`
- `qq_auto_reply`
- `screen_reader`
- `shell_cmd`
- `skill_compat`
- `source_player`
- `steam_manager`
- `store_search`
- `sts2_autoplay`
- `suno_cn_music`
- `sys_monitor`
- `tarot`
- `tavily_search`
- `tc_companion`
- `testbench`
- `tfwr_companion`
- `web_searching`
- `wechat_integration`
- `workbench_installer`
- `writer_power_analysis`
- `xiyin_pavilion`
- `yuilock`
- `yui_home`

</details>

## 2. 逐目录明细

| 目录 | 判定 | 依据 | plugin.toml 里写的 license |
|---|---|---|---|
| `agent_bridge` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `ai_learns_to_drive` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `anysearch` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `apibook` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `app_launcher` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `autocad_assistant_tool` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `better_txt_output` | Apache-2.0 | `src/LICENSE` | — |
| `better_web_search` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `bili_viewer` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `bilibili_integration` | Apache-2.0 | `src/LICENSE` | — |
| `bililearn_bridge` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `browser_skill` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `call_mom` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `card_master` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `catgirl_daily_planner` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `civ6_buddy` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `claude_code_adapter` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `code_sandbox` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `codex_adapter` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `course_schedule` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `coyote_control` | MIT | `src/LICENSE` | — |
| `custom_music_list` | Apache-2.0 | `src/LICENSE` | — |
| `data_backup` | Apache-2.0 | `src/LICENSE` | — |
| `deepseekharness_bridge` | Apache-2.0 | `src/LICENSE` | — |
| `development_aide` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `dsh_bridge` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `endless_test` | MIT | `src/LICENSE` | — |
| `forever_companion` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `from_the_heart` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `galgame_plugin` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `genshin_game_buddy` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `git_memory` | LGPL | `src/LICENSE` | — |
| `hearthstone_companion` | MIT | `src/LICENSE` | — |
| `hitokoto` | MIT | `src/LICENSE` | — |
| `hsr_companion` | AGPL | `src/LICENSE.md` | — |
| `juno_bridge` | GPL | `src/LICENSE` | — |
| `kards_companion` | AGPL | `src/LICENSE` | — |
| `keyboard_controller` | Apache-2.0 | `src/LICENSE` | — |
| `ksp_bridge` | GPL | `src/LICENSE` | — |
| `luogu` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `mail_all` | MIT | `src/LICENSE` | — |
| `mcbot_bridge` | MIT | `src/LICENSE` | — |
| `media_generator` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `meeting_insight` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `miao_miao_bump` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `mijia` | MIT | `src/LICENSE` | — |
| `minecraft_autonomous` | Apache-2.0 | `src/LICENSE` | — |
| `minesweeper` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `mini_games` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `mo_realistic_adjutant` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `mock_stock_meow` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `moneta_fx` | MIT | `src/LICENSE` | — |
| `mood_state` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_163mail` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_ads` | Apache-2.0 | `src/LICENSE` | — |
| `neko_arcade` | Apache-2.0 | `src/LICENSE` | — |
| `neko_assessment` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_calabiqiu` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_clipboard_watcher` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_community_chat` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_diary` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_done_bridge` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_fishpower` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_jam_band` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_ledger` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_live` | AGPL | `src/LICENSE` | — |
| `neko_live_album` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_mail` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_mail_agently` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_mcp_serve` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_morning_brief` | Apache-2.0 | `src/LICENSE` | — |
| `neko_natural_command` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_pawpilot` | Apache-2.0 | `src/LICENSE` | — |
| `neko_perf_saver` | Apache-2.0 | `src/LICENSE` | — |
| `neko_rimworld` | MIT | `src/LICENSE` | — |
| `neko_sous` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_stellaris` | Apache-2.0 | `src/LICENSE` | — |
| `neko_study_copilot` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_survivors` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_terraria` | Apache-2.0 | `src/LICENSE` | — |
| `neko_tlm` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_token_monitor` | Apache-2.0 | `src/LICENSE` | — |
| `neko_translate` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_video` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_voice_bridge` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_warthunder` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_weather` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `neko_wows` | Apache-2.0 | `src/LICENSE` | — |
| `openrails_copilot` | MIT | `src/LICENSE` | — |
| `phone_workflows` | AGPL | `src/LICENSE` | — |
| `plugin_database` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `plugin_knowledge_base` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `plugin_skill_manager` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `proactive_recommender` | Apache-2.0 | `src/LICENSE` | — |
| `pvz_agent` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `qq_auto_reply` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `qqbot_bridge` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `quote_extract` | MIT | `src/LICENSE` | — |
| `recall` | Apache-2.0 | `src/LICENSE` | — |
| `screen_reader` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `selfie` | Apache-2.0 | `src/LICENSE` | — |
| `sfs_bridge` | GPL | `src/LICENSE` | — |
| `shale_core_lib` | Apache-2.0 | `src/LICENSE` | — |
| `shell_cmd` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `skill_compat` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `source_player` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `sp2_copilot` | GPL | `src/LICENSE` | — |
| `space_launch` | GPL | `src/LICENSE` | — |
| `steam_manager` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `sticker_manager` | Apache-2.0 | `src/LICENSE` | — |
| `store_search` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `sts2_autoplay` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `study_companion` | Apache-2.0 | `src/LICENSE` | — |
| `suno_cn_music` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `sys_monitor` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `tarot` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `tavily_search` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `tc_companion` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `teach_sister_drive` | Apache-2.0 | `src/LICENSE` | — |
| `testbench` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `tfwr_companion` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `web_searching` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `wechat_integration` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `workbench_installer` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `writer_power_analysis` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `xiyin_pavilion` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `yui_home` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |
| `yuilock` | 未声明(仓库里没有可识别的 LICENSE) | 无 LICENSE 文件 | — |

## 3. 随包分发的运行时

工坊安装包(`payload.zip`)除本仓库源码外,只额外携带 `workbench/zcode_bundle.json` 登记的三件:

| 条目 | 作用 | 来源与许可 |
|---|---|---|
| `_zcode/node.exe` | 跑 ZCode 底座的 Node 运行时 | Node.js 官方发行版二进制,MIT 及随附第三方许可;版本见 zcode_bundle.json 的 nodeVersion |
| `_zcode/zcode.cjs` | ZCode CLI 主体 | 随 ZCode 官方发布分发(第三方,版本见 agentVersion);工坊不修改其内容,仅随包放置 |
| `_zcode/provider/zcode-builtin.json` | 工坊内置 provider 清单 | 本仓库生成,随本仓库许可 |

`_engine`(OpenCode 编译版)、`_bin`、`_py`、`_site` 自 v1.2.26 起**从不进安装包**,故不在分发清单内;
官方安装器事实上只支持覆盖升级。

## 4. 随包分发的第三方 skill 正典:`references/rbb/`

「先复用,再新建」(reuse-before-build)是**第三方上游 skill**,以逐字副本随包分发,不是本仓库产物。
下表哈希为**生成时现算**,与 `UPSTREAM_PIN.json` 逐条比对;不一致就拒绝出具本声明(不许把不符的事实写进法务文档)。

- 上游仓库:https://github.com/1-dcjiang/neko-reuse-before-build
- 引用点:tag `v0.3.20` / commit `8388e27b1af2595abff780d707e560c9a3f0c7ec`
- 许可:MIT(全文见 UPSTREAM_LICENSE;上游包内 SKILL.md 亦声明);版权行:Copyright (c) 2026 1-dcjiang
- 不变块正典 SHA-256:fa0f991fbfd236d860a2c401aff6f104f994e97d2b1fa100c23eee9d046fbc93(由上游自带 `scripts/verify_localized.py` 钉住)
- 本仓库另存一份**本地化副本** `references/reuse-before-build.neko.md`:上游中文整段逐字 + 工坊落位附录 + `localized:` 声明;对上游条款只收窄不放宽,与正典并存,**不替换正典**。

| 文件 | 字节 | SHA-256(现算) |
|---|---|---|
| `references/rbb/SKILL.md` | 18470 | `87a4994cd33a5475a1ca594dfa180bdf7a9a50106ce48a73ffe328c2da7fbc6c` |
| `references/rbb/localization.contract.json` | 4501 | `591d613fbab94eefea8282b69d1e8055288149ab78d701878b2a4d8576ed2251` |
| `references/rbb/prompt-digests.json` | 2170 | `8dd41472d189faa0a44de96a5f887050711f6919dc3e5413239a2925e0ee4e82` |
| `references/rbb/references/contribution-protocol.md` | 6759 | `9a1ede9f3ea9f1bbb38cd51ffbc5748ca86eb3a63baf49c16b3f121b9831a2c6` |
| `references/rbb/references/discovery-playbook.md` | 5190 | `277a12de685f32cc6def95242de46823a6101f2ee6926a67955345836eb4ba01` |
| `references/rbb/references/host-adapters.md` | 17016 | `bbe747ad4871cba6c90bb3c6ab4553c661a6d9db7da0e68996bc7c961727dda9` |
| `references/rbb/references/integration-seam.md` | 3609 | `58f6114dd6dd2b6e7b600fffde4b282ee5ec0a494baf6076ea89d84c83a75ec6` |
| `references/rbb/references/repo-triage.md` | 8199 | `273d0e78b45551db9aeb8ac8e265591ba23bb66e07029f5485bf9cd1b1228b52` |
| `references/rbb/references/worked-examples.md` | 5344 | `ccd01b20fca10d95d187c547c7dbb8549da924e064401a678d95f5a72b0cde4d` |
| `references/rbb/references/zh-guide.md` | 15537 | `b7476d9e6ed15a7ac2fdf86f7cfa9cc00e1acabcf665d5cb731369a0da0745ab` |
| `references/rbb/scripts/gh_helpers.py` | 7782 | `61752e17261ab7750426cb8f53ca88ab8feab68deefe5b82d1f4c3d3f7f773a5` |
| `references/rbb/scripts/prompt_digest.py` | 13034 | `b3b04f9bed4c7a43e506cf82c9a46267db14d348e57601ee92c4f7c60c7e779f` |
| `references/rbb/scripts/redaction.py` | 2458 | `292e39c96c542af8df016e60164c4daa45432db035c2585b7ab35aa84d379423` |
| `references/rbb/scripts/repo_scan.py` | 16805 | `d60854b22f356bd5d1e32d1b41f7b36f5c91b092a7ecfcff240a79c07052d405` |
| `references/rbb/scripts/verify_localized.py` | 28575 | `83e3725250989d97cc7abb4fbc2bd3ad7bdb5e9f7bf4355a8d2b9d14d2da05c9` |
| `references/rbb/tests/test_skill_pack.py` | 23201 | `6dbf3b813ce87c8773ae7a36ae8c9a67649b3c210207652367cba4acd41a874e` |
| `references/rbb/UPSTREAM_LICENSE` | 1066 | `c8cb364d2c69548eff7029d07b21f3b7c1e05a45fb22d7cff6912593b165c841` |

离线校验三条,都要 0:`node workbench/vendor_rbb.cjs --check`;
`python -I -P references/rbb/scripts/verify_localized.py references/rbb/SKILL.md --canonical`;
`python -I -P references/rbb/scripts/verify_localized.py references/reuse-before-build.neko.md`(副本**不许**加 `--canonical`,加了会被拒)。

## 5. 本仓库生成物

工坊生成的插件脚手架(`packs/neko-plugin/templates/**`)与本仓库自写的官方文档快照
(`packs/neko-plugin/skill/neko-plugin-dev/references/**`,**不含上节的第三方正典 `references/rbb/**` 与据其派生的本地化副本**)按本仓库 MIT 许可分发。
references 下的官方文档属于 N.E.K.O. 项目文档,由工作台同步而来,引用时以文档站为准(见 `SKILL.md` 的「唯一事实来源」条目)。

`references/rbb/**` 与 `reuse-before-build.neko.md` 的版权归上游,按上游 MIT(第 4 节)分发——
**把它们当本仓库 MIT 产物是错的**;此前评审指出过这类口径混淆(issue #8/#13),这里按实测逐条列明。

**不存在的说法**:本文件不声称「forge / 其他仓库反向引用本工程」,也不声称市场语料「改写自」本仓库任何代码——
此前评审意见指出这类表述与事实不符(issue #8),现在明细以上面第 2 节的实测判定为准。

## 6. 本仓库的 MIT 全文

```
MIT License

Copyright (c) 2026 fengdiehualian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

生成于:2026-11-15
