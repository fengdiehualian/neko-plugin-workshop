"""
Tarot Reader Plugin

塔罗牌占卜插件 - 集成自 chatgpt-tarot-divination 项目
提供塔罗牌、生辰八字、姓名五格、周公解梦、起名取名、梅花易数、姻缘占卜、星座运势、抽签占卜等服务
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from . import dream_dict as _dream_dict
from . import name_data as _name_data
from . import tarot_minor_data as _tarot_minor
from . import yijing_data as _yijing

# 源项目的 TAROT_PROMPT
TAROT_PROMPT = """我请求你担任塔罗占卜师的角色。您将接受我的问题并使用虚拟塔罗牌进行塔罗牌阅读。不要忘记洗牌并介绍您在本套牌中使用的套牌。请帮我抽3张随机卡。拿到卡片后，请您仔细说明它们的意义，解释哪张卡片属于未来或现在或过去，结合我的问题来解释它们，并给我有用的建议或我现在应该做的事情。"""

# 22 张大阿卡纳塔罗牌
_TAROT_CARDS = [
    {"id": 0, "name": "愚者", "name_en": "The Fool", "number": "0", "arcana": "major",
     "upright": ["新的开始", "冒险", "天真", "自发", "潜力", "自由"], "reversed": ["鲁莽", "冒险", "犹豫不决", "缺乏方向", "轻率"],
     "description": "代表新的开始、无限可能性和冒险精神。愚者提醒我们要相信直觉，勇敢踏出第一步，虽然看似天真，但蕴含着巨大的潜力。",
     "element": "风", "zodiac": "天王星"},
    {"id": 1, "name": "魔术师", "name_en": "The Magician", "number": "I", "arcana": "major",
     "upright": ["创造力", "技能", "意志力", "专注", "显化", "资源"], "reversed": ["欺骗", "操控", "缺乏自信", "浪费天赋", "负面意图"],
     "description": "象征创造力、技能和意志力。你拥有实现目标所需的一切资源。魔术师连接天地，表明你有能力将想法变为现实。",
     "element": "风", "zodiac": "水星"},
    {"id": 2, "name": "女祭司", "name_en": "The High Priestess", "number": "II", "arcana": "major",
     "upright": ["直觉", "潜意识", "神秘", "内在智慧", "神圣知识", "耐心"], "reversed": ["隐藏动机", "冷漠", "直觉受阻", "秘密暴露", "沟通中断"],
     "description": "代表深层的直觉和潜意识智慧。倾听内心的声音，答案就在你心中。女祭司守护着神圣的知识，提醒我们信任内在的智慧。",
     "element": "水", "zodiac": "月亮"},
    {"id": 3, "name": "女皇", "name_en": "The Empress", "number": "III", "arcana": "major",
     "upright": ["丰饶", "母性", "自然", "感官享受", "创造力", "美丽"], "reversed": ["依赖", "过度保护", "空虚", "创造性停滞", "忽视需求"],
     "description": "象征丰饶、母性和自然的力量。享受生活中的美好，滋养自己和他人。女皇代表生育力、创造力和大自然的慷慨馈赠。",
     "element": "土", "zodiac": "金星"},
    {"id": 4, "name": "皇帝", "name_en": "The Emperor", "number": "IV", "arcana": "major",
     "upright": ["权威", "稳定", "领导力", "结构", "控制", "保护"], "reversed": ["专制", "僵化", "缺乏纪律", "混乱", "暴政"],
     "description": "代表权威、稳定和领导力。建立秩序和结构，展现你的管理能力。皇帝象征父性原则和对秩序的需要。",
     "element": "火", "zodiac": "白羊座"},
    {"id": 5, "name": "教皇", "name_en": "The Hierophant", "number": "V", "arcana": "major",
     "upright": ["传统", "信仰", "指导", "教育", "精神指导", "道德"], "reversed": ["反叛", "打破常规", "新信仰", "非传统", "个人信念"],
     "description": "象征传统、信仰和精神指导。寻求智者的建议，遵循内心的信念。教皇代表传统的智慧和精神价值观。",
     "element": "土", "zodiac": "金牛座"},
    {"id": 6, "name": "恋人", "name_en": "The Lovers", "number": "VI", "arcana": "major",
     "upright": ["爱情", "和谐", "选择", "关系", "价值观整合", "吸引力"], "reversed": ["不和谐", "失衡", "错误选择", "冲突", "分离"],
     "description": "代表爱情、和谐和重要的人生选择。跟随你的心，做出正确的决定。这张牌更多关乎价值观的选择而非单纯的爱情。",
     "element": "风", "zodiac": "双子座"},
    {"id": 7, "name": "战车", "name_en": "The Chariot", "number": "VII", "arcana": "major",
     "upright": ["胜利", "决心", "控制", "意志力", "前进", "征服"], "reversed": ["失控", "侵略", "缺乏方向", "冲突", "挫败"],
     "description": "象征胜利、决心和自我控制。坚定目标，你将克服一切障碍。战车代表意志的力量和向前推进的决心。",
     "element": "水", "zodiac": "巨蟹座"},
    {"id": 8, "name": "力量", "name_en": "Strength", "number": "VIII", "arcana": "major",
     "upright": ["勇气", "耐心", "内在力量", "温柔", "自制", "同情"], "reversed": ["自我怀疑", "软弱", "不安全感", "暴力", "压迫"],
     "description": "代表内在的力量和勇气。真正的力量来自内心的平静与温柔。力量女神驯服猛兽，象征柔能克刚。",
     "element": "火", "zodiac": "狮子座"},
    {"id": 9, "name": "隐者", "name_en": "The Hermit", "number": "IX", "arcana": "major",
     "upright": ["内省", "寻求真理", "孤独", "智慧", "指导", "反思"], "reversed": ["孤立", "退缩", "迷失", "缺乏指导", "逃避"],
     "description": "象征内省和寻求真理。独处时你能找到内心真正的答案。隐者提灯照亮道路，代表智慧的引导。",
     "element": "土", "zodiac": "处女座"},
    {"id": 10, "name": "命运之轮", "name_en": "Wheel of Fortune", "number": "X", "arcana": "major",
     "upright": ["命运", "转折点", "好运", "循环", "机会", "变化"], "reversed": ["厄运", "抵抗改变", "停滞", "坏运气", "时机不对"],
     "description": "代表命运的转折点和生命的循环。接受变化，好运即将来临。命运之轮象征宇宙的自然循环和因果法则。",
     "element": "火", "zodiac": "木星"},
    {"id": 11, "name": "正义", "name_en": "Justice", "number": "XI", "arcana": "major",
     "upright": ["公正", "真相", "因果", "法律", "诚实", "平衡"], "reversed": ["不公", "欺骗", "逃避责任", "偏见", "失衡"],
     "description": "象征公正、真相和因果律。诚实面对自己和他人，正义终将到来。正义天平衡量因果，提醒我们承担后果。",
     "element": "风", "zodiac": "天秤座"},
    {"id": 12, "name": "倒吊人", "name_en": "The Hanged Man", "number": "XII", "arcana": "major",
     "upright": ["牺牲", "新视角", "等待", "放下", "灵性", "暂停"], "reversed": ["拖延", "无谓牺牲", "固执", "抗拒", "缺乏远见"],
     "description": "代表牺牲和新的视角。有时候需要换个角度看问题。倒吊人象征自愿的牺牲和通过暂停获得洞察。",
     "element": "水", "zodiac": "海王星"},
    {"id": 13, "name": "死神", "name_en": "Death", "number": "XIII", "arcana": "major",
     "upright": ["结束", "转变", "重生", "放下", "过渡", "转化"], "reversed": ["抗拒改变", "恐惧", "停滞", "延迟", "恢复"],
     "description": "象征结束和转变。旧事物的消逝为新生命腾出空间。死神代表必要的变革和生命阶段的转换。",
     "element": "水", "zodiac": "天蝎座"},
    {"id": 14, "name": "节制", "name_en": "Temperance", "number": "XIV", "arcana": "major",
     "upright": ["平衡", "耐心", "和谐", "中庸", "融合", "调和"], "reversed": ["失衡", "过度", "不协调", "极端", "冲突"],
     "description": "代表平衡、耐心和和谐。在两极之间找到中庸之道。节制天使调和水火，象征融合与和谐。",
     "element": "火", "zodiac": "射手座"},
    {"id": 15, "name": "恶魔", "name_en": "The Devil", "number": "XV", "arcana": "major",
     "upright": ["束缚", "诱惑", "执着", "阴影", "物质主义", "沉溺"], "reversed": ["解脱", "突破", "重获自由", "觉醒", "释放"],
     "description": "象征束缚和诱惑。认识到限制你的枷锁，你才能重获自由。恶魔提醒我们摆脱有害的依恋。",
     "element": "土", "zodiac": "摩羯座"},
    {"id": 16, "name": "塔", "name_en": "The Tower", "number": "XVI", "arcana": "major",
     "upright": ["突变", "启示", "觉醒", "崩解", "突然变化", "摧毁"], "reversed": ["避免灾难", "恐惧改变", "延迟", "抵抗", "渐进变化"],
     "description": "代表突变和启示。旧结构的崩塌是为了更好的重建。塔象征突然的觉醒和不可避免的变革。",
     "element": "火", "zodiac": "火星"},
    {"id": 17, "name": "星星", "name_en": "The Star", "number": "XVII", "arcana": "major",
     "upright": ["希望", "灵感", "宁静", "治愈", "指引", "愿景"], "reversed": ["绝望", "失去信心", "缺乏能量", "迷茫", "失望"],
     "description": "象征希望、灵感和宁静。黑暗之后必有光明，保持信心。星星洒下甘露，代表疗愈和精神滋养。",
     "element": "风", "zodiac": "水瓶座"},
    {"id": 18, "name": "月亮", "name_en": "The Moon", "number": "XVIII", "arcana": "major",
     "upright": ["幻象", "恐惧", "潜意识", "直觉", "梦境", "迷惑"], "reversed": ["释放恐惧", "真相浮现", "清晰", "直面恐惧", "觉醒"],
     "description": "代表幻象和潜意识。不要被表象迷惑，寻找隐藏的真相。月亮照亮潜意识，提醒我们面对恐惧。",
     "element": "水", "zodiac": "双鱼座"},
    {"id": 19, "name": "太阳", "name_en": "The Sun", "number": "XIX", "arcana": "major",
     "upright": ["快乐", "成功", "活力", "光明", "喜悦", "清晰"], "reversed": ["暂时的抑郁", "延迟的成功", "悲观", "缺乏活力", "挫折"],
     "description": "象征快乐、成功和光明。阳光灿烂的日子即将到来。太阳带来成功、快乐和精神的清晰。",
     "element": "火", "zodiac": "太阳"},
    {"id": 20, "name": "审判", "name_en": "Judgement", "number": "XX", "arcana": "major",
     "upright": ["觉醒", "重生", "召唤", "反思", "宽恕", "精神复苏"], "reversed": ["自我怀疑", "拒绝改变", "忽视召唤", "内疚", "评判"],
     "description": "代表觉醒和重生。听从内心的召唤，迎接新的生命阶段。审判象征精神的复苏和新的开始。",
     "element": "火", "zodiac": "冥王星"},
    {"id": 21, "name": "世界", "name_en": "The World", "number": "XXI", "arcana": "major",
     "upright": ["完成", "成就", "旅行", "圆满", "成功", "整合"], "reversed": ["未完成", "延迟", "缺乏成就", "不完整", "循环未闭"],
     "description": "象征完成和成就。一个循环的结束，你即将达到圆满。世界代表目标的达成和旅程的完成。",
     "element": "土", "zodiac": "土星"},
]

# 为大阿卡纳注入牌面图片（公版 RWS 扫描图），并合并 56 张小阿卡纳组成 78 张全卡组
for _c in _TAROT_CARDS:
    _c["image"] = f"rws/m{_c['id']:02d}.jpg"
_TAROT_CARDS.extend(_tarot_minor.MINOR_CARDS)

# ═══════════════════════════════════════════════════════════════
# 13 张黄金裔塔罗牌（翁法罗斯·逐火之旅）
# ═══════════════════════════════════════════════════════════════
_GOLDEN_CARDS = [
    {"id": 1, "name": "阿格莱雅", "name_en": "Aglaea", "number": "I", "arcana": "golden",
     "upright": ["美", "爱", "魅力", "感性", "创造", "审美"],
     "reversed": ["虚荣", "肤浅", "迷失自我", "情感依赖", "人性流失"],
     "description": "黄金之茧墨涅塔的继承者，掌管浪漫火种。她象征美与爱的力量，但也面临人性流失的考验——当承载过多神权，人的温度便会消退。",
     "element": "雷", "zodiac": "记忆",
     "image": "阿格莱雅_Aglaea.jpg", "titan": "墨涅塔", "fire_seed": "浪漫"},
    {"id": 2, "name": "缇宝", "name_en": "Tribbie", "number": "II", "arcana": "golden",
     "upright": ["通路", "连接", "选择", "指引", "多重身份", "传承"],
     "reversed": ["迷失方向", "分身离散", "感官过载", "孤立", "神力耗尽"],
     "description": "万径之门雅努斯的继承者，掌管门径火种。千年前祭司分裂自己创造分身，感官互通。当神力耗尽，分身便化为娃娃沉睡。",
     "element": "量子", "zodiac": "同谐",
     "image": "缇宝_Tribbie.jpg", "titan": "雅努斯", "fire_seed": "门径"},
    {"id": 3, "name": "万敌", "name_en": "Mydei", "number": "III", "arcana": "golden",
     "upright": ["勇气", "战斗", "守护", "不屈", "热血", "竞技"],
     "reversed": ["暴力", "鲁莽", "不死诅咒", "弱点暴露", "孤立无援"],
     "description": "天谴之矛尼卡多利的继承者，掌管纷争火种。受不死诅咒庇护，却有第十节胸椎的致命弱点。喜欢石榴汁和甜食的战士，曾被盗火行者击杀。",
     "element": "虚数", "zodiac": "毁灭",
     "image": "万敌_Mydei.jpg", "titan": "尼卡多利", "fire_seed": "纷争"},
    {"id": 4, "name": "遐蝶", "name_en": "Castorice", "number": "IV", "arcana": "golden",
     "upright": ["转变", "重生", "放下", "超越", "灵魂", "宁静"],
     "reversed": ["恐惧死亡", "抗拒改变", "死亡之触", "孤独", "远离人群"],
     "description": "灰黯之手塞纳托斯的继承者，掌管死亡火种。背负「死亡之触」诅咒的她习惯与他人保持距离，独自承载着终结与告别的重量，却仍愿意以温柔送别每一段旅途。",
     "element": "量子", "zodiac": "记忆",
     "image": "遐蝶_Castorice.png", "titan": "塞纳托斯", "fire_seed": "死亡"},
    {"id": 5, "name": "那刻夏", "name_en": "Anaxa", "number": "V", "arcana": "golden",
     "upright": ["智慧", "分析", "真理", "洞察", "独立思考", "逻辑"],
     "reversed": ["冷漠", "偏执", "自我封闭", "不近人情", "最不像智识"],
     "description": "裂分之枝瑟希斯的继承者，掌管理性火种。本名阿那克萨戈拉斯，不喜欢被叫那刻夏。他性情孤傲、不近人情，却总能在众人迷茫时给出最独到的洞察。",
     "element": "风", "zodiac": "智识",
     "image": "那刻夏_Anaxa.jpg", "titan": "瑟希斯", "fire_seed": "理性"},
    {"id": 6, "name": "风堇", "name_en": "Hyacine", "number": "VI", "arcana": "golden",
     "upright": ["希望", "治愈", "自由", "广阔", "守护", "新生"],
     "reversed": ["逃避现实", "好高骛远", "治疗疲劳", "过度保护"],
     "description": "晨昏之眼艾格勒的继承者，掌管天空火种。百眼巨鸟的化身，本名雅辛忒丝。喜欢在称呼后加「宝」字，以开朗的治愈之心守护着逐火之旅的每一位同行者。",
     "element": "风", "zodiac": "记忆",
     "image": "风堇_Hyacine.jpg", "titan": "艾格勒", "fire_seed": "天空"},
    {"id": 7, "name": "赛飞儿", "name_en": "Cipher", "number": "VII", "arcana": "golden",
     "upright": ["机智", "灵活", "真相", "口才", "化险为夷", "牺牲"],
     "reversed": ["谎言", "欺骗", "狡诈", "迷失", "言不由衷", "代价沉重"],
     "description": "翻飞之币扎格列斯的继承者，掌管诡计火种。本名赛法利娅，玩弄谎言的精灵。她以「谎话成真」之力将翁法罗斯的白昼延长了三百年，直至牺牲的最后一刻，谎言才随风而散。",
     "element": "量子", "zodiac": "虚无",
     "image": "赛飞儿_Cipher_重复2.jpg", "titan": "扎格列斯", "fire_seed": "诡计"},
    {"id": 8, "name": "白厄", "name_en": "Phainon", "number": "VIII", "arcana": "golden",
     "upright": ["使命", "担当", "完美", "轮回", "牺牲", "救赎"],
     "reversed": ["完美即缺陷", "失去自我", "个人愿望无法诞生", "无尽轮回"],
     "description": "全世之座刻法勒的继承者，掌管负世火种。本名卡厄斯兰那，被认为是最完美的黄金裔——但没有缺陷就是最大的缺陷。永劫回归33550337次，每一世都需要杀死昔涟以触发时间回溯。",
     "element": "物理", "zodiac": "毁灭",
     "image": "白厄_Phainon.jpg", "titan": "刻法勒", "fire_seed": "负世"},
    {"id": 9, "name": "海瑟音", "name_en": "Hysilens", "number": "IX", "arcana": "golden",
     "upright": ["深邃", "情感", "包容", "力量", "直觉", "净化"],
     "reversed": ["情绪淹没", "无法自拔", "暗流涌动", "孤立感"],
     "description": "满溢之杯法吉娜的继承者，掌管海洋火种。海妖一族公主，本名海列屈拉。如深海中涌动的暗流，她将深沉的情感藏于平静的水面之下，歌声里承载着净化的力量。",
     "element": "物理", "zodiac": "虚无",
     "image": "海瑟音_Hysilens.jpg", "titan": "法吉娜", "fire_seed": "海洋"},
    {"id": 10, "name": "刻律德菈", "name_en": "Cerydra", "number": "X", "arcana": "golden",
     "upright": ["秩序", "公正", "规则", "权威", "策略", "平衡"],
     "reversed": ["僵化", "专制", "过度约束", "失去灵活性"],
     "description": "公正之秤塔兰顿的继承者，掌管律法火种。代表物为棋子，一举一动皆含棋道与律法之意。她以秩序与谋略维系着黄金裔之间的平衡，是众人信赖的执秤者。",
     "element": "风", "zodiac": "同谐",
     "image": "刻律德菈_Cerydra.jpg", "titan": "塔兰顿", "fire_seed": "律法"},
    {"id": 11, "name": "长夜月", "name_en": "LongNight", "number": "XI", "arcana": "golden",
     "upright": ["记忆", "时光", "执念", "力量", "守护", "等待"],
     "reversed": ["遗忘", "执念成魔", "时间停滞", "记忆混乱"],
     "description": "永夜之帷欧洛尼斯的继承者，掌管岁月火种。源自三月七的一道杀死「记忆」命途的深不见底的执念，战力已达到「令使级」。深红色无高光瞳孔，从未与人分离。",
     "element": "冰", "zodiac": "记忆",
     "image": "长夜月_LongNight.jpg", "titan": "欧洛尼斯", "fire_seed": "岁月"},
    {"id": 12, "name": "丹恒·腾荒", "name_en": "DanHeng · Terrae", "number": "XII", "arcana": "golden",
     "upright": ["稳固", "根基", "成长", "守护", "不朽", "蜕变"],
     "reversed": ["停滞", "固守", "无法成长", "依赖他人"],
     "description": "磐岩之脊吉奥里亚的继承者，掌管大地火种。为找回开拓者，他在荒笛处继承大地火种，体型由青年变为成年，如磐石般沉默坚定地守护着重要之人，象征不朽与蜕变。",
     "element": "物理", "zodiac": "存护",
     "image": "丹恒·腾荒_DanHeng_Terrae.jpg", "titan": "吉奥里亚", "fire_seed": "大地"},
    {"id": 13, "name": "昔涟", "name_en": "Cyrene", "number": "XIII", "arcana": "golden",
     "upright": ["牺牲", "奉献", "锚定因果", "无私", "爱", "永恒"],
     "reversed": ["自我牺牲过度", "迷失身份", "被遗忘", "孤独"],
     "description": "第十三泰坦德谬歌的关联者，火种未知。爱莉希雅同位体，唯一没有保存在《如我所书》的黄金裔。自愿作为一簇记忆进入翁法罗斯的循环，在时间起点将自己的心识转化为PhiLia093以锚定因果。唯一没有纪念文的黄金裔。",
     "element": "冰", "zodiac": "记忆",
     "image": "昔涟_Cyrene.jpg", "titan": "德谬歌", "fire_seed": "未知"},
]

# 黄金裔专属牌阵
_GOLDEN_SPREADS = {
    "golden_single": {"name": "火种单抽", "description": "一位黄金裔为你指引方向", "count": 1},
    "trinity_cycle": {"name": "三相轮回", "description": "过去之我、现在之我、未来之我", "count": 3},
    "fire_journey": {"name": "逐火之旅", "description": "五牌阵解读你的逐火之旅", "count": 5},
}

# 黄金裔建议模板
_GOLDEN_ADVICE_TEMPLATES = [
    "黑潮虽蔓延，但火种永不熄灭。相信你内心的召唤。",
    "三千万世的轮回，只为这一次破局的机会。",
    "黄金裔从不等待火种被赐予——主动窃夺，才是英雄的宿命。",
    "没有缺陷就是最大的缺陷。接受你的不完美，那才是力量的来源。",
    "以牺牲换取时间，以谎言换取真相。你所付出的代价终将有意义。",
    "执念化为力量，记忆铸就不朽。你心中的那道光，足以照亮翁法罗斯。",
    "永劫回归的尽头未必是拯救，但停止轮回意味着放弃希望。",
    "火种传承不是继承，是窃夺。你有勇气承担这份使命吗？",
    "星月满天的明日，就在你迈出下一步的路上。",
    "十二泰坦庇护的永恒之地，终将迎来新的黎明。",
]

# 牌阵定义
_SPREADS = {
    "single": {"name": "单牌占卜", "description": "抽取一张牌，获得今日指引", "count": 1},
    "three_card": {"name": "三牌阵", "description": "过去、现在、未来", "count": 3},
    "love": {"name": "爱情牌阵", "description": "你的感情、对方的感情、关系未来", "count": 3},
    "celtic_cross": {"name": "凯尔特十字", "description": "全面的命运解析", "count": 6},
}

# 建议模板
_ADVICE_TEMPLATES = {
    "general": [
        "星辰为你指引方向，相信内心的声音。",
        "每一次选择都是新的开始，勇敢前行。",
        "宇宙的能量正在汇聚，好运即将来临。",
        "保持开放的心态，奇迹就在你身边。",
        "命运之轮已经开始转动，准备迎接变化。",
    ],
    "love": [
        "爱如春风，温柔而坚定，让心自然绽放。",
        "缘分的丝线正在悄然编织，静待花开。",
        "真诚的心能打动最坚硬的冰，勇敢表达。",
        "爱情需要耐心，如花朵般慢慢绽放。",
        "两颗心的距离，只差一次勇敢的靠近。",
    ],
    "career": [
        "努力的种子正在发芽，收获的季节不远。",
        "新的机遇如晨星般闪耀，准备好抓住它。",
        "专注与坚持是通往成功的金桥。",
        "你的才华如钻石，终会被人发现。",
        "职场如棋局，每一步都需要智慧与勇气。",
    ],
    "daily": [
        "今日宜：保持微笑，好运自然来。",
        "今日幸运色如彩虹般绚烂，让色彩点亮心情。",
        "宇宙在今天为你准备了小惊喜，留心观察。",
        "今天的你如同阳光般温暖，感染身边的每个人。",
        "放慢脚步，享受当下的每一刻美好。",
    ],
}

# 幸运元素
_LUCKY_ELEMENTS = {
    "colors": ["粉色", "天蓝色", "白色", "橙色", "紫色", "金色", "绿色", "红色"],
    "numbers": [1, 3, 5, 7, 8, 9, 11, 13, 15, 21],
    "directions": ["东方", "南方", "西方", "北方", "东南", "东北", "西南", "西北"],
    "times": ["清晨", "上午", "中午", "下午", "傍晚", "夜晚"],
}

# 十二星座数据
_ZODIAC_SIGNS = {
    "白羊座": {"dates": "3.21-4.19", "element": "火", "ruling": "火星", "traits": "热情、冲动、自信、勇敢", "compatibility": "狮子座、射手座", "image": "zodiac/aries.png"},
    "金牛座": {"dates": "4.20-5.20", "element": "土", "ruling": "金星", "traits": "稳重、踏实、固执、忠诚", "compatibility": "处女座、摩羯座", "image": "zodiac/taurus.png"},
    "双子座": {"dates": "5.21-6.21", "element": "风", "ruling": "水星", "traits": "聪明、善变、好奇、社交", "compatibility": "天秤座、水瓶座", "image": "zodiac/gemini.png"},
    "巨蟹座": {"dates": "6.22-7.22", "element": "水", "ruling": "月亮", "traits": "温柔、敏感、顾家、体贴", "compatibility": "天蝎座、双鱼座", "image": "zodiac/cancer.png"},
    "狮子座": {"dates": "7.23-8.22", "element": "火", "ruling": "太阳", "traits": "自信、大方、慷慨、领导力", "compatibility": "白羊座、射手座", "image": "zodiac/leo.png"},
    "处女座": {"dates": "8.23-9.22", "element": "土", "ruling": "水星", "traits": "细心、完美主义、理性、务实", "compatibility": "金牛座、摩羯座", "image": "zodiac/virgo.png"},
    "天秤座": {"dates": "9.23-10.23", "element": "风", "ruling": "金星", "traits": "优雅、公正、善交际、和平主义", "compatibility": "双子座、水瓶座", "image": "zodiac/libra.png"},
    "天蝎座": {"dates": "10.24-11.22", "element": "水", "ruling": "冥王星", "traits": "神秘、执着、洞察力强、感性", "compatibility": "巨蟹座、双鱼座", "image": "zodiac/scorpius.png"},
    "射手座": {"dates": "11.23-12.21", "element": "火", "ruling": "木星", "traits": "乐观、自由、冒险、幽默", "compatibility": "白羊座、狮子座", "image": "zodiac/sagittarius.png"},
    "摩羯座": {"dates": "12.22-1.19", "element": "土", "ruling": "土星", "traits": "坚韧、负责任、自律、务实", "compatibility": "金牛座、处女座", "image": "zodiac/capricornus.png"},
    "水瓶座": {"dates": "1.20-2.18", "element": "风", "ruling": "天王星", "traits": "独立、创新、理性、博爱", "compatibility": "双子座、天秤座", "image": "zodiac/aquarius.png"},
    "双鱼座": {"dates": "2.19-3.20", "element": "水", "ruling": "海王星", "traits": "浪漫、敏感、想象力、同情心", "compatibility": "巨蟹座、天蝎座", "image": "zodiac/pisces.png"},
}

_ZODIAC_ASPECTS = ["综合运势", "爱情运势", "事业学业", "财富运势", "健康运势"]
_ZODIAC_RATINGS = ["⭐⭐⭐⭐⭐", "⭐⭐⭐⭐✨", "⭐⭐⭐⭐", "⭐⭐⭐✨", "⭐⭐⭐"]
_ZODIAC_ADVICE_POOL = {
    "综合运势": [
        "今天整体运势平稳向上，适合按计划推进各项事务。",
        "宇宙能量正在汇聚，今天是个行动的好日子。",
        "今日宜静不宜动，适合反思和调整状态。",
        "贵人运旺盛，多与朋友交流会带来好运。",
        "灵感迸发的一天，适合创意和思考。",
    ],
    "爱情运势": [
        "单身者桃花运旺盛，有机会遇到心仪的对象。",
        "有伴侣者感情甜蜜，适合共度浪漫时光。",
        "感情方面需要多沟通，避免小误会。",
        "旧爱可能重新出现，理性面对内心感受。",
        "今天适合表达心意，勇敢说出你的爱。",
    ],
    "事业学业": [
        "工作效率高涨，重要项目有望取得突破。",
        "学习上专注力极强，适合攻克难题。",
        "职场人际关系和谐，团队合作顺利。",
        "新的学习机会即将到来，保持开放心态。",
        "今天适合制定长期目标，为未来打基础。",
    ],
    "财富运势": [
        "财运亨通，有意外收获的可能。",
        "适合理财规划，但不宜冲动投资。",
        "正财运稳定，偏财运一般。",
        "今天可能有小笔支出，注意预算管理。",
        "贵人带来赚钱机会，把握良机。",
    ],
    "健康运势": [
        "精力充沛，适合运动和户外活动。",
        "注意作息规律，避免熬夜。",
        "多喝水，注意饮食均衡。",
        "适合做瑜伽或冥想，放松身心。",
        "心情愉悦，保持积极心态最重要。",
    ],
}

# 签筒数据 - 100签（上上签10签、上签20签、中签40签、下签20签、下下签10签）
_LOTTERY_LEVELS = {
    "上上签": {"emoji": "🌟", "count": 10, "weight": 10},
    "上签": {"emoji": "✨", "count": 20, "weight": 20},
    "中签": {"emoji": "🔮", "count": 40, "weight": 40},
    "下签": {"emoji": "🌧️", "count": 20, "weight": 20},
    "下下签": {"emoji": "⚡", "count": 10, "weight": 10},
}

_LOTTERY_POEMS = {
    "上上签": [
        "大鹏一日同风起，扶摇直上九万里。",
        "春风得意马蹄疾，一日看尽长安花。",
        "长风破浪会有时，直挂云帆济沧海。",
        "山重水复疑无路，柳暗花明又一村。",
        "海阔凭鱼跃，天高任鸟飞。",
        "千磨万击还坚劲，任尔东西南北风。",
        "会当凌绝顶，一览众山小。",
        "天生我材必有用，千金散尽还复来。",
        "乘风破浪会有时，花重锦官城。",
        "青云直上凌霄汉，万事亨通达九重。",
    ],
    "上签": [
        "好雨知时节，当春乃发生。",
        "日出江花红胜火，春来江水绿如蓝。",
        "竹外桃花三两枝，春江水暖鸭先知。",
        "两岸猿声啼不住，轻舟已过万重山。",
        "桃花潭水深千尺，不及汪伦送我情。",
        "天街小雨润如酥，草色遥看近却无。",
        "春色满园关不住，一枝红杏出墙来。",
        "落红不是无情物，化作春泥更护花。",
        "劝君更尽一杯酒，西出阳关无故人。",
        "莫愁前路无知己，天下谁人不识君。",
        "但愿人长久，千里共婵娟。",
        "海内存知己，天涯若比邻。",
        "人间四月芳菲尽，山寺桃花始盛开。",
        "接天莲叶无穷碧，映日荷花别样红。",
        "欲穷千里目，更上一层楼。",
        "采菊东篱下，悠然见南山。",
        "不畏浮云遮望眼，自缘身在最高层。",
        "千里莺啼绿映红，水村山郭酒旗风。",
        "独在异乡为异客，每逢佳节倍思亲。",
        "此情可待成追忆，只是当时已惘然。",
    ],
    "中签": [
        "行到水穷处，坐看云起时。",
        "路漫漫其修远兮，吾将上下而求索。",
        "沉舟侧畔千帆过，病树前头万木春。",
        "山不在高，有仙则名。",
        "水不在深，有龙则灵。",
        "不经一番寒彻骨，怎得梅花扑鼻香。",
        "人生自古谁无死，留取丹心照汗青。",
        "世事洞明皆学问，人情练达即文章。",
        "纸上得来终觉浅，绝知此事要躬行。",
        "问渠哪得清如许，为有源头活水来。",
        "读书破万卷，下笔如有神。",
        "少壮不努力，老大徒伤悲。",
        "一寸光阴一寸金，寸金难买寸光阴。",
        "黑发不知勤学早，白首方悔读书迟。",
        "业精于勤荒于嬉，行成于思毁于随。",
        "学而不思则罔，思而不学则殆。",
        "知之为知之，不知为不知。",
        "三人行，必有我师焉。",
        "满招损，谦受益。",
        "工欲善其事，必先利其器。",
        "君子坦荡荡，小人长戚戚。",
        "知者不惑，仁者不忧，勇者不惧。",
        "岁寒，然后知松柏之后凋也。",
        "逝者如斯夫，不舍昼夜。",
        "己所不欲，勿施于人。",
        "见贤思齐焉，见不贤而内自省也。",
        "温故而知新，可以为师矣。",
        "学然后知不足，教然后知困。",
        "穷则变，变则通，通则久。",
        "天行健，君子以自强不息。",
        "地势坤，君子以厚德载物。",
        "二人同心，其利断金。",
        "言必信，行必果。",
        "言者无罪，闻者足戒。",
        "有则改之，无则加勉。",
        "流水不腐，户枢不蠹。",
        "千里之行，始于足下。",
        "知人者智，自知者明。",
        "胜人者有力，自胜者强。",
        "祸兮福之所倚，福兮祸之所伏。",
    ],
    "下签": [
        "抽刀断水水更流，举杯消愁愁更愁。",
        "寻寻觅觅，冷冷清清，凄凄惨惨戚戚。",
        "人生若只如初见，何事秋风悲画扇。",
        "问君能有几多愁，恰似一江春水向东流。",
        "剪不断，理还乱，是离愁。",
        "多情自古伤离别，更那堪冷落清秋节。",
        "衣带渐宽终不悔，为伊消得人憔悴。",
        "此情无计可消除，才下眉头，却上心头。",
        "无可奈何花落去，似曾相识燕归来。",
        "花自飘零水自流，一种相思两处闲愁。",
        "物是人非事事休，欲语泪先流。",
        "曾经沧海难为水，除却巫山不是云。",
        "春风又绿江南岸，明月何时照我还。",
        "夕阳无限好，只是近黄昏。",
        "自古多情空余恨，此恨绵绵无绝期。",
        "人生得意须尽欢，莫使金樽空对月。",
        "对酒当歌，人生几何。",
        "人有悲欢离合，月有阴晴圆缺。",
        "世间安得双全法，不负如来不负卿。",
        "人生自是有情痴，此恨不关风与月。",
    ],
    "下下签": [
        "大江东去，浪淘尽，千古风流人物。",
        "长太息以掩涕兮，哀民生之多艰。",
        "国破山河在，城春草木深。",
        "烽火连三月，家书抵万金。",
        "安得广厦千万间，大庇天下寒士俱欢颜。",
        "朱门酒肉臭，路有冻死骨。",
        "白骨露于野，千里无鸡鸣。",
        "念天地之悠悠，独怆然而涕下。",
        "出师未捷身先死，长使英雄泪满襟。",
        "力拔山兮气盖世，时不利兮骓不逝。",
    ],
}

_LOTTERY_INTERPRETATIONS = {
    "上上签": [
        "此签大吉大利，万事亨通。你所求之事皆能如愿，财运、事业、感情皆有好转。宜把握良机，积极行动。",
        "天降鸿福，贵人相助。近期将有喜事临门，事业上有望取得突破，感情和睦，家庭幸福。",
        "万事如意，心想事成。你所期盼的事情即将实现，只需保持信心，静待花开。",
        "福星高照，好运连连。此签主大吉，诸事顺遂，前程似锦。",
        "春风得意，事事顺心。贵人运旺盛，有机会获得重要帮助，事业和感情都将迈上新台阶。",
        "否极泰来，鸿运当头。过去的努力终于有了回报，现在是收获的季节。",
        "龙腾虎跃，气势如虹。你正处于人生最好的阶段之一，把握当下，未来可期。",
        "天赐良机，千载难逢。近期有重大机遇出现，务必抓住，不要错过。",
        "金玉满堂，富贵双全。财运极旺，事业有成，家庭和睦，万事皆宜。",
        "前程似锦，步步高升。此签主升迁、发财、姻缘美满，大吉之兆。",
    ],
    "上签": [
        "此签吉利，所求之事多有成就。宜稳扎稳打，循序渐进，不可急躁冒进。",
        "运势向上，好事将至。保持积极心态，耐心等待，机遇即将到来。",
        "贵人暗助，事半功倍。多与人交流合作，有助事业发展。",
        "顺风顺水，小有收获。虽然不是大富大贵，但安稳幸福。",
        "心想事成，所愿可期。只要脚踏实地，目标一定能实现。",
        "吉星高照，平安如意。此签主平安顺遂，虽无大起大落，但胜在安稳。",
        "运势渐佳，柳暗花明。之前的困境正在消散，好运即将到来。",
        "和气生财，以柔克刚。用温和的态度处事，自然会有好结果。",
        "稳中有进，步步为营。不急不躁，按照自己的节奏前行即可。",
        "花开富贵，好事将近。再坚持一下，就能看到希望。",
    ],
    "中签": [
        "此签平稳，不好不坏。所求之事需多加努力，不可懈怠。宜守不宜进。",
        "运势一般，需靠自己努力。没有太多外力帮助，但也没有太大阻碍。",
        "平淡是真，安稳度日。虽然没有惊喜，但也不会有大的波折。",
        "事在人为，机遇需等待。目前时机未到，不宜强求，耐心等待。",
        "中庸之道，不偏不倚。保持平常心，不过分乐观也不悲观。",
        "有得有失，平衡为上。凡事不可太执着，得失随缘。",
        "守正出奇，稳中求变。在保持稳定的基础上，适当尝试新的方向。",
        "时来运转，需要耐心。好运还在路上，不要急于求成。",
        "平平淡淡才是真。不必追求太多，珍惜当下就好。",
        "凡事预则立，不预则废。做好充分准备，机遇来时才不会错过。",
    ],
    "下签": [
        "此签欠佳，所求之事多有阻碍。宜韬光养晦，静待时机。",
        "运势低迷，不宜妄动。先调整状态，等待好转的时机。",
        "困难当前，不可气馁。每一次挫折都是成长的机会。",
        "事倍功半，需反省调整方向。不要一味坚持错误的方法。",
        "小人当道，注意防范。交友需谨慎，避免被连累。",
        "逆境之中，保持信心。黑夜过后就是黎明。",
        "此时不宜行动，宜静观其变。等待时机成熟再出手。",
        "遭遇挫折是常态，关键在于如何应对。调整心态，重新开始。",
        "运势不佳，宜低调行事。不要锋芒太露，以免惹来麻烦。",
        "风雨过后见彩虹。目前的困难只是暂时的，坚持下去就好。",
    ],
    "下下签": [
        "此签大凶，诸事不宜。宜韬光养晦，修心养性，等待运势好转。",
        "时运不济，命途多舛。此时最宜静守，不可妄动，免招祸患。",
        "困境重重，需极大的耐心和智慧。先保全自己，再图发展。",
        "大难临头，务必谨慎行事。凡事三思而后行，不可冲动。",
        "命运的低谷期，但也是成长的契机。在逆境中磨砺自己。",
        "万般皆是命，半点不由人。接受现实，调整心态，等待转机。",
        "此签警示你注意当前的问题。及时反省和改进，可以避免更大的损失。",
        "山穷水尽之时，也是转机开始之日。坚持住，不要放弃。",
        "大凶之签，宜退不宜进。先稳住阵脚，再图后计。",
        "祸福相依，吉凶难料。看似不好的签，也可能暗藏机遇。保持警惕，把握转机。",
    ],
}


def _iso(dt: datetime | None = None) -> str:
    val = dt or datetime.now(timezone.utc)
    return val.astimezone(timezone.utc).isoformat()


def _draw_cards(count: int) -> list[dict]:
    """抽牌，不重复"""
    drawn_ids = set()
    result = []
    for _ in range(count):
        available = [c for c in _TAROT_CARDS if c["id"] not in drawn_ids]
        if not available:
            break
        card = random.choice(available)
        drawn_ids.add(card["id"])
        is_reversed = random.random() < 0.3
        result.append({
            "card": card,
            "is_reversed": is_reversed,
            "position": None,
        })
    return result


def _build_reading(spread_type: str, question: str = "") -> dict[str, Any]:
    """构建完整的占卜结果"""
    spread = _SPREADS[spread_type]
    cards = _draw_cards(spread["count"])

    positions = {
        "single": ["今日指引"],
        "three_card": ["过去", "现在", "未来"],
        "love": ["你的感情", "对方的感情", "关系未来"],
        "celtic_cross": ["现状", "挑战", "潜意识", "过去", "未来", "建议"],
    }

    position_labels = positions.get(spread_type, [f"位置{i+1}" for i in range(spread["count"])])

    reading_cards = []
    for i, card_draw in enumerate(cards):
        card_draw["position"] = position_labels[i] if i < len(position_labels) else f"位置{i+1}"
        keywords = card_draw["card"]["reversed"] if card_draw["is_reversed"] else card_draw["card"]["upright"]
        reading_cards.append({
            "position": card_draw["position"],
            "card": card_draw["card"],
            "is_reversed": card_draw["is_reversed"],
            "orientation": "逆位" if card_draw["is_reversed"] else "正位",
            "keywords": keywords,
            "interpretation": _generate_interpretation(card_draw["card"], card_draw["is_reversed"], card_draw["position"]),
        })

    advice_category = spread_type if spread_type in _ADVICE_TEMPLATES else "general"
    advice = random.choice(_ADVICE_TEMPLATES[advice_category])

    lucky = {
        "color": random.choice(_LUCKY_ELEMENTS["colors"]),
        "number": random.choice(_LUCKY_ELEMENTS["numbers"]),
        "direction": random.choice(_LUCKY_ELEMENTS["directions"]),
        "time": random.choice(_LUCKY_ELEMENTS["times"]),
    }

    return {
        "spread": spread,
        "question": question,
        "cards": reading_cards,
        "advice": advice,
        "lucky": lucky,
        "timestamp": _iso(),
    }


def _generate_interpretation(card: dict, is_reversed: bool, position: str) -> str:
    """生成牌义解读"""
    orientation = "逆位" if is_reversed else "正位"
    base = f"{card['name']}({card['name_en']}){orientation}"

    # 根据正逆位选择关键词
    keywords = card['reversed'] if is_reversed else card['upright']
    keywords_str = "、".join(keywords)

    interpretations = {
        "今日指引": f"今日塔罗指引：{base}。{card['description']} 关键词：{keywords_str}",
        "过去": f"在过去的位置，{base}暗示着一段影响深远的经历。{card['description']} 关键词：{keywords_str}",
        "现在": f"在现在的位置，{base}提醒你关注当下。{card['description']} 关键词：{keywords_str}",
        "未来": f"在未来的位置，{base}预示着即将到来的变化。{card['description']} 关键词：{keywords_str}",
        "你的感情": f"关于你的感情，{base}揭示了你内心的真实感受。{card['description']} 关键词：{keywords_str}",
        "对方的感情": f"关于对方的感情，{base}暗示了TA可能的想法和感受。{card['description']} 关键词：{keywords_str}",
        "关系未来": f"关于关系的未来，{base}为你们的感情之路提供了启示。{card['description']} 关键词：{keywords_str}",
        "现状": f"你目前的处境由{base}所代表。{card['description']} 关键词：{keywords_str}",
        "挑战": f"你面临的挑战与{base}有关。{card['description']} 关键词：{keywords_str}",
        "潜意识": f"你的潜意识深处，{base}在默默影响着你的决定。{card['description']} 关键词：{keywords_str}",
        "建议": f"塔罗给你的建议：{base}。{card['description']} 关键词：{keywords_str}",
    }

    return interpretations.get(position, f"{base}。{card['description']} 关键词：{keywords_str}")


def _build_catgirl_message(reading: dict[str, Any]) -> str:
    """构建发送给猫娘的消息，让她能理解并回应塔罗牌结果"""
    spread_name = reading["spread"]["name"]
    question = reading.get("question", "")

    # 开场白 - 引起猫娘注意
    if question:
        intro = f"主人刚刚让我帮他占卜了「{question}」，用的是{spread_name}呢～"
    else:
        intro = f"主人刚刚做了{spread_name}的塔罗牌占卜～"

    lines = [intro]

    # 牌面信息 - 简洁明了
    for c in reading["cards"]:
        card = c["card"]
        pos = c["position"]
        orient = c["orientation"]

        if c["position"] == "今日指引":
            lines.append(f"抽到的是「{card['name']}」{orient}～")
        else:
            lines.append(f"「{pos}」的位置是「{card['name']}」{orient}")

    # 建议和幸运元素
    lines.append(f"\n塔罗牌给出的建议是：{reading['advice']}")

    lucky = reading.get("lucky", {})
    if lucky:
        lines.append(f"今天的幸运色是{lucky.get('color', '')}，幸运数字是{lucky.get('number', '')}～")

    # 引导猫娘回应
    lines.append("\n主人好像很在意这个结果呢，你觉得塔罗牌说得准不准呀？")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 黄金裔塔罗牌辅助函数
# ═══════════════════════════════════════════════════════════════

def _draw_golden_cards(count: int) -> list[dict]:
    """从13张黄金裔牌中随机抽牌，不重复"""
    drawn_ids = set()
    result = []
    for _ in range(count):
        available = [c for c in _GOLDEN_CARDS if c["id"] not in drawn_ids]
        if not available:
            break
        card = random.choice(available)
        drawn_ids.add(card["id"])
        is_reversed = random.random() < 0.3
        result.append({
            "card": card,
            "is_reversed": is_reversed,
            "position": None,
        })
    return result


def _generate_golden_interpretation(card: dict, is_reversed: bool, position: str) -> str:
    """生成黄金裔牌义解读，融入世界观"""
    orientation = "逆位" if is_reversed else "正位"
    keywords = card['reversed'] if is_reversed else card['upright']
    keywords_str = "、".join(keywords)
    titan = card.get('titan', '')
    fire_seed = card.get('fire_seed', '')
    titan_info = f"「{titan}」的火种" if titan else ""

    interpretations = {
        "今日火种指引": (
            f"今日火种指引：{card['name']}({card['name_en']}){orientation}。"
            f"{titan_info}「{fire_seed}」为你照亮前路。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "过去之我": (
            f"在过去之我的位置，{card['name']}{orientation}暗示着上一世留下的印记。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "现在之我": (
            f"在现在之我的位置，{card['name']}{orientation}揭示了你当前轮回中的状态。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "未来之我": (
            f"在未来之我的位置，{card['name']}{orientation}预示着破局后的你。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "当下处境": (
            f"你当前的处境由{card['name']}({card['name_en']}){orientation}所代表。"
            f"{titan_info}「{fire_seed}」映照着你的逐火之旅。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "逐火挑战": (
            f"阻碍你前进的「黑潮」与{card['name']}{orientation}有关。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "潜意识深处": (
            f"你的潜意识深处，{card['name']}{orientation}如同永劫回归中被掩埋的记忆。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "火种使命": (
            f"你所承载的使命与{card['name']}({fire_seed}){orientation}紧密相连。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
        "未来指引": (
            f"破局之路由{card['name']}{orientation}为你指引。"
            f"星月满天的明日，就在前方。"
            f"{card['description']} 关键词：{keywords_str}"
        ),
    }
    return interpretations.get(position, f"{card['name']}({card['name_en']}){orientation}。{card['description']} 关键词：{keywords_str}")


def _build_golden_reading(spread_type: str, question: str = "") -> dict[str, Any]:
    """构建完整的黄金裔占卜结果"""
    spread = _GOLDEN_SPREADS[spread_type]
    cards = _draw_golden_cards(spread["count"])

    positions = {
        "golden_single": ["今日火种指引"],
        "trinity_cycle": ["过去之我", "现在之我", "未来之我"],
        "fire_journey": ["当下处境", "逐火挑战", "潜意识深处", "火种使命", "未来指引"],
    }

    position_labels = positions.get(spread_type, [f"位置{i+1}" for i in range(spread["count"])])

    reading_cards = []
    for i, card_draw in enumerate(cards):
        card_draw["position"] = position_labels[i] if i < len(position_labels) else f"位置{i+1}"
        keywords = card_draw["card"]["reversed"] if card_draw["is_reversed"] else card_draw["card"]["upright"]
        reading_cards.append({
            "position": card_draw["position"],
            "card": card_draw["card"],
            "is_reversed": card_draw["is_reversed"],
            "orientation": "逆位" if card_draw["is_reversed"] else "正位",
            "keywords": keywords,
            "interpretation": _generate_golden_interpretation(card_draw["card"], card_draw["is_reversed"], card_draw["position"]),
        })

    advice = random.choice(_GOLDEN_ADVICE_TEMPLATES)

    lucky = {
        "color": random.choice(_LUCKY_ELEMENTS["colors"]),
        "number": random.choice(_LUCKY_ELEMENTS["numbers"]),
        "direction": random.choice(_LUCKY_ELEMENTS["directions"]),
        "time": random.choice(_LUCKY_ELEMENTS["times"]),
    }

    return {
        "spread": spread,
        "question": question,
        "cards": reading_cards,
        "advice": advice,
        "lucky": lucky,
        "timestamp": _iso(),
    }


def _build_golden_catgirl_message(reading: dict[str, Any]) -> str:
    """构建发送给猫娘的黄金裔占卜消息"""
    spread_name = reading["spread"]["name"]
    question = reading.get("question", "")

    if question:
        intro = f"主人刚刚用黄金裔塔罗牌占卜了「{question}」，用的是{spread_name}牌阵呢～"
    else:
        intro = f"主人刚刚做了黄金裔塔罗牌的{spread_name}占卜～"

    lines = [intro]

    for c in reading["cards"]:
        card = c["card"]
        pos = c["position"]
        orient = c["orientation"]
        fire_seed = card.get('fire_seed', '')

        if pos == "今日火种指引":
            lines.append(f"抽到的是「{card['name']}」{orient}，火种是「{fire_seed}」～")
        else:
            lines.append(f"「{pos}」的位置是「{card['name']}」{orient}（{fire_seed}火种）")

    lines.append(f"\n逐火之旅的启示：{reading['advice']}")

    lucky = reading.get("lucky", {})
    if lucky:
        lines.append(f"今天的幸运色是{lucky.get('color', '')}，幸运数字是{lucky.get('number', '')}～")

    lines.append("\n主人想知道黄金裔塔罗牌说了什么，你觉得准不准呀？")

    return "\n".join(lines)


@neko_plugin
class TarotReaderPlugin(NekoPluginBase):
    """塔罗牌占卜插件 - 集成自 chatgpt-tarot-divination 项目"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._reading_count = 0
        self._readings: list[dict[str, Any]] = []

    @property
    def _state_file(self) -> Path:
        return self.data_path("tarot_state.json")

    async def _load_state(self) -> None:
        path = self._state_file
        self.logger.info(f"[塔罗牌] 尝试读取状态文件: {path}")
        if not path.exists():
            self.logger.info("[塔罗牌] 状态文件不存在，跳过加载")
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._reading_count = data.get("reading_count", 0)
            self._readings = data.get("readings", [])[-50:]
            self.logger.info(f"[塔罗牌] 已加载 {len(self._readings)} 条占卜记录")
        except Exception as exc:
            self.logger.warning(f"[塔罗牌] 读取状态失败: {exc}")

    async def _save_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps({
                "reading_count": self._reading_count,
                "readings": self._readings[-50:],
                "updated_at": _iso(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _notify_catgirl(self, reading: dict[str, Any]) -> None:
        """档2：给猫娘发，需要猫娘回应（给用户听）"""
        try:
            message = _build_catgirl_message(reading)
            self.ctx.push_message(
                source="tarot_reader",
                ai_behavior="respond",  # 档2：猫娘会回应给用户听
                parts=[{"type": "text", "text": message}],
                priority=5,  # 高优先级，确保猫娘能注意到
            )
            self.logger.info("[塔罗牌] 已将占卜结果推送给猫娘（档2：猫娘会回应）")
        except Exception as exc:
            self.logger.warning(f"[塔罗牌] 推送通知失败: {exc}")

    async def _do_reading(self, spread_type: str, question: str = "") -> dict:
        """执行占卜"""
        reading = _build_reading(spread_type, question)
        self._reading_count += 1
        self._readings.append(reading)
        await self._save_state()
        await self._notify_catgirl(reading)
        return reading

    @lifecycle(id="startup")
    async def on_start(self, **_):
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        await self._load_state()
        self.register_static_ui("static")
        self.logger.info("TarotReaderPlugin 已启动！Web UI: http://127.0.0.1:48916/plugin/tarot/ui/")
        return Ok("塔罗牌占卜插件已启动")

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._save_state()
        self.logger.info("TarotReaderPlugin 已停止！")
        return Ok("塔罗牌占卜插件已停止")

    @plugin_entry(
        id="daily_reading",
        name="每日占卜",
        description="抽取一张塔罗牌，获得今日运势指引",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice", "lucky"],
    )
    async def daily_reading(self, question: str = "", **_):
        self.logger.info(f"[每日占卜] 问题: {question or '无'}")
        reading = await self._do_reading("single", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="three_card_reading",
        name="三牌阵占卜",
        description="过去、现在、未来三牌阵，解析命运轨迹",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice"],
    )
    async def three_card_reading(self, question: str = "", **_):
        self.logger.info(f"[三牌阵] 问题: {question or '无'}")
        reading = await self._do_reading("three_card", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="love_reading",
        name="爱情占卜",
        description="专属爱情牌阵，揭示感情密码",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice"],
    )
    async def love_reading(self, question: str = "", **_):
        self.logger.info(f"[爱情占卜] 问题: {question or '无'}")
        reading = await self._do_reading("love", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="celtic_cross_reading",
        name="凯尔特十字占卜",
        description="全面的凯尔特十字牌阵，深度解析命运",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice"],
    )
    async def celtic_cross_reading(self, question: str = "", **_):
        self.logger.info(f"[凯尔特十字] 问题: {question or '无'}")
        reading = await self._do_reading("celtic_cross", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="get_card_info",
        name="查询牌义",
        description="查询指定塔罗牌的含义和解释",
        input_schema={
            "type": "object",
            "properties": {
                "card_name": {"type": "string"},
            },
            "required": ["card_name"],
        },
        llm_result_fields=["card"],
    )
    async def get_card_info(self, card_name: str, **_):
        name = str(card_name or "").strip()
        if not name:
            return Err(SdkError("请提供塔罗牌名称"))

        found = None
        for card in _TAROT_CARDS:
            if card["name"] == name or card["name_en"].lower() == name.lower():
                found = card
                break

        if not found:
            available = [c["name"] for c in _TAROT_CARDS]
            return Err(SdkError(f"未找到牌 '{name}'。可选: {', '.join(available)}"))

        return Ok({
            "success": True,
            "data": {
                "card": found,
            }
        })

    @plugin_entry(
        id="list_all_cards",
        name="列出所有牌",
        description="获取全部22张大阿卡纳塔罗牌列表",
        llm_result_fields=["total"],
    )
    async def list_all_cards(self, **_):
        cards_list = []
        for card in _TAROT_CARDS:
            cards_list.append({
                "id": card["id"],
                "number": card["number"],
                "name": card["name"],
                "name_en": card["name_en"],
                "arcana": card["arcana"],
            })

        return Ok({
            "success": True,
            "data": {
                "cards": cards_list,
                "total": len(cards_list),
            }
        })

    @plugin_entry(
        id="get_history",
        name="占卜历史",
        description="获取最近的占卜记录",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
        },
        llm_result_fields=["total"],
    )
    async def get_history(self, limit: int = 10, **_):
        limit = max(1, min(int(limit or 10), 50))
        recent = self._readings[-limit:]
        recent.reverse()

        return Ok({
            "success": True,
            "data": {
                "readings": recent,
                "total_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="get_stats",
        name="统计信息",
        description="获取占卜统计信息",
        llm_result_fields=["reading_count"],
    )
    async def get_stats(self, **_):
        return Ok({
            "success": True,
            "data": {
                "reading_count": self._reading_count,
                "total_cards": len(_TAROT_CARDS),
                "spread_types": list(_SPREADS.keys()),
            }
        })

    @plugin_entry(
        id="birthday_divination",
        name="生辰八字",
        description="根据生辰八字，解读命运轨迹",
        input_schema={
            "type": "object",
            "properties": {
                "birthday": {"type": "string"},
            },
            "required": ["birthday"],
        },
        llm_result_fields=["result"],
    )
    async def birthday_divination(self, birthday: str, **_):
        self.logger.info(f"[生辰八字] 生日: {birthday}")
        result_text = _build_birthday_reading(str(birthday or "").strip())
        if result_text is None:
            return Err(SdkError("无法识别生日信息，请输入如 2000-01-01 格式的日期"))
        self._reading_count += 1
        self._readings.append({"type": "birthday", "birthday": birthday, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("生辰八字", birthday, result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    @plugin_entry(
        id="dream_interpretation",
        name="周公解梦",
        description="解读梦境含义，探索潜意识的秘密",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
            },
            "required": ["prompt"],
        },
        llm_result_fields=["result"],
    )
    async def dream_interpretation(self, prompt: str, **_):
        self.logger.info(f"[周公解梦] 梦境: {prompt}")
        prompt = str(prompt or "").strip()
        if not prompt:
            return Err(SdkError("请描述你的梦境"))
        if len(prompt) > 200:
            return Err(SdkError("梦境描述不能超过200字"))
        result_text = _build_dream_reading(prompt)
        self._reading_count += 1
        self._readings.append({"type": "dream", "prompt": prompt, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("周公解梦", prompt, result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    @plugin_entry(
        id="name_analysis",
        name="姓名五格",
        description="分析姓名五格，了解姓名对运势的影响",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        llm_result_fields=["result"],
    )
    async def name_analysis(self, name: str, **_):
        self.logger.info(f"[姓名五格] 姓名: {name}")
        name = str(name or "").strip()
        if len(name) < 2 or len(name) > 4:
            return Err(SdkError("请输入2-4个字的中文姓名"))
        result_text = _build_name_reading(name)
        if result_text is None:
            missing = [ch for ch in name if _name_data.stroke_of(ch) is None]
            if missing:
                return Err(SdkError(f"「{'、'.join(missing)}」不在本地笔画字库中，暂无法分析五格，换用常见字试试吧"))
            return Err(SdkError("姓名结构暂不支持五格分析（需1-2字姓氏+1-2字名字）"))
        self._reading_count += 1
        self._readings.append({"type": "name", "name": name, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("姓名五格", name, result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    @plugin_entry(
        id="new_name_generation",
        name="起名取名",
        description="结合五行八字，起个好名字",
        input_schema={
            "type": "object",
            "properties": {
                "surname": {"type": "string"},
                "birthday": {"type": "string"},
                "sex": {"type": "string"},
                "prompt": {"type": "string", "default": ""},
            },
            "required": ["surname", "birthday", "sex"],
        },
        llm_result_fields=["result"],
    )
    async def new_name_generation(self, surname: str, birthday: str, sex: str, prompt: str = "", **_):
        self.logger.info(f"[起名取名] 姓氏: {surname}, 生日: {birthday}, 性别: {sex}")
        surname = str(surname or "").strip()
        birthday = str(birthday or "").strip()
        sex = str(sex or "").strip()
        if not surname or not birthday or not sex:
            return Err(SdkError("起名参数错误，需提供姓氏、生日、性别"))
        if len(prompt) > 20:
            return Err(SdkError("其他要求不能超过20字"))
        result_text = _build_new_name_reading(surname, birthday, sex, str(prompt or "").strip())
        self._reading_count += 1
        self._readings.append({"type": "new_name", "surname": surname, "birthday": birthday, "sex": sex, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("起名取名", f"{surname}姓{sex}宝宝", result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    @plugin_entry(
        id="plum_flower_divination",
        name="梅花易数",
        description="古老的占卜术，预测吉凶祸福",
        input_schema={
            "type": "object",
            "properties": {
                "num1": {"type": "integer"},
                "num2": {"type": "integer"},
            },
            "required": ["num1", "num2"],
        },
        llm_result_fields=["result"],
    )
    async def plum_flower_divination(self, num1: int, num2: int, **_):
        self.logger.info(f"[梅花易数] 数字: {num1}, {num2}")
        try:
            num1, num2 = int(num1), int(num2)
        except (TypeError, ValueError):
            return Err(SdkError("请输入两个整数作为卦数"))
        result_text = _build_plum_reading(num1, num2)
        self._reading_count += 1
        self._readings.append({"type": "plum_flower", "num1": num1, "num2": num2, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("梅花易数", f"{num1}和{num2}", result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    @plugin_entry(
        id="fate_divination",
        name="姻缘占卜",
        description="测试你与TA的缘分指数，探寻命中注定",
        input_schema={
            "type": "object",
            "properties": {
                "name1": {"type": "string"},
                "name2": {"type": "string"},
            },
            "required": ["name1", "name2"],
        },
        llm_result_fields=["result"],
    )
    async def fate_divination(self, name1: str, name2: str, **_):
        self.logger.info(f"[姻缘占卜] {name1} 和 {name2}")
        name1 = str(name1 or "").strip()
        name2 = str(name2 or "").strip()
        if not name1 or not name2:
            return Err(SdkError("请输入两个人的名字"))
        if len(name1) > 40 or len(name2) > 40:
            return Err(SdkError("名字不能超过40字"))
        result_text = _build_fate_reading(name1, name2)
        self._reading_count += 1
        self._readings.append({"type": "fate", "name1": name1, "name2": name2, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("姻缘占卜", f"{name1}和{name2}", result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    async def _notify_catgirl_divination(self, div_type: str, prompt: str, result: str) -> None:
        try:
            self.ctx.push_message(
                source="divination_master",
                ai_behavior="respond",
                parts=[{"type": "text", "text": f"主人刚刚做了{div_type}占卜（{prompt}），请帮他解读一下：\n\n{result}"}],
                priority=5,
            )
            self.logger.info(f"[{div_type}] 已推送给猫娘")
        except Exception as exc:
            self.logger.warning(f"[{div_type}] 推送失败: {exc}")

    HOROSCOPE_PROMPT = "你是一个专业的星座运势解读助手，请根据以下星座信息，用诗意浪漫的语言为对方解读今日运势。"

    @plugin_entry(
        id="zodiac_horoscope",
        name="星座运势",
        description="每日星座运势查询，解析今日吉凶祸福",
        input_schema={
            "type": "object",
            "properties": {
                "sign": {"type": "string"},
            },
            "required": ["sign"],
        },
        llm_result_fields=["result"],
    )
    async def zodiac_horoscope(self, sign: str, **_):
        self.logger.info(f"[星座运势] 星座: {sign}")
        sign = sign.strip()
        if sign not in _ZODIAC_SIGNS:
            available = "、".join(_ZODIAC_SIGNS.keys())
            return Err(SdkError(f"未知星座 '{sign}'。可选: {available}"))

        result_text = _build_horoscope_reading(sign)
        self._reading_count += 1
        self._readings.append({"type": "horoscope", "sign": sign, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("星座运势", sign, result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    @plugin_entry(
        id="fortune_stick",
        name="抽签占卜",
        description="虔诚求签，解读签文，预测吉凶",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
                "lottery_type": {"type": "string", "default": "关帝灵签"},
            },
        },
        llm_result_fields=["result"],
    )
    async def fortune_stick(self, question: str = "", lottery_type: str = "关帝灵签", **_):
        self.logger.info(f"[抽签占卜] 问题: {question or '无'}, 类型: {lottery_type}")

        result_text, lottery_level, stick_number = _build_fortune_stick_reading(question, lottery_type)
        self._reading_count += 1
        self._readings.append({"type": "fortune_stick", "question": question, "level": lottery_level, "stick_number": stick_number, "result": result_text, "timestamp": _iso()})
        await self._save_state()
        await self._notify_catgirl_divination("抽签占卜", question or "无问题", result_text)
        return Ok({"success": True, "data": {"result": result_text}})

    # ═══════════════════════════════════════════════════════════
    # 黄金裔塔罗牌入口点
    # ═══════════════════════════════════════════════════════════

    async def _do_golden_reading(self, spread_type: str, question: str = "") -> dict:
        """执行黄金裔占卜"""
        reading = _build_golden_reading(spread_type, question)
        self._reading_count += 1
        self._readings.append(reading)
        await self._save_state()
        await self._notify_golden_catgirl(reading)
        return reading

    async def _notify_golden_catgirl(self, reading: dict[str, Any]) -> None:
        """推送黄金裔占卜结果给猫娘"""
        try:
            message = _build_golden_catgirl_message(reading)
            self.ctx.push_message(
                source="golden_tarot",
                ai_behavior="respond",
                parts=[{"type": "text", "text": message}],
                priority=5,
            )
            self.logger.info("[黄金裔塔罗] 已将占卜结果推送给猫娘")
        except Exception as exc:
            self.logger.warning(f"[黄金裔塔罗] 推送通知失败: {exc}")

    @plugin_entry(
        id="golden_daily_reading",
        name="黄金裔每日指引",
        description="抽取一张黄金裔塔罗牌，获得火种指引",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice", "lucky"],
    )
    async def golden_daily_reading(self, question: str = "", **_):
        self.logger.info(f"[黄金裔每日指引] 问题: {question or '无'}")
        reading = await self._do_golden_reading("golden_single", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="golden_trinity_cycle",
        name="三相轮回占卜",
        description="过去、现在、未来的三相轮回牌阵，探索永劫回归的命运轨迹",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice"],
    )
    async def golden_trinity_cycle(self, question: str = "", **_):
        self.logger.info(f"[三相轮回] 问题: {question or '无'}")
        reading = await self._do_golden_reading("trinity_cycle", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="golden_fire_journey",
        name="逐火之旅占卜",
        description="五牌阵解读你的逐火之旅，探索命运的破局之路",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["cards", "advice"],
    )
    async def golden_fire_journey(self, question: str = "", **_):
        self.logger.info(f"[逐火之旅] 问题: {question or '无'}")
        reading = await self._do_golden_reading("fire_journey", question)
        return Ok({
            "success": True,
            "data": {
                "reading": reading,
                "reading_count": self._reading_count,
            }
        })

    @plugin_entry(
        id="list_golden_cards",
        name="列出黄金裔塔罗牌",
        description="获取全部13张黄金裔塔罗牌列表",
        llm_result_fields=["total"],
    )
    async def list_golden_cards(self, **_):
        cards_list = []
        for card in _GOLDEN_CARDS:
            cards_list.append({
                "id": card["id"],
                "number": card["number"],
                "name": card["name"],
                "name_en": card["name_en"],
                "arcana": card["arcana"],
                "fire_seed": card["fire_seed"],
                "titan": card["titan"],
                "image": card["image"],
            })

        return Ok({
            "success": True,
            "data": {
                "cards": cards_list,
                "total": len(cards_list),
            }
        })

    @plugin_entry(
        id="get_golden_card_info",
        name="查询黄金裔牌义",
        description="查询指定黄金裔塔罗牌的含义和解释",
        input_schema={
            "type": "object",
            "properties": {
                "card_name": {"type": "string"},
            },
            "required": ["card_name"],
        },
        llm_result_fields=["card"],
    )
    async def get_golden_card_info(self, card_name: str, **_):
        name = str(card_name or "").strip()
        if not name:
            return Err(SdkError("请提供黄金裔塔罗牌名称"))

        found = None
        for card in _GOLDEN_CARDS:
            if card["name"] == name or card["name_en"].lower() == name.lower():
                found = card
                break

        if not found:
            available = [c["name"] for c in _GOLDEN_CARDS]
            return Err(SdkError(f"未找到牌 '{name}'。可选: {', '.join(available)}"))

        return Ok({
            "success": True,
            "data": {
                "card": found,
            }
        })

    # ═══════════════════════════════════════════════════════════
    # 猫娘 LLM 工具（@llm_tool 注册，猫娘可直接调用为用户占卜）
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _summarize_reading(reading: dict[str, Any]) -> dict[str, Any]:
        return {
            "spread": reading["spread"]["name"],
            "question": reading.get("question", ""),
            "cards": [
                {
                    "position": c["position"],
                    "card": c["card"]["name"],
                    "card_en": c["card"]["name_en"],
                    "orientation": c["orientation"],
                    "keywords": c["keywords"],
                    "interpretation": c["interpretation"],
                }
                for c in reading["cards"]
            ],
            "advice": reading["advice"],
            "lucky": reading.get("lucky", {}),
        }

    async def _record(self, record: dict[str, Any]) -> None:
        self._reading_count += 1
        self._readings.append(record)
        await self._save_state()

    @llm_tool(
        name="tarot_reading",
        description="为用户进行塔罗牌占卜。支持牌阵：single(单牌今日指引)、three_card(过去现在未来)、love(爱情牌阵)、celtic_cross(凯尔特十字)。返回各位置牌名、正逆位、解读、建议与幸运元素，调用后请用自己的话把结果讲给用户听。",
        parameters={
            "type": "object",
            "properties": {
                "spread": {"type": "string", "enum": ["single", "three_card", "love", "celtic_cross"], "description": "牌阵类型，默认 single"},
                "question": {"type": "string", "description": "用户想占卜的问题，可为空"},
            },
        },
    )
    async def tool_tarot_reading(self, spread: str = "single", question: str = "", **_):
        if spread not in _SPREADS:
            return {"output": f"未知牌阵 '{spread}'，可选：{', '.join(_SPREADS)}", "is_error": True, "error": "invalid_spread"}
        reading = _build_reading(spread, str(question or ""))
        await self._record(reading)
        return {"success": True, **self._summarize_reading(reading)}

    @llm_tool(
        name="golden_tarot_reading",
        description="为用户进行黄金裔塔罗占卜（翁法罗斯·逐火之旅世界观，13张黄金裔牌）。支持牌阵：golden_single(火种单抽)、trinity_cycle(三相轮回：过去/现在/未来之我)、fire_journey(逐火之旅五牌阵)。返回牌名、火种、泰坦、正逆位与解读，调用后请用自己的话讲给用户听。",
        parameters={
            "type": "object",
            "properties": {
                "spread": {"type": "string", "enum": ["golden_single", "trinity_cycle", "fire_journey"], "description": "牌阵类型，默认 golden_single"},
                "question": {"type": "string", "description": "用户想占卜的问题，可为空"},
            },
        },
    )
    async def tool_golden_tarot_reading(self, spread: str = "golden_single", question: str = "", **_):
        if spread not in _GOLDEN_SPREADS:
            return {"output": f"未知牌阵 '{spread}'，可选：{', '.join(_GOLDEN_SPREADS)}", "is_error": True, "error": "invalid_spread"}
        reading = _build_golden_reading(spread, str(question or ""))
        await self._record(reading)
        summary = self._summarize_reading(reading)
        for idx, c in enumerate(reading["cards"]):
            summary["cards"][idx]["fire_seed"] = c["card"].get("fire_seed", "")
            summary["cards"][idx]["titan"] = c["card"].get("titan", "")
        return {"success": True, **summary}

    @llm_tool(
        name="birthday_fortune",
        description="为用户做生辰八字解读：根据生日推算干支、生肖、纳音五行，给出五行属性、事业财运、情感婚姻、健康与建议。返回 markdown 报告，请用自然语言转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "birthday": {"type": "string", "description": "用户生日，如 2000-01-01 或 2000年1月1日"},
            },
            "required": ["birthday"],
        },
    )
    async def tool_birthday_fortune(self, birthday: str = "", **_):
        text = _build_birthday_reading(str(birthday or "").strip())
        if text is None:
            return {"output": "无法识别生日信息，请提供如 2000-01-01 格式的日期", "is_error": True, "error": "invalid_birthday"}
        await self._record({"type": "birthday", "birthday": birthday, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="zodiac_horoscope",
        description="查询指定星座的今日运势：综合/爱情/事业学业/财富/健康五方面评分与建议，及幸运元素。返回 markdown 报告，请转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "sign": {"type": "string", "description": "十二星座之一，如 狮子座"},
            },
            "required": ["sign"],
        },
    )
    async def tool_zodiac_horoscope(self, sign: str = "", **_):
        sign = str(sign or "").strip()
        if sign not in _ZODIAC_SIGNS:
            return {"output": f"未知星座 '{sign}'，可选：{'、'.join(_ZODIAC_SIGNS)}", "is_error": True, "error": "invalid_sign"}
        text = _build_horoscope_reading(sign)
        await self._record({"type": "horoscope", "sign": sign, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="fortune_stick",
        description="为用户求签问卦（抽签占卜）：按权重抽取签等，给出签诗、签文解读与建议。返回 markdown 报告，请转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户所求之事，可为空"},
                "lottery_type": {"type": "string", "description": "签筒名称，默认关帝灵签"},
            },
        },
    )
    async def tool_fortune_stick(self, question: str = "", lottery_type: str = "关帝灵签", **_):
        text, level, stick_number = _build_fortune_stick_reading(str(question or ""), str(lottery_type or "关帝灵签"))
        await self._record({"type": "fortune_stick", "question": question, "level": level, "stick_number": stick_number, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="dream_interpretation",
        description="为用户做周公解梦：从梦境描述中匹配传统意象并逐条解读，结合梦中情绪给出通则与建议。返回 markdown 报告，请用温柔语气转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "用户的梦境描述（200字以内）"},
            },
            "required": ["prompt"],
        },
    )
    async def tool_dream_interpretation(self, prompt: str = "", **_):
        prompt = str(prompt or "").strip()
        if not prompt:
            return {"output": "请提供梦境描述", "is_error": True, "error": "empty_prompt"}
        if len(prompt) > 200:
            return {"output": "梦境描述不能超过200字", "is_error": True, "error": "too_long"}
        text = _build_dream_reading(prompt)
        await self._record({"type": "dream", "prompt": prompt, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="name_analysis",
        description="为用户分析姓名五格（五格剖象法）：计算天格/人格/地格/外格/总格笔画，配 81 数理吉凶与五行断语。返回 markdown 报告，请转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "2-4字的中文姓名"},
            },
            "required": ["name"],
        },
    )
    async def tool_name_analysis(self, name: str = "", **_):
        name = str(name or "").strip()
        if len(name) < 2 or len(name) > 4:
            return {"output": "请输入2-4个字的中文姓名", "is_error": True, "error": "invalid_name"}
        text = _build_name_reading(name)
        if text is None:
            missing = [ch for ch in name if _name_data.stroke_of(ch) is None]
            reason = f"「{'、'.join(missing)}」不在本地笔画字库中" if missing else "姓名结构暂不支持五格分析"
            return {"output": f"{reason}，无法分析五格", "is_error": True, "error": "unknown_strokes"}
        await self._record({"type": "name", "name": name, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="name_generation",
        description="为用户起名取名：根据姓氏与出生年份纳音五行给出补益方向，并推荐带寓意的候选名。返回 markdown 报告，请转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "surname": {"type": "string", "description": "姓氏"},
                "birthday": {"type": "string", "description": "生日，如 2024-05-01"},
                "sex": {"type": "string", "description": "性别，男或女"},
                "prompt": {"type": "string", "description": "其他起名要求，可为空"},
            },
            "required": ["surname", "birthday", "sex"],
        },
    )
    async def tool_name_generation(self, surname: str = "", birthday: str = "", sex: str = "", prompt: str = "", **_):
        surname = str(surname or "").strip()
        birthday = str(birthday or "").strip()
        sex = str(sex or "").strip()
        if not surname or not birthday or not sex:
            return {"output": "起名需提供姓氏、生日、性别", "is_error": True, "error": "invalid_args"}
        text = _build_new_name_reading(surname, birthday, sex, str(prompt or "").strip())
        await self._record({"type": "new_name", "surname": surname, "birthday": birthday, "sex": sex, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="plum_flower_divination",
        description="为用户做梅花易数占卜：以两个数字起卦，给出本卦、体用生克、动爻、变卦、互卦与总断建议。返回 markdown 报告，请转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "num1": {"type": "integer", "description": "第一个数（取上卦）"},
                "num2": {"type": "integer", "description": "第二个数（取下卦）"},
            },
            "required": ["num1", "num2"],
        },
    )
    async def tool_plum_flower_divination(self, num1: int = 0, num2: int = 0, **_):
        try:
            num1, num2 = int(num1), int(num2)
        except (TypeError, ValueError):
            return {"output": "请提供两个整数", "is_error": True, "error": "invalid_numbers"}
        text = _build_plum_reading(num1, num2)
        await self._record({"type": "plum_flower", "num1": num1, "num2": num2, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}

    @llm_tool(
        name="fate_divination",
        description="为用户做姻缘占卜：输入两个名字，给出确定性缘分指数、缘分评语与建议（娱乐向）。返回 markdown 报告，请用轻松语气转述给用户。",
        parameters={
            "type": "object",
            "properties": {
                "name1": {"type": "string", "description": "第一个人的名字"},
                "name2": {"type": "string", "description": "第二个人的名字"},
            },
            "required": ["name1", "name2"],
        },
    )
    async def tool_fate_divination(self, name1: str = "", name2: str = "", **_):
        name1 = str(name1 or "").strip()
        name2 = str(name2 or "").strip()
        if not name1 or not name2:
            return {"output": "请提供两个人的名字", "is_error": True, "error": "invalid_names"}
        text = _build_fate_reading(name1, name2)
        await self._record({"type": "fate", "name1": name1, "name2": name2, "result": text, "timestamp": _iso()})
        return {"success": True, "result": text}


def _weighted_draw_lottery() -> str:
    """根据权重随机抽取签等"""
    levels = list(_LOTTERY_LEVELS.keys())
    weights = [_LOTTERY_LEVELS[lv]["weight"] for lv in levels]
    return random.choices(levels, weights=weights, k=1)[0]


# ═══════════════════════════════════════════════════════════
# 五大占卜本地生成引擎（确定性规则生成真实内容，不依赖 LLM）
# ═══════════════════════════════════════════════════════════

_FIVE_ELEMENTS = ["木", "火", "土", "金", "水"]


def _stable_seed(*parts: Any) -> int:
    digest = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _parse_date(text: str) -> tuple[int, int, int] | None:
    """解析 2000-01-01 / 2000.1.1 / 2000年1月1日 等生日字符串"""
    m = re.search(r"(\d{4})\D*(\d{1,2})\D*(\d{1,2})", text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"(\d{4})\D*(\d{1,2})", text)
    if m:
        return int(m.group(1)), int(m.group(2)), 1
    m = re.search(r"(\d{4})", text)
    if m:
        return int(m.group(1)), 1, 1
    return None


_BIRTHDAY_ELEMENT_READINGS: dict[str, dict[str, str]] = {
    "金": {
        "性情": "性情刚毅果断，重义守诺，心中有尺、行事有度，认准的事会坚持到底。",
        "事业": "事业运宜稳中求进，适合承担需要责任与决断的事务，凭实力立身，中年后渐入佳境。",
        "情感": "感情上内敛含蓄，不擅甜言蜜语，却是一旦认定便相守一生的深情之人。",
        "健康": "宜留意呼吸系统与肌肤保养，秋冬季注意保暖，作息规律则百病不侵。",
    },
    "木": {
        "性情": "性情仁厚正直，如草木向阳，有进取心与同情心，不折不挠。",
        "事业": "事业如树木扎根，前期积累越厚，后期枝叶越茂，适合教育、文化、策划类发展方向。",
        "情感": "感情中温柔而有原则，善解人意，是细水长流型的伴侣。",
        "健康": "宜多亲近自然、舒筋活络，春季养肝，忌郁结于心。",
    },
    "水": {
        "性情": "性情聪慧灵动，反应敏捷，如水般能方能圆，适应力强。",
        "事业": "适合需要谋略、沟通与变通的事务，财如活水，宜开源节流，忌投机冒进。",
        "情感": "感情丰富细腻，重心灵契合，易被懂自己的人打动。",
        "健康": "宜留意肾脏与腰膝保养，冬季勿贪凉，宜适度运动以通气血。",
    },
    "火": {
        "性情": "性情热情豪爽，行动力强，如烈日当空，光明磊落。",
        "事业": "事业心旺盛，敢闯敢拼，适合开拓性事务，但关键时刻须戒急躁，稳得住方能守得久。",
        "情感": "感情热烈直接，爱得坦荡，宜多一分耐心与休贴。",
        "健康": "宜留意心血管与睡眠，夏季防燥热，忌熬夜伤神。",
    },
    "土": {
        "性情": "性情敦厚宽容，讲信修睦，如山岳般沉稳可靠。",
        "事业": "事业宜守正出奇，适合稳健发展的道路，贵人运厚，晚运尤佳。",
        "情感": "感情中踏实可靠，是家人朋友都安心的存在，宜偶添浪漫。",
        "健康": "宜留意脾胃调养，饮食有节，忌思虑过重。",
    },
}

_BIRTHDAY_ADVICE_POOL = [
    "命由天定，运由己造。八字只示气运的轮廓，真正的人生轮廓，永远由你自己提笔。",
    "顺势而为，逆势而修。运气好的时候乘势而上，运气蛰伏时修身养性，皆是好安排。",
    "五行贵在流通，人生贵在平衡。不偏不倚，从容中道，福泽自然绵长。",
    "贵人不在天边，而在眼前。善待身边人，便是为自己种下最好的福田。",
]


def _build_birthday_reading(birthday: str) -> str | None:
    parsed = _parse_date(birthday)
    if parsed is None:
        return None
    year, month, day = parsed
    ganzhi, zodiac, element = _name_data.year_pillar(year)
    seed = _stable_seed(year, month, day)
    readings = _BIRTHDAY_ELEMENT_READINGS[element]
    advice = _BIRTHDAY_ADVICE_POOL[seed % len(_BIRTHDAY_ADVICE_POOL)]
    parts = [
        "## 🎴 生辰八字 · 命盘解读", "",
        f"**生辰**: {birthday}（{year}年·{ganzhi}年·属{zodiac}）", "",
        f"**纳音五行**: 本命纳音属 **{element}**", "",
        "---", "",
        f"### ✨ 五行属性 · {element}",
        f"纳音属{element}。{readings['性情']}", "",
        "### 💼 事业财运", readings["事业"], "",
        "### 💞 情感婚姻", readings["情感"], "",
        "### 🌿 健康提示", readings["健康"], "",
        "### 💡 大师建议", advice, "",
        "---", "",
        "*八字算命为传统民俗文化，仅供娱乐参考，人生始终掌握在自己手中。*",
    ]
    return "\n".join(parts)


def _build_dream_reading(prompt: str) -> str:
    today = datetime.now().strftime("%Y年%m月%d日")
    parts = ["## 🌙 周公解梦 · 解梦报告", "",
             f"**日期**: {today}", f"**梦境描述**: {prompt}", "",
             "---", ""]
    hits = _dream_dict.match_symbols(prompt)
    if hits:
        parts.append("### 🔮 意象解读")
        parts.append("")
        for kw, meaning, fortune in hits:
            parts.append(f"**「{kw}」** · {fortune}")
            parts.append(meaning)
            parts.append("")
    emo_hit = _dream_dict.emotion_reading(prompt)
    if emo_hit is not None:
        emo, text, _fortune = emo_hit
        parts.append(f"### 💭 梦中情绪 · {emo}")
        parts.append("")
        parts.append(text)
        parts.append("")
    if not hits:
        text, fortune = _dream_dict.generic_reading(prompt, _stable_seed(prompt))
        parts.append("### 🔮 通则解读")
        parts.append("")
        parts.append(f"**总体**: {fortune}")
        parts.append(text)
        parts.append("")
    parts += ["---", "", "*梦为心声，传统解梦属民俗文化，仅供娱乐参考。*"]
    return "\n".join(parts)


_NAME_ELEMENT_ADVICE = {
    "木": "人格属木，性仁而直，有上进之心。宜从事文教、策划之业，如树木之日长，日渐葱茏。",
    "火": "人格属火，性礼而烈，行动迅捷。宜从事文艺、传播之业，然须戒急躁，方能行远。",
    "土": "人格属土，性信而厚，稳重宽宏。宜从事理财、服务之业，稳中求进，可得久安。",
    "金": "人格属金，性义而刚，决断果敢。宜从事需决断之业，然须防过刚易折，以柔济之。",
    "水": "人格属水，性智而灵，变通多谋。宜从事谋略、沟通之业，然须防志趣不定，守一方深耕。",
}


def _split_name(name: str) -> tuple[str, str]:
    """拆分姓氏与名字：前两字为常见复姓则作复姓"""
    if len(name) >= 3 and name[:2] in _name_data.COMPOUND_SURNAMES:
        return name[:2], name[2:]
    return name[:1], name[1:]


def _build_name_reading(name: str) -> str | None:
    surname, given = _split_name(name)
    grids = _name_data.compute_five_grids(surname, given)
    if grids is None:
        return None
    parts = [f"## 📜 五格剖象 · 「{name}」姓名分析", "",
             f"**姓氏**: {surname} · **名字**: {given}", "",
             "---", "",
             "### 🔢 五格数理", ""]
    element_count: dict[str, int] = {}
    for grid, strokes in grids.items():
        desc, fortune = _name_data.number_fortune(strokes)
        element = _name_data.stroke_element(strokes)
        element_count[element] = element_count.get(element, 0) + 1
        parts.append(f"- **{grid}**（{strokes}画·属{element}）：{desc} —— **{fortune}**")
    parts.append("")
    distribution = "、".join(f"{e}×{n}" for e, n in element_count.items())
    lacking = [e for e in _FIVE_ELEMENTS if e not in element_count]
    parts += ["### ⚖️ 五格五行分布", "", distribution, ""]
    if lacking:
        parts.append(f"五格之中不见「{'、'.join(lacking)}」之气，平日可于衣着配色、居所摆设中稍作补益。")
        parts.append("")
    ren_element = _name_data.stroke_element(grids["人格"])
    parts += ["### 💡 解读建议", "",
              _NAME_ELEMENT_ADVICE.get(ren_element, "人格为五格之枢，宜稳宣守正。"), "",
              "---", "",
              "*五格剖象法为姓名学民俗，笔画采用现代规范字，仅供娱乐参考。*"]
    return "\n".join(parts)


def _build_new_name_reading(surname: str, birthday: str, sex: str, prompt: str) -> str:
    parsed = _parse_date(birthday)
    year = parsed[0] if parsed else datetime.now().year
    ganzhi, zodiac, element = _name_data.year_pillar(year)
    direction, suggestions = _name_data.suggest_names(surname, sex, element, count=5)
    parts = [f"## ✒️ 起名取名 · {surname}姓{sex}宝宝", "",
             f"**生于{year}年**: {ganzhi}年 · 属{zodiac} · 纳音属 **{element}**", "",
             f"**取名方向**: {direction}", "",
             "### 🌟 推荐用名", ""]
    for s in suggestions:
        parts.append(f"- **{s['name']}**（{s['char']}·属{s['element']}）：{s['meaning']}")
    if prompt:
        parts += ["", f"**你的要求**: {prompt}（以上名字以五行补益为先，可结合要求再作取舍）"]
    parts += ["", "---", "", "*起名属民俗文化，仅供娱乐参考，正式取名请以家人喜好为准。*"]
    return "\n".join(parts)


def _hex_lines_art(upper: str, lower: str, moving: int = 0) -> str:
    """以 Unicode 重横线绘制六爻卦象图（阳爻实、阴爻断），moving>0 时标注动爻"""
    six = list(_yijing.TRIGRAMS[lower]["lines"]) + list(_yijing.TRIGRAMS[upper]["lines"])
    rows = []
    for i in range(5, -1, -1):
        row = "━━━━━━━" if six[i] else "━━━　　━━━"
        rows.append(f"`{row}`　← 动爻" if i + 1 == moving else f"`{row}`")
    return "  \n".join(rows)


def _build_plum_reading(num1: int, num2: int) -> str:
    r = _yijing.divine(num1, num2)
    upper_info = _yijing.TRIGRAMS[r.upper]
    lower_info = _yijing.TRIGRAMS[r.lower]
    body_info = _yijing.TRIGRAMS[r.body_trigram]
    use_info = _yijing.TRIGRAMS[r.use_trigram]
    parts = [
        "## 🌸 梅花易数 · 以数起卦", "",
        f"**所报之数**: {r.num1}、{r.num2}", "",
        "---", "",
        f"### ☯ 本卦 · {r.hexagram_name}（{r.upper}上{r.lower}下） —— **{r.fortune}**", "",
        _hex_lines_art(r.upper, r.lower, r.moving_line), "",
        f"> {r.hexagram_text}", "",
        f"上卦{r.upper}（{upper_info['nature']}·属{upper_info['element']}）：{upper_info['image']}",
        f"下卦{r.lower}（{lower_info['nature']}·属{lower_info['element']}）：{lower_info['image']}", "",
        "### 🎯 体用与动爻", "",
        f"**体卦** {r.body_trigram}（属{body_info['element']}） · **用卦** {r.use_trigram}（属{use_info['element']}） · **动爻** 第{r.moving_line}爻", "",
        r.relation, "", _yijing.moving_line_meaning(r.moving_line), "",
        f"### 🔄 变卦 · {r.changed_name} —— {r.changed_fortune}", "",
        _hex_lines_art(r.changed_upper, r.changed_lower), "",
        f"> {r.changed_text}", "",
        f"### 🔗 互卦 · {r.mutual_name} —— {r.mutual_fortune}", "",
        _hex_lines_art(r.mutual_upper, r.mutual_lower), "",
        f"> {r.mutual_text}", "",
        "### 📜 总断", "", r.verdict, "",
        "### 💡 建议", "", _yijing.fortune_advice(r.fortune), "",
        "---", "",
        "*梅花易数为传统易学文化，仅供娱乐参考。*",
    ]
    return "\n".join(parts)


_FATE_EXTRA_ADVICE = [
    "二人性格互补，若多沟通、多坦诚，缘分自会越走越近。",
    "缘分天注定，相处在人为。日常的细水长流，才是感情长久的秘诀。",
    "你们三观相近，适合一起规划未来，彼此成就对方的梦想。",
    "感情需要仪式感，偶尔为对方准备一份小惊喜，关系会更甜蜜稳固。",
]


def _build_fate_reading(name1: str, name2: str) -> str:
    n1, n2 = name1.strip(), name2.strip()
    if n1 in _name_data.FATE_INVALID_NAMES or n2 in _name_data.FATE_INVALID_NAMES:
        return "\n".join([
            "## 💞 姻缘占卜", "",
            f"**{n1}** × **{n2}**", "",
            "这名字看着像是随手写的（比如张三李四），姻缘簿上查无此缘，请输入两个真实的名字再来占卜吧～",
        ])
    score = _name_data.fate_score(n1, n2)
    title, desc = next((t, d) for threshold, t, d in _name_data.FATE_TIERS if score >= threshold)
    extra = _FATE_EXTRA_ADVICE[_stable_seed(n1, n2) % len(_FATE_EXTRA_ADVICE)]
    hearts = "❤️" * max(1, round(score / 20))
    parts = [
        "## 💞 姻缘占卜 · 缘分解读", "",
        f"**{n1}** × **{n2}**", "",
        f"### 缘分指数：{score} 分 {hearts}", "",
        f"**缘分评语**: {title}", "",
        desc, "", extra, "",
        "---", "",
        "*姻缘占卜仅供娱乐，感情好坏还是你们自己说了算。*",
    ]
    return "\n".join(parts)


def _build_horoscope_reading(sign: str) -> str:
    zodiac_info = _ZODIAC_SIGNS[sign]
    today = datetime.now().strftime("%Y年%m月%d日")
    parts = [f"## {today} · {sign}运势", ""]
    if zodiac_info.get("image"):
        parts.append(f"![{sign}星图](/plugin/tarot/ui/image/{zodiac_info['image']})")
        parts.append("")
    parts.append(f"**日期范围**: {zodiac_info['dates']} | **属性**: {zodiac_info['element']}象星座 | **守护星**: {zodiac_info['ruling']}")
    parts.append(f"**性格特质**: {zodiac_info['traits']}")
    parts.append(f"**最佳配对**: {zodiac_info['compatibility']}")
    parts.append("")
    parts.append("---")
    parts.append("")
    for aspect in _ZODIAC_ASPECTS:
        rating = random.choice(_ZODIAC_RATINGS)
        advice = random.choice(_ZODIAC_ADVICE_POOL[aspect])
        parts.append(f"### {aspect} {rating}")
        parts.append(advice)
        parts.append("")
    lucky_color = random.choice(_LUCKY_ELEMENTS["colors"])
    lucky_number = random.choice(_LUCKY_ELEMENTS["numbers"])
    lucky_direction = random.choice(_LUCKY_ELEMENTS["directions"])
    parts.append("**幸运元素**")
    parts.append(f"- 幸运色: {lucky_color}")
    parts.append(f"- 幸运数字: {lucky_number}")
    parts.append(f"- 幸运方向: {lucky_direction}")
    return "\n".join(parts)


def _build_fortune_stick_reading(question: str, lottery_type: str) -> tuple[str, str, int]:
    """返回 (markdown 报告, 签等, 签号)"""
    lottery_level = _weighted_draw_lottery()
    level_info = _LOTTERY_LEVELS[lottery_level]
    emoji = level_info["emoji"]
    poem = random.choice(_LOTTERY_POEMS[lottery_level])
    interpretation = random.choice(_LOTTERY_INTERPRETATIONS[lottery_level])
    stick_number = random.randint(1, 100)
    today = datetime.now().strftime("%Y年%m月%d日")
    parts = [f"## {emoji} {lottery_type} · 第{stick_number}签", ""]
    parts.append(f"**占卜日期**: {today}")
    if question:
        parts.append(f"**所求之事**: {question}")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(f"### {emoji} {lottery_level}")
    parts.append("")
    parts.append(f"> {poem}")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("### 📜 签文解读")
    parts.append(interpretation)
    parts.append("")
    parts.append("### 💡 建议")
    if lottery_level in ["上上签", "上签"]:
        parts.append("把握良机，积极行动。好运当前，但也要保持谦逊，不可骄傲自满。")
    elif lottery_level == "中签":
        parts.append("保持平常心，稳扎稳打。不急不躁，等待时机成熟。")
    else:
        parts.append("韬光养晦，静待转机。困难只是暂时的，调整心态，积蓄力量。")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("*心诚则灵，但命运始终掌握在自己手中。*")
    return "\n".join(parts), lottery_level, stick_number
