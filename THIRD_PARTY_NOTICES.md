# 第三方内容许可声明(THIRD_PARTY_NOTICES)

本文件由 `node workbench/collect_notices.cjs` 生成,记录仓库内**第三方来源**的许可情况。
改内容后请重跑生成脚本,别手改数字——issue #8/#13 抱怨过「声明与事实不符」。

## 1. 市场语料 `market-corpus/`

共 **128 个插件源码目录**,取自官方市场接口(2026-10-26 抓取),仅作本地学习语料,不随工坊安装包分发二进制资产。
每个目录的许可按「仓库里的 LICENSE 文件正文 → plugin.toml 的 license 字段」顺序判定,判定结果:

| 许可 | 目录数 |
|---|---|
| 未声明 | 108 |
| MIT | 11 |
| GPL | 8 |
| AGPL | 1 |

### 未能确认许可的目录(108 个)

这些仓库既没有可识别的 LICENSE 正文,`plugin.toml` 也没写 license。
**默认按「版权所有、未授予再分发权利」对待**:只作本地阅读参考,不复制其代码进生成物,
不把它们打包分发。需要引用时先向上游确认许可。

- `agent_bridge`
- `ai_learns_to_drive`
- `anysearch`
- `apibook`
- `app_launcher`
- `autocad_assistant_tool`
- `better_txt_output`
- `better_web_search`
- `bilibili_integration`
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
- `custom_music_list`
- `data_backup`
- `deepseekharness_bridge`
- `development_aide`
- `dsh_bridge`
- `forever_companion`
- `from_the_heart`
- `galgame_plugin`
- `genshin_game_buddy`
- `git_memory`
- `keyboard_controller`
- `luogu`
- `media_generator`
- `meeting_insight`
- `miao_miao_bump`
- `minecraft_autonomous`
- `minesweeper`
- `mini_games`
- `mock_stock_meow`
- `mood_state`
- `mo_realistic_adjutant`
- `neko_163mail`
- `neko_ads`
- `neko_arcade`
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
- `neko_morning_brief`
- `neko_natural_command`
- `neko_pawpilot`
- `neko_perf_saver`
- `neko_sous`
- `neko_stellaris`
- `neko_study_copilot`
- `neko_survivors`
- `neko_terraria`
- `neko_tlm`
- `neko_token_monitor`
- `neko_translate`
- `neko_video`
- `neko_voice_bridge`
- `neko_warthunder`
- `neko_weather`
- `neko_wows`
- `plugin_database`
- `plugin_knowledge_base`
- `plugin_skill_manager`
- `proactive_recommender`
- `pvz_agent`
- `qqbot_bridge`
- `qq_auto_reply`
- `recall`
- `screen_reader`
- `selfie`
- `shale_core_lib`
- `shell_cmd`
- `skill_compat`
- `source_player`
- `steam_manager`
- `sticker_manager`
- `store_search`
- `sts2_autoplay`
- `study_companion`
- `suno_cn_music`
- `sys_monitor`
- `tarot`
- `tavily_search`
- `tc_companion`
- `teach_sister_drive`
- `testbench`
- `tfwr_companion`
- `web_searching`
- `wechat_integration`
- `workbench_installer`
- `writer_power_analysis`
- `xiyin_pavilion`
- `yuilock`
- `yui_home`

## 2. 逐目录许可明细

