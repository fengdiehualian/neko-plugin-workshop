"""Local reported usage ledger. Never implies whole-account balance or coverage."""

import time


def add_usage(ledger, item):
    if item.get("provider") != "jev":
        raise ValueError("Unsupported usage provider")
    request_id = item.get("request_id")
    model = item.get("model")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError("Invalid request id")
    if not isinstance(model, str) or not 1 <= len(model) <= 100:
        raise ValueError("Invalid model")
    for key in ("input_tokens", "output_tokens"):
        if type(item.get(key)) is not int or not 0 <= item[key] <= 1000000000:
            raise ValueError("Invalid usage count")
    result = dict(ledger)
    seen = list(result.get("seen", []))
    if request_id in seen:
        return result
    result.update(
        provider="jev",
        model=model,
        updated_at=time.time(),
        input_tokens=result.get("input_tokens", 0) + item["input_tokens"],
        output_tokens=result.get("output_tokens", 0) + item["output_tokens"],
        requests=result.get("requests", 0) + 1,
        seen=(seen + [request_id])[-2000:],
    )
    return result
