# -*- coding: utf-8 -*-
"""
数据源 API Key 配置
====================
把你注册得到的 token / API Key 粘到下面对应的引号里即可。
没有的就留空字符串 ""(程序会自动跳过该数据源,改用其他可用源)。

也支持用环境变量覆盖(环境变量优先级更高)。

各数据源注册地址:
    Tushare Pro     : https://tushare.pro/register   （A股/港股,免费注册送积分)
    Polygon.io      : https://polygon.io/             （美股,免费档需注册)
    Alpha Vantage   : https://www.alphavantage.co/support/#api-key  （美股,免费 Key)
"""

import os


def _positive_int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default

# A股 / 港股 —— Tushare Pro 的 token
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 美股 —— Polygon.io 的 API Key
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# 美股 —— Alpha Vantage 的 API Key
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")

# 美股热门榜的新鲜度授权。留空时 Alpha Vantage 官方接口返回日终榜；
# 只有订阅相应权限后才可设置 delayed 或 realtime。
ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT = os.environ.get(
    "ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT", ""
).strip().lower()

# 专业热门榜不可用时是否允许尝试公开网页接口。公开源只能作为带降级
# 标记的 best-effort 备援，不会被包装为专业或稳定来源，也绝不回退新浪。
HOT_STOCK_PUBLIC_FALLBACK_ENABLED = os.environ.get(
    "HOT_STOCK_PUBLIC_FALLBACK_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
HOT_STOCK_PROVIDER_FAILURE_THRESHOLD = _positive_int_env(
    "HOT_STOCK_PROVIDER_FAILURE_THRESHOLD", 2, 1
)
HOT_STOCK_PROVIDER_CIRCUIT_SECONDS = _positive_int_env(
    "HOT_STOCK_PROVIDER_CIRCUIT_SECONDS", 300, 30
)
