"""获取并解包 NapCat —— 插件本地实现，不依赖本体任何代码。

链路：下载（镜像优先，官方兜底）→ **校验 sha256 + 字节数** → 安全解包 → 原子提升。

两条硬约束：

1. **校验和是唯一的安全锚**。字节走第三方镜像（本机实测官方源 18 KB/s、镜像
   16 MB/s，差三个数量级），而 sha256 来自 GitHub 官方 API，钉死在本模块里。
   镜像只负责搬运，不负责证明；对不上就删掉临时文件报错。
2. **解包必须防 zip-slip**。压缩包来自第三方中转，内含条目名不可信 ——
   ``../../x`` 或绝对路径会写到目标目录之外。逐条校验，拒绝而不是"修正"。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import httpx

#: 钉死的版本与校验和（sha256 与字节数取自 GitHub 官方 Releases API，
#: 2026-09-11 抓取）。升级版本时两个值必须一起换。
NAPCAT_VERSION = "v4.18.19"
_RELEASE_BASE = f"https://github.com/NapNeko/NapCatQQ/releases/download/{NAPCAT_VERSION}"

#: 资产名 → (字节数, sha256)
PINNED_ASSETS: dict[str, tuple[int, str]] = {
    # 跨平台：含 linux/darwin/win32 的原生模块（所以 Windows 也能用），
    # 但**不含 node.exe**，任何平台都需要系统装 Node。
    #
    # 另一个候选 ``NapCat.Shell.Windows.Node.zip``（111.7 MiB）实测**不是同一个东西**：
    # 它顶层是 QQ NT 本体（wrapper.node + 一堆 DLL），外壳被塞进 ``napcat/`` 子目录 ——
    # 启动器在 ``napcat/launcher-user.bat`` 而不是根目录，且解压后 325 MB。
    # 既然统一走 Shell.zip，它的启动器就在解包根目录，与 find_launcher 的契约直接吻合。
    "NapCat.Shell.zip": (
        29482717,
        "c5b7423d1d5b8c555d62cd9e4059b1908cc0986e7b5c85a0f450f4a8ed170acf",
    ),
}

#: 镜像前缀，**按顺序尝试**。本机实测官方源 18 KB/s、``gh-proxy.com`` 约 16 MB/s，
#: 所以镜像在前、官方兜底。内网/自建加速可通过设置项追加前缀。
DEFAULT_MIRROR_PREFIXES: tuple[str, ...] = ("https://gh-proxy.com/", "")

#: 单次下载的上限时间。官方源慢到 26 分钟量级，但用户不该被无限期挂着 ——
#: 超时后回退下一个候选源。
DOWNLOAD_TIMEOUT_SECONDS = 300.0
_CHUNK = 65536


# ── 资产选择 ────────────────────────────────────────────────

def asset_name(*, windows: bool | None = None) -> str:
    """取哪个资产 —— **两个平台统一**用 ``NapCat.Shell.zip``。

    它是跨平台发行版（``native/`` 里同时带 linux/darwin/win32 的原生模块），
    代价是任何平台都需要系统装 Node。``windows`` 参数保留仅为签名稳定，
    将来若要按平台分资产，改这里一处即可。
    """
    return "NapCat.Shell.zip"


def official_url(asset: str) -> str:
    return f"{_RELEASE_BASE}/{asset}"


def candidate_urls(asset: str,
                   mirrors: Iterable[str] | None = None) -> list[str]:
    """候选下载地址，按尝试顺序（镜像在前、官方兜底）。

    ``mirrors`` 里空前缀表示官方源本身；重复项会被去掉。
    """
    prefixes = tuple(mirrors) if mirrors is not None else DEFAULT_MIRROR_PREFIXES
    url = official_url(asset)
    seen: list[str] = []
    for p in prefixes:
        candidate = f"{p}{url}" if p else url
        if candidate not in seen:
            seen.append(candidate)
    if url not in seen:
        seen.append(url)  # 无论怎么配，官方源永远是最后一道
    return seen


# ── 下载 ────────────────────────────────────────────────────

def sha256_of(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path | str, asset: str) -> tuple[bool, str]:
    """校验字节数与 sha256。返回 ``(是否通过, 说明)``。"""
    pinned = PINNED_ASSETS.get(asset)
    if pinned is None:
        return False, f"未知资产（没有钉死的校验和）: {asset}"
    expect_size, expect_hash = pinned
    p = Path(path)
    try:
        actual_size = p.stat().st_size
    except OSError as e:
        return False, f"读取失败: {e}"
    if actual_size != expect_size:
        return False, f"字节数不符: 实际 {actual_size}，期望 {expect_size}"
    actual_hash = sha256_of(p)
    if actual_hash != expect_hash:
        return False, f"sha256 不符: 实际 {actual_hash}，期望 {expect_hash}"
    return True, "ok"


async def download_asset(
    asset: str,
    dest_dir: Path | str,
    *,
    mirrors: Iterable[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    """下载并校验，返回落盘的 zip 路径。失败抛 ``RuntimeError``。

    先写 ``.part`` 再改名：中途失败/被杀不会留下一个看起来完整、实际半截的包
    （那种文件最坏 —— 校验会拦住它，但用户看到的是"下载成功了却用不了"）。

    **``.part`` 跨次保留、带 Range 续传。** 镜像实测会掉速并中途断流
    （``gh-proxy.com`` 同一天里从 16 MB/s 掉到 0.23 MB/s，且下到 20MB 处断掉），
    在这个速率下"从零重来"等于永远下不完。断流之后换下一个镜像也**接着已下到的
    字节往下走**。只有**校验不过**才丢弃 —— 那说明攒起来的字节本身就是错的。

    已经存在且校验通过的正式包**直接复用，不打网络**。既省一次下载，也是"手动把包
    放进来"的入口 —— 镜像不可用时那是唯一的路。
    """
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / asset
    part = out_dir / (asset + ".part")

    if final.exists():
        ok, _ = verify(final, asset)
        if ok:
            return final
        final.unlink(missing_ok=True)   # 坏包清掉，免得每次部署都白校验一遍

    # 有些镜像（实测 gh-proxy 就如此）走 chunked 传输、**不带 content-length**。
    # 那种情况下进度分母只能靠钉死的字节数，否则界面上就是一片 "0.0 / 0.0 MB"。
    fallback_total = PINNED_ASSETS.get(asset, (0, ""))[0]

    # 上一轮可能已经下满、只是没走到提升那一步：先验一次，别白打一遍网络。
    if part.exists() and fallback_total:
        size = part.stat().st_size
        if size == fallback_total:
            ok, _ = verify(part, asset)
            if ok:
                os.replace(part, final)
                return final
            part.unlink(missing_ok=True)    # 攒够了却验不过 → 这份数据是坏的
        elif size > fallback_total:
            part.unlink(missing_ok=True)    # 比钉死的还长（换过版本？）→ 只能重来

    errors: list[str] = []
    for url in candidate_urls(asset, mirrors):
        resume_from = part.stat().st_size if part.exists() else 0
        try:
            await _fetch(url, part, progress=progress, timeout=timeout,
                         fallback_total=fallback_total, resume_from=resume_from)
        except Exception as e:
            errors.append(f"{url.split('/')[2] if '://' in url else url}: {e}")
            continue        # **留着 .part** —— 下一个镜像/下一次接着下
        ok, why = verify(part, asset)
        if not ok:
            # 校验不过一律丢弃 —— 这是"镜像只搬运不证明"的落点。
            part.unlink(missing_ok=True)
            errors.append(f"{url}: 校验失败（{why}）")
            continue
        os.replace(part, final)
        return final

    raise RuntimeError("NapCat 下载失败：\n  " + "\n  ".join(errors or ["无可用下载源"]))


async def _fetch(url: str, dest: Path, *, progress: Callable[[int, int], None] | None,
                 timeout: float, fallback_total: int = 0, resume_from: int = 0) -> None:
    """把 ``url`` 下进 ``dest``；``resume_from > 0`` 时带 Range 接着写。

    服务端**不支持 Range 时会回 200 而不是 206** —— 那时必须从头写。把整包追加到
    半截文件后面会攒出一份"长度碰巧对得上、内容全错"的包，最后卡在校验上，而且
    每次重试都重演。
    """
    headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else None
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=True, max_redirects=5,
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            try:
                length = int(resp.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            offset = 0
            if resume_from > 0 and resp.status_code == 206:
                offset = resume_from
                # 206 的 content-length 只是**剩余**部分，总量要把它加回来
                total_expected = offset + length if length > 0 else fallback_total
            else:
                total_expected = length if length > 0 else fallback_total
            done = offset
            if progress:
                progress(done, total_expected)
            with open(dest, "ab" if offset else "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total_expected)


# ── 解包 ────────────────────────────────────────────────────

def safe_member_path(name: str, root: Path) -> Path | None:
    """把压缩包内的条目名解析成 root 下的绝对路径；不安全返回 ``None``。

    拒绝三类：绝对路径、盘符/UNC、以及解析后逃出 root 的（``../`` 穿越，zip-slip）。
    这里选择**拒绝**而不是"清洗成安全名" —— 清洗会让一个被篡改的包看起来装成功了，
    之后在别处以更难排查的方式炸掉。
    """
    if not name or name.endswith("/"):
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return None
    if len(normalized) > 1 and normalized[1] == ":":  # C:\... 之类
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    target = root.joinpath(*parts)
    try:
        resolved = target.resolve()
        base = root.resolve()
    except OSError:
        return None
    if resolved != base and base not in resolved.parents:
        return None
    return target


def extract_zip(zip_path: Path | str, target_dir: Path | str,
                *, progress: Callable[[int, int], None] | None = None) -> Path:
    """安全解包到 ``target_dir``（先解到同级 staging，再原子提升）。

    原子提升的理由：解到一半失败时，目标目录要么还是旧的完整版本、要么不存在，
    不会留下一个"一半新一半旧"的 NapCat —— 那种状态启动起来的报错和真因毫无关系。

    POSIX 上补可执行位：zip 格式不保存权限，``native/*.node`` 等需要 x 位。
    """
    src = Path(zip_path)
    final = Path(target_dir)
    staging = final.with_name(f"{final.name}.staging-{uuid.uuid4().hex[:12]}")

    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(src) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            total = len(infos)
            for idx, info in enumerate(infos, 1):
                dest = safe_member_path(info.filename, staging)
                if dest is None:
                    raise RuntimeError(f"压缩包内含不安全的条目名，拒绝解包: {info.filename!r}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d, length=1024 * 1024)
                if os.name != "nt":
                    mode = (info.external_attr >> 16) & 0o777
                    if mode:
                        os.chmod(dest, mode)
                if progress:
                    progress(idx, total)

        # 提升：旧目录先挪走（不是直接删 —— 删了就没法回滚），再改名，最后清掉旧的。
        backup = None
        if final.exists():
            backup = final.with_name(f"{final.name}.old-{uuid.uuid4().hex[:12]}")
            os.replace(final, backup)
        os.replace(staging, final)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
