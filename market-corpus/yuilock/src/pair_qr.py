"""电脑端配对二维码：运行后弹出二维码窗口，用手机 Yui Lock「扫码配对」扫一扫，
端口和配对令牌就会自动填好，不用手动输入。

用法（在插件目录下）：
    uv run --with qrcode --with pillow python pair_qr.py
或已安装依赖时：
    python pair_qr.py [--host 局域网IP] [--port 48912] [--token 令牌] [--no-save]

- 配置里 token 为空时会随机生成一个，并自动写回 plugin.toml 的 [yuilock] token
  （写回后请在插件卡片上点「重载」使其生效；--no-save 可跳过写回）。
- 二维码图片保存在系统临时目录（不落在插件目录里，避免令牌被一起提交/分享）。
"""
from __future__ import annotations

import argparse
import re
import secrets
import socket
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def detect_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def load_config() -> dict:
    try:
        with open(ROOT / "plugin.toml", "rb") as f:
            data = tomllib.load(f)
        section = data.get("yuilock")
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def save_token_to_toml(token: str) -> bool:
    """把生成的 token 写回 plugin.toml 的 [yuilock] 段（只替换该段内的空 token）。"""
    path = ROOT / "plugin.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception:
        return False
    in_section = False
    pattern = re.compile(r"^(\s*token\s*=\s*)\"\"\s*$")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == "[yuilock]"
            continue
        if in_section and pattern.match(line):
            lines[i] = pattern.sub(r'\g<1>"%s"' % token, line, count=1)
            try:
                path.write_text("".join(lines), encoding="utf-8")
                return True
            except Exception:
                return False
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Yui Lock 配对二维码")
    ap.add_argument("--host", help="局域网 IP（默认自动探测）")
    ap.add_argument("--port", type=int, help="端口（默认读配置，其次 48912）")
    ap.add_argument("--token", help="配对令牌（默认读配置，为空则随机生成并写回配置）")
    ap.add_argument("--no-save", action="store_true", help="不把生成的令牌写回 plugin.toml")
    args = ap.parse_args()

    cfg = load_config()
    host = args.host or detect_lan_ip()
    port = args.port or int(cfg.get("port", 48912) or 48912)
    token = args.token or str(cfg.get("token", "") or "")
    if not token:
        token = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(12))
        if args.no_save:
            print(f"[!] 配置里没有 token，已随机生成：{token}")
            print("    (--no-save) 未写回，请自行同步到 plugin.toml 的 [yuilock] token")
        elif save_token_to_toml(token):
            print(f"[!] 配置里没有 token，已随机生成并写回 plugin.toml：{token}")
            print("    请在插件管理器里对本插件点「重载」使其生效。")
        else:
            print(f"[!] 配置里没有 token，已随机生成：{token}")
            print(f"    [!] 写回 plugin.toml 失败，请手动把 token = \"{token}\" 填进 [yuilock] 段。")

    payload = f"yuilock://pair?h={host}&p={port}&t={token}"
    print(f"配对信息：{payload}")
    print(f"如果手机扫码不便，也可以手动在 App 里填：端口 {port}，令牌 {token}")

    try:
        import qrcode
    except ImportError:
        print("[!] 缺少依赖：uv run --with qrcode --with pillow python pair_qr.py")
        print("    或先安装：pip install qrcode pillow")
        sys.exit(1)

    qr = qrcode.QRCode(border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image()

    # 二维码里含令牌：存到系统临时目录，不放插件目录（避免被提交/分享带走）
    png_path = Path(tempfile.gettempdir()) / "yuilock_pair_qr.png"
    try:
        img.save(png_path)
        print(f"二维码已保存：{png_path}")
    except Exception as exc:
        print(f"[!] 二维码保存失败（不影响窗口显示）：{exc}")

    shown = False
    try:
        import tkinter as tk

        from PIL import ImageTk

        root = tk.Tk()
        root.title("Yui Lock 配对二维码")
        root.attributes("-topmost", True)
        tk.Label(root, text=f"{host}:{port} · 用手机 Yui Lock「扫码配对」",
                 font=("Microsoft YaHei UI", 11)).pack(padx=16, pady=(12, 6))
        photo = ImageTk.PhotoImage(img)
        tk.Label(root, image=photo).pack(padx=16, pady=(0, 12))
        root.mainloop()
        shown = True
    except Exception as exc:
        print(f"[!] 窗口显示失败（{exc}），改用终端字符二维码：")
        qr.print_ascii(invert=True)

    if not shown:
        input("按回车退出...")


if __name__ == "__main__":
    main()
