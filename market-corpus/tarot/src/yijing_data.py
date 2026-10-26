"""梅花易数核心算法与数据（依邵雍《梅花易数》体例实现）。

以数起卦：
- 上卦 = num1 % 8（余 0 作 8）
- 下卦 = num2 % 8（余 0 作 8）
- 动爻 = (num1 + num2) % 6（余 0 作 6）
- 本卦动爻所在经卦为「用卦」，静者为「体卦」，以体用生克断吉凶
- 变卦：动爻阴阳互变后所得之卦
- 互卦：本卦二三四爻为下、三四五爻为上
"""
from __future__ import annotations

from dataclasses import dataclass

# 先天八卦数：乾一 兑二 离三 震四 巽五 坎六 艮七 坤八
_TRIGRAM_ORDER = ["乾", "兑", "离", "震", "巽", "坎", "艮", "坤"]

# 三爻（自下而上）：1 阳 0 阴
TRIGRAMS: dict[str, dict] = {
    "乾": {"lines": (1, 1, 1), "element": "金", "nature": "天", "image": "刚健中正，自强不息"},
    "兑": {"lines": (1, 1, 0), "element": "金", "nature": "泽", "image": "和悦相亲，言语沟通"},
    "离": {"lines": (1, 0, 1), "element": "火", "nature": "火", "image": "光明附丽，文明以察"},
    "震": {"lines": (1, 0, 0), "element": "木", "nature": "雷", "image": "奋发震动，雷厉风行"},
    "巽": {"lines": (0, 1, 1), "element": "木", "nature": "风", "image": "柔顺深入，无孔不入"},
    "坎": {"lines": (0, 1, 0), "element": "水", "nature": "水", "image": "险陷流动，外柔内刚"},
    "艮": {"lines": (0, 0, 1), "element": "土", "nature": "山", "image": "静止稳重，知止不殆"},
    "坤": {"lines": (0, 0, 0), "element": "土", "nature": "地", "image": "厚德载物，柔顺包容"},
}

_LINES_TO_TRIGRAM = {tuple(v["lines"]): k for k, v in TRIGRAMS.items()}

