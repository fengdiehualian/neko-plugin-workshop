#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEKO 专家团 · SessionStart 能力底座变更检测 hook（只读 / 零依赖）

用途
  每个新会话触发一次，扫描 MCP / 连接器 / 插件 / marketplace / hooks 底座，
  与 BASELINE 指纹比对，把「有变更 / 无变更 + 差异摘要 + 当前快照」通过
  hookSpecificOutput.additionalContext 注入会话上下文。

建议存放路径
  ~/.workbuddy/hooks/session_capability_refresh.py
  （hooks/ 目录需由主理人创建；本脚本自身不创建任何目录或文件）

挂载示例（需用户确认后，由主理人写入 ~/.workbuddy/settings.json 的 hooks.SessionStart）
  {"type":"command","command":"~/.workbuddy/binaries/python/versions/3.13.12/python.exe ~/.workbuddy/hooks/session_capability_refresh.py","timeout":10}

约束
  - 只读：仅 open(..., 'r')，绝不写文件、不联网、不改配置。
  - 零第三方依赖：仅 Python 标准库（3.8+）；本机实测 3.13.14。
  - 超时安全：内部 8s 软截止（宿主 timeout 10s），超时固定降级输出，不输出部分结果。
  - 永不阻塞：任何异常均被捕获，输出合法 HookOutput JSON 并 exit 0。

维护
  python session_capability_refresh.py --self-test  # 离线自检（CI 阻塞跑）：不联网也不写文件；S10 那段跑 main()，会只读用户目录里的配置文件
  python session_capability_refresh.py --regen   # 只读打印当前指纹字典，用于刷新 BASELINE
  BASELINE 常量须与 references/capability-baseline.md 同步维护。
  已知例外: --regen 以 print 输出多行纯文本（非 HookOutput JSON）；禁止把 --regen 用于 hooks 命令。
  已知例外 2: --self-test 会在系统临时目录建两个夹具文件测 _read 的上限与 BOM，跑完即删。
    hook 自身运行路径（不带参数那次）仍然只读，绝不写文件、不建目录。

注意
  ~/.workbuddy/settings.json 才是本机 WorkBuddy 生效配置；
  ~/.codebuddy/settings.json 并存且带另一套 hindsight codebuddy-* hooks；两个配置根相互独立。
"""
import sys, os, json, hashlib, glob, time, re, unicodedata

BASELINE_DATE = "2026-11-07"
SOFT_DEADLINE = time.monotonic() + 8.0

def _safe(s, n=64):
    """S6: 所有动态值消毒为 [A-Za-z0-9._-] 并截断，避免注入不可见上下文。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:n]


def _path(s, n=200):
    """路径专用的消毒：只去掉控制字符（Cc/Cf），保留 / : 与空格。
    shortcut: 用 _safe 会把分隔符换成下划线，路径读起来像乱码，
    而这行正是要人拿去删注册的。
    """
    return "".join(ch for ch in str(s)
                   if unicodedata.category(ch) not in ("Cc", "Cf"))[:n]

ROOT = os.path.expanduser("~/.workbuddy")
CODEBUDDY = os.path.expanduser("~/.codebuddy")  # forge-declared: SEC-1 理由=按设计只读单机 CLI 配置做能力快照 出处=AUDIT.md#sec-declared 复审=2026-12-15
MKTS = ROOT + "/plugins/marketplaces"
# 这里指安装态目录，不是已退役的仓库目录 neko-plugin-ops/（见 AUDIT.md 整合记录）。
# 改指 skills/neko-plugin-sandbox 会让本项恒报 <MISSING>：那个目录没有 .codebuddy-plugin 清单。
# BASELINE 同理是作者单机指纹（neko-sources.md 第 6 节标为「不得当跨机器结论」）。
NEKO = MKTS + "/my-experts/plugins/neko-plugin-ops"
HINDSIGHT = os.path.expanduser("~/.hindsight/coding-agents/dist")

BASELINE = {
    "scalars": {
        "mcp.workbuddy":          "7d7adece4903",
        "mcp.catalog":            "72896b8dcc73",
        "mcp.catalog.count":      208,
        "mcp.skill.lexiang":      "c891097aa218",
        "settings.workbuddy":     "02fb107c4263",
        "settings.codebuddy":     "684c0eef3067",
        "plugin.neko-plugin-ops": "2689e205c4d0",
        "backups.workbuddy":      2,
        "backups.codebuddy":      2,
    },
    "marketplaces": {
        "cb_teams_marketplace":                  {"fp": "e2cf94afd6bb", "plugins": 29},
        "codebuddy-plugins-official":           {"fp": "46c87e0afe88", "plugins": 206},
        "experts":                              {"fp": "d19afc65e7c6", "plugins": 5},
        "my-experts":                           {"fp": "e5c3984b0016", "plugins": 3},
        "workbuddy-connector-plugins-official": {"fp": "2e6c4f72e6e1", "plugins": 350},
        "workbuddy-wecom-unified":              {"fp": "36af02a124f3", "plugins": 1},
    },
    "marketplace_dirs": ["cb_teams_marketplace", "codebuddy-plugins-official", "experts",
                         "my-experts", "plugin-data", "workbuddy-connector-plugins-official",
                         "workbuddy-wecom-unified"],
    "skills_count": 69,
    "skills_fp": "2e24b30b2315",
}

MCP_KEYS = ("type", "url", "command", "args", "timeout", "disabled", "env", "headers",
            "transportType", "enabled", "staticHeaders")

