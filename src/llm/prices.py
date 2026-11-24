"""模型价格：用量页按它把 token 折成美元（每百万 token 的美元）。

只读所有者在「模型端点」里给模型填的价格（``.vortoone/model_credentials.json``，只取价格，不碰密钥）。
没填价格的模型返回 None：用量页照样记 token，但不把它算成 0 元——缺价格不等于免费。
"""
from __future__ import annotations

import json
from typing import Dict, Optional, Tuple

from src.utils.state_dir import state_dir


def price_table(root: str) -> Dict[str, Tuple[float, float, float]]:
    """模型 id → (输入, 输出, 缓存读) 每百万 token 美元。同一个模型在几个端点上都填了价格时取第一个。"""
    try:
        data = json.loads((state_dir(root) / "model_credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table: Dict[str, Tuple[float, float, float]] = {}
    for item in data.get("credentials") or [] if isinstance(data, dict) else []:
        if not isinstance(item, dict) or item.get("status", "active") != "active":
            continue
        for model in item.get("models") or []:
            prices = model.get("prices") if isinstance(model, dict) else None
            if not isinstance(prices, dict) or not isinstance(model.get("id"), str):
                continue
            try:
                row = (float(prices["input"]), float(prices["output"]), float(prices.get("cacheRead", prices["input"])))
            except (KeyError, TypeError, ValueError):
                continue
            table.setdefault(model["id"], row)
    return table


def cost(table: Dict[str, Tuple[float, float, float]], model: str, prompt: int, completion: int,
         cached: int = 0) -> Optional[float]:
    """这些 token 按登记价格值多少美元；没有价格返回 None。缓存命中的输入按缓存价算。"""
    price = table.get(model)
    if price is None:
        return None
    fresh = max(0, prompt - cached)
    return (fresh * price[0] + min(cached, prompt) * price[2] + completion * price[1]) / 1_000_000