# 六十四卦：(上卦, 下卦) -> (卦名, 卦辞, 吉凶)
HEXAGRAMS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("乾", "乾"): ("乾为天", "元亨利贞。天行健，君子以自强不息。", "大吉"),
    ("乾", "兑"): ("天泽履", "履虎尾，不咥人，亨。如履薄冰而终得平安。", "吉"),
    ("乾", "离"): ("天火同人", "同人于野，亨。与人同心，其利断金。", "吉"),
    ("乾", "震"): ("天雷无妄", "元亨利贞。不存妄念，顺其自然。", "平吉"),
    ("乾", "巽"): ("天风姤", "女壮，勿用取女。不期而遇，慎防意外之缘。", "平凶"),
    ("乾", "坎"): ("天水讼", "有孚窒惕，中吉，终凶。争执宜解不宜结。", "凶"),
    ("乾", "艮"): ("天山遁", "亨，小利贞。宜退守避让，以待时机。", "平"),
    ("乾", "坤"): ("天地否", "否之匪人。天地不交，诸事暂滞。", "凶"),
    ("兑", "乾"): ("泽天夬", "扬于王庭。决断果敢，除恶务尽。", "吉"),
    ("兑", "兑"): ("兑为泽", "亨利贞。朋友讲习，和悦相济。", "吉"),
    ("兑", "离"): ("泽火革", "己日乃孚。顺天应人，革故鼎新。", "吉"),
    ("兑", "震"): ("泽雷随", "元亨利贞，无咎。随时而动，择善而从。", "吉"),
    ("兑", "巽"): ("泽风大过", "栋桡。负荷过重，宜减负担。", "平凶"),
    ("兑", "坎"): ("泽水困", "亨，贞大人吉。困而不失其所亨，唯君子能之。", "平"),
    ("兑", "艮"): ("泽山咸", "亨利贞，取女吉。两心相感，情谊自然。", "大吉"),
    ("兑", "坤"): ("泽地萃", "亨，王假有庙。汇聚人心，共襄盛举。", "吉"),
    ("离", "乾"): ("火天大有", "元亨。如日中天，所有丰盈。", "大吉"),
    ("离", "兑"): ("火泽睽", "小事吉。意见相左，小事可为。", "平"),
    ("离", "离"): ("离为火", "利贞，亨。重明以丽，光明相继。", "吉"),
    ("离", "震"): ("火雷噬嗑", "亨，利用狱。咬合障碍，明断是非。", "平吉"),
    ("离", "巽"): ("火风鼎", "元吉，亨。鼎新革面，烹饪成新。", "大吉"),
    ("离", "坎"): ("火水未济", "亨。小狐汔济，事将成而未成。", "平"),
    ("离", "艮"): ("火山旅", "小亨，旅贞吉。行旅在外，谨慎自持。", "平"),
    ("离", "坤"): ("火地晋", "康侯用锡马蕃庶。如日方升，进取得位。", "吉"),
    ("震", "乾"): ("雷天大壮", "利贞。声势壮大，戒骄戒躁。", "吉"),
    ("震", "兑"): ("雷泽归妹", "征凶，无攸利。情有所偏，宜慎始终。", "凶"),
    ("震", "离"): ("雷火丰", "亨，王假之，勿忧。丰盛盈满，宜明宜照。", "吉"),
    ("震", "震"): ("震为雷", "亨。震来虩虩，笑言哑哑。惊后有喜。", "平吉"),
    ("震", "巽"): ("雷风恒", "亨，无咎，利贞。恒久之道，守常不变。", "吉"),
    ("震", "坎"): ("雷水解", "利西南。险难解除，冰雪消融。", "吉"),
    ("震", "艮"): ("雷山小过", "亨利贞，可小事。小事可为，大事宜缓。", "平"),
    ("震", "坤"): ("雷地豫", "利建侯行师。安乐和顺，众心归附。", "吉"),
    ("巽", "乾"): ("风天小畜", "亨。密云不雨，蓄而未发。", "平"),
    ("巽", "兑"): ("风泽中孚", "豚鱼吉，利涉大川。诚信感物，虽微亦应。", "大吉"),
    ("巽", "离"): ("风火家人", "利女贞。宜室宜家，各正其位。", "吉"),
    ("巽", "震"): ("风雷益", "利有攸往，利涉大川。损上益下，进取有成。", "大吉"),
    ("巽", "巽"): ("巽为风", "小亨，利有攸往。申命行事，因势利导。", "平吉"),
    ("巽", "坎"): ("风水涣", "亨，王假有庙。涣散凝合，释怀前行。", "平吉"),
    ("巽", "艮"): ("风山渐", "女归吉，利贞。循序渐进，不疾不徐。", "吉"),
    ("巽", "坤"): ("风地观", "盥而不荐，有孚颙若。静观其变，以察时势。", "平"),
    ("坎", "乾"): ("水天需", "有孚，光亨，贞吉。云上于天，待时而动。", "吉"),
    ("坎", "兑"): ("水泽节", "亨。苦节不可贞。节制有度，过犹不及。", "平"),
    ("坎", "离"): ("水火既济", "亨小，利贞。初吉终乱，功成当慎。", "平凶"),
    ("坎", "震"): ("水雷屯", "元亨利贞，勿用有攸往。草木初生，艰难起步。", "平凶"),
    ("坎", "巽"): ("水风井", "改邑不改井。养人无穷，守正自持。", "平"),
    ("坎", "坎"): ("坎为水", "习坎，有孚，维心亨。重险当前，行险而不失信。", "凶"),
    ("坎", "艮"): ("水山蹇", "利西南，不利东北。前路险阻，宜反身修德。", "凶"),
    ("坎", "坤"): ("水地比", "吉，原筮元永贞。亲比相辅，众星拱月。", "吉"),
    ("艮", "乾"): ("山天大畜", "利贞，不家食吉。厚积蓄德，大有作为。", "吉"),
    ("艮", "兑"): ("山泽损", "有孚，元吉，无咎。损下益上，先失后得。", "平吉"),
    ("艮", "离"): ("山火贲", "亨，小利有攸往。文饰光明，小事可成。", "平吉"),
    ("艮", "震"): ("山雷颐", "贞吉，观颐。自求口实，养正修身。", "平"),
    ("艮", "巽"): ("山风蛊", "元亨，利涉大川。整饬积弊，拨乱反正。", "平凶"),
    ("艮", "坎"): ("山水蒙", "亨。匪我求童蒙，童蒙求我。启蒙养正，虚心受教。", "平"),
    ("艮", "艮"): ("艮为山", "艮其背，不获其身。时止则止，时行则行。", "平"),
    ("艮", "坤"): ("山地剥", "不利有攸往。根基动摇，宜静守待变。", "凶"),
    ("坤", "乾"): ("地天泰", "小往大来，吉亨。天地交泰，万物通达。", "大吉"),
    ("坤", "兑"): ("地泽临", "元亨利贞。居高临下，教思无穷。", "吉"),
    ("坤", "离"): ("地火明夷", "利艰贞。光明入地，韬光养晦。", "凶"),
    ("坤", "震"): ("地雷复", "亨。出入无疾，朋来无咎。一阳来复，否极泰来。", "吉"),
    ("坤", "巽"): ("地风升", "元亨，用见大人。积小成大，步步上升。", "吉"),
    ("坤", "坎"): ("地水师", "贞，丈人吉。行险而顺，以正治众。", "平"),
    ("坤", "艮"): ("地山谦", "亨，君子有终。谦谦君子，卑以自牧。", "大吉"),
    ("坤", "坤"): ("坤为地", "元亨，利牝马之贞。厚德载物，含弘光大。", "吉"),
}

