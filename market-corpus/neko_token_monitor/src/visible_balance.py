"""Strict, label-bound DeepSeek webpage wallet reads, separate from API keys."""

import re
import time
from decimal import Decimal, InvalidOperation

MONEY = re.compile(r'\s*[¥￥]\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(?:CNY|RMB)\s*', re.I)


def parse_wallet(panels):
    if not isinstance(panels, list) or len(panels) > 4:
        return None
    amounts = {'充值余额': set(), '赠送余额': set()}
    for texts in panels:
        if not isinstance(texts, list) or len(texts) > 24:
            continue
        label = None
        for index, text in enumerate(texts):
            if not isinstance(text, str) or len(text) > 128:
                continue
            if text in amounts:
                label = text
                continue
            if text == '累计消费金额':
                label = None
                continue
            match = None
            for count in (1, 2, 3):
                parts = texts[index:index + count]
                if len(parts) != count or any(not isinstance(p, str) or len(p) > 128 for p in parts):
                    break
                match = MONEY.fullmatch(' '.join(parts))
                if match:
                    break
            if label and match:
                try:
                    value = Decimal(match[1].replace(',', '') + ('.' + match[2] if match[2] else ''))
                    if value.is_finite() and 0 <= value <= Decimal('1e12'):
                        amounts[label].add(value)
                except InvalidOperation:
                    pass
                label = None
    if any(len(values) != 1 for values in amounts.values()):
        return None
    normal, bonus = (next(iter(amounts[key])) for key in ('充值余额', '赠送余额'))
    return {'currency': 'CNY', 'normal': str(normal), 'bonus': str(bonus),
            'balance': str(normal + bonus), 'observed_at': time.time()}


def saved_wallet(value):
    if not isinstance(value, dict) or value.get('currency') != 'CNY':
        return None
    if any(not isinstance(value.get(key), str) or len(value[key]) > 80 for key in ('normal', 'bonus', 'balance')):
        return None
    try:
        numbers = [Decimal(value[k]) for k in ('normal', 'bonus', 'balance')]
        at = value['observed_at']
        if (any(not n.is_finite() or not 0 <= n <= Decimal('2e12') for n in numbers)
                or numbers[0] + numbers[1] != numbers[2] or type(at) not in (int, float)
                or not 0 < at <= time.time() + 5):
            return None
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return None
    return {k: value[k] for k in ('currency', 'normal', 'bonus', 'balance', 'observed_at')}
