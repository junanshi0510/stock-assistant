# -*- coding: utf-8 -*-
"""
数据源 API Key 配置
====================
把你注册得到的 token / API Key 粘到下面对应的引号里即可。
没有的就留空字符串 ""(程序会自动跳过该数据源,改用其他可用源)。

也支持用环境变量覆盖(环境变量优先级更高)。

各数据源注册地址:
    Tushare Pro     : https://tushare.pro/register   （A股/港股,免费注册送积分)
    Massive/Polygon : https://massive.com/             （美股,免费档需注册)
    Alpha Vantage   : https://www.alphavantage.co/support/#api-key  （美股,免费 Key)
    Futu OpenAPI    : https://openapi.futunn.com/      （A/H/美股,需本地 OpenD)
"""

import os


def _positive_int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _nonnegative_float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default

# A股 / 港股 —— Tushare Pro 的 token
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 美股 —— Polygon.io 的 API Key
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# 美股 —— Massive（原 Polygon.io）的 API Key。新部署优先使用这个变量，
# 旧部署的 POLYGON_API_KEY 继续兼容，避免切换供应商品牌时中断历史行情。
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "")
MASSIVE_API_BASE_URL = os.environ.get("MASSIVE_API_BASE_URL", "https://api.massive.com").rstrip("/")

# 美股 —— Alpha Vantage 的 API Key
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")

# 美股热门榜的新鲜度授权。留空时 Alpha Vantage 官方接口返回日终榜；
# 只有订阅相应权限后才可设置 delayed 或 realtime。
ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT = os.environ.get(
    "ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT", ""
).strip().lower()

# 富途 OpenAPI 通过常驻 FutuOpenD 提供行情。HOST 留空即完全禁用，不会在
# 未配置时尝试连接；MARKETS 可用 A/H/US 或 A股/港股/美股组合。
FUTU_OPEND_HOST = os.environ.get("FUTU_OPEND_HOST", "").strip()
FUTU_OPEND_PORT = _positive_int_env("FUTU_OPEND_PORT", 11111, 1)
FUTU_OPEND_MARKETS = os.environ.get("FUTU_OPEND_MARKETS", "A,H,US").strip()
FUTU_SNAPSHOT_BATCH_SIZE = min(
    400, _positive_int_env("FUTU_SNAPSHOT_BATCH_SIZE", 400, 20)
)

# 全市场榜单的最低流动性门槛。默认只过滤极低价和几乎无成交的美股，
# 防止反向拆股/壳股长期占据涨跌榜；原始全市场行数仍会写入质量摘要。
HOT_STOCK_US_MIN_PRICE = _nonnegative_float_env("HOT_STOCK_US_MIN_PRICE", 1.0)
HOT_STOCK_US_MIN_VOLUME = _positive_int_env("HOT_STOCK_US_MIN_VOLUME", 10000, 0)

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
