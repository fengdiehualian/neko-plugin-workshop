"""猫娘计划(N.E.K.O.)插件:Agent 工作台安装器

在猫娘计划里一键安装「N.E.K.O. 插件工坊」(Agent 工作台):
下载官方安装版 Setup.exe(或用本地 exe)→ 静默安装 → 可选建桌面快捷方式 → 一键启动;
随时查看安装与运行状态。所有入口都双注册为 llm_tool,对猫娘说「安装工作台」即可触发。

入口:
  - install : 安装/覆盖安装 Agent 工作台(仅官方安装版 exe)
  - status  : 查看安装与运行状态
  - launch  : 启动工作台(已在运行则直接返回地址)
"""

from __future__ import annotations

# isort: off
# 说明:官方两条门禁(CI 的 plugin-repo 形状与 publish 的 cwd=. 形状)对 plugin.sdk 的
# first/third-party 归类相反,同一排序无法同时满足 —— 冻结本块,双方一致放行

import asyncio
import os
import re
import shutil
import socket
import subprocess

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)
from pydantic import BaseModel, Field

# isort: on

try:
    import httpx
except ImportError:  # 仅缺包时降级到标准库实现;httpx 自身的其他异常必须冒出来,不能静默吞
    httpx = None

_PLUGIN_ID = "workbench_installer"
# 官方从 v1.2.5 起只发安装版 exe,便携 zip 已停更在 v1.2.3;
# 本插件只走官方安装器,不再支持 zip 解压安装。
_SETUP_ASSET = "N.E.K.O.PluginWorkshop-Setup.exe"
_RELEASE_BASE = "https://github.com/fengdiehualian/neko-plugin-workshop/releases/latest/download/"
_DEFAULT_URL = _RELEASE_BASE + _SETUP_ASSET
# 国内镜像(码云):GitHub 拉不动时自动改用这里的最新发行版资产
_MIRROR_REPO = "maple-butterfly-flower-love/neko-plugin-workshop"
_MIRROR_LATEST_API = f"https://gitee.com/api/v5/repos/{_MIRROR_REPO}/releases/latest"
_MIRROR_ASSET = _SETUP_ASSET
# 码云免费账号单附件上限 100MB,133MB 的 SFX 传不上去;镜像侧放 7z 压缩包,
# 由插件内置的 7zr 解压安装(7z 比 deflate 小得多,且解压/压缩工具统一)。
_MIRROR_ARCHIVE_7Z = "N.E.K.O.PluginWorkshop-Payload.7z"
# 兼容旧镜像:此前发过 tar.xz,标准库即可解,作为第三顺位
_MIRROR_ARCHIVE = "N.E.K.O.PluginWorkshop-Payload.tar.xz"
# 码云单附件 100MB 上限仍在,139MB 的安装器只能拆卷发(名字带版本号,如 -v1.2.14);
# 这里认 .exe.partN,按编号顺序下载后拼回原 exe 再交给安装器。
_MIRROR_PART_RE = re.compile(
    r"^N\.E\.K\.O\.PluginWorkshop-Setup(?:[._-]v?[\d.]+)?\.exe\.part(\d+)$", re.I
)


def _pick_mirror_assets(assets) -> list[str]:
    """从码云发行版资产里挑下载地址(按优先级)。

    返回 1 个地址 = 单文件(7z / exe / tar.xz);返回多个 = 同一安装包的连续分卷。
    分卷必须从 1 开始不跳号,否则宁可判为不可用 —— 缺卷拼出来的是坏文件,
    装到一半才报错比直接说"镜像不可用"更糟。
    """
    by_name: dict[str, str] = {}
    for a in assets or []:
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or "")
        if name and url:
            by_name[name] = url
    for want in (_MIRROR_ARCHIVE_7Z, _MIRROR_ASSET, _MIRROR_ARCHIVE):
        if want in by_name:
            return [by_name[want]]
    parts: list[tuple[int, str]] = []
    for name, url in by_name.items():
        m = _MIRROR_PART_RE.match(name)
        if m:
            parts.append((int(m.group(1)), url))
    if len(parts) >= 2:
        parts.sort(key=lambda x: x[0])
        if [n for n, _ in parts] == list(range(1, len(parts) + 1)):
            return [u for _, u in parts]
    return []
# 主源(GitHub)的「停滞」阈值:连接后连续这么久收不到数据就判超时、切镜像。
# 不用总时长上限——133MB 慢速下载本身可能就要 7-10 分钟,按总时长卡会误杀正常下载。
_PRIMARY_STALL_SECONDS = 25.0
# 需要回落镜像的失败类型:超时/连不上/服务端 5xx/资产缺失(404)/限流(429)/鉴权(401、403)。
# 401/403 通常意味着源站拒绝(GitHub 私有化、IP 被限、企业网关拦截),镜像仓是公开的,
# 换源往往能绕过去,所以一并回落。
_FALLBACK_STATUS = frozenset({401, 403, 404, 429})
_HEALTH_URL = "http://127.0.0.1:5099/api/health"
_WORKBENCH_URL = "http://127.0.0.1:5099"
_SHORTCUT_NAME = "N.E.K.O.插件工坊"
# 下载尝试顺序:显式配置的 proxy → 环境变量代理 → 直连。
# 不再硬编码 7890/7892 之类本地端口:那些端口可能是别的软件在用,
# 且每个候选都会把 133MB 完整重下一遍(实测最坏 30 分钟)。
_PROXY_ATTEMPTS = ("config", "env", "direct")


