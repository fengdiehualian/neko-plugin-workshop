"""civ6_buddy 知识库：文明/领袖中英映射 + 特性简述。

数据来源：游戏内 id（存档解析结果）→ 中文名。特性为常用简述，
供 YUI 陪玩时给出准确的文明背景与倾向。全部为人工整理的基础资料，
不读取游戏文件（游戏安装位仅英文文本，见探查记录）。
"""

from __future__ import annotations

# 文明 id → (中文名, 领袖 id → 领袖中文名, 文明特性一句话)
CIVILIZATIONS: dict[str, dict] = {
    "CIVILIZATION_AMERICA": {"name": "美国", "leader": "LEADER_T_ROOSEVELT:西奥多·罗斯福", "ua": "强力启动，首都周边强爆发；P51战斗机与帝国姿态外交加分"},
    "CIVILIZATION_ARABIA": {"name": "阿拉伯", "leader": "LEADER_SALADIN:萨拉丁", "ua": "宗教+科技双线，清真寺发力；传教+买大学，宗教胜利与科技兼顾"},
    "CIVILIZATION_AUSTRALIA": {"name": "澳大利亚", "leader": "LEADER_CURTIN:约翰·科廷", "ua": "沿海城市与牧站加成，被宣战/解放城市大爆发"},
    "CIVILIZATION_AZTEC": {"name": "阿兹特克", "leader": "LEADER_MONTECZUMA:蒙特祖马一世", "ua": "奢侈资源加战斗力，工人可用建造；早战强权"},
    "CIVILIZATION_BRAZIL": {"name": "巴西", "leader": "LEADER_PEDRO_II:佩德罗二世", "ua": "雨林地块文化加成，狂欢节与巨作翻倍；文化胜利向"},
    "CIVILIZATION_CANADA": {"name": "加拿大", "leader": "LEADER_WILFRID:威尔弗里德·劳雷尔", "ua": "不能主动宣战但冻土加成+曲棍球馆，和平发育向"},
    "CIVILIZATION_CHINA": {"name": "中国", "leader": "LEADER_QIN:秦始皇", "ua": "建造者消耗次数+1、奇观加速；长城与历史遗迹，奇迹流"},
    "CIVILIZATION_CREE": {"name": "克里", "leader": "LEADER_POUNDMAKER:庞德梅克", "ua": "商人+使者在邻国间穿梭，攒贸易路线与哨站视野"},
    "CIVILIZATION_EGYPT": {"name": "埃及", "leader": "LEADER_CLEOPATRA:克利奥帕特拉", "ua": "沿河奇观加速，与贸易伙伴加成；防洪渠与狮身人面像"},
    "CIVILIZATION_ENGLAND": {"name": "英格兰", "leader": "LEADER_VICTORIA:维多利亚", "ua": "海外殖民地与皇家海军武库，海图强力；红衫军远征"},
    "CIVILIZATION_FRANCE": {"name": "法国", "leader": "LEADER_CATHERINE_DE_MEDICI:凯瑟琳·德·美第奇", "ua": "巨作文化翻倍、中世纪奇观加速，间谍更强；文化胜利向"},
    "CIVILIZATION_GEORGIA": {"name": "格鲁吉亚", "leader": "LEADER_TAMAR:塔玛拉", "ua": "信仰城墙保护，黄金时代续航；宗教+防御向"},
    "CIVILIZATION_GERMANY": {"name": "德国", "leader": "LEADER_BARBAROSSA:腓特烈·巴巴罗萨", "ua": "相邻城市加成+额外军事槽位，产能流；工业区相邻加成最强"},
    "CIVILIZATION_GREECE": {"name": "希腊", "leader": "LEADER_GORGO:戈尔戈", "ua": "额外通配符槽位+领袖加成，市政/文化流灵活"},
    "CIVILIZATION_HUNGARY": {"name": "匈牙利", "leader": "LEADER_MATTHIAS:马加什·科维努斯", "ua": "雇佣城邦军队打折，多瑙河加成；城邦结盟向"},
    "CIVILIZATION_INCA": {"name": "印加", "leader": "LEADER_PACHACUTI:帕查库特克", "ua": "山地食物+梯田，山多图强势；山地利用极致"},
    "CIVILIZATION_INDIA": {"name": "印度", "leader": "LEADER_GANDHI:甘地", "ua": "宗教宽容+战狂惩罚减免，仙城与精神之道"},
    "CIVILIZATION_INDONESIA": {"name": "印度尼西亚", "leader": "LEADER_GITARJA:吉亚查", "ua": "沿海城市信仰加成，坎帕斯圣坛；海岛图强势"},
    "CIVILIZATION_JAPAN": {"name": "日本", "leader": "LEADER_HOJO:北条时宗", "ua": "军事区/圣坛/剧院相邻加成，明治维新；区域密集流"},
    "CIVILIZATION_KHMER": {"name": "高棉", "leader": "LEADER_JAYAVARMAN:阇耶跋摩七世", "ua": "河流+人口加成，寺庙建筑；宗教人口流"},
    "CIVILIZATION_KONGO": {"name": "刚果", "leader": "LEADER_MVEMBA:恩津加·姆班德", "ua": "无法建圣坛但文化/巨作翻倍、阿皮亚兵种强化；文化胜利最强之一"},
    "CIVILIZATION_KOREA": {"name": "韩国", "leader": "LEADER_SEONDEOK:善德女王", "ua": "书院相邻加成+科技爆发，矿山区块；科技胜利引擎"},
    "CIVILIZATION_MACEDON": {"name": "马其顿", "leader": "LEADER_ALEXANDER:亚历山大大帝", "ua": "征服城市全体力+文化、骑手攻城专精；早战碾压向"},
    "CIVILIZATION_MALI": {"name": "马里", "leader": "LEADER_SUNDIATA:孙迪亚塔·凯塔", "ua": "沙漠产能+金币、矿脉强化；黄金产能流"},
    "CIVILIZATION_MAORI": {"name": "毛利", "leader": "LEADER_KUPE:库佩", "ua": "开局海上探索+森林食物，不能获得传统科技加成；海图神"},
    "CIVILIZATION_MAPUCHE": {"name": "马普切", "leader": "LEADER_LAUTARO:劳塔罗", "ua": "敌人诞生伟人时战斗力加成，切卢比丘古丘；防掠向"},
    "CIVILIZATION_MONGOLIA": {"name": "蒙古", "leader": "LEADER_GENGHIS:成吉思汗", "ua": "骑兵+贸易驿站，侦查骑兵转骠骑；骑兵流"},
    "CIVILIZATION_NETHERLANDS": {"name": "荷兰", "leader": "LEADER_WILHELMINA:威廉明娜", "ua": "河畔+柑橘园与堤坝加成；邻接河流的商业强国"},
    "CIVILIZATION_NORWAY": {"name": "挪威", "leader": "LEADER_HARALD:哈拉尔德·哈德拉达", "ua": "海军近战+海洋掠夺，长船早期远航；海盗向"},
    "CIVILIZATION_NUBIA": {"name": "努比亚", "leader": "LEADER_AMANITORE:阿曼尼托尔", "ua": "远程单位生产加速+矿区加成；弓兵流"},
    "CIVILIZATION_OTTOMAN": {"name": "奥斯曼", "leader": "LEADER_SULEIMAN:苏莱曼一世", "ua": "围城加成+总督驻军，苏丹亲兵；攻城流"},
    "CIVILIZATION_PERSIA": {"name": "波斯", "leader": "LEADER_CYRUS:居鲁士", "ua": "突袭战争+贸易加成，帕萨加迪与长生军；先手突袭"},
    "CIVILIZATION_PHOENICIA": {"name": "腓尼基", "leader": "LEADER_DIDO:狄多", "ua": "殖民与海贸加成，移都能力；海图殖民流"},
    "CIVILIZATION_POLAND": {"name": "波兰", "leader": "LEADER_JADWIGA:雅德维加", "ua": "圣遗物+文化强势，翼骑兵；宗教文化双修"},
    "CIVILIZATION_PORTUGAL": {"name": "葡萄牙", "leader": "LEADER_JOAO:若昂三世", "ua": "航海贸易+城邦商品加成，帆船时代强；海贸流"},
    "CIVILIZATION_ROME": {"name": "罗马", "leader": "LEADER_TRAJAN:图拉真", "ua": "开局建筑免费+道路全通，军团基建；古典扩张流"},
    "CIVILIZATION_RUSSIA": {"name": "俄罗斯", "leader": "LEADER_PETER:彼得大帝", "ua": "冻土+信仰爆发，拉夫拉文化宗教区；冬将军"},
    "CIVILIZATION_SCOTLAND": {"name": "苏格兰", "leader": "LEADER_ROBERT:罗伯特一世", "ua": "高宜居度加成科技文化，高尔夫球场；快乐发育"},
    "CIVILIZATION_SCYTHIA": {"name": "斯基泰", "leader": "LEADER_TOMYRIS:托米丽司", "ua": "轻骑兵翻倍+治愈，萨卡弓骑；骑兵快攻极强"},
    "CIVILIZATION_SPAIN": {"name": "西班牙", "leader": "LEADER_PHILIP_II:腓力二世", "ua": "传教+征服双线，修道院加成；宗教征服向"},
    "CIVILIZATION_SUMERIA": {"name": "苏美尔", "leader": "LEADER_GILGAMESH:吉尔伽美什", "ua": "战车重装+通古斯泥砖，首发蛮族结盟；早期压制"},
    "CIVILIZATION_SWEDEN": {"name": "瑞典", "leader": "LEADER_KRISTINA:克里斯蒂娜", "ua": "巨作主题化+诺贝尔奖加成，博物馆文化；文化外交流"},
    "CIVILIZATION_ZULU": {"name": "祖鲁", "leader": "LEADER_SHAKA:沙卡", "ua": "军团经验+兵团加成，伊布坦朵；军事滚雪球"},
}

