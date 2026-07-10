import { useEffect, useState } from 'react'
import { analyze, fetchPresets, searchUs } from '../api/market'
import { addWatch, removeWatch } from '../api/portfolio'
import { dirClass, probColor } from '../helpers'
import ScoreRing from '../ScoreRing'
import CandleChart from '../CandleChart'
import { QuoteSection, FundamentalsSection, MLSection, NewsSection, CompareSection } from './InsightSections'

const PLACEHOLDER = { 'A股': '如 600519 / 000001', '港股': '如 00700', '美股': '如 AAPL / TSLA' }

function pct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function price(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toFixed(n >= 100 ? 2 : 3)
}

function buildRiskPlan(result) {
  const candles = result?.candles || []
  if (candles.length < 20) return null
  const recent20 = candles.slice(-20)
  const recent14 = candles.slice(-14)
  const latest = candles[candles.length - 1]
  const close = Number(latest.close)
  const support = Math.min(...recent20.map((c) => Number(c.low)))
  const resistance = Math.max(...recent20.map((c) => Number(c.high)))
  const atr = recent14.reduce((sum, c) => sum + (Number(c.high) - Number(c.low)), 0) / recent14.length
  const atrPct = close ? atr / close * 100 : 0
  const stopLine = close * (1 - Math.min(0.18, Math.max(0.04, atrPct * 2 / 100)))
  const upside = close ? (resistance / close - 1) * 100 : null
  const downside = close ? (support / close - 1) * 100 : null
  const riskLevel = atrPct >= 5 ? '高波动' : atrPct >= 3 ? '中波动' : '低波动'
  const stance = result.score >= 65 && atrPct < 4
    ? '可正常观察'
    : result.score >= 50 && atrPct < 5
      ? '轻仓观察'
      : '先观望'
  return { close, support, resistance, stopLine, atrPct, upside, downside, riskLevel, stance }
}

function buildTechnicalCheck(result) {
  const candles = result?.candles || []
  if (candles.length < 30) return null
  const latest = candles[candles.length - 1]
  const close = Number(latest.close)
  const closes = candles.map((c) => Number(c.close)).filter((v) => !Number.isNaN(v))
  const vols = candles.map((c) => Number(c.volume)).filter((v) => !Number.isNaN(v))
  const recent60 = candles.slice(-Math.min(60, candles.length))
  const high60 = Math.max(...recent60.map((c) => Number(c.high)))
  const low60 = Math.min(...recent60.map((c) => Number(c.low)))
  const rangePos = high60 > low60 ? (close - low60) / (high60 - low60) * 100 : null
  const ma20 = Number(latest.ma20)
  const ma60 = Number(latest.ma60)
  const aboveMa20 = close > ma20
  const aboveMa60 = close > ma60
  const latestVol = vols[vols.length - 1]
  const avgVol20 = vols.slice(-21, -1).reduce((a, b) => a + b, 0) / Math.min(20, Math.max(1, vols.length - 1))
  const volumeRatio = avgVol20 ? latestVol / avgVol20 : null
  const returns20 = closes.slice(-21).map((v, i, arr) => (i === 0 ? null : v / arr[i - 1] - 1)).filter((v) => v !== null)
  const mean20 = returns20.reduce((a, b) => a + b, 0) / returns20.length
  const vol20 = Math.sqrt(returns20.reduce((sum, r) => sum + Math.pow(r - mean20, 2), 0) / returns20.length) * Math.sqrt(252) * 100
  const ret5 = closes.length >= 6 ? (closes[closes.length - 1] / closes[closes.length - 6] - 1) * 100 : null
  let streak = 0
  for (let i = closes.length - 1; i > 0; i -= 1) {
    const diff = closes[i] - closes[i - 1]
    if (streak === 0) streak = diff > 0 ? 1 : diff < 0 ? -1 : 0
    else if (streak > 0 && diff > 0) streak += 1
    else if (streak < 0 && diff < 0) streak -= 1
    else break
  }
  const verdict = [
    aboveMa20 ? '站上20日线' : '低于20日线',
    aboveMa60 ? '站上60日线' : '低于60日线',
    volumeRatio >= 1.5 ? '放量' : volumeRatio <= 0.7 ? '缩量' : '量能正常',
  ].join(' · ')
  return { high60, low60, rangePos, aboveMa20, aboveMa60, volumeRatio, vol20, ret5, streak, verdict }
}

function buildVolumePriceStructure(result) {
  const candles = result?.candles || []
  if (candles.length < 45) return null
  const rows = []
  for (let i = 1; i < candles.length; i += 1) {
    const prevClose = Number(candles[i - 1].close)
    const close = Number(candles[i].close)
    const volume = Number(candles[i].volume)
    if (!prevClose || [close, volume].some(Number.isNaN)) continue
    rows.push({
      date: candles[i].date,
      ret: (close / prevClose - 1) * 100,
      close,
      volume,
    })
  }
  if (rows.length < 40) return null
  const avg = (list) => list.length ? list.reduce((sum, n) => sum + n, 0) / list.length : null
  const recent20 = rows.slice(-20)
  const prev20 = rows.slice(-40, -20)
  const recent120 = rows.slice(-Math.min(120, rows.length))
  const latest = recent20[recent20.length - 1]
  const avgVol20 = avg(recent20.map((r) => r.volume))
  const avgVolPrev20 = avg(prev20.map((r) => r.volume))
  const volumeTrend = avgVol20 && avgVolPrev20 ? (avgVol20 / avgVolPrev20 - 1) * 100 : null
  const startClose = rows[rows.length - 21]?.close
  const ret20 = startClose ? (latest.close / startClose - 1) * 100 : null
  const upVol = avg(recent20.filter((r) => r.ret > 0).map((r) => r.volume))
  const downVol = avg(recent20.filter((r) => r.ret < 0).map((r) => r.volume))
  const upDownVolRatio = upVol && downVol ? upVol / downVol : null
  const heavyThreshold = avgVol20 ? avgVol20 * 1.2 : null
  const quietThreshold = avgVol20 ? avgVol20 * 0.7 : null
  const accumulationDays = heavyThreshold ? recent20.filter((r) => r.ret > 0 && r.volume >= heavyThreshold).length : 0
  const distributionDays = heavyThreshold ? recent20.filter((r) => r.ret < 0 && r.volume >= heavyThreshold).length : 0
  const dryUpDays = quietThreshold ? recent20.filter((r) => r.volume <= quietThreshold).length : 0
  const sortedVols = recent120.map((r) => r.volume).sort((a, b) => a - b)
  const latestVolRank = sortedVols.length
    ? sortedVols.filter((v) => v <= latest.volume).length / sortedVols.length * 100
    : null
  const state = ret20 > 2 && volumeTrend > 10
    ? '价涨量增'
    : ret20 > 2 && volumeTrend < -10
      ? '价涨量缩'
      : ret20 < -2 && volumeTrend > 10
        ? '价跌放量'
        : ret20 < -2 && volumeTrend < -10
          ? '价跌量缩'
          : '量价平衡'
  const verdict = [
    state,
    upDownVolRatio >= 1.15 ? '上涨日成交更活跃' : upDownVolRatio <= 0.85 ? '下跌日成交更活跃' : '涨跌量能接近',
    latestVolRank >= 80 ? '最新量能偏高' : latestVolRank <= 20 ? '最新量能偏低' : '最新量能中性',
  ].join(' · ')
  return {
    state,
    verdict,
    ret20,
    volumeTrend,
    upDownVolRatio,
    accumulationDays,
    distributionDays,
    dryUpDays,
    latestVolRank,
  }
}

