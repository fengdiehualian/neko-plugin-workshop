"""货物分类：把 ETS2 真实的货物识别成"货物家族"，给对应的玩法建议。

为什么需要单独一层：`knowledge.cargo_tip()` 是按**中文显示名**做关键词匹配的，
而遥测里同时给了两个真实字段——
  - `cargo_id`：游戏内部 id（英文、稳定，如 `scrap_metals` / `medical_vaccines`）
  - `cargo`   ：本地化显示名（如「废金属」，随语言变）

所以这里先用 id 判家族（稳），再用显示名兜底（兼容旧档/语言差异）。
**只做分类和真实机制对应的建议**，不编造游戏里不存在的规则。

家族与建议都对应 ETS2 真实机制：
  危险品——出事故的罚款与损失远高于普通货；易碎/冷链——颠簸与急刹直接扣货损；
  重载超限——转弯半径大、下坡必须靠缓速器/发动机制动；液体——刹车要线性；
  活畜——急刹会伤到动物；等等。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 家族 → (中文标签, 建议, 关键词)
# 关键词同时覆盖 cargo_id（英文）与常见中英文显示名
FAMILIES: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "hazmat": ("危险品", "出事罚款和货损都比普通货狠，稳住油门、远离前车，别急刹", (
        "explosive", "nitrocel", "chemical", "benzene", "ammonia", "chlorine",
        "propane", "petrol", "diesel", "fuel", "acid", "argon", "nitrogen",
        "magnesium", "sodium", "hydro_sulfate", "uranium", "acetylen",
        "危险", "化学", "燃油", "汽油", "炸药", "易燃",
    )),
    "heavy": ("重载/超限", "转弯半径比平时大得多、下坡一定挂缓速器或低档，别靠刹车硬拖", (
        "excavator", "digger", "bulldozer", "tractor", "helicopter", "yacht",
        "locomotive", "wind_turbine", "turbine", "big_tech", "high_voltage",
        "modular_house", "concrete_beam", "cable_reel", "jet_engine",
        "超限", "重载", "大型", "工程", "直升机", "游艇", "机车",
    )),
    "reefer": ("冷链", "温度相关，尽量别在服务区停太久；迟到和货损都扣得凶", (
        "frozen", "ice_cream", "refrigerated", "vaccine", "medical", "curd",
        "yoghurt", "dairy", "coconut_milk", "condensed_milk",
        "冷藏", "冷冻", "疫苗", "医药", "鲜",
    )),
    "fragile": ("易碎", "一点点颠簸和急刹都会掉完好率，跟车距离留足", (
        "glass", "tableware", "porcelain", "electronics", "computer",
        "玻璃", "易碎", "陶瓷", "电子",
    )),
    "livestock": ("活畜", "急刹会让动物受伤，起步刹车都要柔", (
        "livestock", "cattle", "pig", "poultry", "chicken",
        "牲畜", "活畜", "牛", "猪", "家禽",
    )),
    "liquid": ("液体", "罐里会晃，刹车要线性、别一脚闷死", (
        "milk", "water", "oil", "molasses", "ketchup", "juice",
        "牛奶", "液体", "罐",
    )),
    "bulk": ("散货", "载重大、重心低，注意坡道起步和刹车距离", (
        "cement", "sand", "gravel", "coal", "ore", "limestone", "grain",
        "oats", "rice", "sugar", "fertilizer", "stone_dust", "chips",
        "scrap", "bark", "shaving",
        "水泥", "砂", "煤", "矿石", "粮食", "废料", "散",
    )),
    "construction": ("建材", "超高/超宽，注意隧道限高和路肩", (
        "brick", "concrete", "wall_panel", "pipe", "metal", "steel",
        "lumber", "logs", "wood", "beam",
        "砖", "混凝土", "钢管", "木材", "建材",
    )),
    "machinery": ("机械", "重心偏高，过弯提前减速", (
        "machinery", "forklift", "car_part", "truck",
        "机械", "叉车", "零件",
    )),
    "food": ("食品", "注意时效，别在路上拖太久", (
        "food", "beverage", "flour", "bean", "nut", "peanut", "apple",
        "vegetable", "tomato", "cucumber", "potato", "fruit",
        "食品", "饮料", "水果", "蔬菜",
    )),
}

# 家族 → 陪聊倾向（哪个陪玩维度更该说话）
FAMILY_BIAS = {
    "hazmat": "care", "fragile": "cargo", "reefer": "progress",
    "heavy": "truck", "livestock": "care", "construction": "road",
}

# 重量阈值（吨）：超过即按重载看待（ETS2 里普通半挂货重通常 5~25 t，
# 特种/超限可到 40 t 以上；用低阈值 + 家族双重判定，避免单看重量误判）
HEAVY_MASS_T = 25.0


class CargoBook:
    """货物分类器（无状态，输入即遥测字段）。"""

    def classify(self, cargo_id: str = "", cargo_name: str = "") -> Optional[Dict[str, str]]:
        """返回 {key,label,tip}；认不出来返回 None（不乱猜）。"""
        hay_id = (cargo_id or "").lower()
        hay_name = (cargo_name or "").lower()
        # 先按家族顺序匹配 id（更稳），再匹配显示名
        for hay in (hay_id, hay_name):
            if not hay:
                continue
            for key, (label, tip, keywords) in FAMILIES.items():
                if any(k in hay for k in keywords):
                    return {"key": key, "label": label, "tip": tip}
        return None

    def label(self, cargo_id: str = "", cargo_name: str = "") -> str:
        hit = self.classify(cargo_id, cargo_name)
        return hit["label"] if hit else ""

    def tip(self, cargo_id: str = "", cargo_name: str = "") -> str:
        hit = self.classify(cargo_id, cargo_name)
        return hit["tip"] if hit else ""

    def bias(self, cargo_id: str = "", cargo_name: str = "") -> str:
        hit = self.classify(cargo_id, cargo_name)
        return FAMILY_BIAS.get(hit["key"], "") if hit else ""

    def describe(self, cargo_id: str = "", cargo_name: str = "",
                 mass_t: float = 0.0) -> Tuple[str, str]:
        """返回 (这趟货怎么描述的, 玩法建议)。认不出又不重就返回空。"""
        hit = self.classify(cargo_id, cargo_name)
        heavy = mass_t >= HEAVY_MASS_T
        if hit and heavy and hit["key"] != "heavy":
            desc = f"{hit['label']}·重载（约 {mass_t:.0f} t）"
            tip = f"{hit['tip']}；另外这趟载重在 {mass_t:.0f} t 左右，刹车距离明显变长"
            return desc, tip
        if hit:
            return hit["label"], hit["tip"]
        if heavy:
            return (f"重载（约 {mass_t:.0f} t）",
                    "载重大，注意坡道起步、下坡用缓速器、跟车距离加倍")
        if mass_t > 0:
            return f"普通货（约 {mass_t:.1f} t）", ""
        return "", ""

    def families(self) -> List[str]:
        return list(FAMILIES.keys())
