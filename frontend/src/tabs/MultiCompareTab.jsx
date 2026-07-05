import { useEffect, useMemo, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { fetchPresets, multiCompare } from '../api'
import { dirClass, scoreColor } from '../helpers'

const COLORS = [
  '#ff4d5e', '#5b8cff', '#f5b942', '#1fd286', '#9d6bff', '#26c6da',
  '#ff8a3d', '#e45cff', '#a6e22e', '#d9d9d9', '#64b5f6', '#ff6b9a',
]

function parseSymbols(text) {
  return text.split(/[\s,，、]+/).map((s) => s.trim()).filter(Boolean)
}

function CompareLines({ data }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !data?.rebased?.length) return
    const chart = createChart(ref.current, {
      layout: { background: { color: 'transparent' }, textColor: '#8896a8', fontSize: 11 },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.05)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)' },
      crosshair: { mode: 1 },
      autoSize: true,
    })
    data.symbols.forEach((sym, i) => {
      const series = chart.addLineSeries({
        color: COLORS[i % COLORS.length],
        lineWidth: 2,
        priceLineVisible: false,
        title: sym,
      })
      series.setData(data.rebased.map((r) => ({ time: r.date, value: r[sym] })))
    })
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data])

  return <div ref={ref} className="chart" />
}

function PortfolioLine({ portfolio }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !portfolio?.path?.length) return
    const chart = createChart(ref.current, {
      layout: { background: { color: 'transparent' }, textColor: '#8896a8', fontSize: 11 },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.05)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)' },
      crosshair: { mode: 1 },
      autoSize: true,
    })
    const series = chart.addLineSeries({
      color: '#f5b942',
      lineWidth: 2,
      priceLineVisible: false,
      title: '等权组合',
    })
    series.setData(portfolio.path.map((r) => ({ time: r.date, value: r.value })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [portfolio])

  return <div ref={ref} className="chart small" />
}

function corrColor(v) {
  const a = Math.min(1, Math.max(0, Math.abs(v)))
  if (v >= 0) return `rgba(255, 77, 94, ${0.08 + a * 0.42})`
  return `rgba(31, 210, 134, ${0.08 + a * 0.42})`
}

function sortValue(row, key) {
  const v = row?.[key]
  if (v === null || v === undefined || v === '') return Number.NEGATIVE_INFINITY
  if (typeof v === 'number') return v
  const n = Number(v)
  return Number.isNaN(n) ? String(v) : n
}

function finiteMetric(row, key) {
  const n = Number(row?.[key])
  return Number.isFinite(n) ? n : null
}

function pickMetric(rows, key, mode) {
  const valid = rows.filter((row) => finiteMetric(row, key) !== null)
  if (!valid.length) return null
  return valid.reduce((best, row) => {
    const bestValue = finiteMetric(best, key)
    const rowValue = finiteMetric(row, key)
    return mode === 'min' ? (rowValue < bestValue ? row : best) : (rowValue > bestValue ? row : best)
  }, valid[0])
}

function roundMetric(n) {
  return Number.isFinite(n) ? Number(n.toFixed(2)) : null
}

function buildDispersionSummary(rows) {
  if (!rows?.length) return null
  const validReturns = rows.filter((row) => finiteMetric(row, 'return_pct') !== null)
  if (validReturns.length < 2) return null
  const values = validReturns.map((row) => finiteMetric(row, 'return_pct')).sort((a, b) => a - b)
  const avgReturn = values.reduce((sum, n) => sum + n, 0) / values.length
  const mid = Math.floor(values.length / 2)
  const medianReturn = values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2
  const variance = values.reduce((sum, n) => sum + Math.pow(n - avgReturn, 2), 0) / values.length
  const best = pickMetric(validReturns, 'return_pct', 'max')
  const worst = pickMetric(validReturns, 'return_pct', 'min')
  const highVol = pickMetric(rows, 'annual_vol', 'max')
  const lowVol = pickMetric(rows, 'annual_vol', 'min')
  const deepestDrawdown = pickMetric(rows, 'max_drawdown', 'min')
  const shallowestDrawdown = pickMetric(rows, 'max_drawdown', 'max')
  const winners = validReturns.filter((row) => finiteMetric(row, 'return_pct') > 0).length
  const losers = validReturns.filter((row) => finiteMetric(row, 'return_pct') < 0).length
  const returnSpread = finiteMetric(best, 'return_pct') - finiteMetric(worst, 'return_pct')
  const volSpread = highVol && lowVol ? finiteMetric(highVol, 'annual_vol') - finiteMetric(lowVol, 'annual_vol') : null
  const drawdownGap = deepestDrawdown && shallowestDrawdown
    ? Math.abs(finiteMetric(deepestDrawdown, 'max_drawdown') - finiteMetric(shallowestDrawdown, 'max_drawdown'))
    : null
  const breadth = winners === validReturns.length
    ? '全部上涨'
    : losers === validReturns.length
      ? '全部下跌'
      : winners > losers
        ? '多数上涨'
        : losers > winners
          ? '多数下跌'
          : '涨跌均衡'
  const state = returnSpread >= 40
    ? '强弱高度分化'
    : returnSpread >= 20
      ? '强弱分化明显'
      : returnSpread >= 10
        ? '中等分化'
        : '走势接近'
  return {
    state,
    breadth,
    count: validReturns.length,
    winners,
    losers,
    avgReturn: roundMetric(avgReturn),
    medianReturn: roundMetric(medianReturn),
    returnStd: roundMetric(Math.sqrt(variance)),
    returnSpread: roundMetric(returnSpread),
    volSpread: roundMetric(volSpread),
    drawdownGap: roundMetric(drawdownGap),
    bestSymbol: best.symbol,
    bestReturn: finiteMetric(best, 'return_pct'),
    worstSymbol: worst.symbol,
    worstReturn: finiteMetric(worst, 'return_pct'),
    highCompositeCount: rows.filter((row) => finiteMetric(row, 'composite_score') >= 65).length,
    lowCompositeCount: rows.filter((row) => finiteMetric(row, 'composite_score') < 50).length,
  }
}

function medianMetric(rows, key) {
  const values = rows.map((row) => finiteMetric(row, key)).filter((n) => n !== null).sort((a, b) => a - b)
  if (!values.length) return null
  const mid = Math.floor(values.length / 2)
  return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2
}

