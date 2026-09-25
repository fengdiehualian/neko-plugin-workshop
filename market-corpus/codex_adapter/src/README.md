# Codex Adapter Plugin

通过 @llm_tool 将 OpenAI Codex CLI 注册为猫娘可调用的工具集。

## 功能

- `codex_execute`: 执行 Codex 任务（写代码、改 bug、跑测试、查文档等）
- `codex_check_health`: 检查 Codex CLI 是否可用
- `codex_list_sessions`: 列出所有会话
- `codex_clear_session`: 清除会话记录
- `codex_get_config`: 获取当前适配器配置

## 特性

- 会话恢复（thread_id）
- Web 搜索支持
- 快速模式支持
- 瞬态错误自动重试
- CODEX_HOME 管理

## 安装

在 N.E.K.O 插件商城搜索 "Codex Adapter" 安装。

## 前置要求

- OpenAI Codex CLI 已安装并在 PATH 中可用
- N.E.K.O 主程序运行中
