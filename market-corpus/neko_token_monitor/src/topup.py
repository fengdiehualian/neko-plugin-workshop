"""Verified official entry points only. Opening a page never confirms a payment."""
import copy
import os
import subprocess
import sys
import webbrowser
from urllib.parse import urlsplit

from . import app_topup


def entry(id, label, url, hint):
    return dict(id=id, label=label, url=url, domain=urlsplit(url).hostname, hint=hint)


LINKS = {
    'codex': [entry('usage', 'Codex 用量与补充额度', 'https://chatgpt.com/codex/settings/usage', '登录相同 ChatGPT 账号；可购买项目以官方用量页为准，不是 API 充值。')],
    'chatgpt': [entry('plan', 'ChatGPT 官方套餐', 'https://chatgpt.com/pricing/', 'ChatGPT 订阅与 OpenAI API 余额分别管理。')],
    'trae': [entry('credits', 'TRAE 官方套餐与积分', 'https://www.trae.cn/pricing', '中国站。增购积分也可在 TRAE 用量管理页操作；套餐、赠送积分与有效期以官方为准。')],
    'workbuddy': [entry('plan', 'WorkBuddy 套餐与积分', 'https://www.workbuddy.cn/profile/plan', '登录后查看套餐／加量包；客户端可进入头像 → 设置 → 套餐与积分。是否可购买以官方账户页面为准。')],
    'deepseek': [entry('balance', 'DeepSeek API 充值', 'https://platform.deepseek.com/top_up', '用于 DeepSeek 开放平台 API 账户，不是聊天产品积分。')],
    'deepseek_harness': [entry('balance', 'DeepSeek Harness 官网充值备用', 'https://platform.deepseek.com/top_up', '请登录与 Harness 相同的 DeepSeek 账户，再核对余额与支付。')],
    'openai': [entry('billing', 'OpenAI API 账单与充值', 'https://platform.openai.com/settings/organization/billing/overview', '确认当前组织；自设猫粮预算不会因充值自动改变，账户费用仍按预算口径计算。')],
    'anthropic': [entry('billing', 'Claude API 账单与购买额度', 'https://platform.claude.com/settings/billing', '确认当前组织；不是 Claude 订阅套餐。插件显示的自设费用预算不会因购买额度自动增加。')],
    'claude': [entry('subscription', 'Claude / Claude Code 套餐管理', 'https://claude.ai/settings/billing', '适用于通过 Claude 订阅使用的额度；如果使用 Console API 余额，请切换 Claude API 档案。')],
    'openrouter': [entry('credits', 'OpenRouter 官方充值', 'https://openrouter.ai/settings/credits', '补充此 OpenRouter 账户的多模型共享额度。')],
    'siliconflow': [entry('console', '硅基流动中国站控制台', 'https://cloud.siliconflow.cn/', '登录后进入余额充值。此档案查询中国站 CNY 余额。')],
    'kimi_api_cn': [entry('console', 'Kimi API 中国站充值', 'https://platform.kimi.com/', '登录开放平台控制台后进入费用／用户充值；中国站与国际站、会员／Code 余额不互通。')],
    'kimi_api_global': [entry('account', 'Kimi API 国际站账户', 'https://platform.kimi.ai/console/account', '登录国际站账户后管理账务；请使用与查询密钥相同区域的账户。')],
    'kimi': [entry('membership', 'Kimi 官方会员与 Code 方案', 'https://www.kimi.com/membership/pricing', '此处管理 Kimi 会员／Code 方案；不补充 Kimi 开放平台 API 余额。手动记录需由你校正。')],
    'qwen_api': [entry('balance', '阿里云账户充值', 'https://usercenter2.aliyun.com/home', '补充阿里云账户共享资金；不直接增加 Qwen 聊天积分或某模型免费 Token。')],
    'qwen': [entry('chat', '千问官方聊天产品', 'https://www.qianwen.com/', '查看当前账号提供的权益；不承诺聊天产品提供付费积分。'),
             entry('bailian', '百炼用量与付费方案', 'https://bailian.console.aliyun.com/', '查看百炼免费 Token、Coding Plan 或按量付费；与聊天账号分开管理。'),
             entry('balance', '阿里云账户充值', 'https://usercenter2.aliyun.com/home', '为阿里云按量计费补充账户资金，不等于续期免费 Token。')],
    'gemini': [entry('plan', 'Google AI 官方套餐', 'https://one.google.com/about/google-ai-plans/', '适用于 Gemini 订阅；地区与资格以官方为准，不是 Gemini API 账务。')],
    'doubao': [entry('chat', '豆包官方聊天产品', 'https://www.doubao.com/', '查看聊天账号的实际权益，不承诺提供可充值积分。'),
               entry('ark', '火山方舟 API 账户充值', 'https://console.volcengine.com/finance/fund/recharge', '仅用于火山引擎 API 账户，与豆包聊天产品权益分开管理。')],
    'glm': [entry('console', '智谱 BigModel 官方平台', 'https://bigmodel.cn/', '登录官方控制台选择账户充值或 Coding Plan；请选择与你的额度记录一致的产品。')],
}


