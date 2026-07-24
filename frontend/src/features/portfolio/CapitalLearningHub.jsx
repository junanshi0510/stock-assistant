import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  BadgeCheck,
  Banknote,
  BookOpenCheck,
  CalendarClock,
  CheckCircle2,
  ChartNoAxesCombined,
  CircleDollarSign,
  Database,
  Fingerprint,
  GitCompareArrows,
  History,
  ListChecks,
  LockKeyhole,
  RefreshCw,
  Scale,
  ShieldCheck,
  Target,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import {
  createPortfolioCapitalExecutionEvent,
  fetchPortfolioCapitalOutcomeJob,
  fetchPortfolioCapitalLearning,
  fetchPortfolioCapitalPlanExecution,
  fetchPortfolioCapitalPlanOutcomes,
  refreshPortfolioCapitalPlanOutcome,
  reviewPortfolioCapitalExecutionDeviation,
} from '../../api/portfolio'

const LIFECYCLE = {
  awaiting_execution: ['等待成交对账', 'waiting'],
  partial: ['部分执行', 'partial'],
  reconciled: ['执行已对齐', 'verified'],
  deviated: ['执行有偏差', 'warning'],
  reviewed: ['偏差已复核', 'verified'],
  integrity_failed: ['完整性失败', 'danger'],
  not_applicable: ['无需执行', 'waiting'],
}

const PLAN_STATUS = {
  ready: '可限额试投',
  watch: '继续观察',
  blocked: '暂停新增',
}

