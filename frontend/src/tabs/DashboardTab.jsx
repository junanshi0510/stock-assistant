import { useEffect, useMemo, useState } from 'react'
import { fetchMarketDaily } from '../api/market'
import { fetchHoldings, fetchHoldingsInsights } from '../api/portfolio'

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}元`
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`
}

function deltaClass(value) {
  if (value > 0) return 'delta-pos'
  if (value < 0) return 'delta-neg'
  return 'delta-zero'
}

function SummaryMetric({ label, value, tone = '' }) {
  return (
    <div className="overview-metric">
      <span>{label}</span>
      <b className={tone}>{value}</b>
    </div>
  )
}

export default function DashboardTab({ goPortfolio, goFunds, goMarket }) {
  const [risk, setRisk] = useState('balanced')
  const [holdings, setHoldings] = useState(null)
  const [insights, setInsights] = useState(null)
  const [daily, setDaily] = useState(null)
  const [loading, setLoading] = useState(false)
  const [errors, setErrors] = useState({})

  async function refresh(nextRisk = risk) {
    setLoading(true)
    setErrors({})
    const loadBlock = async (key, request, setData) => {
      const labels = { holdings: '持仓', insights: '组合体检', daily: '市场日报' }
      try {
        setData(await request)
      } catch (error) {
        setErrors((current) => ({
          ...current,
          [key]: error?.message || `真实${labels[key] || '数据'}获取失败`,
        }))
      }
    }
    await Promise.all([
      loadBlock('holdings', fetchHoldings(), setHoldings),
      loadBlock('insights', fetchHoldingsInsights(6), setInsights),
      loadBlock('daily', fetchMarketDaily(nextRisk, 4), setDaily),
    ])
    setLoading(false)
  }

  useEffect(() => { refresh('balanced') }, [])

  const summary = insights?.summary || holdings?.summary || {}
  const holdingCount = summary.holding_count ?? summary.count ?? 0
  const priorityItems = useMemo(() => {
    const portfolioNotes = (insights?.notes || []).map((text) => ({ kind: '组合', title: '组合需要复盘', text }))
    const marketRisks = (daily?.risks || []).map((item) => ({ kind: '市场', title: item.title, text: item.text }))
    return [...portfolioNotes, ...marketRisks].slice(0, 5)
  }, [daily, insights])
  const topIndustry = daily?.summary?.top_industry
  const topFundCategory = daily?.summary?.top_fund_category
  const topCandidate = daily?.fund_candidates?.[0]

  return (
    <>
      <section className="overview-hero" aria-label="投资总览">
        <div>
          <span className="eyebrow">真实持仓与真实市场数据</span>
          <h2>先看组合，再看机会</h2>
          <p>只聚合已保存的持仓、真实基金净值与市场数据；源不可用时直接标记，不以模拟数据补齐。</p>
        </div>
        <div className="overview-actions">
          <label className="compact-field">
            <span>风险偏好</span>
            <select value={risk} onChange={(event) => {
              const nextRisk = event.target.value
              setRisk(nextRisk)
              refresh(nextRisk)
            }}>
              <option value="stable">稳健</option>
              <option value="balanced">均衡</option>
              <option value="aggressive">进取</option>
            </select>
          </label>
          <button onClick={() => refresh()} disabled={loading}>
            {loading ? <><span className="spinner" /> 刷新中</> : '刷新总览'}
          </button>
        </div>
      </section>

      <section className="overview-band">
        <div className="overview-band-head">
          <div>
            <span className="eyebrow">我的组合</span>
            <h3>{holdingCount > 0 ? `${holdingCount} 项真实持仓正在跟踪` : '尚未导入真实持仓'}</h3>
          </div>
          <button className="ghost" onClick={goPortfolio}>{holdingCount > 0 ? '查看组合' : '导入持仓'}</button>
        </div>
        {holdingCount > 0 && (holdings || insights) ? (
          <div className="overview-metrics">
            <SummaryMetric label="持仓金额" value={money(summary.total_amount)} />
            <SummaryMetric label="累计收益" value={money(summary.total_profit)} tone={deltaClass(summary.total_profit)} />
            <SummaryMetric label="昨日收益" value={money(summary.total_yesterday_profit)} tone={deltaClass(summary.total_yesterday_profit)} />
            <SummaryMetric label="加权收益率" value={pct(summary.weighted_profit_rate)} tone={deltaClass(summary.weighted_profit_rate)} />
            <SummaryMetric label="第一大持仓" value={pct(summary.top1_ratio ?? holdings?.summary?.top_concentration)} />
            <SummaryMetric label="集中度" value={summary.concentration_level || '-'} />
          </div>
        ) : (
          <div className="overview-empty">暂无持仓汇总数据。导入并确认持仓金额后，才能计算真实配置与收益贡献。</div>
        )}
        {errors.holdings && <div className="error">持仓数据：{errors.holdings}</div>}
        {errors.insights && <div className="error">组合体检：{errors.insights}</div>}
      </section>

      <div className="overview-grid">
        <section className="overview-band">
          <div className="overview-band-head">
            <div>
              <span className="eyebrow">优先处理</span>
              <h3>需要你确认的事项</h3>
            </div>
            <button className="ghost" onClick={goPortfolio}>组合体检</button>
          </div>
          {priorityItems.length > 0 ? (
            <div className="priority-list">
              {priorityItems.map((item, index) => (
                <div className="priority-item" key={`${item.kind}-${item.title}-${index}`}>
                  <span>{item.kind}</span>
                  <div>
                    <b>{item.title}</b>
                    <p>{item.text}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="overview-empty">真实数据暂未形成需要优先处理的事项。</div>
          )}
        </section>

        <section className="overview-band">
          <div className="overview-band-head">
            <div>
              <span className="eyebrow">市场环境</span>
              <h3>{daily?.summary?.headline || '等待真实市场日报'}</h3>
            </div>
            <button className="ghost" onClick={goMarket}>查看市场</button>
          </div>
          {daily ? (
            <>
              <div className="market-pulse-grid">
                <SummaryMetric label="市场温度" value={daily.summary?.temperature || '-'} />
                <SummaryMetric label="机会线索" value={`${daily.summary?.opportunity_count ?? 0} 条`} />
                <SummaryMetric label="风险提示" value={`${daily.summary?.risk_count ?? 0} 条`} />
                <SummaryMetric label="失败源" value={`${daily.summary?.failed_count ?? 0} 个`} />
              </div>
              <div className="market-pulse-list">
                {topIndustry && <button className="market-pulse" onClick={goMarket}><span>行业热度</span><b>{topIndustry.name}</b><small className={deltaClass(topIndustry.change_pct)}>均涨 {pct(topIndustry.change_pct)}</small></button>}
                {topFundCategory && <button className="market-pulse" onClick={goFunds}><span>基金分类</span><b>{topFundCategory.name}</b><small className={deltaClass(topFundCategory.return_3m)}>近3月 {pct(topFundCategory.return_3m)}</small></button>}
                {topCandidate && <button className="market-pulse" onClick={goFunds}><span>基金候选</span><b>{topCandidate.code} {topCandidate.name}</b><small className={deltaClass(topCandidate.return_3m)}>近3月 {pct(topCandidate.return_3m)}</small></button>}
              </div>
              {daily.failed?.length > 0 && <p className="hint">不可用源：{daily.failed.map((item) => item.source).join('、')}</p>}
              <p className="hint">截至 {daily.as_of} · {daily.method?.aggregation}</p>
            </>
          ) : (
            <div className="overview-empty">真实市场日报尚未返回。</div>
          )}
          {errors.daily && <div className="error">市场日报：{errors.daily}</div>}
        </section>
      </div>
    </>
  )
}