function buildQuadrantSummary(rows) {
  if (!rows?.length) return null
  const valid = rows.filter((row) => finiteMetric(row, 'return_pct') !== null && finiteMetric(row, 'annual_vol') !== null)
  if (valid.length < 2) return null
  const medianReturn = medianMetric(valid, 'return_pct')
  const medianVol = medianMetric(valid, 'annual_vol')
  if (medianReturn === null || medianVol === null) return null
  const defs = [
    { id: 'strong_stable', title: '强势稳健', desc: '收益不低于中位数,波动不高于中位数', tone: 'var(--up)', rows: [] },
    { id: 'strong_volatile', title: '强势高波动', desc: '收益不低于中位数,波动高于中位数', tone: 'var(--neutral)', rows: [] },
    { id: 'weak_stable', title: '弱势低波动', desc: '收益低于中位数,波动不高于中位数', tone: 'var(--accent)', rows: [] },
    { id: 'weak_volatile', title: '弱势高波动', desc: '收益低于中位数,波动高于中位数', tone: 'var(--down)', rows: [] },
  ]
  valid.forEach((row) => {
    const ret = finiteMetric(row, 'return_pct')
    const vol = finiteMetric(row, 'annual_vol')
    const target = ret >= medianReturn
      ? (vol <= medianVol ? defs[0] : defs[1])
      : (vol <= medianVol ? defs[2] : defs[3])
    target.rows.push(row)
  })
  const quadrants = defs.map((q) => {
    const best = pickMetric(q.rows, 'composite_score', 'max')
    return {
      ...q,
      symbols: q.rows.map((row) => row.symbol),
      bestSymbol: best?.symbol || null,
      avgReturn: roundMetric(q.rows.length
        ? q.rows.reduce((sum, row) => sum + finiteMetric(row, 'return_pct'), 0) / q.rows.length
        : null),
      avgVol: roundMetric(q.rows.length
        ? q.rows.reduce((sum, row) => sum + finiteMetric(row, 'annual_vol'), 0) / q.rows.length
        : null),
    }
  })
  return {
    medianReturn: roundMetric(medianReturn),
    medianVol: roundMetric(medianVol),
    quadrants,
  }
}

function buildPathStability(data) {
  if (!data?.symbols?.length || !data?.rebased?.length) return null
  const rows = data.symbols.map((symbol) => {
    const points = data.rebased
      .map((row) => ({ date: row.date, value: Number(row[symbol]) }))
      .filter((row) => Number.isFinite(row.value))
    if (points.length < 20) return null
    const daily = []
    for (let i = 1; i < points.length; i += 1) {
      const prev = points[i - 1].value
      const cur = points[i].value
      if (!prev) continue
      daily.push({ date: points[i].date, ret: (cur / prev - 1) * 100 })
    }
    if (!daily.length) return null
    let maxLossStreak = 0
    let currentLossStreak = 0
    daily.forEach((d) => {
      if (d.ret < 0) {
        currentLossStreak += 1
        maxLossStreak = Math.max(maxLossStreak, currentLossStreak)
      } else {
        currentLossStreak = 0
      }
    })
    const positiveDays = daily.filter((d) => d.ret > 0).length
    const aboveBaseDays = points.filter((p) => p.value >= 100).length
    const last = points[points.length - 1]
    const last20Base = points[Math.max(0, points.length - 21)]
    const recent20Return = last20Base.value ? (last.value / last20Base.value - 1) * 100 : null
    const maxPathValue = Math.max(...points.map((p) => p.value))
    const currentDrawdown = maxPathValue ? (last.value / maxPathValue - 1) * 100 : null
    return {
      symbol,
      positiveRate: positiveDays / daily.length * 100,
      maxLossStreak,
      aboveBaseRate: aboveBaseDays / points.length * 100,
      recent20Return,
      currentDrawdown,
      endingValue: last.value,
    }
  }).filter(Boolean)
  if (!rows.length) return null
  const pick = (key, mode) => rows.reduce((best, row) => {
    const bv = Number(best[key])
    const rv = Number(row[key])
    return mode === 'min' ? (rv < bv ? row : best) : (rv > bv ? row : best)
  }, rows[0])
  return {
    rows: [...rows].sort((a, b) => b.positiveRate - a.positiveRate),
    bestWinRate: pick('positiveRate', 'max'),
    shortestLossStreak: pick('maxLossStreak', 'min'),
    bestAboveBase: pick('aboveBaseRate', 'max'),
    bestRecent20: pick('recent20Return', 'max'),
  }
}

function buildCoMovementDays(data) {
  if (!data?.symbols?.length || !data?.rebased?.length || data.rebased.length < 2) return null
  const rows = []
  for (let i = 1; i < data.rebased.length; i += 1) {
    const prev = data.rebased[i - 1]
    const cur = data.rebased[i]
    const moves = data.symbols.map((symbol) => {
      const prevVal = Number(prev[symbol])
      const curVal = Number(cur[symbol])
      if (!prevVal || !Number.isFinite(curVal)) return null
      return { symbol, ret: (curVal / prevVal - 1) * 100 }
    }).filter(Boolean)
    if (moves.length < data.symbols.length) continue
    const upCount = moves.filter((m) => m.ret > 0).length
    const downCount = moves.filter((m) => m.ret < 0).length
    const avgRet = moves.reduce((sum, m) => sum + m.ret, 0) / moves.length
    rows.push({ date: cur.date, upCount, downCount, avgRet, moves })
  }
  if (!rows.length) return null
  const allUp = rows.filter((row) => row.upCount === data.symbols.length)
  const allDown = rows.filter((row) => row.downCount === data.symbols.length)
  const splitDays = rows.filter((row) => row.upCount > 0 && row.downCount > 0)
  const strongest = rows.reduce((best, row) => (row.avgRet > best.avgRet ? row : best), rows[0])
  const weakest = rows.reduce((best, row) => (row.avgRet < best.avgRet ? row : best), rows[0])
  const latest = rows[rows.length - 1]
  const topDivergence = rows.reduce((best, row) => {
    const absBalance = Math.abs(row.upCount - row.downCount)
    const bestAbsBalance = Math.abs(best.upCount - best.downCount)
    return absBalance < bestAbsBalance ? row : best
  }, rows[0])
  return {
    totalDays: rows.length,
    allUpDays: allUp.length,
    allDownDays: allDown.length,
    splitDays: splitDays.length,
    strongest,
    weakest,
    latest,
    topDivergence,
  }
}

