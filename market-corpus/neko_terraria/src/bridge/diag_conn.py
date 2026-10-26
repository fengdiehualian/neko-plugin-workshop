"""连接层诊断脚本：独立验证 TCP 读循环 + 响应分发（不经过插件）。

用法：python diag_conn.py
"""
import asyncio
import json
import time


async def main():
    host, port = "127.0.0.1", 9877
    print(f"[diag] 连接 {host}:{port} ...")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=3.0)

    # 握手读 welcome
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=1.5)
        print(f"[diag] welcome: {line[:60]!r}")
    except asyncio.TimeoutError:
        print("[diag] welcome 超时（可能旧版 mod）")

    # 独立读循环（与插件相同的逻辑）
    pending = {}
    stop = asyncio.Event()

    async def read_loop():
        n = 0
        while not stop.is_set():
            try:
                line = await reader.readline()
            except Exception as e:
                print(f"[diag] read_loop 异常: {type(e).__name__}: {e}")
                break
            if not line:
                print("[diag] EOF")
                break
            n += 1
            try:
                resp = json.loads(line.decode("utf-8"))
                rid = resp.get("req_id")
                if rid is not None and rid in pending:
                    pending[rid].set_result(resp)
                elif resp.get("type") == "event":
                    print(f"[diag] 事件: {resp.get('event')}")
            except Exception:
                pass
        print(f"[diag] read_loop 结束, 共读 {n} 行")
        for f in pending.values():
            if not f.done():
                f.set_result(None)

    asyncio.create_task(read_loop())

    async def req(cmd, timeout=3.0):
        rid = int(time.time() * 1000) % 100000
        fut = asyncio.get_running_loop().create_future()
        pending[rid] = fut
        writer.write((json.dumps({**cmd, "req_id": rid}) + "\n").encode())
        await writer.drain()
        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
            return resp
        except asyncio.TimeoutError:
            print(f"[diag] req {cmd.get('cmd')} 超时")
            return None
        finally:
            pending.pop(rid, None)

    # 测试序列
    r = await req({"cmd": "get_server_info"}, timeout=5.0)
    print(f"[diag] get_server_info → {'OK: ' + str(r)[:80] if r else '失败'}")
    r = await req({"cmd": "join_status"}, timeout=3.0)
    print(f"[diag] join_status → {'OK: ' + str(r)[:80] if r else '失败'}")
    r = await req({"cmd": "get_network_info"}, timeout=3.0)
    print(f"[diag] get_network_info → {'OK: ' + str(r)[:80] if r else '失败'}")

    await asyncio.sleep(0.5)
    stop.set()
    writer.close()
    await asyncio.sleep(0.2)
    print("[diag] 完成")


if __name__ == "__main__":
    asyncio.run(main())
