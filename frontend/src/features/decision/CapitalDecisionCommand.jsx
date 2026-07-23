import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDollarSign,
  Database,
  FlaskConical,
  History,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Target,
  WalletCards,
  XCircle,
} from 'lucide-react'
import {
  createHoldingsExposureSnapshot,
  createPortfolioActionReport,
  fetchPortfolioCapitalDecision,
  fetchPortfolioCapitalDecisionPlan,
  fetchPortfolioCapitalDecisionPlans,
  freezePortfolioCapitalDecision,
  refreshPortfolioValuation,
} from '../../api/portfolio'

const STATUS = {
  ready: ['可限额试投', 'ready'],
  watch: ['继续观察', 'watch'],
  blocked: ['暂停新增', 'blocked'],
}

const GATE = {
  pass: [CheckCircle2, '通过'],
  watch: [AlertTriangle, '等待'],
  block: [XCircle, '阻断'],
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

function hash(value) {
  return value ? `${String(value).slice(0, 12)}…` : '—'
}

function GateCard({ gate }) {
  const [Icon, label] = GATE[gate.status] || GATE.watch
  return (
    <article className={`capital-gate state-${gate.status}`}>
      <Icon size={17} aria-hidden="true" />
      <div>
        <span>{gate.label}</span>
        <b>{label}</b>
        <small>{gate.detail}</small>
      </div>
    </article>
  )
}

function CandidateCard({ item }) {
  const source = item.sources?.[0] || {}
  const lower = source.familywise_ci95?.lower
  return (
    <article className={`capital-candidate ${item.planned_amount_cny > 0 ? 'approved' : 'observe'}`}>
      <div className="capital-candidate-main">
        <span className="capital-market">{item.market}</span>
        <div>
          <h4>{item.name}<code>{item.symbol}</code></h4>
          <p>{item.label}</p>
        </div>
        <strong>{money(item.planned_amount_cny)}</strong>
      </div>
      <div className="capital-candidate-metrics">
        <span>计划后占比 <b>{pct(item.post_ratio_pct)}</b></span>
        <span>独立批次 <b>{source.mature_cohort_count ?? '—'}</b></span>
        <span>成本后超额 <b>{pct(source.mean_net_excess_return_pct, true)}</b></span>
        <span>家族校正下界 <b>{pct(lower, true)}</b></span>
      </div>
      <div className="capital-candidate-evidence">
        <ShieldCheck size={14} aria-hidden="true" />
        <span>{source.strategy_name || '前瞻策略'} · {source.primary_horizon_trading_days || '—'} 交易日主窗口 · 胜基准 {pct(source.positive_excess_rate_pct)}</span>
      </div>
      {item.blockers?.length > 0 && (
        <small className="capital-candidate-blockers">{item.blockers.join(' · ')}</small>
      )}
    </article>
  )
}

function ExistingAction({ item }) {
  return (
    <article className={`capital-existing action-${item.action}`}>
      <div>
        <span>{item.market || item.asset_type}</span>
        <h4>{item.name}<code>{item.code}</code></h4>
        <p>{item.rationale}</p>
      </div>
      <div>
        <strong>{item.label}</strong>
        <span>{money(item.current_amount_cny)}</span>
        {item.review_amount_cny > 0 && <small>复核金额 {money(item.review_amount_cny)}</small>}
      </div>
    </article>
  )
}

function StressMatrix({ rows }) {
  if (!rows?.length) {
    return <div className="capital-empty">组合风险底图未通过前，不生成伪精确压力数字。</div>
  }
  return (
    <div className="capital-stress-table">
      <div className="capital-stress-head">
        <span>说明性情景</span><span>投入前最坏损失</span><span>计划后最坏损失</span><span>增量风险</span><span>预算占用</span><span>政策</span>
      </div>
      {rows.map((row) => (
        <div className="capital-stress-row" key={row.scenario_id}>
          <span><b>{row.scenario_name}</b><small>可编辑假设 · 非概率预测</small></span>
          <span>{money(row.current_worst_loss_cny)}<small>{pct(row.current_worst_loss_pct)}</small></span>
          <span>{money(row.proposed_worst_loss_cny)}<small>{pct(row.proposed_worst_loss_pct)}</small></span>
          <span className={row.incremental_worst_loss_cny > 0 ? 'risk-up' : 'risk-flat'}>{money(row.incremental_worst_loss_cny)}</span>
          <span>{pct(row.risk_budget_utilization_pct)}</span>
          <span className={row.policy_passed ? 'policy-pass' : 'policy-block'}>{row.policy_passed ? '通过' : row.proposed_gate_blocks?.join(' / ') || '阻断'}</span>
        </div>
      ))}
    </div>
  )
}

export default function CapitalDecisionCommand({ onNavigate }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [operation, setOperation] = useState('')
  const [notice, setNotice] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [selectedPlan, setSelectedPlan] = useState(null)

  async function load() {
    setLoading(true)
    setError('')
    try {
      setData(await fetchPortfolioCapitalDecision())
    } catch (requestError) {
      setError(requestError.message || '投资指挥台读取失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function freeze() {
    setOperation('freeze')
    setNotice('')
    try {
      const result = await freezePortfolioCapitalDecision()
      setNotice(result.created ? '当前决策计划已冻结并写入不可变审计账本。' : '同一组证据已冻结，本次已幂等复用。')
      await load()
      if (historyOpen) await loadHistory()
    } catch (requestError) {
      setError(requestError.message || '决策计划冻结失败')
    } finally {
      setOperation('')
    }
  }

  async function rebuildEvidence() {
    setOperation('rebuild')
    setError('')
    setNotice('正在刷新可信估值…')
    const partialErrors = []
    try {
      await refreshPortfolioValuation(true)
      setNotice('估值已刷新，正在重建持仓行动报告…')
      try {
        await createPortfolioActionReport(8)
      } catch (requestError) {
        partialErrors.push(`行动报告：${requestError.message}`)
      }
      setNotice('正在刷新组合穿透风险底图…')
      try {
        await createHoldingsExposureSnapshot()
      } catch (requestError) {
        partialErrors.push(`风险底图：${requestError.message}`)
      }
      await load()
      setNotice(partialErrors.length ? `部分证据未完成：${partialErrors.join('；')}` : '估值、持仓行动报告与组合风险底图已全部刷新。')
    } catch (requestError) {
      setError(requestError.message || '可信估值刷新失败')
      setNotice('')
    } finally {
      setOperation('')
    }
  }

  async function loadHistory() {
    setHistoryLoading(true)
    try {
      const result = await fetchPortfolioCapitalDecisionPlans(20)
      setHistory(result.items || [])
    } catch (requestError) {
      setError(requestError.message || '历史决策计划读取失败')
    } finally {
      setHistoryLoading(false)
    }
  }

  async function toggleHistory() {
    const next = !historyOpen
    setHistoryOpen(next)
    if (next) await loadHistory()
  }

  async function inspectPlan(planId) {
    try {
      setSelectedPlan(await fetchPortfolioCapitalDecisionPlan(planId))
    } catch (requestError) {
      setError(requestError.message || '决策计划审计详情读取失败')
    }
  }

  const capital = data?.capital || {}
  const [statusLabel, statusTone] = STATUS[data?.status] || STATUS.blocked
  const approved = useMemo(
    () => (data?.candidate_actions || []).filter((item) => Number(item.planned_amount_cny) > 0),
    [data],
  )
  const observed = useMemo(
    () => (data?.candidate_actions || []).filter((item) => Number(item.planned_amount_cny) <= 0),
    [data],
  )

  if (loading && !data) {
    return (
      <section className="capital-command loading" aria-busy="true">
        <span className="spinner" />
        <div><b>正在生成全组合资金决策</b><small>核对持仓、估值、投资政策、前瞻记分卡与压力情景</small></div>
      </section>
    )
  }

  if (error && !data) {
    return (
      <section className="capital-command failed">
        <AlertTriangle size={22} />
        <div><b>投资指挥台暂不可用</b><span>{error}</span></div>
        <button type="button" onClick={load}>重试</button>
      </section>
    )
  }

  return (
    <section className={`capital-command state-${statusTone}`} aria-label="组合资金决策引擎">
      <header className="capital-command-hero">
        <div className="capital-command-title">
          <span className="eyebrow">INVESTMENT COMMAND · 全组合下一最佳行动</span>
          <div><h2>{data?.primary_action?.headline || '正在核对资金决策'}</h2><span className={`capital-status ${statusTone}`}>{statusLabel}</span></div>
          <p>{data?.primary_action?.description}</p>
        </div>
        <div className="capital-command-buttons">
          <button type="button" className="capital-freeze" onClick={freeze} disabled={Boolean(operation)}>
            <LockKeyhole size={16} aria-hidden="true" />
            {operation === 'freeze' ? '冻结中' : data?.persistence?.binding_current ? '当前证据已冻结' : '冻结当前计划'}
          </button>
          <button type="button" className="ghost" onClick={rebuildEvidence} disabled={Boolean(operation)}>
            <RefreshCw size={16} className={operation === 'rebuild' ? 'spin-icon' : ''} aria-hidden="true" />
            {operation === 'rebuild' ? '重建证据中' : '重建全套证据'}
          </button>
          <button type="button" className="ghost" onClick={toggleHistory}>
            <History size={16} aria-hidden="true" />历史审计
          </button>
        </div>
      </header>

      {(error || notice) && <div className={`capital-notice ${error ? 'error' : ''}`}>{error || notice}</div>}

      <div className="capital-ledger">
        <article><WalletCards size={18} /><span>当前组合</span><b>{money(capital.portfolio_value_cny)}</b><small>可信人民币估值</small></article>
        <article><CircleDollarSign size={18} /><span>本月计划资金</span><b>{money(capital.monthly_new_capital_cny)}</b><small>来自已激活投资政策</small></article>
        <article className="deploy"><Target size={18} /><span>本期部署上限</span><b>{money(capital.planned_deployment_cny)}</b><small>{pct(capital.deployment_ratio_of_monthly_budget_pct)} 月度预算</small></article>
        <article><ShieldCheck size={18} /><span>保留资金</span><b>{money(capital.planned_cash_reserve_cny)}</b><small>不是券商实时余额</small></article>
      </div>

      <div className="capital-gates">
        {(data?.gates || []).map((gate) => <GateCard gate={gate} key={gate.code} />)}
      </div>

      <div className="capital-command-grid">
        <section className="capital-panel candidate-panel">
          <div className="capital-panel-head">
            <div><span className="eyebrow">新增资金计划</span><h3>{approved.length ? `${approved.length} 只候选获限额试投资格` : '本期没有候选获资金资格'}</h3></div>
            <button type="button" onClick={() => onNavigate?.('opportunity_profit')}>收益实验室<ArrowRight size={15} /></button>
          </div>
          {approved.length > 0 ? (
            <div className="capital-candidate-list">{approved.map((item) => <CandidateCard item={item} key={`${item.market}:${item.symbol}`} />)}</div>
          ) : (
            <div className="capital-empty">
              <FlaskConical size={25} />
              <b>{data?.primary_action?.label}</b>
              <span>没有通过全部门禁时，资金保持未分配；系统不会为了“给答案”降低证据标准。</span>
              {data?.data_quality?.live_capital_eligible_strategy_count > 0 && <button type="button" onClick={() => onNavigate?.('opportunity_profit')}>冻结当前策略记分卡</button>}
            </div>
          )}
          {observed.length > 0 && (
            <details className="capital-observed">
              <summary>查看 {observed.length} 只继续观察的候选</summary>
              {observed.map((item) => <CandidateCard item={item} key={`${item.market}:${item.symbol}`} />)}
            </details>
          )}
        </section>

        <section className="capital-panel existing-panel">
          <div className="capital-panel-head">
            <div><span className="eyebrow">已有仓位动作</span><h3>{data?.data_quality?.critical_existing_action_count ? `${data.data_quality.critical_existing_action_count} 项优先复核` : '已有仓位维持纪律'}</h3></div>
            <button type="button" onClick={() => onNavigate?.('portfolio')}>持仓行动中心<ArrowRight size={15} /></button>
          </div>
          {(data?.existing_position_actions || []).length ? (
            <div className="capital-existing-list">
              {data.existing_position_actions.slice(0, 8).map((item) => <ExistingAction item={item} key={`${item.holding_id}:${item.code}`} />)}
            </div>
          ) : (
            <div className="capital-empty"><Database size={24} /><b>尚未确认真实持仓</b><span>先导入持仓，才能生成组合级金额决策。</span></div>
          )}
        </section>
      </div>

      <section className="capital-panel stress-panel">
        <div className="capital-panel-head">
          <div><span className="eyebrow">投入前后压力对比</span><h3>同一账户、同一资金总额下比较最坏暴露</h3><p>使用当前暴露区间和说明性冲击，不把历史场景发生概率冒充预测。</p></div>
          <span className="capital-method">{data?.methodology?.stress}</span>
        </div>
        <StressMatrix rows={data?.stress_matrix} />
      </section>

      <footer className="capital-command-footer">
        <div><ShieldCheck size={15} /><span>{data?.boundaries?.notice}</span></div>
        <div><span>证据截止 {dateTime(data?.evidence_cutoff_at)}</span><code>{hash(data?.evidence_sha256)}</code></div>
      </footer>

      {historyOpen && (
        <section className="capital-history">
          <div className="capital-panel-head">
            <div><span className="eyebrow">不可变计划账本</span><h3>历史决策与当时证据永久绑定</h3></div>
            <span>{historyLoading ? '读取中…' : `${history.length} 份计划`}</span>
          </div>
          <div className="capital-history-grid">
            <div className="capital-history-list">
              {history.map((item) => (
                <button type="button" key={item.id} className={selectedPlan?.id === item.id ? 'active' : ''} onClick={() => inspectPlan(item.id)}>
                  <span>{item.decision_date}<i>{STATUS[item.status]?.[0] || item.status}</i></span>
                  <b>{item.primary_action?.headline || '历史资金决策'}</b>
                  <small>{money(item.capital?.planned_deployment_cny)} · {dateTime(item.created_at)}</small>
                  <code>{hash(item.result_sha256)}</code>
                </button>
              ))}
              {!historyLoading && !history.length && <div className="capital-empty">还没有冻结过决策计划。</div>}
            </div>
            <div className="capital-history-detail">
              {selectedPlan ? (
                <>
                  <div className={selectedPlan.integrity?.verified ? 'verified' : 'invalid'}>
                    <ShieldCheck size={18} /><b>{selectedPlan.integrity?.verified ? '证据与结果哈希全部通过' : '完整性校验失败'}</b>
                  </div>
                  <dl>
                    <dt>计划 ID</dt><dd>{selectedPlan.id}</dd>
                    <dt>估值快照</dt><dd>{selectedPlan.valuation_snapshot_id || '—'}</dd>
                    <dt>行动报告</dt><dd>{selectedPlan.action_report_id || '—'}</dd>
                    <dt>风险底图</dt><dd>{selectedPlan.exposure_snapshot_id || '—'}</dd>
                    <dt>策略记分卡</dt><dd>{selectedPlan.evidence?.bindings?.scorecard_ids?.join(' / ') || '—'}</dd>
                    <dt>证据哈希</dt><dd>{selectedPlan.evidence_sha256}</dd>
                    <dt>结果哈希</dt><dd>{selectedPlan.result_sha256}</dd>
                  </dl>
                </>
              ) : <div className="capital-empty">选择一份历史计划查看证据绑定与完整性。</div>}
            </div>
          </div>
        </section>
      )}
    </section>
  )
}