function buildTrendPhase(result) {
  const candles = result?.candles || []
  if (candles.length < 80) return null
  const latest = candles[candles.length - 1]
  const close = Number(latest.close)
  const ma20 = Number(latest.ma20)
  const ma60 = Number(latest.ma60)
  const ma20Past = Number(candles[candles.length - 21]?.ma20)
  const ma60Past = Number(candles[candles.length - 21]?.ma60)
  if ([close, ma20, ma60, ma20Past, ma60Past].some(Number.isNaN)) return null
  const closes = candles.map((c) => Number(c.close))
  const recent60 = candles.slice(-60)
  const first20 = recent60.slice(0, 20)
  const mid20 = recent60.slice(20, 40)
  const last20 = recent60.slice(40)
  const highOf = (list) => Math.max(...list.map((c) => Number(c.high)))
  const lowOf = (list) => Math.min(...list.map((c) => Number(c.low)))
  const highEarly = highOf(first20)
  const highMid = highOf(mid20)
  const highLate = highOf(last20)
  const lowEarly = lowOf(first20)
  const lowMid = lowOf(mid20)
  const lowLate = lowOf(last20)
  const higherHighs = highLate > highMid && highMid > highEarly
  const higherLows = lowLate > lowMid && lowMid > lowEarly
  const lowerHighs = highLate < highMid && highMid < highEarly
  const lowerLows = lowLate < lowMid && lowMid < lowEarly
  const high60 = highOf(recent60)
  const low60 = lowOf(recent60)
  const rangePosition = high60 > low60 ? (close - low60) / (high60 - low60) * 100 : null
  const ret20 = closes[closes.length - 21] ? (close / closes[closes.length - 21] - 1) * 100 : null
  const ret60 = closes[closes.length - 61] ? (close / closes[closes.length - 61] - 1) * 100 : null
  const ma20Slope = ma20Past ? (ma20 / ma20Past - 1) * 100 : null
  const ma60Slope = ma60Past ? (ma60 / ma60Past - 1) * 100 : null
  const aboveMa20 = close >= ma20
  const aboveMa60 = close >= ma60
  let phase = '震荡整理'
  if (aboveMa20 && aboveMa60 && ma20 >= ma60 && ma20Slope > 0 && ma60Slope >= 0 && higherHighs && higherLows) {
    phase = '上升趋势'
  } else if (!aboveMa20 && !aboveMa60 && ma20 <= ma60 && ma20Slope < 0 && ma60Slope <= 0 && lowerHighs && lowerLows) {
    phase = '下降趋势'
  } else if (rangePosition >= 70 && Math.abs(ret20 ?? 0) < 5) {
    phase = '高位整理'
  } else if (rangePosition <= 35 && ma20Slope > 0 && ret20 > 0) {
    phase = '低位修复'
  } else if (aboveMa20 && !aboveMa60 && ma20Slope > 0) {
    phase = '反弹尝试'
  } else if (!aboveMa20 && aboveMa60 && ma20Slope < 0) {
    phase = '趋势转弱'
  }
  const verdict = [
    phase,
    aboveMa20 ? '收盘在MA20上方' : '收盘在MA20下方',
    aboveMa60 ? '收盘在MA60上方' : '收盘在MA60下方',
    higherHighs && higherLows ? '高低点同步抬升' : lowerHighs && lowerLows ? '高低点同步下移' : '高低点结构分化',
  ].join(' · ')
  return {
    phase,
    verdict,
    ret20,
    ret60,
    ma20Slope,
    ma60Slope,
    rangePosition,
    higherHighs,
    higherLows,
    lowerHighs,
    lowerLows,
  }
}

