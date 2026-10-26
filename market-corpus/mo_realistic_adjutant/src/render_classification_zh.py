from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = json.loads((ROOT / "combat_doctrine.json").read_text(encoding="utf-8"))
LABELS = {
    "allied": "\u76df\u519b", "soviet": "\u82cf\u519b", "epsilon": "\u5384\u666e\u897f\u9686", "foehn": "\u711a\u98ce", "unknown": "\u672a\u77e5\u9635\u8425",
    "opening": "\u5f00\u5c40", "tier2": "T2\u79d1\u6280", "tier3": "T3\u79d1\u6280", "late": "\u540e\u671f", "sudden_death": "\u51b3\u80dc\u9636\u6bb5",
    "starved": "\u8d44\u6e90\u7d27\u5f20", "stable": "\u7ecf\u6d4e\u7a33\u5b9a", "surplus": "\u7ecf\u6d4e\u76c8\u4f59", "behind": "\u5175\u529b\u843d\u540e", "even": "\u5175\u529b\u76f8\u5f53", "ahead": "\u5175\u529b\u9886\u5148", "open": "\u5f00\u9614\u5730", "chokepoint": "\u72ed\u7a84\u901a\u9053",
    "infantry": "\u6b65\u5175", "vehicle": "\u88c5\u7532\u8f66\u8f86", "aircraft": "\u7a7a\u519b", "building": "\u5efa\u7b51", "defense": "\u9632\u5fa1\u5efa\u7b51", "technology": "\u79d1\u6280\u5efa\u7b51", "superweapon": "\u8d85\u7ea7\u6b66\u5668", "resource": "\u7ecf\u6d4e\u5efa\u7b51", "unclassified": "\u672a\u5206\u7c7b",
}
ROLE = {"anti_armor":"\u53cd\u88c5\u7532", "heavy_anti_armor":"\u91cd\u578b\u53cd\u88c5\u7532", "area_anti_infantry":"\u8303\u56f4\u53cd\u6b65\u5175", "long_range":"\u8fdc\u7a0b\u706b\u529b", "air_strike":"\u7a7a\u4e2d\u6253\u51fb", "fighter":"\u6218\u6597\u673a", "mobile_anti_air":"\u673a\u52a8\u9632\u7a7a", "static_anti_air":"\u56fa\u5b9a\u9632\u7a7a", "detector":"\u63a2\u6d4b", "disabler":"\u63a7\u5236\u4e0e\u51cf\u901f", "repair_support":"\u4fee\u590d\u652f\u63f4", "combined_arms":"\u8054\u5408\u5175\u79cd", "scout":"\u4fa6\u5bdf", "fast_raider":"\u5feb\u901f\u88ad\u6270", "siege":"\u653b\u57ce\u706b\u529b", "long_range_anti_infantry":"\u8fdc\u7a0b\u53cd\u6b65\u5175", "mobile_suppression":"\u673a\u52a8\u538b\u5236", "snare":"\u9650\u901f\u4e0e\u7275\u5236", "heavy_armor":"\u91cd\u578b\u88c5\u7532", "production":"\u751f\u4ea7\u5efa\u7b51", "power":"\u7535\u529b\u5efa\u7b51"}

def tr(value: object) -> str:
    key = str(value or "")
    return LABELS.get(key, ROLE.get(key, "\u7efc\u5408\u6218\u672f"))

meta = DATA["metadata"]
lines = [
    "# \u6240\u6709\u6218\u7565\u5e93\u7684\u5206\u7c7b", "",
    "> \u672c\u6587\u6863\u4e0e `combat_doctrine.json` \u540c\u6e90\u751f\u6210\uff1b\u89c4\u5219\u53ea\u4f7f\u7528\u53ef\u89c1\u6218\u573a\u4fe1\u606f\uff0c\u5e76\u4e25\u683c\u9075\u5b88\u73a9\u5bb6\u6388\u6743\u3002", "",
    "## \u4e00\u3001\u89c4\u6a21\u4e0e\u68c0\u7d22\u987a\u5e8f", "",
    f"- \u6218\u7565\u6761\u76ee\u603b\u6570\uff1a**{meta['entry_count']}**\u3002", f"- \u6218\u5c40\u77e9\u9635\u6761\u76ee\uff1a**{meta['matrix_entry_count']}**\u3002", f"- \u771f\u5b9e TypeID \u4e13\u5c5e\u53cd\u5236\uff1a**{meta['unit_response_count']}**\u3002", "- \u68c0\u7d22\u987a\u5e8f\uff1a\u5b9e\u65f6\u53ef\u89c1\u4fe1\u606f \u2192 \u6218\u5c40\u77e9\u9635 \u2192 \u9ad8\u5a01\u80c1 TypeID \u6761\u6b3e \u2192 \u52a8\u4f5c\u9a8c\u8bc1\u3002", "",
    "## \u4e8c\u3001\u9635\u8425\u5206\u7c7b", "", "| \u9635\u8425 | \u6838\u5fc3\u5e94\u5bf9 |", "|---|---|",
]
for faction in ("allied", "soviet", "epsilon", "foehn", "unknown"):
    lines.append(f"| {tr(faction)}（`{faction}`） | \u6839\u636e\u654c\u65b9\u9635\u8425\u7279\u5f81\u9009\u62e9\u673a\u52a8\u3001\u53cd\u88c5\u7532\u3001\u53cd\u9690\u5f62\u6216\u53cd\u652f\u63f4\u65b9\u6848 |")
