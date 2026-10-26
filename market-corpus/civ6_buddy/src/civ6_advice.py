"""civ6_buddy 陪玩建议生成（纯逻辑，无宿主依赖，可独立测试）。

从局面数据（civ6_parser.parse_save 输出）→ 规则化中文建议要点。
"""

try:
    from . import civ6_data
except ImportError:  # 包外直接加载（测试/独立脚本）
    import civ6_data  # type: ignore


def advice_lines(sit: dict, topic: str) -> list[str]:
    """局面 → 规则化建议。基于玩家文明特性/对手/城邦/回合阶段，模板式输出。"""
    out: list[str] = []
    humans = sit.get("humans") or []
    ais = sit.get("ais") or []
    css = sit.get("city_states") or []
    turn = sit.get("game_turn") or 0
    t = (topic or "").strip()

    my = humans[0] if humans else None
    ua = civ6_data.civ_ua(my["id"]) if my else ""

    phase = "远古-古典（前50回合）" if turn <= 50 else ("中世纪（50-100）" if turn <= 100 else "后期")

    if t and t not in ("开局", "科技", "建城", "战争", "胜利", "外交", "综合"):
        out.append(f"主题「{t}」未收录，以下为综合建议：")

    # 通用框架
    out.append(f"【阶段】{phase}，第 {turn} 回合。")
    if my:
        out.append(f"【你的文明】{my['name']}（{my['leader']}）：{ua or '特性资料待补充'}")

    if "战争" in t or "军事" in t:
        if my and "征服" in ua or (my and "军事" in (ua or "")):
            out.append("· 你的文明适合军事扩张：优先造兵、发展军事区，考虑早战宣战最近的邻国。")
        else:
            out.append("· 当前以防御为主：每城至少留 1-2 单位驻防，遇到军力强的邻居保持友好。")
        out.append(f"· 已知对手 {len(ais)} 个：{'、'.join(a['name'] for a in ais)}。若邻居武力强（如祖鲁/斯基泰），别主动挑衅。")
    elif "科技" in t or "科技" in str(topic):
        out.append("· 科技线参考：远古优先 采矿→陶器→写作（学院前置）；古典补 青铜器→建筑学。")
        out.append("· 有学院加成倾向（如韩国/巴比伦）的文明优先铺学院；否则按胜利路线定科技。")
    elif "建城" in t or "铺城" in t:
        out.append("· 铺城参考：前 3 城优先抢占奢侈资源/河流/丘陵产能；城距 4-6 格避免互相挤占。")
        out.append("· 抢地盘要快：AI 也会扩张，看到好位置别犹豫。")
    elif "胜利" in t or "胜利路线" in t:
        if my and "文化" in ua:
            out.append("· 你的文明倾向文化胜利：多建剧院广场、抢巨作、后期补旅游业绩。")
        elif my and "宗教" in ua:
            out.append("· 你的文明倾向宗教胜利：抢万神殿、建圣坛传教、防止敌对宗教入侵。")
        else:
            out.append("· 参考三条路线：科技（学院+后期太空）/文化（剧院+巨作）/征服（军事滚雪球）。当前回合选一条主线走。")
    elif "外交" in t:
        out.append(f"· 城邦（{len(css)} 个）是外交资源：早期派使者，优先结盟对本文明加成最大的类型。")
        out.append("· AI 性格各异，战争狂邻居（如希腊戈尔戈）注意军备威慑，和平型可发展贸易。")
    else:  # 开局 / 留空 / 未知主题 → 综合开局建议（与"未收录→综合"承诺一致）
        out.append("· 开局三件事：探图（侦察兵/勇士向外探视野）、抢好地块（奢侈品+河流）、攒产能铺首都。")
        out.append(f"· 城邦有 {len(css)} 个：派使者结盟最有利的（参考各城邦类型）。")
        out.append("· 蛮族营地会刷：看到营地就清，别让蛮族骚扰商路。")

    out.append("· （以上为规则化参考，具体请结合 YUI 与你的实际情况一起判断。）")
    return out