# 本工程自己要确保在位的 hook 脚本。
# 2026-11-18 工单 W12 选 B：Hindsight 那七条从这里删掉。整目录
# `~/.hindsight/coding-agents` 已不存在（实测 `os.path.isdir` 为假），
# 列在这里就是把「别人的组件没装」报成本工程的「Hook 脚本缺失」，永远修不掉。
HOOK_SCRIPTS = [
    ROOT + "/skills/haloflux-lab/scripts/session-beacon.mjs",
]

# 脚本文件路径出现在 hook 命令里的样子。node 与 python 两种写法都覆盖：
# 引号包住的与裸写的都算，所以先把引号去掉，再取以 .js/.mjs/.cjs/.py 结尾的整段路径。
HOOK_TARGET_RE = re.compile(r"([\w./:\-]+\.(?:js|mjs|cjs|py))")


def dangling_hooks(settings, exists=None):
    """settings.json 真注册了什么，就去查那个文件在不在。

    以前的判据只看一份手写名单，看不见「注册指向已删文件」这一类。
    这类是真缺陷：每次会话这三个事件都会起 node 打空（工单 W12 实测 6 条）。
    `settings` 是 [(路径, 解析后的字典), ...]，由调用方给；`exists` 是注入接缝，
    与 build_context 用同一个，自检才能不碰真机。
    """
    exists = exists or os.path.isfile
    out = []
    for path, data in settings:
        for event, groups in (data.get("hooks") or {}).items():
            for grp in groups or []:
                for hook in (grp.get("hooks") or []):
                    # Windows 命令里全是反斜杠，而反斜杠不在路径字符类里：
                    # 不归一化就只能匹配到最后一段文件名，读者看不出该删哪条注册。
                    cmd = str(hook.get("command") or "").replace("\\", "/")
                    for m in HOOK_TARGET_RE.finditer(cmd):
                        tgt = m.group(1).replace("\\", "/")
                        if not exists(os.path.expanduser(tgt)):
                            root_label = os.path.basename(
                                os.path.dirname(path).rstrip("/")).lstrip(".") or "?"
                            short = tgt.replace(os.path.expanduser("~/"), "~/")
                            out.append("%s 的 %s 指向不存在的文件：%s"
                                       % (root_label, event, short[:160]))
    return out

