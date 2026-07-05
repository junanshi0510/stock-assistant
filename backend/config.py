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

# A股 / 港股 —— Tushare Pro 的 token
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 美股 —— Polygon.io 的 API Key
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# 美股 —— Alpha Vantage 的 API Key
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