# Official purchase flow + a working entry, never invented market-share statistics.
RULES = {
    'deepseek_harness': ('application','balance','优先使用 Harness 自己的充值窗口；官网入口作为备用。','https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/client/ui-settings-account'),
    'codex': ('website', 'usage', 'Codex 官方统一用量页提供补充额度；ChatGPT 登录额度与 API 余额分开。', 'https://developers.openai.com/codex/pricing/'),
    'trae': ('application', 'credits', 'TRAE 可在客户端增购积分；macOS 优先应用内入口，官网作为备用。', 'https://docs.trae.cn/ide_subscription-management'),
    'workbuddy': ('website', 'plan', 'WorkBuddy 官方同时支持客户端和官网购买；官网套餐页可直接管理当前账号的订阅与加量包。', 'https://www.codebuddy.cn/docs/workbuddy/Plan'),
    'deepseek': ('website', 'balance', '此档案监测 DeepSeek API 余额，优先进入对应开放平台充值页。', 'https://api-docs.deepseek.com/quick_start/pricing/'),
}


def routing(profile, application):
    entries = LINKS.get(profile, [])
    if not entries:
        return {'kind':'unavailable', 'target':None, 'label':'暂无已核验入口', 'reason':'请前往实际服务商的账户管理页。'}
    if profile in ('qwen', 'doubao'):
        return {'kind':'choice', 'target':None, 'label':'先选择要补充的产品', 'reason':'聊天权益、API 账户资金与免费 Token 不是同一额度，不自动替你选择。'}
    kind, target, reason, source = RULES.get(profile, (
        'website', entries[0]['id'], '优先使用此额度档案对应的官方账户／套餐页面。', None))
    if kind == 'application':
        if application:
            target = 'application'
        else:
            kind = 'website'
            reason = '当前系统使用官方网页入口；请核对与你监测的应用相同的账户。'
    result = {'kind':kind, 'target':target, 'label':'推荐应用内入口' if kind == 'application' else '推荐官网入口',
              'reason':reason, 'basis':'official_flow_and_available_adapter'}
    if source:
        result['source'] = source
    if profile == 'workbuddy':
        result['guide'] = '应用内也可操作：头像 → 设置 → 套餐与积分 → 购买积分。能否增购以当前套餐和官方页面为准。'
    return result


def info(profile):
    application = app_topup.info(profile)
    if profile == 'deepseek_chat':
        return {'profile': profile, 'entries': [], 'application': None,
                'routing': {'kind': 'unavailable', 'target': None, 'label': '聊天产品独立管理',
                            'reason': '网页聊天与开放平台 API 不是同一额度，不代你跳转 API 充值。'},
                'hint': '此处只记录 DeepSeek 网页聊天产品的额度；未接入自动查询或独立充值入口，不显示 API 钱包余额。'}
    return {'profile': profile, 'entries': copy.deepcopy(LINKS.get(profile, [])),
            'application': application, 'routing': routing(profile, application),
            'hint': '选择应用内入口或官方网页；请自行完成支付。返回后刷新额度，手动记录需自行校正。' if application else
                    '仅打开官方页面；请在官网自行完成支付。返回后刷新额度，手动记录需自行校正。' if profile in LINKS else
                    '此来源尚无已核验的官方充值入口，请前往你实际使用的服务商账户管理页；不会按模型备注猜测充值网站。'}


def destination(profile, target):
    if not isinstance(profile, str) or not isinstance(target, str):
        raise ValueError('充值入口无效')
    item = next((x for x in LINKS.get(profile, []) if x['id'] == target), None)
    if not item:
        raise ValueError('没有此官方充值入口')
    return dict(item)


def open_url(url):
    # No shell, account identifiers, keys, tracking parameters, or payment requests.
    if sys.platform == 'darwin':
        subprocess.Popen(['/usr/bin/open', url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    if sys.platform == 'win32':
        os.startfile(url)
        return True
    return webbrowser.open(url, new=2)


def open_official(profile, target, opener=None, application_opener=None):
    if target == 'default':
        route = info(profile)['routing']
        if route['kind'] in ('choice', 'unavailable'):
            return {'requested': False, 'choice_required': route['kind'] == 'choice',
                    'payment_confirmed': False, 'profile': profile}
        target = route['target']
    if target == 'application':
        return app_topup.open_application(profile, application_opener)
    item = destination(profile, target)
    try:
        requested = bool((opener or open_url)(item['url']))
    except Exception:
        requested = False
    return {'requested': requested, 'payment_confirmed': False, 'profile': profile,
            'target': target, 'label': item['label'], 'url': item['url']}