function buildVolatilityRegime(result) {
  const candles = result?.candles || []
  if (candles.length < 70) return null
  const rows = []
  for (let i = 1; i < candles.length; i += 1) {
    const prevClose = Number(candles[i - 1].close)
    const open = Number(candles[i].open)
    const high = Number(candles[i].high)
    const low = Number(candles[i].low)
    const close = Number(candles[i].close)
    if (!prevClose || [open, high, low, close].some(Number.isNaN)) continue
    rows.push({
      date: candles[i].date,
      ret: (close / prevClose - 1) * 100,
      amplitude: low ? (high / low - 1) * 100 : null,
      gap: (open / prevClose - 1) * 100,
      close,
    })
  }
  if (rows.length < 60) return null
  const avg = (list) => list.length ? list.reduce((sum, n) => sum + n, 0) / list.length : null
  const realizedVol = (list) => {
    if (list.length < 2) return null
    const mean = avg(list)
    return Math.sqrt(list.reduce((sum, n) => sum + Math.pow(n - mean, 2), 0) / list.length) * Math.sqrt(252)
  }
  const recent20 = rows.slice(-20)
  const recent60 = rows.slice(-60)
  const vol20 = realizedVol(recent20.map((r) => r.ret))
  const vol60 = realizedVol(recent60.map((r) => r.ret))
  const avgAmp20 = avg(recent20.map((r) => r.amplitude).filter((n) => n !== null))
  const avgAmp60 = avg(recent60.map((r) => r.amplitude).filter((n) => n !== null))
  const highVolDays = recent20.filter((r) => Math.abs(r.ret) >= 3).length
  const calmDays = recent20.filter((r) => Math.abs(r.ret) <= 1).length
  const gapRiskDays = recent20.filter((r) => Math.abs(r.gap) >= 1).length
  const latest = recent20[recent20.length - 1]
  const volRatio = vol20 && vol60 ? vol20 / vol60 : null
  const amplitudeRatio = avgAmp20 && avgAmp60 ? avgAmp20 / avgAmp60 : null
  const state = volRatio !== null && amplitudeRatio !== null && volRatio <= 0.75 && amplitudeRatio <= 0.85
    ? '波动收缩'
    : volRatio !== null && amplitudeRatio !== null && volRatio >= 1.25 && amplitudeRatio >= 1.1
      ? '波动扩张'
      : highVolDays >= 5
        ? '高波动扰动'
        : calmDays >= 12
          ? '低波动盘整'
          : '波动常态'
  const verdict = [
    state,
    volRatio !== null ? `20日/60日波动比 ${volRatio.toFixed(2)}x` : '波动比不足',
    gapRiskDays >= 4 ? '跳空风险偏高' : '跳空风险正常',
  ].join(' · ')
  return {
    state,
    verdict,
    vol20,
    vol60,
    volRatio,
    avgAmp20,
    avgAmp60,
    amplitudeRatio,
    highVolDays,
    calmDays,
    gapRiskDays,
    latestAmplitude: latest.amplitude,
    latestGap: latest.gap,
  }
}

function buildMaDeviation(result) {
  const candles = result?.candles || []
  if (candles.length < 80) return null
  const latest = candles[candles.length - 1]
  const close = Number(latest.close)
  const ma5 = Number(latest.ma5)
  const ma20 = Number(latest.ma20)
  const ma60 = Number(latest.ma60)
  if ([close, ma5, ma20, ma60].some(Number.isNaN)) return null
  const dist5 = ma5 ? (close / ma5 - 1) * 100 : null
  const dist20 = ma20 ? (close / ma20 - 1) * 100 : null
  const dist60 = ma60 ? (close / ma60 - 1) * 100 : null
  const recent = candles.slice(-Math.min(120, candles.length))
  const deviations = recent
    .map((c) => {
      const cClose = Number(c.close)
      const cMa20 = Number(c.ma20)
      return cMa20 ? (cClose / cMa20 - 1) * 100 : null
    })
    .filter((n) => n !== null && Number.isFinite(n))
  const absDist20 = Math.abs(dist20 ?? 0)
  const sortedAbs = deviations.map((n) => Math.abs(n)).sort((a, b) => a - b)
  const deviationRank = sortedAbs.length
    ? sortedAbs.filter((n) => n <= absDist20).length / sortedAbs.length * 100
    : null
  let daysAboveMa20 = 0
  let daysBelowMa20 = 0
  for (let i = candles.length - 1; i >= 0; i -= 1) {
    const cClose = Number(candles[i].close)
    const cMa20 = Number(candles[i].ma20)
    if (!cMa20 || Number.isNaN(cClose)) break
    if (cClose >= cMa20 && daysBelowMa20 === 0) daysAboveMa20 += 1
    else if (cClose < cMa20 && daysAboveMa20 === 0) daysBelowMa20 += 1
    else break
  }
  const state = dist20 >= 8 && deviationRank >= 85
    ? '上方乖离偏大'
    : dist20 <= -8 && deviationRank >= 85
      ? '下方乖离偏大'
      : deviationRank >= 75
        ? '乖离偏高'
        : deviationRank <= 30
          ? '贴近均线'
          : '乖离正常'
  const verdict = [
    state,
    dist20 >= 0 ? `高于MA20 ${dist20.toFixed(2)}%` : `低于MA20 ${Math.abs(dist20).toFixed(2)}%`,
    deviationRank !== null ? `近120日乖离分位 ${deviationRank.toFixed(1)}%` : '分位不足',
  ].join(' · ')
  return {
    state,
    verdict,
    dist5,
    dist20,
    dist60,
    deviationRank,
    daysAboveMa20,
    daysBelowMa20,
  }
}

function buildGapFollowUp(result) {
  const candles = result?.candles || []
  if (candles.length < 40) return null
  const gaps = []
  for (let i = 1; i < candles.length; i += 1) {
    const prevClose = Number(candles[i - 1].close)
    const open = Number(candles[i].open)
    if (!prevClose || Number.isNaN(open)) continue
    const gapPct = (open / prevClose - 1) * 100
    if (Math.abs(gapPct) < 1) continue
    const type = gapPct > 0 ? '向上跳空' : '向下跳空'
    let filled = false
    let fillDate = null
    const end = Math.min(candles.length - 1, i + 10)
    for (let j = i; j <= end; j += 1) {
      const high = Number(candles[j].high)
      const low = Number(candles[j].low)
      if (gapPct > 0 && low <= prevClose) {
        filled = true
        fillDate = candles[j].date
        break
      }
      if (gapPct < 0 && high >= prevClose) {
        filled = true
        fillDate = candles[j].date
        break
      }
    }
    gaps.push({
      date: candles[i].date,
      type,
      gapPct,
      basePrice: prevClose,
      open,
      filled,
      fillDate,
      daysChecked: end - i + 1,
    })
  }
  const recent = gaps.slice(-120)
  if (!recent.length) return null
  const upGaps = recent.filter((g) => g.gapPct > 0)
  const downGaps = recent.filter((g) => g.gapPct < 0)
  const filled = recent.filter((g) => g.filled)
  const unfilled = recent.filter((g) => !g.filled)
  const latest = recent[recent.length - 1]
  const largestUp = upGaps.length ? upGaps.reduce((a, b) => (b.gapPct > a.gapPct ? b : a), upGaps[0]) : null
  const largestDown = downGaps.length ? downGaps.reduce((a, b) => (b.gapPct < a.gapPct ? b : a), downGaps[0]) : null
  const fillRate = filled.length / recent.length * 100
  const state = unfilled.length >= 5
    ? '缺口遗留较多'
    : fillRate >= 70
      ? '回补倾向明显'
      : fillRate <= 35
        ? '回补偏弱'
        : '回补中性'
  return {
    state,
    total: recent.length,
    upCount: upGaps.length,
    downCount: downGaps.length,
    fillRate,
    unfilledCount: unfilled.length,
    latest,
    largestUp,
    largestDown,
    recentUnfilled: unfilled.slice(-3).reverse(),
  }
}