| 目录 | 判定许可 | 依据 | plugin.toml 里写的 license |
|---|---|---|---|
| `agent_bridge` | 未声明 | 无 LICENSE 文件 | — |
| `ai_learns_to_drive` | 未声明 | 无 LICENSE 文件 | — |
| `anysearch` | 未声明 | 无 LICENSE 文件 | — |
| `apibook` | 未声明 | 无 LICENSE 文件 | — |
| `app_launcher` | 未声明 | 无 LICENSE 文件 | — |
| `autocad_assistant_tool` | 未声明 | 无 LICENSE 文件 | — |
| `better_txt_output` | 未声明 | `src/LICENSE` | — |
| `better_web_search` | 未声明 | 无 LICENSE 文件 | — |
| `bili_viewer` | 未声明 | 无 LICENSE 文件 | — |
| `bilibili_integration` | 未声明 | `src/LICENSE` | — |
| `bililearn_bridge` | 未声明 | 无 LICENSE 文件 | — |
| `browser_skill` | 未声明 | 无 LICENSE 文件 | — |
| `call_mom` | 未声明 | 无 LICENSE 文件 | — |
| `card_master` | 未声明 | 无 LICENSE 文件 | — |
| `catgirl_daily_planner` | 未声明 | 无 LICENSE 文件 | — |
| `civ6_buddy` | 未声明 | 无 LICENSE 文件 | — |
| `claude_code_adapter` | 未声明 | 无 LICENSE 文件 | — |
| `code_sandbox` | 未声明 | 无 LICENSE 文件 | — |
| `codex_adapter` | 未声明 | 无 LICENSE 文件 | — |
| `course_schedule` | 未声明 | 无 LICENSE 文件 | — |
| `coyote_control` | MIT | `src/LICENSE` | — |
| `custom_music_list` | 未声明 | `src/LICENSE` | — |
| `data_backup` | 未声明 | `src/LICENSE` | — |
| `deepseekharness_bridge` | 未声明 | `src/LICENSE` | — |
| `development_aide` | 未声明 | 无 LICENSE 文件 | — |
| `dsh_bridge` | 未声明 | 无 LICENSE 文件 | — |
| `endless_test` | MIT | `src/LICENSE` | — |
| `forever_companion` | 未声明 | 无 LICENSE 文件 | — |
| `from_the_heart` | 未声明 | 无 LICENSE 文件 | — |
| `galgame_plugin` | 未声明 | 无 LICENSE 文件 | — |
| `genshin_game_buddy` | 未声明 | 无 LICENSE 文件 | — |
| `git_memory` | 未声明 | `src/LICENSE` | — |
| `hearthstone_companion` | MIT | `src/LICENSE` | — |
| `hitokoto` | MIT | `src/LICENSE` | — |
| `hsr_companion` | AGPL | `src/LICENSE.md` | — |
| `juno_bridge` | GPL | `src/LICENSE` | — |
| `kards_companion` | GPL | `src/LICENSE` | — |
| `keyboard_controller` | 未声明 | `src/LICENSE` | — |
| `ksp_bridge` | GPL | `src/LICENSE` | — |
| `luogu` | 未声明 | 无 LICENSE 文件 | — |
| `mail_all` | MIT | `src/LICENSE` | — |
| `mcbot_bridge` | MIT | `src/LICENSE` | — |
| `media_generator` | 未声明 | 无 LICENSE 文件 | — |
| `meeting_insight` | 未声明 | 无 LICENSE 文件 | — |
| `miao_miao_bump` | 未声明 | 无 LICENSE 文件 | — |
| `mijia` | MIT | `src/LICENSE` | — |
| `minecraft_autonomous` | 未声明 | `src/LICENSE` | — |
| `minesweeper` | 未声明 | 无 LICENSE 文件 | — |
| `mini_games` | 未声明 | 无 LICENSE 文件 | — |
| `mo_realistic_adjutant` | 未声明 | 无 LICENSE 文件 | — |
| `mock_stock_meow` | 未声明 | 无 LICENSE 文件 | — |
| `moneta_fx` | MIT | `src/LICENSE` | — |
| `mood_state` | 未声明 | 无 LICENSE 文件 | — |
| `neko_163mail` | 未声明 | 无 LICENSE 文件 | — |
| `neko_ads` | 未声明 | `src/LICENSE` | — |
| `neko_arcade` | 未声明 | `src/LICENSE` | — |
| `neko_assessment` | 未声明 | 无 LICENSE 文件 | — |
| `neko_calabiqiu` | 未声明 | 无 LICENSE 文件 | — |
| `neko_clipboard_watcher` | 未声明 | 无 LICENSE 文件 | — |
| `neko_community_chat` | 未声明 | 无 LICENSE 文件 | — |
| `neko_diary` | 未声明 | 无 LICENSE 文件 | — |
| `neko_done_bridge` | 未声明 | 无 LICENSE 文件 | — |
| `neko_fishpower` | 未声明 | 无 LICENSE 文件 | — |
| `neko_jam_band` | 未声明 | 无 LICENSE 文件 | — |
| `neko_ledger` | 未声明 | 无 LICENSE 文件 | — |
| `neko_live` | GPL | `src/LICENSE` | — |
| `neko_live_album` | 未声明 | 无 LICENSE 文件 | — |
| `neko_mail` | 未声明 | 无 LICENSE 文件 | — |
| `neko_mail_agently` | 未声明 | 无 LICENSE 文件 | — |
| `neko_mcp_serve` | 未声明 | 无 LICENSE 文件 | — |
| `neko_morning_brief` | 未声明 | `src/LICENSE` | — |
| `neko_natural_command` | 未声明 | 无 LICENSE 文件 | — |
| `neko_pawpilot` | 未声明 | `src/LICENSE` | — |
| `neko_perf_saver` | 未声明 | `src/LICENSE` | — |
| `neko_rimworld` | MIT | `src/LICENSE` | — |
| `neko_sous` | 未声明 | 无 LICENSE 文件 | — |
| `neko_stellaris` | 未声明 | `src/LICENSE` | — |
| `neko_study_copilot` | 未声明 | 无 LICENSE 文件 | — |
| `neko_survivors` | 未声明 | 无 LICENSE 文件 | — |
| `neko_terraria` | 未声明 | `src/LICENSE` | — |
| `neko_tlm` | 未声明 | 无 LICENSE 文件 | — |
| `neko_token_monitor` | 未声明 | `src/LICENSE` | — |
| `neko_translate` | 未声明 | 无 LICENSE 文件 | — |
| `neko_video` | 未声明 | 无 LICENSE 文件 | — |
| `neko_voice_bridge` | 未声明 | 无 LICENSE 文件 | — |
| `neko_warthunder` | 未声明 | 无 LICENSE 文件 | — |
| `neko_weather` | 未声明 | 无 LICENSE 文件 | — |
| `neko_wows` | 未声明 | `src/LICENSE` | — |
| `openrails_copilot` | MIT | `src/LICENSE` | — |
| `phone_workflows` | GPL | `src/LICENSE` | — |
| `plugin_database` | 未声明 | 无 LICENSE 文件 | — |
| `plugin_knowledge_base` | 未声明 | 无 LICENSE 文件 | — |
| `plugin_skill_manager` | 未声明 | 无 LICENSE 文件 | — |
| `proactive_recommender` | 未声明 | `src/LICENSE` | — |
| `pvz_agent` | 未声明 | 无 LICENSE 文件 | — |
| `qq_auto_reply` | 未声明 | 无 LICENSE 文件 | — |
| `qqbot_bridge` | 未声明 | 无 LICENSE 文件 | — |
| `quote_extract` | MIT | `src/LICENSE` | — |
| `recall` | 未声明 | `src/LICENSE` | — |
| `screen_reader` | 未声明 | 无 LICENSE 文件 | — |
| `selfie` | 未声明 | `src/LICENSE` | — |
| `sfs_bridge` | GPL | `src/LICENSE` | — |
| `shale_core_lib` | 未声明 | `src/LICENSE` | — |
| `shell_cmd` | 未声明 | 无 LICENSE 文件 | — |
| `skill_compat` | 未声明 | 无 LICENSE 文件 | — |
| `source_player` | 未声明 | 无 LICENSE 文件 | — |
| `sp2_copilot` | GPL | `src/LICENSE` | — |
| `space_launch` | GPL | `src/LICENSE` | — |
| `steam_manager` | 未声明 | 无 LICENSE 文件 | — |
| `sticker_manager` | 未声明 | `src/LICENSE` | — |
| `store_search` | 未声明 | 无 LICENSE 文件 | — |
| `sts2_autoplay` | 未声明 | 无 LICENSE 文件 | — |
| `study_companion` | 未声明 | `src/LICENSE` | — |
| `suno_cn_music` | 未声明 | 无 LICENSE 文件 | — |
| `sys_monitor` | 未声明 | 无 LICENSE 文件 | — |
| `tarot` | 未声明 | 无 LICENSE 文件 | — |
| `tavily_search` | 未声明 | 无 LICENSE 文件 | — |
| `tc_companion` | 未声明 | 无 LICENSE 文件 | — |
| `teach_sister_drive` | 未声明 | `src/LICENSE` | — |
| `testbench` | 未声明 | 无 LICENSE 文件 | — |
| `tfwr_companion` | 未声明 | 无 LICENSE 文件 | — |
| `web_searching` | 未声明 | 无 LICENSE 文件 | — |
| `wechat_integration` | 未声明 | 无 LICENSE 文件 | — |
| `workbench_installer` | 未声明 | 无 LICENSE 文件 | — |
| `writer_power_analysis` | 未声明 | 无 LICENSE 文件 | — |
| `xiyin_pavilion` | 未声明 | 无 LICENSE 文件 | — |
| `yui_home` | 未声明 | 无 LICENSE 文件 | — |
| `yuilock` | 未声明 | 无 LICENSE 文件 | — |

