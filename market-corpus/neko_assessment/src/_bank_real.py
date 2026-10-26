"""真实量表题库（照搬公开工具的原题，不自己编）。

来源与许可
----------
1. **大五人格 100 题** —— IPIP Big-Five Factor Markers（Goldberg）
   来源：https://ipip.ori.org/newBigFive5broadKey.htm
   许可：**公共领域（public domain）**，可自由使用。每条取该因子 20 题版本（每因子 20 题 × 5 = 100 题），
   `+` 为正向计分、`-` 为反向计分，完全按原键值。中文为本插件自译。

2. **职业兴趣 60 题** —— O*NET Interest Profiler Short Form（美国劳工部）
   来源：https://www.onetcenter.org/dl_tools/ipsf/Interest_Profiler.pdf
   许可：**美国政府作品，公共领域**。六个兴趣区各 10 条，共 60 条原题。中文为本插件自译。

3. **16 型人格 32 题** —— Open Extended Jungian Type Scales 1.2（Eric Jorgenson）
   来源：https://openpsychometrics.org/tests/OJTS/development/OEJTS1.2.pdf
   许可：**CC BY-NC-SA 4.0（署名 · 非商业 · 相同方式共享）** → 仅供个人/非商业使用；
   本插件标注出处，未与 MBTI 有任何关联。原量表为「双极五点」（如「爱列清单 ↔ 靠脑子记」），
   这里改成同一对的陈述句 + 五点同意度，计分按原量表的正负号。
"""

from __future__ import annotations


def _ipip(dim, rows):
    """rows: [(中文条目, 是否反向)]（英文原句写在行尾注释里，便于逐条核对）。"""
    return [{"text": text, "dim": dim, "reverse": bool(rev)} for text, rev in rows]


