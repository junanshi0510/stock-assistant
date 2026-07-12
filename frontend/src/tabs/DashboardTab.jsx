import { useEffect, useMemo, useState } from 'react'
import { fetchDecisionCenter } from '../api/portfolio'
import DecisionCenter from '../features/decision/DecisionCenter'

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `¥${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
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
  const [decision, setDecision] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function refresh() {
    setLoading(true)
    setError('')
    try {
      setDecision(await fetchDecisionCenter())
    } catch (requestError) {
      setError(requestError.message || '真实投资决策数据获取失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const portfolio = decision?.portfolio
  const market = decision?.market
  const summary = portfolio?.summary || {}
  const holdingCount = summary.holding_count ?? 0
  const topIndustry = market?.summary?.top_industry
  const topFundCategory = market?.summary?.top_fund_category
  const topCandidate = market?.fund_candidates?.[0]
  const unavailableCount = decision?.summary?.unavailable_count ?? 0
  const sourceState = useMemo(() => {
    if (!decision) return '正在获取真实数据'
    if (unavailableCount > 0) return `${unavailableCount} 个来源暂不可用`
    return '真实数据已完成汇总'
  }, [decision, unavailableCount])

  function navigateDecision(target) {
    if (target === 'portfolio') goPortfolio()
    if (target === 'ledger') goPortfolio('ledger')
    if (target === 'funds') goFunds()
    if (target === 'market') goMarket()
  }

  return (
    <>
      <section className="overview-hero" aria-label="投资决策总览">
        <div>
          <span className="eyebrow">真实持仓与真实市场数据</span>
          <h2>先处理组合，再研究机会</h2>
          <p>把持仓风险、基金回撤、重复暴露和市场日报放进同一条行动清单。数据源不可用时会明确标注，不用模拟结果补齐。</p>
        </div>
        <div className="overview-actions">
          <div className="overview-source-state">{sourceState}</div>
          <button onClick={refresh} disabled={loading}>
            {loading ? <><span className="spinner" /> 刷新中</> : '刷新决策'}
          </button>
        </div>
      </section>

      <DecisionCenter
        data={decision}
        loading={loading}
        error={error}
        onRefresh={refresh}
        onNavigate={navigateDecision}
      />

      <section className="overview-band">
        <div className="overview-band-head">
          <div>
            <span className="eyebrow">我的组合</span>
            <h3>{holdingCount > 0 ? `${holdingCount} 项真实持仓正在跟踪` : '尚未导入真实持仓'}</h3>
          </div>
          <button className="ghost" onClick={goPortfolio}>{holdingCount > 0 ? '查看组合' : '导入持仓'}</button>
        </div>
        {portfolio?.status === 'available' && holdingCount > 0 ? (
          <div className="overview-metrics">
            <SummaryMetric label="持仓金额" value={money(summary.total_amount)} />
            <SummaryMetric label="累计收益" value={money(summary.total_profit)} tone={deltaClass(summary.total_profit)} />
            <SummaryMetric label="昨日收益" value={money(summary.total_yesterday_profit)} tone={deltaClass(summary.total_yesterday_profit)} />
            <SummaryMetric label="加权收益率" value={pct(summary.weighted_profit_rate)} tone={deltaClass(summary.weighted_profit_rate)} />
            <SummaryMetric label="第一大持仓" value={pct(summary.top1_ratio)} />
            <SummaryMetric label="集中度" value={summary.concentration_level || '-'} />
          </div>
        ) : (
          <div className="overview-empty">导入并确认每项持仓金额后，才能计算真实配置、收益贡献和集中度风险。</div>
        )}
      </section>

      <div className="overview-grid">
        <section className="overview-band">
          <div className="overview-band-head">
            <div>
              <span className="eyebrow">组合复盘依据</span>
              <h3>只使用已确认的持仓数据</h3>
            </div>
            <button className="ghost" onClick={goPortfolio}>组合体检</button>
          </div>
          {portfolio?.notes?.length > 0 ? (
            <div className="priority-list">
              {portfolio.notes.slice(0, 4).map((text, index) => (
                <div className="priority-item" key={`${text}-${index}`}>
                  <span>组合</span>
                  <div><b>需要复盘</b><p>{text}</p></div>
                </div>
              ))}
            </div>
          ) : (
            <div className="overview-empty">真实持仓数据尚未形成额外组合复盘提示。</div>
          )}
        </section>

        <section className="overview-band">
          <div className="overview-band-head">
            <div>
              <span className="eyebrow">市场环境</span>
              <h3>{market?.status === 'available' ? (market.summary?.headline || '真实市场日报') : '等待真实市场日报'}</h3>
            </div>
            <button className="ghost" onClick={goMarket}>查看市场</button>
          </div>
          {market?.status === 'available' ? (
            <>
              <div className="market-pulse-grid">
                <SummaryMetric label="市场温度" value={market.summary?.temperature || '-'} />
                <SummaryMetric label="机会线索" value={`${market.summary?.opportunity_count ?? 0} 条`} />
                <SummaryMetric label="风险提示" value={`${market.summary?.risk_count ?? 0} 条`} />
                <SummaryMetric label="不可用来源" value={`${market.failed?.length ?? 0} 个`} />
              </div>
              <div className="market-pulse-list">
                {topIndustry?.name && <button className="market-pulse" onClick={goMarket}><span>行业热度</span><b>{topIndustry.name}</b><small className={deltaClass(topIndustry.change_pct)}>区间 {pct(topIndustry.change_pct)}</small></button>}
                {topFundCategory?.name && <button className="market-pulse" onClick={goFunds}><span>基金分类</span><b>{topFundCategory.name}</b><small className={deltaClass(topFundCategory.return_3m)}>近 3 月 {pct(topFundCategory.return_3m)}</small></button>}
                {topCandidate && <button className="market-pulse" onClick={goFunds}><span>基金线索</span><b>{topCandidate.code} {topCandidate.name}</b><small className={deltaClass(topCandidate.return_3m)}>近 3 月 {pct(topCandidate.return_3m)}</small></button>}
              </div>
              <p className="hint">截至 {market.as_of || '-'}。市场线索仅用于研究排序，不构成买入建议。</p>
            </>
          ) : (
            <div className="overview-empty">真实市场日报尚未返回；市场层结论已暂停。</div>
          )}
        </section>
      </div>
    </>
  )
}