# 五行生克
_GENERATE = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
_RESTRAIN = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}


@dataclass
class PlumReading:
    num1: int
    num2: int
    upper: str
    lower: str
    moving_line: int          # 1-6，自下而上
    body_trigram: str         # 体卦
    use_trigram: str          # 用卦
    hexagram_name: str
    hexagram_text: str
    fortune: str              # 吉凶
    changed_name: str
    changed_text: str
    changed_fortune: str
    mutual_name: str
    mutual_text: str
    mutual_fortune: str
    changed_upper: str        # 变卦上卦
    changed_lower: str        # 变卦下卦
    mutual_upper: str         # 互卦上卦
    mutual_lower: str         # 互卦下卦
    relation: str             # 体用关系描述
    verdict: str              # 总断


def _flip_line(lines: tuple[int, int, int], idx: int) -> tuple[int, int, int]:
    """翻转指定爻（idx 0-2，自下而上）"""
    lst = list(lines)
    lst[idx] = 1 - lst[idx]
    return tuple(lst)


def _hexagram(upper: str, lower: str) -> tuple[str, str, str]:
    return HEXAGRAMS.get((upper, lower), (f"{upper}{lower}合象", "卦象未载，以象推之。", "平"))


def _mutual_hexagram(upper: str, lower: str) -> tuple[str, str]:
    """互卦：二三四爻为下卦，三四五爻为上卦（爻自下而上共六爻）"""
    six = list(TRIGRAMS[lower]["lines"]) + list(TRIGRAMS[upper]["lines"])
    m_lower = _LINES_TO_TRIGRAM[tuple(six[1:4])]
    m_upper = _LINES_TO_TRIGRAM[tuple(six[2:5])]
    return m_upper, m_lower


def _changed_trigrams(upper: str, lower: str, moving: int) -> tuple[str, str]:
    """动爻互变后的 (新上卦, 新下卦)"""
    if moving <= 3:
        return upper, _LINES_TO_TRIGRAM[_flip_line(TRIGRAMS[lower]["lines"], moving - 1)]
    return _LINES_TO_TRIGRAM[_flip_line(TRIGRAMS[upper]["lines"], moving - 4)], lower


def _body_use_relation(body: str, use: str) -> tuple[str, str]:
    """返回 (关系描述, 吉凶倾向)"""
    be, ue = TRIGRAMS[body]["element"], TRIGRAMS[use]["element"]
    if be == ue:
        return f"体用比和（皆属{be}），内外相济，事可顺遂。", "吉"
    if _GENERATE[ue] == be:
        return f"用卦{ue}生体卦{be}，外力相助，得贵人扶持。", "大吉"
    if _GENERATE[be] == ue:
        return f"体卦{be}生用卦{ue}，泄气于外，付出多而收获缓。", "小凶"
    if _RESTRAIN[be] == ue:
        return f"体卦{be}克用卦{ue}，我能制事，所求可得，然须费力。", "平吉"
    return f"用卦{ue}克体卦{be}，外力相侵，诸事受阻，宜守不宜进。", "凶"


