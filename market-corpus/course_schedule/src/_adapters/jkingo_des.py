# -*- coding: utf-8 -*-
"""
青果教务 jkingo.des.js 的纯 Python 移植（零第三方依赖，替代 execjs+Node）
逐行对照原 JS：strEnc / getKeyBytes / strToBt / enc /
initPermute / expandPermute / xor / sBoxPermute / pPermute /
finallyPermute / generateKeys / bt64ToHex

对外接口：
    kingo_str_enc(data: str, key: str) -> str   # 等价 JS strEnc(data, key, null, null)
    KingoDES.encrypt(data, des_key) -> str      # 与 main.py 原 execjs 版同签名，含 base64
"""

import base64


def _str_to_bt(s):
    """JS strToBt: <=4 字符 -> 64 位 bit 数组；不足 4 字符补 0"""
    bt = [0] * 64
    leng = len(s)
    if leng < 4:
        for i in range(leng):
            k = ord(s[i])
            for j in range(16):
                bt[16 * i + j] = (k // (1 << (15 - j))) % 2
    else:
        for i in range(4):
            k = ord(s[i])
            for j in range(16):
                bt[16 * i + j] = (k // (1 << (15 - j))) % 2
    return bt


def _get_key_bytes(key):
    """JS getKeyBytes: 密钥按 4 字符切块，每块转 64-bit"""
    key_bytes = []
    leng = len(key)
    iterator = leng // 4
    remainder = leng % 4
    for i in range(iterator):
        key_bytes.append(_str_to_bt(key[i * 4 : i * 4 + 4]))
    if remainder > 0:
        key_bytes.append(_str_to_bt(key[iterator * 4 : leng]))
    return key_bytes


def _init_permute(original_data):
    ip_byte = [0] * 64
    m, n = 1, 0
    for i in range(4):
        k = 0
        for j in range(7, -1, -1):
            ip_byte[i * 8 + k] = original_data[j * 8 + m]
            ip_byte[i * 8 + k + 32] = original_data[j * 8 + n]
            k += 1
        m += 2
        n += 2
    return ip_byte


def _expand_permute(right_data):
    ep_byte = [0] * 48
    for i in range(8):
        ep_byte[i * 6 + 0] = right_data[31] if i == 0 else right_data[i * 4 - 1]
        ep_byte[i * 6 + 1] = right_data[i * 4 + 0]
        ep_byte[i * 6 + 2] = right_data[i * 4 + 1]
        ep_byte[i * 6 + 3] = right_data[i * 4 + 2]
        ep_byte[i * 6 + 4] = right_data[i * 4 + 3]
        ep_byte[i * 6 + 5] = right_data[0] if i == 7 else right_data[i * 4 + 4]
    return ep_byte


def _xor(a, b):
    return [x ^ y for x, y in zip(a, b)]


_SBOXES = (
    (
        (14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7),
        (0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8),
        (4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0),
        (15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13),
    ),
    (
        (15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10),
        (3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5),
        (0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15),
        (13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9),
    ),
    (
        (10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8),
        (13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1),
        (13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7),
        (1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12),
    ),
    (
        (7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15),
        (13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9),
        (10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4),
        (3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14),
    ),
    (
        (2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9),
        (14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6),
        (4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14),
        (11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3),
    ),
    (
        (12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11),
        (10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8),
        (9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6),
        (4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13),
    ),
    (
        (4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1),
        (13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6),
        (1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2),
        (6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12),
    ),
    (
        (13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7),
        (1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2),
        (7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8),
        (2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11),
    ),
)


def _s_box_permute(expand_byte):
    s_box_byte = [0] * 32
    for m in range(8):
        i = expand_byte[m * 6 + 0] * 2 + expand_byte[m * 6 + 5]
        j = (
            expand_byte[m * 6 + 1] * 8
            + expand_byte[m * 6 + 2] * 4
            + expand_byte[m * 6 + 3] * 2
            + expand_byte[m * 6 + 4]
        )
        val = _SBOXES[m][i][j]
        s_box_byte[m * 4 + 0] = (val >> 3) & 1
        s_box_byte[m * 4 + 1] = (val >> 2) & 1
        s_box_byte[m * 4 + 2] = (val >> 1) & 1
        s_box_byte[m * 4 + 3] = val & 1
    return s_box_byte


_P_SOURCE = (
    15,
    6,
    19,
    20,
    28,
    11,
    27,
    16,
    0,
    14,
    22,
    25,
    4,
    17,
    30,
    9,
    1,
    7,
    23,
    13,
    31,
    26,
    2,
    8,
    18,
    12,
    29,
    5,
    21,
    10,
    3,
    24,
)


def _p_permute(s_box_byte):
    return [s_box_byte[src] for src in _P_SOURCE]


_FP_SOURCE = (
    39,
    7,
    47,
    15,
    55,
    23,
    63,
    31,
    38,
    6,
    46,
    14,
    54,
    22,
    62,
    30,
    37,
    5,
    45,
    13,
    53,
    21,
    61,
    29,
    36,
    4,
    44,
    12,
    52,
    20,
    60,
    28,
    35,
    3,
    43,
    11,
    51,
    19,
    59,
    27,
    34,
    2,
    42,
    10,
    50,
    18,
    58,
    26,
    33,
    1,
    41,
    9,
    49,
    17,
    57,
    25,
    32,
    0,
    40,
    8,
    48,
    16,
    56,
    24,
)


def _finally_permute(end_byte):
    return [end_byte[src] for src in _FP_SOURCE]


_KEY_LOOP = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)

_PC2 = (
    13,
    16,
    10,
    23,
    0,
    4,
    2,
    27,
    14,
    5,
    20,
    9,
    22,
    18,
    11,
    3,
    25,
    7,
    15,
    6,
    26,
    19,
    12,
    1,
    40,
    51,
    30,
    36,
    46,
    54,
    29,
    39,
    50,
    44,
    32,
    47,
    43,
    48,
    38,
    55,
    33,
    52,
    45,
    41,
    49,
    35,
    28,
    31,
)


def _generate_keys(key_byte):
    key = [0] * 56
    for i in range(7):
        for j in range(8):
            key[i * 8 + j] = key_byte[8 * (7 - j) + i]

    keys = []
    for i in range(16):
        for _ in range(_KEY_LOOP[i]):
            temp_left = key[0]
            temp_right = key[28]
            for k in range(27):
                key[k] = key[k + 1]
                key[28 + k] = key[29 + k]
            key[27] = temp_left
            key[55] = temp_right
        keys.append([key[idx] for idx in _PC2])
    return keys


def _enc(data_byte, key_byte):
    keys = _generate_keys(key_byte)
    ip_byte = _init_permute(data_byte)
    ip_left = ip_byte[:32]
    ip_right = ip_byte[32:]
    for i in range(16):
        temp_left = ip_right
        temp_right = _xor(_p_permute(_s_box_permute(_xor(_expand_permute(ip_right), keys[i]))), ip_left)
        ip_left = temp_left
        ip_right = temp_right
    final_data = ip_right + ip_left
    return _finally_permute(final_data)


_HEX_DIGITS = "0123456789ABCDEF"


def _bt64_to_hex(byte_data):
    return "".join(
        _HEX_DIGITS[
            byte_data[i * 4 + 0] * 8 + byte_data[i * 4 + 1] * 4 + byte_data[i * 4 + 2] * 2 + byte_data[i * 4 + 3]
        ]
        for i in range(16)
    )


class KingoDES:
    """与原基于 execjs 的 KingoDES 同接口的纯 Python 替代。"""

    @staticmethod
    def encrypt(data: str, des_key: str) -> str:
        first_key_bt = _get_key_bytes(des_key)
        out = []
        iterator = len(data) // 4
        remainder = len(data) % 4
        for i in range(iterator):
            temp_bt = _str_to_bt(data[i * 4 : i * 4 + 4])
            for kb in first_key_bt:
                temp_bt = _enc(temp_bt, kb)
            out.append(_bt64_to_hex(temp_bt))
        if remainder > 0:
            temp_bt = _str_to_bt(data[iterator * 4 :])
            for kb in first_key_bt:
                temp_bt = _enc(temp_bt, kb)
            out.append(_bt64_to_hex(temp_bt))
        encrypted_hex = "".join(out)
        return base64.b64encode(encrypted_hex.encode("utf-8")).decode("utf-8")
