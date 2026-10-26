# 第三方数据与许可说明

## UID 公开展示导入（2026-10-18，源码候选）

- 数据服务：[Enka.Network](https://enka.network/)，星铁 UID 接口为
  `https://enka.network/api/hsr/uid/{uid}`。用户明确同意后，才发送其输入的国服 UID。
  遵守服务使用要求、自定义 User-Agent、服务 TTL 和限流；不枚举 UID、不采集全服数据、
  不自动切换服务、不发送密码或 Cookie，不把第三方服务声称为游戏官方接口。
- 系统代理为独立可选项，仅用于Enka请求，经用户选择后沿用其现有代理；不创建新代理、
  不修改系统/宿主设置，不覆盖明确禁用代理的选择。代理地址和凭据不进入存档或错误诊断。
- 接口字段与国服分支参考开源客户端 [seriaati/enka-py](https://github.com/seriaati/enka-py)：
  `enka/clients/hsr.py`、`enka/models/hsr/response.py`、`enka/models/hsr/character.py`、
  `enka/enums/hsr.py`。该客户端为 GPL-3.0；本候选未复制或捆绑其 SDK/属性计算器代码，
  使用 Python 标准库独立实现必要的协议字段映射。不得将其代码许可证等同于服务、
  账号数据或游戏内容的许可证。若以后直接引入 SDK，需另行履行对应许可证义务。
- 中文身份、光锥和套装名称仍按已有 StarRailRes 外置快照的 ID 查询，沿用其来源与 AGPL 说明。
  不从展示昵称或文本猜测角色，不为缺失的最终属性面板制造数值。
- 原始返回、签名、生日、昵称、owner 等无关字段不写入存档或默认诊断；仅保存玩家确认的
  角色/配装白名单、UID、来源、查询时间及必要缓存期限。未展示角色不等于未拥有。
  未确认预览保存在插件数据中，30分钟后不可确认，下一次成功读取替换；不会自动写入正式角色库。

## 演示线D1有限养成评价（2026-10-18）

新增黄泉常规输出候选，来自同一固定MIT提交的
`src/lib/conditionals/character/1300/Acheron.ts` 中 simulation/scoring 的少量元数据；
仍随附 Fribbels MIT 全文。不复制优化器、不把上游权重变成自造分数或毕业阈值。
黄泉光锥「晚安与睡颜」为外置StarRailRes效果基础上的条件推断，不标作上游排名。
真珠/绯英/黄泉的属性作用与检查项为原创短摘要；引用原有规则来源，
不存储或再分发整份游戏资料。黄泉是常驻参考覆盖，不虚构她在4.6当期复刻。

## 国服4.6特化攻略服务（2026-10-15源码候选）

- `fribbels/hsr-optimizer` 固定提交 `d68d14cf63bc5c2be448b40822588fe95b86066e`，MIT，
  Copyright (c) 2024 Fribbels。随附全文 `resources/licenses/Fribbels-MIT.txt`。
  少量主词条、套装候选、参考队伍元数据来自 `src/lib/relics/scoring/scoringMetadata.ts`、
  `src/lib/state/metadataInitializer.ts`，机制参考 `src/lib/conditionals/character/1500/Pearl.ts`
  与 `Evanescia.ts`。记录固定提交链接；不捆绑、不运行优化器，不将其模拟配置当作关卡排名。
- 官方4.6公告 https://hsr.hoyoverse.com/zh-cn/news/166468 ，官方4.5公告
  https://sr.mihoyo.com/news/165896 ，官方活动跃迁公告
  https://www.taptap.cn/moment/852211155802787824 ：只核对公告事实和日期并写简短摘要，
  不转载公告正文、图片，不声称官方文本采用插件开源许可证。
- 冻梨游研社的虚构叙事参考 https://www.taptap.cn/moment/848634700330176018 与
  异相仲裁参考 https://www.taptap.cn/moment/853782721422426516 ：仅作少量节点事实、
  参考队伍及简短原创摘要的来源，保留作者和链接，不复制整篇攻略、图片或完整推荐表。
  公开可读不代表开源许可；插件许可证不授予这些文章内容的再分发权。
- 上述来源与适用期次逐条记在 `resources/guidance.cn-4.6.json`；装备名称和效果仍按实体 ID
  从原有外置 StarRailRes 组件读取，不复制整库。机制推断的替代光锥标为 `mechanism_inference`，
  与上游候选及公告事实区别，不宣称经过账号模拟或固定强度排名。
- 一篇4.6末日幻影社区文章将期次写成「支配恶兽」，与官方「支配遗忘」冲突；用户确认以
  官方为准，本候选未纳入该文节点/配队，不因同版本或相似天气自动合并来源。
- 战雷插件设计参考：
  https://github.com/TL0SR2/project-N-E-K-O-Warthunder-8111-data-plugin ，只参考数据、事件、
  发言仲裁分层；保留本插件旧观察与发言链路，不复制上游代码，不加入游戏操作。

MIT/AGPL等开源许可均不授予游戏角色、商标、美术或其他权利人的独立权利。
本候选未发布市场；未来扩充或发布时仍须按每个来源的许可与使用范围逐项检查。

## Mar-7th/StarRailRes

- 项目：https://github.com/Mar-7th/StarRailRes
- 固定提交：`541e1100dcfe9a299c6bd500c6d3c4115e0451e4`
- 上游许可：GNU Affero General Public License v3.0 only（AGPL-3.0-only）
- 许可全文：https://github.com/Mar-7th/StarRailRes/blob/541e1100dcfe9a299c6bd500c6d3c4115e0451e4/LICENSE
- 随附许可证全文：`resources/licenses/StarRailRes-AGPL-3.0.txt`

插件内的 `resources/character_registry.json` 是从固定提交的
`index_min/cn/characters.json` 派生的最小角色身份表。它只保留角色 ID、中文名称、
检索标签和人工补充的常见中文别名，并合并“三月七”和“开拓者”的多命途形态。

0.9 的完整资料不随插件包分发。完整外置资料使用固定提交 `dbe8cdfcfb0bf657b9fe1cf92d5537f118ccb487`，与上述最小名单的基准提交分别记录；`scripts/build_data_pack.py` 从该提交读取：

- `characters.json`
- `character_skills.json`
- `character_ranks.json`
- `character_skill_trees.json`
- `character_promotions.json`
- `light_cones.json`
- `light_cone_ranks.json`
- `light_cone_promotions.json`
- `relic_sets.json`
- `items.json`
- `paths.json`
- `elements.json`

这些结构化文本会被转换成独立的 SQLite 资料组件，保存在 N.E.K.O 本机数据目录，
同时保存来源、固定提交、AGPL 许可副本、Schema 版本和 SHA-256。资料组件不包含上游
图像、图标、音频或角色立绘。将资料组件放在插件外部只是为控制插件包体和便于独立
更新，不改变或规避上游许可义务；复制或再分发该资料组件时仍须遵守 AGPL-3.0-only。

上游许可仅覆盖贡献者有权许可的部分，并不授予《崩坏：星穹铁道》的游戏内容、角色
形象、商标或其他知识产权。相关权利归米哈游/HoYoverse 等权利人所有。

## OneDragon-Anything/StarRailOneDragon（头像模板）

- 来源：https://github.com/OneDragon-Anything/StarRailOneDragon
- 固定提交：`1ffd7e082c38bcdb559d2c0a194191ae21514ef0`
- 上游许可：GNU General Public License v3.0，原始全文见该提交的 `LICENSE`。
- 引用范围：`assets/template/character_avatar/*/raw.png`，以及用于角色 ID/中文名映射的 `src/sr_od/config/character_const.py` 元数据。
- 插件仅附带路径、实体映射与 Git blob 校验值清单 `resources/avatar_templates.json`；约 1.5 MB 的 72 份图像在使用时从固定上游提交下载到插件外部，并保留 `LICENSE.txt`、`SOURCE.json`。
- 不复制、导入或运行上游的游戏操作、战斗、刷资源或其他自动化模块。
- 队伍识别设计参考同一固定提交的 `src/sr_od/operations/team/check_team_members_in_world.py` 与 `assets/game_data/screen_info/normal_world.yml`：分别读取四个位置的名字，再用头像补充未知位置。本插件另行实现坐标校验与独立确认，不采用上游无最低相似度的名字择优，也不采用通过自动切人指令记录当前角色的做法。选中角色视觉判断尚未完成。
- 匹配计算使用 OpenCV 的 SIFT、特征匹配与 RANSAC；OpenCV 4.12 使用 Apache-2.0 许可，参考 https://github.com/opencv/opencv/blob/4.12.0/LICENSE 。本插件包不捆绑 OpenCV 二进制；优先复用已有依赖，缺失时下载官方 PyPI `opencv-python-headless==4.12.0.88` Windows abi3 wheel（约 38 MB），核对固定 SHA-256 后安装到插件外部 `vision-runtime`，保留 wheel 内的许可证与第三方声明。失败时仍可文字识别。

游戏头像素材的权利仍属于米哈游/HoYoverse 等权利人。上游代码许可证不构成对游戏美术的独立授权；不得将这些素材视为可任意商用或再分发的公共领域资源。若要另行分发头像资料，须保留来源与许可证并确认相应素材使用范围。

## 跨页面 OCR 设计参考（不捆绑上游代码）

- `kel-z/HSR-Scanner`：https://github.com/kel-z/HSR-Scanner ，固定提交
  `cad008e2509b6b086fd892b140c8b277424fcb03`，GPL-3.0。
  人工阅读 `src/utils/ocr.py` 和 `src/config/screenshot.py`，参考文字预处理、
  局部放大和角色名/装备穿戴者等字段分离的思路。本插件用既有共享 RapidOCR
  独立实现；不复制上述源文件，不运行其鼠标键盘控制、账号扫描或自动导航。
- `StarRailOneDragon`：沿用上述固定提交，人工阅读 `screen_info/team.yml`、
  `bag_relic.yml`、`phone_menu.yml` 等页面定义，扩展界面检查范围。配置坐标
  不复制到通用读名层；原始像素分块覆盖不依赖这些页面的固定区域。
- N.E.K.O. 社区本地 `galgame_plugin`：参考 OCR 与说话者身份分离；
  `neko_warthunder`：参考不把不可信自由文本直接作为模型事实。
  不复制其实现或引入任何游戏控制模块。

参考开源设计不表示其许可覆盖游戏文字、美术或商标。各项目的许可证与游戏
资源权利仍应分别遵守；此次补丁没有增加上游代码、权重或素材的分发。

## 本地 OCR 模型对照与可选适配（2026-10-09）

- 公开引擎：RapidAI/RapidOCR（现有 `rapidocr_onnxruntime`），项目许可证 Apache-2.0：
  https://github.com/RapidAI/RapidOCR/blob/v3.8.0/LICENSE 。
- PP-OCRv5 源项目 PaddlePaddle/PaddleOCR 的许可证见
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE （Apache-2.0）。
- 权重来源固定 RapidAI 模型镜像 `v3.8.0/onnx/PP-OCRv5`：
  https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.8.0/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx 。
  检测和识别权重的文件名、SHA-256 见 `adapters/upright_ocr.py`，使用前校验。
- 权重未随插件包分发。本次只在本机独立目录准备对照模型，尚未提供自动下载或
  新的市场模型组件；若进一步分发权重，须保留上游许可、通知及权重来源声明，
  逐项确认权重使用条款，不能仅以项目代码许可证代替所有模型授权核对。
- 插件调用公开引擎接口，不复制上游引擎源代码，不修改 N.E.K.O. 本体；游戏画面
  仅用于本地识别，不用于训练或上传。外置文件不规避许可义务。

## 官方复核参考（名称与版本）

- HoYoLAB《崩坏：星穹铁道》官方 Wiki：https://wiki.hoyolab.com/pc/hsr/
- 《崩坏：星穹铁道》官方网站：https://hsr.hoyoverse.com/
- 4.6 版本更新说明：https://hsr.hoyoverse.com/zh-cn/news/166468

官方页面只用于名称和实装状态的人工复核。本插件不复制官方 Wiki 正文、攻略正文或
媒体素材。

## 页面适配参考（2026-10-12）

- 社区界面清单：https://www.miyoushe.com/sr/article/60331284
- StarRailCopilot 页面清单及识别策略：
  https://github.com/LmeSzinc/StarRailCopilot/blob/master/tasks/base/page.py
  https://github.com/LmeSzinc/StarRailCopilot/blob/master/tasks/base/ui.py
- StarRailOneDragon 页面标志与区域识别策略：
  https://github.com/OneDragon-Anything/StarRailOneDragon/blob/main/src/one_dragon/base/screen/screen_utils.py

仅用于人工整理页面家族和参考算法思想，不代表社区使用频率统计或完整页面覆盖。
本轮规则、局部更新和补答实现由本插件编写，未复制以上项目源码、攻略正文、模型或游戏素材。
这些项目的自动操作游戏能力未接入。

## 0.9.6 第二阶段逐项适配（2026-10-17）

继续使用已登记固定提交的 Fribbels Pearl/Evanescia 主词条候选和副词条优先方向元数据，
用本插件编写的规则逐项比较；保留已有MIT全文和来源链接。不复制或运行其评分/伤害
优化引擎，不把此实现称为Fribbels完整评分或账号最优解，不复制上游角色源文件。
角色机制解释仍调用已有外置StarRailRes资料接口；许可及游戏内容权利边界不变。
本轮无新增第三方依赖、模型、权重、游戏素材或完整数据库捆绑。

## D2 普通混沌12层资料核对（2026-10-19）

- 4.6 官方公告：https://hsr.hoyoverse.com/zh-cn/news/166468 ，核对期次和规则。
- 作者实战记录：https://touno-mitsuki.hatenablog.com/entry/2026/10/05/093000 ，
  只交叉核对前两节点的首领身份、污染与忆灵相关方向；该文包含星启第三节点，
  不将其节点数、配置成绩或数值移植到普通模式。
- BWIKI「第一位天才，隐德来希，赞达尔」：
  https://wiki.biligame.com/sr/第一位天才，隐德来希，赞达尔 ，只核对少量通用机制。

上述网页未被当作可自由分发的开源数据：仅保存少量事实和本插件原创摘要，
标明来源，不分发正文或图片。普通12层两侧首领同时由本次游戏实机与玩家核对。
关卡只覆盖已确认的首领与机制方向，未声称完整波次、抗性或满星验证。
新增大黑塔/遐蝶条件候选是基于既有外置 StarRailRes 角色机制的独立推断，
不是上游优化器评分、社区队伍排名或作者已打通的普通模式配队。
沿用已有 Fribbels 来源时保留原 MIT 通知与固定提交；本轮不增加其源码或引擎。

## 后台感知助手分发说明

v0.9.0 的 Windows 后台感知助手作为独立 GitHub Release 资产分发，不包含游戏素材、
角色立绘或模型权重。窗口隔离补丁使用 CPython、PyInstaller、Pillow、psutil、pywin32、
windows-capture、NumPy 和 opencv-python-headless；版本、项目链接和许可证见
`assistant_runtime/THIRD_PARTY_NOTICES.md`。独立分发不改变插件本身的
AGPL-3.0-only 许可，也不授予任何游戏内容权利。