def _norm(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

def _fp(o):
    return hashlib.sha256(_norm(o).encode("utf-8")).hexdigest()[:12]

# 单文件读取上限。以前是 256 KiB，实测把 workbuddy-connector-plugins-official 的清单
#（1 498 928 B、964 项，2026-11-18 现数）判成 `<UNREADABLE>`。
# 那是合法大数据，不是坏文件；报成读不动反而把真实漂移盖住了。
MAX_READ_BYTES = 8 * 1024 * 1024

def _read(p):
    if os.path.getsize(p) > MAX_READ_BYTES:
        raise ValueError("file too large")
    with open(p, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def _ext_mcp(d):
    return {k: {kk: vv for kk, vv in sorted(v.items()) if kk in MCP_KEYS}
            for k, v in sorted(d.get("mcpServers", {}).items())}

def _ext_settings(d):
    return {"hooks": d.get("hooks", {}), "enabledPlugins": d.get("enabledPlugins", {})}

def _ext_mkt(d):
    return {"name": d.get("name"),
            "plugins": [{"name": p.get("name"), "version": p.get("version"),
                         "source": p.get("source")} for p in d.get("plugins", [])]}

def _ext_plugin(d):
    return {"version": d.get("version"), "agents": d.get("agents", []),
            "teamInfo": d.get("teamInfo", {}), "expertType": d.get("expertType")}

def _step(label):
    if time.monotonic() > SOFT_DEADLINE:
        raise TimeoutError(label)

def collect_fingerprints():
    """只读扫描，返回 (当前指纹字典, 是否截断, 读取异常列表)。"""
    cur = {"scalars": {}, "marketplaces": {}, "marketplace_dirs": [],
           "skills_count": 0, "skills_fp": ""}
    notes, truncated = [], False
    try:
        targets = [
            ("mcp.workbuddy",          ROOT + "/mcp.json",                                   _ext_mcp),
            ("mcp.catalog",            ROOT + "/connectors/default/mcp.json",             _ext_mcp),
            ("mcp.skill.lexiang",      ROOT + "/skills/lexiang-knowledge-base/mcp.json",  _ext_mcp),
            ("settings.workbuddy",     ROOT + "/settings.json",                           _ext_settings),
            ("settings.codebuddy",     CODEBUDDY + "/settings.json",                      _ext_settings),
            ("plugin.neko-plugin-ops", NEKO + "/.codebuddy-plugin/plugin.json",           _ext_plugin),
        ]
        for key, path, fn in targets:
            _step(key)
            if not os.path.isfile(path):
                cur["scalars"][key] = "<MISSING>"
                notes.append("路径缺失: " + _safe(path.replace(os.path.expanduser("~/"), "~/")))
                continue
            try:
                cur["scalars"][key] = _fp(fn(_read(path)))
            except Exception as e:
                cur["scalars"][key] = "<UNREADABLE>"
                notes.append("解析失败 %s: %s" % (_safe(path.replace(os.path.expanduser("~/"), "~/")), _safe(type(e).__name__)))
        _step("mcp.catalog.count")
        try:
            cur["scalars"]["mcp.catalog.count"] = len(
                _read(ROOT + "/connectors/default/mcp.json").get("mcpServers", {}))
        except Exception:
            cur["scalars"]["mcp.catalog.count"] = -1
        _step("marketplaces")
        for f in sorted(glob.glob(MKTS + "/*/.codebuddy-plugin/marketplace.json")):
            _step(f)
            name = os.path.basename(os.path.dirname(os.path.dirname(f)))
            try:
                d = _read(f)
                cur["marketplaces"][name] = {"fp": _fp(_ext_mkt(d)), "plugins": len(d.get("plugins", []))}
            except Exception:
                cur["marketplaces"][name] = {"fp": "<UNREADABLE>", "plugins": -1}
        _step("marketplace_dirs")
        cur["marketplace_dirs"] = sorted(
            os.path.basename(x) for x in glob.glob(MKTS + "/*") if os.path.isdir(x))
        _step("skills")
        sk = sorted(os.path.basename(x) for x in glob.glob(ROOT + "/skills/*") if os.path.isdir(x))
        cur["skills_count"] = len(sk)
        cur["skills_fp"] = _fp(sk)
        _step("backups")
        try:
            cur["scalars"]["backups.workbuddy"] = sum(
                1 for n in os.listdir(ROOT) if n.endswith(".hindsight-backup"))
            cur["scalars"]["backups.codebuddy"] = sum(
                1 for n in os.listdir(CODEBUDDY) if n.endswith(".hindsight-backup"))
        except Exception:
            cur["scalars"]["backups.workbuddy"] = -1
            cur["scalars"]["backups.codebuddy"] = -1
    except TimeoutError as e:
        truncated = True
        notes.append("内部 8s 软截止触发，剩余检测跳过: " + _safe(str(e)))
    return cur, truncated, notes

def build_context(cur, notes, read=None, exists=None):
    """把当前指纹与 BASELINE 比成一段上下文文本。

    read / exists 是两个注入接缝（与 _posix_cmdline、_wait_gone 同一口径）：不注入就读真机，
    注入就能在 CI 里离线跑满全部差异形状。2026-11-17 起本函数不再收 truncated 形参——
    函数体从没读过它，降级判断在 main 里做。
    """
    read = read or _read
    exists = exists or os.path.isfile
    diffs, snap = [], []
    for k, base in BASELINE["scalars"].items():
        c = cur["scalars"].get(k, "<NOT_SCANNED>")
        if c == "<NOT_SCANNED>":
            continue
        if c != base:
            diffs.append("  - %s: 基线 %s -> 当前 %s" % (k, base, c))
    bm, cm = BASELINE["marketplaces"], cur["marketplaces"]
    for n in sorted(set(bm) | set(cm)):
        if n not in cm:
            diffs.append("  - marketplace 清单删除: " + _fp(n))
        elif n not in bm:
            diffs.append("  - marketplace 清单新增: " + _fp(n))
        elif cm[n] != bm[n]:
            diffs.append("  - marketplace 变更: %s %s -> %s" % (_fp(n), bm[n], cm[n]))
    bd, cd = set(BASELINE["marketplace_dirs"]), set(cur["marketplace_dirs"])
    for n in sorted(cd - bd):
        diffs.append("  - 新增 marketplace 目录: " + _fp(n))
    for n in sorted(bd - cd):
        diffs.append("  - 删除 marketplace 目录: " + _fp(n))
    if cur["skills_count"] != BASELINE["skills_count"]:
        diffs.append("  - skills 数量: %s -> %s" % (BASELINE["skills_count"], cur["skills_count"]))
    if cur["skills_fp"] and cur["skills_fp"] != BASELINE["skills_fp"]:
        diffs.append("  - skills 清单指纹变化（增删技能；清单由 Task A 维护）")
    missing = [p for p in HOOK_SCRIPTS if not exists(p)]
    for p in missing:
        diffs.append("  - Hook 脚本缺失: " + _safe(p.replace(os.path.expanduser("~/"), "~/")))
    # 注册悬空：settings.json 里还挂着指向已删文件的命令（工单 W12 那一类）
    regs = []
    for root in (ROOT, CODEBUDDY):
        p = root + "/settings.json"
        try:
            v = read(p)
        except Exception:
            v = None
        if isinstance(v, dict):
            regs.append((p, v))
    dangling = dangling_hooks([(p, v) for p, v in regs if isinstance(v, dict)], exists)
    if dangling:
        diffs.append("  - Hook 注册悬空 %d 条（注册指向不存在的文件，每次会话起进程打空）"
                     % len(dangling))
        for d in dangling[:6]:
            # 这一行要给人拿去删注册，用 _safe 会把 / : 空格全换成下划线，读起来像乱码
            diffs.append("      - " + _path(d))
    if len(dangling) > 6:
        diffs.append("      ...另有 %d 条，见 settings.json" % (len(dangling) - 6))

    try:
        ms = read(ROOT + "/mcp.json").get("mcpServers", {})
        en = [k for k, v in ms.items() if not v.get("disabled", False)]
        dis = [k for k, v in ms.items() if v.get("disabled", False)]
        snap.append("MCP 主配置 %d 项 | 启用 %d 项 (fp %s) | 禁用 %d 项 (fp %s)"
                    % (len(ms), len(en), _fp(sorted(en)), len(dis), _fp(sorted(dis))))
    except Exception:
        snap.append("MCP 主配置快照不可用")
    snap.append("连接器目录模板 %s 项（全 disabled；运行时启用状态未在本快照核验）"
                % cur["scalars"].get("mcp.catalog.count", "?"))
    try:
        h = read(ROOT + "/settings.json").get("hooks", {})
        ncmd = sum(len(e.get("hooks", [])) for g in h.values() for e in g)
        snap.append("生效配置 ~/.workbuddy/settings.json: hooks %d 事件 / %d 条命令；~/.codebuddy/settings.json 另有另一套 hindsight codebuddy-* 并存" % (len(h), ncmd))
    except Exception:
        snap.append("settings 快照不可用")
    snap.append("marketplaces: %d 目录 / %d 份清单（plugin-data 为数据目录，无清单）"
                % (len(cur["marketplace_dirs"]), len(cur["marketplaces"])))
    try:
        snap.append("专家团插件 neko-plugin-ops v%s（my-experts 共 %s 个插件）"
                    % (_safe(read(NEKO + "/.codebuddy-plugin/plugin.json").get("version", "?"), 16),
                       _safe(cur["marketplaces"].get("my-experts", {}).get("plugins", "?"), 8)))
    except Exception:
        snap.append("插件版本快照不可用")
    snap.append("skills: %d 个（清单由 Task A / neko-skill-ops 维护）" % cur["skills_count"])
    snap.append("Hook 脚本就绪: %d/%d" % (len(HOOK_SCRIPTS) - len(missing), len(HOOK_SCRIPTS)))

    lines = ["[NEKO 能力底座 · 会话启动检测] 基线 " + BASELINE_DATE,
             "结论: " + ("有变更（%d 项）" % len(diffs) if diffs
                        else "无变更（基线一致，最后核验 " + BASELINE_DATE + "）")]
    lines.append("当前快照:")
    for s in snap:
        lines.append("  · " + s)
    lines.append("差异:")
    lines.extend(diffs[:20] if diffs else ["  - 无（与基线一致）"])
    if len(diffs) > 20:
        lines.append("  - ...另有 %d 项，见完整扫描" % (len(diffs) - 20))
    if notes:
        lines.append("读取异常:")
        for n in notes[:8]:
            lines.append("  - " + re.sub(r"[\x00-\x1f\x7f]", "_", str(n))[:200])
    lines.append("证据: 本机文件只读 [FACT/LOCAL]；连接器运行时状态、skill 远程版本、官方文档更新均为 [未联网核验]，不在本机文件只读范围内。")
    if diffs:
        lines.append("差异为只读观测结果；基线由主理人维护；本脚本不安装或更新第三方组件。")
    ctx = "\n".join(lines)
    if len(ctx) > 2000:
        marker = "\ntruncated=1"
        ctx = ctx[:2000 - len(marker)] + marker
    return ctx

FALLBACK_CONTEXT = "本次检测不可用。本 hook 不写任何文件。"

def emit(context):
    # S8 精确形态：顶层仅 hookSpecificOutput，不写任何用户可见的第二通道。
    payload = {
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context},
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()

def _fp_of(obj, extractor):
    """辅助：同一个抽取器对两个不同内容算出的指纹必须不同。"""
    return _fp(extractor(obj))


def _self_test():
    """离线自检：不联网、不写文件。大部分用例走注入接缝，但 S10 那一段跑 main()，
    它的 build_context 用的是真实 ROOT，会只读 ~/.workbuddy 与 ~/.codebuddy 下的配置文件
    （2026-11-18 用 open() 探针实测到 3 个真实路径）。以前的口径说自检完全不碰用户目录，那句是错的。

    2026-11-17 加。这个 hook 每个会话都跑，以前 CI 只做 py_compile，等于零覆盖。
    重点钉三条：S8 的输出形态、S10 的永不阻塞、以及差异文本的截断。
    自检只在系统临时目录里建两个夹具文件（测 _read 的上限与 BOM），hook 的运行路径照旧一个文件都不写。
    """
    import contextlib
    import io

    bad = []
    n = [0]

    def chk(label, cond):
        n[0] += 1
        if not cond:
            bad.append(label)

    # 1) 消毒与指纹
    chk("_safe 把空白与斜杠压成下划线", _safe("a b/c\td") == "a_b_c_d")
    chk("_safe 截到 n 个字符", len(_safe("x" * 200, 64)) == 64)
    chk("_norm 与键序无关", _norm({"b": 1, "a": 2}) == _norm({"a": 2, "b": 1}))
    chk("_fp 长度 12", len(_fp({"a": 1})) == 12)
    chk("_fp 同输入同值", _fp({"a": [1, 2]}) == _fp({"a": [1, 2]}))
    chk("_fp 分辨值变化", _fp({"a": 1}) != _fp({"a": 2}))

    # 2) 四个抽取器只取该取的键，多余字段不许混进指纹
    mcp = _ext_mcp({"mcpServers": {"z": {"url": "u", "extra": 1}, "a": {"command": "c"}}})
    chk("_ext_mcp 按字典序且丢掉清单外的键",
        list(mcp) == ["a", "z"] and "extra" not in mcp["z"])
    st = _ext_settings({"hooks": {"H": 1}, "enabledPlugins": {"p": True}, "theme": "x"})
    # 整值比较，不比较键集合：只查键的话，把值抽干成 {} 也照样「通过」，
    # 而 hook 集合正是这个监控器唯一要看的东西（工单 W04）
    chk("_ext_settings 留两项且值原样带出",
        st == {"hooks": {"H": 1}, "enabledPlugins": {"p": True}})
    chk("_ext_settings 抽干值要能被发现",
        _fp_of({"hooks": {"S": [{"hooks": [{"command": "evil"}]}]}}, _ext_settings)
        != _fp_of({"hooks": {}}, _ext_settings))
    mk = _ext_mkt({"name": "m", "extra": 2,
                   "plugins": [{"name": "p", "version": "1", "source": "s", "junk": 1}]})
    chk("_ext_mkt 只留 name 与插件三字段",
        mk == {"name": "m", "plugins": [{"name": "p", "version": "1", "source": "s"}]})
    pl = _ext_plugin({"version": "1", "agents": ["a"], "teamInfo": {}, "expertType": "t", "other": 9})
    chk("_ext_plugin 只留四字段", sorted(pl) == ["agents", "expertType", "teamInfo", "version"])

    # 3) _read 的两条：256KB 上限与 BOM。夹具写在系统临时目录里，脚本运行路径仍一个文件都不写。
    #    用临时夹具而不是仓库里的 roster.json：自检要能在任何一份拷贝里跑，包括装机面与副本。
    import shutil
    import tempfile
    td = tempfile.mkdtemp(prefix="cap-self-")
    try:
        big = os.path.join(td, "big.json")
        with open(big, "wb") as f:
            f.write(b'{"a":"' + b"x" * (MAX_READ_BYTES + 1) + b'"}')
        refused = False
        try:
            _read(big)
        except ValueError:
            refused = True
        chk("_read 拒绝超过上限的文件", refused)
        # 上限不能拍太小：connector-plugins-official 的清单实测 1 498 928 B（964 项），
        # 旧的 256 KiB 把它判成读不动，于是每一项差异都变成假信号
        mid = os.path.join(td, "mid.json")
        with open(mid, "wb") as f:
            f.write(b'{"plugins": [' + b'{"a":1},' * 190000 + b'{"a":1}]}')
        chk("1.5MB 量级的合法清单要读得动",
            os.path.getsize(mid) > 256 * 1024 and isinstance(
                _read(mid).get("plugins"), list))
        small = os.path.join(td, "small.json")
        with open(small, "wb") as f:
            f.write("﻿".encode("utf-8") + b'{"members": [{"agent_id": "x"}]}')
        chk("_read 吃下 BOM 并解析", isinstance(_read(small).get("members"), list))
        missing_ok = False
        try:
            _read(os.path.join(td, "nope.json"))
        except OSError:
            missing_ok = True
        chk("_read 缺文件时抛 OSError，交给调用方记 <UNREADABLE>", missing_ok)
    except OSError as e:
        chk("_read 那组夹具没跑成（%s）" % type(e).__name__, False)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    # 4) build_context 的差异形状，全走注入接缝
    base = {"scalars": dict(BASELINE["scalars"]),
            "marketplaces": dict(BASELINE["marketplaces"]),
            "marketplace_dirs": list(BASELINE["marketplace_dirs"]),
            "skills_count": BASELINE["skills_count"],
            "skills_fp": BASELINE["skills_fp"]}
    ok_read = lambda p: {"mcpServers": {}, "hooks": {}, "version": "1.6.0"}
    all_there = lambda p: True

    def cur(**kw):
        d = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
             for k, v in base.items()}
        d.update(kw)
        return d

    ctx = build_context(base, [], read=ok_read, exists=all_there)
    chk("基线一致时说无变更", "无变更（基线一致" in ctx)
    chk("无变更时也给出最后核验日期", BASELINE_DATE in ctx)
    chk("基线一致时差异段写无", "无（与基线一致）" in ctx)

    ctx = build_context(cur(scalars=dict(base["scalars"], **{"mcp.workbuddy": "deadbeef0000"})),
                        [], read=ok_read, exists=all_there)
    chk("标量变更报出基线值与当前值",
        "mcp.workbuddy: 基线 %s -> 当前 deadbeef0000" % BASELINE["scalars"]["mcp.workbuddy"] in ctx)

    mp = dict(base["marketplaces"])
    mp["brand_new"] = {"fp": "fff", "plugins": 1}
    del mp["experts"]
    mp["my-experts"] = {"fp": "e5c3984b0016", "plugins": 4}
    ctx = build_context(cur(marketplaces=mp,
                            marketplace_dirs=base["marketplace_dirs"] + ["zzz_dir"],
                            skills_count=BASELINE["skills_count"] + 3,
                            skills_fp="0" * 12),
                        [], read=ok_read, exists=all_there)
    for kind in ("marketplace 清单新增", "marketplace 清单删除", "marketplace 变更",
                 "新增 marketplace 目录", "skills 数量", "skills 清单指纹变化"):
        chk("差异种类 " + kind, kind in ctx)
    chk("没删目录就不报删目录", "删除 marketplace 目录" not in ctx)

    ctx = build_context(cur(marketplace_dirs=[d for d in base["marketplace_dirs"]
                                              if d != "plugin-data"]),
                        [], read=ok_read, exists=all_there)
    chk("删掉的目录要报删除", "删除 marketplace 目录" in ctx)

    # 5) Hook 脚本缺失：单独成行，就绪计数跟着掉。
    #    名单现在只剩本工程自己那一条，夹具就撤这一条。
    half = lambda p: not p.endswith("session-beacon.mjs")
    ctx = build_context(base, [], read=ok_read, exists=half)
    chk("缺失的 Hook 脚本要单列", "Hook 脚本缺失" in ctx)
    chk("就绪计数按缺失扣减",
        "Hook 脚本就绪: %d/%d" % (len(HOOK_SCRIPTS) - 1, len(HOOK_SCRIPTS)) in ctx)
    # 5b) 注册悬空（工单 W12 选 B 后新增的那一类）：夹具喂 settings 字典，不碰真机
    fake_root = "~/wb/settings.json"
    set_wb = {"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": 'node "~/.hindsight/coding-agents/dist/gone.js"'}]}],
        "Stop": [{"hooks": [{"type": "command",
                             "command": '"C:/x/python.exe" "~/.workbuddy/hooks/keep.py"'}]}]}}
    # Windows 形状的注册命令全是反斜杠。不归一化就只能匹配到最后一段文件名，
    # 报出来的行没法拿去对照 settings.json（反证 33 第三臂实测这条当时没有判据覆盖）
    B = chr(92)
    win_cmd = ("node " + chr(34) + "C:" + B + "Users" + B + "me" + B + ".hindsight" + B
               + "coding-agents" + B + "dist" + B + "workbuddy-sessionstart-hook.js" + chr(34))
    win = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": win_cmd}]}]}}
    dw = dangling_hooks([("~/wb/settings.json", win)], lambda p: False)
    chk("反斜杠路径也要认出来", len(dw) == 1)
    chk("报出来的路径要保留目录部分", bool(dw) and "coding-agents" in dw[0] and B not in dw[0])
    dk = dangling_hooks([(fake_root, set_wb)], lambda p: p.endswith("keep.py"))
    chk("悬空注册要被报出来", len(dk) == 1 and "SessionStart" in dk[0])
    chk("在位的注册不许误报", all("keep.py" not in x for x in dk))
    ctx2 = build_context(base, [], read=lambda p: ({"hooks": set_wb["hooks"]}, None)[0],
                         exists=lambda p: False)
    chk("注册悬空要进差异", "Hook 注册悬空" in ctx2)
    # 外部组件缺席不再算本工程缺陷：HOOK_SCRIPTS 里不该再有 hindsight 路径
    chk("Hindsight 路径不再进本工程名单",
        not any("hindsight" in p.lower() for p in HOOK_SCRIPTS))

    # 6) 快照取不到时降级成说明，不许抛
    def boom(p):
        raise OSError("取不到")

    ctx = build_context(base, [], read=boom, exists=all_there)
    for kind in ("MCP 主配置快照不可用", "settings 快照不可用", "插件版本快照不可用"):
        chk("降级句 " + kind, kind in ctx)
    chk("连接器目录模板那句照常输出", "连接器目录模板" in ctx)

    # 7) 截断：差异超过 20 条只列 20 条，整段超 2000 字符要带 truncated=1
    many_scalars = {k: "v%02d%s" % (i, "f" * 10) for i, k in enumerate(BASELINE["scalars"])}
    many_mp = {("mk%03d" % i): {"fp": "f" * 12, "plugins": i} for i in range(40)}
    ctx = build_context(cur(scalars=many_scalars, marketplaces=many_mp,
                            marketplace_dirs=[], skills_count=-1, skills_fp=""),
                        ["读取异常甲", "带\n换行的异常"], read=ok_read, exists=all_there)
    chk("差异超过 20 条只列 20 条", "另有 43 项" in ctx and len(ctx) <= 2000)
    chk("读取异常里的裸换行被消毒", "带\n换行的异常" not in ctx.split("读取异常:")[-1])
    # 整段超 2000 字符是另一条独立护栏：用足够长的读取异常把它顶过去
    long_notes = ["异常" + ("详" * 300) + chr(10) + "尾%d" % i for i in range(9)]
    ctx2 = build_context(cur(scalars=many_scalars, marketplaces=many_mp,
                             marketplace_dirs=[], skills_count=-1, skills_fp=""),
                         long_notes, read=ok_read, exists=all_there)
    chk("整段超 2000 字符时截到 2000 并打标记", len(ctx2) <= 2000 and ctx2.endswith("truncated=1"))
    # 条数上限 notes[:8] 是另一条独立护栏。用短异常 + 无差异基线，
    # 免得被上面 2000 字那条护栏抢先截断；旧写法 ctx2.count(...) <= 8 只列 3 条也为真（工单 O2）
    ctx3 = build_context(base, ["异常详%d" % i for i in range(9)],
                         read=ok_read, exists=all_there)
    chk("异常正好列 8 条、第 9 条不许出现",
        ctx3.count("异常详") == 8 and "异常详8" not in ctx3)
    chk("证据行说明未联网核验", "[未联网核验]" in ctx)
    chk("有差异时补一句只读观测", "差异为只读观测结果" in ctx)

    # 8) S8 输出形态：顶层只有 hookSpecificOutput，且必须是合法 JSON
    real_out = sys.stdout
    buf = io.StringIO()
    try:
        sys.stdout = buf
        emit("多行\n上下文 中文")
    finally:
        sys.stdout = real_out
    line = buf.getvalue()
    try:
        got = json.loads(line)
    except ValueError:
        got = None
    chk("emit 输出是合法单行 JSON", isinstance(got, dict) and line.count(chr(10)) == 1)
    chk("S8 顶层只有 hookSpecificOutput", isinstance(got, dict) and list(got) == ["hookSpecificOutput"])
    ho = (got or {}).get("hookSpecificOutput", {})
    chk("事件名是 SessionStart", ho.get("hookEventName") == "SessionStart")
    chk("additionalContext 原样带出", ho.get("additionalContext") == "多行\n上下文 中文")
    chk("ensure_ascii 生效，字节里没有裸中文", all(ord(c) < 128 for c in line))

    # 9) S10：扫描炸了也要合法 JSON 且 exit 0。stdin 要换掉，CI 里没人关管道会挂住
    real_collect, real_argv, real_stdin = collect_fingerprints, sys.argv, sys.stdin

    def explode():
        raise RuntimeError("扫描器炸了")

    class _NoStdin(object):
        @staticmethod
        def read():
            return ""

    try:
        globals()["collect_fingerprints"] = explode
        sys.argv = ["session_capability_refresh.py"]
        sys.stdin = _NoStdin
        buf = io.StringIO()
        sys.stdout = buf
        try:
            rc = main()
        finally:
            sys.stdout = real_out
        chk("异常路径退 0", rc == 0)
        chk("异常路径给降级文本",
            json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"] == FALLBACK_CONTEXT)
        chk("降级文本声明不写文件", "不写任何文件" in FALLBACK_CONTEXT)

        globals()["collect_fingerprints"] = lambda: (base, True, ["部分结果"])
        buf = io.StringIO()
        sys.stdout = buf
        try:
            rc = main()
        finally:
            sys.stdout = real_out
        chk("软截止路径退 0", rc == 0)
        chk("截止时不输出部分结果",
            json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"] == FALLBACK_CONTEXT)

        globals()["collect_fingerprints"] = lambda: (base, False, [])
        buf = io.StringIO()
        sys.stdout = buf
        try:
            rc = main()
        finally:
            sys.stdout = real_out
        chk("正常路径退 0", rc == 0)
        chk("正常路径输出完整上下文",
            "会话启动检测" in json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"])
    finally:
        globals()["collect_fingerprints"] = real_collect
        sys.argv = real_argv
        sys.stdin = real_stdin

    # 6) collect_fingerprints 的扫描分支。以前自检把真扫描整个跳过了：
    #    缺文件、坏 JSON、软截止、marketplace 计数这五条路一次都没跑过（终检 sweep 第 11 条）。
    #    这里把五个路径常量指到临时夹具，跑真函数，跑完原样换回来。
    sd = tempfile.mkdtemp(prefix="cap-scan-")
    real_paths = (ROOT, CODEBUDDY, MKTS, NEKO, SOFT_DEADLINE)
    try:
        wb = os.path.join(sd, ".workbuddy")
        cb = os.path.join(sd, ".codebuddy")
        mkt = os.path.join(wb, "plugins", "marketplaces")

        def _mk(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        _mk(os.path.join(wb, "mcp.json"), '{"mcpServers": {"a": {"command": "x", "env": {"K": "v"}}}}')
        _mk(os.path.join(wb, "connectors", "default", "mcp.json"),
            '{"mcpServers": {"a": {}, "b": {}}}')
        _mk(os.path.join(wb, "settings.json"), '{"hooks": {"X": []}, "enabledPlugins": {}}')
        _mk(os.path.join(cb, "settings.json"), '{"hooks": {}}')
        _mk(os.path.join(mkt, "demo", ".codebuddy-plugin", "marketplace.json"),
            '{"version": "1", "plugins": [{"id": "p1"}, {"id": "p2"}]}')
        _mk(os.path.join(mkt, "demo", "plugins", "neko-plugin-ops", ".codebuddy-plugin",
                         "plugin.json"), '{"name": "neko-plugin-ops", "version": "1"}')
        os.makedirs(os.path.join(wb, "skills", "one"), exist_ok=True)
        os.makedirs(os.path.join(wb, "skills", "two"), exist_ok=True)
        _mk(os.path.join(wb, "x.hindsight-backup"), "{}")

        globals()["ROOT"], globals()["CODEBUDDY"] = wb, cb
        globals()["MKTS"], globals()["NEKO"] = mkt, os.path.join(mkt, "demo", "plugins", "neko-plugin-ops")
        globals()["SOFT_DEADLINE"] = time.monotonic() + 30
        cur, trunc, notes = collect_fingerprints()
        chk("扫描: 有 mcp 指纹", len(cur["scalars"]["mcp.workbuddy"]) == 12)
        chk("扫描: 缺文件记 <MISSING>", cur["scalars"]["mcp.skill.lexiang"] == "<MISSING>")
        chk("扫描: 缺文件要进 notes", any("路径缺失" in x for x in notes))
        chk("扫描: truncated 不该被触发", trunc is False)
        chk("扫描: catalog 计数", cur["scalars"]["mcp.catalog.count"] == 2)
        chk("扫描: marketplace 插件数", cur["marketplaces"]["demo"]["plugins"] == 2)
        chk("扫描: marketplace 目录", cur["marketplace_dirs"] == ["demo"])
        chk("扫描: skills 计数", cur["skills_count"] == 2)
        chk("扫描: 备份计数", cur["scalars"]["backups.workbuddy"] == 1)
        chk("扫描: 清单指纹", len(cur["scalars"]["plugin.neko-plugin-ops"]) == 12)

        # 值穿透：每个受控目标改一次内容，对应指纹必须变。
        # 这一组是工单 W04 的正证——旧自检只查键名，把 hooks 的值换成 {} 也全绿。
        prev = dict(cur["scalars"])
        prev_mkt = json.dumps(cur["marketplaces"], sort_keys=True)
        steps = [
            (os.path.join(wb, "settings.json"),
             '{"hooks": {"X": [], "NEW": [{"hooks": [{"command": "evil"}]}]},'
             ' "enabledPlugins": {}}', "settings.workbuddy"),
            (os.path.join(wb, "mcp.json"),
             '{"mcpServers": {"a": {"command": "x", "env": {"K": "v"}},'
             ' "b": {"url": "http://127.0.0.1:9/m"}}}', "mcp.workbuddy"),
            # 连接器目录加一个服务器：计数必须跟着动，不然说明目标指错了文件
            (os.path.join(wb, "connectors", "default", "mcp.json"),
             '{"mcpServers": {"a": {"disabled": true}, "b": {}, "c": {}}}',
             "mcp.catalog.count"),
            (os.path.join(mkt, "demo", ".codebuddy-plugin", "marketplace.json"),
             '{"version": "1", "plugins": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]}',
             "__MARKETPLACES__"),
            (os.path.join(mkt, "demo", "plugins", "neko-plugin-ops", ".codebuddy-plugin",
                          "plugin.json"), '{"name": "neko-plugin-ops", "version": "2"}',
             "plugin.neko-plugin-ops"),
        ]
        for path, body, key in steps:
            _mk(path, body)
            cur_x, _tx, _nx = collect_fingerprints()
            if key == "__MARKETPLACES__":
                now_mkt = json.dumps(cur_x["marketplaces"], sort_keys=True)
                chk("值穿透: 改 marketplace 清单要反映到台账", now_mkt != prev_mkt)
                prev_mkt = now_mkt
            else:
                chk("值穿透: 改 %s 的内容必须动 %s"
                    % (os.path.basename(path), key),
                    cur_x["scalars"].get(key) != prev.get(key))
                prev = dict(cur_x["scalars"])

        # 坏 JSON：解析失败要记 <UNREADABLE> 并留一条说明，不许抛出去。
        # catalog 计数走另一个文件：它坏了要记 -1，也不能把整轮扫描带崩。
        _mk(os.path.join(wb, "settings.json"), "{不是 JSON")
        _mk(os.path.join(wb, "connectors", "default", "mcp.json"), "{也不是 JSON")
        cur2, _t2, notes2 = collect_fingerprints()
        chk("扫描: 坏 JSON 记 <UNREADABLE>", cur2["scalars"]["settings.workbuddy"] == "<UNREADABLE>")
        chk("扫描: 坏 JSON 有说明", any("解析失败" in x for x in notes2))
        chk("扫描: catalog 坏了记 -1", cur2["scalars"]["mcp.catalog.count"] == -1)
        chk("扫描: 一处坏了不拦其余检测", len(cur2["scalars"]) == len(cur["scalars"]))

        # 软截止：跳过剩余检测并标记 truncated，主路径靠这个标记走降级文本
        globals()["SOFT_DEADLINE"] = time.monotonic() - 1
        _t3, trunc3, notes3 = collect_fingerprints()
        chk("扫描: 软截止标记 truncated", trunc3 is True)
        chk("扫描: 软截止有说明", any("软截止" in x for x in notes3))

        # 配置根整个不存在：backups 走 -1，不能抛
        globals()["SOFT_DEADLINE"] = time.monotonic() + 30
        globals()["CODEBUDDY"] = os.path.join(sd, "nope")
        _t4, _t4b, notes4 = collect_fingerprints()
        chk("扫描: CODEBUDDY 缺失时 backups 记 -1", _t4["scalars"]["backups.codebuddy"] == -1)
    except OSError as e:
        bad.append("扫描夹具没跑成：" + type(e).__name__)
    finally:
        (globals()["ROOT"], globals()["CODEBUDDY"], globals()["MKTS"],
         globals()["NEKO"], globals()["SOFT_DEADLINE"]) = real_paths
        shutil.rmtree(sd, ignore_errors=True)

    print("能力刷新自检 %d 项：%s" % (n[0], "全部通过" if not bad else "%d 项失败" % len(bad)))
    for m in bad:
        print("  FAIL:", m)
    return 0 if not bad else 1


def main():
    # 自检必须在下面那个大 try 之外：S10 的兜底是「hook 绝不能把会话拦住」，
    # 但它也会把自检的异常吃成 exit 0——CI 那一步于是永远绿（终检 sweep 第 16 条）。
    # 自检不是会话路径，它失败就是要红。
    if "--self-test" in sys.argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return _self_test()
    try:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        # --regen 也必须先于 stdin 读取：常在交互式终端手动执行，
        # 若先 sys.stdin.read() 会因等待 EOF 而挂起（宿主/终端未关闭 stdin）。
        if "--regen" in sys.argv:
            cur, _, _ = collect_fingerprints()
            print(json.dumps(cur, indent=2, ensure_ascii=False))
            return 0
        # 读而不解：本 hook 不使用 payload 里任何字段，读的目的只是把 stdin 排空，
        # 免得宿主等 EOF。2026-11-17 去掉旧版那句没人读的 json.loads。
        try:
            sys.stdin.read()
        except Exception:
            pass
        cur, truncated, notes = collect_fingerprints()
        if truncated:
            emit(FALLBACK_CONTEXT)
            return 0
        emit(build_context(cur, notes))
        return 0
    except Exception:
        # S10：固定单行 JSON、零动态文本；emit() 再包一层 try/except，
        # 保证 stdout 自身异常时也静默放行并 exit 0。
        try:
            emit(FALLBACK_CONTEXT)
        except Exception:
            pass
        return 0

if __name__ == "__main__":
    sys.exit(main())
