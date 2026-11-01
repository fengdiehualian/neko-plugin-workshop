## 1.2.42 — ZCode 底座代码审查修复

对照「OpenCode → ZCode 迁移」逐项审查后修了 5 处真实缺陷:

1. **token 用量没记账**:ZCode 分支不记 `usage`,导致会话 token 统计、上下文容量环在 ZCode 模式下恒为 0。现已与引擎分支同款累计。
2. **内置 ZCode 误报"没找到 zcode"**:保存底座时只要没在 PATH 里搜到 zcode 就报警告,即使包内已有 `_zcode/`;且保存响应漏了 `zcodeBundled`,UI 标签会闪回「外部 CLI」。两处都补上了。
3. **ZCode 看不到全局规则**:引擎那份 `AGENTS.md`(身份、wb.cmd 用法、安全底线)只写进引擎配置目录,ZCode 读不到。现给工作区生成一份带标记的 `AGENTS.md`,用户自己改过就不覆盖。
4. **超时一刀切 240s**:大任务会被误杀。改为「无输出看门狗」(`--verbose` 心跳,默认 360s 无输出才算挂死)+ 全局总超时;有进展就续命。
5. **报错是天书**:`ProviderBusinessError: inference exceeds tpm/rpm limit` 这类原文直接甩给用户。补了一层人话翻译(限流/密钥)。

实测:默认 ZCode 底座,任务返回 `usage={input:31971,output:69,reasoning:57}`,会话累计 `totalTokens=90233`;启动日志含「工作区 AGENTS.md 就绪」。
