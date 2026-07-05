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
import json
import os
import re
import urllib.request
from io import BytesIO

import storage


_CODE_RE = re.compile(r"(?<![\d.])(\d{6}|\d{5})(?![\d.])")
_NUM_RE = re.compile(r"[-+]?\d+(?:[,，]\d{3})*(?:\.\d+)?")
_FUND_META_CACHE: dict[str, dict] = {}
_FUND_SEARCH_ROWS: list[dict] | None = None
_FUND_SEARCH_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
_INLINE_VALUE_RE = re.compile(r"[-+]?\d+(?:[,，]\d{3})*(?:\.\d+)?%?")


def _num(text):
    if text is None:
        return None
    try:
        raw = str(text).replace("，", ",").replace(",", "").replace("%", "").strip()
        if not raw or raw in ("-", "--"):
            return None
        return float(raw)
    except ValueError:
        return None


def _word_box(word: dict) -> dict:
    pos = word.get("pos") or []
    xs = [p.get("x", 0) for p in pos]
    ys = [p.get("y", 0) for p in pos]
    if not xs or not ys:
        x = word.get("x") or 0
        y = word.get("y") or 0
        width = word.get("width") or 0
        height = word.get("height") or 0
        xs = [x, x + width]
        ys = [y, y + height]
    return {
        "text": str(word.get("word") or "").strip(),
        "minx": min(xs),
        "maxx": max(xs),
        "miny": min(ys),
        "maxy": max(ys),
        "cx": (min(xs) + max(xs)) / 2,
        "cy": (min(ys) + max(ys)) / 2,
    }