lines += ["", "## \u4e09\u3001\u6218\u5c40\u6761\u4ef6", "", "| \u9636\u6bb5 | \u7ecf\u6d4e | \u5175\u529b\u5173\u7cfb | \u5730\u5f62 | \u6267\u884c\u8981\u70b9 |", "|---|---|---|---|---|"]
for stage in ("opening", "tier2", "tier3", "late", "sudden_death"):
    for economy in ("starved", "stable", "surplus"):
        for ratio in ("behind", "even", "ahead"):
            for terrain in ("open", "chokepoint"):
                lines.append(f"| {tr(stage)} | {tr(economy)} | {tr(ratio)} | {tr(terrain)} | \u5148\u4fdd\u8bc1\u7ecf\u6d4e\u4e0e\u9632\u5fa1\uff0c\u518d\u6309\u654c\u65b9\u7c7b\u578b\u5e76\u884c\u751f\u4ea7\u53cd\u5236\u529b\u91cf |")
lines += ["", "## \u56db\u3001TypeID \u4e13\u5c5e\u53cd\u5236", "", "| TypeID | \u654c\u65b9\u804c\u80fd | \u79d1\u6280\u7b49\u7ea7 | \u53cd\u5236\u89d2\u8272 | \u4f18\u5148\u76ee\u6807 |", "|---|---|---:|---|---|"]
for item in DATA.get("unit_responses", []):
    if item.get("force_ratio") != "even" or item.get("terrain") != "open":
        continue
    lines.append(f"| `{item.get('enemy_type_id')}` | {tr(item.get('enemy_role'))} | {item.get('enemy_tech_level', '')} | {'、'.join(tr(x) for x in item.get('production_roles', []))} | {'、'.join(tr(x) for x in item.get('target_priorities', []))} |")
lines += ["", "## \u4e94\u3001\u6743\u9650\u4e0e\u52a8\u4f5c\u9a8c\u8bc1", "", "1. \u672a\u83b7\u5f97\u73a9\u5bb6\u660e\u786e\u6388\u6743\u65f6\uff0c\u4e0d\u542f\u52a8\u81ea\u4e3b\u53d1\u5c55\u3001\u652f\u63f4\u6280\u80fd\u6216\u81ea\u4e3b\u8fdb\u653b\u3002", "2. \u5efa\u7b51\u3001\u652f\u63f4\u6280\u80fd\u3001\u6b65\u5175\u3001\u8f66\u8f86\u3001\u7a7a\u519b\u3001\u6d77\u519b\u516d\u6761\u4ea7\u7ebf\u72ec\u7acb\u8f6e\u8be2\u3002", "3. \u6865\u63a5\u63a5\u53d7\u4e0d\u7b49\u4e8e\u6e38\u620f\u5b8c\u6210\uff0c\u5fc5\u987b\u901a\u8fc7\u540e\u7eed\u9065\u6d4b\u786e\u8ba4\u3002", "4. \u76ee\u6807\u6d88\u5931\u6216\u88ab\u8bc6\u522b\u4e3a\u4e2d\u7acb\u573a\u666f\u7269\u4ef6\u540e\uff0c\u65e7\u547d\u4ee4\u7acb\u5373\u5931\u6548\u5e76\u91cd\u65b0\u8bc4\u4f30\u3002"]
(ROOT / "\u6240\u6709\u6218\u7565\u5e93\u7684\u5206\u7c7b.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