def _default_target() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    return os.path.join(base, "Programs", "NEKOWorkshop")


def _looks_like_exe(path: str) -> bool:
    """按文件头判断是不是 Windows 可执行文件(MZ),不看扩展名——
    镜像下载的文件名固定为 Setup.exe,内容却可能是压缩包。"""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def _html_escape(text: str) -> str:
    """最小 HTML 转义,用于手动下载页(URL 和错误信息都可能含特殊字符)。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _archive_kind(path: str) -> str:
    """按文件头判类型:exe / 7z / xz / unknown。"""
    try:
        with open(path, "rb") as f:
            head = f.read(6)
    except OSError:
        return "unknown"
    if head[:2] == b"MZ":
        return "exe"
    if head == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head == b"\xfd7zXZ\x00":
        return "xz"
    return "unknown"


def _find_7zr() -> str:
    """找可用的 7z 可执行文件。

    内置的 `bin/7zr.exe` 是 Windows 程序,只能在本机平台匹配时才用——
    否则在 Linux/macOS 上会拿 PE 文件去执行,直接 PermissionError(CI 踩过)。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.name == "nt":
        for name in ("7zr.exe", "7za.exe", "7z.exe"):
            p = os.path.join(here, "bin", name)
            if os.path.isfile(p):
                return p
    for name in ("7zr", "7za", "7z"):
        p = shutil.which(name)
        if p:
            return p
    if os.name == "nt":
        for p in (
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "7-Zip", "7z.exe"),
            os.path.join(
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "7-Zip", "7z.exe"
            ),
        ):
            if os.path.isfile(p):
                return p
    return ""