def _is_number_word(text: str) -> bool:
    cleaned = str(text or "").replace("，", ",").replace("%", "").strip()
    return bool(re.fullmatch(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", cleaned))


def _first_number(words: list[dict], *, minx=None, maxx=None, contains_percent=None, signed=None):
    rows = []
    for word in words:
        text = word["text"]
        if contains_percent is True and "%" not in text:
            continue
        if contains_percent is False and "%" in text:
            continue
        if signed is True and not text.startswith(("+", "-")):
            continue
        if not _is_number_word(text):
            continue
        if minx is not None and word["minx"] < minx:
            continue
        if maxx is not None and word["minx"] > maxx:
            continue
        rows.append(word)
    rows.sort(key=lambda w: (w["miny"], w["minx"]))
    return _num(rows[0]["text"]) if rows else None


def _number_words(words: list[dict], *, minx=None, maxx=None, y_min=None, contains_percent=None, signed=None):
    rows = []
    for word in words:
        text = word["text"]
        if contains_percent is True and "%" not in text:
            continue
        if contains_percent is False and "%" in text:
            continue
        if signed is True and not text.startswith(("+", "-")):
            continue
        if not _is_number_word(text):
            continue
        if y_min is not None and word["miny"] < y_min:
            continue
        if minx is not None and word["cx"] < minx:
            continue
        if maxx is not None and word["cx"] > maxx:
            continue
        rows.append(word)
    rows.sort(key=lambda w: (w["miny"], w["minx"]))
    return rows


def _pick_number(words: list[dict], *, minx=None, maxx=None, y_min=None, contains_percent=None, signed=None):
    rows = _number_words(
        words,
        minx=minx,
        maxx=maxx,
        y_min=y_min,
        contains_percent=contains_percent,
        signed=signed,
    )
    return _num(rows[0]["text"]) if rows else None


def _find_label(words: list[dict], labels: tuple[str, ...]):
    hits = [w for w in words if any(label in w["text"] for label in labels)]
    hits.sort(key=lambda w: (w["miny"], w["minx"]))
    return hits[0] if hits else None


def _layout_columns(block: list[dict]) -> dict:
    asset = _find_label(block, ("资产", "市值", "金额"))
    yesterday = _find_label(block, ("昨日收益", "日收益"))
    profit = _find_label(block, ("持仓收益/率", "持仓收益", "收益/率", "收益率"))
    if asset and yesterday and profit:
        left_mid = (asset["cx"] + yesterday["cx"]) / 2
        right_mid = (yesterday["cx"] + profit["cx"]) / 2
        value_min_y = max(asset["maxy"], yesterday["maxy"], profit["maxy"]) + 8
        return {
            "amount": (-10**9, left_mid),
            "yesterday_profit": (left_mid, right_mid),
            "profit": (right_mid, 10**9),
            "value_min_y": value_min_y,
            "mode": "labels",
        }
    return {
        "amount": (-10**9, 360),
        "yesterday_profit": (450, 850),
        "profit": (880, 10**9),
        "value_min_y": None,
        "mode": "fallback",
    }


def _lookup_fund_meta(code: str) -> dict:
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return {}
    if code in _FUND_META_CACHE:
        return _FUND_META_CACHE[code]
    try:
        global _FUND_SEARCH_ROWS
        if _FUND_SEARCH_ROWS is None:
            request = urllib.request.Request(
                _FUND_SEARCH_URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                    ),
                    "Referer": "https://fund.eastmoney.com/",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                text = response.read().decode("utf-8", errors="ignore")
            match = re.search(r"var\s+r\s*=\s*(\[.*\]);?\s*$", text, re.S)
            if not match:
                raise RuntimeError("东方财富基金代码库返回格式异常")
            raw_rows = json.loads(match.group(1))
            _FUND_SEARCH_ROWS = [
                {
                    "code": str(row[0]),
                    "abbr": str(row[1] or ""),
                    "name": str(row[2] or ""),
                    "type": str(row[3] or ""),
                    "pinyin": str(row[4] or ""),
                }
                for row in raw_rows
                if len(row) >= 5
            ]
        exact = next((item for item in _FUND_SEARCH_ROWS if item.get("code") == code), None)
        meta = {
            "code": code,
            "name": (exact or {}).get("name") or "",
            "type": (exact or {}).get("type") or "",
            "source": "东方财富基金代码搜索库",
            "source_url": _FUND_SEARCH_URL,
        }
    except Exception as exc:
        meta = {"code": code, "name": "", "type": "", "error": str(exc)[:160]}
    _FUND_META_CACHE[code] = meta
    return meta


def _compact_name_for_compare(text: str) -> str:
    return re.sub(r"[\s（）()\[\]【】<>《》·,，:：;；/\\_-]+", "", str(text or "")).upper()


def _apply_verified_fund_name(candidate: dict, warnings: list[str]):
    if candidate.get("asset_type") != "fund" or not re.fullmatch(r"\d{6}", str(candidate.get("code") or "")):
        return
    meta = _lookup_fund_meta(candidate["code"])
    verified_name = meta.get("name") or ""
    if verified_name:
        ocr_name = candidate.get("name") or ""
        if ocr_name and _compact_name_for_compare(ocr_name) != _compact_name_for_compare(verified_name):
            candidate["ocr_name"] = ocr_name
            warnings.append(f"{candidate['code']} OCR 名称已用真实基金代码库校正为: {verified_name}")
        candidate["name"] = verified_name
        candidate["fund_type"] = meta.get("type") or ""
        candidate["name_source"] = meta.get("source") or ""
        candidate["name_source_url"] = meta.get("source_url") or ""
        candidate["source"] = f"{candidate.get('source') or 'ocr'}+fund_code_lookup"
    elif meta.get("error"):
        warnings.append(f"{candidate['code']} 基金名称反查失败: {meta['error']}")


def _inline_name_from_prefix(prefix: str, code: str) -> str:
    text = str(prefix or "")
    text = re.sub(r"^.*(?:\d{2}-\d{2}|[-+]?\d+(?:[,，]\d{3})*(?:\.\d+)?%)\s+", "", text)
    text = re.sub(r"^.*(?:提醒|定投|资产|昨日收益|持仓收益/率)\s+", "", text)
    return _clean_name(text, code)


def _inline_values_after_code(segment: str) -> tuple[float | None, float | None, float | None, float | None, bool]:
    text = str(segment or "")
    label_idx = text.find("持仓收益/率")
    if label_idx >= 0:
        value_text = text[label_idx + len("持仓收益/率"):]
    else:
        label_idx = text.find("资产")
        value_text = text[label_idx + len("资产"):] if label_idx >= 0 else text
    tokens = [m.group(0) for m in _INLINE_VALUE_RE.finditer(value_text)]
    amount = _num(tokens[0]) if len(tokens) >= 1 else None
    yesterday = _num(tokens[1]) if len(tokens) >= 2 else None
    profit = _num(tokens[2]) if len(tokens) >= 3 else None
    profit_rate = _num(tokens[3]) if len(tokens) >= 4 and "%" in tokens[3] else None
    bad_rate_token = len(tokens) >= 4 and "%" not in tokens[3]
    return amount, yesterday, profit, profit_rate, bad_rate_token


def _parse_inline_card_text(raw_text: str) -> dict | None:
    text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
    code_matches = list(_CODE_RE.finditer(text))
    if len(code_matches) < 2:
        return None
    candidates = []
    warnings = []
    seen = set()
    for idx, match in enumerate(code_matches):
        code = match.group(1)
        if code in seen:
            continue
        prev_end = code_matches[idx - 1].end() if idx > 0 else 0
        next_start = code_matches[idx + 1].start() if idx + 1 < len(code_matches) else len(text)
        prefix = text[prev_end:match.start()]
        segment = text[match.end():next_start]
        name = _inline_name_from_prefix(prefix, code)
        amount, yesterday, profit, profit_rate, bad_rate_token = _inline_values_after_code(segment)
        asset_type = _infer_asset_type(code, name)
        candidate = {
            "asset_type": asset_type,
            "market": _infer_market(code, asset_type),
            "code": code,
            "name": name,
            "amount": amount,
            "cost": None,
            "yesterday_profit": yesterday,
            "profit": profit,
            "profit_rate": profit_rate,
            "shares": None,
            "source": "ocr_inline_text",
            "raw_text": (prefix + " " + code + " " + segment)[:1000],
            "confidence_note": "基于单行 OCR 文本按代码切分解析，名称会用真实基金代码库校正，请保存前核对金额和收益。",
        }
        _apply_verified_fund_name(candidate, warnings)
        if bad_rate_token and profit is not None:
            warnings.append(f"{code} 的收益率未可靠识别，请手动核对。")
        candidates.append(candidate)
        seen.add(code)
    return {
        "raw_text": raw_text,
        "candidates": candidates,
        "warnings": warnings,
        "layout_parser": "inline_card_text",
    }


def _parse_aliyun_words_payload(raw_text: str) -> dict | None:
    try:
        payload = json.loads(raw_text)
    except Exception:
        return None
    words_raw = payload.get("prism_wordsInfo") or payload.get("wordsInfo") or []
    if not isinstance(words_raw, list) or not words_raw:
        return None
    words = [_word_box(w) for w in words_raw if str(w.get("word") or "").strip()]
    code_words = [w for w in words if re.fullmatch(r"\d{5,6}", w["text"])]
    code_words.sort(key=lambda w: (w["miny"], w["minx"]))
    candidates = []
    warnings = []
    seen = set()
    for idx, code_word in enumerate(code_words):
        code = code_word["text"]
        if code in seen:
            continue
        block_start = code_word["miny"] - 30
        block_end = code_words[idx + 1]["miny"] - 20 if idx + 1 < len(code_words) else 10**9
        block = [w for w in words if block_start <= w["miny"] < block_end]

        header_words = [
            w for w in block
            if abs(w["cy"] - code_word["cy"]) <= 65
            and w["maxx"] <= code_word["minx"] + 30
            and w["text"] != code
            and not _is_number_word(w["text"])
            and w["text"] not in (">", "定投", "资产", "昨日收益", "持仓收益/率")
        ]
        header_words.sort(key=lambda w: w["minx"])
        name = " ".join(w["text"] for w in header_words).strip()
        if not name:
            name = _clean_name(" ".join(w["text"] for w in block[:3]), code)

        columns = _layout_columns(block)
        value_min_y = columns["value_min_y"] or code_word["miny"] + 90
        amount_minx, amount_maxx = columns["amount"]
        yesterday_minx, yesterday_maxx = columns["yesterday_profit"]
        profit_minx, profit_maxx = columns["profit"]
        amount = _pick_number(
            block,
            minx=amount_minx,
            maxx=amount_maxx,
            y_min=value_min_y,
            contains_percent=False,
        )
        profit = _pick_number(
            block,
            minx=profit_minx,
            maxx=profit_maxx,
            y_min=value_min_y,
            contains_percent=False,
            signed=True,
        )
        if profit is None:
            profit = _pick_number(
                block,
                minx=profit_minx,
                maxx=profit_maxx,
                y_min=value_min_y,
                contains_percent=False,
            )
        profit_rate = _pick_number(
            block,
            minx=profit_minx,
            maxx=profit_maxx,
            y_min=value_min_y,
            contains_percent=True,
        )
        yesterday = _pick_number(
            block,
            minx=yesterday_minx,
            maxx=yesterday_maxx,
            y_min=value_min_y,
            contains_percent=False,
        )
        asset_type = _infer_asset_type(code, name)
        candidate = {
            "asset_type": asset_type,
            "market": _infer_market(code, asset_type),
            "code": code,
            "name": name,
            "amount": amount,
            "cost": None,
            "profit": profit,
            "profit_rate": profit_rate,
            "shares": None,
            "source": "aliyun_ocr_layout",
            "raw_text": " ".join(w["text"] for w in block)[:1000],
            "confidence_note": "基于阿里云 OCR 坐标和字段标题分组解析，名称用真实基金代码库校正，请保存前核对金额和收益。",
        }
        if yesterday is not None:
            candidate["yesterday_profit"] = yesterday
        _apply_verified_fund_name(candidate, warnings)
        if profit_rate is None and profit is not None:
            warnings.append(f"{code} 的收益率未可靠识别，请手动核对。")
        candidates.append(candidate)
        seen.add(code)

    if not candidates:
        return None
    return {
        "raw_text": payload.get("content") or raw_text,
        "candidates": candidates,
        "warnings": warnings,
        "layout_parser": "aliyun_prism_wordsInfo",
    }


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

    layout_result = _parse_aliyun_words_payload(raw_text)
    if layout_result:
        return layout_result

    inline_result = _parse_inline_card_text(raw_text)
    if inline_result:
        return inline_result

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    candidates = []
    warnings = []
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
            candidate = {
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
            }
            _apply_verified_fund_name(candidate, warnings)
            candidates.append(candidate)
            seen.add(code)

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
        if data.get("prism_wordsInfo") or data.get("wordsInfo"):
            return json.dumps(data, ensure_ascii=False)
        return data.get("content") or json.dumps(data, ensure_ascii=False)
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
