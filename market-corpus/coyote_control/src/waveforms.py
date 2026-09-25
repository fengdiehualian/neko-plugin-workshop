"""
郊狼 V3 波形数据。

官方 Socket 协议要求每帧 8 字节（16 个十六进制字符），对应 100ms：
前 4 字节为频率，后 4 字节为相对强度。
"""

from typing import List

WAVEFORM_DATA = {
    "BREATH": {
        "name": "呼吸",
        "description": "温柔的呼吸节奏",
        "frames": [
            "0A0A0A0A0A0A0A0A",
            "0A0A0A0A14141414",
            "0A0A0A0A1E1E1E1E",
            "0A0A0A0A28282828",
            "0A0A0A0A32323232",
            "0A0A0A0A3C3C3C3C",
            "0A0A0A0A32323232",
            "0A0A0A0A28282828",
            "0A0A0A0A1E1E1E1E",
            "0A0A0A0A14141414",
        ],
    },
    "BUBBLE": {
        "name": "气泡",
        "description": "轻柔的气泡感",
        "frames": [
            "0C0C0C0C08080808",
            "0C0C0C0C18181818",
            "0C0C0C0C0A0A0A0A",
            "0C0C0C0C1E1E1E1E",
            "0C0C0C0C0C0C0C0C",
            "0C0C0C0C14141414",
        ],
    },
    "PULSE": {
        "name": "脉冲",
        "description": "规律的脉冲刺激",
        "frames": [
            "1414141400000000",
            "141414143C3C3C3C",
            "1414141400000000",
            "141414143C3C3C3C",
        ],
    },
    "TIDE": {
        "name": "潮汐",
        "description": "潮水般涨落",
        "frames": [
            "101010100A0A0A0A",
            "1010101014141414",
            "101010101E1E1E1E",
            "1010101028282828",
            "1010101032323232",
            "101010103C3C3C3C",
            "1010101446464646",
            "101010103C3C3C3C",
            "1010101032323232",
            "1010101028282828",
        ],
    },
    "WAVE": {
        "name": "波浪",
        "description": "连续的波浪感",
        "frames": [
            "1212121214141414",
            "1212121228282828",
            "121212123C3C3C3C",
            "1212121228282828",
            "1212121214141414",
            "121212120A0A0A0A",
        ],
    },
    "CLIMB": {
        "name": "攀爬",
        "description": "逐步加强",
        "frames": [
            "161616160A0A0A0A",
            "1616161614141414",
            "161616161E1E1E1E",
            "1616161628282828",
            "1616161632323232",
            "161616163C3C3C3C",
            "1616161446464646",
            "1616161450505050",
        ],
    },
    "RANDOM": {
        "name": "随机",
        "description": "不规则节奏",
        "frames": [
            "181818180A0A0A0A",
            "0C0C0C0C32323232",
            "1E1E1E1E14141414",
            "101010103C3C3C3C",
            "1414141408080808",
            "1A1A1A1A28282828",
        ],
    },
    "FROG": {
        "name": "青蛙",
        "description": "跳跃式刺激",
        "frames": [
            "1E1E1E1E00000000",
            "1E1E1E1E00000000",
            "1E1E1E1E5A5A5A5A",
            "1E1E1E1E00000000",
        ],
    },
}


def _normalize_name(waveform_name: str) -> str:
    return (waveform_name or "BREATH").upper()


def get_waveform_frames(waveform_name: str, intensity: int = None, duration_ms: int = 2000) -> List[str]:
    """
    按持续时间展开波形帧，每帧 100ms。
    
    Args:
        waveform_name: 波形名称
        intensity: 强度（保留参数用于兼容性，实际波形强度由设备强度设置控制）
        duration_ms: 持续时间（毫秒）
    
    Returns:
        波形帧列表
    """
    waveform = WAVEFORM_DATA.get(_normalize_name(waveform_name), WAVEFORM_DATA["BREATH"])
    pattern = waveform["frames"]
    needed = max(1, int(duration_ms) // 100)
    return [pattern[index % len(pattern)] for index in range(needed)]


def get_waveform_data(waveform_name: str) -> List[str]:
    """返回原始波形帧列表。"""
    waveform = WAVEFORM_DATA.get(_normalize_name(waveform_name), WAVEFORM_DATA["BREATH"])
    return list(waveform["frames"])


def get_waveform_description(waveform_name: str) -> str:
    """获取波形中文名。"""
    waveform = WAVEFORM_DATA.get(_normalize_name(waveform_name))
    if not waveform:
        return "呼吸"
    return waveform["name"]


def list_waveforms() -> list:
    """列出所有可用波形。"""
    return [
        {"name": key, "display": value["name"], "description": value["description"]}
        for key, value in WAVEFORM_DATA.items()
    ]
