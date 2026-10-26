"""Talk only to the user's installed Catfood bridge, never Harness credentials."""
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path


def connection_path():
    base = Path.home() / 'Library/Application Support' if sys.platform == 'darwin' else Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) if sys.platform == 'win32' else Path.home() / '.local/share'
    return base / 'NekoCatfood/bridges/deepseek_harness/connection.json'


def connection(path=None):
    path = Path(path) if path else connection_path()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd) as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise ValueError()
            if sys.platform != 'win32' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError()
            value = json.load(file)
        if value.get('protocol') != 1 or not re.fullmatch(r'[a-f0-9]{64}', value.get('token', '')):
            raise ValueError()
        match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})', value.get('url', ''))
        if not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError()
        return {'url': value['url'], 'token': value['token']}
    except Exception:
        raise ValueError('请在 Harness 安装并启用「猫粮余额桥接」，再刷新；未读取任何登录凭据') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request(cfg, route, body=None, timeout=10):
    # No proxy or redirect may receive the private, loopback-only bridge token.
    raw = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg['url'] + route, data=raw, headers={
        'Authorization': 'Bearer ' + cfg['token'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=timeout) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError()
            result = json.loads(data)
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise ValueError()
            return result
    except Exception:
        raise ValueError('Harness 桥接暂不可用，请确认应用与桥接已开启；保留上次读数') from None


def parse_balance(value, now=None):
    now = time.time() if now is None else now
    try:
        if value.get('protocol') != 1 or value.get('provider') != 'deepseek_harness':
            raise ValueError()
        stamp = value['observed_at']
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 0 <= now - stamp <= 120:
            raise ValueError()
        totals, breakdown = {}, {}
        for kind in ('normal', 'bonus'):
            rows = value['wallets'][kind]
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError()
            for row in rows:
                currency, raw = row['currency'], row['balance']
                if currency not in ('CNY', 'USD') or not isinstance(raw, str) or len(raw) > 80:
                    raise ValueError()
                amount = Decimal(raw)
                if not amount.is_finite() or amount.copy_abs() > Decimal('1e12'):
                    raise ValueError()
                values = breakdown.setdefault(currency, {'normal': Decimal(0), 'bonus': Decimal(0)})
                values[kind] += amount
                totals[currency] = totals.get(currency, Decimal(0)) + amount
        if not totals or any(abs(x) > Decimal('1e12') for x in totals.values()):
            raise ValueError()
        return {'is_available': any(x > 0 for x in totals.values()),
                'balance_infos': [{'currency': k, 'total_balance': str(v)} for k, v in totals.items()],
                'wallet_breakdown': {k: {kind: str(amount) for kind, amount in row.items()} for k, row in breakdown.items()},
                'observed_at': stamp, 'basis': 'DeepSeek Harness 账户余额 · 官方账户组件桥接'}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError('Harness 余额返回无效或已过期，保留上次读数') from None


def read_balance():
    return parse_balance(request(connection(), '/v1/balance'))


def open_topup(timeout=15):
    cfg = connection()
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['/usr/bin/open', 'dsh://open'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == 'win32':
            os.startfile('dsh://open')
    except OSError:
        pass
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if request(cfg, '/v1/status', timeout=2).get('native'):
            break
        time.sleep(.2)
    else:
        return {'ok': False, 'error': 'not_confirmed'}
    command = request(cfg, '/v1/open', {'page': 'top-up'}, timeout=3)
    id = command.get('id', '')
    if not isinstance(id, str) or not re.fullmatch(r'[a-f0-9-]{36}', id):
        return {'ok': False, 'error': 'not_confirmed'}
    while time.monotonic() < end:
        result = request(cfg, '/v1/requests/' + id, timeout=2)
        if result.get('state') == 'opened':
            return {'ok': True, 'stage': 'credits'}
        if result.get('state') in ('failed', 'returned'):
            break
        time.sleep(.2)
    return {'ok': False, 'error': 'not_confirmed'}
