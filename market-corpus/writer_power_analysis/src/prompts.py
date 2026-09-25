"""Full Ink Battles prompt loader for the writer power analysis plugin."""

from __future__ import annotations

import re
from pathlib import Path

PROMPT_FILES = (
    "system-prompt-01.md",
    "system-prompt-02.md",
    "system-prompt-03.md",
)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "system"

STANDARD_MODE_INSTRUCTION = """
## 评分模式：⚙️ 标准模式
- **说明**: 当前未启用任何特殊评分模式。请作为一名客观、中立的文学评论家，严格依据系统的全部16个维度定义和规则，对作品进行全面、均衡的评估。
"""

ANALYSIS_MODE_INSTRUCTIONS: dict[str, str] = {
    "初窥门径": """
## 评分模式：✅ 初窥门径
- **功能说明**: 专为评估未出版或影响力有限的新兴作品设计，旨在提供一个公平的、排除经典光环干扰的评价环境。
- **启用效果**:
  - **基础维度上限**: 所有14个基础维度的评分最高限制为SS级 (4.5分)，SSS级 (5分) 在此模式下禁用。
  - **经典性权重**: 【👑 经典性】维度的权重最高限制为C级 (1.1)。
  - **新锐性权重**: 【🧑‍🚀 新锐性】维度的权重不受此模式限制。
""",
    "严苛编辑": """
## 评分模式：✅ 严苛编辑
- **功能说明**: 模拟资深编辑或保守评论家的审稿标准，进行压力测试，旨在找出作品的短板和"最低可接受水平"。
- **启用效果**:
  - **评分标准收紧**: 对每个基础维度进行更严格的审视，每一维度的评分结果都会比标准模式低0.5至1分。
  - **杜绝鼓励分**: 彻底排除任何"鼓励性"评分，仅基于文本硬实力给出最审慎的判断。
""",
    "宽容读者": """
## 评分模式：✅ 宽容读者
- **功能说明**: 模拟充满善意的早期读者视角，侧重于发掘作品的闪光点与潜力，适用于为创作者提供积极反馈。
- **启用效果**:
  - **优点放大**: 若某维度表现出明显优点或潜力，允许在标准评分的基础上酌情上调0.5分。
  - **包容短板**: 对作品的缺点和不足之处采取更包容的态度，在评分时不过分苛责。
""",
    "文本法官": """
## 评分模式：✅ 文本法官
- **功能说明**: 采用严格的文本细读方法，要求所有评价均建立在可引证的文本证据之上。
- **启用效果**:
  - **强制引证**: 对每个维度的评分，必须明确引用或概括具体的文本段落、情节或语言用法作为核心依据。
  - **无证不评**: 若无法从文本中找到充分证据支撑某一评分等级，则必须选择更低的、有证据支持的等级。
""",
    "热血粉丝": """
## 评分模式：✅ 热血粉丝
- **功能说明**: 模拟对某一作品或风格抱有极高热情的粉丝视角，允许主观情感和个人偏好显著影响评分结果。
- **启用效果**:
  - **主观加权**: 可以对特别喜爱的维度给予极高评价，甚至突破标准模式下的感性上限。
  - **情感优先**: 评价将优先考虑情感共鸣和个人体验，而非纯粹客观技术分析。
""",
    "反现代主义者": """
## 评分模式：✅ 反现代主义者
- **功能说明**: 模拟偏爱古典叙事和传统结构的读者视角，降低对实验性和形式主义创新的评价权重。
- **启用效果**:
  - **维度降权**: 对【🌀 先锋性/实验性】、【🧠 结构复杂度】、【📚 引用张力】等维度的评价将更保守。
  - **重视传统**: 评分将更侧重故事流畅性、情感普适性和主题明确性。
""",
    "碎片主义护法": """
## 评分模式：✅ 碎片主义护法
- **功能说明**: 模拟热衷于后现代和实验文学的读者视角，高度赞赏文本在形式、语言和结构上的创新。
- **启用效果**:
  - **维度加权**: 对【🌀 先锋性/实验性】、【🧬 语言原创性】、【🧠 结构复杂度】等维度给予额外重视。
  - **创新优先**: 评价将优先考虑文本的颠覆性、思辨性和形式美学。
""",
    "速写视角": """
## 评分模式：✅ 速写视角
- **功能说明**: 适用于需要快速形成初步印象的场景，通过聚焦少数核心维度进行简明评估。
- **启用效果**:
  - **聚焦核心**: 必须仍然返回完整16个维度，但只需重点展开3-5个最能体现作品特质的维度；其他维度可给出较短但仍基于文本的说明。
  - **简化表达**: 最终战力值仍按完整维度体系计算，不能省略任何维度字段。
""",
}

MODE_ALIASES = {
    "standard": "标准模式",
    "default": "标准模式",
    "标准": "标准模式",
    "标准模式": "标准模式",
    "beginner": "初窥门径",
    "初窥门径": "初窥门径",
    "strict": "严苛编辑",
    "严苛编辑": "严苛编辑",
    "reader": "宽容读者",
    "宽容读者": "宽容读者",
    "judge": "文本法官",
    "文本法官": "文本法官",
    "fan": "热血粉丝",
    "热血粉丝": "热血粉丝",
    "anti-modern": "反现代主义者",
    "anti_modern": "反现代主义者",
    "反现代主义者": "反现代主义者",
    "quick": "速写视角",
    "速写视角": "速写视角",
    "fragment": "碎片主义护法",
    "碎片主义": "碎片主义护法",
    "碎片主义护法": "碎片主义护法",
}


def get_mode_instructions(mode: str | list[str]) -> str:
    """Return the full backend scoring-mode instruction text."""

    if isinstance(mode, list):
        raw_modes = mode
    else:
        raw_modes = [item.strip() for item in re.split(r"[,，;；\n]+", str(mode)) if item.strip()]

    if not raw_modes:
        return STANDARD_MODE_INSTRUCTION

    instructions: list[str] = []
    for raw_mode in raw_modes:
        normalized = MODE_ALIASES.get(raw_mode.strip(), raw_mode.strip())
        if normalized in {"", "标准模式"}:
            instructions.append(STANDARD_MODE_INSTRUCTION)
        else:
            instructions.append(ANALYSIS_MODE_INSTRUCTIONS.get(normalized, STANDARD_MODE_INSTRUCTION))
    return "\n\n".join(instructions)


def build_system_prompt(mode: str | list[str]) -> str:
    """Build the complete Ink Battles backend system prompt."""

    chunks = [(PROMPT_DIR / filename).read_text(encoding="utf-8") for filename in PROMPT_FILES]
    return "\n".join(chunks).replace("{{MODE_INSTRUCTION}}", get_mode_instructions(mode))
