# v1.2.65 更新说明

这一版只做一件事:把「先复用,再新建」(reuse-before-build)这条流程规则,**按它自己定的规矩**装进工坊。

上一轮复查(issue #8)指出:仓库里原先那份是**改写版冒充正典**——缺 `Join/Extend/Fork/Build` 判决序、缺「不可复用原因」首行门禁、缺可改性硬闸、缺公开写逐次确认、缺"不可信仓库内容",而且没有上游哈希、没跑上游自带的校验器,测试只做了字符串 contains。本轮按上游契约重做,并补了能重复跑的验证。

## 一、正典逐字进包:`references/rbb/`

- 上游:`1-dcjiang/neko-reuse-before-build`,tag `v0.3.20` / commit `8388e27b1af2`,**17 个文件逐字节复制**
- 每个文件的 SHA-256 与 git blob 哈希登记在 `references/rbb/UPSTREAM_PIN.json`;离线核对:`node workbench/vendor_rbb.cjs --check`(改一个字节就非零退出)
- MIT 全文随包(`UPSTREAM_LICENSE`,版权行 `Copyright (c) 2026 1-dcjiang`),不是只写一句"MIT"
- 上游包内文件一律不改写;要升级就重跑 vendor 脚本整目录重建,不留旧副本

## 二、工坊自己的条款写成**本地化副本**,不再顶替正典

新增 `references/reuse-before-build.neko.md` = 上游中文完整版**整段逐字** + 工坊落位附录 + `localized:` 声明。
它由 `workbench/localize_rbb.cjs` 拼装生成(正典部分不经过人手),`--check` 保证生成物不会落后于源。

副本里新增的都是"更具体",没有一条放宽上游:

- 检索五层顺序:market-patterns → market-catalog → 本机 market-corpus 源码 → market-examples → 公开仓库
- 「连续 90 天无发布且无提交 = 维护停滞」——**明确标注为本工程自定阈值**,不冒充上游条款
- 四个判决到工坊动作的映射(Join/Extend/Fork/Build → 提上游 PR / 加扩展点 / fork 自建 / 新建插件),并保留"没有第五个判决"
- 引擎门禁的实测口径:OpenCode 底座 `bash` 默认 `ask`、外发形态逐段判定;**ZCode 底座拿不到权限事件,等于没有硬闸**,交付时照实说,不许声称"配了门禁"
- 不可信内容三条硬线:语料只读不执行、不带密钥、不进发布包

## 三、许可声明与事实对齐

- `THIRD_PARTY_NOTICES.md` 新增第 4 节:上游/tag/commit/版权行/**逐文件哈希(生成时现算)**;与 `UPSTREAM_PIN` 不符时脚本**拒绝出声明**,而不是出一版看着齐全的
- 原先「`references/**` 按本仓库 MIT 分发」这句过宽表述已收紧——第三方正典与其派生副本的版权归上游,按上游 MIT 分发
- `rules.json` 的 W2 明确把 vendored 第三方代码纳入"只当资料读",并要求确需运行时用隔离参数(`-I`,Python 3.11+ 再加 `-P`)

## 四、门禁

单测从 156 增至 **182**,新增 26 项(`test/rbb-vendor.test.ts`):正典逐字节哈希、上游校验器真跑并判 `PASS`、副本声明四键齐全、许可声明与 pin 同源。

反向对照也进了测试——不是为了好看,是为了证明这道闸在管 things:
抽掉 `localized:` 声明行、删掉一条不变规则、换成"编号齐但没语义锚点"的假不变块、把关键章节砍成一句、摘掉 must_have 锚点,**全部 rc=1**;
副本偷用 `--canonical` 或换一份更宽松的契约,**全部 rc=2**。
本机没有 Python 时显式跳过并打印警告,**不会静默变绿**(issue #9 的教训)。

顺手修掉一个自己造的坑:`vendor_rbb.cjs --check` 名义离线,实则在模块加载就去问凭据管理器(实测 5–7 秒,会把测试超时吃光),现在改成只在真要联网时取 token。

## 你需要做什么

不需要操作。覆盖安装即可;`runtime/`(API 配置、会话、记忆)与 `workspace/` 不参与覆盖。