# 城邦 id → (中文名, 类型)
CITY_STATES: dict[str, str] = {
    "CIVILIZATION_AMSTERDAM": "阿姆斯特丹（商业）",
    "CIVILIZATION_ANTANANARIVO": "塔那那利佛（文化）",
    "CIVILIZATION_AUCKLAND": "奥克兰（工业）",
    "CIVILIZATION_BABYLON": "巴比伦（科技）",
    "CIVILIZATION_BANDAR_BRUNEI": "斯里巴加湾市（工业）",
    "CIVILIZATION_BOLOGNA": "博洛尼亚（科技）",
    "CIVILIZATION_BUENOS_AIRES": "布宜诺斯艾利斯（工业）",
    "CIVILIZATION_BRUSSELS": "布鲁塞尔（工业）",
    "CIVILIZATION_CAGUANAS": "卡瓜纳斯（军事）",
    "CIVILIZATION_CARTHAGE": "迦太基（军事）",
    "CIVILIZATION_FEZ": "非斯（宗教）",
    "CIVILIZATION_GENEVA": "日内瓦（科技）",
    "CIVILIZATION_GRANADA": "格拉纳达（军事）",
    "CIVILIZATION_HANSEATIC_LEAGUE": "汉萨同盟（商业）",
    "CIVILIZATION_HATTUSA": "哈图沙（军事）",
    "CIVILIZATION_HONG_KONG": "香港（商业）",
    "CIVILIZATION_JAKARTA": "雅加达（商业）",
    "CIVILIZATION_JERUSALEM": "耶路撒冷（宗教）",
    "CIVILIZATION_KABUL": "喀布尔（军事）",
    "CIVILIZATION_KANDY": "康提（宗教）",
    "CIVILIZATION_KUMASI": "库马西（文化）",
    "CIVILIZATION_LA_VENTA": "拉文塔（宗教）",
    "CIVILIZATION_LHASA": "拉萨（宗教）",
    "CIVILIZATION_LISBON": "里斯本（商业）",
    "CIVILIZATION_MEXICO_CITY": "墨西哥城（工业）",
    "CIVILIZATION_MITLA": "米特拉（宗教）",
    "CIVILIZATION_MOHENJO_DARO": "摩亨佐达罗（工业）",
    "CIVILIZATION_MUSCAT": "马斯喀特（商业）",
    "CIVILIZATION_NAN_MADOL": "南马都尔（文化）",
    "CIVILIZATION_NAZCA": "纳斯卡（宗教）",
    "CIVILIZATION_PALENQUE": "帕伦克（文化）",
    "CIVILIZATION_PRESLAV": "普雷斯拉夫（军事）",
    "CIVILIZATION_SEOUL": "首尔（科技）",
    "CIVILIZATION_STOCKHOLM": "斯德哥尔摩（文化）",
    "CIVILIZATION_TARUGA": "塔鲁加（文化）",
    "CIVILIZATION_TORONTO": "多伦多（工业）",
    "CIVILIZATION_VALLETTA": "瓦莱塔（军事）",
    "CIVILIZATION_VILNIUS": "维尔纽斯（文化）",
    "CIVILIZATION_WOLIN": "沃林（军事）",
    "CIVILIZATION_YEREVAN": "埃里温（宗教）",
    "CIVILIZATION_ZANZIBAR": "桑给巴尔（商业）",
}