const LEARNING_STATUS = {
  collecting: ['积累样本', 'collecting'],
  review_selection: ['复核选股', 'warning'],
  review_execution: ['复核执行', 'warning'],
  stable: ['纪律稳定', 'stable'],
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `¥${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function pct(value, signed = false) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function dateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-CN')
}

function shortHash(value) {
  return value ? `${String(value).slice(0, 12)}…` : '—'
}

function lifecycleLabel(value) {
  return LIFECYCLE[value]?.[0] || value || '等待成交对账'
}

function lifecycleTone(value) {
  return LIFECYCLE[value]?.[1] || 'waiting'
}

function Metric({ icon: Icon, label, value, detail, tone = '' }) {
  return (
    <article className={`capital-learning-metric ${tone}`}>
      <Icon size={19} aria-hidden="true" />
      <span><small>{label}</small><b>{value}</b><em>{detail}</em></span>
    </article>
  )
}

function Workflow() {
  const steps = [
    [LockKeyhole, '冻结计划', '锁定当时的证据、候选与金额上限'],
    [ListChecks, '绑定成交', '选择真实买入并确认人民币结算金额'],
    [GitCompareArrows, '解释偏差', '区分未成交、超额投入与计划外买入'],
    [ChartNoAxesCombined, '学习结果', '观察 5 / 20 / 60 交易日相对基准结果'],
  ]
  return (
    <div className="capital-learning-workflow" aria-label="资本计划闭环">
      {steps.map(([Icon, title, detail], index) => (
        <article key={title}>
          <span>{index + 1}</span>
          <Icon size={17} aria-hidden="true" />
          <div><b>{title}</b><small>{detail}</small></div>
          {index < steps.length - 1 && <ArrowRight size={14} className="workflow-arrow" aria-hidden="true" />}
        </article>
      ))}
    </div>
  )
}

function Scorecard({ scorecard }) {
  const [label, tone] = LEARNING_STATUS[scorecard?.status] || LEARNING_STATUS.collecting
  const horizons = scorecard?.horizons || []
  return (
    <section className="capital-learning-scorecard">
      <div className="capital-learning-section-head">
        <div>
          <span className="eyebrow">DECISION LEARNING · 只用成熟的真实结果</span>
          <h3>这套决策和执行纪律，历史上到底有没有创造相对价值</h3>
          <p>{scorecard?.next_action || '正在积累独立执行计划的精确交易日结果。'}</p>
        </div>
        <span className={`capital-learning-state ${tone}`}>{label}</span>
      </div>
      <div className="capital-learning-horizon-grid">
        {horizons.map((item) => {
          const mean = item.executed_path?.mean
          const gap = item.implementation_gap?.mean
          return (
            <article key={item.trading_days}>
              <header>
                <span>{item.trading_days} 交易日</span>
                <b>{item.mature_plan_count} 个成熟计划</b>
              </header>
              <div>
                <span>执行路径超额<strong className={mean > 0 ? 'positive' : mean < 0 ? 'negative' : ''}>{pct(mean, true)}</strong></span>
                <span>正超额比例<strong>{pct(item.positive_excess_rate_pct)}</strong></span>
                <span>平均实施差值<strong className={gap > 0 ? 'positive' : gap < 0 ? 'negative' : ''}>{pct(gap, true)}</strong></span>
                <span>最差一期<strong className={item.worst_executed_excess_return_pct < 0 ? 'negative' : ''}>{pct(item.worst_executed_excess_return_pct, true)}</strong></span>
              </div>
              <footer>
                {item.evidence_status === 'decision_eligible'
                  ? <><BadgeCheck size={13} />达到最小学习样本</>
                  : <><CalendarClock size={13} />至少 6 个样本后才调整规则</>}
              </footer>
            </article>
          )
        })}
      </div>
      <div className="capital-learning-regimes">
        <span>20 日市场状态切片</span>
        {(scorecard?.regime_breakdown_20d || []).length ? scorecard.regime_breakdown_20d.map((item) => (
          <div key={item.regime}>
            <b>{item.regime}</b>
            <small>{item.sample_count} 期</small>
            <strong className={item.mean > 0 ? 'positive' : item.mean < 0 ? 'negative' : ''}>{pct(item.mean, true)}</strong>
          </div>
        )) : <small>样本成熟后显示，避免把偶然行情当作稳定规律。</small>}
      </div>
    </section>
  )
}

function PlanList({ items, selectedId, onSelect }) {
  return (
    <aside className="capital-learning-plans">
      <header>
        <div><History size={17} /><span><b>冻结计划队列</b><small>按时间倒序</small></span></div>
        <strong>{items.length}</strong>
      </header>
      <div>
        {items.map((item) => {
          const active = selectedId === item.plan_id
          return (
            <button type="button" className={active ? 'active' : ''} key={item.plan_id} onClick={() => onSelect(item.plan_id)}>
              <span>
                <i>{item.decision_date}</i>
                <em className={lifecycleTone(item.lifecycle_status)}>{lifecycleLabel(item.lifecycle_status)}</em>
              </span>
              <b>{item.primary_action?.headline || PLAN_STATUS[item.plan_status] || '历史资本计划'}</b>
              <small>
                计划 {money(item.capital?.planned_deployment_cny)}
                {item.latest_execution ? ` · 实际 ${money(item.latest_execution.settled_amount_cny)}` : ''}
              </small>
              <code>{shortHash(item.latest_execution?.event_hash || item.plan_id)}</code>
            </button>
          )
        })}
        {!items.length && (
          <div className="capital-learning-empty">
            <Database size={22} />
            <b>还没有冻结资本计划</b>
            <span>回到首页，在证据门禁通过后先冻结一份计划。</span>
          </div>
        )}
      </div>
    </aside>
  )
}

function Reconciliation({ execution, lifecycle }) {
  const result = execution?.result
  if (!result) {
    return (
      <div className="capital-learning-empty compact">
        {lifecycle === 'not_applicable' ? <ShieldCheck size={21} /> : <GitCompareArrows size={21} />}
        <b>{lifecycle === 'not_applicable' ? '该计划无需成交对账' : '等待第一次成交对账'}</b>
        <span>{lifecycle === 'not_applicable'
          ? '计划没有获得资金资格，只保留当时的证据与阻断原因。'
          : '选择已经发生的真实股票买入，系统才会比较计划与执行。'}</span>
      </div>
    )
  }
  return (
    <div className="capital-reconciliation">
      <div className="capital-reconciliation-kpis">
        <span>计划覆盖率<b>{pct(result.plan_coverage_pct)}</b></span>
        <span>绝对偏差<b>{pct(result.absolute_deviation_pct)}</b></span>
        <span>计划外金额<b>{money(result.off_plan_settled_amount_cny)}</b></span>
        <span>加权执行延迟<b>{result.weighted_execution_lag_calendar_days ?? '—'} 天</b></span>
      </div>
      <div className="capital-reconciliation-table">
        <div className="head"><span>标的</span><span>计划</span><span>实际结算</span><span>偏差</span><span>状态</span></div>
        {(result.candidate_reconciliation || []).map((item) => (
          <div key={`${item.market}:${item.symbol}`}>
            <span><b>{item.name}</b><small>{item.market} · {item.symbol}</small></span>
            <span>{money(item.planned_amount_cny)}</span>
            <span>{money(item.actual_settled_amount_cny)}</span>
            <span className={item.deviation_amount_cny > 0 ? 'negative' : item.deviation_amount_cny < 0 ? 'warning-text' : ''}>{money(item.deviation_amount_cny)}</span>
            <span><em className={item.status}>{{
              aligned: '对齐',
              partial: '部分',
              unfilled: '未成交',
              over: '超额',
            }[item.status] || item.status}</em></span>
          </div>
        ))}
        {(result.off_plan_transactions || []).map((item) => (
          <div key={`off:${item.market}:${item.symbol}`} className="off-plan">
            <span><b>{item.symbol}</b><small>{item.market} · 计划外</small></span>
            <span>—</span>
            <span>{money(item.actual_settled_amount_cny)}</span>
            <span className="negative">{money(item.actual_settled_amount_cny)}</span>
            <span><em className="off_plan">计划外</em></span>
          </div>
        ))}
      </div>
    </div>
  )
}

function OutcomeAttribution({ outcome, lifecycle }) {
  const result = outcome?.result
  if (!result) {
    return (
      <div className="capital-learning-empty compact">
        {lifecycle === 'not_applicable' ? <ShieldCheck size={21} /> : <ChartNoAxesCombined size={21} />}
        <b>{lifecycle === 'not_applicable' ? '该计划不进入收益归因' : '成交对账后开始观察'}</b>
        <span>{lifecycle === 'not_applicable'
          ? '没有真实资金执行，就不制造纸面“盈利结果”。'
          : '系统会按真实交易日自动补齐 5 / 20 / 60 日结果，不按自然日凑数。'}</span>
      </div>
    )
  }
  return (
    <div className="capital-outcome">
      <div className="capital-outcome-method">
        <span><Target size={14} /><b>选择结果</b> 冻结计划按决策日基线相对市场基准</span>
        <span><GitCompareArrows size={14} /><b>实施差值</b> 真实成交路径减去冻结计划路径</span>
        <span><Fingerprint size={14} /><b>观察哈希</b> {shortHash(outcome.result_sha256)}</span>
      </div>
      <div className="capital-outcome-table">
        <div className="head"><span>窗口</span><span>计划选择超额</span><span>真实执行超额</span><span>实施差值</span><span>覆盖 / 状态</span></div>
        {(result.horizons || []).map((item) => {
          const executed = item.executed_path_excess_return_pct
          const gap = item.implementation_gap_pct
          return (
            <div key={item.trading_days}>
              <span><b>{item.trading_days} 日</b><small>精确交易日</small></span>
              <span className={item.planned_decision_excess_return_pct > 0 ? 'positive' : item.planned_decision_excess_return_pct < 0 ? 'negative' : ''}>{pct(item.planned_decision_excess_return_pct, true)}</span>
              <span className={executed > 0 ? 'positive' : executed < 0 ? 'negative' : ''}>{pct(executed, true)}</span>
              <span className={gap > 0 ? 'positive' : gap < 0 ? 'negative' : ''}>{pct(gap, true)}</span>
              <span>
                {item.status === 'complete' ? <em className="complete"><CheckCircle2 size={12} />完整</em> : <em className="collecting"><CalendarClock size={12} />积累中</em>}
                <small>{pct(item.executed_coverage_pct)} 执行覆盖</small>
              </span>
            </div>
          )
        })}
      </div>
      <div className="capital-outcome-boundary">
        <AlertTriangle size={14} />
        <span>{result.boundaries?.notice}</span>
      </div>
    </div>
  )
}

export default function CapitalLearningHub() {
  const [overview, setOverview] = useState(null)
  const [selectedId, setSelectedId] = useState('')
  const [context, setContext] = useState(null)
  const [outcomes, setOutcomes] = useState([])
  const [selected, setSelected] = useState({})
  const [amounts, setAmounts] = useState({})
  const [acknowledged, setAcknowledged] = useState(false)
  const [reviewNote, setReviewNote] = useState('')
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [operation, setOperation] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function hydrateTransactions(nextContext) {
    const nextSelected = {}
    const nextAmounts = {}
    for (const item of nextContext?.eligible_transactions || []) {
      if (item.already_bound_to_plan) nextSelected[item.id] = true
      const amount = item.confirmed_settled_amount_cny ?? item.suggested_settled_amount_cny
      if (amount != null) nextAmounts[item.id] = String(amount)
    }
    setSelected(nextSelected)
    setAmounts(nextAmounts)
    setAcknowledged(false)
    setReviewNote('')
    setReviewAcknowledged(false)
  }

  async function openPlan(planId) {
    if (!planId) {
      setSelectedId('')
      setContext(null)
      setOutcomes([])
      return
    }
    setSelectedId(planId)
    setDetailLoading(true)
    setError('')
    try {
      const [execution, history] = await Promise.all([
        fetchPortfolioCapitalPlanExecution(planId),
        fetchPortfolioCapitalPlanOutcomes(planId),
      ])
      setContext(execution)
      setOutcomes(history.items || [])
      hydrateTransactions(execution)
    } catch (requestError) {
      setError(requestError.message || '资本计划详情读取失败')
      setContext(null)
      setOutcomes([])
    } finally {
      setDetailLoading(false)
    }
  }

  async function load(preferredId = '') {
    setLoading(true)
    setError('')
    try {
      const result = await fetchPortfolioCapitalLearning(50)
      setOverview(result)
      const ids = (result.items || []).map((item) => item.plan_id)
      const nextId = ids.includes(preferredId) ? preferredId : ids[0] || ''
      await openPlan(nextId)
    } catch (requestError) {
      setError(requestError.message || '决策学习中枢读取失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const selectedTransactions = useMemo(
    () => (context?.eligible_transactions || []).filter((item) => selected[item.id]),
    [context, selected],
  )
  const submission = useMemo(
    () => selectedTransactions.map((item) => ({
      transaction_id: item.id,
      settled_amount_cny: Number(amounts[item.id]),
    })),
    [selectedTransactions, amounts],
  )
  const invalidAmount = submission.some((item) => !Number.isFinite(item.settled_amount_cny) || item.settled_amount_cny <= 0)
  const latestOutcome = outcomes[0] || null
  const canReconcile = context?.plan?.status === 'ready'
    && context?.execution_verification?.verified !== false
    && submission.length > 0
    && !invalidAmount
    && acknowledged

  async function submitExecution(event) {
    event.preventDefault()
    if (!canReconcile) return
    setOperation('execution')
    setError('')
    setNotice('')
    try {
      const result = await createPortfolioCapitalExecutionEvent(selectedId, {
        transactions: submission,
        acknowledged: true,
        expected_previous_event_hash: context?.latest_execution?.event_hash || null,
      })
      setNotice(result.created ? '真实成交已写入不可变执行链，月度预算已同步扣减。' : '这些成交已经确认过，本次幂等复用了原事件。')
      await load(selectedId)
    } catch (requestError) {
      setError(requestError.message || '成交对账保存失败')
    } finally {
      setOperation('')
    }
  }

  async function refreshOutcome() {
    if (!selectedId) return
    setOperation('outcome')
    setError('')
    setNotice('正在读取真实行情并核对精确交易日窗口…')
    try {
      const accepted = await refreshPortfolioCapitalPlanOutcome(selectedId)
      if (!accepted.job_id) {
        setNotice(accepted.created ? '本次真实结果观察已保存；未成熟窗口会继续自动跟踪。' : '今天已经观察过该执行事件，已幂等复用。')
        await load(selectedId)
        return
      }

      setNotice('结果观察已进入后台队列；页面可以继续使用，正在核对任务状态…')
      let job = null
      for (let attempt = 0; attempt < 12; attempt += 1) {
        job = await fetchPortfolioCapitalOutcomeJob(accepted.job_id)
        if (['succeeded', 'partial', 'failed', 'cancelled'].includes(job.status)) break
        await new Promise((resolve) => globalThis.setTimeout(resolve, 1000))
      }

      if (job?.status === 'succeeded' || job?.status === 'partial') {
        setNotice('本次真实结果观察已保存；未成熟窗口会继续由后台自动跟踪。')
        await load(selectedId)
      } else if (job?.status === 'failed' || job?.status === 'cancelled') {
        throw new Error(job.error?.message || '后台结果观察未完成')
      } else {
        setNotice(`后台任务仍在运行（${String(accepted.job_id).slice(0, 12)}…）；无需停留在此页，稍后刷新即可查看结果。`)
      }
    } catch (requestError) {
      setError(requestError.message || '真实结果观察失败')
      setNotice('')
    } finally {
      setOperation('')
    }
  }

  async function reviewDeviation(event) {
    event.preventDefault()
    if (!selectedId || context?.lifecycle_status !== 'deviated') return
    setOperation('review')
    setError('')
    setNotice('')
    try {
      const result = await reviewPortfolioCapitalExecutionDeviation(selectedId, {
        note: reviewNote.trim(),
        acknowledged: reviewAcknowledged,
        expected_previous_event_hash: context.latest_execution.event_hash,
      })
      setNotice(result.created
        ? '偏差复核已写入不可变事件链；真实成交、偏差数值和预算占用均保持不变。'
        : '该偏差已经复核，本次幂等复用了原事件。')
      await load(selectedId)
    } catch (requestError) {
      setError(requestError.message || '偏差复核保存失败')
    } finally {
      setOperation('')
    }
  }

  if (loading && !overview) {
    return (
      <section className="capital-learning-shell loading" aria-busy="true">
        <span className="spinner" />
        <div><b>正在建立资本计划闭环</b><small>核对冻结计划、真实成交、月度预算和精确交易日结果</small></div>
      </section>
    )
  }

  if (error && !overview) {
    return (
      <section className="capital-learning-shell failed">
        <AlertTriangle size={23} />
        <div><b>决策学习中枢暂不可用</b><span>{error}</span></div>
        <button type="button" onClick={() => load()}>重试</button>
      </section>
    )
  }

  const month = overview?.month_execution || {}
  const scorecard = overview?.scorecard || {}
  const primaryHorizon = (scorecard.horizons || []).find((item) => item.trading_days === 20) || {}
  const [learningLabel, learningTone] = LEARNING_STATUS[scorecard.status] || LEARNING_STATUS.collecting

  return (
    <section className="capital-learning-shell">
      <header className="capital-learning-hero">
        <div>
          <span className="eyebrow">CAPITAL PLAN REALIZATION · 资本计划兑现与决策学习中枢</span>
          <h2>把“系统说该怎么做”与“你实际上怎么做、结果如何”连接起来</h2>
          <p>一笔真实成交只归属一个冻结计划；系统按确认的人民币结算金额扣减本月预算，并用精确 5 / 20 / 60 交易日结果区分选股质量与执行偏差。</p>
        </div>
        <div>
          <span className={`capital-learning-state ${learningTone}`}>{learningLabel}</span>
          <button type="button" onClick={() => load(selectedId)} disabled={Boolean(operation)}>
            <RefreshCw size={15} className={loading ? 'spin-icon' : ''} />刷新全链路
          </button>
        </div>
      </header>

      {(error || notice) && <div className={`capital-learning-notice ${error ? 'error' : ''}`}>{error || notice}</div>}

      <div className="capital-learning-metrics">
        <Metric icon={WalletCards} label="本月已确认投入" value={money(month.confirmed_settled_amount_cny)} detail={`${month.ready_plan_count || 0} 份可执行冻结计划`} />
        <Metric icon={BookOpenCheck} label="已有真实执行" value={`${scorecard.execution_plan_count || 0} 期`} detail={`${scorecard.observed_plan_count || 0} 期已有结果观察`} tone="teal" />
        <Metric icon={Target} label="20 日成熟样本" value={`${primaryHorizon.mature_plan_count || 0} 期`} detail={`正超额 ${pct(primaryHorizon.positive_excess_rate_pct)}`} tone="gold" />
        <Metric
          icon={(primaryHorizon.executed_path?.mean || 0) >= 0 ? TrendingUp : TrendingDown}
          label="20 日执行路径超额"
          value={pct(primaryHorizon.executed_path?.mean, true)}
          detail={`实施差值 ${pct(primaryHorizon.implementation_gap?.mean, true)}`}
          tone={(primaryHorizon.executed_path?.mean || 0) >= 0 ? 'teal' : 'red'}
        />
      </div>

      <Workflow />
      <Scorecard scorecard={scorecard} />

      <div className="capital-learning-console">
        <PlanList items={overview?.items || []} selectedId={selectedId} onSelect={openPlan} />

        <main className="capital-learning-detail">
          {detailLoading ? (
            <div className="capital-learning-empty detail-loading"><span className="spinner" /><b>正在核对计划与真实流水</b></div>
          ) : context ? (
            <>
              <header className="capital-learning-plan-head">
                <div>
                  <span className="eyebrow">SELECTED PLAN · {context.plan.decision_date}</span>
                  <h3>{context.plan.primary_action?.headline || '冻结资本计划'}</h3>
                  <p>{context.plan.status === 'ready'
                    ? `执行窗口 ${context.window.starts_on} 至 ${context.window.ends_on} · 只接受计划后真实股票买入`
                    : '该计划未获得资金资格，仅保留冻结证据和阻断原因'}</p>
                </div>
                <div>
                  <span className={`capital-learning-state ${lifecycleTone(context.lifecycle_status)}`}>{lifecycleLabel(context.lifecycle_status)}</span>
                  <code>{shortHash(context.latest_execution?.event_hash || context.plan.result_sha256)}</code>
                </div>
              </header>

              <div className="capital-plan-ledger">
                <article><Target size={17} /><span>冻结计划<b>{money(context.planned_amount_cny)}</b><small>研究金额上限</small></span></article>
                <article><Banknote size={17} /><span>实际结算<b>{money(context.latest_execution?.result?.actual_settled_amount_cny || 0)}</b><small>用户确认人民币金额</small></span></article>
                <article><Scale size={17} /><span>本月已占预算<b>{money(month.confirmed_settled_amount_cny)}</b><small>删除流水也不会释放</small></span></article>
                <article><ShieldCheck size={17} /><span>执行链完整性<b>{context.plan.status !== 'ready' ? '不适用' : context.execution_verification ? (context.execution_verification.verified ? '通过' : '失败') : '等待事件'}</b><small>{context.events.length} 个累计事件</small></span></article>
              </div>

              <section className="capital-learning-section">
                <div className="capital-learning-section-head">
                  <div>
                    <span className="eyebrow">REAL TRANSACTION BINDING · 真实成交绑定</span>
                    <h3>确认哪些真实买入是在执行这份计划</h3>
                    <p>A 股会给出本币金额参考；港股和美股必须按券商实际人民币结算金额确认，避免伪精确汇率。</p>
                  </div>
                  <span>{selectedTransactions.length} / {context.eligible_transactions.length} 笔已选择</span>
                </div>

                <form onSubmit={submitExecution}>
                  <div className="capital-transaction-list">
                    {(context.eligible_transactions || []).map((item) => (
                      <div className={`capital-transaction ${item.plan_match ? 'matched' : 'off-plan'} ${item.already_bound_to_plan ? 'locked' : ''}`} key={item.id}>
                        <input
                          type="checkbox"
                          checked={Boolean(selected[item.id])}
                          disabled={item.already_bound_to_plan || context.execution_verification?.verified === false}
                          onChange={(event) => setSelected((current) => ({ ...current, [item.id]: event.target.checked }))}
                        />
                        <span className="capital-transaction-main">
                          <b>{item.name || item.code}<code>{item.market} · {item.code}</code></b>
                          <small>{item.trade_date} · {item.shares} 股 × {item.unit_price} · 费用 {item.fee || 0}</small>
                        </span>
                        <em className={item.plan_match ? 'match' : 'off'}>{item.plan_match ? '匹配计划' : '计划外'}</em>
                        <span className="capital-transaction-amount">
                          <small>实际人民币结算</small>
                          <label>
                            <span>¥</span>
                            <input
                              type="number"
                              min="0.01"
                              max="100000000"
                              step="0.01"
                              value={amounts[item.id] || ''}
                              disabled={item.already_bound_to_plan}
                              onChange={(event) => setAmounts((current) => ({ ...current, [item.id]: event.target.value }))}
                              aria-label={`${item.name || item.code} 人民币结算金额`}
                            />
                          </label>
                        </span>
                        {item.already_bound_to_plan && <LockKeyhole size={14} className="transaction-lock" aria-label="已锁定" />}
                      </div>
                    ))}
                    {!context.eligible_transactions.length && context.plan.status !== 'ready' && (
                      <div className="capital-learning-empty compact">
                        <ShieldCheck size={21} />
                        <b>这份计划没有获得资金执行资格</b>
                        <span>状态为“{PLAN_STATUS[context.plan.status] || context.plan.status}”，保留审计记录即可，不需要虚构成交来完成流程。</span>
                      </div>
                    )}
                    {!context.eligible_transactions.length && context.plan.status === 'ready' && (
                      <div className="capital-learning-empty compact">
                        <Database size={21} />
                        <b>执行窗口内还没有可绑定的股票买入</b>
                        <span>先在“交易账本”记录真实成交；不要为了完成流程虚构流水。</span>
                      </div>
                    )}
                  </div>

                  {context.plan.status === 'ready' && (
                    <div className="capital-execution-confirm">
                      <label>
                        <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
                        <span>{context.acknowledgment.text}</span>
                      </label>
                      <button type="submit" disabled={!canReconcile || Boolean(operation)}>
                        <LockKeyhole size={15} />
                        {operation === 'execution' ? '写入执行链…' : context.latest_execution ? '追加并重新对账' : '确认成交并对账'}
                      </button>
                    </div>
                  )}
                </form>
              </section>

              <section className="capital-learning-section">
                <div className="capital-learning-section-head">
                  <div>
                    <span className="eyebrow">PLAN VS ACTUAL · 计划与实际偏差</span>
                    <h3>先解释执行偏差，再评价策略好坏</h3>
                    <p>部分成交、超额投入、延迟和计划外买入会单独显示，不把执行问题错误归咎于选股模型。</p>
                  </div>
                  <span className={`capital-learning-state ${lifecycleTone(context.lifecycle_status)}`}>{lifecycleLabel(context.lifecycle_status)}</span>
                </div>
                <Reconciliation execution={context.latest_execution} lifecycle={context.lifecycle_status} />
                {context.lifecycle_status === 'deviated' && (
                  <form className="capital-deviation-review" onSubmit={reviewDeviation}>
                    <div>
                      <AlertTriangle size={17} />
                      <span>
                        <b>这次执行明显偏离冻结计划</b>
                        <small>请说明为什么仍接受这次实际执行。复核不会删除偏差，也不会释放已经占用的月度预算。</small>
                      </span>
                    </div>
                    <textarea
                      value={reviewNote}
                      minLength={10}
                      maxLength={500}
                      onChange={(event) => setReviewNote(event.target.value)}
                      placeholder="例如：计划冻结后可用现金发生变化，已人工复核超额投入仍在投资政策边界内……"
                    />
                    <label>
                      <input type="checkbox" checked={reviewAcknowledged} onChange={(event) => setReviewAcknowledged(event.target.checked)} />
                      <span>我确认实际成交事实、偏差和预算占用保持不变，只完成流程复核。</span>
                    </label>
                    <button type="submit" disabled={reviewNote.trim().length < 10 || !reviewAcknowledged || Boolean(operation)}>
                      <BookOpenCheck size={15} />
                      {operation === 'review' ? '写入复核事件…' : '确认偏差复核'}
                    </button>
                  </form>
                )}
              </section>

              <section className="capital-learning-section">
                <div className="capital-learning-section-head">
                  <div>
                    <span className="eyebrow">OUTCOME ATTRIBUTION · 精确交易日结果归因</span>
                    <h3>冻结计划的选择质量，与真实执行路径分别记账</h3>
                    <p>只在计划篮子、执行篮子和市场基准均达到至少 90% 覆盖时，才把窗口标记为完整。</p>
                  </div>
                  <button type="button" onClick={refreshOutcome} disabled={!context.latest_execution || context.execution_verification?.verified === false || Boolean(operation)}>
                    <ChartNoAxesCombined size={15} />
                    {operation === 'outcome' ? '观察中…' : '立即观察真实结果'}
                  </button>
                </div>
                <OutcomeAttribution outcome={latestOutcome} lifecycle={context.lifecycle_status} />
              </section>

              <footer className="capital-learning-audit">
                <div><Fingerprint size={15} /><span><b>计划证据</b><code>{context.plan.result_sha256}</code></span></div>
                <div><ShieldCheck size={15} /><span><b>执行边界</b><small>{context.boundaries.notice}</small></span></div>
                <div><CircleDollarSign size={15} /><span><b>收益边界</b><small>结果用于改进决策流程，不代表未来预测或保证盈利。</small></span></div>
              </footer>
            </>
          ) : (
            <div className="capital-learning-empty detail-loading">
              <WalletCards size={23} />
              <b>请选择一份冻结计划</b>
              <span>计划、成交、偏差和收益观察会显示在这里。</span>
            </div>
          )}
        </main>
      </div>
    </section>
  )
}
