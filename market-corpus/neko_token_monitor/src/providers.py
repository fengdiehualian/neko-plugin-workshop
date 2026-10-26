"""Read-only provider adapters. Report budget estimates separately from balances."""

import datetime as dt
import json
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .aliyun_balance import query as query_aliyun


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_json(url, headers):
    try:
        with build_opener(NoRedirect).open(Request(url, headers=headers), timeout=12) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except Exception:
        raise ValueError("查询失败，请检查接口、查询权限和密钥；保留上次数据") from None


def money(value):
    if isinstance(value, bool):
        raise ValueError("金额格式无效")
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("金额格式无效")
    return amount


def validate_endpoint(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment or p.query:
        raise ValueError("自定义接口须为 HTTPS 地址，不要在地址中放密钥或查询参数")
    return url


def query(cfg, request=read_json):
    source = cfg["source"]
    if source == "manual":
        value = money(cfg["budget"])
        basis = "手动填写 · 不自动扣减"
    elif source in ("moonshot_cn", "moonshot_global"):
        host = "api.moonshot.cn" if source == "moonshot_cn" else "api.moonshot.ai"
        payload = request("https://" + host + "/v1/users/me/balance", {"Authorization": "Bearer " + cfg["api_key"]})
        if payload.get("status") is not True or type(payload.get("code")) is not int or payload["code"] != 0:
            raise ValueError("Kimi 开放平台余额查询失败")
        value = money(payload["data"]["available_balance"])
        basis = "Kimi 开放平台可用余额 · 含可用代金券 · 非 Kimi 会员或 Code 套餐"
    elif source == "aliyun":
        payload = query_aliyun(cfg.get("aliyun_profile", ""))
        data = payload["Data"]
        if payload.get("Success") is not True or data["Currency"] not in ("CNY", "USD"):
            raise ValueError("阿里云余额数据无效")
        value = money(data["AvailableAmount"].replace(',', '') if isinstance(data["AvailableAmount"], str) else data["AvailableAmount"])
        if data["Currency"] != cfg["currency"]:
            raise ValueError("账户返回币种与设置不一致，请切换币种后重试")
        basis = "阿里云账户共享可用额度（含授信）· 非 Qwen 积分或百炼免费 Token"
    elif source in ("openrouter", "siliconflow"):
        url = (
            "https://openrouter.ai/api/v1/credits"
            if source == "openrouter"
            else "https://api.siliconflow.cn/v1/user/info"
        )
        payload = request(url, {"Authorization": "Bearer " + cfg["api_key"]})
        data = payload["data"]
        if source == "openrouter":
            value = money(data["total_credits"]) - money(data["total_usage"])
        else:
            if payload.get("status") is not True:
                raise ValueError("硅基流动余额查询失败")
            value = money(data["totalBalance"])
        basis = "服务商账户共享余额 · 非单模型独立额度"
    elif source == "custom":
        payload = request(validate_endpoint(cfg["endpoint"]), {"Authorization": "Bearer " + cfg["api_key"]})
        value = payload
        for part in cfg["value_path"].split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
        value = money(value)
        basis = "自定义接口返回余额"
    else:
        now = dt.datetime.now(dt.timezone.utc)
        start = dt.datetime.fromisoformat(cfg["budget_start"]).replace(tzinfo=dt.timezone.utc)
        if start >= now:
            raise ValueError("预算起始日期须早于当前时间")
        openai = source == "openai"
        url = (
            "https://api.openai.com/v1/organization/costs"
            if openai
            else "https://api.anthropic.com/v1/organizations/cost_report"
        )
        params = (
            {"start_time": int(start.timestamp()), "end_time": int(now.timestamp()), "limit": 180}
            if openai
            else {"starting_at": start.isoformat(), "ending_at": now.isoformat(), "limit": 31}
        )
        headers = (
            {"Authorization": "Bearer " + cfg["api_key"]}
            if openai
            else {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
        )
        headers["User-Agent"] = "NekoCatFood/0.7"
        total = Decimal(0)
        seen = set()
        for _ in range(100):
            payload = request(url + "?" + urlencode(params), headers)
            for bucket in payload["data"]:
                for row in bucket["results"]:
                    if openai:
                        if row["amount"]["currency"].lower() != "usd":
                            raise ValueError("费用币种不是 USD")
                        total += money(row["amount"]["value"])
                    else:
                        if row.get("currency", "USD").lower() != "usd":
                            raise ValueError("费用币种不是 USD")
                        total += money(row["amount"]) / 100
            if not payload.get("has_more", False):
                break
            cursor = payload.get("next_page")
            if not cursor or cursor in seen:
                raise ValueError("费用分页不完整")
            seen.add(cursor)
            params["page"] = cursor
        else:
            raise ValueError("费用分页过多，请缩短预算期间")
        value = money(cfg["budget"]) - total
        basis = "预算减组织已用费用 · 非平台余额"
    return {
        "is_available": value > 0,
        "balance_infos": [{"currency": cfg["currency"], "total_balance": str(value)}],
        "basis": basis,
    }
