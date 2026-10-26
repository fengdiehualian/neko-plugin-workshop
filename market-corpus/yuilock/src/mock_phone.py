"""模拟手机端 Yui Lock 协议（挑战-应答认证：proof 绑定 cmd|nonce|challenge，按连接隔离）。

用法：python mock_phone.py [端口] [token]   （token 为空 = 模拟手机未设令牌）
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 48912
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""


def auth(token: str, cmd: str, nonce: str, challenge: str) -> str:
    msg = f"{cmd}|{nonce}|{challenge}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()


async def handle(reader, writer):
    # 挑战按连接隔离：一条连接上的验证互不打断
    session = {"challenge": "", "cmd": "", "nonce": ""}
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                req = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            cmd = req.get("cmd")
            if cmd in ("lock", "applock", "unlock"):
                if not TOKEN:
                    resp = {"ok": False, "error": "手机未设置令牌，拒绝控制命令"}
                elif not req.get("proof"):
                    session["challenge"] = secrets.token_hex(16)
                    session["cmd"] = cmd
                    session["nonce"] = str(req.get("nonce", ""))
                    resp = {"ok": False, "error": "需要配对验证", "auth": "hmac-sha256",
                            "challenge": session["challenge"]}
                else:
                    valid = (session["challenge"]
                             and session["cmd"] == cmd
                             and session["nonce"] == str(req.get("nonce", "")))
                    want = auth(TOKEN, cmd, str(req.get("nonce", "")),
                                session["challenge"]) if valid else ""
                    ch = session["challenge"]
                    nonce = str(req.get("nonce", ""))
                    session["challenge"] = ""
                    if valid and hmac.compare_digest(want, str(req.get("proof")).lower()):
                        resp = {"ok": True, "action": cmd}
                    else:
                        resp = {"ok": False, "error": "令牌验证失败"}
                    if TOKEN:
                        # 失败与成功都签名（resp|cmd|nonce|challenge|ok|error），客户端全量核验
                        flag = "1" if resp.get("ok") else "0"
                        err = str(resp.get("error", ""))
                        resp["sig"] = hmac.new(
                            TOKEN.encode(),
                            f"resp|{cmd}|{nonce}|{ch}|{flag}|{err}".encode(),
                            hashlib.sha256).hexdigest()
            else:
                resp = {"ok": True, "action": cmd}
                if cmd == "ping":
                    resp.update({"device": "MockPhone", "android": "14", "admin": True,
                                 "applock": False, "battery": 88, "token_set": bool(TOKEN)})
            print("recv:", req, flush=True)
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
    finally:
        writer.close()


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    print(f"mock phone listening on {PORT} (token={'set' if TOKEN else 'EMPTY'})", flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
