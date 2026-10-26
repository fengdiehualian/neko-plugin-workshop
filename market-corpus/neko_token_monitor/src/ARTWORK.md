# 素材说明

`static/yui-chibi.png` 为 AI 辅助生成的 YUI Q 版衍生插画，使用用户提供的动作和表情参考，并参考 N.E.K.O 的 YUI 人设、配色和装饰。它不是官方原版立绘。公开包不包含用户参考视频或开发机器路径。

N.E.K.O 来源：https://github.com/Project-N-E-K-O/N.E.K.O

保留 `NEKO-ASSET-LICENSE` 与 `NEKO-ASSET-NOTICE`。本插件不表示官方认可。

## 低额度追逐（0.8.19 隔离测试）

### 0.8.25 当前使用的大肥鱼动作

正常奔跑、被咬受惊、生气来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 0.3.7 的「原地左转奔跑」「被吓一跳」「点击回应-傲娇生气」。从奔跑段排除站立和摔倒部分，转换为保留透明通道的等比裁切 PNG 图集。

反追时按用户要求，戴头套的大肥鱼整体保留以前那版 `whale-maid-tiger-run.png`，原文件未改；未采用新绘的舞狮头图，也未采用出现拖影的光流补帧。

`yui-dsh-style-run-12.png` 是以试作六帧 YUI 为参考，使用内置 imagegen 补画的十二帧跑路动作，4 列 3 行，24 fps，动作周期 0.5 秒。人物保留黑发猫耳、蓝眼、钻石发饰、猫咪光环和蓝白外套。原始图集 `artwork/chase/yui-fleeing-12-source.png` 仅机械分格、缩放、留透明边距后接入，提示词见 `artwork/chase/yui-fleeing-12-prompt.txt`。

桌面可见角色统一在 WebKit 的 requestAnimationFrame 动画层绘制，原生透明窗口仅捕获拖动，不再用约 20 Hz 的原生图像位置更新绘制小白猫。受击反馈包括短促弹起、压缩、闪光圈和短暂绕头星星；大肥鱼被咬后0.58秒转入逃跑，YUI被追到后1.05秒转入逃跑；绕星约1.18秒后消失，不改变持久计数。

素材允许开源使用、禁止商用，二创介绍和分发须附作者仓库链接。保留 [DSH-PET-ASSET-NOTICE](DSH-PET-ASSET-NOTICE) 和逐文件来源、哈希、帧范围记录 `static/chase/dsh-whale-provenance.json`。构建脚本为 `native/build_whale_assets.py`。YUI 普通立绘、换装与原版小白猫未改动；旧 YUI 跑路和旧大肥鱼六帧图集保留为历史文件。

- `static/chase/neko-white-cat-idle.gif`：来自原版 `cat-idle-cat1.gif`；追逐现用 `neko-white-cat-walk.gif`，逐字节复制原版 `cat-idle-cat4-1.gif`。N.E.K.O 的 `_getNekoIdleCat1WalkingAssetUrl()` 使用的正是该侧身四脚走动素材，127 帧、3.81 秒，默认朝左。没有改画或生成猫咪。沿用原分发包的许可与署名文件。
- `static/chase/neko-white-cat-react.gif`：逐字节复制原版 `cat-idle-cat4-3.gif`，用于轻咬后的可爱反应。咬合与受惊星形特效由 SVG/CSS 绘制，不重新生成人物图片。
- `static/chase/whale-maid.png`：用户第三张全身鲸鱼女仆参考图的初版透明衍生素材，现仅作人物设定参考。
- `static/chase/whale-maid-frightened-run.png`：内置 imagegen 根据上述造型制作的六帧害怕奔跑图集，1536×1024、3 列 2 行、真实透明。保留蓝发、鲸鱼耳鳍与尾巴、女仆头饰和蝴蝶结；改变为惊慌回头、张嘴流汗、摆臂迈腿、头发与尾巴后甩，腾出双手而移除碗和油条。提示词全文保存在隔离 QA 目录 `whale-run-prompt.txt`。图集属于生成衍生画，并非官方动作素材。
- 初版的 `neko-white-cat-move.gif` 是被提起的动画，现已从追逐渲染中撤下；初版静态女仆也不再用于追逐。
- 「大肥鱼」指该蓝发鲸鱼女仆角色。最初试制的灰猫和普通鱼图未用于插件。
- 本次发行保留素材声明；开源代码不解除素材的非商用和署名条件，各角色与素材权利仍归相应权利人。

## YUI 模型服装（0.8.15）

`static/outfits/*.png` 是以本项目 `yui-chibi.png` 为编辑目标，用 imagegen 独立制作的九套服装衍生图。保留 YUI 的脸、发型、猫耳、发饰、姿态和 Q 版画风，修改颈部以下服装；原图保留。全部使用透明背景。

换模型时映射服装的行为参考 https://github.com/lokicorvus/crosspet 的官方模型识别说明与钩子元数据结构，没有复制其角色立绘。GPT、Claude、DeepSeek、Gemini、GLM、Kimi、Qwen、豆包和 JEV 名称用于识别对应模型或来源，不表示品牌官方合作。

## YUI 独立受击动作（0.8.25 本地修订）

`static/chase/yui-hit.png` 为四帧独立受击表情，使用内置 imagegen，以已有十二帧 YUI 为参考，保留人物与服饰：闭眼缩身、蹲下、晕乎乎、恢复平衡。原始透明素材在 `artwork/chase/yui-hit-source.png`，完整提示词在 `artwork/chase/yui-hit-prompt.txt`。运行图集仅机械分格、缩放并留透明边距。

四帧按 0 / 0.12 / 0.4 / 0.86 秒切换，1.15 秒恢复跑步；YUI 被追到的反馈阶段为 1.05 秒。受击时隐藏跑步图，前置 YUI 并让大肥鱼稍稍后撤，台词气泡优先显示“呜喵！轻、轻一点喵～”。没有更换以前那版戴头套大肥鱼，也没有更改普通 YUI、小白猫或换装。
