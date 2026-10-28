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


def check_node(path):
    r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
    if r.returncode == 0:
        return None
    return (r.stderr or "").strip().splitlines()[-3:]


def check_sh(path):
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stderr or "").strip()[:160]


def check_yaml(path):
    """没有 PyYAML 时的结构校验:禁 TAB、缩进 2 的倍数、块标量跳过、按层级查重复键。"""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except UnicodeDecodeError:
        return "非 UTF-8 编码"
    block_indent = None          # 进入 | / > 块标量后的基准缩进
    seq_stack = {}               # indent -> 当前列表序号(区分同一缩进下的不同列表项)
    seen = {}
    for no, raw in enumerate(lines, 1):
        if "	" in raw:
            return f"第 {no} 行含 TAB(YAML 不允许)"
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() in ("---", "..."):
            continue
        indent = len(line) - len(line.lstrip(" "))

        if block_indent is not None:
            if indent > block_indent:
                continue          # 块标量正文,原样跳过
            block_indent = None

        if indent % 2:
            return f"第 {no} 行缩进 {indent} 不是 2 的倍数"
        body = line.strip()
        if body.startswith("-"):
            # 新列表项:更深的缩进序号重置
            for k in [k for k in seq_stack if k > indent]:
                del seq_stack[k]
            seq_stack[indent] = seq_stack.get(indent, 0) + 1
            body = body[1:].strip()
            if not body:
                continue
        if not body:
            continue
        if ":" not in body:
            # 纯标量列表项(如 - "v*")合法;只有空行或奇怪结构才报
            if body[0] in "\"'[{" or body.replace(".", "").isalnum() or " " not in body:
                continue
            return f"第 {no} 行缺少冒号: {body[:40]}"
        key = body.split(":", 1)[0].strip()
        val = body.split(":", 1)[1].strip()
        if val in ("|", ">", "|-", ">-", "|+", ">+"):
            block_indent = indent
        path_id = tuple(sorted(seq_stack.items()))
        k = (path_id, indent, key)
        if k in seen:
            return f"第 {no} 行与第 {seen[k]} 行重复的键 {key!r}(同层)"
        seen[k] = no
        for a, b in (("[", "]"), ("{", "}")):
            if body.count(a) != body.count(b):
                return f"第 {no} 行括号不配对: {body[:40]}"
    return None


CHECKS = {".py": check_py, ".toml": check_toml, ".json": check_json, ".mjs": check_node,
          ".js": check_node, ".cjs": check_node, ".sh": check_sh,
          ".yml": check_yaml, ".yaml": check_yaml}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    own, corpus = [], []
    counts = {}
    for path, ext in iter_files():
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
    return 1 if own else 0


if __name__ == "__main__":
    sys.exit(main())
