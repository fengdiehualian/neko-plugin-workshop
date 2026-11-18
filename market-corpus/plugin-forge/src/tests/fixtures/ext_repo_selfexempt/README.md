# ext_repo_selfexempt · 回归夹具

**不是插件，不要安装。** 这是 `security_audit.py` 的负向样例，用于钉住 A791 的 CRITICAL：

| 要素 | 作用 |
|---|---|
| `__init__.py` 里的 `DEMO_TOKEN` 行 | 被替换成真形状的假 token 后，必须被判 SEC-2 |
| 同一行的 `forge-declared: SEC-2 …` | 格式合法，但在外部仓里不许生效 |
| 自带 `AUDIT.md` 的 `### sec-declared` | 证明「自带账本 + 锚点」这条自免路径已死 |

用例在 `tests/test_security_audit.py`。断言三条：裸扫退出码非 0、`declared_ok` 为 0、
审核方清单能豁免同一行而哈希不符时仍然阻断。
