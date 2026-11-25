# v1.2.68 更新说明

这一版给 API 设置页加了**探测可用模型**功能。覆盖安装即可；`runtime/` 与 `workspace/` 照例不参与覆盖。

## 一、API 设置页：探测可用模型

- 引导页填写 API key 和调用地址后，模型名称输入框旁新增「🔍 探测可用模型」按钮。
- 点击后后端调 `{baseURL}/models` 拉取该服务实际可用的模型列表：
  - **OpenAI 兼容**格式直接透传 `data[].id`；
  - **Anthropic** 补 `x-api-key` + `anthropic-version` 头；
  - **Gemini** 走 `v1beta/models?key=` 取 `name` 字段。
- 探测成功弹出下拉框，选中即自动填进模型名称；探测失败（网络不通/key 无效）仍可手动填写，不影响原有流程。

## 二、继承自上一轮的口径

- 打包：默认排除开发件 + 白名单闸（tools/ 整目录、test/、.github/、market-examples/、本机配置与登记副本全部不进包）。
- 语料锁（PR #49）、check-page 防挂死、外发门禁三洞、Gitee token 请求头等修复继续生效。

## 三、数量口径

- 单测 **287 pass / 0 fail**；static 全部通过；syntax 仓库自有 0 错。
- 包体：`BUILD_INFO.json` 出处 `3640ed0 @ v1.2.68`。

## 你需要做什么

不需要操作，覆盖安装即可。
