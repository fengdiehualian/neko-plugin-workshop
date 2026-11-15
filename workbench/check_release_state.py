"""发布收尾核对:两端 latest 是否都指到目标 tag、资产名/体积/SHA 是否与本地 exe 一致。

用途是拿「闭环证据」,而不是相信脚本自己打印的成功:GitHub 抖动时常见 release 建了但资产没传,
或码云附件名被改成带后缀(安装器插件按精确名回退,名字错就等于没有)。
用法:python -I workbench/check_release_state.py [--expect v1.2.63]
"""
import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = ("fengdiehualian", "neko-plugin-workshop")
GT = ("maple-butterfly-flower-love", "neko-plugin-workshop")
NAME = "N.E.K.O.PluginWorkshop-Setup.exe"
# issue #11:这里以前用 ssl._create_unverified_context(),而请求带着 token。
# 现在与 publish_release.py 同一个口径:默认照常校验证书,要降级必须显式设环境变量并打印警告。
sys.path.insert(0, os.path.join(ROOT, "workbench"))
from release_tls import cred as _cred, make_context  # noqa: E402

CTX = make_context()
# urllib 在 Windows 上除环境变量外还会读注册表里的系统代理(加速器/已死掉的本地代理都会留在那里),
# 结果直连能通的 api.github.com / gitee.com 会变成 10061「目标计算机积极拒绝」。
# 与 publish_release.py 保持同一口径:显式不走代理。
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=CTX))


def hash_url(url, headers=None):
    """把资产真正下回来算 SHA256:正文里那行哈希只是自我声明,
    资产被换掉时它对不上才有意义(issue #11:以前只比正文,等于没核资产)。

    注意:不要用响应头里的 content-length 当「应有大小」——GitHub 的 browser_download_url
    会 302 到 objects.githubusercontent.com,那一跳的头与 release API 报的 size 不同源,
    实测会误报「字节数与声明不符」。这里只如实回报实际收到的字节数,由调用方与本地 exe 比对。
    """
    h = hashlib.sha256()
    n = 0
    req = urllib.request.Request(url, headers=headers or {"user-agent": "neko"})
    try:
        with OPENER.open(req, timeout=1800) as r:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                n += len(chunk)
                h.update(chunk)
    except Exception as e:
        return "", 0, "下载失败:%s" % str(e)[:140]
    if n == 0:
        return "", 0, "下载到的内容是空的"
    return h.hexdigest().upper(), n, ""


def cred(host):
    return _cred(host, cwd=ROOT)


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"user-agent": "neko"})
    try:
        with OPENER.open(req, timeout=45) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "请求失败:%s" % str(e)[:120]
    try:
        return json.loads(body or "null"), ""
    except Exception as e:
        return None, "解析失败:%s %s" % (e, body[:80])


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest().upper()