function buildReturnStats(result) {
  const candles = result?.candles || []
  if (candles.length < 30) return null
  const daily = []
  for (let i = 1; i < candles.length; i += 1) {
    const prev = Number(candles[i - 1].close)
    const close = Number(candles[i].close)
    const open = Number(candles[i].open)
    if (!prev || Number.isNaN(close) || Number.isNaN(open)) continue
    daily.push({
      date: candles[i].date,
      ret: (close / prev - 1) * 100,
      gap: (open / prev - 1) * 100,
    })
  }
  if (!daily.length) return null
  const ups = daily.filter((d) => d.ret > 0)
  const downs = daily.filter((d) => d.ret < 0)
  const avg = (arr, key) => arr.length ? arr.reduce((s, x) => s + x[key], 0) / arr.length : null
  const best = daily.reduce((a, b) => (b.ret > a.ret ? b : a), daily[0])
  const worst = daily.reduce((a, b) => (b.ret < a.ret ? b : a), daily[0])
  const recent60 = daily.slice(-60)
  const gapUpCount = recent60.filter((d) => d.gap >= 1).length
  const gapDownCount = recent60.filter((d) => d.gap <= -1).length

  const monthMap = new Map()
  candles.forEach((c) => {
    const key = String(c.date).slice(0, 7)
    if (!monthMap.has(key)) monthMap.set(key, [])
    monthMap.get(key).push(Number(c.close))
  })
  const monthly = Array.from(monthMap.entries()).slice(-12).map(([month, vals]) => {
    const first = vals[0]
    const last = vals[vals.length - 1]
    return { month, ret: first ? (last / first - 1) * 100 : null }
  })
  return {
    days: daily.length,
    upDays: ups.length,
    downDays: downs.length,
    winRate: ups.length / daily.length * 100,
    avgUp: avg(ups, 'ret'),
    avgDown: avg(downs, 'ret'),
    best,
    worst,
    latestGap: daily[daily.length - 1].gap,
    gapUpCount,
    gapDownCount,
    monthly,
  }
}

function buildAnomalyStats(result) {
  const candles = result?.candles || []
  if (candles.length < 30) return null
  const rows = []
  for (let i = 1; i < candles.length; i += 1) {
    const prev = candles[i - 1]
    const cur = candles[i]
    const prevClose = Number(prev.close)
    const open = Number(cur.open)
    const high = Number(cur.high)
    const low = Number(cur.low)
    const close = Number(cur.close)
    const volume = Number(cur.volume)
    if (!prevClose || [open, high, low, close, volume].some(Number.isNaN)) continue
    rows.push({
      date: cur.date,
      ret: (close / prevClose - 1) * 100,
      gap: (open / prevClose - 1) * 100,
      amplitude: low ? (high / low - 1) * 100 : null,
      volume,
    })
  }
  const recent = rows.slice(-120)
  if (!recent.length) return null
  const avgVol20 = recent.slice(-21, -1).reduce((s, r) => s + r.volume, 0) / Math.min(20, Math.max(1, recent.length - 1))
  const latest = recent[recent.length - 1]
  const topVolume = recent.reduce((a, b) => (b.volume > a.volume ? b : a), recent[0])
  const topGapUp = recent.reduce((a, b) => (b.gap > a.gap ? b : a), recent[0])
  const topGapDown = recent.reduce((a, b) => (b.gap < a.gap ? b : a), recent[0])
  const topAmplitude = recent.reduce((a, b) => ((b.amplitude ?? 0) > (a.amplitude ?? 0) ? b : a), recent[0])
  return {
    bigUpDays: recent.filter((r) => r.ret >= 5).length,
    bigDownDays: recent.filter((r) => r.ret <= -5).length,
    latestRet: latest.ret,
    latestGap: latest.gap,
    latestAmplitude: latest.amplitude,
    latestVolumeRatio: avgVol20 ? latest.volume / avgVol20 : null,
    topVolume: { ...topVolume, ratio: avgVol20 ? topVolume.volume / avgVol20 : null },
    topGapUp,
    topGapDown,
    topAmplitude,
  }
}

function buildDrawdownRecovery(result) {
  const candles = result?.candles || []
  if (candles.length < 30) return null
  const recent = candles.slice(-Math.min(120, candles.length))
  const latest = recent[recent.length - 1]
  const close = Number(latest.close)
  let peak = recent[0]
  let trough = recent[0]
  recent.forEach((c) => {
    if (Number(c.high) > Number(peak.high)) peak = c
    if (Number(c.low) < Number(trough.low)) trough = c
  })
  const peakPrice = Number(peak.high)
  const troughPrice = Number(trough.low)
  const currentDrawdown = peakPrice ? (close / peakPrice - 1) * 100 : null
  const reboundFromLow = troughPrice ? (close / troughPrice - 1) * 100 : null
  const repairToHigh = close ? (peakPrice / close - 1) * 100 : null
  const latestDate = new Date(latest.date)
  const daysSincePeak = Math.round((latestDate - new Date(peak.date)) / 86400000)
  const daysSinceTrough = Math.round((latestDate - new Date(trough.date)) / 86400000)
  const state = currentDrawdown >= -3
    ? '接近高位'
    : currentDrawdown >= -12
      ? '温和回撤'
      : currentDrawdown >= -25
        ? '中度回撤'
        : '深度回撤'
  return {
    peakDate: peak.date,
    peakPrice,
    troughDate: trough.date,
    troughPrice,
    currentDrawdown,
    reboundFromLow,
    repairToHigh,
    daysSincePeak,
    daysSinceTrough,
    state,
  }
}

