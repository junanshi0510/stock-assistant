import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { fetchDecisionCenter } from '../api/portfolio'
import CapitalDecisionCommand from '../features/decision/CapitalDecisionCommand'
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

const SOURCE_STATUS = {
  succeeded: '完整完成',
  completed: '完整完成',
  complete: '完整完成',
  partial: '部分完成',
  running: '运行中',
  failed: '运行失败',
  abstained: '主动弃权',
  empty: '尚未运行',
  unavailable: '读取失败',
}

function ResearchSourceStrip({ research, onNavigate }) {
  const sources = research?.sources || []
  return (
    <section className="research-source-strip" aria-label="统一研究证据源">
      <div className="research-source-head">
        <div>
          <span className="eyebrow">统一研究证据</span>
          <h3>研究结果只从这里进入行动与验证</h3>
        </div>
        <span>{research?.summary?.ready_source_count ?? 0}/{research?.summary?.source_count ?? 4} 个引擎已有可核验结果</span>
      </div>
      <div className="research-source-grid">
        {sources.length > 0 ? sources.map((source) => (
          <button type="button" key={source.id} className={`research-source-card ${source.status}`} onClick={() => onNavigate(source.target)}>
            <span>{source.label}</span>
            <b>{SOURCE_STATUS[source.status] || source.status}</b>
            <small>{source.summary}</small>
            <i>{source.evidence_status === 'verified' ? '证据已验证' : source.evidence_status === 'partial' ? '部分证据' : source.evidence_status === 'invalid' ? '完整性异常' : '等待证据'}</i>
          </button>
        )) : (
          <div className="research-source-empty">正在读取 Agent、机会工厂、收益实验室和组合情景实验室的持久化结果。</div>
        )}
      </div>
    </section>
  )
}

export default function DashboardTab({ goPortfolio, goFunds, goMarket, goAgent, goOpportunities, onTaskSummaryChange }) {
  const [decision, setDecision] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function refresh() {
    setLoading(true)
    setError('')
    try {
      const result = await fetchDecisionCenter()
      setDecision(result)
      onTaskSummaryChange?.(result?.task_inbox?.summary || null)
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
  const valuationReady = Boolean(portfolio?.valuation?.runtime_gate?.risk_analysis_eligible)
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
    else if (target === 'opportunities') goOpportunities('campaigns')
    else if (target === 'opportunity_profit') goOpportunities('profit')
    else if (target === 'opportunity_committee') goOpportunities('committee')
    else if (target === 'twin') goPortfolio('twin')
  }

  function taskUpdated(result) {
    const task = result?.task
    if (!task) return
    onTaskSummaryChange?.(result.summary || null)
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
      <CapitalDecisionCommand onNavigate={navigate} />

      <section className="command-hero" aria-label="今日投资决策">
        <div>
          <span className="eyebrow">今日决策</span>
          <h2>{next ? `${next.required_for_decision ? '先完成' : '下一步验证'}：${next.title}` : workflow?.decision_ready ? '决策证据门槛已就绪' : '正在核对决策基础'}</h2>
          <p>{next?.description || '先完成组合事实、可信估值、风险政策和持有纪律，再研究市场机会。'}</p>
        </div>
        <div className="command-hero-status">
          <span>{unavailableCount > 0 ? `${unavailableCount} 个真实来源暂不可用` : '真实来源已完成汇总'}</span>
          <button type="button" onClick={refresh} disabled={loading}><RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />{loading ? '刷新中' : '刷新全部证据'}</button>
        </div>
      </section>

      <DecisionWorkflow workflow={workflow} onNavigate={navigate} />

      <ResearchSourceStrip research={decision?.research} onNavigate={navigate} />

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
              <SummaryMetric label={valuationReady ? '可信估值总额' : '用户确认总额'} value={money(summary.total_amount)} />
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
