"""NLP 引擎 100 条家庭智能家居自然语言用例（端到端 route()）。

覆盖 route() 五步短路的六大分支：
  scene（场景/口语别名）/ query（查询）/ switch（开关）/ action（动作动词）/
  control（属性/模式/相对调整/极值/颜色）/ unknown（无法理解）。

每条用例断言 branch + 该分支的关键字段（场景名 / 设备提示 / 解析属性 / 值 /
方向 / delta / 匹配设备 / 歧义状态）。运行方式见 AGENTS.md（staging 拷贝）。
"""

import pytest

# isort: split
from nlp.router import route

# 模拟 devices_cache.json 内容：14 台家庭智能家居设备，含房间/别名/属性/动作
MOCK_DEVICES = [
    {
        "did": "bedroom-light-1",
        "name": "卧室灯",
        "model": "light.wyze",
        "room_name": "卧室",
        "is_online": True,
        "alias": "床头灯,夜灯",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Brightness", "access": "read_write", "type": "uint8", "value_range": [0, 100, 1]},
            {"siid": 2, "piid": 2, "name": "Color Temperature", "access": "read_write", "type": "uint32", "value_range": [2700, 6500, 100]},
        ],
        "actions": [],
    },
    {
        "did": "living-room-light-1",
        "name": "客厅灯",
        "model": "light.wyze",
        "room_name": "客厅",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Brightness", "access": "read_write", "type": "uint8", "value_range": [0, 100, 1]},
        ],
        "actions": [],
    },
    {
        "did": "living-ac-1",
        "name": "客厅空调",
        "model": "ac.midea.fz",
        "room_name": "客厅",
        "is_online": True,
        "alias": "空调",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Target Temperature", "access": "read_write", "type": "float", "value_range": [16, 30, 1]},
            {"siid": 2, "piid": 2, "name": "Mode", "access": "read_write", "type": "string", "value_list": ["制冷", "制热", "自动", "送风"]},
        ],
        "actions": [],
    },
    {
        "did": "bedroom-ac-1",
        "name": "卧室空调",
        "model": "ac.midea.fz",
        "room_name": "卧室",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Target Temperature", "access": "read_write", "type": "float", "value_range": [16, 30, 1]},
        ],
        "actions": [],
    },
    {
        "did": "study-tv-1",
        "name": "书房电视",
        "model": "miot.tv.v2",
        "room_name": "书房",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 1, "piid": 2, "name": "Power", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
    {
        "did": "living-tv-1",
        "name": "客厅电视",
        "model": "miot.tv.v2",
        "room_name": "客厅",
        "is_online": False,
        "alias": "",
        "properties": [
            {"siid": 1, "piid": 2, "name": "Power", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
    {
        "did": "vacuum-1",
        "name": "扫地机",
        "model": "roborock.vacuum.a10",
        "room_name": "客厅",
        "is_online": True,
        "alias": "扫地机器人",
        "properties": [],
        "actions": [
            {"siid": 2, "aiid": 1, "name": "Start Sweep"},
            {"siid": 2, "aiid": 2, "name": "Pause Sweeping"},
            {"siid": 2, "aiid": 3, "name": "Stop Sweeping"},
            {"siid": 2, "aiid": 4, "name": "Start Charge"},
        ],
    },
    {
        "did": "dryer-1",
        "name": "烘干机",
        "model": "midea.dryer.d10",
        "room_name": "阳台",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Temperature", "access": "read_write", "type": "uint8", "value_range": [30, 90, 5]},
        ],
        "actions": [],
    },
    {
        "did": "purifier-1",
        "name": "空气净化器",
        "model": "zhimi.airpurifier.m1",
        "room_name": "客厅",
        "is_online": True,
        "alias": "净化器",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Fan Level", "access": "read_write", "type": "uint8", "value_range": [1, 3, 1]},
            {"siid": 3, "piid": 1, "name": "PM2.5", "access": "read", "type": "uint16"},
        ],
        "actions": [],
    },
    {
        "did": "humidifier-1",
        "name": "加湿器",
        "model": "deerma.humidifier.jsq",
        "room_name": "卧室",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Humidity", "access": "read_write", "type": "uint8", "value_range": [30, 80, 1]},
        ],
        "actions": [],
    },
    {
        "did": "fridge-1",
        "name": "冰箱",
        "model": "midea.fridge.v1",
        "room_name": "厨房",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Temperature", "access": "read_write", "type": "int8", "value_range": [2, 8, 1]},
        ],
        "actions": [],
    },
    {
        "did": "washer-1",
        "name": "洗衣机",
        "model": "midea.washer.v1",
        "room_name": "卫生间",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 2, "name": "Mode", "access": "read_write", "type": "string", "value_list": ["快洗", "标准洗", "轻柔"]},
        ],
        "actions": [],
    },
    {
        "did": "curtain-1",
        "name": "客厅窗帘",
        "model": "mijia.curtain.v1",
        "room_name": "客厅",
        "is_online": True,
        "alias": "窗帘",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Switch Status", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
    {
        "did": "bedroom-curtain-1",
        "name": "卧室窗帘",
        "model": "mijia.curtain.v1",
        "room_name": "卧室",
        "is_online": True,
        "alias": "",
        "properties": [
            {"siid": 2, "piid": 1, "name": "Switch Status", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
]


def _check(result, expect: dict) -> None:
    """按期望键断言 RouteResult；parsed 字段走 result.parsed，匹配走 result.match。"""
    for key, val in expect.items():
        if key == "match_did":
            assert result.match is not None
            assert result.match.status == "ok"
            assert result.match.devices[0]["did"] == val
        elif key == "match_status":
            assert result.match is not None
            assert result.match.status == val
        elif key in ("prop", "value", "direction", "delta"):
            assert result.parsed is not None
            assert getattr(result.parsed, key) == val
        else:
            assert getattr(result, key) == val


# (case_id, 名称, 指令, 期望断言)
CASES = [
    # ── 场景 scene（01-10）──
    ("scene-01", "执行回家模式", "执行回家模式", {"branch": "scene", "scene_name": "回家模式"}),
    ("scene-02", "运行离家模式", "运行离家模式", {"branch": "scene", "scene_name": "离家模式"}),
    ("scene-03", "触发观影模式", "触发观影模式", {"branch": "scene", "scene_name": "观影模式"}),
    ("scene-04", "口语别名：我回家了", "我回家了", {"branch": "scene", "scene_name": "回家"}),
    ("scene-05", "口语别名：我到家了", "我到家了", {"branch": "scene", "scene_name": "回家"}),
    ("scene-06", "口语别名：我回来了", "我回来了", {"branch": "scene", "scene_name": "回家"}),
    ("scene-07", "口语别名：晚安", "晚安", {"branch": "scene", "scene_name": "睡眠"}),
    ("scene-08", "口语别名：睡觉了", "睡觉了", {"branch": "scene", "scene_name": "睡眠"}),
    ("scene-09", "打开回家模式", "打开回家模式", {"branch": "scene", "scene_name": "回家"}),
    ("scene-10", "启动睡眠场景", "启动睡眠场景", {"branch": "scene", "scene_name": "睡眠"}),

    # ── 查询 query（11-25）──
    ("query-01", "空调多少度", "空调多少度", {"branch": "query", "device_hint": "空调", "query_prop": "温度"}),
    ("query-02", "空调几度", "空调几度", {"branch": "query", "device_hint": "空调", "query_prop": "温度"}),
    ("query-03", "卧室灯多亮", "卧室灯多亮", {"branch": "query", "device_hint": "卧室灯", "query_prop": "亮度"}),
    ("query-04", "卧室灯多暗", "卧室灯多暗", {"branch": "query", "device_hint": "卧室灯", "query_prop": "亮度"}),
    ("query-05", "空调多热", "空调多热", {"branch": "query", "device_hint": "空调", "query_prop": "温度"}),
    ("query-06", "空调多冷", "空调多冷", {"branch": "query", "device_hint": "空调", "query_prop": "温度"}),
    ("query-07", "空调怎么样", "空调怎么样", {"branch": "query", "device_hint": "空调", "query_prop": ""}),
    ("query-08", "空调什么状态", "空调什么状态", {"branch": "query", "device_hint": "空调", "query_prop": ""}),
    ("query-09", "空调是多少", "空调是多少", {"branch": "query", "device_hint": "空调", "query_prop": ""}),
    ("query-10", "空调查询", "空调查询", {"branch": "query", "device_hint": "空调", "query_prop": ""}),
    ("query-11", "冰箱剩余电量", "冰箱剩余电量", {"branch": "query", "device_hint": "冰箱", "query_prop": ""}),
    ("query-12", "扫地机还有多久", "扫地机还有多久", {"branch": "query", "device_hint": "扫地机", "query_prop": ""}),
    ("query-13", "扫地机还剩多少时间", "扫地机还剩多少时间", {"branch": "query", "device_hint": "扫地机", "query_prop": ""}),
    ("query-14", "卧室空调温度多少度", "卧室空调温度多少度", {"branch": "query", "device_hint": "卧室空调", "query_prop": "温度"}),
    ("query-15", "卧室灯亮度是多少", "卧室灯亮度是多少", {"branch": "query", "device_hint": "卧室灯", "query_prop": "亮度"}),

    # ── 开关 switch（26-50）──
    ("switch-01", "打开卧室灯", "打开卧室灯", {"branch": "switch", "value": True, "match_did": "bedroom-light-1"}),
    ("switch-02", "开启卧室灯", "开启卧室灯", {"branch": "switch", "value": True, "match_did": "bedroom-light-1"}),
    ("switch-03", "开卧室灯", "开卧室灯", {"branch": "switch", "value": True, "match_did": "bedroom-light-1"}),
    ("switch-04", "关闭卧室灯", "关闭卧室灯", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-05", "关掉卧室灯", "关掉卧室灯", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-06", "关卧室灯", "关卧室灯", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-07", "句末动词：卧室灯打开", "卧室灯打开", {"branch": "switch", "value": True, "match_did": "bedroom-light-1"}),
    ("switch-08", "句末动词：卧室灯关闭", "卧室灯关闭", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-09", "句末动词：卧室灯关了", "卧室灯关了", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-10", "句末动词：卧室灯关上", "卧室灯关上", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-11", "礼貌前缀：请打开卧室灯", "请打开卧室灯", {"branch": "switch", "value": True, "match_did": "bedroom-light-1"}),
    ("switch-12", "礼貌前缀：帮我关掉卧室灯", "帮我关掉卧室灯", {"branch": "switch", "value": False, "match_did": "bedroom-light-1"}),
    ("switch-13", "把灯打开（多灯歧义）", "把灯打开", {"branch": "switch", "value": True, "match_status": "ambiguous"}),
    ("switch-14", "关掉客厅电视", "关掉客厅电视", {"branch": "switch", "value": False, "match_did": "living-tv-1"}),
    ("switch-15", "打开客厅窗帘", "打开客厅窗帘", {"branch": "switch", "value": True, "match_did": "curtain-1"}),
    ("switch-16", "关闭卧室窗帘", "关闭卧室窗帘", {"branch": "switch", "value": False, "match_did": "bedroom-curtain-1"}),
    ("switch-17", "打开客厅灯", "打开客厅灯", {"branch": "switch", "value": True, "match_did": "living-room-light-1"}),
    ("switch-18", "关闭卧室灯怎么样（防查询劫持）", "关闭卧室灯怎么样", {"branch": "switch", "value": False, "device_hint": "卧室灯", "match_did": "bedroom-light-1"}),
    ("switch-19", "打开空调（别名命中）", "打开空调", {"branch": "switch", "value": True, "match_did": "living-ac-1"}),
    ("switch-20", "关空调（别名命中）", "关空调", {"branch": "switch", "value": False, "match_did": "living-ac-1"}),
    ("switch-21", "打开书房电视", "打开书房电视", {"branch": "switch", "value": True, "match_did": "study-tv-1"}),
    ("switch-22", "打开卧室空调", "打开卧室空调", {"branch": "switch", "value": True, "match_did": "bedroom-ac-1"}),
    ("switch-23", "关闭卧室空调", "关闭卧室空调", {"branch": "switch", "value": False, "match_did": "bedroom-ac-1"}),
    ("switch-24", "打开冰箱", "打开冰箱", {"branch": "switch", "value": True, "match_did": "fridge-1"}),
    ("switch-25", "打开加湿器", "打开加湿器", {"branch": "switch", "value": True, "match_did": "humidifier-1"}),

    # ── 动作 action（51-60）──
    ("action-01", "开始扫地", "开始扫地", {"branch": "action", "verb": "开始", "match_did": "vacuum-1"}),
    ("action-02", "暂停扫地", "暂停扫地", {"branch": "action", "verb": "暂停", "match_did": "vacuum-1"}),
    ("action-03", "停止扫地", "停止扫地", {"branch": "action", "verb": "停止", "match_did": "vacuum-1"}),
    ("action-04", "继续扫地", "继续扫地", {"branch": "action", "verb": "继续", "match_did": "vacuum-1"}),
    ("action-05", "动词在尾部：扫地机开始清扫", "扫地机开始清扫", {"branch": "action", "verb": "开始", "match_did": "vacuum-1"}),
    ("action-06", "动词在尾部：扫地机停止清扫", "扫地机停止清扫", {"branch": "action", "verb": "停止", "match_did": "vacuum-1"}),
    ("action-07", "动词在尾部：扫地机回充", "扫地机回充", {"branch": "action", "verb": "回充", "match_did": "vacuum-1"}),
    ("action-08", "启动扫地机", "启动扫地机", {"branch": "action", "verb": "启动", "match_did": "vacuum-1"}),
    ("action-09", "烘干机开始烘干", "烘干机开始烘干", {"branch": "action", "verb": "开始", "match_did": "dryer-1"}),
    ("action-10", "扫地机开始扫地", "扫地机开始扫地", {"branch": "action", "verb": "开始", "match_did": "vacuum-1"}),

    # ── 属性/模式控制 control（61-92）──
    ("control-01", "空调调到26度", "空调调到26度", {"branch": "control", "prop": "温度", "value": 26, "match_did": "living-ac-1"}),
    ("control-02", "空调调26度（单字调分界）", "空调调26度", {"branch": "control", "prop": "温度", "value": 26, "match_did": "living-ac-1"}),
    ("control-03", "空调26度（纯数字分界）", "空调26度", {"branch": "control", "prop": "温度", "value": 26, "match_did": "living-ac-1"}),
    ("control-04", "空调设成26度", "空调设成26度", {"branch": "control", "prop": "温度", "value": 26, "match_did": "living-ac-1"}),
    ("control-05", "空调设置为26度", "空调设置为26度", {"branch": "control", "prop": "温度", "value": 26, "match_did": "living-ac-1"}),
    ("control-06", "卧室空调26度", "卧室空调26度", {"branch": "control", "prop": "温度", "value": 26, "match_did": "bedroom-ac-1"}),
    ("control-07", "冰箱调到5度", "冰箱调到5度", {"branch": "control", "prop": "温度", "value": 5, "match_did": "fridge-1"}),
    ("control-08", "空调制冷（模式）", "空调制冷", {"branch": "control", "prop": "模式", "value": "制冷", "match_did": "living-ac-1"}),
    ("control-09", "空调调制冷", "空调调制冷", {"branch": "control", "prop": "模式", "value": "制冷", "match_did": "living-ac-1"}),
    ("control-10", "空调自动模式", "空调自动模式", {"branch": "control", "prop": "模式", "value": "自动", "match_did": "living-ac-1"}),
    ("control-11", "空调切到制热", "空调切到制热", {"branch": "control", "prop": "模式", "value": "制热", "match_did": "living-ac-1"}),
    ("control-12", "空调调到睡眠模式", "空调调到睡眠模式", {"branch": "control", "prop": "模式", "value": "睡眠", "match_did": "living-ac-1"}),
    ("control-13", "洗衣机快洗", "洗衣机快洗", {"branch": "control", "prop": "模式", "value": "快洗", "match_did": "washer-1"}),
    ("control-14", "洗衣机标准洗", "洗衣机标准洗", {"branch": "control", "prop": "模式", "value": "标准洗", "match_did": "washer-1"}),
    ("control-15", "加湿器自动模式", "加湿器自动模式", {"branch": "control", "prop": "模式", "value": "自动", "match_did": "humidifier-1"}),
    ("control-16", "灯亮度50%（多灯歧义）", "灯亮度50%", {"branch": "control", "prop": "亮度", "value": 50, "match_status": "ambiguous"}),
    ("control-17", "灯调到50%（单位推断亮度）", "灯调到50%", {"branch": "control", "prop": "亮度", "value": 50, "match_status": "ambiguous"}),
    ("control-18", "卧室灯50%", "卧室灯50%", {"branch": "control", "prop": "亮度", "value": 50, "match_did": "bedroom-light-1"}),
    ("control-19", "卧室灯亮度调到80", "卧室灯亮度调到80", {"branch": "control", "prop": "亮度", "value": 80, "match_did": "bedroom-light-1"}),
    ("control-20", "空调温度调高一点（相对）", "空调温度调高一点", {"branch": "control", "prop": "温度", "direction": 1, "delta": None, "match_did": "living-ac-1"}),
    ("control-21", "空调温度调高5度", "空调温度调高5度", {"branch": "control", "prop": "温度", "direction": 1, "delta": 5.0, "match_did": "living-ac-1"}),
    ("control-22", "空调调低两度（中文数字）", "空调调低两度", {"branch": "control", "prop": "温度", "direction": -1, "delta": 2.0, "match_did": "living-ac-1"}),
    ("control-23", "灯调亮一点（多灯歧义）", "灯调亮一点", {"branch": "control", "prop": "亮度", "direction": 1, "match_status": "ambiguous"}),
    ("control-24", "空调调高一度", "空调调高一度", {"branch": "control", "prop": "温度", "direction": 1, "delta": 1.0, "match_did": "living-ac-1"}),
    ("control-25", "电视音量调大（多电视歧义）", "电视音量调大", {"branch": "control", "prop": "音量", "direction": 1, "match_status": "ambiguous"}),
    ("control-26", "卧室灯调暗一点", "卧室灯调暗一点", {"branch": "control", "prop": "亮度", "direction": -1, "match_did": "bedroom-light-1"}),
    ("control-27", "空调调到最冷（极值）", "空调调到最冷", {"branch": "control", "prop": "温度", "value": "min", "match_did": "living-ac-1"}),
    ("control-28", "空调调到最高温度（极值）", "空调调到最高温度", {"branch": "control", "prop": "温度", "value": "max", "match_did": "living-ac-1"}),
    ("control-29", "灯调到最亮（多灯歧义）", "灯调到最亮", {"branch": "control", "prop": "亮度", "value": "max", "match_status": "ambiguous"}),
    ("control-30", "灯调到红色（颜色）", "灯调到红色", {"branch": "control", "prop": "颜色", "value": 0xFF0000, "match_status": "ambiguous"}),
    ("control-31", "灯设成蓝色（颜色）", "灯设成蓝色", {"branch": "control", "prop": "颜色", "value": 0x0000FF, "match_status": "ambiguous"}),
    ("control-32", "加湿器湿度调到60%", "加湿器湿度调到60%", {"branch": "control", "prop": "湿度", "value": 60, "match_did": "humidifier-1"}),

    # ── 无法理解 unknown（93-100）──
    ("unknown-01", "帮我倒杯水", "帮我倒杯水", {"branch": "unknown"}),
    ("unknown-02", "播放音乐", "播放音乐", {"branch": "unknown"}),
    ("unknown-03", "把空调擦一下", "把空调擦一下", {"branch": "unknown"}),
    ("unknown-04", "扫地机去哪里了", "扫地机去哪里了", {"branch": "unknown"}),
    ("unknown-05", "你好", "你好", {"branch": "unknown"}),
    ("unknown-06", "播放电影", "播放电影", {"branch": "unknown"}),
    ("unknown-07", "我想睡觉", "我想睡觉", {"branch": "unknown"}),
    ("unknown-08", "扫地", "扫地", {"branch": "unknown"}),
]


@pytest.mark.parametrize(
    ("case_id", "name", "text", "expect"),
    [(c[0], c[1], c[2], c[3]) for c in CASES],
    ids=[f"{c[0]} {c[1]}" for c in CASES],
)
async def test_route_case(case_id: str, name: str, text: str, expect: dict) -> None:
    result = await route(text, MOCK_DEVICES)
    _check(result, expect)
