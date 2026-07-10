import { useEffect, useMemo, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { analyzeFund, analyzeFundOverlap, compareFunds, fetchFundAlternatives, fetchFundCategories, fetchFundDividends, fetchFundOpportunities, fetchFundPeers, fetchFundPortfolio, fetchHotFunds, searchFunds } from '../api'

const COLORS = ['#48a6ff', '#20c486', '#f05d68', '#d8a833', '#9d7cff', '#26c6da', '#ff8a3d', '#a6e22e']

const CATEGORIES = [
  ['all', '全部'],
  ['stock', '股票型'],
  ['hybrid', '混合型'],
  ['bond', '债券型'],
  ['index', '指数型'],
  ['qdii', 'QDII'],
  ['fof', 'FOF'],
]

const SORTS = [
  ['1y', '近1年'],
  ['ytd', '今年来'],
  ['6m', '近6月'],
  ['3m', '近3月'],
  ['1m', '近1月'],
]

const RISK_OPTIONS = [
  ['stable', '稳健'],
  ['balanced', '均衡'],
  ['aggressive', '进取'],
]

const FUND_VIEWS = [
  ['discover', '发现基金', '从真实榜单和分类热度中建立候选池'],
  ['research', '研究基金', '将单只基金的数据转化为可复盘的决策框架'],
  ['compare', '比较与替换', '比较多只基金的风险、相关性与重复暴露'],
]

function pct(v) {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function num(v, digits = 2) {
  if (v == null) return '-'
  return Number(v).toFixed(digits)
}

function metricText(metric) {
  if (metric?.value == null) return '-'
  if (metric.unit === '只' || metric.unit === '组') return `${Number(metric.value).toFixed(0)}${metric.unit}`
  if (metric.unit === '%') return `${Number(metric.value).toFixed(2)}%`
  return `${num(metric.value)}${metric.unit || ''}`
}

function deltaClass(v) {
  if (v > 0) return 'delta-pos'
  if (v < 0) return 'delta-neg'
  return 'delta-zero'
}

function parseCodes(text) {
  return String(text || '').split(/[\s,，、;；]+/).map((s) => s.trim()).filter(Boolean)
}

function FundLineChart({ data }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !data?.length) return
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
    const navSeries = chart.addLineSeries({
      color: '#5b8cff',
      lineWidth: 2,
      priceLineVisible: false,
      title: '单位净值',
    })
    navSeries.setData(data.map((r) => ({ time: r.date, value: Number(r.unit_nav) })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data])

  return <div ref={ref} className="chart small" />
}

function FundCompareChart({ data }) {
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
    data.codes.forEach((code, i) => {
      const series = chart.addLineSeries({
        color: COLORS[i % COLORS.length],
        lineWidth: 2,
        priceLineVisible: false,
        title: code,
      })
      series.setData(data.rebased
        .filter((r) => r[code] != null)
        .map((r) => ({ time: r.date, value: Number(r[code]) })))
    })
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data])

  return <div ref={ref} className="chart small" />
}

function MetricCard({ label, value, cls = '' }) {
  return (
    <div className="bt-card">
      <div className="k">{label}</div>
      <div className={`v ${cls}`}>{value}</div>
    </div>
  )
}