# ════════════════════════════════════════════════════════════
# ① IPIP 大五人格（每因子 20 题，公共领域）
# ════════════════════════════════════════════════════════════
IPIP_BIG5 = []
IPIP_BIG5 += _ipip("E", [
    ("我是聚会上的活跃分子。", False),          # Am the life of the party.
    ("我在人群里很自在。", False),              # Feel comfortable around people.
    ("我常主动开启对话。", False),              # Start conversations.
    ("在聚会上我会跟很多人聊天。", False),      # Talk to a lot of different people at parties.
    ("我不介意成为大家的焦点。", False),        # Don't mind being the center of attention.
    ("我很容易交到朋友。", False),              # Make friends easily.
    ("我习惯把事情揽过来牵头。", False),        # Take charge.
    ("我知道怎么让别人对我感兴趣。", False),    # Know how to captivate people.
    ("我和人相处时很放松。", False),            # Feel at ease with people.
    ("我在社交场合挺有一套。", False),          # Am skilled in handling social situations.
    ("我不太说话。", True),                     # Don't talk a lot.
    ("我习惯待在人群的后面。", True),           # Keep in the background.
    ("我没什么可说的。", True),                 # Have little to say.
    ("我不喜欢别人把注意力放在我身上。", True),  # Don't like to draw attention to myself.
    ("我在陌生人面前很安静。", True),           # Am quiet around strangers.
    ("我很难主动接近别人。", True),             # Find it difficult to approach others.
    ("我常觉得和别人在一起不自在。", True),     # Often feel uncomfortable around others.
    ("我会把自己的感受憋着。", True),           # Bottle up my feelings.
    ("我是很注重隐私的人。", True),             # Am a very private person.
    ("我习惯等别人先出头。", True),             # Wait for others to lead the way.
])
IPIP_BIG5 += _ipip("A", [
    ("我对人感兴趣。", False),                  # Am interested in people.
    ("我能体会别人的感受。", False),            # Sympathize with others' feelings.
    ("我心比较软。", False),                    # Have a soft heart.
    ("我愿意为别人腾出时间。", False),          # Take time out for others.
    ("我能感受到别人的情绪。", False),          # Feel others' emotions.
    ("我让别人觉得自在。", False),              # Make people feel at ease.
    ("我会关心别人过得好不好。", False),        # Inquire about others' well-being.
    ("我知道怎么安慰人。", False),              # Know how to comfort others.
    ("我很喜欢小孩。", False),                  # Love children.
    ("我跟几乎所有人都处得来。", False),        # Am on good terms with nearly everyone.
    ("我总是替别人说好话。", False),            # Have a good word for everyone.
    ("我会表达感谢。", False),                  # Show my gratitude.
    ("我常先想别人。", False),                  # Think of others first.
    ("我很乐意帮别人。", False),                # Love to help others.
    ("我会挖苦别人。", True),                   # Insult people.
    ("我对别人的问题没兴趣。", True),           # Am not interested in other people's problems.
    ("我不太在意别人。", True),                 # Feel little concern for others.
    ("我其实对别人没什么兴趣。", True),         # Am not really interested in others.
    ("我很难让人走近。", True),                 # Am hard to get to know.
    ("我对别人的感受很无感。", True),           # Am indifferent to the feelings of others.
])
IPIP_BIG5 += _ipip("C", [
    ("我总是做好准备。", False),                # Am always prepared.
    ("我注意细节。", False),                    # Pay attention to details.
    ("该做的家务我马上做。", False),            # Get chores done right away.
    ("我喜欢整齐。", False),                    # Like order.
    ("我会按计划表来。", False),                # Follow a schedule.
    ("我做事要求精确。", False),                # Am exacting in my work.
    ("我按计划做事。", False),                  # Do things according to a plan.
    ("不做完美我不罢手。", False),              # Continue until everything is perfect.
    ("我定了计划就会坚持。", False),            # Make plans and stick to them.
    ("我喜欢规律和秩序。", False),              # Love order and regularity.
    ("我喜欢把东西收拾干净。", False),          # Like to tidy up.
    ("我随手乱放东西。", True),                 # Leave my belongings around.
    ("我常把事情弄得一团糟。", True),           # Make a mess of things.
    ("我常忘了把东西放回原处。", True),         # Often forget to put things back in their proper place.
    ("我逃避该做的事。", True),                 # Shirk my duties.
    ("我会忽略自己的职责。", True),             # Neglect my duties.
    ("我浪费时间。", True),                     # Waste my time.
    ("我做事只做一半。", True),                 # Do things in a half-way manner.
    ("我很难进入工作状态。", True),             # Find it difficult to get down to work.
    ("我的房间总是很乱。", True),               # Leave a mess in my room.
])
IPIP_BIG5 += _ipip("N", [
    ("我大部分时候都很放松。", True),           # Am relaxed most of the time.
    ("我很少感到低落。", True),                 # Seldom feel blue.
    ("我很少被事情打扰。", True),               # Am not easily bothered by things.
    ("我很少被惹到。", True),                   # Rarely get irritated.
    ("我很少发脾气。", True),                   # Seldom get mad.
    ("我很容易紧张。", False),                  # Get stressed out easily.
    ("我常常担心事情。", False),                # Worry about things.
    ("我很容易心烦。", False),                  # Am easily disturbed.
    ("我很容易不高兴。", False),                # Get upset easily.
    ("我的情绪变化很大。", False),              # Change my mood a lot.
    ("我的心情经常起伏。", False),              # Have frequent mood swings.
    ("我容易被惹火。", False),                  # Get irritated easily.
    ("我常常情绪低落。", False),                # Often feel blue.
    ("我容易生气。", False),                    # Get angry easily.
    ("我容易恐慌。", False),                    # Panic easily.
    ("我容易觉得被威胁。", False),              # Feel threatened easily.
    ("我常被情绪淹没。", False),                # Get overwhelmed by emotions.
    ("我容易觉得被冒犯。", False),              # Take offense easily.
    ("我常陷在自己的问题里。", False),          # Get caught up in my problems.
    ("我常抱怨。", False),                      # Grumble about things.
])
IPIP_BIG5 += _ipip("O", [
    ("我词汇量丰富。", False),                  # Have a rich vocabulary.
    ("我想象力丰富。", False),                  # Have a vivid imagination.
    ("我有很多好点子。", False),                # Have excellent ideas.
    ("我理解东西很快。", False),                # Am quick to understand things.
    ("我会用比较难的词。", False),              # Use difficult words.
    ("我会花时间反思。", False),                # Spend time reflecting on things.
    ("我脑子里有很多想法。", False),            # Am full of ideas.
    ("我能把话题带到更深一层。", False),        # Carry the conversation to a higher level.
    ("我上手新东西很快。", False),              # Catch on to things quickly.
    ("我能处理大量信息。", False),              # Can handle a lot of information.
    ("我喜欢想新的做事方式。", False),          # Love to think up new ways of doing things.
    ("我喜欢读有挑战的材料。", False),          # Love to read challenging material.
    ("我很多方面都做得不错。", False),          # Am good at many things.
    ("我理解抽象概念有点吃力。", True),         # Have difficulty understanding abstract ideas.
    ("我对抽象的想法没兴趣。", True),           # Am not interested in abstract ideas.
    ("我没什么想象力。", True),                 # Do not have a good imagination.
    ("我会避开想法复杂的人。", True),           # Try to avoid complex people.
    ("我很难想象出画面。", True),               # Have difficulty imagining things.
    ("我会避开难读的材料。", True),             # Avoid difficult reading material.
    ("我不会深入钻研某个主题。", True),         # Will not probe deeply into a subject.
])


