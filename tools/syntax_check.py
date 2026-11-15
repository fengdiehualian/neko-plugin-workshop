"""全包语法体检:py / toml / json / mjs / sh / yml,区分「仓库自有」与「市场语料(第三方)」。

用法: python tools/syntax_check.py [--json]
退出码:自有文件存在语法错误 → 1,否则 0(语料里的旧语法只统计,不影响退出码)。
"""
import argparse
import json
import os
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "node_modules", "runtime", "workspace", "_review", "_scaffold", "__pycache__", ".venv", "vendor"}
CORPUS = os.path.join(ROOT, "market-corpus")

PY = sys.executable
NODE = os.environ.get("NODE_BIN", "node")
_NODE_OK = None  # 探测一次:本机/CI 有没有可用的 node
_JS_SKIPPED = [0]  # 因为没 node 而没校验的 JS 文件数


def iter_files():
    for cur, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = os.path.join(cur, f)
            yield p, os.path.splitext(f)[1].lower()


def check_py(path):
    try:
        src = open(path, "rb").read()
        compile(src, path, "exec")
        return None
    except SyntaxError as e:
        return f"{e.msg}(line {e.lineno})"
    except Exception as e:
        return str(e)[:120]


def check_toml(path):
    try:
        tomllib.load(open(path, "rb"))
        return None
    except Exception as e:
        return str(e)[:120]


def check_json(path):
    try:
        json.load(open(path, encoding="utf-8"))
        return None
    except Exception as e:
        return str(e)[:120]


def node_missing():
    """工具链没配好时不要装作「65 个文件全坏了」:那会让人以为代码崩了。
    改成「这些 JS 本次没校验」并在末尾显式警告(CI 里装了 node,不会出现这条)。"""
    global _NODE_OK
    if _NODE_OK is None:
        try:
            r = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=20)
            _NODE_OK = r.returncode == 0
        except Exception:
            _NODE_OK = False
    return not _NODE_OK


def check_node(path):
    if node_missing():
        _JS_SKIPPED[0] += 1
        return None  # 跳过并在结尾汇总里警告,不算文件有问题
    try:
        r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if r.returncode == 0:
        return None
    return (r.stderr or "").strip().splitlines()[-3:]


def check_sh(path):
    try:
        r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    except FileNotFoundError:
        return None  # 本机没有 bash(Windows 常见):跳过,不算失败
    return None if r.returncode == 0 else (r.stderr or "").strip()[:160]


def check_yaml(path):
    """没有 PyYAML 时的结构校验:禁 TAB、缩进 2 的倍数、块标量跳过、按父级作用域查重复键。"""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except UnicodeDecodeError:
        return "非 UTF-8 编码"
    chain = []            # [(indent, scope_token)] 当前打开的父级
    seq = {}              # (parent_chain, indent) -> 列表序号
    seen = set()
    block_indent = None
    for no, raw in enumerate(lines, 1):
        if "	" in raw:
            return f"第 {no} 行含 TAB(YAML 不允许)"
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() in ("---", "..."):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None
        if indent % 2:
            return f"第 {no} 行缩进 {indent} 不是 2 的倍数"
        while chain and chain[-1][0] >= indent:
            chain.pop()
        body = line.strip()
        if body.startswith("-"):
            key = (tuple(t for _, t in chain), indent)
            seq[key] = seq.get(key, 0) + 1
            chain.append((indent, f"-{seq[key]}"))
            body = body[1:].strip()
            if not body:
                continue
        if ":" not in body:
            if body[:1] in "\"'[{" or " " not in body:
                continue
            return f"第 {no} 行缺少冒号: {body[:40]}"
        k, v = body.split(":", 1)
        k, v = k.strip(), v.strip()
        scope = tuple(t for _, t in chain)
        kid = (scope, indent, k)
        if kid in seen:
            return f"第 {no} 行重复的键 {k!r}(同一父级下)"
        seen.add(kid)
        if v in ("|", ">", "|-", ">-", "|+", ">+", ""):
            chain.append((indent, k))
        if v in ("|", ">", "|-", ">-", "|+", ">+"):
            block_indent = indent
        for a, b in (("[", "]"), ("{", "}")):
            if body.count(a) != body.count(b):
                return f"第 {no} 行括号不配对: {body[:40]}"
    return None


