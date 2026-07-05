# -*- coding: utf-8 -*-
"""
持仓导入与分析。

第一版支持:
- 从 OCR/粘贴文本中提取基金/股票候选持仓
- 调用真实 OCR 服务的接入点
- 汇总用户持仓构成

注意: OCR 结果必须由用户确认后再入库，避免截图识别误差直接污染持仓。
"""

from __future__ import annotations

import base64
import os
import re
from io import BytesIO

import storage


_CODE_RE = re.compile(r"(?<![\d.])(\d{6}|\d{5})(?![\d.])")
_NUM_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")


def _num(text):
    if text is None:
        return None
    try:
        raw = str(text).replace(",", "").replace("%", "").strip()
        if not raw or raw in ("-", "--"):
            return None
        return float(raw)
    except ValueError:
        return None


def _clean_name(line: str, code: str) -> str:
    text = line.replace(code, " ")
    text = re.sub(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?", " ", text)
    text = re.sub(r"[|:：,，;；/\\()\[\]【】]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    stop_words = [
        "持有", "收益", "收益率", "持仓", "金额", "市值", "份额", "成本", "昨日收益",
        "累计收益", "日收益", "估算", "基金", "股票",
    ]
    parts = [p for p in text.split(" ") if p and p not in stop_words]
    return " ".join(parts[:4]).strip()


def _infer_asset_type(code: str, context: str) -> str:
    if len(code) == 5:
        return "stock"
    if any(word in context for word in ("基金", "混合", "债券", "指数", "ETF", "QDII", "LOF", "FOF")):
        return "fund"
    return "fund"


def _infer_market(code: str, asset_type: str) -> str:
    if asset_type == "fund":
        return "基金"
    if len(code) == 5:
        return "港股"
    if code.startswith(("6", "0", "3", "8", "4")):
        return "A股"
    return ""


def _find_labeled_number(context: str, labels: list[str]):
    for label in labels:
        idx = context.find(label)
        if idx < 0:
            continue
        segment = context[idx:idx + 60]
        match = _NUM_RE.search(segment)
        if match:
            return _num(match.group(0))
    return None


def parse_holdings_text(text: str) -> dict:
    raw_text = str(text or "").strip()
    if not raw_text:
        return {"raw_text": "", "candidates": [], "warnings": ["识别文本为空"]}

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    candidates = []
    seen = set()
    for idx, line in enumerate(lines):
        for match in _CODE_RE.finditer(line):
            code = match.group(1)
            if code in seen:
                continue
            context = " ".join(lines[max(0, idx - 2):idx + 5])
            value_context = " ".join(lines[idx:idx + 5])
            asset_type = _infer_asset_type(code, context)
            name = _clean_name(line, code)
            if not name and idx > 0:
                name = _clean_name(lines[idx - 1], code)
            amount = _find_labeled_number(value_context, ["持有金额", "持仓金额", "持有市值", "市值", "资产", "金额"])
            cost = _find_labeled_number(value_context, ["持仓成本", "成本", "本金", "投入"])
            profit = _find_labeled_number(value_context, ["持有收益", "累计收益", "收益", "盈亏"])
            profit_rate = _find_labeled_number(value_context, ["收益率", "持有收益率", "盈亏率"])
            shares = _find_labeled_number(value_context, ["持有份额", "份额", "持仓份额"])
            candidates.append({
                "asset_type": asset_type,
                "market": _infer_market(code, asset_type),
                "code": code,
                "name": name,
                "amount": amount,
                "cost": cost,
                "profit": profit,
                "profit_rate": profit_rate,
                "shares": shares,
                "source": "ocr_text",
                "raw_text": context[:1000],
                "confidence_note": "基于截图文字规则解析，请保存前核对名称、金额和收益。",
            })
            seen.add(code)

    warnings = []
    if not candidates:
        warnings.append("未识别到 6 位基金/股票代码或 5 位港股代码，请尝试更清晰截图或手动粘贴文字。")
    return {"raw_text": raw_text, "candidates": candidates, "warnings": warnings}


def holdings_summary(items: list[dict]) -> dict:
    total_amount = sum((item.get("amount") or 0) for item in items)
    by_type = {}
    by_market = {}
    for item in items:
        amount = item.get("amount") or 0
        asset_type = item.get("asset_type") or "unknown"
        market = item.get("market") or "未分类"
        by_type[asset_type] = by_type.get(asset_type, 0) + amount
        by_market[market] = by_market.get(market, 0) + amount
    top_items = sorted(items, key=lambda x: x.get("amount") or 0, reverse=True)[:10]
    top_amount = top_items[0].get("amount") if top_items else None
    concentration = top_amount / total_amount * 100 if total_amount and top_amount is not None else None
    return {
        "count": len(items),
        "total_amount": round(total_amount, 2) if total_amount else None,
        "by_asset_type": [
            {"name": k, "amount": round(v, 2), "ratio": round(v / total_amount * 100, 2) if total_amount else None}
            for k, v in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "by_market": [
            {"name": k, "amount": round(v, 2), "ratio": round(v / total_amount * 100, 2) if total_amount else None}
            for k, v in sorted(by_market.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "top_concentration": round(concentration, 2) if concentration is not None else None,
        "top_items": top_items,
        "risk_notes": _risk_notes(items, total_amount, concentration),
    }


def _risk_notes(items: list[dict], total_amount: float, concentration):
    notes = []
    fund_count = sum(1 for item in items if item.get("asset_type") == "fund")
    stock_count = sum(1 for item in items if item.get("asset_type") == "stock")
    if concentration is not None and concentration >= 40:
        notes.append("单一持仓占比较高，需要关注集中度风险。")
    if fund_count >= 2:
        notes.append("可进一步分析基金重仓股和行业暴露，检查是否买了多只相似基金。")
    if stock_count >= 3:
        notes.append("股票持仓可按行业和市场拆分，检查是否集中在同一板块。")
    if total_amount <= 0:
        notes.append("多数持仓缺少金额，补全后才能计算真实配置比例。")
    return notes


def list_holdings() -> dict:
    items = storage.list_holdings()
    return {"items": items, "summary": holdings_summary(items)}


def save_holdings(items: list[dict]) -> dict:
    saved = [storage.upsert_holding(item) for item in items]
    all_items = storage.list_holdings()
    return {"saved": saved, "items": all_items, "summary": holdings_summary(all_items)}


def delete_holding(holding_id: int) -> bool:
    return storage.delete_holding(holding_id)


def recognize_image_with_aliyun(image_bytes: bytes, content_type: str = "image/png") -> str:
    access_key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.getenv("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
    endpoint = os.getenv("ALIYUN_OCR_ENDPOINT", "ocr-api.cn-hangzhou.aliyuncs.com")
    if not access_key_id or not access_key_secret:
        raise RuntimeError("未配置阿里云 OCR AccessKey。请设置 ALIBABA_CLOUD_ACCESS_KEY_ID 和 ALIBABA_CLOUD_ACCESS_KEY_SECRET。")
    try:
        from alibabacloud_ocr_api20210707.client import Client
        from alibabacloud_ocr_api20210707 import models as ocr_models
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_tea_util import models as util_models
    except Exception as exc:
        raise RuntimeError(f"缺少阿里云 OCR SDK 依赖，请安装 requirements.txt:{exc}") from exc

    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        endpoint=endpoint,
    )
    client = Client(config)
    request = ocr_models.RecognizeGeneralRequest(body=BytesIO(image_bytes))
    runtime = util_models.RuntimeOptions()
    response = client.recognize_general_with_options(request, runtime)
    body = getattr(response, "body", None)
    data = getattr(body, "data", None)
    if isinstance(data, dict):
        return data.get("content") or ""
    content = getattr(data, "content", None)
    if content:
        return content
    # 某些 SDK 版本返回 JSON 字符串，这里保留一层兜住真实响应。
    raw = str(data or "")
    if raw:
        return raw
    return ""


def recognize_image(image_bytes: bytes, content_type: str = "image/png") -> dict:
    text = recognize_image_with_aliyun(image_bytes, content_type)
    parsed = parse_holdings_text(text)
    parsed["ocr_provider"] = "aliyun_ocr"
    return parsed
