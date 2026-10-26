"""Read-only local Codex app-server client; no credentials are read or copied."""

import json
import math
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path


def normalize(payload):
    buckets = payload.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else payload.get("rateLimits")
    if not isinstance(bucket, dict):
        raise ValueError("当前账号未返回 Codex 额度")
    windows = []
    for key in ("primary", "secondary"):
        item = bucket.get(key)
        if not isinstance(item, dict):
            continue
        used, minutes, reset = (item.get(k) for k in ("usedPercent", "windowDurationMins", "resetsAt"))
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        if type(minutes) is not int or minutes <= 0 or type(reset) is not int:
            continue
        label = "周额度" if minutes == 10080 else "5 小时额度" if minutes == 300 else f"{minutes} 分钟额度"
        windows.append(dict(label=label, remaining=max(0, min(100, 100 - used)), resets_at=reset, minutes=minutes))
    if not windows:
        raise ValueError("额度暂不可用")
    return {"windows": windows, "observed_at": time.time()}


def read_quota():
    candidates = [
        shutil.which("codex"),
        str(Path.home() / ".local/bin/codex"),
        "/Applications/Codex.app/Contents/Resources/codex",
    ]
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if not executable:
        raise ValueError("请先安装 Codex 并登录此电脑上的账号")
    process = subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        close_fds=False,
    )
    messages = queue.Queue()

    def collect():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
        messages.put(None)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()

    def send(data):
        process.stdin.write(json.dumps(data) + "\n")
        process.stdin.flush()

    def receive(identifier):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                message = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                raise ValueError("Codex 额度服务提前退出，请检查本机登录状态与运行权限")
            if message.get("id") == identifier:
                if "error" in message:
                    raise ValueError("无法读取 Codex 额度，请确认本机 Codex 已登录")
                return message["result"]
        raise ValueError("Codex 额度查询超时")

    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "catfood_quota", "version": "0.8.24"}}})
        receive(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        return normalize(receive(2))
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()
