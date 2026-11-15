# v1.2.64 更新说明

这一版继续处理 issue:上一版(1.2.63)加的门禁被指出「可能根本不会触发」,以及对发布链路的凭据/TLS 质疑。
本轮把这四条(#10 / #11 / #12 / #13)全部查实并修好,而且每条都给了真引擎或真渠道的证据。

## 一、外发写门禁之前是空转的,现在真的会拦(issue #10,最重要)

上一版的确认逻辑挂在引擎的 `permission.asked` 事件上,但工坊写给引擎的配置是 `bash: "allow"`。
从引擎二进制里能看到:字符串会被展开成 `{"*":"allow"}` —— **任何命令都不会发出权限询问**。
也就是说:你让我 push,Agent 直接 push,那个确认卡永远不会出现。这轮修的是这个根子:

- 引擎权限配置改为**按命令模式细分**:外发写形态(git push 及其 `git -C 仓库 push`、`git --git-dir=… push`、
  `git -c … push` 等变体,`gh *`,`npm/pnpm/yarn publish`,`docker/podman push`,`curl -X POST/--data`,
  `Invoke-RestMethod`,内联脚本发 HTTP)设 `ask`;只读形态(`gh issue list`、`git status/log/diff/commit` 等)再放回 `allow`。
  引擎取「最后命中」的规则,所以放行项必须排在询问项之后 —— 这点也从二进制里的 `findLast` 确认过。
- **询问规则与运行时判定由同一张表生成**(`enginePermissionConfig()`),不再一处改一处忘。
- 判定不再是纯黑名单:git/gh 先按 token 解析跳过全局选项再看子命令,`gh --repo x issue create`、
  `python -c "requests.post(...)"`、`node -e "fetch(...)"` 都会命中。
- **真引擎端到端回归**已进仓库:`tools/probes/e2e-outward-gate.cjs`。用真引擎 + 会返回工具调用的本地 mock 网关,
  断言三件事:①外发写确实进入确认队列;②**批准前命令没有执行**(以网关计数为证:引擎没有带着工具结果回来);
  ③点「允许这一次」之后才执行。本轮实测 10 项全过。
- 边界仍然要说清:**ZCode 底座由子进程自理权限,工坊拿不到 permission 事件**,那条路径只有 AGENTS 规则约束;
  OpenCode 引擎底座现在是真拦截。

## 二、发布链路不再关证书校验,token 不再进 URL(issue #11)

- 新增 `workbench/release_tls.py` 统一 TLS 口径:**默认证书校验开启**,CA 优先 certifi。
  之前那些请求全都带着 GitHub / 码云的 token 却用 `CERT_NONE` —— 中间人等于顺手拿到发布渠道的钥匙。
- git 侧的真相记下来:本机 PortableGit(schannel)够不到吊销服务器,一律报 `0x80092013`;
  `http.schannelCheckRevoke=false` 在这个构建上**不生效**(实测)。正解是换 **OpenSSL 后端 + CA bundle**
  —— 实测码云在完整校验下照样通,于是 git 不再关校验,`build_payload.py` 里那句多余的 `GIT_SSL_NO_VERIFY` 也删了
  (`git archive` 本来就不联网)。
- 码云 token 从 URL 查询串改到 `Authorization: token …` 请求头(实测两端都认),URL 不再进日志;
  真遇到不认头的情况才退回查询参数,并且会把这件事打印出来。
- **已发布的 tag 不再强推重写**:脚本会先比对远端 tag 指向,不同就停下来要求换版本号 ——
  同名版本换内容,谁也没法说清装的是哪一份。
- 收尾核对升级为**把资产本体下回来算 SHA256**(以前只比 release 正文那行字,正文和包可以一起被换)。
  实测:码云附件下回 71,717,376 字节、SHA 与本地一致 ✓;GitHub 那条顺带修掉一个我自己的 bug
  (拿 302 后 CDN 的 content-length 当基准,会误报「字节数与声明不符」)。
- 真要临时降级只剩显式开关 `NEKO_INSECURE_TLS=1` / `NEKO_INSECURE_GIT=1`,而且每次都会打印风险,不会静默。

## 三、自动更新有了独立信任根(issue #12)

哈希与包同渠道 = 渠道被劫持就能一起换;而且客户端取「版本号最高的源」,谎报更高版本就能胜出。这轮把信任根挪出渠道:

- 发布端用 **ed25519 私钥**对清单(版本 / 资产名 / 体积 / SHA256 / 发布时间)签名,
  清单作为 `N.E.K.O.PluginWorkshop-Manifest.json` 同时传到 GitHub 资产与码云附件;
  私钥只在发布机 `~\.neko-workshop\update-sign-ed25519`,**仓库里没有**(签私钥这件事由 `workbench/update_sign.cjs` 管)。
- 客户端内置公钥,**验签通过才认清单里的哈希**;验不过就当这份清单不存在。
- 升级结论的印证规则(`planUpdateTarget`):
  - 两个渠道都报同一版本 → 互相印证,可装;
  - **只有一个渠道报这个版本 → 必须有有效签名清单**,否则拒装(单渠道被劫持不再能推着升级);
  - 各渠道对同一版本声明的哈希互相矛盾 → 拒装;
  - 清单与正文声明不一致 → 以清单为准并拒装;
  - 清单有效但下回来的包哈希不对 → 拒装。
- GitHub 资产自带的 `digest` 以前被代码丢掉了(那条分支是死代码),现在真的读进来了,并与清单交叉核对。
- 端到端实测(`tools/verify-security.cjs`,12 项全过):伪造清单 → 拒;换包 → 拒;正文矛盾 → 拒;
  清单验签且哈希一致 → 才放行。
- **需要你知道的一件事**:私钥丢了就签不出新清单,那时自动更新只剩「双渠道互相印证」这条路,再不行会提示手动安装。
  请自行备份 `C:\Users\Administrator\.neko-workshop\update-sign-ed25519`。

## 四、杂项与口径修正(issue #13)

- `rules.json` 里承诺「由 test/rules-numbering.test.ts 校验」但实际文件叫 `docs-consistency.test.ts` ——
  现在真的把编号一致性拆成 `test/rules-numbering.test.ts`,并且加了一条测试:**rules.json 提到的测试文件必须存在**。
- 工具脚本里写死的 `C:\Users\Administrator\…` 收敛到 `tools/probes/paths.cjs`(环境变量优先,其次按 `%USERPROFILE%` 推导),
  7 个脚本改完,仓库里已无残留硬路径。
- `_zcode`(随包的 Node 运行时 + ZCode CLI)以前只是「从本机拷一份」,来源/版本/完整性都没记 ——
  现在 `workbench/zcode_bundle.json` 登记了 Node `v24.14.0`、ZCode `0.13.3` 与三件文件的 SHA256,
  `build_payload.py` 打包时逐项比对,**哈希对不上直接拒绝出包**。
- 新增 `THIRD_PARTY_NOTICES.md`(由 `workbench/collect_notices.cjs` 实测生成,附机器可读 `workbench/notices.json`)。
  数字不是拍的:**128 个语料目录里,MIT 11 个、GPL 8 个、AGPL 1 个、未声明 108 个**。
  未声明的那些按「未授予再分发权利」对待:只作本地阅读参考,不复制进生成物、不打包分发 ——
  原先「语料都在 MIT 下」的说法与事实不符,已在文件里更正;MIT 全文也附在文末。
- CI 里 `bun-version: latest` 改为钉住 `1.4.2`(上游发新版不该把我们的 CI 跑法换掉)。

## 校验

- 大小与 SHA256 见下方「校验」段,工坊自动更新会按它比对;本次起还附带一张已签名的清单资产。

## 升级方式

覆盖安装即可(双击 setup.exe,或用工坊「检查更新 → 是」自动走较快通道);
`runtime\`(API 配置、会话、记忆、新增的 `engine-auth.json`)与 `workspace\`(你生成的插件)都不会被动到。
