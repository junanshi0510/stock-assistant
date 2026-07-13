# -*- coding: utf-8 -*-
"""
自选股打分变化监控
==================
后台线程定时扫描自选股,检测打分穿越关键档位时记录提醒。

档位定义(与前端徽章逻辑一致):
    看涨(bullish): score >= 65
    看跌(bearish): score <= 35
    中性(neutral): 36 <= score <= 64

触发逻辑:
    - 从中性/看跌 → 看涨(≥65): 发"进入看涨区"提醒
    - 从中性/看涨 → 看跌(≤35): 发"进入看跌区"提醒
    - 从看涨/看跌 → 中性(36-64): 发"回到中性区"提醒

为避免重复提醒,在内存记录每只股票的上次打分和档位。
启动时恢复初始状态(假设全中性,首次扫描只记录,不报)。
"""

import logging
import threading
import time

import data_fetch
import analysis
import storage

logger = logging.getLogger("monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# 内存缓存:记录每只股票的上次打分和档位,key="market:symbol"
_last_state = {}
_lock = threading.Lock()


def _classify(score: float) -> str:
    """根据打分判断档位。"""
    if score >= 65:
        return "bullish"
    elif score <= 35:
        return "bearish"
    else:
        return "neutral"


def _scan_once(user_id: str | None = None):
    """扫描一次自选股,检测打分变化。"""
    try:
        items = storage.list_all_watchlist()
        if user_id is not None:
            items = [item for item in items if item.get("user_id") == user_id]
        if not items:
            logger.info("自选股为空,跳过本次扫描。")
            return

        logger.info(f"开始扫描 {len(items)} 只自选股...")
        for it in items:
            user_id = str(it.get("user_id") or "default")
            market, symbol = it["market"], it["symbol"]
            key = f"{user_id}:{market}:{symbol}"

            try:
                df = data_fetch.get_history_months(market, symbol, 12, fetch_months=12)
                r = analysis.score_only(df)
                score = r["score"]
                zone = _classify(score)
            except Exception as e:
                logger.warning(f"  {key} 抓取失败: {e}")
                continue

            with _lock:
                prev = _last_state.get(key)
                _last_state[key] = {"score": score, "zone": zone}

            # 首次扫描,只记录不报(避免启动时误报一堆)
            if prev is None:
                logger.info(f"  {key} 首次扫描: score={score} zone={zone}")
                continue

            prev_zone = prev["zone"]
            if zone == prev_zone:
                continue  # 档位未变,不报

            # 档位变化 → 记录提醒
            if zone == "bullish":
                msg = f"进入看涨区(打分 {score},前次 {prev['score']})"
            elif zone == "bearish":
                msg = f"进入看跌区(打分 {score},前次 {prev['score']})"
            else:  # neutral
                msg = f"回到中性区(打分 {score},前次 {prev['score']})"

            storage.add_alert(market, symbol, zone, score, msg, user_id=user_id)
            logger.info(f"  ⚠️ {key} 档位变化: {prev_zone} → {zone}, {msg}")

        logger.info("本次扫描完成。")
    except Exception as e:
        logger.error(f"扫描出错: {e}", exc_info=True)


def _monitor_loop(interval_seconds: int = 3600):
    """后台循环:每 interval_seconds 扫描一次。"""
    logger.info(f"监控线程启动,扫描间隔 {interval_seconds} 秒({interval_seconds//60} 分钟)。")
    while True:
        _scan_once()
        time.sleep(interval_seconds)


_thread = None


def start_monitor(interval_seconds: int = 3600):
    """启动后台监控线程(daemon,随主程序退出)。只能调用一次。"""
    global _thread
    if _thread is not None:
        logger.warning("监控线程已在运行。")
        return
    _thread = threading.Thread(target=_monitor_loop, args=(interval_seconds,), daemon=True)
    _thread.start()
    logger.info("监控线程已启动(后台 daemon)。")


def trigger_scan_now(user_id: str | None = None):
    """手动触发一次扫描(同步,阻塞到扫描完成)。用于测试或用户手动刷新。"""
    _scan_once(user_id=user_id)
