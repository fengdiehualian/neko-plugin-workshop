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
# 仓库根由脚本位置推导,别写死本机绝对路径(issue #6:换台机器就跑不了,而且日志里泄露本机目录)
REPO_DIR = os.environ.get("NEKO_REPO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "N.E.K.O.PluginWorkshop-Setup.exe"


def cred(host):
    # 统一走 release_tls:凭据只在这一处取,不回显、不进日志(路径解析也集中在那里)
    return _cred(host, cwd=REPO_DIR)


# issue #11:以前这里是 CERT_NONE + git sslVerify=false,而这些请求都带着 token。
# 现在默认校验证书;真要降级必须显式设环境变量,而且每次都会打印风险。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from release_tls import cred as _cred, git_nocred, make_context, resolve_git, resolve_node  # noqa: E402
from release_tls import run_git as git_tls_run  # noqa: E402

_opener_ctx = make_context()
op = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_opener_ctx))


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

    # 1) tag —— issue #11:不再 `tag -f` + `push -f` 重写已发布的 tag。
    #    已装好的客户端按同名 tag 比对内容,tag 被挪动就等于「同一个版本号换了包」,
    #    排查时谁也说不清谁装的是哪一份。出问题时正确做法是换一个新版本号。
    local_sha = git_nocred(["rev-parse", tag], cwd=REPO_DIR).stdout.strip()
    if not local_sha:
        git_nocred(["tag", tag], cwd=REPO_DIR)
        local_sha = git_nocred(["rev-parse", tag], cwd=REPO_DIR).stdout.strip()
    print(f"  本仓库 {tag} → {local_sha[:7]}")

    def remote_tag_sha(remote):
        # ls-remote 输出是 "<sha>\trefs/tags/<tag>"[annotated 还会多一行 ^{}]:
        # 取 parts[1] 会把 ref 名当 SHA 比,于是「一致」永远判成「不一致」(我自己踩过,直接挡死发布)
        r = git_tls_run(["ls-remote", remote, "refs/tags/" + tag], cwd=REPO_DIR, timeout=180)
        want = "refs/tags/" + tag
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == want:
                return parts[0]
        return ""

    for remote in ("gitee", "origin"):
        cur = remote_tag_sha(remote)
        if cur and cur != local_sha:
            print(f"  !! {remote} 上的 {tag} 指向 {cur[:7]},与本地 {local_sha[:7]} 不同 —— 已发布的 tag 不重写。"
                  f"请改 package.json 版本号后重新发布(见 issue #11)。")
            raise SystemExit(2)
        if cur == local_sha:
            print(f"  tag→{remote}: 已在 {cur[:7]},无需重复推送")
            continue
        for i in range(4):
            r = git_tls_run(["push", remote, tag], cwd=REPO_DIR, timeout=900)
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
    # issue #11:token 不再拼进 URL —— URL 会进代理日志/服务端访问日志/Shell 历史。
    # 先按请求头鉴权;只有码云明确拒绝(401/403)才退回查询参数老办法,并把这件事打印出来。
    tok = cred("gitee.com")

    def gitee(path, data=None, method="GET", raw=None, ctype="application/json"):
        url = f"https://gitee.com/api/v5/repos/{GT[0]}/{GT[1]}/{path}"
        code, res = call(url, data=data, method=method, raw=raw, ctype=ctype,
                         headers={"authorization": "token " + tok})
        if code in (401, 403):
            print("  (码云不认 Authorization 头,退回查询参数携带 token;这条 URL 请勿外贴)")
            code, res = call(url + ("&" if "?" in url else "?") + "access_token=" + tok,
                             data=data, method=method, raw=raw, ctype=ctype)
        return code, res

    def do_gitee():
        code, rel = gitee(f"releases/tags/{tag}")
        # 坑:发行版不存在时,码云返回 HTTP 200 + body "null",不能只判状态码
        if code == 200 and isinstance(rel, dict) and rel.get("id"):
            gid = rel["id"]
            gitee(f"releases/{gid}", {"tag_name": tag, "name": title, "body": body, "prerelease": False}, method="PATCH")
            print("  Gitee 发行版已存在", gid)
        else:
            code, rel = gitee("releases", {"tag_name": tag, "name": title, "body": body,
                                           "target_commitish": "main", "prerelease": False}, method="POST")
            print("  Gitee 建发行版:", code)
            if code not in (200, 201) or not isinstance(rel, dict) or not rel.get("id"):
                return "建发行版失败 HTTP %s %s" % (code, str(rel)[:160])
            gid = rel["id"]
        code, att = gitee(f"releases/{gid}/attach_files")
        if any(x.get("name") == NAME for x in (att if isinstance(att, list) else [])):
            print("  Gitee 附件已存在(先删后传)")
            for x in att:
                if x.get("name") == NAME:
                    gitee(f"releases/{gid}/attach_files/{x['id']}", method="DELETE")
        b = "----neko" + uuid.uuid4().hex
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{tok}\r\n".encode(),
                 f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{NAME}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
                 blob, f"\r\n--{b}--\r\n".encode()]
        c, res = gitee(f"releases/{gid}/attach_files", raw=b"".join(parts), method="POST",
                      ctype=f"multipart/form-data; boundary={b}")
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

    # 4) 签名清单(issue #12)——客户端的自更新信任根。
    #    私钥只在这台发布机上(%USERPROFILE%\.neko-workshop\update-sign-ed25519),仓库里没有;
    #    客户端用内置公钥验签,验不过就不自动安装。清单没发出去 = 单渠道场景下用户只能手动装,
    #    所以这一步失败要显式报出来,不能悄悄跳过。
    MANIFEST_NAME = "N.E.K.O.PluginWorkshop-Manifest.json"

    def do_manifest():
        node = resolve_node()
        if not node:
            return "找不到 node,无法签清单(设 NODE_BIN)"
        mpath = os.path.join(REPO_DIR, "build", MANIFEST_NAME)
        r = subprocess.run([node, os.path.join(REPO_DIR, "workbench", "update_sign.cjs"), "sign",
                            "--exe", a.exe, "--version", tag, "--out", mpath],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=REPO_DIR)
        print("  签清单:", (r.stdout or "").strip().replace("\n", " | ")[:220] or (r.stderr or "").strip()[:200])
        if r.returncode != 0:
            return "签清单失败:" + (r.stderr or r.stdout or "")[:180]
        mblob = open(mpath, "rb").read()
        errs = []
        # GitHub:作为 release 资产
        H = {"authorization": "token " + cred("github.com"), "user-agent": "neko", "accept": "application/vnd.github+json"}
        code, rel = call(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases/tags/{tag}", headers=H)
        rid = (rel or {}).get("id") if isinstance(rel, dict) else None
        if not rid:
            errs.append("GitHub 取不到 release id")
        else:
            code, assets = call(f"https://api.github.com/repos/{GH[0]}/{GH[1]}/releases/{rid}/assets", headers=H)
            for x in (assets if isinstance(assets, list) else []):
                if x.get("name") == MANIFEST_NAME:
                    call("https://api.github.com" + x.get("url", "").split("api.github.com")[-1], method="DELETE", headers=H)
            c, res = call(f"https://uploads.github.com/repos/{GH[0]}/{GH[1]}/releases/{rid}/assets?name={MANIFEST_NAME}",
                          raw=mblob, method="POST", ctype="application/json", headers=H)
            print("  GitHub 清单上传:", c)
            if c not in (200, 201):
                errs.append("GitHub 清单上传 HTTP %s %s" % (c, str(res)[:120]))
        # 码云:作为附件
        code, rel = gitee(f"releases/tags/{tag}")
        gid = (rel or {}).get("id") if isinstance(rel, dict) else None
        if not gid:
            errs.append("码云取不到发行版 id")
        else:
            code, att = gitee(f"releases/{gid}/attach_files")
            for x in (att if isinstance(att, list) else []):
                if x.get("name") == MANIFEST_NAME:
                    gitee(f"releases/{gid}/attach_files/{x['id']}", method="DELETE")
            b = "----neko" + uuid.uuid4().hex
            parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{MANIFEST_NAME}\"\r\nContent-Type: application/json\r\n\r\n".encode(),
                     mblob, f"\r\n--{b}--\r\n".encode()]
            c, res = gitee(f"releases/{gid}/attach_files", raw=b"".join(parts), method="POST",
                           ctype=f"multipart/form-data; boundary={b}")
            print("  码云清单上传:", c, str(res)[:80])
            if c not in (200, 201):
                errs.append("码云清单上传 HTTP %s %s" % (c, str(res)[:120]))
        return "; ".join(errs)

    try:
        err = do_manifest()
    except Exception as e:
        err = "异常 %s" % str(e)[:150]
    if err:
        fails.append("manifest:" + err)
        print("  ! 签名清单没发全:" + err)

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