def _download_urllib(
    url: str, dest: str, proxy: str | None, trust_env: bool, read_timeout: float = 60.0
) -> None:
    import urllib.request

    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    elif trust_env:
        handler = urllib.request.ProxyHandler()  # 空参 = 读环境变量
    else:
        handler = urllib.request.ProxyHandler({})  # 显式直连
    opener = urllib.request.build_opener(handler)
    with opener.open(url, timeout=read_timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _make_httpx_client(timeout, proxy, trust_env: bool):
    kwargs = {"timeout": timeout, "follow_redirects": True, "trust_env": trust_env}
    if proxy:
        # 按签名选参数名:新版 httpx 用 proxy=,旧版用 proxies=。
        # 不用 try/except TypeError 兜底——timeout 等无关 TypeError 会被误吞进错误分支
        import inspect

        params = inspect.signature(httpx.AsyncClient.__init__).parameters
        if "proxy" in params:
            kwargs["proxy"] = proxy
        else:
            kwargs["proxies"] = proxy
    return httpx.AsyncClient(**kwargs)


class InstallParams(BaseModel):
    exe: str = Field("", description="本地安装包(.exe)路径;填了就不联网下载")
    url: str = Field("", description="下载地址;留空=官方 Release 最新安装版")
    launch: bool = Field(True, description="安装完成后是否立刻启动")
    shortcut: bool = Field(True, description="是否创建桌面快捷方式")
    overwrite: bool = Field(
        False,
        description="已安装时是否覆盖重装(默认拒绝;官方安装器会保留 runtime 配置与 workspace 用户项目)",
    )
    manual_page: bool = Field(
        True,
        description="下载失败时是否自动弹出手动下载页(内含可一键复制的下载地址)",
    )


@neko_plugin
class WorkbenchInstallerPlugin(NekoPluginBase):
    """Agent 工作台安装器 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._cfg_target = ""
        self._cfg_url = _DEFAULT_URL
        self._cfg_mirror = ""
        self._cfg_manual_page = True
        self._cfg_proxy = ""
        self._cfg_download_timeout = 900.0

    # ── lifecycle ──
    async def _load_cfg(self):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get(_PLUGIN_ID)
        section = section if isinstance(section, dict) else {}
        self._cfg_url = str(section.get("url", "") or "").strip() or _DEFAULT_URL
        # mirror: 留空=自动用码云最新发行版资产;"off"/"none"=关闭回落;或填具体直链
        self._cfg_mirror = str(section.get("mirror", "") or "").strip()
        flag = str(section.get("manual_page", "true")).strip().lower()
        self._cfg_manual_page = flag not in ("0", "false", "off", "no")
        self._cfg_proxy = str(section.get("proxy", "") or "").strip()
        try:
            t = float(section.get("download_timeout", 900) or 900)
        except (TypeError, ValueError):
            t = 900.0
        self._cfg_download_timeout = max(60.0, min(t, 1800.0))

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._load_cfg()
        self.logger.info("Agent 工作台安装器已启动")
        return Ok({"status": "ready"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        await self._load_cfg()
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "bye"})

    # ── 辅助 ──
    def _resolve_target(self, target: str) -> str:
        # 安装位置由官方安装器决定(固定默认目录);target 只用于 status/launch 探测自定义安装
        t = (target or "").strip() or _default_target()
        return os.path.abspath(os.path.expandvars(os.path.expanduser(t)))

    def _probe_install(self, target: str) -> dict:
        start_cmd = os.path.join(target, "start.cmd")
        installed = os.path.isfile(start_cmd)
        files = 0
        size = 0
        if os.path.isdir(target):
            for root, _dirs, names in os.walk(target):
                for n in names:
                    files += 1
                    try:
                        size += os.path.getsize(os.path.join(root, n))
                    except OSError:
                        pass
        return {
            "installed": installed,
            "target": target,
            "start_cmd": start_cmd if installed else "",
            "files": files,
            "size_mb": round(size / 1024 / 1024, 1),
        }

    @staticmethod
    def _merge_tree(src: str, dst: str) -> int:
        """把解压出来的临时树合进安装目录。

        runtime/workspace 是用户数据区:目标已存在的文件不覆盖;
        其余程序文件一律覆盖(升级要生效)。
        """
        copied = 0
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            top = "" if rel == "." else rel.split(os.sep)[0]
            protected = top in ("runtime", "workspace")
            out_dir = dst if rel == "." else os.path.join(dst, rel)
            os.makedirs(out_dir, exist_ok=True)
            for name in files:
                s = os.path.join(root, name)
                d = os.path.join(out_dir, name)
                if protected and os.path.exists(d):
                    continue
                shutil.copy2(s, d)
                copied += 1
        return copied

    def _install_from_7z(self, archive: str, target: str) -> dict:
        """用 7z(优先插件内置 7zr)解压到临时目录,再合并进安装目录。"""
        seven = _find_7zr()
        if not seven:
            return {
                "ok": False,
                "error": "未找到 7z 解压程序(插件内置 7zr 缺失且系统未安装 7-Zip)",
            }
        tmp = os.path.join(self.cache_path("extract"), "payload")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        try:
            proc = subprocess.run(
                [seven, "x", archive, f"-o{tmp}", "-y", "-bso0", "-bsp0"],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[:300]
                return {"ok": False, "error": f"7z 解压失败(exit {proc.returncode}): {detail}"}
            count = self._merge_tree(tmp, target)
        except Exception as e:
            return {"ok": False, "error": f"{e}"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": True, "files": count}

    def _install_from_archive(self, archive: str, target: str) -> dict:
        """把镜像压缩包(tar.xz)解压成工作台安装,保留 runtime/workspace 用户数据。"""
        import tarfile

        os.makedirs(target, exist_ok=True)
        count = 0
        try:
            with tarfile.open(archive, "r:xz") as tf:
                for member in tf:
                    name = member.name.replace("\\", "/")
                    while name.startswith("./"):
                        name = name[2:]
                    if not name or name == ".":
                        continue
                    parts = name.split("/")
                    if name.startswith("/") or ".." in parts:
                        raise ValueError(f"压缩包包含不安全路径: {member.name}")
                    dest = os.path.join(target, name)
                    # runtime/workspace 是用户数据区:已存在的文件一律不覆盖,
                    # 但缺失的默认内容(如 workspace/.opencode/skill)仍要补上,
                    # 否则全新安装会缺技能库。
                    protected = parts[0] in ("runtime", "workspace")
                    if member.isdir():
                        os.makedirs(dest, exist_ok=True)
                        continue
                    if not member.isfile():  # 跳过符号链接/设备文件等
                        continue
                    if protected and os.path.exists(dest):
                        continue
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    count += 1
        except Exception as e:
            return {"ok": False, "error": f"{e}"}
        return {"ok": True, "files": count}

    def _proxy_for(self, attempt: str):
        """按尝试计划返回 (proxy, trust_env);config 未配置时返回 (None, None) 表示跳过。"""
        if attempt == "config":
            return (self._cfg_proxy or None, False) if self._cfg_proxy else (None, None)
        if attempt == "env":
            return None, True
        return None, False

    async def _download_once(
        self, url: str, dest: str, proxy, trust_env: bool, read_timeout: float | None = None
    ) -> None:
        stall = read_timeout if read_timeout else self._cfg_download_timeout
        if httpx is not None:
            # read 超时是「两次收到数据之间的最大间隔」,正好当停滞看门狗用
            timeout = httpx.Timeout(self._cfg_download_timeout, connect=10.0, read=stall)
            async with _make_httpx_client(timeout, proxy, trust_env) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(1 << 16):
                            f.write(chunk)
        else:
            await asyncio.to_thread(_download_urllib, url, dest, proxy, trust_env, stall)

    @staticmethod
    def _failure_kind(exc: Exception) -> tuple[str, int | None]:
        """把下载异常归类成 timeout / conn / http,用于决定是否该回落到镜像源。"""
        if httpx is not None:
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    return "http", exc.response.status_code
                except Exception:
                    return "http", None
            if isinstance(exc, httpx.TimeoutException):
                return "timeout", None
            if isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)):
                return "conn", None
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return "timeout", None
        if isinstance(exc, (ConnectionError, OSError)):
            return "conn", None
        return "other", None

    async def _download(
        self, url: str, dest: str, read_timeout: float | None = None
    ) -> dict:
        dest_dir = os.path.dirname(dest)
        if dest_dir:  # 裸文件名时 dirname 为空,makedirs("") 会直接炸
            os.makedirs(dest_dir, exist_ok=True)
        errors = []
        for attempt in _PROXY_ATTEMPTS:
            proxy, trust_env = self._proxy_for(attempt)
            if trust_env is None:  # 该通道未配置,跳过
                continue
            label = proxy or ("env" if trust_env else "direct")
            try:
                await self._download_once(url, dest, proxy, trust_env, read_timeout)
                size = os.path.getsize(dest) if os.path.isfile(dest) else 0
                if size > 0:
                    return {"ok": True, "path": dest, "bytes": size, "via": label}
                errors.append(f"{label}: 文件为空")
            except Exception as e:
                kind, status = self._failure_kind(e)
                # 服务端已明确回应(404/403 等):换代理重试没有意义,直接报错
                if kind == "http":
                    return {
                        "ok": False,
                        "kind": "http",
                        "status": status,
                        "error": f"{label}: HTTP {status}(地址不存在或无权限): {url}",
                    }
                errors.append(f"{label}: {e}")
                # 超时/连不上:同一地址换代理仍可能通,继续下一通道;
                # 全部通道都失败时由上层按 kind 决定是否切镜像
        return {"ok": False, "kind": "timeout" if all("timed out" in e or "Timeout" in e for e in errors) else "conn", "error": " | ".join(errors)[:800]}

    async def _mirror_urls(self, primary_url: str) -> list[str]:
        """解析码云镜像的最新发行版下载地址;不可用时返回空列表。

        单文件资产返回 1 个地址;分卷安装包返回按 part 编号排好序的多个地址。
        """
        cfg = self._cfg_mirror.lower()
        if cfg in ("off", "none", "0", "false"):
            return []
        if self._cfg_mirror:
            return [self._cfg_mirror]  # 用户直接指定了镜像直链
        if primary_url != self._cfg_url:
            return []  # 用户自定义了下载源,不再擅自换源
        # 通过 API 取最新发行版的资产清单,再按名字挑(单文件优先,其次分卷)
        try:
            if httpx is not None:
                async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                    r = await client.get(_MIRROR_LATEST_API)
                    r.raise_for_status()
                    data = r.json()
            else:
                import json
                import urllib.request

                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(_MIRROR_LATEST_API, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.logger.warning("码云镜像地址解析失败,跳过回落: {}", e)
            return []
        picked = _pick_mirror_assets(data.get("assets") or [])
        if picked:
            return picked
        self.logger.warning(
            "码云最新发行版里没有 {} / {} / {} / 分卷 .exe.partN,跳过回落",
            _MIRROR_ARCHIVE_7Z, _MIRROR_ASSET, _MIRROR_ARCHIVE,
        )
        return []

    async def _download_split(self, urls: list[str], dest: str) -> dict:
        """把同一安装包的分卷按顺序下载并拼回单一 exe;任何一卷失败都整体判失败。"""
        parts: list[str] = []
        total = 0
        try:
            for i, u in enumerate(urls, 1):
                part = f"{dest}.part{i}"
                got = await self._download(u, part)
                if not got.get("ok"):
                    return {
                        "ok": False,
                        "kind": got.get("kind"),
                        "status": got.get("status"),
                        "error": f"分卷 {i}/{len(urls)} 下载失败: {got.get('error', '')}",
                        "tried": list(urls[:i]),
                    }
                parts.append(part)
                total += int(got.get("bytes") or 0)
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as out:
                for p in parts:
                    with open(p, "rb") as f:
                        shutil.copyfileobj(f, out, 1024 * 1024)
            size = os.path.getsize(dest)
            with open(dest, "rb") as f:
                magic = f.read(2)
            if magic != b"MZ":
                return {
                    "ok": False,
                    "kind": "http",
                    "error": "分卷合并后不是有效的安装程序(缺少 MZ 头),可能分卷不完整",
                    "tried": list(urls),
                }
            return {
                "ok": True,
                "path": dest,
                "bytes": size,
                "via": f"gitee 镜像(分卷 {len(urls)} 卷, {total} bytes)",
                "fallback": True,
            }
        finally:
            for p in parts:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def _download_preferring(self, primary_url: str, dest: str) -> dict:
        """先拉 GitHub(短停滞阈值);超时/连不上/5xx/404 再回落码云镜像。"""
        got = await self._download(primary_url, dest, read_timeout=_PRIMARY_STALL_SECONDS)
        if got.get("ok"):
            return got
        kind, status = got.get("kind"), got.get("status")
        retryable = kind in ("timeout", "conn") or (
            kind == "http" and (status in _FALLBACK_STATUS or (status or 0) >= 500)
        )
        if not retryable:
            got["tried"] = [primary_url]
            return got
        mirrors = await self._mirror_urls(primary_url)
        if not mirrors:
            got["tried"] = [primary_url]
            return got
        self.logger.info(
            "主源失败({}),改用码云镜像: {} URL ({} 卷)",
            kind, len(mirrors), len(mirrors),
        )
        if len(mirrors) > 1:
            got2 = await self._download_split(mirrors, dest)
        else:
            got2 = await self._download(mirrors[0], dest)
            if got2.get("ok"):
                got2["via"] = f"{got2['via']} (gitee 镜像)"
                got2["fallback"] = True
        if got2.get("ok"):
            return got2
        return {
            "ok": False,
            "kind": got2.get("kind") or kind,
            "tried": [primary_url, *mirrors],
            "error": f"github 失败: {got.get('error', '')} || gitee 失败: {got2.get('error', '')}"[
                :800
            ],
        }

    # ── 手动下载兜底页 ──

    def _manual_page_html(self, urls: list[str], detail: str) -> str:
        rows = []
        for i, u in enumerate(urls, 1):
            safe = _html_escape(u)
            rows.append(
                f'<div class="row"><div class="idx">{i}</div>'
                f'<div class="url" id="u{i}">{safe}</div>'
                f'<div class="acts">'
                f'<button onclick="copyUrl(\'u{i}\',this)">复制</button>'
                f'<a class="btn" href="{safe}" target="_blank" rel="noreferrer">打开</a>'
                f"</div></div>"
            )
        url_list = "\n".join(rows)
        safe_detail = _html_escape(detail or "未知原因")
        hints = (
            "1) 点「复制」把地址复制到剪贴板,再粘贴到浏览器地址栏回车;<br>"
            "2) 下载得到的文件双击运行,选默认目录安装(会保留配置与用户项目);<br>"
            "3) 装好后回到猫娘说「启动工作台」即可。"
        )
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>N.E.K.O 插件工坊 · 手动下载</title>
<style>
:root{{color-scheme:light}}
body{{margin:0;padding:40px 20px;font:15px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;
background:#fdf6f8;color:#33262c}}
.card{{max-width:760px;margin:0 auto;background:#fff;border-radius:16px;padding:28px 30px;
box-shadow:0 8px 30px rgba(190,90,130,.12)}}
h1{{margin:0 0 6px;font-size:21px;color:#b23a63}}
.sub{{color:#8a7078;font-size:13px;margin-bottom:18px}}
.err{{background:#fff2f5;border-left:4px solid #e2749a;padding:10px 14px;border-radius:8px;
font-size:12.5px;color:#8a4055;word-break:break-all;margin-bottom:20px;max-height:140px;overflow:auto}}
.row{{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px dashed #f0dfe5}}
.idx{{width:22px;height:22px;flex:0 0 22px;border-radius:50%;background:#f6d3df;color:#a83b60;
font-size:12px;display:flex;align-items:center;justify-content:center}}
.url{{flex:1;font-family:Consolas,monospace;font-size:12px;word-break:break-all;color:#4a3540}}
.acts{{display:flex;gap:6px;flex:0 0 auto}}
button,.btn{{font:12px/1 inherit;padding:7px 12px;border-radius:8px;border:1px solid #e9c3d1;
background:#fff;color:#a83b60;cursor:pointer;text-decoration:none;white-space:nowrap}}
button:hover,.btn:hover{{background:#fdeef3}}
button.done{{background:#e8f7ee;border-color:#bfe6cd;color:#2c7a4b}}
.tips{{margin-top:20px;font-size:13px;color:#6d5b63;background:#fbf7f9;padding:14px 16px;border-radius:10px}}
.foot{{margin-top:18px;font-size:12px;color:#a2919a}}
</style></head><body><div class="card">
<h1>下载失败了，这里有手动下载地址</h1>
<div class="sub">GitHub 和码云镜像都没拉下来。可以用下面的地址手动下载安装。</div>
<div class="err">失败原因：{safe_detail}</div>
{url_list}
<div class="tips">{hints}</div>
<div class="foot">安装目录固定为 %LOCALAPPDATA%\\Programs\\NEKOWorkshop，覆盖安装会保留 runtime 配置与 workspace 里的作品。</div>
</div>
<script>
function copyUrl(id, btn){{
  var t = document.getElementById(id).innerText.trim();
  function ok(){{ var old = btn.textContent; btn.textContent = '已复制'; btn.classList.add('done');
    setTimeout(function(){{ btn.textContent = old; btn.classList.remove('done'); }}, 1500); }}
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(t).then(ok, function(){{ fallback(t, ok); }});
  }} else {{ fallback(t, ok); }}
}}
function fallback(text, cb){{
  var ta = document.createElement('textarea');
  ta.value = text; ta.style.position='fixed'; ta.style.opacity='0';
  document.body.appendChild(ta); ta.select();
  try {{ document.execCommand('copy'); cb(); }} catch(e) {{ alert('复制失败，请手动选中地址复制'); }}
  document.body.removeChild(ta);
}}
</script></body></html>"""

    def _open_manual_page(self, urls: list[str], detail: str) -> str:
        """生成手动下载页并用默认浏览器打开;返回文件路径(失败返回空串)。"""
        path = self.cache_path("manual-download.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._manual_page_html(urls, detail))
        except OSError as e:
            self.logger.warning("写手动下载页失败: {}", e)
            return ""
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606 - 打开本地 HTML 供用户手动下载
            else:
                import webbrowser

                webbrowser.open(f"file://{path}")
        except Exception as e:
            self.logger.warning("打开手动下载页失败: {}", e)
            return path
        return path

    async def _health(self):
        # 本机回环探测绝不能走代理:环境里一旦有 HTTP_PROXY,127.0.0.1 会被拦掉,
        # 会把"已在运行"误判成"未运行"(实测踩过)。
        try:
            if httpx is not None:
                async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                    r = await client.get(_HEALTH_URL)
                    return r.json()
            import json
            import urllib.request

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(_HEALTH_URL, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    async def _wait_health(self, seconds: float = 25.0):
        # get_event_loop() 在运行中的协程里已废弃(3.12 起报错),用 get_running_loop()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            h = await self._health()
            if h is not None:
                return h
            await asyncio.sleep(1.5)
        return None

    def _spawn(self, target: str) -> dict:
        start_cmd = os.path.join(target, "start.cmd")
        if not os.path.isfile(start_cmd):
            return {"ok": False, "error": "未找到 start.cmd,该目录不是工作台安装目录"}
        kwargs = {}
        if os.name == "nt":
            # DETACHED_PROCESS 与 CREATE_NEW_CONSOLE 互斥:同时使用 CreateProcess 报
            # WinError 87(参数错误),启动会静默失败 —— 只用 CREATE_NEW_CONSOLE(等效双击)
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        try:
            proc = subprocess.Popen(["cmd", "/c", str(start_cmd)], cwd=target, **kwargs)
            return {"ok": True, "pid": proc.pid}
        except Exception as e:
            return {"ok": False, "error": f"启动失败: {e}"}

    def _run_setup_exe(self, exe_path: str) -> dict:
        """静默运行官方安装器(--silent);它固定装到默认目录,并在升级时自行保留 runtime/workspace。"""
        try:
            r = subprocess.run(
                [str(exe_path), "--silent"],
                capture_output=True,
                timeout=300,
            )
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", "replace")[:200]
                return {"ok": False, "error": f"exit={r.returncode} {err}".strip()}
            return {"ok": True, "returncode": r.returncode}
        except Exception as e:
            return {"ok": False, "error": f"运行安装器超时或失败: {e}"}

    async def _running_hint(self) -> str:
        """安装失败时补一句人话:多半是工作台还开着,文件被占用写不进去。"""
        if await self._health() is None:
            return ""
        return "。检测到工作台仍在运行:请先关闭工作台窗口(不是最小化)再重试,否则程序文件被占用会导致覆盖失败"

    async def _wait_installed(self, target: str, seconds: float) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        info = await asyncio.to_thread(self._probe_install, target)
        while loop.time() < deadline and not info["installed"]:
            await asyncio.sleep(1.5)
            info = await asyncio.to_thread(self._probe_install, target)
        return info

    def _make_shortcut(self, target: str) -> dict:
        if os.name != "nt":
            return {"ok": False, "skipped": True, "error": "仅 Windows 支持快捷方式"}
        start_cmd = os.path.join(target, "start.cmd")
        # 路径经环境变量传给 PowerShell,不内联进脚本:含单引号/特殊字符的路径
        # (如 C:\Users\O'Brien\...)不会破坏语法
        ps = (
            "$ws = New-Object -ComObject WScript.Shell; "
            "$desktop = [Environment]::GetFolderPath('Desktop'); "
            "$lnk = $ws.CreateShortcut((Join-Path $desktop ($env:WB_SC_NAME + '.lnk'))); "
            "$lnk.TargetPath = $env:WB_SC_TARGET; "
            "$lnk.WorkingDirectory = $env:WB_SC_DIR; "
            "$lnk.Description = 'N.E.K.O. Plugin Workbench'; "
            "$lnk.Save()"
        )
        env = {
            **os.environ,
            "WB_SC_TARGET": start_cmd,
            "WB_SC_DIR": target,
            "WB_SC_NAME": _SHORTCUT_NAME,
        }
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                timeout=30,
                env=env,
            )
            if r.returncode == 0:
                return {"ok": True, "name": f"{_SHORTCUT_NAME}.lnk"}
            return {
                "ok": False,
                "error": (r.stderr or b"").decode("utf-8", "replace")[:200],
            }
        except Exception as e:
            return {"ok": False, "error": f"创建快捷方式失败: {e}"}

    # ── 入口:安装(仅官方安装版 exe)──
    @llm_tool(
        name="workbench_installer_install",
        description="安装「N.E.K.O. 插件工坊」(Agent 工作台)到这台电脑:下载官方安装版 Setup.exe"
        "(或用本地 exe)并静默安装,可选创建桌面快捷方式并启动。"
        "当用户想安装/重装 Agent 工作台、插件工坊时使用。",
        parameters={
            "type": "object",
            "properties": {
                "exe": {
                    "type": "string",
                    "description": "本地安装包(.exe)路径;填了就不联网下载",
                },
                "url": {
                    "type": "string",
                    "description": "下载地址;留空=官方 Release 最新安装版",
                },
                "launch": {"type": "boolean", "description": "装完是否立刻启动,默认 true"},
                "shortcut": {"type": "boolean", "description": "是否创建桌面快捷方式,默认 true"},
                "overwrite": {
                    "type": "boolean",
                    "description": "已安装时是否覆盖重装,默认 false(拒绝覆盖)",
                },
                "manual_page": {
                    "type": "boolean",
                    "description": "下载失败时是否自动弹出手动下载页(可一键复制下载地址),默认 true",
                },
            },
        },
        # 官方文档硬上限:@llm_tool timeout ≤ 300 秒(服务端会截断)。下载慢时可能不够,
        # 面板/手动触发走下面的 @plugin_entry(900 秒),不受此限制。
        timeout=300.0,
    )
    @plugin_entry(
        id="install",
        name="安装 Agent 工作台",
        description="下载官方安装版并静默安装,可创建桌面快捷方式并启动(仅支持 exe)",
        timeout=900.0,
        llm_result_fields=[
            "ok",
            "message",
            "target",
            "already_installed",
            "launched",
            "url",
        ],
    )
    async def install(self, params: InstallParams, **_):
        # 先校验本地安装包:输入错误应立即报错,而不是被"已安装"挡住
        exe_path = (params.exe or "").strip()
        if exe_path:
            if not os.path.isfile(exe_path):
                return Err(SdkError(f"本地安装包不存在: {exe_path}"))
            if not exe_path.lower().endswith(".exe"):
                return Err(SdkError("只支持官方安装版 .exe;便携 zip 已停止支持"))

        # 安装位置由官方安装器固定(默认目录),不支持自定义
        target = self._resolve_target("")
        info = await asyncio.to_thread(self._probe_install, target)
        if info["installed"] and not params.overwrite:
            return Ok({
                "ok": True,
                "already_installed": True,
                "target": target,
                "url": _WORKBENCH_URL,
                "message": "工作台已安装,未执行覆盖;如需覆盖重装请传 overwrite=true",
            })

        # 获取安装包:本地 exe 优先,否则联网下载(GitHub 优先,超时回落码云镜像)
        source = exe_path or (params.url or "").strip() or self._cfg_url
        if not exe_path:
            dest = os.path.join(self.cache_path("download"), _SETUP_ASSET)
            self.logger.info("下载工作台安装版: {}", source)
            got = await self._download_preferring(source, dest)
            if not got.get("ok"):
                urls = [u for u in (got.get("tried") or []) if u] or [source]
                page = ""
                if params.manual_page and self._cfg_manual_page:
                    page = await asyncio.to_thread(
                        self._open_manual_page, urls, got.get("error", "")
                    )
                urls_text = " 、 ".join(urls)
                tail = (
                    f"已为你打开手动下载页({page}),可一键复制地址;"
                    if page
                    else ""
                )
                return Err(
                    SdkError(
                        f"下载失败(含码云镜像回落)。{tail}手动下载地址: {urls_text}。"
                        f"原因: {got.get('error', '')}"
                    )
                )
            exe_path = got["path"]
            source = f"{source} ({got['bytes']} bytes, via {got['via']})"

        # 按文件头分流:官方 SFX 安装器(MZ)交给安装器跑;
        # 7z 压缩包用内置 7zr 解压;旧的 tar.xz 用标准库兜底。
        kind = _archive_kind(exe_path)
        if kind == "exe":
            ran = await asyncio.to_thread(self._run_setup_exe, exe_path)
            if not ran.get("ok"):
                return Err(SdkError(
                    f"运行安装程序失败: {ran.get('error', '')}{await self._running_hint()}"
                ))
            info = await self._wait_installed(target, 120.0)
            if not info["installed"]:
                return Err(SdkError(
                    "安装程序已结束,但 120 秒内未检测到 start.cmd;请手动运行安装包确认"
                    + (await self._running_hint())
                ))
            files = info["files"]
        else:
            if kind == "7z":
                packed = await asyncio.to_thread(self._install_from_7z, exe_path, target)
            else:
                packed = await asyncio.to_thread(self._install_from_archive, exe_path, target)
            if not packed.get("ok"):
                return Err(SdkError(f"解压安装失败: {packed.get('error', '')}"))
            info = await asyncio.to_thread(self._probe_install, target)
            if not info["installed"]:
                return Err(SdkError("解压完成,但未找到 start.cmd;压缩包可能不完整"))
            files = info["files"]
            self.logger.info(
                "已从压缩包({})安装 {} 个文件到 {}", kind, packed.get("files"), target
            )

        shortcut = {"ok": False, "skipped": True}
        if params.shortcut:
            shortcut = await asyncio.to_thread(self._make_shortcut, target)

        launched = False
        launch_msg = "未启动"
        if params.launch:
            health = await self._health()
            if health is not None:
                launch_msg = "工作台已在运行,无需重复启动"
            else:
                spawned = await asyncio.to_thread(self._spawn, target)
                if spawned.get("ok"):
                    health = await self._wait_health(25.0)
                    launched = health is not None
                    launch_msg = (
                        "已启动"
                        if launched
                        else "已发出启动命令,但 25 秒内未就绪(可稍后用 status 查看)"
                    )
                else:
                    launch_msg = f"启动失败: {spawned.get('error', '')}"

        return Ok({
            "ok": True,
            "already_installed": False,
            "target": target,
            "source": source,
            "files": files,
            "shortcut": bool(shortcut.get("ok")),
            "launched": launched,
            "url": _WORKBENCH_URL,
            "message": f"安装完成({files} 个文件);{launch_msg}",
        })

    # ── 入口:状态 ──
    @llm_tool(
        name="workbench_installer_status",
        description="查看「N.E.K.O. 插件工坊」(Agent 工作台)的安装与运行状态。"
        "当用户问工作台装没装、有没有在运行、安装在哪个目录时使用。",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "安装目录;留空=默认目录"},
            },
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="status",
        name="工作台状态",
        description="查看工作台是否已安装、是否在运行",
        timeout=30.0,
        llm_result_fields=["ok", "installed", "running", "target", "message", "url"],
    )
    async def status(self, target: str = "", **_):
        t = self._resolve_target(target)
        info = await asyncio.to_thread(self._probe_install, t)
        health = await self._health()
        running = health is not None
        if not info["installed"]:
            msg = "未安装;调用 install 入口即可安装"
        elif running:
            msg = "已安装,且工作台正在运行"
        else:
            msg = "已安装,但未运行;调用 launch 入口启动"
        return Ok({
            "ok": True,
            "installed": info["installed"],
            "running": running,
            "target": t,
            "files": info["files"],
            "size_mb": info["size_mb"],
            "url": _WORKBENCH_URL,
            "health": health,
            "message": msg,
        })

    # ── 入口:启动 ──
    @llm_tool(
        name="workbench_installer_launch",
        description="启动已安装的「N.E.K.O. 插件工坊」(Agent 工作台);已经在运行就直接返回访问地址。"
        "当用户想打开/启动工作台时使用。",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "安装目录;留空=默认目录"},
            },
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="launch",
        name="启动工作台",
        description="启动已安装的 Agent 工作台;已在运行则返回地址",
        timeout=60.0,
        llm_result_fields=["ok", "started", "already_running", "url", "message"],
    )
    async def launch(self, target: str = "", **_):
        t = self._resolve_target(target)
        info = await asyncio.to_thread(self._probe_install, t)
        if not info["installed"]:
            return Err(SdkError(f"未检测到已安装的工作台({t});请先调用 install 入口安装"))
        health = await self._health()
        if health is not None:
            return Ok({
                "ok": True,
                "started": False,
                "already_running": True,
                "url": _WORKBENCH_URL,
                "message": "工作台已在运行,直接打开地址即可",
            })
        spawned = await asyncio.to_thread(self._spawn, t)
        if not spawned.get("ok"):
            return Err(SdkError(str(spawned.get("error", "启动失败"))))
        health = await self._wait_health(25.0)
        if health is None:
            return Ok({
                "ok": True,
                "started": False,
                "already_running": False,
                "url": _WORKBENCH_URL,
                "message": "已发出启动命令,但 25 秒内未就绪,请稍后用 status 查看",
            })
        return Ok({
            "ok": True,
            "started": True,
            "already_running": False,
            "url": _WORKBENCH_URL,
            "message": "工作台已启动,浏览器打开地址即可使用",
        })
