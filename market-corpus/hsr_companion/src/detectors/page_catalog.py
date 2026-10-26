"""Page families, not a guessed popularity ranking or per-character rules.

Inventory references (manually authored predicates; no upstream code/assets):
https://www.miyoushe.com/sr/article/60331284
https://github.com/LmeSzinc/StarRailCopilot/blob/master/tasks/base/page.py
Only exact heading + independent controls establish these generic menus.
"""

COMMUNITY_PAGE_RULES = tuple(
    {"page_type": page, "scene": "menu", "label": label,
     "strong": headings, "anchors": controls, "minimum": 2,
     "heading_required": True, "required_anchors": 1}
    for page, label, headings, controls in (
        ("mission_menu", "任务列表与详情", ("任务", "任务详情"),
         ("开拓任务", "冒险任务", "同行任务", "命路歧图", "停止追踪", "追踪任务", "任务奖励")),
        ("guide", "星际和平指南", ("星际和平指南",),
         ("每日实训", "生存索引", "行动摘要", "逐光捡金", "战术训练", "行动简报")),
        ("battle_pass", "无名勋礼", ("无名勋礼", "无名客的荣勋", "无名客的赠礼"),
         ("本周任务", "每日任务", "本期任务", "领取奖励", "无名客的荣勋", "无名客的赠礼")),
        ("map_menu", "地图", ("地图", "星轨航图"),
         ("传送", "空间锚点", "地图图例", "导航", "星轨航图")),
        ("tutorial", "教程", ("教程", "教程详情"),
         ("战斗教程", "探索教程", "教程奖励", "教程目录", "领取奖励")),
        ("messages", "短信与消息", ("短信", "消息"),
         ("联系人", "全部已读", "聊天记录", "回复")),
        ("camera", "拍照", ("拍照", "相机"),
         ("隐藏界面", "拍摄", "镜头", "表情", "动作")),
        ("synthesize", "合成", ("合成", "万能合成机"),
         ("消耗品合成", "材料合成", "材料置换", "遗器合成", "合成材料")),
        ("settings", "设置", ("设置",),
         ("画面设置", "声音设置", "按键设置", "其他设置", "账户设置")),
        ("shop", "商店", ("商店",),
         ("星芒兑换", "余烬兑换", "黑塔的商店", "礼包", "购买")),
        ("phone_menu", "手机菜单", ("开拓等级",),
         ("好友", "成就", "智库", "设置", "合成", "委托")),
    )
)