# 地图大小 / 速度 / 时代 id → 中文
MAP_SIZES: dict[str, str] = {
    "MAPSIZE_DUEL": "决斗（2人）",
    "MAPSIZE_TINY": "极小（4人）",
    "MAPSIZE_SMALL": "小型（6人）",
    "MAPSIZE_STANDARD": "标准（8人）",
    "MAPSIZE_LARGE": "大型（10人）",
    "MAPSIZE_HUGE": "巨大（12人）",
}
GAME_SPEEDS: dict[str, str] = {
    "GAMESPEED_ONLINE": "联机（极快）",
    "GAMESPEED_QUICK": "快速",
    "GAMESPEED_STANDARD": "标准",
    "GAMESPEED_EPIC": "史诗",
    "GAMESPEED_MARATHON": "马拉松",
}

# 地图文件名 → 中文（常见）
MAP_FILES: dict[str, str] = {
    "Continents.lua": "大陆",
    "Continents and Islands.lua": "大陆与岛屿",
    "Pangaea.lua": "盘古大陆",
    "Archipelago.lua": "群岛",
    "Shuffle.lua": "随机混合",
    "Fractal.lua": "分形",
    "Inland Sea.lua": "内陆海",
    "Four Corners.lua": "四角",
    "Lakes.lua": "湖泊",
    "Seven Seas.lua": "七海",
    "Terra.lua": "特拉（新大陆）",
    "True Start Location Earth.lua": "真实地球",
    "Primordial.lua": "原始大陆",
    "Highlands.lua": "高地",
    "Island Plates.lua": "岛弧板块",
}