## 3. 随包分发的运行时

工坊安装包(`payload.zip`)除本仓库源码外,只额外携带 `workbench/zcode_bundle.json` 登记的三件:

| 条目 | 作用 | 来源与许可 |
|---|---|---|
| `_zcode/node.exe` | 跑 ZCode 底座的 Node 运行时 | Node.js 官方发行版二进制,MIT 及随附第三方许可;版本见 zcode_bundle.json 的 nodeVersion |
| `_zcode/zcode.cjs` | ZCode CLI 主体 | 随 ZCode 官方发布分发(第三方,版本见 agentVersion);工坊不修改其内容,仅随包放置 |
| `_zcode/provider/zcode-builtin.json` | 工坊内置 provider 清单 | 本仓库生成,随本仓库许可 |

`_engine`(OpenCode 编译版)、`_bin`、`_py`、`_site` 自 v1.2.26 起**从不进安装包**,故不在分发清单内;
官方安装器事实上只支持覆盖升级。

## 4. 本仓库生成物

工坊生成的插件脚手架(`packs/neko-plugin/templates/**`)与官方文档快照
(`packs/neko-plugin/skill/neko-plugin-dev/references/**`)按本仓库 MIT 许可分发;
其中 references 下的官方文档属于 N.E.K.O. 项目文档,由工作台同步而来,
引用时以文档站为准(见 `SKILL.md` 的「唯一事实来源」条目)。