function buildTailRiskDays(data) {
  if (!data?.symbols?.length || !data?.rebased?.length || data.rebased.length < 2) return null
  const rows = []
  for (let i = 1; i < data.rebased.length; i += 1) {
    const prev = data.rebased[i - 1]
    const cur = data.rebased[i]
    const moves = data.symbols.map((symbol) => {
      const prevVal = Number(prev[symbol])
      const curVal = Number(cur[symbol])
      if (!prevVal || !Number.isFinite(curVal)) return null
      return { symbol, ret: (curVal / prevVal - 1) * 100 }
    }).filter(Boolean)
    if (moves.length < data.symbols.length) continue
    const losses = moves.filter((m) => m.ret <= -2)
    const severeLosses = moves.filter((m) => m.ret <= -5)
    const worstMove = moves.reduce((best, m) => (m.ret < best.ret ? m : best), moves[0])
    const avgRet = moves.reduce((sum, m) => sum + m.ret, 0) / moves.length
    rows.push({
      date: cur.date,
      avgRet,
      lossCount: losses.length,
      severeLossCount: severeLosses.length,
      worstSymbol: worstMove.symbol,
      worstRet: worstMove.ret,
    })
  }
  if (!rows.length) return null
  const broadLossThreshold = Math.ceil(data.symbols.length * 0.5)
  const broadLossDays = rows.filter((row) => row.lossCount >= broadLossThreshold)
  const severeDays = rows.filter((row) => row.severeLossCount > 0)
  const worstAverageDay = rows.reduce((best, row) => (row.avgRet < best.avgRet ? row : best), rows[0])
  const worstSingleDay = rows.reduce((best, row) => (row.worstRet < best.worstRet ? row : best), rows[0])
  const latest = rows[rows.length - 1]
  const recent20 = rows.slice(-20)
  const recentBroadLossDays = recent20.filter((row) => row.lossCount >= broadLossThreshold).length
  return {
    totalDays: rows.length,
    broadLossThreshold,
    broadLossDays: broadLossDays.length,
    severeDays: severeDays.length,
    recentBroadLossDays,
    worstAverageDay,
    worstSingleDay,
    latest,
    recentRows: rows.slice(-8).reverse(),
  }
}

function SortTh({ label, sortKey, sort, setSort }) {
  const active = sort.key === sortKey
  const marker = active ? (sort.desc ? ' ↓' : ' ↑') : ''
  return (
    <th>
      <button
        className={`sort-head ${active ? 'active' : ''}`}
        onClick={() => setSort((s) => (
          s.key === sortKey ? { key: sortKey, desc: !s.desc } : { key: sortKey, desc: true }
        ))}
      >
        {label}{marker}
      </button>
    </th>
  )
}

