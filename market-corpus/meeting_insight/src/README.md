**联网搜索助手（meeting_insight）**  
为 N.E.K.O. 提供**基于 DeepSeek 国内 API 的联网搜索能力**。无需配置 Tavily、无需代理，填入一个 DeepSeek API Key 即可使用。  
**为什么需要它**  
系统内置的联网搜索通常依赖第三方服务（如 Tavily），国内用户配置门槛较高。本插件直接调用 DeepSeek 服务端自带的 web_search 能力，只需一个国内可访问的 API Key，开箱即用。  
**功能**  
- **api_web_search**：通用联网搜索工具。用户询问新闻、实时数据、最新事件、产品版本，或任何你不确定的事实时，它会自动联网检索并返回带引用来源的结论。  
- **meeting_summarize**：会议记录总结工具。从会议转写文本中提炼核心要点与待办事项，可选联网检索补充背景。  
**安装**  
方式一：从插件市场安装（推荐）  
   
 方式二：手动导入 meeting_insight.neko-plugin 包  
**配置**  
在插件配置中填入以下字段：  
- deepseek_api_key：必填。你的 DeepSeek API Key，可从 [https://platform.deepseek.com 申请。](https://platform.deepseek.com "https://platform.deepseek.com")  
- deepseek_base_url：可选，默认 https://api.deepseek.com。  
- - `deepseek_model`：可选，默认 `deepseek-v4-flash`（服务端 web_search 需要支持该工具的模型）。  
- web_search_enabled：可选，默认 true。控制是否启用联网检索。  
- web_search_max_uses：可选，默认 5。单次调用最多检索次数。  
**安全提示**：请勿将 API Key 填入插件源码目录的 plugin.toml。推荐在 N.E.K.O. 插件管理页面的「配置」标签里填写，它保存在用户运行期配置中，不会随代码提交到 Git。  
**使用示例**  
在 N.E.K.O. 对话界面直接提问，例如：  
*帮我搜一下最近有什么 AI 相关的大新闻。*  
*2026 年 9 月 18 日外交部发言人说了什么？*  
模型会自动判断是否需要联网，并在需要时调用本插件。  
**版本**  
- 当前版本：v0.1.1  
- 作者：silverwolftyro  
- 仓库：https://github.com/silverwolftyro/n.e.k.o_plugin_meeting_insight  
