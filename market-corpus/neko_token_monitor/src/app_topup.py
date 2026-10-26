"""Explicit user action: navigate an allowlisted app to its credits screen only."""
import json
import subprocess
import sys
from pathlib import Path

from .app_navigation import LOCK as _lock
from .harness_bridge import open_topup

_errors = {
    'unsupported': '此平台暂未接入应用内入口，请使用下方官网入口喵～',
    'permission': '应用内入口需要 macOS 辅助功能权限；请在系统设置中授权实际运行猫粮的程序，或使用下方官网入口喵～',
    'not_installed': '未找到 TRAE 中国版，请使用下方官网入口喵～',
    'busy': '正在打开应用内页面，请稍候喵～',
    'interrupted': '已停止导航：当前应用发生切换。可重试或使用下方官网入口喵～',
    'not_confirmed': '未能确认应用内增购页面，已停止导航。可先手动打开 TRAE 用量管理，再重试，或使用下方官网入口喵～',
    'component': '应用内入口组件不可用，请更新猫粮或使用下方官网入口喵～',
}


def info(profile):
    if profile == 'workbuddy' and sys.platform == 'darwin':
        return {'id':'application','label':'在 WorkBuddy 应用内补充猫粮',
                'hint':'打开套餐与积分的购买页面；无法定位时停止，可使用官网入口。需应用已登录及辅助功能权限。'}
    if profile == 'deepseek_harness' and sys.platform in ('darwin', 'win32'):
        return {'id':'application', 'label':'在 DeepSeek Harness 内补充猫粮',
                'hint':'需启用 Harness 猫粮余额桥接；使用 Harness 自己的充值窗口，登录凭据留在应用内。'}
    if profile == 'trae' and sys.platform == 'darwin':
        return {'id': 'application', 'label': '在 TRAE 应用内增购积分',
                'hint': 'macOS · TRAE 中国版。打开官方增购窗口后，由你选择套餐并完成支付；需要辅助功能权限。'}
    return None


def _run(profile):
    if profile == 'deepseek_harness':
        return open_topup()
    helper = Path(__file__).parent / 'bin/catfood-open-topup'
    if not helper.is_file():
        return {'ok': False, 'error': 'component'}
    try:
        helper.chmod(helper.stat().st_mode | 0o100)
        completed = subprocess.run([str(helper), profile], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=16)
        if completed.returncode or len(completed.stdout) > 4096:
            raise ValueError()
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        return {'ok': False, 'error': 'not_confirmed'}


def open_application(profile, opener=None):
    # No bundle IDs, labels, URLs, arguments or payment options come from HTTP.
    if not info(profile):
        raise ValueError('没有此应用内充值入口')
    if not _lock.acquire(blocking=False):
        result = {'ok': False, 'error': 'busy'}
    else:
        try:
            result = (opener or _run)(profile)
        except Exception:
            result = {'ok': False, 'error': 'not_confirmed'}
        finally:
            _lock.release()
    if not isinstance(result, dict):
        result = {}
    confirmed = result.get('ok') is True and result.get('stage') == 'credits'
    error = None if confirmed else result.get('error')
    if (not isinstance(error, str) or error not in _errors) and not confirmed:
        error = 'not_confirmed'
    steps = result.get('steps')
    if not isinstance(steps, list):
        steps = []
    return {'requested': confirmed, 'page_confirmed': confirmed, 'payment_confirmed': False,
            'opened_in': 'application', 'profile': profile, 'target': 'application',
            'navigation_steps': [x for x in steps if x in ('avatar', 'settings', 'quota', 'credits')][:4],
            'diagnostic': result.get('diagnostic') if result.get('diagnostic') in ('no_window', 'no_avatar', 'no_quota', 'no_credits', 'no_modal', 'activation') else None,
            'error': error, 'message': ('已打开 Harness 应用内充值窗口；请自行完成支付喵～' if profile == 'deepseek_harness' else '已打开 WorkBuddy 应用内购买积分窗口；请自行完成支付喵～' if profile == 'workbuddy' else '已打开 TRAE 应用内增购积分窗口；请自行完成支付喵～') if confirmed else
                '未能确认 Harness 充值窗口，请检查应用与「猫粮余额桥接」，或使用官网入口喵～' if profile == 'deepseek_harness' else
                '未能确认 WorkBuddy 购买积分窗口，请检查应用，或使用官网入口喵～' if profile == 'workbuddy' else _errors[error]}