def line(tag, ok, detail):
    print("%s %-26s %s" % ("✓" if ok else "✗", tag, detail))
    return 0 if ok else 1


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", default="")
    ap.add_argument("--exe", default=os.path.join(ROOT, "build", NAME))
    ap.add_argument("--no-asset", action="store_true", dest="no_asset",
                    help="只比元数据,不把 71MB 资产下回来算哈希(默认会算)")
    a = ap.parse_args()
    ver = json.load(open(os.path.join(ROOT, "package.json"), encoding="utf-8"))["version"]
    expect = a.expect or ("v%s" % ver)
    local_size = os.path.getsize(a.exe) if os.path.isfile(a.exe) else 0
    local_sha = sha_of(a.exe) if local_size else ""
    fails = 0

    print("== GitHub ==")
    j, err = get(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases/latest",
                 {"user-agent": "neko", "authorization": "token " + cred("github.com")})
    if not isinstance(j, dict):
        print("  取不到 latest:", err)
        fails += 1
    else:
        tag = j.get("tag_name", "")
        fails += line("latest tag", tag == expect, "%s(期望 %s)" % (tag, expect))
        assets = [x for x in (j.get("assets") or []) if x.get("name") == NAME]
        fails += line("资产精确名", len(assets) == 1, "命中 %d 个 / 全部名 %s" % (len(assets), [x.get("name") for x in (j.get("assets") or [])][:4]))
        if assets:
            sz = assets[0].get("size", 0)
            fails += line("资产体积", sz == local_size if local_size else True, "%s 字节%s" % (format(sz, ","), "" if local_size else "(本地无 exe 可比)"))
            # 正文里的哈希只是自我声明;真要看渠道有没有被换包,得把资产本身下回来算。
            # 但 GitHub 的 CDN 在有些网络里就是够不着(v1.2.65 发布当晚两次超时,而推送阶段也连断 3 次):
            # 够不着时退一步用**接口自带的 digest**(GitHub 对已存储字节算的 SHA-256)印证,
            # 并明确标注这是元数据级证据,不是逐字节回读 —— 既不假装通过,也不把网络问题报成"包被换"。
            if not a.no_asset:
                dig = str(assets[0].get("digest") or "").lower()
                gh_sha, gh_n, herr = hash_url(assets[0].get("browser_download_url") or "")
                if not herr and gh_n:
                    ok = (gh_n == local_size if local_size else True) and (not local_sha or gh_sha == local_sha)
                    fails += line("资产本体 SHA256", ok and bool(local_sha),
                                  "下回 %s 字节 %s…%s" % (format(gh_n, ","), gh_sha[:16],
                                                          "" if not dig else "(接口 digest=%s)" % dig[:22]))
                else:
                    want = "sha256:" + (local_sha or "").lower()
                    has = dig.startswith("sha256:")
                    print("  · 资产下载核对      够不着(%s)→ 改用接口 digest 印证,属元数据级证据" % herr)
                    fails += line("资产接口 digest", has and bool(want != "sha256:") and dig == want,
                                  "接口 %s / 本地 %s" % (dig[:22] or "(无 digest)", (local_sha or "?")[:22] + "…"))
        body = j.get("body") or ""
        m = re.search(r"SHA256:`([0-9a-fA-F]{64})`", body)
        if local_sha:
            fails += line("说明里的 SHA256", bool(m) and m.group(1).upper() == local_sha, (m.group(1)[:16] + "…") if m else "正文没有校验段")

    print("== Gitee ==")
    tok = cred("gitee.com")
    # issue #11:token 不再拼进 URL(会进日志),改走请求头
    GT_H = {"user-agent": "neko", "authorization": "token " + tok}
    j, err = get(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/latest", GT_H)
    if not isinstance(j, dict):
        print("  取不到 latest:", err)
        fails += 1
    else:
        tag = (j.get("tag") or j.get("tag_name") or "")
        fails += line("latest tag", tag == expect, "%s(期望 %s)" % (tag, expect))
        att_list = j.get("attach_files")
        if att_list is None and j.get("id"):
            # 码云的 latest 接口不内联附件列表,要按发行版 id 单查(否则会被误判成「没附件」)
            j2, _e2 = get(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/{j['id']}/attach_files", GT_H)
            att_list = j2 if isinstance(j2, list) else []
        atts = [x.get("name") for x in (att_list or [])]
        exact = [n for n in atts if n == NAME]
        fails += line("附件精确名", len(exact) == 1, "附件 %s" % (atts[:5] or "无"))
        if att_list and not a.no_asset:
            one = [x for x in att_list if x.get("name") == NAME][0]
            gh_sha, gh_n, herr = hash_url(one.get("browser_download_url") or "")
            if herr or gh_n == 0:
                print("  ✗ 附件本体核对       %s" % herr)
                fails += 1
            else:
                ok = (gh_n == local_size if local_size else True) and (not local_sha or gh_sha == local_sha)
                fails += line("附件本体 SHA256", ok and bool(local_sha),
                              "下回 %s 字节 %s…" % (format(gh_n, ","), gh_sha[:16]))
        body = j.get("body") or ""
        m = re.search(r"SHA256:`([0-9a-fA-F]{64})`", body)
        if local_sha:
            fails += line("说明里的 SHA256", bool(m) and m.group(1).upper() == local_sha, (m.group(1)[:16] + "…") if m else "正文没有校验段")

    print("\n本地基准:%s %s 字节 SHA256 %s" % (NAME, format(local_size, ","), local_sha[:24] + "…" if local_sha else "(无)"))
    print("结论:", "双端与本地一致" if not fails else "有 %d 项不一致,必须补" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
