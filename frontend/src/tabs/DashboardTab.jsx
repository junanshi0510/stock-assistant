import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { fetchDecisionCenter } from '../api/portfolio'
import DecisionCenter from '../features/decision/DecisionCenter'
import DecisionWorkflow from '../features/decision/DecisionWorkflow'

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
  return <div className="overview-metric"><span>{label}</span><b className={tone}>{value}</b></div>
}

export default function DashboardTab({ goPortfolio, goFunds, goMarket, goAgent }) {
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
  const workflow = decision?.workflow
  const summary = portfolio?.summary || {}
  const holdingCount = summary.holding_count ?? 0
  const next = workflow?.next_action
  const unavailableCount = decision?.summary?.unavailable_count ?? 0

  function navigate(target) {
    if (target === 'profile') goPortfolio('policy')
    else if (target === 'ledger') goPortfolio('ledger')
    else if (target === 'portfolio') goPortfolio('holdings')
    else if (target === 'funds') goFunds()
    else if (target === 'market') goMarket()
    else if (target === 'agent') goAgent()
  }

  function taskUpdated(result) {
    const task = result?.task
    if (!task) return
    setDecision((current) => current ? {
      ...current,
      task_inbox: {
        ...(current.task_inbox || {}),
        status: 'available',
        summary: result.summary || current.task_inbox?.summary || {},
      },
      actions: (current.actions || []).map((action) => (
        action.id === task.action_key ? { ...action, task } : action
      )),
    } : current)
  }

  return (
    <>
      <section className="command-hero" aria-label="今日投资决策">
        <div>
          <span className="eyebrow">今日决策</span>
          <h2>{next ? `先完成：${next.title}` : workflow?.decision_ready ? '决策闭环已就绪' : '正在核对决策基础'}</h2>
          <p>{next?.description || '先完成组合事实、风险政策和持有纪律，再研究市场机会。'}</p>
        </div>
        <div className="command-hero-status">
          <span>{unavailableCount > 0 ? `${unavailableCount} 个真实来源暂不可用` : '真实来源已完成汇总'}</span>
          <button type="button" onClick={refresh} disabled={loading}><RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />{loading ? '刷新中' : '刷新全部证据'}</button>
        </div>
      </section>

      <DecisionWorkflow workflow={workflow} onNavigate={navigate} />

      <DecisionCenter
        data={decision}
        loading={loading}
        error={error}
        onRefresh={refresh}
        onNavigate={navigate}
        onTaskUpdated={taskUpdated}
      />

      <div className="overview-grid overview-snapshots">
        <section className="overview-band">
          <div className="overview-band-head">
            <div><span className="eyebrow">资产快照</span><h3>{holdingCount > 0 ? `${holdingCount} 项真实持仓` : '尚未导入真实持仓'}</h3></div>
            <button className="ghost" onClick={() => goPortfolio('holdings')}>{holdingCount > 0 ? '管理持仓' : '导入持仓'}</button>
          </div>
          {portfolio?.status === 'available' && holdingCount > 0 ? (
            <div className="overview-metrics compact">
              <SummaryMetric label="确认总额" value={money(summary.total_amount)} />
              <SummaryMetric label="累计收益" value={money(summary.total_profit)} tone={deltaClass(summary.total_profit)} />
              <SummaryMetric label="加权收益率" value={pct(summary.weighted_profit_rate)} tone={deltaClass(summary.weighted_profit_rate)} />
              <SummaryMetric label="第一大持仓" value={pct(summary.top1_ratio)} />
            </div>
          ) : <div className="overview-empty">确认金额后才计算配置、收益贡献和集中度。</div>}
        </section>

        <section className="overview-band">
          <div className="overview-band-head">
            <div><span className="eyebrow">研究环境</span><h3>{market?.status === 'available' ? (market.summary?.headline || '真实市场日报') : '市场证据尚未就绪'}</h3></div>
            <button className="ghost" onClick={goMarket}>进入研究中心</button>
          </div>
          {market?.status === 'available' ? (
            <div className="overview-metrics compact">
              <SummaryMetric label="市场温度" value={market.summary?.temperature || '-'} />
              <SummaryMetric label="研究线索" value={`${market.summary?.opportunity_count ?? 0} 条`} />
              <SummaryMetric label="风险提示" value={`${market.summary?.risk_count ?? 0} 条`} />
              <SummaryMetric label="热门行业" value={market.summary?.top_industry?.name || '-'} />
            </div>
          ) : <div className="overview-empty">真实市场来源未返回时，研究层结论保持暂停。</div>}
          <p className="hint">数据截至 {market?.as_of || '-'}，线索只进入研究队列。</p>
        </section>
      </div>
    </>
  )
}