def check_tsx_batch(files):
    """把 .ts/.tsx/.jsx 一次性交给 bun 的转译器做语法校验(无需 typescript 依赖)"""
    if not files:
        return {}
    bun = os.environ.get("BUN_BIN", "bun")
    try:
        probe = subprocess.run([bun, "--version"], capture_output=True, text=True, timeout=20)
        if probe.returncode != 0:
            raise FileNotFoundError(bun)
    except Exception:
        # 和 node 同理:工具链缺失要说成「没校验」,不能把 16 个 .ts 全报成坏文件
        _JS_SKIPPED[0] += len(files)
        return {}
    try:
        r = subprocess.run([bun, os.path.join(ROOT, "tools", "tsx_check.mjs")] + files,
                           capture_output=True, text=True, timeout=300)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        return {b["file"]: b["error"] for b in data.get("bad", [])}
    except Exception as e:
        # bun 在但跑挂了:这确实要报,否则「转译器静默失效」没人发现
        return {f: f"TSX 校验器异常:{str(e)[:80]}" for f in files}


CHECKS = {".py": check_py, ".toml": check_toml, ".json": check_json, ".mjs": check_node,
          ".js": check_node, ".cjs": check_node, ".sh": check_sh,
          ".yml": check_yaml, ".yaml": check_yaml}


def main():
    # 输出里有 ✗ / … 这类字符,Windows 控制台默认 GBK:一旦把结果重定向到文件就会
    # UnicodeEncodeError 崩在半路(报错信息反而看不见)。强制按 UTF-8 输出,兜不住就替换成 ?。
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    own, corpus = [], []
    counts = {}
    tsx_files = []
    for path, ext in iter_files():
        if ext in (".ts", ".tsx", ".jsx"):
            tsx_files.append(path)
        fn = CHECKS.get(ext)
        if not fn:
            continue
        err = fn(path)
        rel = os.path.relpath(path, ROOT)
        bucket = corpus if path.startswith(CORPUS) else own
        counts.setdefault(ext, [0, 0])
        counts[ext][0] += 1
        if err:
            counts[ext][1] += 1
            bucket.append((rel, err))

    tsx_bad = check_tsx_batch(tsx_files)
    if tsx_files:
        counts.setdefault(".tsx", [0, 0])
        counts[".tsx"][0] = len(tsx_files)
        counts[".tsx"][1] = len(tsx_bad)
        for path, err in tsx_bad.items():
            rel = os.path.relpath(path, ROOT)
            (corpus if path.startswith(CORPUS) else own).append((rel, err))

    if a.json:
        print(json.dumps({"counts": counts, "own": own, "corpus": corpus[:80]}, ensure_ascii=False, indent=1))
    else:
        print("== 检查文件数 ==")
        for ext, (n, bad) in sorted(counts.items()):
            print(f"  {ext:6s} {n:5d} 个,失败 {bad}")
        print(f"\n== 仓库自有文件错误 ({len(own)}) ==")
        for rel, err in own:
            print(f"  ✗ {rel}\n    {err}")
        print(f"\n== 市场语料(第三方)错误 {len(corpus)} 个 ==")
        for rel, err in corpus[:15]:
            print(f"  ! {rel}  {str(err)[:90]}")
        if len(corpus) > 15:
            print(f"  … 另有 {len(corpus)-15} 个")
        if _JS_SKIPPED[0]:
            print(f"\n⚠ 工具链没配好(node / bun 不可用):{_JS_SKIPPED[0]} 个 JS/MJS/TS 文件本次「未校验」(不是通过)。"
                  f"设 NODE_BIN、BUN_BIN 或把它们加进 PATH 再跑。")
        else:
            print("\nJS/TS 语法校验:全部已校验(未出现「工具链缺失跳过」)")
    return 1 if own else 0


if __name__ == "__main__":
    sys.exit(main())
