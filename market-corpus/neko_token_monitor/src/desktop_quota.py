"""Read only an already-open quota panel; never read credentials or send model requests."""
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from .app_navigation import LOCK

LABELS = {
    "trae": ("总可用积分",),
    "workbuddy": ("剩余积分", "可用积分", "积分余额", "总可用积分", "Credits remaining", "Available credits"),
}


class NavigationBusy(ValueError):
    pass


NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"


def parse_panels(app, panels):
    """Require an exact balance label and nearby amount, never sum expiring packages."""
    if app not in LABELS or not isinstance(panels, list):
        raise ValueError("不支持的本机额度来源")
    amounts = set()
    for panel in panels[:100]:
        if not isinstance(panel, list) or len(panel) > 200:
            continue
        texts = [x.strip() for x in panel if isinstance(x, str) and len(x) <= 256]
        for index, text in enumerate(texts):
            for label in LABELS[app]:
                match = re.fullmatch(re.escape(label) + r"\s*[:：]?\s*(" + NUM + r")\s*(?:积分|credits)?", text, re.I)
                if match:
                    amounts.add(float(match[1].replace(',', '')))
                elif text.casefold() == label.casefold():
                    for candidate in texts[index + 1:index + 5]:
                        match = re.fullmatch(r"(" + NUM + r")\s*(?:积分|credits)?", candidate, re.I)
                        if match:
                            amounts.add(float(match[1].replace(',', '')))
                            break
    if len(amounts) != 1:
        raise ValueError("请在应用中打开积分用量页；未找到唯一可用积分，保留上次数据")
    remaining = amounts.pop()
    if not math.isfinite(remaining) or not 0 <= remaining <= 1e12:
        raise ValueError("本机积分数字无效")
    return {"remaining": remaining, "unit": "积分", "total": None,
            "basis": ("TRAE" if app == "trae" else "WorkBuddy") + " 本机额度面板读数 · 非接口实时查询",
            "observed_at": time.time()}


def read_quota(app):
    if sys.platform != 'darwin':
        raise ValueError("本机面板自动读取目前支持 macOS；其他系统可切换手动积分记录")
    if app not in LABELS:
        raise ValueError("不支持的本机额度来源")
    helper = Path(__file__).parent / 'bin/catfood-quota-reader'
    if not helper.is_file():
        raise ValueError("缺少本机额度读取组件，请更新猫粮或切换手动记录")
    if not LOCK.acquire(blocking=False):
        raise NavigationBusy("应用页面正在读取或打开充值入口，请稍后刷新")
    try:
        # Plugin Center creates extracted files with 0644 permissions.
        helper.chmod(helper.stat().st_mode | 0o100)
        result = subprocess.run([str(helper), app], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        if len(result.stdout) > 65536:
            raise ValueError()
        payload = json.loads(result.stdout)
        if app == 'trae' and payload.get('ok') is True and not payload.get('panels'):
            navigator = Path(__file__).parent / 'bin/catfood-open-topup'
            navigator.chmod(navigator.stat().st_mode | 0o100)
            result = subprocess.run([str(navigator), 'trae-quota'], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12)
            if result.returncode or len(result.stdout) > 65536:
                raise ValueError()
            payload = json.loads(result.stdout)
    except Exception:
        raise ValueError("本机额度读取失败，保留上次数据") from None
    finally:
        LOCK.release()
    if not payload.get('ok'):
        if payload.get('error') == 'interrupted':
            raise NavigationBusy('TRAE 正在使用增购页面，稍后再读取积分')
        errors = {'permission': '请在系统辅助功能中授权猫粮的额度读取组件；也可切换手动记录',
                  'not_running': '目标应用未运行，请打开应用的积分用量页',
                  'unsupported': '不支持的本机额度来源',
                  'interrupted': 'TRAE 页面发生切换，已停止读取并保留上次数据',
                  'restore_failed': 'TRAE 积分页未能自动收起，请点击返回应用；保留上次数据'}
        raise ValueError(errors.get(payload.get('error'), '请在应用中打开积分用量页后刷新'))
    quota = parse_panels(app, payload.get('panels'))
    if 'restored' in payload:
        quota['navigation'] = {'opened_page': 'quota' in payload.get('steps', []),
                               'returned_to_app': payload['restored'] is True}
    return quota
