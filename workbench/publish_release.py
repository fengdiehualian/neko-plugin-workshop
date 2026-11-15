r"""通用发布脚本:推 tag → GitHub Release+资产 → Gitee 发行版+附件。

用法: python publish_release.py --notes notes.md --exe path\to\Setup.exe
版本号取仓库 package.json;标题/说明中自动带上大小与 SHA256。
"""
import argparse
import hashlib
import json
import os
import ssl
import subprocess
import sys  # 结尾要 sys.exit(main()):GitHub 抖动时不再装作发布成功
import time
import urllib.error
import urllib.request
import uuid

GH = ("fengdiehualian", "neko-plugin-workshop")
GT = ("maple-butterfly-flower-love", "neko-plugin-workshop")
REPO_DIR = r"C:\Users\Administrator\dev\neko-plugin-workshop"
NAME = "N.E.K.O.PluginWorkshop-Setup.exe"


def cred(host):
    out = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n",
                         capture_output=True, text=True).stdout
    return next(l.split("=", 1)[1] for l in out.splitlines() if l.startswith("password="))


ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))


def call(url, data=None, method="GET", ctype="application/json", raw=None, headers=None, timeout=900):
    body = raw
    if body is None and data is not None:
        body = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if body is not None:
        req.add_header("content-type", ctype)
    try:
        with op.open(req, timeout=timeout) as r:
            txt = r.read().decode()
            try:
                return r.status, json.loads(txt)
            except Exception:
                return r.status, txt[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:250]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--notes", required=True, help="更新说明 markdown")
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    ver = json.load(open(os.path.join(REPO_DIR, "package.json"), encoding="utf-8"))["version"]
    tag = f"v{ver}"
    size = os.path.getsize(a.exe)
    sha = hashlib.sha256(open(a.exe, "rb").read()).hexdigest().upper()
    blob = open(a.exe, "rb").read()
    notes = open(a.notes, encoding="utf-8").read().strip()
    body = notes + f"\n\n## 校验\n\n- 大小:{size:,} 字节\n- SHA256:`{sha}`\n"
    title = a.title or f"{tag} 安装包"
    print(f"发布 {tag}  {size:,} 字节  {sha}")

    # 1) tag
    subprocess.run(["git", "tag", "-f", tag], cwd=REPO_DIR, capture_output=True, text=True)
    for remote in ("gitee", "origin"):
        for i in range(4):
            r = subprocess.run(["git", "-c", "http.proxy=", "-c", "http.sslVerify=false", "push", "-f", remote, tag],
                               cwd=REPO_DIR, capture_output=True, text=True)
            print(f"  tag→{remote}:", (r.stderr or r.stdout).strip().splitlines()[-1][:90])
            if r.returncode == 0:
                break
            time.sleep(8)

    # 2) GitHub —— 一端不通绝不能挡另一端:原来 GitHub 一失败就 raise SystemExit,
    #    结果 github.com 周期性抖动的时段里,码云明明能发也被一起卡住。
    def do_github():
        H = {"authorization": "token " + cred("github.com"), "user-agent": "neko", "accept": "application/vnd.github+json"}
        code, rel = call(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases/tags/{tag}", headers=H)
        if code == 200 and isinstance(rel, dict) and rel.get("id"):
            rid = rel["id"]
            call(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases/{rid}", {"body": body, "name": title}, method="PATCH", headers=H)
            print("  GitHub release 已存在", rid)
        else:
            code, rel = call(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases",
                             {"tag_name": tag, "name": title, "body": body, "draft": False, "prerelease": False},
                             method="POST", headers=H)
            print("  GitHub 建 release:", code)
            if code not in (200, 201) or not isinstance(rel, dict) or not rel.get("id"):
                return "建 release 失败 HTTP %s %s" % (code, str(rel)[:160])
            rid = rel["id"]
        code, assets = call(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases/{rid}/assets", headers=H)
        if any(a.get("name") == NAME for a in (assets if isinstance(assets, list) else [])):
            print("  GitHub 资产已存在")
        else:
            c, res = call(f"https://uploads.github.com/repos/{GH[0]}/{GH[1]}/releases/{rid}/assets?name={NAME}",
                          raw=blob, method="POST", ctype="application/octet-stream", headers=H)
            print("  GitHub 上传:", c, (res or {}).get("size") if isinstance(res, dict) else str(res)[:80])
            if c not in (200, 201):
                return "资产上传失败 HTTP %s %s" % (c, str(res)[:160])
        return ""

    fails = []
    for attempt in range(1, 7):
        try:
            err = do_github()
        except Exception as e:
            err = "异常 %s" % str(e)[:150]
        if not err:
            break
        fails.append("github:%s" % err)
        print("  GitHub 第 %d 次没成功:%s" % (attempt, err))
        time.sleep(min(45, 6 * attempt))
    else:
        print("  ! GitHub 最终未发布,继续发码云(事后重跑本脚本会补上 GitHub)")

    # 3) Gitee(同样不让它因一次抖动就中断;附件名必须是精确名,安装器插件按精确名回退)
    def do_gitee():
        tok = cred("gitee.com")
        code, rel = call(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/tags/{tag}?access_token={tok}")
        # 坑:发行版不存在时,码云返回 HTTP 200 + body "null",不能只判状态码
        if code == 200 and isinstance(rel, dict) and rel.get("id"):
            gid = rel["id"]
            call(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/{gid}?access_token={tok}",
                 {"tag_name": tag, "name": title, "body": body, "prerelease": False}, method="PATCH")
            print("  Gitee 发行版已存在", gid)
        else:
            code, rel = call(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases?access_token={tok}",
                             {"tag_name": tag, "name": title, "body": body, "target_commitish": "main", "prerelease": False},
                             method="POST")
            print("  Gitee 建发行版:", code)
            if code not in (200, 201) or not isinstance(rel, dict) or not rel.get("id"):
                return "建发行版失败 HTTP %s %s" % (code, str(rel)[:160])
            gid = rel["id"]
        code, att = call(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/{gid}/attach_files?access_token={tok}")
        if any(x.get("name") == NAME for x in (att if isinstance(att, list) else [])):
            print("  Gitee 附件已存在(先删后传)")
            for x in att:
                if x.get("name") == NAME:
                    call(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/{gid}/attach_files/{x['id']}?access_token={tok}", method="DELETE")
        b = "----neko" + uuid.uuid4().hex
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{tok}\r\n".encode(),
                 f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{NAME}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
                 blob, f"\r\n--{b}--\r\n".encode()]
        c, res = call(f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/releases/{gid}/attach_files",
                      raw=b"".join(parts), method="POST", ctype=f"multipart/form-data; boundary={b}")
        print("  Gitee 上传:", c, str(res)[:90])
        if c not in (200, 201):
            return "附件上传失败 HTTP %s %s" % (c, str(res)[:160])
        return ""

    for attempt in range(1, 5):
        try:
            err = do_gitee()
        except Exception as e:
            err = "异常 %s" % str(e)[:150]
        if not err:
            break
        fails.append("gitee:%s" % err)
        print("  Gitee 第 %d 次没成功:%s" % (attempt, err))
        time.sleep(min(30, 5 * attempt))

    if fails:
        # 去重:重试循环里同一端会留多条
        uniq = []
        for f in fails:
            if not any(f.split(":")[0] == u.split(":")[0] and f == u for u in uniq):
                uniq.append(f)
        print("\n!! 发布未全部完成:%s" % "; ".join(uniq[-2:]))
        print("   两端都是「已存在就改、缺附件就传」的幂等写法,重跑本脚本即可补齐。")
        return 1
    print("\n双端发布完成:%s(%s 字节)" % (tag, format(size, ",")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