# ════════════════════════════════════════════════════════════
# ② O*NET 职业兴趣 60 题（美国劳工部，公共领域）—— 题目为「你愿意做这件事吗」
# ════════════════════════════════════════════════════════════
ONET_RIASEC = []
ONET_RIASEC += _ipip("R", [
    ("做厨房橱柜。", False), ("砌砖或铺瓷砖。", False), ("修家用电器。", False),
    ("在孵化场养鱼。", False), ("组装电子零件。", False), ("开卡车送货上门。", False),
    ("发货前检测零件质量。", False), ("修锁和装锁。", False), ("开动机器生产产品。", False),
    ("参与扑灭森林大火。", False),
])
ONET_RIASEC += _ipip("I", [
    ("研发一种新药。", False), ("研究减少水污染的办法。", False), ("做化学实验。", False),
    ("研究行星的运行。", False), ("用显微镜检查血样。", False), ("调查火灾的起因。", False),
    ("研究更准确预报天气的方法。", False), ("在生物实验室工作。", False),
    ("发明糖的替代品。", False), ("做化验来识别疾病。", False),
])
ONET_RIASEC += _ipip("A", [
    ("写书或写剧本。", False), ("演奏乐器。", False), ("作曲或编曲。", False),
    ("画画。", False), ("给电影做特效。", False), ("给话剧画布景。", False),
    ("给影视剧写剧本。", False), ("跳爵士舞或踢踏舞。", False), ("在乐队里唱歌。", False),
    ("剪辑影片。", False),
])
ONET_RIASEC += _ipip("S", [
    ("教别人做一套健身动作。", False), ("帮人处理个人或情绪上的问题。", False),
    ("给人做职业指导。", False), ("做康复治疗。", False), ("在公益组织做志愿工作。", False),
    ("教孩子打球。", False), ("教聋人学手语。", False), ("协助带一次团体治疗。", False),
    ("在托儿所照顾孩子。", False), ("教一个高中班。", False),
])
ONET_RIASEC += _ipip("E", [
    ("买卖股票和债券。", False), ("管一家零售店。", False), ("经营美容院或理发店。", False),
    ("管理大公司里的一个部门。", False), ("自己创业。", False), ("谈商业合同。", False),
    ("在诉讼中代表客户。", False), ("推广一条新的服装线。", False), ("在百货公司卖东西。", False),
    ("经营一家服装店。", False),
])
ONET_RIASEC += _ipip("C", [
    ("用软件做电子表格。", False), ("校对记录或表格。", False), ("在大型网络上批量装机。", False),
    ("使用计算器。", False), ("保管发货和收货记录。", False), ("核算员工工资。", False),
    ("用手持设备盘点库存。", False), ("记录房租收付。", False), ("保管库存记录。", False),
    ("给机构盖戳、分拣和分发邮件。", False),
])