export default function MultiCompareTab({ markets, goAnalyze }) {
  const [market, setMarket] = useState('A股')
  const [months, setMonths] = useState(12)
  const [text, setText] = useState('600519, 000858, 000001, 002594')
  const [presets, setPresets] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)
  const [includeFundamentals, setIncludeFundamentals] = useState(false)
  const [sort, setSort] = useState({ key: 'return_pct', desc: true })
  const [lastRandom, setLastRandom] = useState({})
  const [portfolioType, setPortfolioType] = useState('等权组合')

  useEffect(() => {
    fetchPresets().then((d) => setPresets(d.presets || {})).catch(() => {})
  }, [])

  const presetList = presets[market] || []
  const selectedPortfolio = useMemo(() => {
    const list = data?.portfolios || (data?.portfolio ? [data.portfolio] : [])
    return list.find((p) => p.type === portfolioType) || list[0] || null
  }, [data, portfolioType])
  const sortedMetrics = useMemo(() => {
    if (!data?.metrics) return []
    return [...data.metrics].sort((a, b) => {
      const av = sortValue(a, sort.key)
      const bv = sortValue(b, sort.key)
      if (typeof av === 'string' || typeof bv === 'string') {
        return sort.desc
          ? String(bv).localeCompare(String(av))
          : String(av).localeCompare(String(bv))
      }
      return sort.desc ? bv - av : av - bv
    })
  }, [data, sort])
  const riskLeaders = useMemo(() => {
    const rows = data?.metrics || []
    if (!rows.length) return []
    const defs = [
      { key: 'annual_vol', mode: 'min', label: '最低波动', suffix: '%', tone: 'var(--up)' },
      { key: 'max_drawdown', mode: 'max', label: '回撤最浅', suffix: '%', tone: 'var(--up)' },
      { key: 'max_drawdown', mode: 'min', label: '回撤最大', suffix: '%', tone: 'var(--down)' },
      { key: 'risk_adjusted', mode: 'max', label: '收益/波动最好', suffix: '', tone: 'var(--text)' },
      { key: 'composite_score', mode: 'max', label: '综合分最高', suffix: '', tone: 'var(--accent)' },
    ]
    return defs.map((def) => {
      const row = pickMetric(rows, def.key, def.mode)
      if (!row) return null
      return { ...def, row, value: finiteMetric(row, def.key) }
    }).filter(Boolean)
  }, [data])
  const dispersionSummary = useMemo(() => buildDispersionSummary(data?.metrics || []), [data])
  const quadrantSummary = useMemo(() => buildQuadrantSummary(data?.metrics || []), [data])
  const pathStability = useMemo(() => buildPathStability(data), [data])
  const coMovementDays = useMemo(() => buildCoMovementDays(data), [data])
  const tailRiskDays = useMemo(() => buildTailRiskDays(data), [data])

  function loadPreset(limit = 6) {
    setText(presetList.slice(0, limit).map((p) => p.symbol).join(', '))
  }

  function addRandomStock() {
    if (!presetList.length) {
      setError('当前市场暂无可随机选择的预设股票。')
      return
    }
    const selected = new Set(parseSymbols(text))
    const last = lastRandom[market]
    let candidates = presetList.filter((p) => !selected.has(p.symbol) && p.symbol !== last)
    if (!candidates.length) candidates = presetList.filter((p) => p.symbol !== last)
    if (!candidates.length) candidates = presetList
    const picked = candidates[Math.floor(Math.random() * candidates.length)]
    setLastRandom((m) => ({ ...m, [market]: picked.symbol }))
    setText((t) => {
      const current = parseSymbols(t).filter((s) => s !== picked.symbol)
      const next = [...current, picked.symbol].slice(-12)
      return next.join(', ')
    })
    setData(null)
    setError('')
  }

  function randomGroup() {
    if (presetList.length < 2) {
      setError('当前市场暂无足够的预设股票生成随机组合。')
      return
    }
    const last = lastRandom[market]
    const shuffled = [...presetList]
      .filter((p) => p.symbol !== last)
      .sort(() => Math.random() - 0.5)
    const size = Math.min(6, Math.max(3, Math.floor(Math.random() * 4) + 3), shuffled.length)
    const picked = shuffled.slice(0, size)
    setLastRandom((m) => ({ ...m, [market]: picked[0]?.symbol }))
    setText(picked.map((p) => p.symbol).join(', '))
    setData(null)
    setError('')
  }

  async function run() {
    const symbols = parseSymbols(text)
    if (symbols.length < 2) {
      setError('多股对比至少需要 2 只股票。')
      return
    }
    setLoading(true); setError(''); setData(null)
    try {
      const result = await multiCompare(market, symbols, months, includeFundamentals && market === 'A股')
      setData(result)
      setPortfolioType(result.portfolios?.[0]?.type || result.portfolio?.type || '等权组合')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const nameOf = (sym) => (presetList.find((p) => p.symbol === sym) || {}).name || ''

  function exportCsv() {
    if (!sortedMetrics.length) return
    const hasFund = data.fundamentals_included
    const header = ['代码', '名称', '综合分', '综合结论', '1月收益%', '3月收益%', '6月收益%', '区间收益%', '年化波动%', '最大回撤%', '收益/波动', '技术评分', '上涨概率%', '方向']
    if (hasFund) {
      header.push('基本面评分', '评级', 'ROE%', '毛利率%', '净利率%', '负债率%', '现金流质量', 'PE分位%', 'PB分位%', '营收连续增长年数', '净利润连续增长年数')
    }
    const lines = sortedMetrics.map((r) => [
      r.symbol,
      nameOf(r.symbol),
      r.composite_score ?? '',
      r.composite_verdict ?? '',
      r.return_1m ?? '',
      r.return_3m ?? '',
      r.return_6m ?? '',
      r.return_pct,
      r.annual_vol,
      r.max_drawdown,
      r.risk_adjusted ?? '',
      r.score,
      r.probability,
      r.direction,
      ...(hasFund ? [
        r.fundamental_score ?? '',
        r.fundamental_rating ?? '',
        r.roe ?? '',
        r.gross_margin ?? '',
        r.net_margin ?? '',
        r.debt_ratio ?? '',
        r.cashflow_quality ?? '',
        r.pe_percentile ?? '',
        r.pb_percentile ?? '',
        r.revenue_growth_years ?? '',
        r.profit_growth_years ?? '',
      ] : []),
    ])
    const csv = [header, ...lines]
      .map((row) => row.map((v) => `"${String(v ?? '').replaceAll('"', '""')}"`).join(','))
      .join('\n')
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${data.market}_multi_compare_${data.months}m.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
      <div className="panel">
        <h3 className="section-title">
          多股对比 <span className="hint">同市场横向比较收益、波动、回撤、评分和相关性</span>
        </h3>
        <div className="form-row" style={{ marginTop: 14 }}>
          <div className="field">
            <label>市场</label>
            <select value={market} onChange={(e) => { setMarket(e.target.value); setData(null) }}>
              {markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field">
            <label>区间 {months} 个月</label>
            <input type="range" min="3" max="36" value={months}
              onChange={(e) => setMonths(Number(e.target.value))} />
          </div>
          <label className={`toggle-line ${market !== 'A股' ? 'disabled' : ''}`}>
            <input
              type="checkbox"
              checked={includeFundamentals && market === 'A股'}
              disabled={market !== 'A股' || loading}
              onChange={(e) => setIncludeFundamentals(e.target.checked)}
            />
            <span>加入基本面</span>
            <span className="hint">A股真实财务数据,会慢一些</span>
          </label>
          <button onClick={run} disabled={loading}>
            {loading ? <><span className="spinner" /> {includeFundamentals && market === 'A股' ? '价格与基本面加载中' : '对比中'}</> : '开始对比'}
          </button>
          {presetList.length > 0 && (
            <button className="ghost" onClick={() => loadPreset(6)} disabled={loading}>
              载入预设前 6 只
            </button>
          )}
          {presetList.length > 0 && (
            <button className="ghost" onClick={addRandomStock} disabled={loading}>
              随机加一只
            </button>
          )}
          {presetList.length > 0 && (
            <button className="ghost" onClick={randomGroup} disabled={loading}>
              随机换一组
            </button>
          )}
        </div>

        <div className="field" style={{ marginTop: 14 }}>
          <label>股票代码列表(同一市场,逗号/空格分隔,最多 12 只)</label>
          <textarea value={text}
            placeholder="例如:600519, 000858, 000001, 002594"
            onChange={(e) => setText(e.target.value)} />
        </div>

        {presetList.length > 0 && (
          <div className="chips">
            {presetList.map((p) => (
              <span key={p.symbol} className="chip"
                onClick={() => setText((t) => (t ? `${t}, ${p.symbol}` : p.symbol))}>
                {p.name} {p.symbol}
              </span>
            ))}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      {!data && !loading && (
        <div className="placeholder">
          <div className="big">≋</div>
          把几只股票放在一起,都从 100 起步,马上能看出谁更强、谁更稳、谁回撤更深。
        </div>
      )}

      {data && (
        <div className="fade-in">
          {data.data_quality && (
            <div className="panel">
              <h3 className="section-title">
                数据质量 <span className="hint">真实行情对齐后的可比样本</span>
              </h3>
              <div className="bt-cards quality-cards">
                <div className="bt-card">
                  <div className="k">对齐交易日</div>
                  <div className="v">{data.data_quality.aligned_days}</div>
                </div>
                <div className="bt-card">
                  <div className="k">实际区间</div>
                  <div className="v quality-date">{data.data_quality.start}</div>
                  <div className="hint" style={{ marginTop: 6 }}>至 {data.data_quality.end}</div>
                </div>
                <div className="bt-card">
                  <div className="k">成功 / 失败</div>
                  <div className="v">{data.data_quality.success_symbols} / {data.data_quality.failed_symbols}</div>
                </div>
                <div className="bt-card">
                  <div className="k">平均相关度</div>
                  <div className="v">{data.correlation_summary?.average_abs ?? '—'}</div>
                </div>
              </div>
              {data.correlation_summary?.highest_pair && (
                <div className="hint">
                  最高相关:{data.correlation_summary.highest_pair.a}-{data.correlation_summary.highest_pair.b}
                  ({data.correlation_summary.highest_pair.value}) ·
                  最低相关:{data.correlation_summary.lowest_pair.a}-{data.correlation_summary.lowest_pair.b}
                  ({data.correlation_summary.lowest_pair.value})
                </div>
              )}
            </div>
          )}

          {dispersionSummary && (
            <div className="panel">
              <h3 className="section-title">
                横向分化 <span className="hint">同组股票真实区间收益、波动和回撤的离散程度</span>
              </h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>
                {dispersionSummary.state} · {dispersionSummary.breadth} ·
                最强 {dispersionSummary.bestSymbol} {dispersionSummary.bestReturn > 0 ? '+' : ''}{dispersionSummary.bestReturn}% /
                最弱 {dispersionSummary.worstSymbol} {dispersionSummary.worstReturn > 0 ? '+' : ''}{dispersionSummary.worstReturn}%
              </div>
              <div className="bt-cards quality-cards">
                <div className="bt-card">
                  <div className="k">上涨 / 下跌</div>
                  <div className="v">{dispersionSummary.winners} / {dispersionSummary.losers}</div>
                  <div className="hint">共 {dispersionSummary.count} 只成功纳入</div>
                </div>
                <div className="bt-card">
                  <div className="k">收益均值 / 中位数</div>
                  <div className="v">{dispersionSummary.avgReturn}%</div>
                  <div className="hint">中位数 {dispersionSummary.medianReturn}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">收益标准差</div>
                  <div className="v">{dispersionSummary.returnStd}%</div>
                  <div className="hint">越高代表内部强弱越分散</div>
                </div>
                <div className="bt-card">
                  <div className="k">最高最低收益差</div>
                  <div className="v">{dispersionSummary.returnSpread}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">波动率差</div>
                  <div className="v">{dispersionSummary.volSpread ?? '—'}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">回撤差</div>
                  <div className="v">{dispersionSummary.drawdownGap ?? '—'}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">综合高分</div>
                  <div className="v">{dispersionSummary.highCompositeCount}</div>
                  <div className="hint">综合分 ≥ 65</div>
                </div>
                <div className="bt-card">
                  <div className="k">综合低分</div>
                  <div className="v">{dispersionSummary.lowCompositeCount}</div>
                  <div className="hint">综合分 &lt; 50</div>
                </div>
              </div>
            </div>
          )}

          {quadrantSummary && (
            <div className="panel">
              <h3 className="section-title">
                收益-波动象限 <span className="hint">以本组中位收益和中位波动为分界</span>
              </h3>
              <div className="hint" style={{ marginBottom: 12 }}>
                中位收益 {quadrantSummary.medianReturn}% · 中位年化波动 {quadrantSummary.medianVol}%
              </div>
              <div className="bt-cards quality-cards">
                {quadrantSummary.quadrants.map((q) => (
                  <div key={q.id} className="bt-card">
                    <div className="k">{q.title}</div>
                    <div className="v" style={{ color: q.tone }}>{q.symbols.length}</div>
                    <div className="hint">{q.desc}</div>
                    <div className="hint" style={{ marginTop: 8 }}>
                      平均收益 {q.avgReturn ?? '—'}% · 平均波动 {q.avgVol ?? '—'}%
                    </div>
                    <div className="weight-list" style={{ marginTop: 10 }}>
                      {q.symbols.length ? q.symbols.map((sym) => (
                        <span key={sym} className="weight-pill" onClick={() => goAnalyze(data.market, sym)}>
                          {sym}{q.bestSymbol === sym ? ' ★' : ''}
                        </span>
                      )) : <span className="hint">暂无股票</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {pathStability && (
            <div className="panel">
              <h3 className="section-title">
                路径稳定性 <span className="hint">从真实归一化走势计算日胜率、连跌和基准线上方时间</span>
              </h3>
              <div className="bt-cards quality-cards">
                <div className="bt-card clickable" onClick={() => goAnalyze(data.market, pathStability.bestWinRate.symbol)}>
                  <div className="k">日胜率最高</div>
                  <div className="v">{pathStability.bestWinRate.symbol}</div>
                  <div className="hint">{pathStability.bestWinRate.positiveRate.toFixed(1)}%</div>
                </div>
                <div className="bt-card clickable" onClick={() => goAnalyze(data.market, pathStability.shortestLossStreak.symbol)}>
                  <div className="k">最长连跌最短</div>
                  <div className="v">{pathStability.shortestLossStreak.symbol}</div>
                  <div className="hint">{pathStability.shortestLossStreak.maxLossStreak} 天</div>
                </div>
                <div className="bt-card clickable" onClick={() => goAnalyze(data.market, pathStability.bestAboveBase.symbol)}>
                  <div className="k">基准线上方最多</div>
                  <div className="v">{pathStability.bestAboveBase.symbol}</div>
                  <div className="hint">{pathStability.bestAboveBase.aboveBaseRate.toFixed(1)}% 的交易日</div>
                </div>
                <div className="bt-card clickable" onClick={() => goAnalyze(data.market, pathStability.bestRecent20.symbol)}>
                  <div className="k">近20日最强</div>
                  <div className="v">{pathStability.bestRecent20.symbol}</div>
                  <div className="hint">{pathStability.bestRecent20.recent20Return > 0 ? '+' : ''}{pathStability.bestRecent20.recent20Return.toFixed(2)}%</div>
                </div>
              </div>
              <div className="corr-wrap">
                <table className="compact-table">
                  <thead>
                    <tr><th>代码</th><th>日胜率</th><th>最长连跌</th><th>基准线上方</th><th>近20日</th><th>当前回撤</th></tr>
                  </thead>
                  <tbody>
                    {pathStability.rows.map((row) => (
                      <tr key={row.symbol} className="clickable" onClick={() => goAnalyze(data.market, row.symbol)}>
                        <td style={{ fontWeight: 700 }}>{row.symbol}</td>
                        <td>{row.positiveRate.toFixed(1)}%</td>
                        <td>{row.maxLossStreak}天</td>
                        <td>{row.aboveBaseRate.toFixed(1)}%</td>
                        <td className={row.recent20Return > 0 ? 'delta-pos' : row.recent20Return < 0 ? 'delta-neg' : 'delta-zero'}>
                          {row.recent20Return > 0 ? '+' : ''}{row.recent20Return.toFixed(2)}%
                        </td>
                        <td className="delta-neg">{row.currentDrawdown?.toFixed(2) ?? '—'}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {coMovementDays && (
            <div className="panel">
              <h3 className="section-title">
                共振交易日 <span className="hint">统计这组股票同涨、同跌和分化的真实交易日</span>
              </h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>
                最新交易日 {coMovementDays.latest.date}:上涨 {coMovementDays.latest.upCount} 只 / 下跌 {coMovementDays.latest.downCount} 只 ·
                平均涨跌 {coMovementDays.latest.avgRet > 0 ? '+' : ''}{coMovementDays.latest.avgRet.toFixed(2)}%
              </div>
              <div className="bt-cards quality-cards">
                <div className="bt-card">
                  <div className="k">同涨日</div>
                  <div className="v">{coMovementDays.allUpDays}</div>
                  <div className="hint">占 {((coMovementDays.allUpDays / coMovementDays.totalDays) * 100).toFixed(1)}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">同跌日</div>
                  <div className="v">{coMovementDays.allDownDays}</div>
                  <div className="hint">占 {((coMovementDays.allDownDays / coMovementDays.totalDays) * 100).toFixed(1)}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">分化日</div>
                  <div className="v">{coMovementDays.splitDays}</div>
                  <div className="hint">占 {((coMovementDays.splitDays / coMovementDays.totalDays) * 100).toFixed(1)}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">统计交易日</div>
                  <div className="v">{coMovementDays.totalDays}</div>
                </div>
              </div>
              <table className="compact-table">
                <thead><tr><th>类型</th><th>日期</th><th>上涨/下跌</th><th>平均涨跌</th></tr></thead>
                <tbody>
                  <tr><td>最强共振日</td><td>{coMovementDays.strongest.date}</td><td>{coMovementDays.strongest.upCount}/{coMovementDays.strongest.downCount}</td><td className="delta-pos">+{coMovementDays.strongest.avgRet.toFixed(2)}%</td></tr>
                  <tr><td>最弱共振日</td><td>{coMovementDays.weakest.date}</td><td>{coMovementDays.weakest.upCount}/{coMovementDays.weakest.downCount}</td><td className="delta-neg">{coMovementDays.weakest.avgRet.toFixed(2)}%</td></tr>
                  <tr><td>典型分化日</td><td>{coMovementDays.topDivergence.date}</td><td>{coMovementDays.topDivergence.upCount}/{coMovementDays.topDivergence.downCount}</td><td>{coMovementDays.topDivergence.avgRet > 0 ? '+' : ''}{coMovementDays.topDivergence.avgRet.toFixed(2)}%</td></tr>
                </tbody>
              </table>
            </div>
          )}

          {tailRiskDays && (
            <div className="panel">
              <h3 className="section-title">
                尾部风险日 <span className="hint">识别多股同时大跌和单股极端下跌的真实交易日</span>
              </h3>
              <div className="warning" style={{ margin: '0 0 14px' }}>
                最新交易日 {tailRiskDays.latest.date}: {tailRiskDays.latest.lossCount} 只跌幅≤-2% ·
                最弱 {tailRiskDays.latest.worstSymbol} {tailRiskDays.latest.worstRet.toFixed(2)}%
              </div>
              <div className="bt-cards quality-cards">
                <div className="bt-card">
                  <div className="k">广泛大跌日</div>
                  <div className="v">{tailRiskDays.broadLossDays}</div>
                  <div className="hint">至少 {tailRiskDays.broadLossThreshold} 只跌幅≤-2%</div>
                </div>
                <div className="bt-card">
                  <div className="k">近20日广泛大跌</div>
                  <div className="v">{tailRiskDays.recentBroadLossDays}</div>
                </div>
                <div className="bt-card">
                  <div className="k">单股极端下跌日</div>
                  <div className="v">{tailRiskDays.severeDays}</div>
                  <div className="hint">至少1只跌幅≤-5%</div>
                </div>
                <div className="bt-card">
                  <div className="k">最差平均日</div>
                  <div className="v">{tailRiskDays.worstAverageDay.avgRet.toFixed(2)}%</div>
                  <div className="hint">{tailRiskDays.worstAverageDay.date}</div>
                </div>
              </div>
              <table className="compact-table">
                <thead><tr><th>类型</th><th>日期</th><th>大跌只数</th><th>最弱股票</th><th>平均涨跌</th></tr></thead>
                <tbody>
                  <tr>
                    <td>最差平均日</td><td>{tailRiskDays.worstAverageDay.date}</td>
                    <td>{tailRiskDays.worstAverageDay.lossCount}</td>
                    <td>{tailRiskDays.worstAverageDay.worstSymbol} {tailRiskDays.worstAverageDay.worstRet.toFixed(2)}%</td>
                    <td className="delta-neg">{tailRiskDays.worstAverageDay.avgRet.toFixed(2)}%</td>
                  </tr>
                  <tr>
                    <td>最大单股跌幅</td><td>{tailRiskDays.worstSingleDay.date}</td>
                    <td>{tailRiskDays.worstSingleDay.lossCount}</td>
                    <td>{tailRiskDays.worstSingleDay.worstSymbol} {tailRiskDays.worstSingleDay.worstRet.toFixed(2)}%</td>
                    <td className={tailRiskDays.worstSingleDay.avgRet > 0 ? 'delta-pos' : 'delta-neg'}>{tailRiskDays.worstSingleDay.avgRet.toFixed(2)}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {riskLeaders.length > 0 && (
            <div className="panel">
              <h3 className="section-title">
                风险榜单 <span className="hint">基于真实历史K线计算的波动、回撤和收益/波动</span>
              </h3>
              <div className="bt-cards quality-cards">
                {riskLeaders.map((item) => (
                  <div
                    key={`${item.label}-${item.row.symbol}`}
                    className="bt-card clickable"
                    onClick={() => goAnalyze(data.market, item.row.symbol)}
                  >
                    <div className="k">{item.label}</div>
                    <div className="v" style={{ color: item.tone }}>{item.row.symbol}</div>
                    <div className="hint">
                      {nameOf(item.row.symbol) || '—'} · {item.value}{item.suffix}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {data.period_strength && (
            <div className="panel">
              <h3 className="section-title">
                多周期强弱 <span className="hint">1月/3月/6月/区间真实收益赢家与输家</span>
              </h3>
              <div className="period-grid">
                {['return_1m', 'return_3m', 'return_6m', 'return_pct'].map((key) => {
                  const item = data.period_strength[key]
                  if (!item) return null
                  return (
                    <div className="period-card" key={key}>
                      <div className="period-title">{item.label}</div>
                      <div><span className="delta-pos">{item.best_symbol} {item.best_return > 0 ? '+' : ''}{item.best_return}%</span></div>
                      <div><span className="delta-neg">{item.worst_symbol} {item.worst_return > 0 ? '+' : ''}{item.worst_return}%</span></div>
                    </div>
                  )
                })}
              </div>
              {data.period_strength.consistency?.strongest && (
                <div className="hint" style={{ marginTop: 12 }}>
                  多周期偏强:{data.period_strength.consistency.strongest.symbol}
                  ({data.period_strength.consistency.strongest.positive_periods}/{data.period_strength.consistency.strongest.valid_periods} 个周期上涨) ·
                  多周期偏弱:{data.period_strength.consistency.weakest.symbol}
                  ({data.period_strength.consistency.weakest.negative_periods}/{data.period_strength.consistency.weakest.valid_periods} 个周期下跌)
                </div>
              )}
            </div>
          )}

          {selectedPortfolio && (
            <div className="panel">
              <h3 className="section-title">
                组合模拟 <span className="hint">基于已选股票真实历史价格</span>
              </h3>
              {data.portfolios?.length > 1 && (
                <div className="segmented" style={{ marginBottom: 12 }}>
                  {data.portfolios.map((p) => (
                    <button
                      key={p.type}
                      className={portfolioType === p.type ? 'active' : ''}
                      onClick={() => setPortfolioType(p.type)}
                    >
                      {p.type}
                    </button>
                  ))}
                </div>
              )}
              <div className="bt-cards portfolio-cards">
                <div className="bt-card">
                  <div className="k">组合收益</div>
                  <div className="v" style={{ color: selectedPortfolio.return_pct > 0 ? 'var(--up)' : 'var(--down)' }}>
                    {selectedPortfolio.return_pct > 0 ? '+' : ''}{selectedPortfolio.return_pct}%
                  </div>
                </div>
                <div className="bt-card">
                  <div className="k">年化波动</div>
                  <div className="v">{selectedPortfolio.annual_vol}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">最大回撤</div>
                  <div className="v" style={{ color: 'var(--down)' }}>{selectedPortfolio.max_drawdown}%</div>
                </div>
                <div className="bt-card">
                  <div className="k">收益/波动</div>
                  <div className="v">{selectedPortfolio.risk_adjusted ?? '—'}</div>
                </div>
              </div>
              <PortfolioLine portfolio={selectedPortfolio} />
              <div className="weight-list">
                {Object.entries(selectedPortfolio.weights || {}).map(([sym, w]) => (
                  <span key={sym} className="weight-pill">{sym} {(w * 100).toFixed(1)}%</span>
                ))}
              </div>
            </div>
          )}

          <div className="panel">
            <h3 className="section-title">
              归一化走势 <span className="hint">{data.summary}</span>
            </h3>
            <div className="legend">
              {data.symbols.map((sym, i) => (
                <span key={sym} className="legend-item">
                  <i style={{ background: COLORS[i % COLORS.length] }} />{sym}{nameOf(sym) ? ` ${nameOf(sym)}` : ''}
                </span>
              ))}
            </div>
            <CompareLines data={data} />
          </div>

          <div className="panel">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <h3 className="section-title" style={{ marginBottom: 0 }}>
                对比表 <span className="hint">按区间收益排序 · 点击代码查看单股详情</span>
              </h3>
              <button className="ghost" onClick={exportCsv}>导出 CSV</button>
            </div>
            {data.fundamentals_included && data.fundamental_summary?.best_quality_symbol && (
              <div className="warning" style={{ margin: '14px 0 0' }}>
                综合优先:{data.best_composite?.symbol}
                ({data.best_composite?.verdict} {data.best_composite?.score})。
                基本面最高:{data.fundamental_summary.best_quality_symbol}
                ({data.fundamental_summary.best_quality_rating} {data.fundamental_summary.best_quality_score})。
                {data.fundamental_summary.lowest_pe_percentile_symbol && (
                  <> PE历史分位最低:{data.fundamental_summary.lowest_pe_percentile_symbol}
                    ({data.fundamental_summary.lowest_pe_percentile}%)。</>
                )}
              </div>
            )}
            <div style={{ height: 16 }} />
            <div className="corr-wrap">
            <table className={data.fundamentals_included ? 'multi-table wide' : 'multi-table'}>
              <thead>
                <tr>
                  <th>#</th><th>代码</th><th>名称</th>
                  <SortTh label="综合" sortKey="composite_score" sort={sort} setSort={setSort} />
                  <SortTh label="1月" sortKey="return_1m" sort={sort} setSort={setSort} />
                  <SortTh label="3月" sortKey="return_3m" sort={sort} setSort={setSort} />
                  <SortTh label="6月" sortKey="return_6m" sort={sort} setSort={setSort} />
                  <SortTh label="区间收益" sortKey="return_pct" sort={sort} setSort={setSort} />
                  <SortTh label="年化波动" sortKey="annual_vol" sort={sort} setSort={setSort} />
                  <SortTh label="最大回撤" sortKey="max_drawdown" sort={sort} setSort={setSort} />
                  <SortTh label="收益/波动" sortKey="risk_adjusted" sort={sort} setSort={setSort} />
                  <SortTh label="技术评分" sortKey="score" sort={sort} setSort={setSort} />
                  <th>方向</th>
                  {data.fundamentals_included && (
                    <>
                      <SortTh label="基本面" sortKey="fundamental_score" sort={sort} setSort={setSort} />
                      <SortTh label="ROE" sortKey="roe" sort={sort} setSort={setSort} />
                      <SortTh label="毛利率" sortKey="gross_margin" sort={sort} setSort={setSort} />
                      <SortTh label="净利率" sortKey="net_margin" sort={sort} setSort={setSort} />
                      <SortTh label="负债率" sortKey="debt_ratio" sort={sort} setSort={setSort} />
                      <SortTh label="现金流质量" sortKey="cashflow_quality" sort={sort} setSort={setSort} />
                      <SortTh label="PE分位" sortKey="pe_percentile" sort={sort} setSort={setSort} />
                      <SortTh label="PB分位" sortKey="pb_percentile" sort={sort} setSort={setSort} />
                      <th>连续增长</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {sortedMetrics.map((r, i) => (
                  <tr key={r.symbol} className="clickable" onClick={() => goAnalyze(data.market, r.symbol)}>
                    <td className="rank-idx">{i + 1}</td>
                    <td style={{ fontWeight: 700 }}>{r.symbol}</td>
                    <td className="hint" style={{ color: 'var(--text)' }}>{nameOf(r.symbol) || '—'}</td>
                    <td>
                      <div className="rank-score">
                        <span className="rank-num" style={{ color: scoreColor(r.composite_score) }}>{r.composite_score}</span>
                        <div className="bar"><div style={{ width: `${r.composite_score}%`, background: scoreColor(r.composite_score) }} /></div>
                      </div>
                      <div className="hint">{r.composite_verdict}</div>
                    </td>
                    <td className={r.return_1m > 0 ? 'delta-pos' : r.return_1m < 0 ? 'delta-neg' : 'delta-zero'}>
                      {r.return_1m == null ? '—' : `${r.return_1m > 0 ? '+' : ''}${r.return_1m}%`}
                    </td>
                    <td className={r.return_3m > 0 ? 'delta-pos' : r.return_3m < 0 ? 'delta-neg' : 'delta-zero'}>
                      {r.return_3m == null ? '—' : `${r.return_3m > 0 ? '+' : ''}${r.return_3m}%`}
                    </td>
                    <td className={r.return_6m > 0 ? 'delta-pos' : r.return_6m < 0 ? 'delta-neg' : 'delta-zero'}>
                      {r.return_6m == null ? '—' : `${r.return_6m > 0 ? '+' : ''}${r.return_6m}%`}
                    </td>
                    <td className={r.return_pct > 0 ? 'delta-pos' : r.return_pct < 0 ? 'delta-neg' : 'delta-zero'}>
                      {r.return_pct > 0 ? '+' : ''}{r.return_pct}%
                    </td>
                    <td>{r.annual_vol}%</td>
                    <td className="delta-neg">{r.max_drawdown}%</td>
                    <td>{r.risk_adjusted ?? '—'}</td>
                    <td>
                      <div className="rank-score">
                        <span className="rank-num" style={{ color: scoreColor(r.score) }}>{r.score}</span>
                        <div className="bar"><div style={{ width: `${r.score}%`, background: scoreColor(r.score) }} /></div>
                      </div>
                    </td>
                    <td><span className={`badge ${dirClass(r.direction)}`} style={{ fontSize: 12, padding: '3px 10px' }}>{r.direction}</span></td>
                    {data.fundamentals_included && (
                      <>
                        <td>
                          {r.fundamental_available ? (
                            <div className="rank-score">
                              <span className="rank-num" style={{ color: scoreColor(r.fundamental_score) }}>{r.fundamental_score}</span>
                              <div className="bar"><div style={{ width: `${r.fundamental_score}%`, background: scoreColor(r.fundamental_score) }} /></div>
                            </div>
                          ) : <span className="hint">{r.fundamental_error || '—'}</span>}
                        </td>
                        <td>{r.roe ?? '—'}{r.roe != null ? '%' : ''}</td>
                        <td>{r.gross_margin ?? '—'}{r.gross_margin != null ? '%' : ''}</td>
                        <td>{r.net_margin ?? '—'}{r.net_margin != null ? '%' : ''}</td>
                        <td>{r.debt_ratio ?? '—'}{r.debt_ratio != null ? '%' : ''}</td>
                        <td>{r.cashflow_quality ?? '—'}</td>
                        <td>{r.pe_percentile ?? '—'}{r.pe_percentile != null ? '%' : ''}</td>
                        <td>{r.pb_percentile ?? '—'}{r.pb_percentile != null ? '%' : ''}</td>
                        <td>
                          {r.revenue_growth_years ?? '—'} / {r.profit_growth_years ?? '—'} 年
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
            {data.failed_count > 0 && (
              <p className="hint" style={{ marginTop: 12 }}>
                未纳入:{data.failed.map((f) => `${f.symbol}(${f.error})`).join(' ; ')}
              </p>
            )}
            {data.fundamental_failed_count > 0 && (
              <p className="hint" style={{ marginTop: 8 }}>
                基本面未纳入:{data.fundamental_failed.map((f) => `${f.symbol}(${f.error})`).join(' ; ')}
              </p>
            )}
          </div>

          <div className="panel">
            <h3 className="section-title">
              相关性矩阵 <span className="hint">越接近 1 越同涨同跌,越接近 -1 越反向</span>
            </h3>
            <div className="corr-wrap">
              <table className="corr-table">
                <thead>
                  <tr><th></th>{data.symbols.map((s) => <th key={s}>{s}</th>)}</tr>
                </thead>
                <tbody>
                  {data.symbols.map((row) => (
                    <tr key={row}>
                      <th>{row}</th>
                      {data.symbols.map((col) => {
                        const v = data.correlations?.[row]?.[col] ?? 0
                        return (
                          <td key={col} style={{ background: corrColor(v) }}>
                            {v.toFixed(2)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