_FORTUNE_ADVICE = {
    "大吉": "此卦大吉，所求之事顺遂可期。顺势而为，把握良机，同时心存谦敬，福泽绵长。",
    "吉": "此卦为吉，事有可为。稳扎稳打，不骄不躁，自然水到渠成。",
    "平吉": "此卦平中带吉，小事可成，大事须缓。量力而行，先固根本。",
    "平": "此卦平稳，无大得失。宜守常度，静观其变，不宜妄动求新。",
    "平凶": "此卦略有阻滞，所求之事恐有反复。谨慎应对，防微杜渐，可减其咎。",
    "小凶": "此卦欠佳，气运外泄。宜收敛锋芒，减少投入，养精蓄锐以待来日。",
    "凶": "此卦不利，险阻当前。宜静守退避，勿强求妄进，修德自省，待时而动。",
}

_POSITION_MEANING = {
    1: "初爻动：事在萌芽，根基初立，宜审慎起步。",
    2: "二爻动：事在中途，内有变数，守正可得中道。",
    3: "三爻动：下卦之极，进退交接之际，谨防反复。",
    4: "四爻动：上卦之初，由内而外，新境将开。",
    5: "五爻动：居尊得中，事态显达，主事者之变。",
    6: "上爻动：事至终极，物极必反，慎防过犹不及。",
}


def divine(num1: int, num2: int) -> PlumReading:
    """以两数起卦，返回完整梅花易数解读"""
    n1, n2 = abs(int(num1)) or 8, abs(int(num2)) or 8
    upper_idx = n1 % 8 or 8
    lower_idx = n2 % 8 or 8
    upper = _TRIGRAM_ORDER[upper_idx - 1]
    lower = _TRIGRAM_ORDER[lower_idx - 1]
    moving = (n1 + n2) % 6 or 6

    # 体用：动爻所在卦为用
    if moving <= 3:
        use, body = lower, upper
    else:
        use, body = upper, lower

    # 变卦：动爻互变
    new_upper, new_lower = _changed_trigrams(upper, lower, moving)
    ch_name, ch_text, ch_fortune = _hexagram(new_upper, new_lower)

    m_upper, m_lower = _mutual_hexagram(upper, lower)
    mu_name, mu_text, mu_fortune = _hexagram(m_upper, m_lower)

    name, text, fortune = _hexagram(upper, lower)
    relation_desc, _ = _body_use_relation(body, use)

    # 综合判断：本卦为主，变卦为终
    rank = ["凶", "小凶", "平凶", "平", "平吉", "吉", "大吉"]
    main_r = rank.index(fortune)
    ch_r = rank.index(ch_fortune)
    verdict = (
        "本卦主当前之势，变卦示事态之终。"
        "若本卦吉而变卦亦吉，则善始善终；"
        if main_r >= 4 and ch_r >= 4 else
        f"本卦{fortune}、变卦{ch_fortune}，始虽{'顺' if main_r >= 4 else '滞'}而终{'成' if ch_r >= 4 else '变'}，行事宜察其终。"
        if main_r >= 4 or ch_r >= 4 else
        "本卦变卦俱见险象，近时诸事宜缓，守正待时，勿轻举妄动。"
    )

    return PlumReading(
        num1=n1, num2=n2, upper=upper, lower=lower, moving_line=moving,
        body_trigram=body, use_trigram=use,
        hexagram_name=name, hexagram_text=text, fortune=fortune,
        changed_name=ch_name, changed_text=ch_text, changed_fortune=ch_fortune,
        mutual_name=mu_name, mutual_text=mu_text, mutual_fortune=mu_fortune,
        changed_upper=new_upper, changed_lower=new_lower,
        mutual_upper=m_upper, mutual_lower=m_lower,
        relation=relation_desc, verdict=verdict,
    )


def moving_line_meaning(moving: int) -> str:
    return _POSITION_MEANING.get(moving, "")


def fortune_advice(fortune: str) -> str:
    return _FORTUNE_ADVICE.get(fortune, _FORTUNE_ADVICE["平"])