export default function FundTab() {
  const [fundView, setFundView] = useState('discover')
  const [researchLayer, setResearchLayer] = useState('decision')
  const [category, setCategory] = useState('all')
  const [sort, setSort] = useState('1y')
  const [limit, setLimit] = useState(30)
  const [months, setMonths] = useState(36)
  const [code, setCode] = useState('')
  const [hot, setHot] = useState(null)
  const [categories, setCategories] = useState([])
  const [fund, setFund] = useState(null)
  const [portfolio, setPortfolio] = useState(null)
  const [portfolioError, setPortfolioError] = useState('')
  const [peers, setPeers] = useState(null)
  const [peerSort, setPeerSort] = useState('1y')
  const [dividends, setDividends] = useState(null)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [compareInput, setCompareInput] = useState('110022 001480 006502')
  const [compareData, setCompareData] = useState(null)
  const [overlapData, setOverlapData] = useState(null)
  const [opportunityRisk, setOpportunityRisk] = useState('balanced')
  const [opportunities, setOpportunities] = useState(null)
  const [alternatives, setAlternatives] = useState(null)
  const [loadingHot, setLoadingHot] = useState(false)
  const [loadingFund, setLoadingFund] = useState(false)
  const [loadingPortfolio, setLoadingPortfolio] = useState(false)
  const [loadingPeers, setLoadingPeers] = useState(false)
  const [loadingDividends, setLoadingDividends] = useState(false)
  const [loadingSearch, setLoadingSearch] = useState(false)
  const [loadingCompare, setLoadingCompare] = useState(false)
  const [loadingOverlap, setLoadingOverlap] = useState(false)
  const [loadingOpportunities, setLoadingOpportunities] = useState(false)
  const [loadingAlternatives, setLoadingAlternatives] = useState(false)
  const [error, setError] = useState('')

  async function loadHot(nextCategory = category, nextSort = sort) {
    setLoadingHot(true); setError('')
    try {
      const data = await fetchHotFunds(nextCategory, limit, nextSort)
      setHot(data)
      const first = data.items?.[0]
      if (first && !code) setCode(first.code)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingHot(false)
    }
  }

  async function loadFund(nextCode = code, nextMonths = months) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) {
      setError('请输入 6 位基金代码')
      return
    }
    setFundView('research'); setResearchLayer('decision')
    setLoadingFund(true); setError('')
    setPortfolio(null); setPortfolioError('')
    setPeers(null)
    setDividends(null)
    setAlternatives(null)
    try {
      const data = await analyzeFund(clean, nextMonths)
      setFund(data)
      setCode(clean)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingFund(false)
    }
  }

  async function loadPortfolio(nextCode = code) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) return
    setLoadingPortfolio(true); setPortfolioError('')
    try {
      const data = await fetchFundPortfolio(clean)
      setPortfolio(data)
    } catch (e) {
      setPortfolioError(e.message)
    } finally {
      setLoadingPortfolio(false)
    }
  }

  async function loadPeers(nextCode = code, nextSort = peerSort) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) return
    setLoadingPeers(true)
    try {
      const data = await fetchFundPeers(clean, nextSort, 1000)
      setPeers(data)
    } catch (e) {
      setPeers({ error: e.message })
    } finally {
      setLoadingPeers(false)
    }
  }

  async function loadDividends(nextCode = code) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) return
    setLoadingDividends(true)
    try {
      const data = await fetchFundDividends(clean)
      setDividends(data)
    } catch (e) {
      setDividends({ error: e.message })
    } finally {
      setLoadingDividends(false)
    }
  }

  async function runSearch() {
    const kw = searchKeyword.trim()
    if (!kw) return
    setLoadingSearch(true); setError('')
    try {
      const data = await searchFunds(kw, 12)
      setSearchResults(data.items || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingSearch(false)
    }
  }

  async function runCompare() {
    const codes = parseCodes(compareInput)
    if (codes.length < 2) {
      setError('至少输入 2 只基金代码进行对比')
      return
    }
    setLoadingCompare(true); setError('')
    try {
      const data = await compareFunds(codes, months)
      setCompareData(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingCompare(false)
    }
  }

  async function runOverlap() {
    const codes = parseCodes(compareInput)
    if (codes.length < 2) {
      setError('至少输入 2 只基金代码进行持仓重合度分析')
      return
    }
    setLoadingOverlap(true); setError('')
    try {
      const data = await analyzeFundOverlap(codes)
      setOverlapData(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingOverlap(false)
    }
  }

  async function loadOpportunities(nextRisk = opportunityRisk) {
    setLoadingOpportunities(true); setError('')
    try {
      const data = await fetchFundOpportunities(nextRisk, 5)
      setOpportunities(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingOpportunities(false)
    }
  }

  async function loadAlternatives(nextCode = code, nextSort = peerSort) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) {
      setError('请输入 6 位基金代码')
      return
    }
    setLoadingAlternatives(true); setError('')
    try {
      const data = await fetchFundAlternatives(clean, nextSort, 5, months)
      setAlternatives(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingAlternatives(false)
    }
  }

  async function loadCategories() {
    try {
      const data = await fetchFundCategories()
      setCategories(data.items || [])
    } catch (e) {
      setCategories([])
    }
  }

  useEffect(() => {
    loadHot()
    loadCategories()
    loadOpportunities('balanced')
  }, [])

  useEffect(() => {
    if (fundView !== 'research' || researchLayer !== 'evidence' || !fund?.code) return
    loadPortfolio(fund.code)
    loadPeers(fund.code, peerSort)
    loadDividends(fund.code)
  }, [fundView, researchLayer, fund?.code])

  const rows = hot?.items || []
  const selectedName = fund?.name || rows.find((r) => r.code === code)?.name || ''
  const categoryHeat = useMemo(() => categories || [], [categories])
  const factSheet = fund?.fact_sheet || null
  const assetLatest = factSheet?.asset_latest || {}
  const manager = factSheet?.managers?.[0]
  const flowSummary = factSheet?.flow_summary || {}
  const fundEvaluation = factSheet?.performance_evaluation || null
  const similarPercentile = factSheet?.similar_percentile || null
  const benchmarkComparison = factSheet?.benchmark_comparison || null
  const currentView = FUND_VIEWS.find(([id]) => id === fundView) || FUND_VIEWS[0]

  return (
    <>
      <section className="workspace-header">
        <div>
          <span className="eyebrow">基金中心</span>
          <h2>{currentView[1]}</h2>
          <p>{currentView[2]}。所有排序、净值和持仓披露均标注真实来源。</p>
        </div>
        <div className="workspace-nav" role="tablist" aria-label="基金中心功能">
          {FUND_VIEWS.map(([id, label]) => (
            <button key={id} className={fundView === id ? 'active' : ''} onClick={() => setFundView(id)}>{label}</button>
          ))}
        </div>
      </section>

      <div className="panel">
        <div className="form-row">
          {fundView === 'discover' && <>
            <div className="field">
              <label>基金分类</label>
              <select value={category} onChange={(e) => { setCategory(e.target.value); loadHot(e.target.value, sort) }}>
                {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </div>
            <div className="field">
              <label>排序窗口</label>
              <select value={sort} onChange={(e) => { setSort(e.target.value); loadHot(category, e.target.value) }}>
                {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </div>
            <div className="field">
              <label>榜单数量</label>
              <input type="number" min="5" max="100" value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
            </div>
            <button onClick={() => loadHot()} disabled={loadingHot}>
              {loadingHot ? <><span className="spinner" /> 加载中</> : '刷新基金榜'}
            </button>
          </>}
          {fundView === 'research' && <>
            <div className="field">
              <label>基金代码</label>
              <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="例如 110022" />
            </div>
            <div className="field">
              <label>净值周期(月)</label>
              <input type="number" min="6" max="120" value={months} onChange={(e) => setMonths(Number(e.target.value))} />
            </div>
            <button onClick={() => loadFund()} disabled={loadingFund}>
              {loadingFund ? <><span className="spinner" /> 分析中</> : '研究基金'}
            </button>
            <div className="field">
              <label>基金搜索</label>
              <input value={searchKeyword} onChange={(e) => setSearchKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') runSearch() }}
                placeholder="代码 / 名称 / 拼音" />
            </div>
            <button className="ghost" onClick={runSearch} disabled={loadingSearch}>
              {loadingSearch ? <><span className="spinner" /> 搜索中</> : '搜索基金'}
            </button>
          </>}
          {fundView === 'compare' && <span className="hint">输入两只或以上基金后，比较真实净值、波动、回撤和披露持仓重合。</span>}
        </div>
        {fundView === 'research' && searchResults.length > 0 && (
          <div className="fund-search-results">
            {searchResults.map((item) => (
              <button key={item.code} className="fund-search-item" onClick={() => {
                setCode(item.code)
                loadFund(item.code, months)
              }}>
                <b>{item.code}</b>
                <span>{item.name}</span>
                <small>{item.type}</small>
              </button>
            ))}
          </div>
        )}
        {fundView === 'discover' && hot && <p className="hint" style={{ marginTop: 12 }}>数据源: {hot.source}，截至 {hot.as_of}；高收益只代表历史表现，仍需继续研究回撤和持仓。</p>}
        {error && <div className="error">{error}</div>}
      </div>

      {fundView === 'discover' && <>
      <div className="panel fade-in">
        <h3 className="section-title">
          基金机会雷达 <span className="hint">基于真实榜单筛选候选，高分只代表更值得进一步研究</span>
        </h3>
        <div className="form-row" style={{ marginBottom: 14 }}>
          <div className="field">
            <label>风险偏好</label>
            <select value={opportunityRisk} onChange={(e) => {
              setOpportunityRisk(e.target.value)
              loadOpportunities(e.target.value)
            }}>
              {RISK_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <button onClick={() => loadOpportunities()} disabled={loadingOpportunities}>
            {loadingOpportunities ? <><span className="spinner" /> 筛选中</> : '刷新机会'}
          </button>
          {opportunities && <span className="hint">数据源: {opportunities.source} · 截至 {opportunities.as_of || '-'}</span>}
        </div>

        {opportunities && (
          <>
            <div className="fund-opportunity-grid">
              {opportunities.buckets.map((bucket) => (
                <div className="fund-opportunity-card" key={bucket.key}>
                  <h4 className="fund-subhead">{bucket.name} <span className="hint">{bucket.profile}</span></h4>
                  <div className="corr-wrap">
                    <table className="compact-table fund-opportunity-table">
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>名称</th>
                          <th>分数</th>
                          <th>近3月</th>
                          <th>近1年</th>
                          <th>规模</th>
                          <th>提示</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bucket.items.map((row) => (
                          <tr key={row.code} className="clickable" onClick={() => loadFund(row.code, months)}>
                            <td style={{ fontWeight: 800 }}>{row.code}</td>
                            <td>{row.name}</td>
                            <td>{num(row.opportunity_score, 1)}</td>
                            <td className={deltaClass(row.return_3m)}>{pct(row.return_3m)}</td>
                            <td className={deltaClass(row.return_1y)}>{pct(row.return_1y)}</td>
                            <td>{row.scale_yi != null ? `${num(row.scale_yi)}亿` : '-'}</td>
                            <td>{row.cautions?.slice(-1)[0] || '-'}</td>
                          </tr>
                        ))}
                        {!bucket.items.length && (
                          <tr><td colSpan="7" className="hint">当前真实榜单下没有满足筛选条件的候选</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
            {opportunities.failed?.length > 0 && (
              <div className="error" style={{ marginTop: 12 }}>
                {opportunities.failed.map((x) => `${x.name}: ${x.error}`).join('；')}
              </div>
            )}
            <p className="hint" style={{ marginTop: 12 }}>
              {opportunities.method.score} {opportunities.risk_note}
            </p>
          </>
        )}
      </div>

      {categoryHeat.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">基金分类热度</h3>
          <div className="fund-category-grid">
            {categoryHeat.map((c) => (
              <button key={c.category} className={`fund-category ${category === c.category ? 'active' : ''}`}
                onClick={() => { setCategory(c.category); loadHot(c.category, sort) }}>
                <span>{c.name}</span>
                <b className={deltaClass(c.avg_3m)}>{pct(c.avg_3m)}</b>
                <small>{c.heat} · 领涨 {c.leader_name || '-'}</small>
              </button>
            ))}
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">热门基金榜 <span className="hint">{hot.category_name} · {hot.sort}</span></h3>
          <div className="corr-wrap">
            <table className="compact-table fund-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>代码</th>
                  <th>基金简称</th>
                  <th>日期</th>
                  <th>单位净值</th>
                  <th>近1月</th>
                  <th>近3月</th>
                  <th>近6月</th>
                  <th>近1年</th>
                  <th>今年来</th>
                  <th>趋势</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.code} className={`clickable ${r.code === code ? 'row-active' : ''}`}
                    onClick={() => loadFund(r.code, months)}>
                    <td className="rank-idx">{r.rank}</td>
                    <td style={{ fontWeight: 800 }}>{r.code}</td>
                    <td>{r.name}</td>
                    <td>{r.date}</td>
                    <td>{num(r.unit_nav, 4)}</td>
                    <td className={deltaClass(r.return_1m)}>{pct(r.return_1m)}</td>
                    <td className={deltaClass(r.return_3m)}>{pct(r.return_3m)}</td>
                    <td className={deltaClass(r.return_6m)}>{pct(r.return_6m)}</td>
                    <td className={deltaClass(r.return_1y)}>{pct(r.return_1y)}</td>
                    <td className={deltaClass(r.return_ytd)}>{pct(r.return_ytd)}</td>
                    <td><span className="tag neutral">{r.trend}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      </>}

      {fundView === 'compare' && <div className="panel fade-in">
        <h3 className="section-title">
          多基金对比 <span className="hint">共同净值日期重算，首日=100，横向比较收益、回撤、波动和相关性</span>
        </h3>
        <div className="form-row">
          <div className="field fund-compare-input">
            <label>基金代码</label>
            <textarea value={compareInput} onChange={(e) => setCompareInput(e.target.value)}
              placeholder="例如: 110022 001480 006502" />
          </div>
          <button onClick={runCompare} disabled={loadingCompare}>
            {loadingCompare ? <><span className="spinner" /> 对比中</> : '开始对比'}
          </button>
          <button className="ghost" onClick={runOverlap} disabled={loadingOverlap}>
            {loadingOverlap ? <><span className="spinner" /> 分析中</> : '持仓重合度'}
          </button>
        </div>
        {compareData && (
          <>
            <div className="bt-cards quality-cards fund-leader-cards">
              <MetricCard label="近3月领先" value={`${compareData.leaders.best_3m.code} ${pct(compareData.leaders.best_3m.return_3m)}`} cls={deltaClass(compareData.leaders.best_3m.return_3m)} />
              <MetricCard label="近1年领先" value={`${compareData.leaders.best_1y.code} ${pct(compareData.leaders.best_1y.return_1y)}`} cls={deltaClass(compareData.leaders.best_1y.return_1y)} />
              <MetricCard label="低波动" value={`${compareData.leaders.lowest_vol.code} ${pct(compareData.leaders.lowest_vol.annual_volatility)}`} />
              <MetricCard label="低回撤" value={`${compareData.leaders.shallowest_drawdown.code} ${pct(compareData.leaders.shallowest_drawdown.max_drawdown)}`} cls="delta-neg" />
            </div>
            {compareData.portfolio_playbook && (
              <div className="fund-playbook-panel fund-batch-playbook">
                <h4 className="fund-subhead">批量投资经验手册</h4>
                <div className="fund-playbook-hero">
                  <div>
                    <span className="tag neutral">{compareData.portfolio_playbook.label}</span>
                    <h4>{compareData.portfolio_playbook.conclusion}</h4>
                    <div className="daily-tags">
                      {(compareData.portfolio_playbook.risk_flags || []).map((text) => (
                        <span className="tag neutral" key={text}>{text}</span>
                      ))}
                    </div>
                  </div>
                  <div className="playbook-review-grid">
                    {(compareData.portfolio_playbook.metrics || []).map((m) => (
                      <div className="playbook-review" key={m.name}>
                        <span>{m.name}</span>
                        <b className={m.unit === '%' ? deltaClass(m.value) : ''}>
                          {metricText(m)}
                        </b>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="playbook-grid">
                  <div>
                    <h4 className="fund-subhead">角色分布</h4>
                    <div className="playbook-rule-list">
                      {(compareData.portfolio_playbook.role_distribution || []).map((row) => (
                        <div className="playbook-rule" key={row.name}>
                          <b>{row.name} · {row.count}只</b>
                          <span>组合占比 {num(row.ratio)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">高相关提示</h4>
                    <div className="playbook-rule-list">
                      {(compareData.portfolio_playbook.high_corr_pairs || []).length > 0 ? (
                        compareData.portfolio_playbook.high_corr_pairs.map((row) => (
                          <div className="playbook-rule danger" key={`${row.a}-${row.b}`}>
                            <b>{row.a} / {row.b}</b>
                            <span>历史收益相关性 {num(row.correlation, 3)}，新增资金前先判断是否重复暴露。</span>
                          </div>
                        ))
                      ) : (
                        <div className="playbook-rule">
                          <b>未触发高相关红旗</b>
                          <span>当前共同净值样本中未发现相关性高于 0.85 的基金组合。</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <h4 className="fund-subhead">单只基金批量动作</h4>
                <div className="corr-wrap">
                  <table className="compact-table batch-action-table">
                    <thead>
                      <tr>
                        <th>基金</th>
                        <th>角色</th>
                        <th>动作</th>
                        <th>依据</th>
                        <th>注意</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(compareData.portfolio_playbook.fund_actions || []).map((row) => (
                        <tr key={row.code}>
                          <td><b>{row.code}</b><br />{row.name}</td>
                          <td>{row.risk_band || row.role || '-'}</td>
                          <td><span className="tag neutral">{row.action}</span></td>
                          <td>{row.reason}</td>
                          <td>{(row.cautions || []).join('；') || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="playbook-grid">
                  <div>
                    <h4 className="fund-subhead">批量规则</h4>
                    <div className="playbook-rule-list">
                      {(compareData.portfolio_playbook.batch_rules || []).map((row) => (
                        <div className="playbook-rule" key={row.title}>
                          <b>{row.title}</b>
                          <span>{row.text}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">执行步骤</h4>
                    <div className="playbook-rule-list">
                      {(compareData.portfolio_playbook.execution_steps || []).map((row) => (
                        <div className="playbook-rule" key={row.step}>
                          <b>{row.step}</b>
                          <span>{row.action}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <h4 className="fund-subhead">复盘问题</h4>
                <div className="fund-bond-list">
                  {(compareData.portfolio_playbook.review_questions || []).map((text) => (
                    <span className="tag neutral" key={text}>{text}</span>
                  ))}
                </div>
                <p className="hint" style={{ marginTop: 12 }}>{compareData.portfolio_playbook.method?.note}</p>
              </div>
            )}
            <FundCompareChart data={compareData} />
            <div className="corr-wrap">
              <table className="compact-table fund-compare-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>近1月</th>
                    <th>近3月</th>
                    <th>近6月</th>
                    <th>近1年</th>
                    <th>年化波动</th>
                    <th>最大回撤</th>
                    <th>定投分</th>
                  </tr>
                </thead>
                <tbody>
                  {compareData.items.map((r) => (
                    <tr key={r.code} className="clickable" onClick={() => loadFund(r.code, months)}>
                      <td style={{ fontWeight: 800 }}>{r.code}</td>
                      <td>{r.name}</td>
                      <td className={deltaClass(r.return_1m)}>{pct(r.return_1m)}</td>
                      <td className={deltaClass(r.return_3m)}>{pct(r.return_3m)}</td>
                      <td className={deltaClass(r.return_6m)}>{pct(r.return_6m)}</td>
                      <td className={deltaClass(r.return_1y)}>{pct(r.return_1y)}</td>
                      <td>{pct(r.annual_volatility)}</td>
                      <td className="delta-neg">{pct(r.max_drawdown)}</td>
                      <td>{r.dca_score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {overlapData && (
          <div className="fund-overlap-block">
            <div className="bt-cards quality-cards">
              <MetricCard label="平均个股重合" value={pct(overlapData.summary.avg_stock_overlap_weight)} />
              <MetricCard label="平均行业重合" value={pct(overlapData.summary.avg_industry_overlap_weight)} />
              <MetricCard label="高重合组合" value={`${overlapData.summary.high_overlap_pair_count}/${overlapData.summary.pair_count}`} />
              <MetricCard label="结论" value={overlapData.summary.conclusion} />
            </div>
            <div className="fund-holding-grid">
              <div>
                <h4 className="fund-subhead">基金两两重合</h4>
                <div className="corr-wrap">
                  <table className="compact-table fund-overlap-table">
                    <thead>
                      <tr>
                        <th>基金组合</th>
                        <th>共同股数</th>
                        <th>个股重合</th>
                        <th>行业重合</th>
                        <th>等级</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overlapData.pairwise.map((r) => (
                        <tr key={`${r.fund_a}-${r.fund_b}`}>
                          <td>{r.fund_a} / {r.fund_b}</td>
                          <td>{r.common_stock_count}</td>
                          <td>{pct(r.stock_overlap_weight)}</td>
                          <td>{pct(r.industry_overlap_weight)}</td>
                          <td><span className="tag neutral">{r.level}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div>
                <h4 className="fund-subhead">共同重仓股</h4>
                <div className="fund-bond-list">
                  {overlapData.shared_stocks.slice(0, 12).map((r) => (
                    <span className="tag neutral" key={r.code}>{r.name} {r.fund_count}只 · {pct(r.max_ratio)}</span>
                  ))}
                  {!overlapData.shared_stocks.length && <span className="hint">未发现披露重仓股重合</span>}
                </div>
                <h4 className="fund-subhead">共同暴露行业</h4>
                <div className="fund-bar-list">
                  {overlapData.shared_industries.slice(0, 8).map((r) => (
                    <div className="fund-bar-row" key={r.name}>
                      <div className="fund-bar-label">{r.name}</div>
                      <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(1, r.max_ratio || 0))}%` }} /></div>
                      <div className="fund-bar-value">{pct(r.max_ratio)}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <p className="hint" style={{ marginTop: 12 }}>{overlapData.method.note} 数据源: {overlapData.source}。</p>
          </div>
        )}
      </div>}

      {fundView === 'research' && fund && (
        <>
          <div className="panel fade-in">
            <h3 className="section-title">
              {fund.code} {selectedName} <span className="hint">{fund.trend_state} · 截至 {fund.as_of} · 样本 {fund.sample_count} 条</span>
            </h3>
            <div className="bt-cards quality-cards">
              <MetricCard label="最新单位净值" value={num(fund.latest.unit_nav, 4)} />
              <MetricCard label="近1月" value={pct(fund.metrics.return_1m)} cls={deltaClass(fund.metrics.return_1m)} />
              <MetricCard label="近3月" value={pct(fund.metrics.return_3m)} cls={deltaClass(fund.metrics.return_3m)} />
              <MetricCard label="近1年" value={pct(fund.metrics.return_1y)} cls={deltaClass(fund.metrics.return_1y)} />
              <MetricCard label="最大回撤" value={pct(fund.metrics.max_drawdown)} cls="delta-neg" />
              <MetricCard label="定投适配" value={`${fund.metrics.dca_score} · ${fund.metrics.dca_label}`} />
            </div>
            <FundLineChart data={fund.nav} />
          </div>

          <div className="research-layer-nav" role="tablist" aria-label="基金研究层级">
            <button className={researchLayer === 'decision' ? 'active' : ''} onClick={() => setResearchLayer('decision')}>投资决策</button>
            <button className={researchLayer === 'evidence' ? 'active' : ''} onClick={() => setResearchLayer('evidence')}>数据证据</button>
          </div>

          {researchLayer === 'decision' && <>
          {fund.timing && (
            <div className="panel fade-in">
              <h3 className="section-title">
                买入节奏 <span className="hint">基于真实净值历史计算回撤分位、均线结构和滚动收益，不做模拟预测</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="节奏评分" value={fund.timing.score != null ? `${fund.timing.score} · ${fund.timing.label}` : fund.timing.label} />
                <MetricCard label="当前回撤" value={pct(fund.timing.zones?.current_drawdown)} cls="delta-neg" />
                <MetricCard label="回撤分位" value={pct(fund.timing.zones?.drawdown_percentile)} />
                <MetricCard label="阶段高点" value={fund.timing.zones?.high_nav != null ? `${num(fund.timing.zones.high_nav, 4)} · ${fund.timing.zones.high_date}` : '-'} />
                <MetricCard label="20日均值" value={fund.timing.zones?.ma20 != null ? num(fund.timing.zones.ma20, 4) : '-'} />
                <MetricCard label="60日均值" value={fund.timing.zones?.ma60 != null ? num(fund.timing.zones.ma60, 4) : '-'} />
              </div>
              <div className="fund-timing-grid">
                <div>
                  <h4 className="fund-subhead">当前判断</h4>
                  <p className="fund-timing-summary">{fund.timing.summary}</p>
                  <div className="fund-timing-actions">
                    {(fund.timing.actions || []).map((item) => (
                      <div className="fund-timing-action" key={item.title}>
                        <b>{item.title}</b>
                        <span>{item.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">净值位置</h4>
                  <div className="fund-zone-list">
                    <div><span>最新净值</span><b>{fund.timing.zones?.latest_nav != null ? num(fund.timing.zones.latest_nav, 4) : '-'}</b></div>
                    <div><span>接近高位线</span><b>{fund.timing.zones?.near_high_nav != null ? num(fund.timing.zones.near_high_nav, 4) : '-'}</b></div>
                    <div><span>普通回撤线</span><b>{fund.timing.zones?.normal_pullback_nav != null ? num(fund.timing.zones.normal_pullback_nav, 4) : '-'}</b></div>
                    <div><span>深度回撤线</span><b>{fund.timing.zones?.deep_pullback_nav != null ? num(fund.timing.zones.deep_pullback_nav, 4) : '-'}</b></div>
                  </div>
                  <p className="hint">这些阈值由真实阶段高点折算，用于控制买入节奏，不代表目标价。</p>
                </div>
              </div>
              {(fund.timing.signals || []).length > 0 && (
                <div className="fund-signal-grid">
                  {fund.timing.signals.map((s, idx) => (
                    <div className={`fund-signal ${s.level || 'neutral'}`} key={`${s.name}-${idx}`}>
                      <b>{s.name}</b>
                      <span>{s.text}</span>
                    </div>
                  ))}
                </div>
              )}
              {(fund.timing.rolling_returns || []).length > 0 && (
                <div className="corr-wrap" style={{ marginTop: 14 }}>
                  <table className="compact-table fund-timing-table">
                    <thead>
                      <tr>
                        <th>窗口</th>
                        <th>当前收益</th>
                        <th>历史分位</th>
                        <th>平均收益</th>
                        <th>正收益占比</th>
                        <th>样本</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fund.timing.rolling_returns.map((r) => (
                        <tr key={r.label}>
                          <td>{r.label}</td>
                          <td className={deltaClass(r.current_return)}>{pct(r.current_return)}</td>
                          <td>{pct(r.historical_percentile)}</td>
                          <td className={deltaClass(r.avg_return)}>{pct(r.avg_return)}</td>
                          <td>{pct(r.positive_ratio)}</td>
                          <td>{r.sample_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="hint" style={{ marginTop: 12 }}>{fund.timing.method}</p>
            </div>
          )}

          {fund.playbook && (
            <div className="panel fade-in fund-playbook-panel">
              <h3 className="section-title">
                投资经验手册 <span className="hint">把真实数据转成投前、买入、持有、退出的操作框架，不做收益承诺</span>
              </h3>
              <div className="fund-playbook-hero">
                <div>
                  <span className="tag neutral">{fund.playbook.role?.risk_band}</span>
                  <h4>{fund.playbook.role?.label}</h4>
                  <p>{fund.playbook.role?.reason}</p>
                  <div className="daily-tags">
                    {(fund.playbook.role?.risk_labels || []).map((x) => <span className="tag neutral" key={x}>{x}</span>)}
                    {(fund.playbook.role?.style_labels || []).map((x) => <span className="tag neutral" key={`style-${x}`}>{x}</span>)}
                  </div>
                </div>
                <div className="playbook-review-grid">
                  {(fund.playbook.review_metrics || []).slice(0, 8).map((m) => (
                    <div className="playbook-review" key={m.name}>
                      <span>{m.name}</span>
                      <b className={m.unit === '%' ? deltaClass(m.value) : ''}>{m.value == null ? '-' : `${num(m.value)}${m.unit || ''}`}</b>
                    </div>
                  ))}
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">仓位经验区间</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.position_ranges || []).map((row) => (
                      <div className="playbook-rule" key={row.investor}>
                        <b>{row.investor} · {row.range}</b>
                        <span>{row.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">建仓规则</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.entry_rules || []).map((row) => (
                      <div className="playbook-rule" key={row.level}>
                        <b>{row.level}</b>
                        <span>{row.rule}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">持有纪律</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.hold_rules || []).map((row) => (
                      <div className="playbook-rule" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">退出/降仓规则</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.exit_rules || []).map((row) => (
                      <div className="playbook-rule danger" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <h4 className="fund-subhead">情景预案</h4>
              <div className="corr-wrap">
                <table className="compact-table playbook-table">
                  <thead>
                    <tr>
                      <th>情景</th>
                      <th>观察什么</th>
                      <th>怎么处理</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(fund.playbook.scenario_plan || []).map((row) => (
                      <tr key={row.scenario}>
                        <td>{row.scenario}</td>
                        <td>{row.watch}</td>
                        <td>{row.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">执行步骤</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.execution_steps || []).map((row) => (
                      <div className="playbook-rule" key={row.step}>
                        <b>{row.step}</b>
                        <span>{row.action}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">经验提醒</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.experience_notes || []).map((row) => (
                      <div className="playbook-rule" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">红旗清单</h4>
                  <div className="fund-bond-list">
                    {(fund.playbook.red_flags || []).map((text) => <span className="tag neutral" key={text}>{text}</span>)}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">买前五问</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.checklist || []).map((row) => (
                      <div className="playbook-rule" key={row.item}>
                        <b>{row.item}</b>
                        <span>{row.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <p className="hint" style={{ marginTop: 12 }}>{fund.playbook.disclaimer}</p>
            </div>
          )}
          </>}

          {researchLayer === 'evidence' && <>
          <div className="panel fade-in">
            <h3 className="section-title">
              同类定位 <span className="hint">在同类型基金排行中查看当前基金的位置</span>
            </h3>
            <div className="form-row" style={{ marginBottom: 14 }}>
              <div className="field">
                <label>同类排序</label>
                <select value={peerSort} onChange={(e) => {
                  setPeerSort(e.target.value)
                  loadPeers(fund.code, e.target.value)
                }}>
                  {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </div>
              <button className="ghost" onClick={() => loadPeers(fund.code, peerSort)} disabled={loadingPeers}>
                {loadingPeers ? <><span className="spinner" /> 定位中</> : '刷新同类定位'}
              </button>
            </div>
            {loadingPeers && !peers && <div className="placeholder"><div className="big">⌛</div>正在获取真实同类基金排行</div>}
            {peers?.error && <div className="error">{peers.error}</div>}
            {peers && !peers.error && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="同类类型" value={peers.category_name || '-'} />
                  <MetricCard label="同类排名" value={peers.rank ? `${peers.rank}/${peers.sample_count}` : `未进前${peers.sample_count}`} />
                  <MetricCard label="击败同类" value={peers.beat_ratio != null ? pct(peers.beat_ratio) : '-'} />
                  <MetricCard label="位置判断" value={peers.position_label} />
                </div>
                <div className="fund-peer-grid">
                  <div>
                    <h4 className="fund-subhead">同类前十 <span className="hint">{peers.as_of}</span></h4>
                    <div className="corr-wrap">
                      <table className="compact-table fund-peer-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            <th>代码</th>
                            <th>名称</th>
                            <th>近1年</th>
                            <th>近3月</th>
                          </tr>
                        </thead>
                        <tbody>
                          {peers.leaders.slice(0, 10).map((r) => (
                            <tr key={`leader-${r.code}`} className="clickable" onClick={() => loadFund(r.code, months)}>
                              <td>{r.rank}</td>
                              <td style={{ fontWeight: 800 }}>{r.code}</td>
                              <td>{r.name}</td>
                              <td className={deltaClass(r.return_1y)}>{pct(r.return_1y)}</td>
                              <td className={deltaClass(r.return_3m)}>{pct(r.return_3m)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">当前位置附近</h4>
                    {peers.neighbors?.length ? (
                      <div className="corr-wrap">
                        <table className="compact-table fund-peer-table">
                          <thead>
                            <tr>
                              <th>#</th>
                              <th>代码</th>
                              <th>名称</th>
                              <th>近1年</th>
                              <th>近3月</th>
                            </tr>
                          </thead>
                          <tbody>
                            {peers.neighbors.map((r) => (
                              <tr key={`neighbor-${r.code}`} className={`clickable ${r.code === fund.code ? 'row-active' : ''}`} onClick={() => loadFund(r.code, months)}>
                                <td>{r.rank}</td>
                                <td style={{ fontWeight: 800 }}>{r.code}</td>
                                <td>{r.name}</td>
                                <td className={deltaClass(r.return_1y)}>{pct(r.return_1y)}</td>
                                <td className={deltaClass(r.return_3m)}>{pct(r.return_3m)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="placeholder">当前基金未进入本次同类样本榜单</div>
                    )}
                  </div>
                </div>
                <p className="hint" style={{ marginTop: 12 }}>{peers.method?.ranking} {peers.method?.limit_note}</p>
              </>
            )}
          </div>

          <div className="panel fade-in">
            <h3 className="section-title">
              基金替代品对比 <span className="hint">从同类真实榜单里筛候选，再读取真实净值横向比较收益、波动、回撤和买入节奏</span>
            </h3>
            <div className="form-row" style={{ marginBottom: 14 }}>
              <button onClick={() => loadAlternatives(fund.code, peerSort)} disabled={loadingAlternatives}>
                {loadingAlternatives ? <><span className="spinner" /> 查找中</> : '查找替代基金'}
              </button>
              <button className="ghost" onClick={() => {
                setCompareInput([fund.code, ...(alternatives?.alternatives || []).slice(0, 3).map((r) => r.code)].join(' '))
              }} disabled={!alternatives?.alternatives?.length}>
                加入多基金对比
              </button>
              {alternatives && <span className="hint">同类 {alternatives.selected?.category_name || '-'} · 排序 {alternatives.sort} · 截至 {alternatives.as_of || '-'}</span>}
            </div>
            {loadingAlternatives && !alternatives && <div className="placeholder"><div className="big">⌛</div>正在读取真实同类基金和净值指标</div>}
            {alternatives && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="当前基金" value={`${alternatives.selected.code} ${alternatives.selected.name || ''}`} />
                  <MetricCard label="当前同类排名" value={alternatives.selected.rank ? `${alternatives.selected.rank}/${alternatives.selected.sample_count}` : `未进前${alternatives.selected.sample_count}`} />
                  <MetricCard label="评分最高候选" value={`${alternatives.summary.best_score.code} · ${alternatives.summary.best_score.score}`} />
                  <MetricCard label="低波候选" value={`${alternatives.summary.lower_volatility.code} ${pct(alternatives.summary.lower_volatility.metrics.annual_volatility)}`} />
                  <MetricCard label="一年收益候选" value={`${alternatives.summary.better_1y.code} ${pct(alternatives.summary.better_1y.metrics.return_1y)}`} cls={deltaClass(alternatives.summary.better_1y.metrics.return_1y)} />
                  <MetricCard label="低回撤候选" value={`${alternatives.summary.shallower_drawdown.code} ${pct(alternatives.summary.shallower_drawdown.metrics.max_drawdown)}`} cls="delta-neg" />
                </div>
                <div className="corr-wrap">
                  <table className="compact-table fund-alternative-table">
                    <thead>
                      <tr>
                        <th>候选</th>
                        <th>评分</th>
                        <th>近3月</th>
                        <th>近1年</th>
                        <th>波动</th>
                        <th>最大回撤</th>
                        <th>相对优势</th>
                        <th>风险点</th>
                      </tr>
                    </thead>
                    <tbody>
                      {alternatives.alternatives.map((row) => (
                        <tr key={row.code} className="clickable" onClick={() => loadFund(row.code, months)}>
                          <td>
                            <b>{row.code}</b>
                            <span className="table-sub">{row.name}</span>
                          </td>
                          <td>{row.score} · {row.label}</td>
                          <td className={deltaClass(row.metrics.return_3m)}>{pct(row.metrics.return_3m)}</td>
                          <td className={deltaClass(row.metrics.return_1y)}>{pct(row.metrics.return_1y)}</td>
                          <td>{pct(row.metrics.annual_volatility)}</td>
                          <td className="delta-neg">{pct(row.metrics.max_drawdown)}</td>
                          <td>{row.advantages?.[0] || '-'}</td>
                          <td>{row.cautions?.[0] || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="fund-alt-card-grid">
                  {alternatives.alternatives.slice(0, 4).map((row) => (
                    <div className="fund-alt-card" key={`alt-card-${row.code}`}>
                      <h4>{row.code} {row.name}</h4>
                      <div className="daily-metrics">
                        <span>评分 {row.score}</span>
                        <span>{row.timing_label || '-'}</span>
                        <span>规模 {row.scale_yi != null ? `${num(row.scale_yi)}亿` : '-'}</span>
                      </div>
                      <div className="fund-bond-list">
                        {(row.advantages || []).slice(0, 3).map((text) => <span className="tag up" key={text}>{text}</span>)}
                        {(row.cautions || []).slice(0, 2).map((text) => <span className="tag neutral" key={text}>{text}</span>)}
                      </div>
                    </div>
                  ))}
                </div>
                {alternatives.failed?.length > 0 && (
                  <div className="error" style={{ marginTop: 12 }}>
                    {alternatives.failed.map((x) => `${x.code || x.name}: ${x.error}`).join('；')}
                  </div>
                )}
                <p className="hint" style={{ marginTop: 12 }}>{alternatives.method?.score} {alternatives.method?.note}</p>
              </>
            )}
          </div>

          {factSheet && (
            <div className="panel fade-in">
              <h3 className="section-title">
                基金档案 <span className="hint">{factSheet.source} · {assetLatest.date || factSheet.scale_latest?.date || fund.as_of}</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="股票占比" value={pct(assetLatest.stock_ratio)} />
                <MetricCard label="债券占比" value={pct(assetLatest.bond_ratio)} />
                <MetricCard label="现金占比" value={pct(assetLatest.cash_ratio)} />
                <MetricCard label="净资产" value={assetLatest.net_asset_yi != null ? `${num(assetLatest.net_asset_yi)}亿` : '-'} />
                <MetricCard label="当前费率" value={factSheet.fee?.current_rate != null ? `${num(factSheet.fee.current_rate)}%` : '-'} />
                <MetricCard label="原始费率" value={factSheet.fee?.source_rate != null ? `${num(factSheet.fee.source_rate)}%` : '-'} />
              </div>
              {manager && (
                <div className="fund-manager-card">
                  <div>
                    <div className="hint">现任基金经理</div>
                    <h4>{manager.name} <span className="tag neutral">{manager.label || '任期中性'}</span></h4>
                    <p>{manager.work_time} · 管理规模 {manager.fund_size} · 评分日期 {manager.score_date || '-'}</p>
                  </div>
                  <div className="fund-manager-metrics">
                    <span>评分 <b>{num(manager.score)}</b></span>
                    <span>星级 <b>{manager.star || '-'}</b></span>
                    <span>任期收益 <b className={deltaClass(manager.tenure_return)}>{pct(manager.tenure_return)}</b></span>
                    <span>超同类 <b className={deltaClass(manager.excess_vs_peer)}>{pct(manager.excess_vs_peer)}</b></span>
                    <span>超沪深300 <b className={deltaClass(manager.excess_vs_hs300)}>{pct(manager.excess_vs_hs300)}</b></span>
                  </div>
                </div>
              )}
              {manager?.score_breakdown?.length > 0 && (
                <div className="fund-manager-detail">
                  <div>
                    <h4 className="fund-subhead">能力评分</h4>
                    <div className="fund-manager-score-list">
                      {manager.score_breakdown.map((r) => (
                        <div className="fund-manager-score-row" key={r.label}>
                          <span>{r.label}</span>
                          <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(2, r.score || 0))}%` }} /></div>
                          <b>{num(r.score)}</b>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">任期表现对比</h4>
                    <div className="ind-grid manager-mini-grid">
                      <div className="ind"><div className="k">本基金</div><div className={`v ${deltaClass(manager.tenure_return)}`}>{pct(manager.tenure_return)}</div></div>
                      <div className="ind"><div className="k">同类平均</div><div className={`v ${deltaClass(manager.tenure_peer_avg)}`}>{pct(manager.tenure_peer_avg)}</div></div>
                      <div className="ind"><div className="k">沪深300</div><div className={`v ${deltaClass(manager.tenure_hs300)}`}>{pct(manager.tenure_hs300)}</div></div>
                    </div>
                    <div className="fund-bond-list" style={{ marginTop: 12 }}>
                      {(manager.strengths || []).map((r) => <span className="tag up" key={`s-${r.label}`}>强项 {r.label} {num(r.score)}</span>)}
                      {(manager.weaknesses || []).map((r) => <span className="tag neutral" key={`w-${r.label}`}>短板 {r.label} {num(r.score)}</span>)}
                    </div>
                  </div>
                </div>
              )}
              {(fundEvaluation?.scores?.length > 0 || benchmarkComparison?.series?.length > 0) && (
                <div className="fund-evaluation-panel">
                  <div className="fund-evaluation-head">
                    <div>
                      <h4>基金能力画像</h4>
                      <p className="hint">{fundEvaluation?.label || '暂无评分'} · 同类百分位 {similarPercentile?.latest != null ? num(similarPercentile.latest) : '-'}</p>
                    </div>
                    <div className="fund-flow-summary">
                      <span>综合评分 <b>{num(fundEvaluation?.avg_score)}</b></span>
                      <span>20日均值 <b>{num(similarPercentile?.avg_20)}</b></span>
                      <span>20日变化 <b className={deltaClass(similarPercentile?.change_20)}>{num(similarPercentile?.change_20)}</b></span>
                    </div>
                  </div>
                  <div className="fund-evaluation-grid">
                    <div>
                      <h4 className="fund-subhead">基金五项评分</h4>
                      <div className="fund-manager-score-list">
                        {(fundEvaluation?.scores || []).map((r) => (
                          <div className="fund-manager-score-row" key={`fund-${r.label}`}>
                            <span>{r.label}</span>
                            <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(2, r.score || 0))}%` }} /></div>
                            <b>{num(r.score)}</b>
                          </div>
                        ))}
                      </div>
                      <div className="fund-bond-list" style={{ marginTop: 12 }}>
                        {(fundEvaluation?.strengths || []).map((r) => <span className="tag up" key={`fs-${r.label}`}>强项 {r.label} {num(r.score)}</span>)}
                        {(fundEvaluation?.weaknesses || []).map((r) => <span className="tag neutral" key={`fw-${r.label}`}>短板 {r.label} {num(r.score)}</span>)}
                      </div>
                    </div>
                    <div>
                      <h4 className="fund-subhead">累计收益对比 <span className="hint">{benchmarkComparison?.as_of || ''}</span></h4>
                      {benchmarkComparison?.series?.length > 0 ? (
                        <div className="corr-wrap">
                          <table className="compact-table fund-benchmark-table">
                            <thead>
                              <tr>
                                <th>序列</th>
                                <th>区间</th>
                                <th>累计收益</th>
                                <th>本基金超额</th>
                              </tr>
                            </thead>
                            <tbody>
                              {benchmarkComparison.series.map((r, idx) => (
                                <tr key={`${r.name}-${idx}`}>
                                  <td>{r.name}</td>
                                  <td>{r.start_date} ~ {r.end_date}</td>
                                  <td className={deltaClass(r.latest_return)}>{pct(r.latest_return)}</td>
                                  <td className={deltaClass(r.fund_excess)}>{idx === 0 ? '-' : pct(r.fund_excess)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="placeholder">暂无累计收益对比数据</div>
                      )}
                      <p className="hint">{similarPercentile?.label || ''}。{similarPercentile?.method || ''}</p>
                    </div>
                  </div>
                </div>
              )}
              {factSheet.flow_rows?.length > 0 && (
                <div className="fund-flow-panel">
                  <div className="fund-flow-head">
                    <div>
                      <h4>规模与申赎压力</h4>
                      <p className="hint">{flowSummary.latest_date} · {flowSummary.pressure || '申赎状态待观察'}</p>
                    </div>
                    <div className="fund-flow-summary">
                      <span>最新净申赎 <b className={deltaClass(flowSummary.latest_net_subscribe_yi)}>{flowSummary.latest_net_subscribe_yi != null ? `${num(flowSummary.latest_net_subscribe_yi)}亿` : '-'}</b></span>
                      <span>近几期合计 <b className={deltaClass(flowSummary.total_net_subscribe_yi)}>{flowSummary.total_net_subscribe_yi != null ? `${num(flowSummary.total_net_subscribe_yi)}亿` : '-'}</b></span>
                      <span>总份额 <b>{flowSummary.latest_total_share_yi != null ? `${num(flowSummary.latest_total_share_yi)}亿份` : '-'}</b></span>
                    </div>
                  </div>
                  <div className="fund-flow-grid">
                    <div>
                      <h4 className="fund-subhead">申购/赎回</h4>
                      <div className="fund-flow-bars">
                        {factSheet.flow_rows.map((r) => {
                          const maxAbs = Math.max(1, ...factSheet.flow_rows.map((x) => Math.abs(x.net_subscribe_yi || 0)))
                          const width = Math.min(100, Math.abs(r.net_subscribe_yi || 0) / maxAbs * 100)
                          return (
                            <div className="fund-flow-row" key={r.date}>
                              <span>{r.date}</span>
                              <div className={`fund-flow-track ${r.net_subscribe_yi >= 0 ? 'in' : 'out'}`}>
                                <i style={{ width: `${width}%` }} />
                              </div>
                              <b className={deltaClass(r.net_subscribe_yi)}>{r.net_subscribe_yi != null ? `${num(r.net_subscribe_yi)}亿` : '-'}</b>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    <div>
                      <h4 className="fund-subhead">规模变化</h4>
                      <div className="fund-bar-list">
                        {(factSheet.scale_rows || []).map((r) => {
                          const maxScale = Math.max(1, ...(factSheet.scale_rows || []).map((x) => x.scale_yi || 0))
                          return (
                            <div className="fund-bar-row" key={r.date}>
                              <div className="fund-bar-label">{r.date}</div>
                              <div className="fund-bar-track"><i style={{ width: `${Math.min(100, (r.scale_yi || 0) / maxScale * 100)}%` }} /></div>
                              <div className="fund-bar-value">{r.scale_yi != null ? `${num(r.scale_yi)}亿` : '-'}</div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              <p className="hint" style={{ marginTop: 12 }}>基金档案来自东方财富基金详情页，资产配置和规模以基金披露日期为准。</p>
            </div>
          )}

          <div className="panel fade-in">
            <h3 className="section-title">
              分红送配 <span className="hint">现金分配记录、拆分折算和累计分红画像</span>
            </h3>
            {loadingDividends && !dividends && <div className="placeholder"><div className="big">⌛</div>正在读取真实分红记录</div>}
            {dividends?.error && <div className="error">{dividends.error}</div>}
            {dividends && !dividends.error && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="分红特征" value={dividends.summary.label} />
                  <MetricCard label="分红次数" value={dividends.summary.dividend_count} />
                  <MetricCard label="累计每份分红" value={dividends.summary.total_cash_per_share != null ? `${num(dividends.summary.total_cash_per_share, 4)}元` : '-'} />
                  <MetricCard label="拆分次数" value={dividends.summary.split_count} />
                </div>
                <p className="hint" style={{ marginTop: -4 }}>{dividends.summary.note}</p>
                {dividends.dividends.length > 0 ? (
                  <div className="corr-wrap">
                    <table className="compact-table fund-dividend-table">
                      <thead>
                        <tr>
                          <th>年份</th>
                          <th>权益登记日</th>
                          <th>除息日</th>
                          <th>每份分红</th>
                          <th>发放日</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dividends.dividends.slice(0, 12).map((r, idx) => (
                          <tr key={`${r.ex_dividend_date}-${idx}`}>
                            <td>{r.year}</td>
                            <td>{r.record_date}</td>
                            <td>{r.ex_dividend_date}</td>
                            <td>{r.cash_per_share != null ? `${num(r.cash_per_share, 4)}元` : r.cash_text}</td>
                            <td>{r.payment_date}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="placeholder">该基金分红页面暂无分红信息</div>
                )}
                {dividends.splits.length > 0 && (
                  <div className="fund-bond-list" style={{ marginTop: 12 }}>
                    {dividends.splits.slice(0, 6).map((r, idx) => (
                      <span className="tag neutral" key={`${r.date}-${idx}`}>{r.date} {r.type} {r.ratio}</span>
                    ))}
                  </div>
                )}
                <p className="hint" style={{ marginTop: 12 }}>{dividends.method.note} 数据源: {dividends.source}。</p>
              </>
            )}
          </div>

          <div className="panel fade-in">
            <h3 className="section-title">风险与持有体验</h3>
            <div className="ind-grid">
              <div className="ind"><div className="k">当前回撤</div><div className="v delta-neg">{pct(fund.metrics.current_drawdown)}</div></div>
              <div className="ind"><div className="k">年化波动</div><div className="v">{pct(fund.metrics.annual_volatility)}</div></div>
              <div className="ind"><div className="k">日胜率</div><div className="v">{pct(fund.metrics.win_rate)}</div></div>
              <div className="ind"><div className="k">月度胜率</div><div className="v">{pct(fund.metrics.positive_month_ratio)}</div></div>
              <div className="ind"><div className="k">最差单日</div><div className="v delta-neg">{pct(fund.metrics.worst_day)}</div></div>
            </div>
          </div>

          {fund.drawdown_recovery && (
            <div className="panel fade-in">
              <h3 className="section-title">
                回撤修复画像 <span className="hint">从真实历史净值统计创新高、回撤深度和修复耗时</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="修复特征" value={fund.drawdown_recovery.label} />
                <MetricCard label="最近新高" value={fund.drawdown_recovery.latest_high_date || '-'} />
                <MetricCard label="离新高天数" value={fund.drawdown_recovery.days_since_high != null ? `${fund.drawdown_recovery.days_since_high}天` : '-'} />
                <MetricCard label="历史回撤段" value={fund.drawdown_recovery.episode_count} />
                <MetricCard label="已修复比例" value={pct(fund.drawdown_recovery.recovery_rate)} />
                <MetricCard label="平均修复" value={fund.drawdown_recovery.avg_recovery_days != null ? `${num(fund.drawdown_recovery.avg_recovery_days, 0)}天` : '-'} />
              </div>
              <div className="fund-recovery-grid">
                <div>
                  <h4 className="fund-subhead">回撤分布</h4>
                  <div className="fund-bond-list">
                    <span className="tag neutral">超过5%: {fund.drawdown_recovery.deep_drawdown_count_5}次</span>
                    <span className="tag neutral">超过10%: {fund.drawdown_recovery.deep_drawdown_count_10}次</span>
                    <span className="tag neutral">超过20%: {fund.drawdown_recovery.deep_drawdown_count_20}次</span>
                    <span className="tag neutral">最长修复: {fund.drawdown_recovery.max_recovery_days != null ? `${fund.drawdown_recovery.max_recovery_days}天` : '-'}</span>
                  </div>
                  <p className="hint">当前仍在回撤时，开放回撤天数为 {fund.drawdown_recovery.open_drawdown_days != null ? `${fund.drawdown_recovery.open_drawdown_days}天` : '-'}，当前开放回撤深度 {pct(fund.drawdown_recovery.open_drawdown_depth)}。</p>
                </div>
                <div>
                  <h4 className="fund-subhead">最深回撤区间</h4>
                  {fund.drawdown_recovery.episodes?.length ? (
                    <div className="corr-wrap">
                      <table className="compact-table fund-recovery-table">
                        <thead>
                          <tr>
                            <th>高点</th>
                            <th>低点</th>
                            <th>深度</th>
                            <th>修复日</th>
                            <th>修复耗时</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fund.drawdown_recovery.episodes.map((r, idx) => (
                            <tr key={`${r.peak_date}-${r.trough_date}-${idx}`}>
                              <td>{r.peak_date}</td>
                              <td>{r.trough_date}</td>
                              <td className="delta-neg">{pct(r.depth)}</td>
                              <td>{r.recovered ? r.recovery_date : '未修复'}</td>
                              <td>{r.recovery_days != null ? `${r.recovery_days}天` : '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="placeholder">当前净值窗口内暂未形成有效回撤区间</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {fund.calendar_returns && (
            <div className="panel fade-in">
              <h3 className="section-title">
                收益日历 <span className="hint">自然年、最近月份和月份胜率，均由真实单位净值计算</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="年度胜率" value={pct(fund.calendar_returns.summary?.positive_year_ratio)} />
                <MetricCard label="上涨年份" value={fund.calendar_returns.summary?.positive_years ?? '-'} />
                <MetricCard label="下跌年份" value={fund.calendar_returns.summary?.negative_years ?? '-'} />
                <MetricCard label="最好年份" value={fund.calendar_returns.summary?.best_year ? `${fund.calendar_returns.summary.best_year.year} ${pct(fund.calendar_returns.summary.best_year.return)}` : '-'} cls={deltaClass(fund.calendar_returns.summary?.best_year?.return)} />
                <MetricCard label="最差年份" value={fund.calendar_returns.summary?.worst_year ? `${fund.calendar_returns.summary.worst_year.year} ${pct(fund.calendar_returns.summary.worst_year.return)}` : '-'} cls="delta-neg" />
                <MetricCard label="最好月份" value={fund.calendar_returns.summary?.best_month ? `${fund.calendar_returns.summary.best_month.month} ${pct(fund.calendar_returns.summary.best_month.return)}` : '-'} cls={deltaClass(fund.calendar_returns.summary?.best_month?.return)} />
              </div>
              <div className="fund-calendar-grid">
                <div>
                  <h4 className="fund-subhead">年度收益</h4>
                  <div className="corr-wrap">
                    <table className="compact-table fund-calendar-table">
                      <thead>
                        <tr>
                          <th>年份</th>
                          <th>起始日</th>
                          <th>结束日</th>
                          <th>收益</th>
                          <th>样本</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fund.calendar_returns.years.map((r) => (
                          <tr key={r.year}>
                            <td>{r.year}</td>
                            <td>{r.start_date}</td>
                            <td>{r.end_date}</td>
                            <td className={deltaClass(r.return)}>{pct(r.return)}</td>
                            <td>{r.sample_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">月份统计</h4>
                  <div className="fund-bar-list">
                    {fund.calendar_returns.month_stats.map((r) => (
                      <div className="fund-bar-row" key={r.month}>
                        <div className="fund-bar-label">{r.month}月</div>
                        <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(4, Math.abs(r.avg_return || 0) * 4))}%` }} /></div>
                        <div className={`fund-bar-value ${deltaClass(r.avg_return)}`}>{pct(r.avg_return)} · 胜率 {pct(r.win_rate)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <h4 className="fund-subhead" style={{ marginTop: 16 }}>最近月份</h4>
              <div className="corr-wrap">
                <table className="compact-table fund-calendar-table">
                  <thead>
                    <tr>
                      <th>月份</th>
                      <th>起始日</th>
                      <th>结束日</th>
                      <th>收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fund.calendar_returns.recent_months.map((r) => (
                      <tr key={r.month}>
                        <td>{r.month}</td>
                        <td>{r.start_date}</td>
                        <td>{r.end_date}</td>
                        <td className={deltaClass(r.return)}>{pct(r.return)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="panel fade-in">
            <h3 className="section-title">
              持仓画像 <span className="hint">
                {loadingPortfolio ? '正在读取基金定期报告披露持仓' : portfolio ? `${portfolio.year} · 股票 ${portfolio.summary.stock_count} 只 · 行业 ${portfolio.summary.industry_count} 个` : '基金定期报告披露数据'}
              </span>
            </h3>
            {loadingPortfolio && <div className="placeholder"><div className="big">⌛</div>正在获取真实持仓数据</div>}
            {portfolioError && <div className="error">{portfolioError}</div>}
            {portfolio && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="前3大重仓" value={pct(portfolio.summary.top3_stock_ratio)} />
                  <MetricCard label="前10大重仓" value={pct(portfolio.summary.top10_stock_ratio)} />
                  <MetricCard label="集中度" value={portfolio.summary.concentration} />
                  <MetricCard label="风格提示" value={portfolio.summary.style_note} />
                </div>
                <div className="fund-holding-grid">
                  <div>
                    <h4 className="fund-subhead">重仓股票 <span className="hint">{portfolio.stock_period}</span></h4>
                    <div className="corr-wrap">
                      <table className="compact-table fund-holding-table">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>名称</th>
                            <th>占净值</th>
                            <th>持仓市值(万)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {portfolio.stocks.slice(0, 10).map((r) => (
                            <tr key={`${r.code}-${r.name}`}>
                              <td style={{ fontWeight: 800 }}>{r.code}</td>
                              <td>{r.name}</td>
                              <td>{pct(r.nav_ratio)}</td>
                              <td>{num(r.market_value_wan)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">行业配置 <span className="hint">{portfolio.industry_period}</span></h4>
                    <div className="fund-bar-list">
                      {portfolio.industries.slice(0, 8).map((r) => (
                        <div className="fund-bar-row" key={r.name}>
                          <div className="fund-bar-label">{r.name}</div>
                          <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(1, r.nav_ratio || 0))}%` }} /></div>
                          <div className="fund-bar-value">{pct(r.nav_ratio)}</div>
                        </div>
                      ))}
                    </div>
                    {portfolio.bonds.length > 0 && (
                      <>
                        <h4 className="fund-subhead">债券持仓 <span className="hint">{portfolio.bond_period}</span></h4>
                        <div className="fund-bond-list">
                          {portfolio.bonds.slice(0, 5).map((r) => (
                            <span className="tag neutral" key={`${r.code}-${r.name}`}>{r.name} {pct(r.nav_ratio)}</span>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                </div>
                <p className="hint" style={{ marginTop: 12 }}>{portfolio.method.note} 数据源: {portfolio.source}。</p>
              </>
            )}
          </div>
          </>}

          <div className="panel fade-in">
            <h3 className="section-title">投资分析</h3>
            <div className="fund-insight-grid">
              {fund.insights.map((item) => (
                <div className="fund-insight" key={item.title}>
                  <h4>{item.title}</h4>
                  <p>{item.text}</p>
                </div>
              ))}
            </div>
            <p className="hint" style={{ marginTop: 12 }}>
              {fund.method.note} 数据源: {fund.source}。申购状态: {fund.latest.subscribe_status || '-'}；赎回状态: {fund.latest.redeem_status || '-'}。
            </p>
          </div>
        </>
      )}
    </>
  )
}
