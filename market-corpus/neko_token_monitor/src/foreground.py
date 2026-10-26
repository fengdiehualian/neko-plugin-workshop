"""Follow foreground products and read only allowlisted wallet fields."""

import json
import subprocess
import sys
import threading
from pathlib import Path

from .visible_balance import parse_wallet

APP_PROFILES = {
    'com.openai.codex': 'codex',
    'cn.trae.app': 'trae', 'com.trae.app': 'trae',
    'com.tencent.workbuddy': 'workbuddy', 'com.tencent.codebuddy.workbuddy': 'workbuddy',
    'com.deepseek.dsh': 'deepseek_harness',
    'com.anthropic.claudefordesktop': 'claude', 'com.openai.chat': 'chatgpt',
}
# Product boundaries matter: a chat subscription is not the same as an API wallet.
PAGE_PROFILES = {
    'chatgpt.com': 'chatgpt', 'chat.openai.com': 'chatgpt', 'platform.openai.com': 'openai',
    'claude.ai': 'claude', 'console.anthropic.com': 'anthropic', 'platform.claude.com': 'anthropic',
    'platform.deepseek.com': 'deepseek', 'chat.deepseek.com': 'deepseek_chat',
    'trae.cn': 'trae', 'www.trae.cn': 'trae', 'trae.ai': 'trae', 'www.trae.ai': 'trae',
    'workbuddy.cn': 'workbuddy', 'www.workbuddy.cn': 'workbuddy',
    'kimi.com': 'kimi', 'www.kimi.com': 'kimi', 'kimi.moonshot.cn': 'kimi',
    'platform.moonshot.cn': 'kimi_api_cn', 'platform.moonshot.ai': 'kimi_api_global',
    'platform.kimi.com': 'kimi_api_cn', 'platform.kimi.ai': 'kimi_api_global',
    'chat.qwen.ai': 'qwen', 'qianwen.com': 'qwen', 'www.qianwen.com': 'qwen',
    'bailian.console.aliyun.com': 'qwen_api', 'usercenter2.aliyun.com': 'qwen_api',
    'gemini.google.com': 'gemini', 'aistudio.google.com': 'gemini',
    'doubao.com': 'doubao', 'www.doubao.com': 'doubao',
    'chatglm.cn': 'glm', 'bigmodel.cn': 'glm', 'open.bigmodel.cn': 'glm',
    'openrouter.ai': 'openrouter', 'cloud.siliconflow.cn': 'siliconflow',
}
IGNORED_APPS = {'cn.nekotech.neko', 'local.neko.budgetwidget', 'local.neko.token-monitor.widget'}


def classify(value):
    if not isinstance(value, dict):
        return {'profile': None, 'status': 'unrecognized'}
    bundle, host = value.get('bundle'), value.get('host', '')
    if not isinstance(bundle, str) or len(bundle) > 160 or not isinstance(host, str) or len(host) > 253:
        return {'profile': None, 'status': 'unrecognized'}
    bundle, host = bundle.lower(), host.lower().rstrip('.')
    if bundle in IGNORED_APPS or bundle.startswith('local.neko.catfood.') or host in ('127.0.0.1', 'localhost', '::1'):
        return {'profile': None, 'status': 'ignored'}
    if host:
        # Exact host only: no title guesses, suffix matches or token-bearing URLs.
        profile = PAGE_PROFILES.get(host)
        result = {'profile': profile, 'status': 'page' if profile else 'unrecognized'}
        if host == 'platform.deepseek.com':
            wallet = parse_wallet(value.get('wallet_panels'))
            if wallet:
                result['page_wallet'] = wallet
        return result
    profile = APP_PROFILES.get(bundle)
    return {'profile': profile, 'status': 'application' if profile else
            'permission' if value.get('error') == 'permission' else 'unrecognized'}


class ForegroundWatcher:
    def __init__(self, callback):
        self.callback = callback
        self.stop = threading.Event()
        self.thread = None
        self.process = None
        self.last = None

    def event(self, value):
        if self.stop.is_set():
            return
        context = classify(value)
        if context != self.last:
            self.last = context
            self.callback(context)

    def start(self):
        if sys.platform != 'darwin' or self.thread and self.thread.is_alive():
            return

        def worker():
            helper = Path(__file__).parent / 'bin/catfood-quota-reader'
            while not self.stop.is_set():
                try:
                    helper.chmod(helper.stat().st_mode | 0o100)
                    process = subprocess.Popen([str(helper), 'foreground-watch'], stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    self.process = process
                    if self.stop.is_set():
                        process.terminate()
                    while not self.stop.is_set():
                        raw = process.stdout.readline(4097)
                        if not raw or len(raw) > 4096:
                            break
                        try:
                            self.event(json.loads(raw))
                        except (ValueError, TypeError):
                            continue
                except (OSError, ValueError):
                    pass
                finally:
                    process = self.process
                    if process:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                        if process.stdout:
                            process.stdout.close()
                    self.process = None
                self.last = None
                if not self.stop.is_set():
                    self.callback({'profile': None, 'status': 'unavailable'})
                self.stop.wait(5)

        self.thread = threading.Thread(target=worker, daemon=True, name='catfood-foreground')
        self.thread.start()

    def close(self):
        self.stop.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        if self.thread:
            self.thread.join(4)