# 人类/AI 标识
AI_HUMAN_LABEL = {1: "AI", 2: "AI（已移除）", 3: "人类玩家", 5: "城邦/蛮族（AI）"}


def civ_name(civ_id: str) -> str:
    """文明 id → 中文名（城邦/文明均可）。"""
    if not civ_id:
        return civ_id or "未知"
    if civ_id in CIVILIZATIONS:
        return CIVILIZATIONS[civ_id]["name"]
    if civ_id in CITY_STATES:
        return CITY_STATES[civ_id]
    if civ_id == "CIVILIZATION_BARBARIAN":
        return "野蛮人"
    if civ_id.startswith("CIVILIZATION_LEVEL"):
        return civ_id.replace("CIVILIZATION_LEVEL_", "")
    # 城邦式命名兜底
    return civ_id.replace("CIVILIZATION_", "").replace("_", " ").title()


def leader_name(civ_id: str, leader_id: str | None) -> str:
    """领袖 id → 中文名。"""
    if civ_id in CIVILIZATIONS and leader_id:
        entry = CIVILIZATIONS[civ_id].get("leader", "")
        for part in entry.split(","):
            k, _, v = part.partition(":")
            if k.strip() == leader_id:
                return v.strip()
    if leader_id:
        return leader_id.replace("LEADER_", "").replace("MINOR_CIV_", "").replace("_", " ").title()
    return ""


def civ_ua(civ_id: str) -> str:
    """文明特性一句话（无则空串）。"""
    if civ_id in CIVILIZATIONS:
        return CIVILIZATIONS[civ_id]["ua"]
    return ""
