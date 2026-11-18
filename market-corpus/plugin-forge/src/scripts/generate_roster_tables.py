#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plugin-forge · 仓库文档一致性门禁（roster.json 为唯一来源）

用途
  从 roster.json 重新生成 README.md、skills/plugin-forge/SKILL.md、
  agents/plugin-forge-lead.md 里的名册表格，并同步各处的人数声明
  （"1 主理人 + N 专家"、"调度 N 位专家"、"N 人编制"、"N 个人设"、"共 N 人编制"）。
  人数声明同时覆盖 .claude-plugin/plugin.json。
  第三件事：交付证据硬格式那句规范句必须逐字出现在点名文件里（第 3 项任务）。
  表格区域由成对标记包住，标记之间只许本脚本写入，不许手改。

用法
  python scripts/generate_roster_tables.py --check   # 只比对与校验，不改文件（CI 用）
  python scripts/generate_roster_tables.py --write   # 重新生成表格并同步人数声明
  python scripts/generate_roster_tables.py --self-test  # 人数改写的自检

校验
  - roster.json 的 members 与 agents/*.md 一一对应（文件存在、frontmatter
    name/displayName/maxTurns 与 roster 一致）。
  - 标记缺失或表格漂移 → --check 退出码 1；--write 会修复表格与人数。
  - 脚本修不了的问题（标记缺失、roster 与 agents 不一致）→ --check 与 --write
    都退出码 1，绝不静默跳过。

约束
  - 零第三方依赖（Python 3.8+）。只写上面三个被标记的文档，外加 .claude-plugin/plugin.json
    里的人数声明；其余文件一律不动。
"""
import sys, os, re, json, glob, argparse, io, contextlib

def _utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROSTER_PATH = os.path.join(ROOT, "roster.json")

# 目标：(相对路径, 标记 id, 成员子集, 列定义) 列 = (表头, 取值函数)
def _c_id(m): return m["agent_id"]
def _c_role(m): return "主理人" if m["role"] == "lead" else "专家"
def _c_name(m): return m["name_zh"]
def _c_duty(m): return m["duty"]
def _c_when(m): return m["when_to_dispatch"]

TARGETS = [
    ("README.md", "roster:readme", "all",
     [("Agent ID", _c_id), ("角色", _c_role), ("名号", _c_name), ("职责", _c_duty)]),
    ("skills/plugin-forge/SKILL.md", "roster:skill", "all",
     [("Agent ID", _c_id), ("名号", _c_name), ("什么时候派", _c_when)]),
    ("agents/plugin-forge-lead.md", "roster:lead", "experts",
     [("Agent ID", _c_id), ("名号", _c_name), ("职责", _c_duty)]),
]

# 人数声明同步：(文件, 正则, 用哪个数)  group1 = 旧数字位置
COUNT_CLAIMS = [
    (r"1 主理人 \+ (\d+) 专家", "n_experts"),
    (r"调度 (\d+) 位专家", "n_experts"),
    (r"(\d+) 人编制", "n_total"),
    (r"(\d+) 个人设", "n_total"),
    # 2026-11-16 改（A571 第 7 条）：上一条「共 (\d+) 人」是全仓库空转模式——仓库里
    # 根本没有这种句子，只有自检的合成用例。它同时作用于四份文档，将来任何无关的
    # 「会上共 3 人」都会被 --write 悄悄改成编制人数。锚定到「编制」才生效。
    (r"共 (\d+) 人编制", "n_total"),
]
COUNT_FILES = ["README.md", "skills/plugin-forge/SKILL.md",
               "agents/plugin-forge-lead.md", ".claude-plugin/plugin.json",
               # 2026-11-17 补：这份参考文档里写着「1 主理人 + 13 专家」，是活声明，
               # 以前不在名单里，人数改了它不动，--check 照样退 0（终检 sweep 第 19 条）。
               "skills/plugin-forge/references/neko/safe-dev-capabilities.md"]
# 名单外的文件里出现人数声明 = 要么补进 COUNT_FILES，要么改成「以 roster.json 为准」。
# 豁免只有两类：AUDIT.md 是带日期的历史账本（改历史数字等于造假），
# references/neko/ 下那两份机器状态记录同样按日期记账。
COUNT_EXEMPT = ["AUDIT.md"]
# 这里是 endswith 的后缀表，不是 glob：写 "*.md" 就永远不会命中（本轮自己踩的）。
COUNT_SCAN_SUFFIXES = (".md", ".markdown", ".json")

def load_roster():
    with open(ROSTER_PATH, encoding="utf-8-sig") as f:
        data = json.load(f)
    members = data["members"]
    ids = [m["agent_id"] for m in members]
    if len(set(ids)) != len(ids):
        raise ValueError("roster.json 存在重复 agent_id")
    return members

def read(path):
    # W11 字节面：文本模式默认把 "\r\n" 折成 "\n"，--write 因此顺手重排全文行尾；
    # utf-8-sig 读时剥 BOM、写时不补。改一个数字会连带改掉与人数无关的字节。
    # newline="" 保住原行尾；下游正则用 \s 与按行匹配，不受影响。
    with open(path, encoding="utf-8-sig", newline="") as f:
        return f.read()

def write(path, text):
    # W11 字节面：原行尾原样写回（text 里 \r\n 不再被二次翻译），有 BOM 就继续用
    # utf-8-sig 写回。混合行尾文件的无关行因此不再被归一化改掉。
    bom = open(path, "rb").read(3) == b"\xef\xbb\xbf"
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        f.write(text)

def render_table(members, subset, columns):
    rows = members if subset == "all" else [m for m in members if m["role"] != "lead"]
    lines = ["| " + " | ".join(h for h, _ in columns) + " |",
             "|" + "|".join(["---"] * len(columns)) + "|"]
    for m in rows:
        lines.append("| " + " | ".join(fn(m) for _, fn in columns) + " |")
    return "\n".join(lines)

def validate_against_agents_dir(members):
    problems = []
    files = {os.path.basename(p): p for p in glob.glob(os.path.join(ROOT, "agents", "*.md"))}
    roster_files = {os.path.basename(m["file"]) for m in members}
    for extra in sorted(set(files) - roster_files):
        problems.append(f"agents/{extra} 不在 roster.json 中")
    for missing in sorted(roster_files - set(files)):
        problems.append(f"roster.json 引用 agents/{missing} 但文件不存在")
    for m in members:
        p = os.path.join(ROOT, m["file"])
        if not os.path.isfile(p):
            continue
        text = read(p)
        fm = text.split("---")[1] if text.startswith("---") else ""
        name = re.search(r"name:\s*(\S+)", fm)
        if not name or name.group(1) != m["agent_id"]:
            problems.append(f'{m["file"]}: frontmatter name 与 roster 不一致')
        disp = re.findall(r'"([^"]*)"', fm.split("displayName:")[1].split("profession:")[0]) if "displayName:" in fm else []
        if "displayName:" not in fm:
            problems.append(f'{m["file"]}: frontmatter 没有 displayName，名号无法核对（静默跳过曾让 roster 改了名也报 OK）')
        elif len(disp) < 2:
            problems.append(f'{m["file"]}: displayName 不是 en/zh 两项的形状，得到 {disp}，无法核对')
        else:
            if disp[0] != m["name_en"]:
                problems.append(f'{m["file"]}: displayName.en "{disp[0]}" != roster "{m["name_en"]}"')
            if disp[1] != m["name_zh"]:
                problems.append(f'{m["file"]}: displayName.zh "{disp[1]}" != roster "{m["name_zh"]}"')
        mt = re.search(r"maxTurns:\s*(\d+)", fm)
        if not mt:
            problems.append(f'{m["file"]}: frontmatter 没有 maxTurns，轮数无法核对')
        elif int(mt.group(1)) != m["max_turns"]:
            problems.append(f'{m["file"]}: maxTurns {mt.group(1)} != roster {m["max_turns"]}')
    return problems

def sync_tables(members, do_write, errors):
    problems = []
    for rel, marker, subset, columns in TARGETS:
        path = os.path.join(ROOT, rel)
        text = read(path)
        begin, end = f"<!-- BEGIN {marker} -->", f"<!-- END {marker} -->"
        missing = [t for t in (begin, end) if t not in text]
        if missing:
            errors.append(f"{rel}: 缺少标记 {' 与 '.join(missing)}")
            continue
        head, rest = text.split(begin, 1)
        old, tail = rest.split(end, 1)
        # W11 字节面：标记区用什么行尾就按什么行尾渲染，否则 CRLF 文档的表格
        # 永远判「不新鲜」，--write 再整片重写
        eol = "\r\n" if "\r\n" in old else "\n"
        new_block = eol + render_table(members, subset, columns).replace("\n", eol) + eol
        if old != new_block:
            problems.append(f"{rel}: 标记内表格与 roster 不一致（{'已重写' if do_write else '需 --write 重写'}）")
            if do_write:
                write(path, head + begin + new_block + end + tail)
    return problems

def _replace_group(mo, new):
    """只替换捕获组那一段：旧数字不进正则，匹配里的其他数字也不动。"""
    s, base = mo.group(0), mo.start(0)
    return s[:mo.start(1) - base] + new + s[mo.end(1) - base:]

def apply_counts(text, vals):
    """把 text 里的人数声明改成 vals 里的数，返回 (新文本, [(旧声明, 应为)])。"""
    hits = []
    for pat, key in COUNT_CLAIMS:
        def repl(mo, k=key):
            if mo.group(1) == str(vals[k]):
                return mo.group(0)
            hits.append((mo.group(0), vals[k]))
            return _replace_group(mo, str(vals[k]))
        text = re.sub(pat, repl, text)
    return text, hits

# 交付证据的硬格式：规范句只有一处定义（skills/plugin-forge/SKILL.md §6），其余文件逐字引用。
# 这一条来自 2026-11-17 的第 3 项任务：口径要「机器可检」，否则它只是一句愿望。
REPORT_FORMULA = "验收证据 = 命令 + 退出码 + 输出末 20 行；三者缺一按半道交付打回。"
REPORT_SOURCE = "skills/plugin-forge/SKILL.md"
REPORT_FILES = [
    "skills/plugin-forge/SKILL.md",
    "agents/plugin-forge-lead.md",
    "agents/plugin-forge-docs.md",
    "agents/plugin-forge-sandbox.md",
    "skills/plugin-forge/references/delivery-self-check.md",
]


def check_report_formula():
    """该带规范句的文件必须逐字带上；同一文件里写两遍也要报——
    改口径时只改一处，另一处就变成假的「已对齐」。"""
    problems = []
    src_path = os.path.join(ROOT, REPORT_SOURCE.replace("/", os.sep))
    try:
        src_text = read(src_path)
    except OSError as e:
        return ["读不到规范句来源 %s：%s" % (REPORT_SOURCE, e)]
    if REPORT_FORMULA not in src_text:
        problems.append("%s 里找不到规范句，其余文件的核对没有基准" % REPORT_SOURCE)
    for rel in REPORT_FILES:
        fp = os.path.join(ROOT, rel.replace("/", os.sep))
        try:
            text = read(fp)
        except OSError as e:
            problems.append("%s 读不到：%s" % (rel, e))
            continue
        times = text.count(REPORT_FORMULA)
        if times == 0:
            problems.append("%s 缺交付证据硬格式那句（要逐字）：%s" % (rel, REPORT_FORMULA))
        elif times > 1:
            problems.append("%s 里同一句出现 %d 次，改口径时只会改一处" % (rel, times))
    return problems


def stray_count_claims():
    """名单之外还写着人数声明的文件。它们不在同步范围里，改编制时一定变陈旧。"""
    pats = [re.compile(p) for p, _k in COUNT_CLAIMS]
    found = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".drift-cache",
                                                "node_modules", "venv", ".venv")]
        for fn in files:
            # W11 A1：后缀表本身是逃逸面——同内容存成 .txt 或去掉后缀，声明检查整段跳过。
            # 无后缀文件也扫；常见文本后缀并入扫描集。解不了码的文件在下面 except 里跳过。
            # 不能改成「凡文件都试解码」：脚本源码与自检用例里就有五种受管句式的样例句，
            # 全量扫会把 scripts/*.py 与 tests/*.py 变成常红误报。
            if not (fn.endswith(COUNT_SCAN_SUFFIXES + (".txt", ".rst", ".yml", ".yaml", ".toml"))
                    or "." not in fn):
                continue
            rel = os.path.relpath(os.path.join(root, fn), ROOT).replace("\\", "/")
            # W11 A3：endswith("profile-dsh.md") 让任何以此结尾的路径整文件豁免——
            # docs/evil-profile-dsh.md 种错数也退 0，改一个字母才报。豁免只给那一个真实路径。
            if (rel in COUNT_FILES or rel in COUNT_EXEMPT
                    or rel == "skills/plugin-forge/references/profile-dsh.md"):
                continue
            try:
                text = read(os.path.join(root, fn))
            except (OSError, UnicodeDecodeError):
                continue
            for rx in pats:
                for m in rx.finditer(text):
                    found.append((rel, m.group(0)))
                    break
    return found


def sync_counts(members, do_write):
    problems = []
    vals = {"n_experts": sum(1 for m in members if m["role"] != "lead"),
            "n_total": len(members)}
    # apply_counts 只回「数字不对」的那些；声明对不对都得先知道名单里还有没有声明，
    # 所以这里单独数一遍命中（终检 sweep 第 19 条的另一半：声明整句被删掉时以前一片安静）。
    total_claims = 0
    for rel in COUNT_FILES:
        path = os.path.join(ROOT, rel)
        orig = read(path)
        total_claims += sum(1 for pat, _k in COUNT_CLAIMS for _m in re.finditer(pat, orig))
        new, hits = apply_counts(orig, vals)
        for old, want in hits:
            problems.append(f"{rel}: 「{old}」应为 {want}（{'已改' if do_write else '需 --write'}）")
        if do_write and new != orig:
            write(path, new)
    # 声明整句被删掉时，上面两条都安静：0 处命中 = 0 个问题，--check 退 0。
    # 那一格就是「人数改了没人知道」，所以这里要求名单里至少还有一处活声明。
    if not total_claims:
        problems.append("COUNT_FILES 里一处人数声明都没有：名单成了空转，请补回声明或换文件")
    # 名单外的人数声明不是「陈旧」而是「根本没人管」，所以 --write 也不代替它改：
    # 要么把文件补进名单，要么把那句话说成以 roster.json 为准。
    for rel, snippet in stray_count_claims():
        problems.append("%s: 名单外的人数声明「%s」，请补进 COUNT_FILES 或改成引用 roster.json"
                        % (rel, snippet))
    return problems

def unfixable(drift):
    """drift 里脚本自己修不了的那几条：名单外声明与空转。

    --write 对这两种也无能为力，必须退 1 让人来处理（工单 W11 A5）。
    识别靠字样：sync_counts 的文案里有「名单外」「空转」，自检钉住这两句。
    """
    return [d for d in drift if "名单外" in d or "空转" in d]


def _self_test():
    """自检：人数改写只动捕获组，前导的 1 与已正确的声明不受影响。"""
    ok = True
    vals = {"n_experts": 13, "n_total": 14}
    cases = [
        ("1 主理人 + 1 专家，调度 1 位专家，1 人编制，1 个人设",
         "1 主理人 + 13 专家，调度 13 位专家，14 人编制，14 个人设", 4),
        ("1 主理人 + 13 专家", "1 主理人 + 13 专家", 0),
        # 「共 N 人编制」这一写法以前不在模式表里：人数改了它不动，--check 还退 0
        ("编制是 1 主理人 + 13 专家，共 12 人编制。",
         "编制是 1 主理人 + 13 专家，共 14 人编制。", 1),
        # 反向：与编制无关的「共 N 人」不许被改。旧的全仓库空转模式会把它一起改掉。
        ("调研纪要：会上共 3 人发言。", "调研纪要：会上共 3 人发言。", 0),
    ]
    for src, want, want_hits in cases:
        got, hits = apply_counts(src, vals)
        if got != want or len(hits) != want_hits:
            ok = False
            print("SELF-TEST FAIL: %r → %r（命中 %d），应为 %r（命中 %d）"
                  % (src, got, len(hits), want, want_hits))
    # --write 这条路径以前没有用例：它会改文件，却只靠 CI 的 --check 侧证（终检 sweep 第 10 条）。
    # 下面这段在临时目录里造一份最小仓库，真写、真比对，跑完删掉。
    import shutil
    import tempfile
    wd = tempfile.mkdtemp(prefix="roster-self-")
    real_root = globals()["ROOT"]
    real_targets = globals()["TARGETS"]
    real_roster = globals()["ROSTER_PATH"]
    real_counts = globals()["COUNT_FILES"]
    real_exempt = list(globals()["COUNT_EXEMPT"])
    try:
        globals()["ROOT"] = wd
        members = [{"agent_id": "a-lead", "role": "lead", "name_zh": "甲", "duty": "定方向",
                    "when_to_dispatch": "开头", "max_turns": 8, "file": "agents/a-lead.md"},
                   {"agent_id": "a-exp", "role": "expert", "name_zh": "乙", "duty": "写代码",
                    "when_to_dispatch": "实现", "max_turns": 12, "file": "agents/a-exp.md"}]
        os.makedirs(os.path.join(wd, "agents"), exist_ok=True)
        # 表格里只写甲一行，roster 有两人 —— 这就是「不新鲜」的形状
        old_table = ["| Agent ID | 角色 | 名号 | 职责 |", "|---|---|---|---|",
                     "| a-lead | 主理人 | 甲 | 定方向 |"]
        first = TARGETS[0][0]
        for rel, marker, _subset, _cols in TARGETS:
            p = os.path.join(wd, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8", newline="").write(
                "抬头" + chr(10) + "<!-- BEGIN " + marker + " -->" + chr(10)
                + chr(10).join(old_table) + chr(10)
                + "<!-- END " + marker + " -->" + chr(10) + "尾巴文字" + chr(10))
        errs = []
        drift = sync_tables(members, False, errs)
        if not drift or errs:
            ok = False
            print("SELF-TEST FAIL(--check): 不新鲜的表格应报 drift 且不报 error，实得 %r / %r"
                  % (drift, errs))
        before = io.open(os.path.join(wd, first.replace("/", os.sep)), encoding="utf-8").read()
        sync_tables(members, True, [])
        after = io.open(os.path.join(wd, first.replace("/", os.sep)), encoding="utf-8").read()
        if "| a-exp |" not in after or "抬头" not in after or "尾巴文字" not in after:
            ok = False
            print("SELF-TEST FAIL(--write): 重写没写进新成员，或吃掉了标记外的文字")
        if after == before:
            ok = False
            print("SELF-TEST FAIL(--write): do_write 两条路内容一样，说明没真写")
        if sync_tables(members, False, []):
            ok = False
            print("SELF-TEST FAIL(--write): 重写之后仍报不新鲜")
        # 行尾必须跟随原文件：CRLF 的文档不能被 --write 改成 LF
        crlf = os.path.join(wd, "crlf.md")
        with open(crlf, "wb") as f:
            f.write(b"a\r\nb\r\n")
        write(crlf, "c" + chr(13) + chr(10) + "d" + chr(13) + chr(10))
        if b"\r\n" not in open(crlf, "rb").read():
            ok = False
            print("SELF-TEST FAIL(行尾): CRLF 文件被写成了 LF")
        # 缺标记 = 脚本修不了的 error，不许静默跳过
        globals()["TARGETS"] = [(first, "no-such-marker", "all", real_targets[0][3])]
        errs2 = []
        sync_tables(members, False, errs2)
        if not errs2 or "缺少标记" not in " ".join(errs2):
            ok = False
            print("SELF-TEST FAIL(标记): 缺标记没报 error，实得 %r" % (errs2,))
        # main() 的退出码：roster 读不到要退 1，不许退 0 装绿
        globals()["ROSTER_PATH"] = os.path.join(wd, "missing-roster.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_read = main(["--check"])
        if rc_read != 1:
            ok = False
            print("SELF-TEST FAIL(退出码): roster 读不到应退 1，实得 %s" % rc_read)
        # 重复 agent_id 必须在 load_roster 就炸
        dup = os.path.join(wd, "dup.json")
        io.open(dup, "w", encoding="utf-8", newline="").write(json.dumps(
            {"members": [dict(members[0], agent_id="x"), dict(members[1], agent_id="x")]},
            ensure_ascii=False))
        globals()["ROSTER_PATH"] = dup
        try:
            load_roster()
            ok = False
            print("SELF-TEST FAIL(花名册): 重复 agent_id 没被拦住")
        except ValueError:
            pass
        # 人数声明的覆盖面：名单内要报陈旧，名单外要报「没人管」，豁免要真豁免。
        # 这一组的来源是终检 sweep 第 19 条：safe-dev-capabilities.md 里的编制人数
        # 以前不在任何名单里，改了多少人都没人知道。
        claim_dir = os.path.join(wd, "docs")
        os.makedirs(claim_dir, exist_ok=True)
        io.open(os.path.join(claim_dir, "live.md"), "w", encoding="utf-8", newline="").write(
            "现有 1 主理人 + 9 专家。" + chr(10))
        io.open(os.path.join(claim_dir, "stray.md"), "w", encoding="utf-8", newline="").write(
            "调度 3 位专家。" + chr(10))
        globals()["COUNT_FILES"] = ["docs/live.md"]
        globals()["COUNT_EXEMPT"] = []
        probs = sync_counts(members, False)
        if not any("1 主理人 + 9 专家" in p for p in probs):
            ok = False
            print("SELF-TEST FAIL(人数): 名单内的陈旧声明没报出来，实得 %r" % (probs,))
        probs2 = sync_counts(members, False)
        if not any("名单外" in p for p in probs2):
            ok = False
            print("SELF-TEST FAIL(人数): 名单外的声明没报出来，实得 %r" % (probs2,))
        globals()["COUNT_EXEMPT"] = ["docs/stray.md"]
        if any("名单外" in p for p in sync_counts(members, False)):
            ok = False
            print("SELF-TEST FAIL(人数): 豁免名单没生效")
        # 声明整句被删掉：0 处命中不能算「一致」，那是门禁空转
        io.open(os.path.join(claim_dir, "live.md"), "w", encoding="utf-8", newline="").write(
            "这里不再写编制人数。" + chr(10))
        probs3 = sync_counts(members, False)
        if not any("空转" in p for p in probs3):
            ok = False
            print("SELF-TEST FAIL(人数): 声明全部消失时没报空转，实得 %r" % (probs3,))
        # W11 A1：后缀表是逃逸面。同内容换成 .txt 或干脆不带后缀，以前整段跳过。
        io.open(os.path.join(claim_dir, "stray.txt"), "w", encoding="utf-8", newline="").write(
            "调度 7 位专家。" + chr(10))
        io.open(os.path.join(claim_dir, "STATUS"), "w", encoding="utf-8", newline="").write(
            "编制 8 个人设。" + chr(10))
        globals()["COUNT_EXEMPT"] = []
        probs_a1 = sync_counts(members, False)
        if not any("stray.txt" in p and "名单外" in p for p in probs_a1):
            ok = False
            print("SELF-TEST FAIL(W11-A1): .txt 里的名单外声明没报出来，实得 %r" % (probs_a1[:2],))
        if not any("STATUS" in p and "名单外" in p for p in probs_a1):
            ok = False
            print("SELF-TEST FAIL(W11-A1): 无后缀文件里的名单外声明没报出来，实得 %r"
                  % (probs_a1[:2],))
        os.remove(os.path.join(claim_dir, "stray.txt"))
        os.remove(os.path.join(claim_dir, "STATUS"))
        # W11 A3：豁免必须是精确相对路径。endswith("profile-dsh.md") 时，
        # docs/evil-profile-dsh.md 也整文件免掉，改一个字母才报——那是给单个文件的口子。
        evil = os.path.join(claim_dir, "evil-profile-dsh.md")
        io.open(evil, "w", encoding="utf-8", newline="").write("调度 4 位专家。" + chr(10))
        if not any("evil-profile-dsh.md" in p and "名单外" in p for p in sync_counts(members, False)):
            ok = False
            print("SELF-TEST FAIL(W11-A3): 以 profile-dsh.md 结尾的冒名文件没被报出来")
        os.remove(evil)
        # W11 A5：脚本修不了的两种情况必须让 --write 也退 1。判据抽成 unfixable()，
        # 这里正向与反向都问一遍：文案一改、识别就失效，先在这里红。
        if len(unfixable(["docs/x.md: 名单外的人数声明「调度 5 位专家」"])) != 1:
            ok = False
            print("SELF-TEST FAIL(W11-A5): 名单外声明没被认成修不了的问题")
        if len(unfixable(["COUNT_FILES 里一处人数声明都没有：名单成了空转"])) != 1:
            ok = False
            print("SELF-TEST FAIL(W11-A5): 空转没被认成修不了的问题")
        if unfixable(["README.md: 「1 主理人 + 9 专家」应为 13（需 --write）"]) != []:
            ok = False
            print("SELF-TEST FAIL(W11-A5): 可修的陈旧声明被误认成修不了的问题")
        # W11 字节面：改数字不许顺手改掉 BOM 与无关行的行尾。
        # 夹具形状：BOM + 第 1 行 CRLF + 第 2 行裸 LF + 第 3 行 CRLF 且带旧数字。
        bomp = os.path.join(wd, "docs", "bom-crlf.md")
        CR, LF = chr(13), chr(10)
        body = ("标语文本 A" + CR + LF + "标语文本 B" + LF + "现有 1 主理人 + 9 专家。" + CR + LF)
        io.open(bomp, "wb").write(bytes([0xEF, 0xBB, 0xBF]) + body.encode("utf-8"))
        got = read(bomp)
        if got != body:
            ok = False
            print("SELF-TEST FAIL(W11 字节面): read 改了行尾，实得 %r" % (got[:40],))
        write(bomp, got.replace("9 专家", "13 专家"))
        raw = io.open(bomp, "rb").read()
        rows = raw.decode("utf-8-sig").split(LF)
        if raw[:3] != bytes([0xEF, 0xBB, 0xBF]):
            ok = False
            print("SELF-TEST FAIL(W11 字节面): write 把 BOM 吃掉了")
        if sum(1 for r in rows if r.endswith(CR)) != 2 or not rows[1].endswith("B"):
            ok = False
            print("SELF-TEST FAIL(W11 字节面): 混合行尾被归一化了，实得 %r" % (rows,))
        if "1 主理人 + 13 专家" not in raw.decode("utf-8-sig"):
            ok = False
            print("SELF-TEST FAIL(W11 字节面): 数字没改成，实得 %r" % (rows[2],))
        # 交付口径核对：ROOT 此刻是临时目录，所以按 REPORT_SOURCE 造一份带规范句的替身
        real_files, real_source = globals()["REPORT_FILES"], globals()["REPORT_SOURCE"]
        sp = os.path.join(wd, real_source.replace("/", os.sep))
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        io.open(sp, "w", encoding="utf-8", newline="").write(
            "规范句在此：" + REPORT_FORMULA + chr(10))
        globals()["REPORT_FILES"] = ["docs/missing-formula.md"]
        os.makedirs(os.path.join(wd, "docs"), exist_ok=True)
        mfp = os.path.join(wd, "docs", "missing-formula.md")
        io.open(mfp, "w", encoding="utf-8", newline="").write("没有那句规范句" + chr(10))
        got_f = check_report_formula()
        if not any("缺交付证据硬格式" in x for x in got_f):
            ok = False
            print("SELF-TEST FAIL(交付口径): 缺规范句没报出来，实得 %r" % (got_f[:2],))
        io.open(mfp, "w", encoding="utf-8", newline="").write(
            REPORT_FORMULA + chr(10) + REPORT_FORMULA + chr(10))
        got_f2 = check_report_formula()
        if not any("出现 2 次" in x for x in got_f2):
            ok = False
            print("SELF-TEST FAIL(交付口径): 同句写两遍没报，实得 %r" % (got_f2[:2],))
        io.open(mfp, "w", encoding="utf-8", newline="").write(REPORT_FORMULA + chr(10))
        got_f3 = check_report_formula()
        if got_f3:
            ok = False
            print("SELF-TEST FAIL(交付口径): 已经对齐了还报错，实得 %r" % (got_f3[:2],))
        globals()["REPORT_FILES"], globals()["REPORT_SOURCE"] = real_files, real_source
    except Exception as e:
        ok = False
        print("SELF-TEST FAIL(写路径): 用例没跑成 %s: %s" % (type(e).__name__, e))
    finally:
        globals()["ROOT"] = real_root
        globals()["TARGETS"] = real_targets
        globals()["ROSTER_PATH"] = real_roster
        globals()["COUNT_FILES"] = real_counts
        globals()["COUNT_EXEMPT"] = real_exempt
        shutil.rmtree(wd, ignore_errors=True)

    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

def main(argv=None):
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="从 roster.json 生成名册表格并同步人数声明")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只校验，不改文件")
    g.add_argument("--write", action="store_true", help="重写表格与人数声明")
    g.add_argument("--self-test", action="store_true", help="内置自检（CI 用）")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    try:
        members = load_roster()
    except Exception as e:
        print(f"FAIL: roster.json 读取失败: {e}")
        return 1
    errors = validate_against_agents_dir(members)          # 脚本修不了，必须手工改
    errors += check_report_formula()                        # 交付口径逐字核对
    drift = sync_tables(members, args.write, errors) + sync_counts(members, args.write)
    for e in errors:
        print("ERROR:", e)
    for d in drift:
        print("DRIFT:", d)
    if errors:
        print("结论: 有脚本修不了的错误，手工改正后重跑（--write 也不会跳过）。")
        return 1
    strays = unfixable(drift)
    if strays:
        # W11 A5：stray 与空转进的是 drift，旧写法 --write 也退 0 还打印「已修复」，
        # 与文件头「脚本修不了的问题 → --check 与 --write 都退出码 1」直接矛盾。
        # 靠这两个字样识别：自检用例钉住了它们，改文案会先红自检，耦合由 CI 兜住。
        print("结论: 名单外声明与空转两种模式都修不了，必须人工处理。")
        return 1
    if drift:
        print(f"结论: {'已修复，请复查 git diff' if args.write else '不新鲜，运行 python scripts/generate_roster_tables.py --write'}")
        return 1 if args.check else 0
    print(f"OK: roster.json（{len(members)} 人 = 1 主理人 + {len(members)-1} 专家）与文档表格、人数声明一致。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
