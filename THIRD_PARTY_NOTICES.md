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

## 4. 仓库里的第三方文本与副本(逐条实测)

以前这一段最容易被含混过去:把 `references/**` 整片说成「本仓库文档」。这里按实测分开写。

- `market-examples/shell_cmd`:5 个文件,与 `market-corpus/shell_cmd` 逐字节相同,许可判定:**未声明**(仓库里没有 LICENSE 文件)
- `market-examples/sys_monitor`:5 个文件,与 `market-corpus/sys_monitor` 逐字节相同,许可判定:**未声明**(仓库里没有 LICENSE 文件)
- `market-examples/tavily_search`:6 个文件,与 `market-corpus/tavily_search` 逐字节相同,许可判定:**未声明**(仓库里没有 LICENSE 文件)

处理方式:既然 `market-corpus/**` 因许可问题已不进安装包,这三份**同内容副本**也没有理由继续随包分发。
`market-examples` 与 `market-examples/**` 都设了 `export-ignore`,`build_payload.py` 的 FORBIDDEN 与
`check_package.ps1` 的禁含清单同样拦住它;开发仓里仍保留(要读源码时它是现成的精读范例),
装机版改成本机 `node workbench/fetch_corpus.cjs` 取回后读 `market-corpus/<slug>/src`。

### `experts/neko-plugin-ops/`(专家团文本)

该团 `README.md` 自陈:多名成员的能力**吸收自**开源 agent 集合
[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) 与其中文版
[jnMetaCode/agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh)。
两个上游 2026-11-16 实测**均存在、均为 MIT**(GitHub API license=MIT)。
所以这里是「按 MIT 分发并保留上游署名」,不是「本仓库原创」;逐文件的对应关系尚未建立,
下表先把现状哈希钉住,日后改动或补映射时能看出差异。

| 文件 | 字节 | SHA-256(现算) |
|---|---|---|
| `experts/neko-plugin-ops/references/neko-market-patterns.md` | 4484 | `f33a8c716f58c87ef239dab78c62a62d9f647cb7e3be6fc4838fd2b0f415d768` |
| `experts/neko-plugin-ops/references/neko-sources.md` | 4321 | `f1141873df69bd9fdf0587fcb0ee70c00758f10e36758dd6d4cef7515c318d8c` |
| `experts/neko-plugin-ops/references/plugin-system-prompt-template.md` | 4591 | `9cac202208b9f5478e8e0c9df62f9061f6f504b57f4d41d8d41471605a87a617` |
| `experts/neko-plugin-ops/references/system.llm` | 50125 | `82e012e0c7510af66e7b690370e1c8e79ff38b0bcd892f5e5adcb199d7a6d9d1` |
| `experts/neko-plugin-ops/agents/neko-architect.md` | 2753 | `c367d8506c199c92448c40a6adf08c7ad9cf82560a049cdadb2b514436e56cbb` |
| `experts/neko-plugin-ops/agents/neko-planner.md` | 2003 | `4a87cb708e41e7a5f54fdd97d8abcb4e6838d4e3fb35019f96869f3c5fc27664` |
| `experts/neko-plugin-ops/agents/neko-plugin-dev.md` | 3395 | `c0db54c42b2c477605a21d293d3bb744c012e142964fddfa277a08271fecf34f` |
| `experts/neko-plugin-ops/agents/neko-plugin-ops-team-lead.md` | 10634 | `4458b3af1fcdb944c0ce252d7125f50c01408a90f572820a360c4705482e3951` |
| `experts/neko-plugin-ops/agents/neko-reliability.md` | 2548 | `58fdd8f5889f2f04614930bbe00ad4c7d3ed44ebaa1e9fadee93f111f1504ad3` |
| `experts/neko-plugin-ops/agents/neko-reviewer.md` | 2154 | `77b90e84aabbe1f54f848899183c9af28e1e1f62ebe0097db2afdca10dab499f` |
| `experts/neko-plugin-ops/agents/neko-security.md` | 3021 | `80a31c167b55ba0324ea643bc9ecedd1a35b5bbb0875cab1344b4f5a14251770` |
| `experts/neko-plugin-ops/agents/neko-simplifier.md` | 2188 | `9f67f564b5b9d5038bfb32345b46ae76e8db81591f53574e290689941c533e26` |
| `experts/neko-plugin-ops/agents/neko-skill-ops.md` | 2173 | `f004b13e529006f4cd0e236775471646f94e2896c4eb91ccab9f662a6e8966cb` |
| `experts/neko-plugin-ops/agents/neko-tdd.md` | 2063 | `ab9e0d088d0330aaa143403dab988e14e97e03433eab5c80c64473d1f4085473` |


## 5. 本仓库生成物

工坊生成的插件脚手架(`packs/neko-plugin/templates/**`)与本仓库自写的官方文档快照
(`packs/neko-plugin/skill/neko-plugin-dev/references/**`,**不含第 4 节所列的第三方副本与派生文本**)按本仓库 MIT 许可分发。
references 下的官方文档属于 N.E.K.O. 项目文档,由工作台同步而来,引用时以文档站为准(见 `SKILL.md` 的「唯一事实来源」条目)。

第 4 节那两类不归在这里:`market-examples/**` 是第三方市场插件的逐字节副本(许可未声明,已不进包),
`experts/neko-plugin-ops` 的提示词文本吸收自两个 MIT 上游(保留上游署名,不是本仓库原创)。

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

生成于:2026-11-16
