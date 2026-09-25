# -*- coding: utf-8 -*-
"""HTTP 请求工具：GET/POST/HEAD + 文件下载，返回状态码/响应头/正文。"""

from __future__ import annotations

import ipaddress
import json as _json
import os
import socket
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# 响应体读取上限：LLM 可指向任意 URL，无上限 read() 会把大文件整个
# 拽进内存（OOM）。截断展示字符数是另一回事，这里限的是真实读取量。
_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def _url_host_resolves_private(url: str) -> bool:
    """URL 的 host 是否解析到 loopback / 私有 / 链路本地地址。

    SSRF 防护：这个工具是给 LLM 访问公网用的，默认拒绝内网目标
    （127.0.0.1 的 N.E.K.O 主程序管理面、169.254.169.254 云元数据、
    局域网管理面板等）。解析失败的域名按"不私有"放行，交给后续
    urlopen 的正常失败路径。
    """
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").strip()
        if not host:
            return True
        if host == "localhost":
            return True
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr.split("%")[0])
            except ValueError:
                continue
            if (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return True
    except Exception:
        return False
    return False


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str = "",
    json: Any = None,
    timeout: float = 15.0,
    max_response_chars: int = 10000,
) -> dict[str, Any]:
    """发送 HTTP 请求。

    url: 目标 URL
    method: GET/POST/HEAD
    headers: 额外请求头
    body: 请求正文（文本）
    json: JSON 请求体（与 body 二选一）
    timeout: 超时秒数
    max_response_chars: 响应正文截断上限
    """
    method = method.upper()
    if method not in ("GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"):
        return {"ok": False, "error": f"不支持的 HTTP 方法：{method}"}

    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}

    if _url_host_resolves_private(url):
        return {
            "ok": False,
            "error": "拒绝访问内网/回环地址（SSRF 防护）：" + url,
            "url": url,
        }

    try:
        data = None
        if json is not None:
            body = _json.dumps(json, ensure_ascii=False)
            if headers is None:
                headers = {}
            headers.setdefault("Content-Type", "application/json")

        if body:
            data = body.encode("utf-8")

        req = Request(url, data=data, method=method)
        req.add_header("User-Agent", "N.E.K.O-KeyboardController/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        started = time.time()
        resp = urlopen(req, timeout=timeout)
        elapsed = round(time.time() - started, 3)

        response_headers = dict(resp.headers)
        content_type = resp.headers.get("Content-Type", "")

        # 读取响应正文（限量：防止大响应把内存吃光）
        raw = resp.read(_MAX_BODY_BYTES)
        read_limit_hit = len(raw) == _MAX_BODY_BYTES
        resp_body = ""
        if "text" in content_type or "json" in content_type or "xml" in content_type:
            try:
                resp_body = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    resp_body = raw.decode("gbk", errors="replace")
                except Exception:
                    resp_body = raw.decode("latin-1", errors="replace")
        elif "image" in content_type or "audio" in content_type or "video" in content_type:
            resp_body = f"[二进制数据: {len(raw)} bytes, content-type={content_type}]"
        else:
            try:
                resp_body = raw.decode("utf-8", errors="replace")
            except Exception:
                resp_body = f"[二进制数据: {len(raw)} bytes]"

        truncated = len(resp_body) > max_response_chars
        if truncated:
            resp_body = resp_body[:max_response_chars] + f"\n\n... [截断，共 {len(raw)} 字符]"
        if read_limit_hit:
            resp_body += f"\n\n... [响应体超过 {_MAX_BODY_BYTES // 1024 // 1024}MB 读取上限，已截断]"

        return {
            "ok": True,
            "status_code": resp.status,
            "reason": resp.reason,
            "headers": response_headers,
            "content_type": content_type,
            "body": resp_body,
            "body_bytes": len(raw),
            "truncated": truncated,
            "elapsed_s": elapsed,
            "url": url,
            "method": method,
        }
    except URLError as e:
        return {"ok": False, "error": str(e.reason), "url": url, "method": method}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url, "method": method}


def download_file(
    url: str,
    path: str,
    timeout: float = 60.0,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    """下载文件到工作区。

    url: 文件 URL
    path: 保存路径。调用方（dispatch 层）必须先用 file_ops.resolve_path
          把它约束到插件工作区内 —— 本函数不再接受任意绝对路径，
          否则就是绕过 write_file 沙箱的文件写入旁路。
    timeout: 超时秒数
    max_bytes: 下载大小上限（防止无上限 read() 把内存吃光）
    """
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}

    if _url_host_resolves_private(url):
        return {
            "ok": False,
            "error": "拒绝从内网/回环地址下载（SSRF 防护）：" + url,
            "url": url,
        }

    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "N.E.K.O-KeyboardController/1.0")
        started = time.time()
        resp = urlopen(req, timeout=timeout)
        elapsed = round(time.time() - started, 3)

        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return {
                "ok": False,
                "error": f"文件超过下载上限（>{max_bytes // 1024 // 1024}MB），已取消",
                "url": url,
                "path": path,
            }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)

        return {
            "ok": True,
            "path": path,
            "size_bytes": len(raw),
            "size_kb": round(len(raw) / 1024, 1),
            "status_code": resp.status,
            "content_type": resp.headers.get("Content-Type", ""),
            "elapsed_s": elapsed,
            "url": url,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url, "path": path}


def ping_host(host: str, count: int = 4, timeout: float = 10.0) -> dict[str, Any]:
    """Ping 主机，返回延迟统计。

    host: 主机名或 IP
    count: 发包数（默认 4，钳制 1..10）
    timeout: 总超时秒数
    """
    import re
    import subprocess

    try:
        count = max(1, min(int(count or 4), 10))
        if sys.platform == "win32":
            cmd = ["ping", "-n", str(count), "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", str(count), "-W", str(int(timeout)), host]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        output = result.stdout

        # 解析延迟。只认"回复行"里的 time=/时间=（zh-CN Windows 输出
        # `时间=13ms`）；不能退回裸 `(\d+)ms` 全文匹配 —— 那会把统计行
        # `最短 = 0ms, 最长 = 0ms, 平均 = 0ms` 也吸进样本，min/max/avg
        # 全部失真。
        times = re.findall(r"(?:time|时间)[=<]\s*(\d+\.?\d*)\s*ms", output, re.IGNORECASE)

        latencies = [float(t) for t in times]
        stats = {}
        if latencies:
            stats = {
                "min_ms": round(min(latencies), 1),
                "max_ms": round(max(latencies), 1),
                "avg_ms": round(sum(latencies) / len(latencies), 1),
                "loss_pct": round((count - len(latencies)) / count * 100, 1),
            }

        return {
            "ok": result.returncode == 0,
            "host": host,
            "reachable": result.returncode == 0,
            "latencies": stats,
            "raw": output.strip()[-500:],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "host": host}