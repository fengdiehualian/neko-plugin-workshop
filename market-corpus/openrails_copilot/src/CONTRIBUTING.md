# 贡献指南

感谢你考虑为 Open Rails Copilot 做出贡献！

## 🤝 如何贡献

### 报告问题

如果你发现 bug 或有功能建议：

1. 在 Issues 中搜索，确认问题是否已被报告
2. 如果没有，创建新 Issue，包含：
   - 清晰的标题和描述
   - 重现步骤（如果是 bug）
   - 预期行为 vs 实际行为
   - 你的环境信息（OS、Python 版本、Open Rails 版本）
   - 相关日志或截图

### 提交代码

1. **Fork 仓库**
   ```bash
   git clone https://github.com/<your-username>/openrails_copilot.git
   cd openrails_copilot
   ```

2. **创建特性分支**
   ```bash
   git checkout -b feature/your-feature-name
   # 或
   git checkout -b fix/your-bug-fix
   ```

3. **进行修改**
   - 遵循现有代码风格
   - 添加必要的测试
   - 更新相关文档

4. **运行测试**
   ```bash
   python -m pytest
   ```

5. **提交更改**
   ```bash
   git add .
   git commit -m "feat: add amazing feature"
   # 或
   git commit -m "fix: resolve speed detection issue"
   ```

   提交信息格式：
   - `feat:` 新功能
   - `fix:` Bug 修复
   - `docs:` 文档更新
   - `test:` 测试相关
   - `refactor:` 代码重构
   - `chore:` 构建/工具链更新

6. **推送并创建 Pull Request**
   ```bash
   git push origin feature/your-feature-name
   ```

   在 GitHub 上创建 Pull Request，描述：
   - 这个 PR 做了什么
   - 为什么需要这个改动
   - 如何测试这个改动

## 📝 代码风格

- 使用 4 空格缩进
- 遵循 PEP 8 Python 代码规范
- 函数和类添加文档字符串
- 复杂逻辑添加注释

## 🧪 测试

- 新功能需要添加测试
- 确保所有测试通过后再提交 PR
- 测试文件放在 `tests/` 目录

## 📚 文档

如果你的改动影响用户使用：

- 更新 README.md
- 更新相关 docs/ 文档
- 在 PR 中说明文档的改动

## 🎯 开发重点

当前欢迎的贡献方向：

1. **参数调优** - 基于实战验证数据优化检测阈值
2. **新事件检测** - 添加更多有价值的安全事件
3. **性能优化** - 降低 CPU/内存占用
4. **测试覆盖** - 增加单元测试和集成测试
5. **文档改进** - 完善使用说明和示例

## ❓ 问题？

有任何疑问欢迎：
- 在 Issue 中提问
- 在 PR 中讨论
- 查看现有文档

再次感谢你的贡献！ 🙏