export default function AnalyzeTab({ markets, market, setMarket, symbol, setSymbol,
                                    months, setMonths, runKey, requestRun }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [insightKey, setInsightKey] = useState(0)
  const [usKeyword, setUsKeyword] = useState('')
  const [usHits, setUsHits] = useState([])
  const [watched, setWatched] = useState(false)
  const [watchBusy, setWatchBusy] = useState(false)
  const [presets, setPresets] = useState({})
  const [lastRandom, setLastRandom] = useState({})

  useEffect(() => {
    if (runKey > 0 && symbol.trim()) doAnalyze()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey])

  useEffect(() => {
    fetchPresets().then((d) => setPresets(d.presets || {})).catch(() => {})
  }, [])

  async function doAnalyze(nextSymbol = symbol) {
    const target = nextSymbol.trim()
    if (!target) { setError('请先输入股票代码。'); return }
    setLoading(true); setError(''); setResult(null); setWatched(false)
    try {
      setResult(await analyze(market, target, months))
      setInsightKey((k) => k + 1)  // 触发基本面/AI/新闻三块按需加载
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  function pickRandomStock() {
    const pool = presets[market] || []
    if (!pool.length) {
      setError('当前市场暂无可随机选择的预设股票。')
      return
    }
    const current = symbol.trim().toUpperCase()
    const last = lastRandom[market]
    let candidates = pool.filter((p) => p.symbol !== current && p.symbol !== last)
    if (!candidates.length) candidates = pool.filter((p) => p.symbol !== current)
    if (!candidates.length) candidates = pool
    const picked = candidates[Math.floor(Math.random() * candidates.length)]
    setLastRandom((m) => ({ ...m, [market]: picked.symbol }))
    setSymbol(picked.symbol)
    doAnalyze(picked.symbol)
  }

  async function toggleWatch() {
    if (!result) return
    setWatchBusy(true)
    try {
      if (watched) {
        await removeWatch(result.market, result.symbol)
        setWatched(false)
      } else {
        await addWatch(result.market, result.symbol)
        setWatched(true)
      }
    } catch (e) { setError(e.message) } finally { setWatchBusy(false) }
  }

  async function doSearchUs() {
    if (!usKeyword.trim()) return
    try { setUsHits((await searchUs(usKeyword.trim())).results || []) }
    catch (e) { setError(e.message) }
  }

  const maxAbs = result ? Math.max(...result.reasons.map((r) => Math.abs(r.delta)), 1) : 1
  const riskPlan = buildRiskPlan(result)
  const technicalCheck = buildTechnicalCheck(result)
  const trendPhase = buildTrendPhase(result)
  const volatilityRegime = buildVolatilityRegime(result)
  const maDeviation = buildMaDeviation(result)
  const gapFollowUp = buildGapFollowUp(result)
  const volumePrice = buildVolumePriceStructure(result)
  const returnStats = buildReturnStats(result)
  const anomalyStats = buildAnomalyStats(result)
  const drawdownRecovery = buildDrawdownRecovery(result)

  return (
    <>
      <div className="panel">
        <div className="form-row">
          <div className="field">
            <label>市场</label>
            <select value={market} onChange={(e) => setMarket(e.target.value)}>
              {markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field" style={{ flex: 1, minWidth: 170 }}>
            <label>股票代码</label>
            <input value={symbol} placeholder={PLACEHOLDER[market]}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && requestRun()} />
          </div>
          <div className="field">
            <label>回溯 {months} 个月</label>
            <input type="range" min="6" max="36" value={months}
              onChange={(e) => setMonths(Number(e.target.value))} />
          </div>
          <button onClick={requestRun} disabled={loading}>
            {loading ? <><span className="spinner" /> 分析中</> : '开始分析'}
          </button>
          <button className="ghost" onClick={pickRandomStock} disabled={loading}>
            随机选股
          </button>
        </div>

        {market === '美股' && (
          <div style={{ marginTop: 14 }}>
            <div className="form-row">
              <div className="field" style={{ flex: 1, minWidth: 170 }}>
                <label>🔍 美股代码查找(忘了代码时用)</label>
                <input value={usKeyword} placeholder="苹果 / AAPL"
                  onChange={(e) => setUsKeyword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && doSearchUs()} />
              </div>
              <button className="ghost" onClick={doSearchUs}>查找</button>
            </div>
            {usHits.length > 0 && (
              <table style={{ marginTop: 10 }}>
                <thead><tr><th>代码</th><th>名称</th><th></th></tr></thead>
                <tbody>
                  {usHits.map((h) => (
                    <tr key={h['代码']}>
                      <td>{h['代码']}</td><td>{h['名称']}</td>
                      <td><button className="ghost" onClick={() => setSymbol(h['代码'])}>选用</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      {!result && !loading && (
        <div className="placeholder">
          <div className="big">📊</div>
          选择市场、输入股票代码,点击「开始分析」获取多因子打分与走势图。
        </div>
      )}

      {result && (
        <div className="fade-in">
          <div className="panel">
            <div className="result-head">
              <ScoreRing score={result.score} />
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16, flexWrap: 'wrap' }}>
                  <span className={`badge ${dirClass(result.direction)}`}>{result.direction}</span>
                  <span className="hint">{result.market} · {result.symbol}</span>
                  <button className="ghost" onClick={toggleWatch} disabled={watchBusy}
                    style={{ marginLeft: 'auto' }}>
                    {watched ? '★ 已收藏(点击取消)' : '☆ 加入自选'}
                  </button>
                </div>
                <div className="stat-grid">
                  <div className="stat">
                    <div className="k">模型估计上涨概率</div>
                    <div className="v">{result.probability}%</div>
                    <div className="prob-bar"><div style={{ width: `${result.probability}%`, background: probColor(result.probability) }} /></div>
                  </div>
                  <div className="stat"><div className="k">最新收盘价</div><div className="v">{result.indicators['收盘价']}</div></div>
                  <div className="stat"><div className="k">20日动量</div><div className="v">{result.indicators['20日动量%']}%</div></div>
                </div>
              </div>
            </div>
          </div>

          <div className="panel">
            <h3 className="section-title">📈 走势图 <span className="hint">K线 + 均线 + 布林带 + 成交量 · 涨红跌绿</span></h3>
            <CandleChart candles={result.candles} />
          </div>

          {technicalCheck && (
            <div className="panel">
              <h3 className="section-title">🧭 技术体检 <span className="hint">基于真实历史K线派生</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>{technicalCheck.verdict}</div>
              <div className="ind-grid">
                <div className="ind"><div className="k">近60日位置</div><div className="v">{technicalCheck.rangePos?.toFixed(1) ?? '—'}%</div></div>
                <div className="ind"><div className="k">近60日高点</div><div className="v">{price(technicalCheck.high60)}</div></div>
                <div className="ind"><div className="k">近60日低点</div><div className="v">{price(technicalCheck.low60)}</div></div>
                <div className="ind"><div className="k">量能比</div><div className="v">{technicalCheck.volumeRatio?.toFixed(2) ?? '—'}x</div></div>
                <div className="ind"><div className="k">20日年化波动</div><div className="v">{technicalCheck.vol20.toFixed(2)}%</div></div>
                <div className="ind"><div className="k">近5日收益</div><div className="v">{pct(technicalCheck.ret5)}</div></div>
                <div className="ind"><div className="k">连续涨跌</div><div className="v">{technicalCheck.streak > 0 ? `连涨${technicalCheck.streak}日` : technicalCheck.streak < 0 ? `连跌${Math.abs(technicalCheck.streak)}日` : '持平'}</div></div>
                <div className="ind"><div className="k">均线状态</div><div className="v">{technicalCheck.aboveMa20 && technicalCheck.aboveMa60 ? '偏强' : !technicalCheck.aboveMa20 && !technicalCheck.aboveMa60 ? '偏弱' : '分化'}</div></div>
              </div>
            </div>
          )}

          {trendPhase && (
            <div className="panel">
              <h3 className="section-title">🧱 趋势阶段 <span className="hint">基于均线斜率、区间位置和高低点结构</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>{trendPhase.verdict}</div>
              <div className="ind-grid">
                <div className="ind"><div className="k">阶段识别</div><div className="v">{trendPhase.phase}</div></div>
                <div className="ind"><div className="k">20日收益</div><div className="v">{pct(trendPhase.ret20)}</div></div>
                <div className="ind"><div className="k">60日收益</div><div className="v">{pct(trendPhase.ret60)}</div></div>
                <div className="ind"><div className="k">MA20斜率</div><div className="v">{pct(trendPhase.ma20Slope)}</div><div className="hint">近20个交易日</div></div>
                <div className="ind"><div className="k">MA60斜率</div><div className="v">{pct(trendPhase.ma60Slope)}</div><div className="hint">近20个交易日</div></div>
                <div className="ind"><div className="k">60日区间位置</div><div className="v">{trendPhase.rangePosition?.toFixed(1) ?? '—'}%</div></div>
                <div className="ind"><div className="k">高点结构</div><div className="v">{trendPhase.higherHighs ? '逐段抬高' : trendPhase.lowerHighs ? '逐段降低' : '分化'}</div></div>
                <div className="ind"><div className="k">低点结构</div><div className="v">{trendPhase.higherLows ? '逐段抬高' : trendPhase.lowerLows ? '逐段降低' : '分化'}</div></div>
              </div>
            </div>
          )}

          {volatilityRegime && (
            <div className="panel">
              <h3 className="section-title">🌊 波动环境 <span className="hint">比较近20日与近60日真实波动</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>{volatilityRegime.verdict}</div>
              <div className="ind-grid">
                <div className="ind"><div className="k">环境判断</div><div className="v">{volatilityRegime.state}</div></div>
                <div className="ind"><div className="k">20日年化波动</div><div className="v">{volatilityRegime.vol20?.toFixed(2) ?? '—'}%</div></div>
                <div className="ind"><div className="k">60日年化波动</div><div className="v">{volatilityRegime.vol60?.toFixed(2) ?? '—'}%</div></div>
                <div className="ind"><div className="k">波动比</div><div className="v">{volatilityRegime.volRatio?.toFixed(2) ?? '—'}x</div><div className="hint">20日 / 60日</div></div>
                <div className="ind"><div className="k">20日平均振幅</div><div className="v">{pct(volatilityRegime.avgAmp20)}</div></div>
                <div className="ind"><div className="k">60日平均振幅</div><div className="v">{pct(volatilityRegime.avgAmp60)}</div></div>
                <div className="ind"><div className="k">近20日大波动</div><div className="v">{volatilityRegime.highVolDays}</div><div className="hint">日涨跌幅≥3%</div></div>
                <div className="ind"><div className="k">近20日平静日</div><div className="v">{volatilityRegime.calmDays}</div><div className="hint">日涨跌幅≤1%</div></div>
                <div className="ind"><div className="k">近20日跳空风险</div><div className="v">{volatilityRegime.gapRiskDays}</div><div className="hint">开盘跳空≥1%</div></div>
                <div className="ind"><div className="k">最新振幅/跳空</div><div className="v">{pct(volatilityRegime.latestAmplitude)}</div><div className="hint">{pct(volatilityRegime.latestGap)}</div></div>
              </div>
            </div>
          )}

          {maDeviation && (
            <div className="panel">
              <h3 className="section-title">📏 均线乖离 <span className="hint">当前价格相对短中期均线的位置</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>{maDeviation.verdict}</div>
              <div className="ind-grid">
                <div className="ind"><div className="k">乖离判断</div><div className="v">{maDeviation.state}</div></div>
                <div className="ind"><div className="k">距MA5</div><div className="v">{pct(maDeviation.dist5)}</div></div>
                <div className="ind"><div className="k">距MA20</div><div className="v">{pct(maDeviation.dist20)}</div></div>
                <div className="ind"><div className="k">距MA60</div><div className="v">{pct(maDeviation.dist60)}</div></div>
                <div className="ind"><div className="k">MA20乖离分位</div><div className="v">{maDeviation.deviationRank?.toFixed(1) ?? '—'}%</div><div className="hint">近120日绝对偏离</div></div>
                <div className="ind"><div className="k">连续站上MA20</div><div className="v">{maDeviation.daysAboveMa20}天</div></div>
                <div className="ind"><div className="k">连续跌破MA20</div><div className="v">{maDeviation.daysBelowMa20}天</div></div>
              </div>
            </div>
          )}

          {gapFollowUp && (
            <div className="panel">
              <h3 className="section-title">🪟 缺口跟踪 <span className="hint">近120个跳空缺口的10日内回补情况</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>
                {gapFollowUp.state} · 最近缺口:{gapFollowUp.latest.type} {pct(gapFollowUp.latest.gapPct)}
                {gapFollowUp.latest.filled ? ` · 已于 ${gapFollowUp.latest.fillDate} 回补` : ' · 暂未回补'}
              </div>
              <div className="ind-grid">
                <div className="ind"><div className="k">统计缺口数</div><div className="v">{gapFollowUp.total}</div><div className="hint">跳空≥1%</div></div>
                <div className="ind"><div className="k">向上/向下跳空</div><div className="v">{gapFollowUp.upCount}/{gapFollowUp.downCount}</div></div>
                <div className="ind"><div className="k">10日回补率</div><div className="v">{gapFollowUp.fillRate.toFixed(1)}%</div></div>
                <div className="ind"><div className="k">未回补缺口</div><div className="v">{gapFollowUp.unfilledCount}</div></div>
                <div className="ind"><div className="k">最大向上缺口</div><div className="v">{gapFollowUp.largestUp ? pct(gapFollowUp.largestUp.gapPct) : '—'}</div><div className="hint">{gapFollowUp.largestUp?.date || '—'}</div></div>
                <div className="ind"><div className="k">最大向下缺口</div><div className="v">{gapFollowUp.largestDown ? pct(gapFollowUp.largestDown.gapPct) : '—'}</div><div className="hint">{gapFollowUp.largestDown?.date || '—'}</div></div>
              </div>
              {gapFollowUp.recentUnfilled.length > 0 && (
                <table className="compact-table" style={{ marginTop: 14 }}>
                  <thead><tr><th>未回补日期</th><th>方向</th><th>缺口幅度</th><th>缺口基准价</th></tr></thead>
                  <tbody>
                    {gapFollowUp.recentUnfilled.map((g) => (
                      <tr key={`${g.date}-${g.gapPct}`}>
                        <td>{g.date}</td><td>{g.type}</td>
                        <td className={g.gapPct > 0 ? 'delta-pos' : 'delta-neg'}>{pct(g.gapPct)}</td>
                        <td>{price(g.basePrice)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {volumePrice && (
            <div className="panel">
              <h3 className="section-title">🔎 量价结构 <span className="hint">近20个交易日成交量与价格方向配合</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>{volumePrice.verdict}</div>
              <div className="ind-grid">
                <div className="ind"><div className="k">结构判断</div><div className="v">{volumePrice.state}</div></div>
                <div className="ind"><div className="k">近20日收益</div><div className="v">{pct(volumePrice.ret20)}</div></div>
                <div className="ind"><div className="k">20日均量变化</div><div className="v">{pct(volumePrice.volumeTrend)}</div><div className="hint">对比前20日</div></div>
                <div className="ind"><div className="k">上涨/下跌日量比</div><div className="v">{volumePrice.upDownVolRatio?.toFixed(2) ?? '—'}x</div></div>
                <div className="ind"><div className="k">放量上涨日</div><div className="v">{volumePrice.accumulationDays}</div><div className="hint">高于20日均量20%</div></div>
                <div className="ind"><div className="k">放量下跌日</div><div className="v">{volumePrice.distributionDays}</div><div className="hint">高于20日均量20%</div></div>
                <div className="ind"><div className="k">缩量交易日</div><div className="v">{volumePrice.dryUpDays}</div><div className="hint">低于20日均量30%</div></div>
                <div className="ind"><div className="k">最新量能分位</div><div className="v">{volumePrice.latestVolRank?.toFixed(1) ?? '—'}%</div><div className="hint">近120日</div></div>
              </div>
            </div>
          )}

          {returnStats && (
            <div className="panel">
              <h3 className="section-title">📊 收益统计 <span className="hint">基于逐日真实收盘价计算</span></h3>
              <div className="ind-grid">
                <div className="ind"><div className="k">统计交易日</div><div className="v">{returnStats.days}</div></div>
                <div className="ind"><div className="k">上涨/下跌天数</div><div className="v">{returnStats.upDays}/{returnStats.downDays}</div></div>
                <div className="ind"><div className="k">日胜率</div><div className="v">{returnStats.winRate.toFixed(1)}%</div></div>
                <div className="ind"><div className="k">平均上涨</div><div className="v">{pct(returnStats.avgUp)}</div></div>
                <div className="ind"><div className="k">平均下跌</div><div className="v">{pct(returnStats.avgDown)}</div></div>
                <div className="ind"><div className="k">最佳单日</div><div className="v">{pct(returnStats.best.ret)}</div><div className="hint">{returnStats.best.date}</div></div>
                <div className="ind"><div className="k">最差单日</div><div className="v">{pct(returnStats.worst.ret)}</div><div className="hint">{returnStats.worst.date}</div></div>
                <div className="ind"><div className="k">最新跳空</div><div className="v">{pct(returnStats.latestGap)}</div></div>
                <div className="ind"><div className="k">近60日向上跳空</div><div className="v">{returnStats.gapUpCount}</div></div>
                <div className="ind"><div className="k">近60日向下跳空</div><div className="v">{returnStats.gapDownCount}</div></div>
              </div>
              <div className="fund-subhead">最近12个月收益</div>
              <div className="corr-wrap">
                <table className="monthly-table">
                  <thead><tr>{returnStats.monthly.map((m) => <th key={m.month}>{m.month.slice(5)}</th>)}</tr></thead>
                  <tbody>
                    <tr>
                      {returnStats.monthly.map((m) => (
                        <td key={m.month} className={m.ret > 0 ? 'delta-pos' : m.ret < 0 ? 'delta-neg' : 'delta-zero'}>
                          {pct(m.ret)}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {anomalyStats && (
            <div className="panel">
              <h3 className="section-title">🚨 异常波动监控 <span className="hint">近120个交易日真实K线统计</span></h3>
              <div className="ind-grid">
                <div className="ind"><div className="k">近120日大涨日</div><div className="v">{anomalyStats.bigUpDays}</div><div className="hint">单日涨幅≥5%</div></div>
                <div className="ind"><div className="k">近120日大跌日</div><div className="v">{anomalyStats.bigDownDays}</div><div className="hint">单日跌幅≤-5%</div></div>
                <div className="ind"><div className="k">最新日涨跌</div><div className="v">{pct(anomalyStats.latestRet)}</div></div>
                <div className="ind"><div className="k">最新跳空</div><div className="v">{pct(anomalyStats.latestGap)}</div></div>
                <div className="ind"><div className="k">最新振幅</div><div className="v">{pct(anomalyStats.latestAmplitude)}</div></div>
                <div className="ind"><div className="k">最新量能比</div><div className="v">{anomalyStats.latestVolumeRatio?.toFixed(2) ?? '—'}x</div></div>
              </div>
              <table className="compact-table" style={{ marginTop: 14 }}>
                <thead><tr><th>异常项</th><th>日期</th><th>数值</th></tr></thead>
                <tbody>
                  <tr><td>最大向上跳空</td><td>{anomalyStats.topGapUp.date}</td><td className="delta-pos">{pct(anomalyStats.topGapUp.gap)}</td></tr>
                  <tr><td>最大向下跳空</td><td>{anomalyStats.topGapDown.date}</td><td className="delta-neg">{pct(anomalyStats.topGapDown.gap)}</td></tr>
                  <tr><td>最大振幅</td><td>{anomalyStats.topAmplitude.date}</td><td>{pct(anomalyStats.topAmplitude.amplitude)}</td></tr>
                  <tr><td>最大成交量</td><td>{anomalyStats.topVolume.date}</td><td>{anomalyStats.topVolume.ratio?.toFixed(2) ?? '—'}x 当前20日均量</td></tr>
                </tbody>
              </table>
            </div>
          )}

          {drawdownRecovery && (
            <div className="panel">
              <h3 className="section-title">🧩 回撤修复分析 <span className="hint">近120个交易日高低点与当前位置</span></h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>
                当前状态:{drawdownRecovery.state} · 距近120日高点 {pct(drawdownRecovery.currentDrawdown)} · 从低点反弹 {pct(drawdownRecovery.reboundFromLow)}
              </div>
              <div className="ind-grid">
                <div className="ind"><div className="k">近120日高点</div><div className="v">{price(drawdownRecovery.peakPrice)}</div><div className="hint">{drawdownRecovery.peakDate}</div></div>
                <div className="ind"><div className="k">近120日低点</div><div className="v">{price(drawdownRecovery.troughPrice)}</div><div className="hint">{drawdownRecovery.troughDate}</div></div>
                <div className="ind"><div className="k">当前回撤</div><div className="v">{pct(drawdownRecovery.currentDrawdown)}</div></div>
                <div className="ind"><div className="k">低点以来反弹</div><div className="v">{pct(drawdownRecovery.reboundFromLow)}</div></div>
                <div className="ind"><div className="k">修复前高所需</div><div className="v">{pct(drawdownRecovery.repairToHigh)}</div></div>
                <div className="ind"><div className="k">距高点天数</div><div className="v">{drawdownRecovery.daysSincePeak}天</div></div>
                <div className="ind"><div className="k">距低点天数</div><div className="v">{drawdownRecovery.daysSinceTrough}天</div></div>
              </div>
            </div>
          )}

          {riskPlan && (
            <div className="panel">
              <h3 className="section-title">🛡️ 风控计划 <span className="hint">基于最近真实K线波动计算,用于交易前检查</span></h3>
              <div className="risk-grid">
                <div className="risk-main">
                  <div className={`badge ${riskPlan.stance === '先观望' ? 'down' : riskPlan.stance === '轻仓观察' ? 'neutral' : 'up'}`}>
                    {riskPlan.stance}
                  </div>
                  <div className="hint" style={{ marginTop: 10 }}>
                    波动等级:{riskPlan.riskLevel} · 近14日平均振幅 {riskPlan.atrPct.toFixed(2)}%
                  </div>
                </div>
                <div className="stat">
                  <div className="k">近20日支撑</div>
                  <div className="v">{price(riskPlan.support)}</div>
                  <div className="hint">{pct(riskPlan.downside)} 距当前价</div>
                </div>
                <div className="stat">
                  <div className="k">近20日压力</div>
                  <div className="v">{price(riskPlan.resistance)}</div>
                  <div className="hint">{pct(riskPlan.upside)} 距当前价</div>
                </div>
                <div className="stat">
                  <div className="k">参考止损线</div>
                  <div className="v">{price(riskPlan.stopLine)}</div>
                  <div className="hint">按波动自动放宽/收紧</div>
                </div>
              </div>
              <p className="hint" style={{ margin: '12px 0 0' }}>
                支撑/压力来自最近20个交易日高低点;参考止损线来自近14日平均振幅,不是保证成交或收益的价格。
              </p>
            </div>
          )}

          <div className="panel">
            <h3 className="section-title">🧮 打分依据 <span className="hint">每个因子加/减了多少分,透明可解释</span></h3>
            <table>
              <thead><tr><th style={{ width: 90 }}>因子</th><th style={{ width: 60 }}>加减分</th><th style={{ width: 160 }}>影响</th><th>说明</th></tr></thead>
              <tbody>
                {result.reasons.map((r, i) => (
                  <tr key={i}>
                    <td>{r.name}</td>
                    <td className={r.delta > 0 ? 'delta-pos' : r.delta < 0 ? 'delta-neg' : 'delta-zero'}>
                      {r.delta > 0 ? `+${r.delta}` : r.delta}
                    </td>
                    <td>
                      <span className="factor-bar" style={{
                        width: `${Math.abs(r.delta) / maxAbs * 80}px`,
                        background: r.delta > 0 ? 'var(--up)' : r.delta < 0 ? 'var(--down)' : 'var(--faint)',
                      }} />
                    </td>
                    <td className="hint" style={{ color: 'var(--text)' }}>{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h3 className="section-title">📐 关键技术指标</h3>
            <div className="ind-grid">
              {Object.entries(result.indicators).map(([k, v]) => (
                <div className="ind" key={k}><div className="k">{k}</div><div className="v">{v ?? '—'}</div></div>
              ))}
            </div>
          </div>

          <CompareSection market={result.market} symbol={result.symbol} trigger={insightKey} />
          <QuoteSection market={result.market} symbol={result.symbol} trigger={insightKey} />
          <FundamentalsSection market={result.market} symbol={result.symbol} trigger={insightKey} />
          <MLSection market={result.market} symbol={result.symbol} trigger={insightKey} />
          <NewsSection market={result.market} symbol={result.symbol} trigger={insightKey} />
        </div>
      )}
    </>
  )
}
