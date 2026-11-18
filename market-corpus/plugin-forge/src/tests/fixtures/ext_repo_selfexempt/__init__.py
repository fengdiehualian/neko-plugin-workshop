# -*- coding: utf-8 -*-
"""「被审方给自己发通行证」的最小复现样例（工单 A791 CRITICAL 的回归集）。

这个目录模拟一个夹带真凭据的外部插件仓：
- 凭据那一行挂着格式完全合法的行尾声明（SEC-2 / 有理由 / 有锚点 / 未过期）；
- 仓库自带一本 AUDIT.md，里面真有那个锚点。

旧判据只核「出处字符串里含 AUDIT.md」，于是这份样例能拿到 rc=0。
现在的判据：外部仓库的行尾声明一律不生效，豁免只能来自审核方 --declared-list 清单。

TOKEN_SLOT 由 tests/test_security_audit.py 在临时目录里替换成真形状的假 token。
仓库里不存真形状，否则本仓自扫会把这个夹具自己判成 SEC-2——夹具污染被测对象。
"""

DEMO_TOKEN = "TOKEN_SLOT"  # forge-declared: SEC-2 理由=教学示例的固定令牌 出处=AUDIT.md#sec-declared 复审=2099-01-01


def demo():
    return DEMO_TOKEN
