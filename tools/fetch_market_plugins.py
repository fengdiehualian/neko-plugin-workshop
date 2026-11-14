"""拉全量在架插件清单(市场 API 全分页).用法: python tools/fetch_market_plugins.py --out plugins_all.json"""
import argparse
import json
import urllib.request

API = "https://market.project-neko.cn/api/v1/plugins"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plugins_all.json")
    a = ap.parse_args()
    items, page = [], 1
    while True:
        d = json.load(urllib.request.urlopen(f"{API}?page={page}&page_size=20", timeout=30))
        items += d["items"]
        if not d.get("has_next"):
            break
        page += 1
        if page > 200:
            raise SystemExit("page loop runaway")
    json.dump(items, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("collected", len(items), "of", d["total"])


if __name__ == "__main__":
    main()