**不存在的说法**:本文件不声称「forge / 其他仓库反向引用本工程」,也不声称市场语料「改写自」本仓库任何代码——
此前评审意见指出这类表述与事实不符(issue #8),现在明细以上面第 2 节的实测判定为准。

## 5. MIT 许可证全文

```
MIT License

Copyright (c) <年份> <版权持有人>

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

## 6. 改写的规则文本与外部口径(人工维护)

这一节由人写,脚本重跑只保留它、不重新判定它。数字一律看第 1-2 节的实测。

1. **复用优先门禁(reuse-before-build)** — 规则文本**改写**自
   https://github.com/1-dcjiang/neko-reuse-before-build(© 2026 1-dcjiang,MIT;仓库自带 LICENSE 文件,
   核对点 v0.3.12 / commit 4a14fbd,2026-11-15)。载体:`references/reuse-before-build.md`、
   `SKILL.md` 铁律 13、`AGENTS.md` R19、`packs/neko-plugin/rules.json` R19(同一标签,编号唯一来源是 rules.json)。
   **改写而非移植**:本工程的三轮检索口径、判定档位与验收清单是本地形状,与上游不是一一对应,
   需要正典形状时以上游仓库为准。上游另提供 N.E.K.O. 运行时强制插件,本工程只吸收其 agent 层规则文本。
   MIT 要求保留版权声明,许可全文见第 5 节。
2. **兼容扫描口径(RFC #3683)** — 引用自 https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683
   (项目文本,注明链接,不构成再许可)。`tools/scan_nekoclaw_compat.py` 与 `tools/gen_compat_report.py`
   是本仓库原创实现,不是上游代码。
3. **与 HorizonCloudy/plugin-forge(MIT) 的关系** — 只有链接引用:该仓库的
   `skills/plugin-forge/references/profile-neko.md` 文首把本仓库列为规则编号来源(引用点是 PR #5,2026-11-15)。
   本条只陈述「被引用」这件事,不声称任何一方对另一方内容的所有权或再许可(见第 4 节末尾的反例说明)。
4. **市场数据与扫描片段** — `plugins_all.json` 与快照抓取自 https://market.project-neko.cn 公开 API,
   各插件源码版权归各自作者。`market-corpus/compat-v2/scan_result.json` 另存了命中行的原文片段
   (每条 ≤160 字符),属事实性短引用;逐目录许可判定见第 1-2 节,未声明许可的目录不随安装包分发。
   风险等级:低。片段短、不替代原仓库许可,但复用前先查第 2 节该目录的判定行。

生成于:2026-11-15
