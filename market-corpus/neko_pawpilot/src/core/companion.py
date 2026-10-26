"""陪玩维度引擎：把"陪玩"拆成十几个可轮转的话题维度。

设计定调（2026-10 主人反馈）：**"陪玩是方方面面的"**。
旧版把主动输出收得太紧——只有安全提醒能开口，剩下全是低频兜底，
自由驾驶（没接单）时更是几乎全被门控挡掉，玩起来就不像有人陪。

这里换一个模型：
- 每个维度 = 触发条件 + 冷却 + 播报类别 + 若干"角度"
- 命中后只产出一条**事实行**（客观数据 + 这次要用的角度），
  交给宿主 LLM 按当前人设演绎（respond 模式），所以同一维度也不会说重样
- 维度按类别挂到面板的「播报类别」开关上，关掉"闲聊"就真的不再闲聊
- 全局最小间隔 × 面板「播报频率」倍率，用户自己能调密度
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from .vehicle import trailer_config

# 最近说过的角度记忆长度：21 个维度共享一个窗口，保证"换着花样说"
RECENT_ANGLE_MEMORY = 14


@dataclass(frozen=True)
class Dimension:
    """一个陪玩维度。"""

    id: str
    label: str
    category: str                 # 复用面板的播报类别开关
    cooldown: float               # 本维度冷却（秒）
    priority: int                 # 越大越优先
    angles: Tuple[str, ...]       # 轮转角度：换着花样，不车轱辘话
    when: Callable[..., bool]     # (snap, ctx) -> 是否适用
    facts: Callable[..., str]     # (snap, ctx) -> 客观事实串

    def angle(self, idx: int) -> str:
        return self.angles[idx % len(self.angles)] if self.angles else "随口聊一句"


# ── 各维度的事实构造 ──────────────────────────────────────────────
def _clock(ctx: dict) -> str:
    return str(ctx.get("clock") or "")


def _hour(ctx: dict) -> int:
    return int(ctx.get("hour", -1))


def _cargo_pct(snap) -> int:
    return int(round((1.0 - float(snap.cargo_damage or 0.0)) * 100))


def f_weather(snap, ctx) -> str:
    """天气事实行。

    只有"雨刷开着"这一个信号时**不能断言在下雨**：欧卡的 wipers 是雨刷开关
    状态，很多人习惯把开关留在开位（实测主人反馈"没下雨就说下雨"）。
    只有拿到画面/OCR 证据（rain_confirmed）才敢确认，否则据实描述并让她问一句。
    """
    bits = []
    if ctx.get("rain_confirmed"):
        bits.append("外面在下雨（画面里能看到雨）")
    else:
        bits.append("雨刷开关是开着的（只能说明开关开着，不一定真在下雨，"
                    "也可能只是之前开过没关）")
    bits.append(f"车速 {snap.speed_kmh:.0f} km/h")
    if snap.is_city:
        bits.append("在市区")
    return "；".join(bits)


def f_night(snap, ctx) -> str:
    bits = [f"现在是深夜 {_clock(ctx)}", f"主人还在开（车速 {snap.speed_kmh:.0f} km/h）"]
    if snap.rest_stop_min:
        bits.append(f"距离强制休息还有 {snap.rest_stop_min:.0f} 分钟")
    return "；".join(bits)


def f_dawn_dusk(snap, ctx) -> str:
    when = "清晨" if 5 <= _hour(ctx) < 8 else "傍晚"
    return f"现在是{when} {_clock(ctx)}，天光正好，车速 {snap.speed_kmh:.0f} km/h"


def f_road(snap, ctx) -> str:
    bits = [f"车速 {snap.speed_kmh:.0f} km/h"]
    if snap.speed_limit_kmh:
        bits.append(f"限速 {snap.speed_limit_kmh:.0f}")
    pos = ctx.get("position") or ""
    if pos:
        bits.insert(0, pos)
    if ctx.get("curve_deg"):
        bits.append(f"前方约 350 米有急弯（约 {ctx['curve_deg']:.0f}°）")
    if ctx.get("facility"):
        bits.append(str(ctx["facility"]))
    return "；".join(bits)


def f_city(snap, ctx) -> str:
    name = ctx.get("place_name") or ""
    bits = [f"正在穿{name + ' ' if name else ''}市区"]
    if snap.speed_kmh < 5:
        bits.append("车停着（等灯/靠边）")
    else:
        bits.append(f"车速 {snap.speed_kmh:.0f} km/h")
    return "；".join(bits)


def f_cargo(snap, ctx) -> str:
    bits = [f"这单拉的是 {snap.cargo or '普通货'}",
            f"{snap.city_src or '起点'} → {snap.city_dst or '终点'}",
            f"全程约 {snap.planned_distance_km:.0f} km"]
    bits.append(f"货物完好率 {_cargo_pct(snap)}%")
    if snap.cargo_mass:
        bits.append(f"货重约 {snap.cargo_mass:.1f} t")
    if not snap.is_cargo_loaded:
        bits.append("目前是空载")
    if snap.planned_distance_km:
        bits.append(f"已走 {float(snap.trip_progress_percent or 0):.0f}%")
    return "；".join(bits)


def f_progress(snap, ctx) -> str:
    bits = [f"这单还剩 {snap.route_remaining_km:.0f} km"]
    if snap.route_remaining_time_min:
        bits.append(f"按当前路线还要约 {snap.route_remaining_time_min:.0f} 分钟")
    if snap.delivery_remaining_min:
        bits.append(f"交付时间还剩 {snap.delivery_remaining_min:.0f} 分钟")
    if ctx.get("drive_min"):
        bits.append(f"已经连续开了 {ctx['drive_min']:.0f} 分钟")
    return "；".join(bits)


def f_truck(snap, ctx) -> str:
    bits = []
    dmg = float(snap.max_damage or 0.0) * 100
    if dmg >= 5:
        parts = "、".join(f"{k} {v * 100:.0f}%" for k, v in (snap.damage_parts or {}).items()
                          if v > 0.01)
        bits.append(f"车损累计 {dmg:.0f}%" + (f"（{parts}）" if parts else ""))
    if snap.fuel_percent:
        bits.append(f"油量 {snap.fuel_percent:.0f}%")
    if snap.fuel_range_km:
        bits.append(f"续航 {snap.fuel_range_km:.0f} km")
    # 以前采集了却没人读的机务数值，这里让它们进入闲聊
    if (snap.adblue_capacity or 0) > 0:
        ad_pct = (snap.adblue or 0) / snap.adblue_capacity * 100.0
        if ad_pct < 40:
            bits.append(f"尿素 {ad_pct:.0f}%")
    if snap.water_temperature and snap.water_temperature > 85:
        bits.append(f"水温 {snap.water_temperature:.0f}°C")
    if snap.brake_temperature and snap.brake_temperature > 180:
        bits.append(f"刹车温度 {snap.brake_temperature:.0f}°C")
    if snap.battery_voltage and snap.battery_voltage < 24.5:
        bits.append(f"电压 {snap.battery_voltage:.1f}V")
    # 挂车编组（ETS2 真实的多节挂车玩法）
    label, tip = trailer_config(int(getattr(snap, "trailer_count", 0) or 0))
    if label and label != "单节挂车":
        bits.append(f"{label}（要点：{tip}）")
    elif snap.trailer_attached and snap.trailer_license:
        bits.append(f"挂着挂车 {snap.trailer_license}")
    elif snap.truck_license:
        bits.append(f"车牌 {snap.truck_license}")
    return "；".join(bits)


def f_care(snap, ctx) -> str:
    bits = []
    if ctx.get("drive_min"):
        bits.append(f"主人已经连续开了 {ctx['drive_min']:.0f} 分钟")
    if snap.rest_stop_min:
        bits.append(f"距离强制休息还有 {snap.rest_stop_min:.0f} 分钟")
    if _hour(ctx) >= 22 or _hour(ctx) < 5:
        bits.append("现在是深夜")
    return "；".join(bits) or "长途驾驶中"


def f_cruise(snap, ctx) -> str:
    return (f"定速巡航开着（设定 {snap.cruise_speed_mps * 3.6:.0f} km/h），"
            f"实际车速 {snap.speed_kmh:.0f} km/h")


def f_ask(snap, ctx) -> str:
    bits = [f"现在车速 {snap.speed_kmh:.0f} km/h"]
    if snap.on_job and snap.city_dst:
        bits.append(f"正往 {snap.city_dst} 去")
    elif snap.is_open_road:
        bits.append("在郊外/高速上跑着")
    if ctx.get("odometer_km"):
        bits.append(f"总里程 {ctx['odometer_km']:.0f} km")
    return "；".join(bits)


def f_life(snap, ctx) -> str:
    bits = []
    if _clock(ctx):
        bits.append(f"现在是 {_clock(ctx)}")
    bits.append("车在跑" if snap.speed_kmh > 5 else "车停着（休息/等人）")
    if snap.on_job:
        bits.append(f"手里有活（{snap.cargo or '货'}）")
    else:
        bits.append("没有接单，自由跑")
    return "；".join(bits)


def f_memory(snap, ctx) -> str:
    bits = [f"主人累计跑了 {ctx.get('odometer_km', 0):.0f} km"]
    if ctx.get("trips"):
        bits.append(f"完成 {ctx['trips']} 单")
    if ctx.get("top_city"):
        bits.append(f"跑得最多的城市是 {ctx['top_city']}")
    today = ctx.get("diary_today") or {}
    if today.get("trips"):
        bits.append(f"今天已经跑了 {today['km']:.0f} km、{today['trips']} 单"
                    f"（收入 {today['revenue']:.0f} €）")
    last = ctx.get("diary_last") or {}
    if last.get("dst"):
        bits.append(f"上一趟是 {last.get('src') or '起点'} → {last['dst']}"
                    f"（{last.get('cargo') or '货'}，{float(last.get('km', 0)):.0f} km）")
    if ctx.get("today_km"):
        bits.append(f"本想提一下今天 {ctx['today_km']:.0f} km")
    return "；".join(bits)


def f_photo(snap, ctx) -> str:
    return (f"开阔路段 + 天光好（{_clock(ctx)}，{ctx.get('road_label') or '郊外'}），"
            f"车速 {snap.speed_kmh:.0f} km/h，风景不错")

def f_scenery(snap, ctx) -> str:
    bits = [f"车速 {snap.speed_kmh:.0f} km/h"]
    if ctx.get("clock"):
        bits.append(f"现在是 {ctx['clock']}")
    if ctx.get("position"):
        bits.append(str(ctx["position"]))
    if ctx.get("road_label"):
        bits.append(f"路段：{ctx['road_label']}")
    if snap.is_city:
        bits.append("在市区，两边是街道和楼")
    elif snap.is_open_road:
        bits.append("在郊外/高速，视野开阔")
    if snap.wipers and ctx.get("rain_confirmed"):
        bits.append("还下着雨")
    if ctx.get("place_name"):
        bits.append(f"附近是 {ctx['place_name']}")
    return "；".join(bits)


def f_remark(snap, ctx) -> str:
    """提起主人之前说过的话（陪玩的核心：说过的要被记住）。"""
    bits = [f"主人之前说过：「{ctx.get('remark', '')}」"]
    if ctx.get("clock"):
        bits.append(f"现在是 {ctx['clock']}")
    if snap.on_job and snap.city_dst:
        bits.append(f"正往 {snap.city_dst} 去")
    return "；".join(bits)


def f_job_play(snap, ctx) -> str:
    """这趟是什么玩法、该怎么开（全部来自真实遥测 + 游戏机制）。"""
    bits = []
    if ctx.get("play_label"):
        bits.append(f"这趟的玩法是「{ctx['play_label']}」")
    fam = ctx.get("cargo_family") or ""
    if fam:
        bits.append(f"货物属于{fam}")
    if snap.cargo_mass:
        bits.append(f"货重约 {snap.cargo_mass / 1000:.1f} t")
    if snap.on_job and snap.city_dst:
        bits.append(f"{snap.city_src or '起点'} → {snap.city_dst}")
    if ctx.get("play_advice"):
        bits.append(f"对应驾驶要点：{ctx['play_advice']}")
    return "；".join(bits)


def f_milestone(snap, ctx) -> str:
    """里程里程碑（原 SmallTalk 的唯一在用功能，2026-10 并入陪玩维度表）。"""
    bits = [f"总里程刚越过 {ctx.get('milestone_km', 0):.0f} km 这个整数关口"]
    if ctx.get("odometer_km"):
        bits.append(f"当前总里程 {ctx['odometer_km']:.0f} km")
    if ctx.get("trips"):
        bits.append(f"累计完成 {ctx['trips']} 单")
    if snap.on_job and snap.city_dst:
        bits.append(f"正在往 {snap.city_dst} 跑")
    return "；".join(bits)


def f_banter(snap, ctx) -> str:
    bits = [f"车速 {snap.speed_kmh:.0f} km/h"]
    if snap.speed_limit_kmh:
        bits.append(f"限速 {snap.speed_limit_kmh:.0f}")
    if snap.engine_rpm:
        bits.append(f"转速 {snap.engine_rpm:.0f}")
    if snap.gear_dashboard:
        bits.append(f"档位 {snap.gear_dashboard}")
    if ctx.get("drive_min"):
        bits.append(f"已经开了 {ctx['drive_min']:.0f} 分钟")
    if float(snap.max_damage or 0) >= 0.03:
        bits.append(f"车身有 {snap.max_damage * 100:.0f}% 的损伤")
    if snap.on_job and snap.city_dst:
        bits.append(f"正往 {snap.city_dst} 去")
    # 驾驶画像：让她真的"认识你"（标签来自真实统计，不是编的）
    if ctx.get("profile_label"):
        bits.append(f"他长期以来的驾驶画像：{ctx['profile_label']}")
    return "；".join(bits)


# ── 维度表 ───────────────────────────────────────────────────────
# 主题题库：与驾驶无关的生活话题，让"陪玩"不只围着车转
# （2026-10 主人反馈"话题闲聊太少"，这里一次给足；角度轮换不会连着说同一件）
_TOPIC_BANK = (
    "服务区/加油站能吃到什么，你最想吃哪样",
    "跑长途时车里放什么歌，有没有一放就犯困的",
    "最想去哪个国家跑一趟运输，为什么",
    "欧洲哪条路最美，想再走一次",
    "深夜开车和白天开车的感受有什么不一样",
    "你自己在车上平时做什么（打盹/看路/数羊）",
    "路上遇到过的卡车司机，有没有印象深的",
    "服务区遇到的旅人，猜猜他们是干什么的",
    "第一次开大车是什么感觉",
    "跑过最远的一趟是去哪儿",
    "雨后的空气和湿漉漉的路面",
    "今天的光线怎么样（日出/日落/星空）",
    "这辆车的脾气：起步、刹车、爬坡哪样最难伺候",
    "最喜欢的卡车品牌和理由",
    "驾驶室里的摆件和贴纸有什么故事",
    "问主人今天过得怎么样、累不累",
    "问主人最喜欢的季节和天气",
    "问主人小时候想做什么",
    "吐槽长途的枯燥，但承认还是喜欢在路上",
    "说说你自己今天的小心情",
    "碎碎念：今天踩回来多少泥点（可自嘲）",
    "服务区的猫，或者路上遇到的动物",
    "车里常备的零食和饮料",
    "如果今晚不用开车，想干什么",
    "给这趟路起个名字",
    "聊聊路牌上的地名好不好听",
    "猜猜前面那辆车上装的是什么货",
    "各国车牌/收费站/限速牌的趣事",
    "如果猫也会开车，会开成什么样",
    "说说电台里那些你听懂/听不懂的语言",
)

# ── 维度表 ───────────────────────────────────────────────────────
DIMENSIONS: Tuple[Dimension, ...] = (
    Dimension(
        "weather", "雨刷/天气", "safety", 900, 8,
        ("如果真在下雨就提醒路滑、刹车距离变长（没下雨就别说下雨）",
         "问一句是不是下雨了，或者提醒雨刷忘了关",
         "关心一下视线和雨刷", "说说雨天的路面和听歌的感觉"),
        lambda s, c: bool(s.wipers) and s.speed_kmh > 5,
        f_weather,
    ),
    Dimension(
        "night", "夜路", "safety", 1200, 7,
        ("陪着守夜，提醒别犯困", "聊深夜跑车的安静感",
         "提一句车灯/视野", "让他困了就叫你"),
        lambda s, c: (_hour(c) >= 22 or 0 <= _hour(c) < 5) and s.speed_kmh > 5,
        f_night,
    ),
    Dimension(
        "truck", "车况", "safety", 900, 7,
        ("提醒该修车/该加油了", "念叨一下别把车开坏",
         "关心刹车和水温", "算笔修车账"),
        lambda s, c: (float(s.max_damage or 0) >= 0.05 or (s.fuel_percent and s.fuel_percent < 45)
                      or (s.water_temperature or 0) > 90 or (s.brake_temperature or 0) > 200),
        f_truck,
    ),
    Dimension(
        "care", "关心", "safety", 900, 6,
        ("劝他找个地方歇一歇", "关心他累不累、渴不渴",
         "提醒疲劳驾驶的危险", "提议放首歌提提神"),
        lambda s, c: (float(c.get("drive_min") or 0) > 45
                      or (s.rest_stop_min and 0 < s.rest_stop_min < 180)) and s.speed_kmh > 5,
        f_care,
    ),
    Dimension(
        "road", "路况", "trip", 600, 6,
        ("聊沿途的风景", "说说这段路开起来什么感觉",
         "提一句前方路况", "夸他开得稳 / 吐槽路况"),
        lambda s, c: s.speed_kmh > 25,
        f_road,
    ),
    Dimension(
        "city", "进城", "trip", 900, 5,
        ("说一句这座城市给人的印象", "提醒市区注意行人和红灯",
         "聊城里送货的琐碎", "提一句附近有什么吃的"),
        lambda s, c: bool(s.is_city) and s.speed_kmh > 5,
        f_city,
    ),
    Dimension(
        "ask", "反问", "chatter", 720, 5,
        ("问他这一趟想跑哪条线、想不想抄近路", "问他饿不饿、要不要找个服务区吃东西",
         "问他今天累不累、心情怎么样", "问他喜欢白天跑还是夜里跑",
         "问他最想跑哪个国家的线路"),
        lambda s, c: s.speed_kmh > 15,
        f_ask,
    ),
    Dimension(
        "cargo", "这单货", "task", 1200, 4,
        ("聊这趟拉的是什么、值不值钱", "念叨货别磕碰，完好率是要扣钱的",
         "说说目的地的行情", "算算这单能赚多少"),
        lambda s, c: bool(s.on_job) and bool(s.cargo),
        f_cargo,
    ),
    Dimension(
        "progress", "行程", "task", 900, 4,
        ("报一下还剩多少、什么时候到", "鼓励他最后一段稳住",
         "提醒交付时间", "说说到了之后干什么"),
        lambda s, c: bool(s.on_job) and float(s.route_remaining_km or 0) > 5,
        f_progress,
    ),
    Dimension(
        "dawn_dusk", "晨昏", "trip", 1500, 4,
        ("聊清晨/傍晚的光线和心情", "说说这个点出发的舒服",
         "提一句日出日落、今天打算跑多远"),
        lambda s, c: (5 <= _hour(c) < 8 or 17 <= _hour(c) < 21) and s.speed_kmh > 5,
        f_dawn_dusk,
    ),
    Dimension(
        "life", "生活闲聊", "chatter", 200, 3,
        ("聊吃的（面包、咖啡、服务区的热狗）", "聊音乐、电台、你爱听的歌",
         "聊猫、聊聊你自己在车上的小习惯", "聊天气之外的生活琐事",
         "讲讲你听来的公路传说或见闻", "吐槽长途司机的苦与乐",
         "说说你今天的小心情", "碎碎念两句（可自嘲）",
         "聊车里的零食和饮料", "如果今晚不用开车，想干什么"),
        lambda s, c: True,
        f_life,
    ),
    Dimension(
        "topics", "主题闲聊", "chatter", 240, 3,
        _TOPIC_BANK,
        lambda s, c: True,
        f_life,
    ),
    Dimension(
        "scenery", "路边风景", "trip", 240, 3,
        ("夸一夸眼前的风景", "说说路边看到的车或建筑",
         "聊聊天空和云的样子", "隧道/桥/山口这类路段开起来的感觉",
         "猜猜路边那片田或林子里有什么", "念一句路牌上你觉得好听的地名",
         "感叹一下视野突然开阔", "留意路上有没有动物或行人"),
        lambda s, c: s.speed_kmh > 20,
        f_scenery,
    ),
    Dimension(
        "banter", "打趣", "chatter", 300, 3,
        ("打趣他开车的小习惯", "夸他开得稳（或损他刚才那脚刹车，别真批评）",
         "调侃他选的这条路/这一单", "假装嫌弃其实是在关心",
         "拿他的车速开玩笑（点到为止）", "用一句话给他打气",
         "拿他的驾驶画像开涮（爱超速/夜猫子之类，点到为止，别真的训人）",
         "跟他打个赌，赌谁先犯困"),
        lambda s, c: s.speed_kmh > 10,
        f_banter,
    ),
    Dimension(
        "job_play", "这趟怎么开", "task", 900, 6,
        ("按这个玩法提醒一句最要紧的技术要点（用驾驶建议那条）",
         "说说这趟和普通单子比难在哪",
         "顺带夸一句他这趟开得如何（结合玩法难度）",
         "提一句这种货最容易在哪掉链子"),
        lambda s, c: bool(c.get("play_label")) and bool(c.get("play_advice")),
        f_job_play,
    ),
    Dimension(
        "milestone", "里程碑", "chatter", 600, 6,
        ("为这个关口高兴一下，顺带说说跑了多久",
         "纪念一下这个数字，提一句你们一起跑过的路",
         "恭喜他，并定一个小目标"),
        lambda s, c: bool(c.get("milestone_km")),
        f_milestone,
    ),
    Dimension(
        "remark", "记得你说过", "chatter", 1200, 5,
        ("自然地把这句话接回来，像一直记着的样子",
         "就这句话回一句关心或调侃（别生硬复述）",
         "顺着这句话聊下去，可以顺带问问近况",
         "把这句话和眼前的路上情况联系起来说一句"),
        lambda s, c: bool(c.get("remark")),
        f_remark,
    ),
    Dimension(
        "cruise", "巡航", "trip", 900, 3,
        ("说定速巡航的舒服", "提醒巡航也要留神、右脚别离刹车太远",
         "聊长途巡航怎么防困"),
        lambda s, c: bool(s.cruise_control) and s.speed_kmh > 30,
        f_cruise,
    ),
    Dimension(
        "memory", "回忆", "chatter", 1800, 3,
        ("翻旧账：提一提以前跑过的城市和单子", "夸他总里程越来越长",
         "回忆一次惊险的瞬间（如果有）"),
        lambda s, c: (c.get("odometer_km") or 0) > 50,
        f_memory,
    ),
    Dimension(
        "photo", "风景", "trip", 2400, 2,
        ("问主人要不要拍一张留着（提醒可以直接说「拍吧」，面板上也有按钮）",
         "说说眼前这个画面值得记下来，问要不要拍",
         "提议拍一张，顺便说说这个光线/景色好在哪"),
        lambda s, c: (bool(c.get("open_road")) and s.speed_kmh > 40
                      and 7 <= _hour(c) <= 19),
        f_photo,
    ),
)

_REQUIREMENT = ""  # 要求行由 EmotionRenderer.compose_fact 统一追加，别两段指令叠一起


class Companion:
    """陪玩维度调度：挑一个到期维度，产出一条事实行。"""

    def __init__(self, cfg: Any, map_kb: Any = None) -> None:
        self.cfg = cfg
        self.map_kb = map_kb
        self._last: Dict[str, float] = {}
        self._last_any = 0.0
        self._last_dim = ""
        self._angle: Dict[str, int] = {}
        self._rng = random.Random()
        # 最近说过的角度（跨维度共享）：避免"同一个说法"隔几次又冒出来
        self._recent_angles: Deque[str] = deque(maxlen=RECENT_ANGLE_MEMORY)

    # ── 调参 ──
    @property
    def gap_s(self) -> float:
        """全局最小间隔（秒）：面板「播报频率」再乘倍率。

        2026-10 主人反馈"话题闲聊太少" → 基准从 150s 降到 90s。
        配合面板倍率：安静 144s / 标准 90s / 活跃 58s 一条主动陪聊。
        """
        return max(30.0, float(getattr(self.cfg, "companion_gap_s", 90.0) or 90.0))

    # ── 上下文 ──
    def build_context(self, snap, drive_min: float = 0.0, place: Any = None,
                      odometer_km: float = 0.0, trips: int = 0,
                      top_city: str = "", today_km: float = 0.0,
                      rain_confirmed: bool = False,
                      remark: str = "", mood: str = "",
                      memory_hint: str = "", diary_today: Any = None,
                      diary_last: Any = None, driving_by: str = "",
                      profile_label: str = "", mood_tone: str = "") -> Dict[str, Any]:
        """把快照 + 地图知识 + 情绪/记忆整理成维度可用的上下文（不含活对象）。"""
        ctx: Dict[str, Any] = {
            "clock": "",
            "hour": -1,
            "drive_min": float(drive_min or 0.0),
            "odometer_km": float(odometer_km or 0.0),
            "trips": int(trips or 0),
            "top_city": str(top_city or ""),
            "today_km": float(today_km or 0.0),
            "place_name": "",
            "position": "",
            "curve_deg": 0.0,
            "facility": "",
            "road_label": "",
            "open_road": bool(getattr(snap, "is_open_road", False)),
            "rain_confirmed": bool(rain_confirmed),
            "remark": str(remark or ""),
            "mood": str(mood or ""),
            "memory_hint": str(memory_hint or ""),
            "milestone_km": 0.0,
            "diary_today": dict(diary_today or {}),
            "diary_last": dict(diary_last or {}),
            "driving_by": str(driving_by or ""),
            "play_label": "",
            "play_advice": "",
            "cargo_family": "",
            "profile_label": str(profile_label or ""),
            "mood_tone": str(mood_tone or ""),
        }
        if getattr(snap, "time_abs_min", 0):
            h = (snap.time_abs_min // 60) % 24
            ctx["hour"] = h
            ctx["clock"] = f"{h:02d}:{(snap.time_abs_min % 60):02d}"
        if self.map_kb is not None:
            try:
                ctx["road_label"] = self.map_kb.road_level(snap.speed_limit_kmh)
            except Exception:
                ctx["road_label"] = ""
            if snap.world_x or snap.world_z:
                try:
                    nr = self.map_kb.nearest_road(snap.world_x, snap.world_z, max_km=1.0)
                except Exception:
                    nr = None
                if nr and nr.get("distance_km", 9) <= 0.5:
                    ctx["position"] = f"正压在{ctx['road_label'] or '主路'}上"
                try:
                    curve = self.map_kb.curve_ahead(snap.world_x, snap.world_z)
                except Exception:
                    curve = None
                if curve:
                    ctx["curve_deg"] = float(curve) * 57.3
                # 最近服务设施（3 km 内才提；油量低优先报加油站）
                want = "fuel" if float(getattr(snap, "fuel_percent", 0) or 0) < 45 else "service"
                try:
                    fac = self.map_kb.nearest_facility(
                        snap.world_x, snap.world_z, want, max_km=3.0)
                except Exception:
                    fac = None
                if fac:
                    kind = "加油站" if want == "fuel" else "服务区"
                    ctx["facility"] = f"前方 {fac.get('distance_km', 0):.1f} km 有{kind}"
        if place:
            ctx["place_name"] = str(place.get("name", "") or "")
        return ctx

    # ── 调度 ──
    def next_fact(self, snap, ctx: Optional[dict] = None, now: Optional[float] = None,
                  categories: Optional[dict] = None, gap_multiplier: float = 1.0,
                  bias: str = "", bias_gain: int = 4,
                  ) -> Optional[Dict[str, str]]:
        """返回 {id,label,category,fact}；没有到期的维度返回 None。

        `bias` 是"刚发生的事"带来的话题倾向（事故 → 关心、货损 → 这单货、
        罚单 → 安全/打趣…）：给她一个加权的维度，避免刚撞完就聊"服务区吃什么"。
        """
        if snap is None or not getattr(self.cfg, "companion_enabled", True):
            return None
        ctx = ctx or {}
        now = now or time.time()
        if now - self._last_any < self.gap_s * max(0.2, float(gap_multiplier or 1.0)):
            return None
        cats = categories if categories is not None else {}

        best: Optional[Dimension] = None
        best_key = None
        for dim in DIMENSIONS:
            if cats.get(dim.category, True) is False:
                continue
            if now - self._last.get(dim.id, -1e9) < dim.cooldown:
                continue
            try:
                if not dim.when(snap, ctx):
                    continue
            except Exception:
                continue
            key = (dim.priority, -self._last.get(dim.id, -1e9))
            # 别连着两次同一个维度：优先级相近时优先换人
            if dim.id == self._last_dim:
                key = (key[0] - 1, key[1])
            if bias and dim.id == bias:
                key = (key[0] + max(1, int(bias_gain)), key[1])
            if best_key is None or key > best_key:
                best, best_key = dim, key
        if best is None:
            return None

        try:
            facts = best.facts(snap, ctx)
        except Exception:
            return None
        if not facts:
            return None

        idx = self._angle.get(best.id, 0)
        angle = self._pick_angle(best, idx)
        self._angle[best.id] = idx + 1
        self._last[best.id] = now
        self._last_any = now
        self._last_dim = best.id
        fact = (f"[陪玩·{best.label}] {facts}。\n"
                f"（这次想聊的角度：{angle}）{_REQUIREMENT}")
        hint = []
        if ctx.get("mood_tone"):
            hint.append(ctx["mood_tone"])          # 情绪主线：所有维度共用的语气底
        if ctx.get("mood"):
            hint.append(f"当前情绪：{ctx['mood']}")
        if ctx.get("memory_hint"):
            hint.append(f"可以顺带提一句：{ctx['memory_hint']}")
        if ctx.get("driving_by") == "catgirl":
            # 智驾打通：她握着方向盘的时候，口吻要像司机而不是副驾
            hint.append("现在方向盘在你（猫娘）手里，说话要像开车的人，别说成是你在旁边看着")
        if bias:
            hint.append("延续刚刚发生的事，别突然换到无关话题")
        return {"id": best.id, "label": best.label, "category": best.category,
                "fact": fact, "hint": "；".join(hint)}

    def _pick_angle(self, dim: "Dimension", idx: int) -> str:
        """挑一个"最近没说过"的角度。

        旧版是纯递增下标（4 个角度 → 每 4 次就原样重复一遍），
        聊久了玩家就会听到同一句话。现在跨维度共享一个最近窗口，
        挑角度时跳过窗口内的说法，实在绕不开才复用。
        """
        n = len(dim.angles)
        if n == 0:
            return "随口聊一句"
        for k in range(n):
            cand = dim.angle(idx + k)
            if cand not in self._recent_angles:
                self._recent_angles.append(cand)
                return cand
        cand = dim.angle(idx)               # 全都刚说过 → 只能复用
        self._recent_angles.append(cand)
        return cand

    def snapshot(self) -> Dict[str, Any]:
        return {
            "dimensions": len(DIMENSIONS),
            "gap_s": self.gap_s,
            "last": {k: round(v, 1) for k, v in self._last.items()},
        }
