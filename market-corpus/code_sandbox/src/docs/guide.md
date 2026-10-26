# 代码沙箱（code_sandbox）

让 YUI 在**受限安全沙箱**中执行 Python 代码，适合：

- 数学计算、统计与数据处理
- 算法验证与逻辑推理辅助
- 批量文本/列表变换（纯计算）
- 简单数据建模实验

## 工具

| 工具 | 说明 |
|---|---|
| `run_code(code, timeout=10)` | 在子进程隔离中执行 Python 代码，返回 stdout/stderr |

- `code`：要执行的 Python 代码（纯计算逻辑）
- `timeout`：超时秒数（1–60，默认 10），超时自动强制终止

## 安全边界（内置，不可关闭）

以下行为会被 **AST 静态扫描拦截**，不会执行：

- 导入系统/网络/文件类模块：`os`、`subprocess`、`sys`、`socket`、`requests`、
  `urllib`、`ctypes`、`shutil`、`pathlib`、`multiprocessing`、`threading` 等
- 调用危险内置：`open`、`exec`、`eval`、`compile`、`input`、`getattr` 等
- 访问危险属性：`.system`、`.Popen`、`.run`、`.connect`、`.write_text` 等

**允许使用的标准库**：`math`、`random`、`json`、`statistics`、`collections`、
`datetime`、`re`、`itertools`、`functools`、`decimal`、`fractions`、`heapq`、
`bisect`、`string`、`base64`、`hashlib`、`struct`、`uuid`、`secrets` 等纯计算模块。

## 示例

用户说：帮我算一下 1 到 100 的质数之和

YUI 调用：

```text
run_code:
  code: |
    import math
    primes = [n for n in range(2, 101)
              if all(n % d != 0 for d in range(2, int(math.sqrt(n)) + 1))]
    print(sum(primes), primes[:10])
```

返回：`执行成功（0.2s）：1060 [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]`

## 环境

- 执行使用 `python -I`（隔离模式：忽略外部环境变量与 site-packages）
- 子进程环境已剥离常见 API Key（DASHSCOPE_/OPENAI_/DOUBAO_/ARK_/KLING_ 等）
- 工作目录为系统临时目录下的 `neko_code_sandbox/`
- 输出每路截断 50K 字符
