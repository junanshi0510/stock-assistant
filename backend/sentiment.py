# -*- coding: utf-8 -*-
"""
新闻舆情情绪模块
================
- A股:用 akshare 取东方财富个股新闻,用【中文金融情绪词典】粗略打分。
- 美股:用 Alpha Vantage 的 NEWS_SENTIMENT(需免费 Key),自带情绪分。
- 港股:免费新闻源有限,暂不支持。

⚠️ 词典法情绪只是【粗略】参考:它只数关键词,不理解语义和反讽,准确度有限。
新闻情绪更多反映"市场情绪温度",不预测涨跌。
"""

import data_fetch  # 确保 pandas 兼容设置已生效
import requests
import config

# —— 中文金融情绪词典(精简版)——
_POS = [
    "涨停", "大涨", "飙升", "攀升", "走高", "反弹", "回升", "新高", "突破", "利好",
    "增长", "增持", "盈利", "扭亏", "超预期", "看好", "强劲", "受益", "中标", "签约",
    "合作", "分红", "回购", "上调", "提升", "机会", "复苏", "订单", "放量", "涨价",
]
_NEG = [
    "跌停", "大跌", "暴跌", "重挫", "跳水", "闪崩", "下跌", "下滑", "下降", "利空",
    "亏损", "减持", "风险", "警示", "退市", "违规", "处罚", "调查", "诉讼", "下调",
    "低于预期", "爆雷", "暴雷", "承压", "套牢", "萎缩", "停牌", "质押", "减值", "裁员",
]


def _label(text: str):
    p = sum(text.count(w) for w in _POS)
    n = sum(text.count(w) for w in _NEG)
    if p > n:
        return "利好", p, n
    if n > p:
        return "利空", p, n
    return "中性", p, n


def _news_a(symbol, limit=15):
    import akshare as ak
    df = ak.stock_news_em(symbol=symbol)
    items = []
    tot_p = tot_n = 0
    for _, row in df.head(limit).iterrows():
        title = str(row.get("新闻标题", ""))
        content = str(row.get("新闻内容", ""))[:80]
        lab, p, n = _label(title + content)
        tot_p += p; tot_n += n
        items.append({
            "title": title,
            "time": str(row.get("发布时间", "")),
            "source": str(row.get("文章来源", "")),
            "url": str(row.get("新闻链接", "")),
            "label": lab,
        })
    return items, tot_p, tot_n


def _news_us(symbol, limit=15):
    if not config.ALPHAVANTAGE_API_KEY:
        raise PermissionError("美股新闻情绪需要 Alpha Vantage Key,请在 backend/config.py 配置后重试。")
    r = requests.get("https://www.alphavantage.co/query", params={
        "function": "NEWS_SENTIMENT", "tickers": symbol.upper(),
        "limit": limit, "apikey": config.ALPHAVANTAGE_API_KEY}, timeout=20)
    r.raise_for_status()
    js = r.json()
    feed = js.get("feed")
    if not feed:
        msg = js.get("Note") or js.get("Information") or "未取到新闻"
        raise RuntimeError(str(msg)[:120])

    items = []
    scores = []
    for art in feed[:limit]:
        # 找到针对本 ticker 的情绪分
        tsent = None
        for t in art.get("ticker_sentiment", []):
            if t.get("ticker", "").upper() == symbol.upper():
                tsent = float(t.get("ticker_sentiment_score", 0))
                break
        if tsent is None:
            tsent = float(art.get("overall_sentiment_score", 0))
        scores.append(tsent)
        lab = "利好" if tsent > 0.15 else "利空" if tsent < -0.15 else "中性"
        items.append({
            "title": art.get("title", ""),
            "time": art.get("time_published", ""),
            "source": art.get("source", ""),
            "url": art.get("url", ""),
            "label": lab,
        })
    return items, scores


def get_sentiment(market: str, symbol: str) -> dict:
    symbol = symbol.strip()
    if market == "A股":
        items, p, n = _news_a(symbol)
        if not items:
            return {"available": False, "message": "未取到相关新闻。"}
        # 情绪分:50 中性,(p-n) 归一化
        denom = p + n + 1
        score = round(50 + 50 * (p - n) / denom, 1)
        score = max(0.0, min(100.0, score))
    elif market == "美股":
        items, scores = _news_us(symbol)
        if not items:
            return {"available": False, "message": "未取到相关新闻。"}
        avg = sum(scores) / len(scores) if scores else 0
        # AV 情绪分约 -0.35..0.35,映射到 0-100
        score = round(max(0.0, min(100.0, 50 + avg * 140)), 1)
    elif market == "港股":
        return {"available": False, "message": "港股暂无免费新闻情绪数据源。"}
    else:
        raise ValueError(f"不支持的市场:{market}")

    pos = sum(1 for i in items if i["label"] == "利好")
    neg = sum(1 for i in items if i["label"] == "利空")
    mood = "偏乐观" if score >= 60 else "偏悲观" if score <= 40 else "中性"
    return {
        "available": True,
        "market": market, "symbol": symbol,
        "score": score, "mood": mood,
        "pos_count": pos, "neg_count": neg, "total": len(items),
        "news": items,
    }