# ════════════════════════════════════════════════════════════
# ③ OEJTS 1.2 十六型 32 题（CC BY-NC-SA 4.0）
#    原题为「双极五点」，这里改为单句 + 五点同意度；`reverse` 表示该句属于哪一端
# ════════════════════════════════════════════════════════════
OEJTS_32 = []
# 每对「双极描述」拆成两句陈述，各自归到对应的字母（不再用反向题表达另一极），
# 这样引擎的四组二分比较（E vs I …）才能拿到两侧分数。共 32 对 → 64 句。
OEJTS_PAIRS = [
    # (一端文字, 字母, 另一端文字, 字母)  —— 顺序按 OEJTS 原题
    ("我习惯把事情列成清单。", "J", "我更多靠脑子记，不太列清单。", "P"),          # Q1
    ("我对别人的话常持怀疑态度。", "T", "我更愿意相信别人。", "F"),                 # Q2
    ("一个人待久了我才会觉得无聊。", "E", "我需要独处的时间。", "I"),               # Q3
    ("我对现状比较能接受。", "S", "我总觉得现状不太对。", "N"),                     # Q4
    ("我房间通常收拾得很干净。", "J", "东西我随手放。", "P"),                       # Q5
    ("我更希望自己的脑子像个精密的机械系统。", "T", "我觉得被说「像个机器」是骂人。", "F"),  # Q6
    ("我精力比较旺盛。", "E", "我比较温和松弛。", "I"),                             # Q7
    ("我更喜欢多选题。", "S", "我更喜欢论述题。", "N"),                             # Q8
    ("我做事的顺序常是乱的。", "P", "我把东西收拾得有条理。", "J"),                 # Q9
    ("我很容易被伤到。", "F", "我不太容易被伤到。", "T"),                           # Q10
    ("我在团队里状态最好。", "E", "我一个人做事效率最高。", "I"),                   # Q11
    ("我更关注当下。", "S", "我更关注未来。", "N"),                                 # Q12
    ("我很早就把计划定好。", "J", "我常到最后一刻才安排。", "P"),                   # Q13
    ("我更想要别人的爱。", "F", "我更在意别人是否尊重我。", "T"),                   # Q14
    ("聚会会让我来劲。", "E", "聚会会让我很累。", "I"),                             # Q15
    ("我更想融入大家。", "S", "我更喜欢与众不同。", "N"),                           # Q16
    ("我倾向早早定下来做承诺。", "J", "我喜欢留着各种可能。", "P"),                 # Q17
    ("我更愿意修好人。", "F", "我更愿意修好东西。", "T"),                           # Q18
    ("我话比较多。", "E", "我更常听别人说。", "I"),                                 # Q19
    ("我讲一件事时会说发生了什么。", "S", "我讲一件事时会说它意味着什么。", "N"),     # Q20
    ("我会把手上的活马上做掉。", "J", "我常常拖着不做。", "P"),                     # Q21
    ("我会跟着心走。", "F", "我会跟着理性走。", "T"),                               # Q22
    ("我更愿意出门去玩。", "E", "我更愿意待在家里。", "I"),                         # Q23
    ("我更在意细节。", "S", "我更想看到整体图景。", "N"),                           # Q24
    ("我做事前会先准备。", "J", "我习惯临时发挥。", "P"),                           # Q25
    ("我认为对错该以同情为标准。", "F", "我认为对错该以公正为标准。", "T"),           # Q26
    ("大声喊远处的人对我来说不难。", "E", "我不太敢大声喊。", "I"),                 # Q27
    ("我更看重实证。", "S", "我更看重理论。", "N"),                                 # Q28
    ("我工作起来很投入。", "J", "我更愿意玩得尽兴。", "P"),                         # Q29
    ("我很看重情绪。", "F", "我对情绪不太自在。", "T"),                             # Q30
    ("我喜欢在别人面前表演。", "E", "我会避开当众发言。", "I"),                     # Q31
    ("我喜欢知道「谁、什么、什么时候」。", "S", "我喜欢知道「为什么」。", "N"),       # Q32
]
for _a, _la, _b, _lb in OEJTS_PAIRS:
    OEJTS_32.append({"text": _a, "dim": _la, "reverse": False})
    OEJTS_32.append({"text": _b, "dim": _lb, "reverse": False})

BANKS = {
    "big5": IPIP_BIG5,
    "riasec": ONET_RIASEC,
    "type16": OEJTS_32,
}

SOURCE = {
    "big5": "IPIP Big-Five Factor Markers（Goldberg）· ipip.ori.org · 公共领域 · 每因子 20 题共 100 题，中文自译",
    "riasec": "O*NET Interest Profiler Short Form（美国劳工部）· onetcenter.org · 美国政府作品（公共领域）· 六区各 10 题共 60 题，中文自译",
    "type16": "OEJTS（Open Extended Jungian Type Scales 1.2，Eric Jorgenson）· CC BY-NC-SA 4.0 · 非商业使用；与 MBTI 无关联 · 自译",
}

# 量表显示名（题库已换成真实工具原题后，名称要与之一致，不能再用「20 题 / 原创」的旧说法）
NAME = {
    "big5": "大五人格（IPIP）",
    "riasec": "职业兴趣（霍兰德）",
    "type16": "16 型人格（OEJTS）",
}
