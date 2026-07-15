import { useEffect, useMemo, useState } from 'react'
import {
  ArrowUpRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Layers3,
  RefreshCw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Square,
  WalletCards,
} from 'lucide-react'

const TERMINAL = new Set(['completed', 'partial', 'failed', 'cancelled', 'abstained'])

const STATUS = {
  queued: ['等待执行', 'queued'],
  running: ['正在研究', 'running'],
  completed: ['证据完整', 'complete'],
  partial: ['部分完成', 'partial'],
  failed: ['执行失败', 'failed'],
  cancelled: ['已取消', 'cancelled'],
  abstained: ['数据不足', 'partial'],
}

const ACTIONS = {
  consider_tranche: '满足条件后分批',
  batch_allocation_pending: '进入组合资金分配',
  hold_review: '持有并复核',
  hold_no_add: '持有但不加仓',
  wait: '等待改善',
  do_not_add: '当前不加仓',
  reduce_exposure: '降低暴露',
  research_only: '仅作研究',
  setup_required: '先完善约束',
  strategy_not_released: '策略未发布',
  market_data_required: '等待市场数据',
  exposure_data_required: '补齐组合穿透',
  budget_required: '确认预算',
  research: '可继续研究',
  avoid_for_now: '当前暂缓',
  data_required: '数据不足',
}

function statusMeta(status) {
  return STATUS[status] || [status || '未知', 'partial']
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function timeText(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? String(value).replace('T', ' ')
    : parsed.toLocaleString('zh-CN', { hour12: false })
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

const PURCHASE_STATUS = {
  available: '可申购',
  limited: '限额申购',
  unavailable: '暂停申购',
  unknown: '平台未明确',
}

const PREFLIGHT_STATUS = {
  ready_for_manual_purchase_review: ['已通过人工确认门禁', 'complete'],
  purchase_preflight_blocked: ['执行前复核阻断', 'failed'],
  expired: ['平台报价已过期', 'partial'],
  superseded: ['组合事实已变化', 'partial'],
  integrity_failed: ['审计完整性失败', 'failed'],
}

function initialPurchaseQuotes(allocation) {
  return ((allocation?.allocation?.items) || [])
    .filter((item) => Number(item.allocated_amount_yuan) > 0.02)
    .map((item) => ({
      code: item.code,
      name: item.name,
      platform_name: '',
      quoted_at: '',
      order_amount_yuan: String(Number(item.allocated_amount_yuan).toFixed(2)),
      entry_fee_yuan: '',
      purchase_status: '',
      purchase_limit_yuan: '',
      expected_confirmation_date: '',
    }))
}

function PortfolioAllocation({ batch, active, allocating, onCreate }) {
  const allocation = batch.allocation
  const input = batch.input || {}
  const hasBudget = Number(input.planned_amount) > 0 && input.acknowledged_available_cash === true
  const canCreate = !active && hasBudget && input.include_portfolio_context === true

  if (!allocation) {
    return (
      <section className="agent-batch-allocation" aria-label="组合资金分配">
        <div className="agent-section-head">
          <div>
            <span className="eyebrow">Portfolio Capital Allocation</span>
            <h3><WalletCards size={18} aria-hidden="true" />组合资金分配复核</h3>
          </div>
          {hasBudget && <strong className="agent-allocation-budget">总预算 {money(input.planned_amount)}</strong>}
        </div>
        <div className="agent-allocation-empty">
          <ShieldAlert size={18} aria-hidden="true" />
          <div>
            <b>{active
              ? '等待全部基金完成真实数据研究'
              : hasBudget
                ? '尚未固化组合级资金分配'
                : '本批次是仅研究模式'}</b>
            <p>{active
              ? '组合分配必须绑定全部子 Run 的最终结果哈希，运行期间不会提前猜测金额。'
              : hasBudget
                ? '生成后将形成一次不可修改的分配快照；大模型只能解释，不能改变金额。'
                : '批次创建时没有确认唯一总预算，因此不会生成任何投入金额。需要金额时应新建批次并确认真实可用资金。'}</p>
          </div>
          {canCreate && (
            <button type="button" onClick={onCreate} disabled={allocating}>
              {allocating ? <RefreshCw size={15} className="spin-icon" aria-hidden="true" /> : <Scale size={15} aria-hidden="true" />}
              {allocating ? '正在固化' : '生成分配快照'}
            </button>
          )}
        </div>
      </section>
    )
  }

  const budget = allocation.budget || {}
  const allocationDetail = allocation.allocation || {}
  const items = allocationDetail.items || []
  const ready = allocation.status === 'ready'
  const integrityFailed = allocation.status === 'integrity_failed'

  return (
    <section className="agent-batch-allocation" aria-label="组合资金分配">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Portfolio Capital Allocation</span>
          <h3><WalletCards size={18} aria-hidden="true" />组合资金分配复核</h3>
          <small>
            {allocation.snapshot?.created_at ? `快照 ${timeText(allocation.snapshot.created_at)}` : '不可变分配快照'}
            {allocation.snapshot?.integrity_verified ? ' · 完整性已验证' : ''}
          </small>
        </div>
        <span className={`agent-status ${ready ? 'complete' : 'failed'}`}>
          {ready ? '可人工复核' : integrityFailed ? '完整性失败' : '门禁阻断'}
        </span>
      </div>

      <div className="agent-allocation-metrics">
        <div><span>批次唯一总预算</span><b>{money(budget.requested_total_yuan)}</b></div>
        <div><span>已通过约束分配</span><b>{money(budget.allocated_total_yuan)}</b></div>
        <div><span>保持未投入</span><b>{money(budget.unallocated_total_yuan)}</b></div>
        <div><span>联合约束缩放</span><b>{allocationDetail.constraint_scale == null ? '-' : pct(Number(allocationDetail.constraint_scale) * 100)}</b></div>
      </div>

      {(allocation.gates || []).length > 0 && (
        <div className="agent-allocation-gates" aria-label="资金分配门禁">
          {allocation.gates.map((gate) => (
            <span key={gate.code} className={gate.status} title={gate.detail}>
              {gate.status === 'pass' ? <CheckCircle2 size={13} aria-hidden="true" /> : <CircleAlert size={13} aria-hidden="true" />}
              {gate.label}
            </span>
          ))}
        </div>
      )}

      {(allocation.blockers || []).length > 0 && (
        <div className="agent-allocation-blockers">
          {(allocation.blockers || []).map((blocker) => <p key={blocker}><CircleAlert size={14} aria-hidden="true" />{blocker}</p>)}
        </div>
      )}

      {items.length > 0 && (
        <div className="agent-allocation-table" role="table" aria-label="逐只基金组合分配">
          <div className="agent-allocation-row heading" role="row">
            <span>基金</span><span>真实风险 / 重合</span><span>可验证容量</span><span>组合分配</span><span>首批复核</span>
          </div>
          {items.map((item) => (
            <div className={`agent-allocation-row ${item.eligible ? '' : 'ineligible'}`} role="row" key={item.run_id || item.code}>
              <span><b>{item.code} {item.name}</b><small>{item.eligible ? '通过单基金门禁' : '未进入资金分配'}</small></span>
              <span><b>{pct(item.annual_volatility_pct)}</b><small>披露重合累计 {pct(item.known_overlap_sum_pct)}</small></span>
              <span><b>{money(item.capacity_yuan)}</b><small>单品与组合上限中的较小值</small></span>
              <span><strong>{money(item.allocated_amount_yuan)}</strong><small>{item.normalized_risk_weight_pct == null ? '-' : `占已分配 ${pct(item.normalized_risk_weight_pct)}`}</small></span>
              <span><b>{money(item.first_tranche_amount_yuan)}</b><small>{item.tranche_count ? `共 ${item.tranche_count} 批` : '不形成投入批次'}</small></span>
              {(item.reasons || []).length > 0 && <p>{item.reasons.join('；')}</p>}
            </div>
          ))}
        </div>
      )}

      {(allocation.warnings || []).length > 0 && (
        <div className="agent-allocation-warnings">
          {allocation.warnings.map((warning) => <p key={warning}><CircleAlert size={13} aria-hidden="true" />{warning}</p>)}
        </div>
      )}
      <p className="agent-batch-policy">{allocation.policy}</p>
    </section>
  )
}

function BatchPurchasePreflight({ batch, reviewing, onReview }) {
  const allocation = batch.allocation
  const preflight = batch.purchase_preflight
  const allocationSnapshotId = allocation?.snapshot?.id || ''
  const allocatedItems = useMemo(() => initialPurchaseQuotes(allocation), [allocationSnapshotId])
  const [formOpen, setFormOpen] = useState(false)
  const [quotes, setQuotes] = useState(allocatedItems)
  const [acknowledged, setAcknowledged] = useState(false)
  const [formError, setFormError] = useState('')

  useEffect(() => {
    setQuotes(allocatedItems)
    setAcknowledged(false)
    setFormError('')
    setFormOpen(false)
  }, [allocationSnapshotId])

  if (!allocation || allocation.status !== 'ready' || allocatedItems.length === 0) {
    return (
      <section className="agent-purchase-preflight" aria-label="批量基金申购执行前复核">
        <div className="agent-section-head">
          <div>
            <span className="eyebrow">Purchase Preflight</span>
            <h3><ShieldCheck size={18} aria-hidden="true" />申购执行前复核</h3>
          </div>
        </div>
        <div className="agent-preflight-empty">
          <Clock3 size={17} aria-hidden="true" />
          <div><b>等待组合资金分配通过</b><p>只有已固化且通过门禁的组合分配，才能逐只核对销售平台申购事实。</p></div>
        </div>
      </section>
    )
  }

  const [statusLabel, statusTone] = PREFLIGHT_STATUS[preflight?.status] || ['尚未复核', 'queued']
  const cashflow = preflight?.cashflow || {}
  const bindings = preflight?.current_bindings || {}

  function updateQuote(code, field, value) {
    setQuotes((current) => current.map((item) => (
      item.code === code ? { ...item, [field]: value } : item
    )))
  }

  async function submitReview(event) {
    event.preventDefault()
    const invalid = quotes.find((item) => {
      const activeStatus = ['available', 'limited'].includes(item.purchase_status)
      return !item.platform_name.trim()
        || !item.quoted_at
        || !item.purchase_status
        || !(Number(item.order_amount_yuan) > 0)
        || (activeStatus && item.entry_fee_yuan === '')
        || (activeStatus && !item.expected_confirmation_date)
        || (item.purchase_status === 'limited' && !(Number(item.purchase_limit_yuan) > 0))
    })
    if (invalid) {
      setFormError(`${invalid.code} 的平台、报价时间、申购状态或执行金额事实不完整`)
      return
    }
    if (!acknowledged) {
      setFormError('请确认这些数据逐只来自销售平台本次申购页')
      return
    }
    const normalizedQuotes = quotes.map((item) => ({
      code: item.code,
      platform_name: item.platform_name.trim(),
      quoted_at: new Date(item.quoted_at).toISOString(),
      currency: 'CNY',
      order_amount_yuan: Number(item.order_amount_yuan),
      entry_fee_yuan: item.entry_fee_yuan === '' ? null : Number(item.entry_fee_yuan),
      purchase_status: item.purchase_status,
      purchase_limit_yuan: item.purchase_limit_yuan === '' ? null : Number(item.purchase_limit_yuan),
      expected_confirmation_date: item.expected_confirmation_date || null,
    }))
    setFormError('')
    const succeeded = await onReview({
      expected_allocation_event_id: allocation.snapshot.id,
      expected_allocation_event_hash: allocation.snapshot.event_hash,
      expected_previous_event_hash: preflight?.snapshot?.event_hash || null,
      acknowledged_platform_quotes: true,
      quotes: normalizedQuotes,
    })
    if (succeeded) setFormOpen(false)
  }

  return (
    <section className="agent-purchase-preflight" aria-label="批量基金申购执行前复核">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Purchase Preflight</span>
          <h3><ShieldCheck size={18} aria-hidden="true" />申购执行前复核</h3>
          <small>
            {preflight?.snapshot
              ? `修订 ${preflight.snapshot.revision} · ${timeText(preflight.snapshot.created_at)}`
              : `绑定分配快照 ${allocation.snapshot.id}`}
          </small>
        </div>
        <div className="agent-preflight-head-actions">
          <span className={`agent-status ${statusTone}`}>{statusLabel}</span>
          <button type="button" className="ghost" onClick={() => setFormOpen((value) => !value)} disabled={reviewing}>
            {reviewing ? <RefreshCw size={14} className="spin-icon" aria-hidden="true" /> : <Scale size={14} aria-hidden="true" />}
            {preflight ? '重新核对平台事实' : '录入平台申购事实'}
          </button>
        </div>
      </div>

      {preflight && (
        <>
          <div className="agent-preflight-metrics">
            <div><span>拟申购总额</span><b>{money(cashflow.proposed_order_total_yuan)}</b></div>
            <div><span>平台确认费用</span><b>{money(cashflow.confirmed_entry_fee_total_yuan)}</b></div>
            <div><span>预计净投入</span><b>{money(cashflow.projected_net_asset_total_yuan)}</b></div>
            <div><span>分配内保留现金</span><b>{money(cashflow.allocated_cash_retained_yuan)}</b></div>
          </div>

          {(preflight.gates || []).length > 0 && (
            <div className="agent-preflight-gates">
              {preflight.gates.map((gate) => (
                <span key={gate.code} className={gate.status} title={gate.detail}>
                  {gate.status === 'pass' ? <CheckCircle2 size={13} aria-hidden="true" /> : <CircleAlert size={13} aria-hidden="true" />}
                  {gate.label}
                </span>
              ))}
            </div>
          )}

          {(preflight.blockers || []).length > 0 && (
            <div className="agent-preflight-blockers">
              {preflight.blockers.map((item, index) => <p key={`${item}-${index}`}><CircleAlert size={14} aria-hidden="true" />{item}</p>)}
            </div>
          )}

          {(preflight.quotes || []).length > 0 && (
            <div className="agent-preflight-result-table" role="table" aria-label="逐只基金申购复核结果">
              <div className="agent-preflight-result-row heading" role="row">
                <span>基金 / 平台</span><span>拟申购 / 费用</span><span>状态 / 限额</span><span>确认日 / 报价有效期</span><span>门禁</span>
              </div>
              {preflight.quotes.map((item) => (
                <div className={`agent-preflight-result-row ${item.ready ? '' : 'blocked'}`} role="row" key={item.code}>
                  <span><b>{item.code} {item.name}</b><small>{item.platform_name || '-'}</small></span>
                  <span><b>{money(item.order_amount_yuan)}</b><small>费用 {money(item.entry_fee_yuan)}</small></span>
                  <span><b>{PURCHASE_STATUS[item.purchase_status] || item.purchase_status}</b><small>限额 {money(item.purchase_limit_yuan)}</small></span>
                  <span><b>{item.expected_confirmation_date || '-'}</b><small>至 {timeText(item.quote_expires_at)}</small></span>
                  <span>{item.ready ? <em className="agent-status complete">通过</em> : <em className="agent-status failed">阻断</em>}</span>
                  {(item.reasons || []).length > 0 && <p>{item.reasons.join('；')}</p>}
                </div>
              ))}
            </div>
          )}

          <div className="agent-preflight-integrity">
            <ShieldCheck size={15} aria-hidden="true" />
            <span>
              {preflight.snapshot?.integrity_verified && preflight.snapshot?.audit_chain_verified
                ? `内容与审计链已验证 · ${preflight.snapshot.audit_event_count} 个不可变修订`
                : '完整性未通过'}
            </span>
            <span>分配 {bindings.allocation_current === false ? '已变化' : '当前'}</span>
            <span>持仓 {bindings.holdings_current === false ? '已变化' : '当前'}</span>
            <span>IPS {bindings.profile_current === false ? '已变化' : '当前'}</span>
          </div>
        </>
      )}

      {formOpen && (
        <form className="agent-preflight-form" onSubmit={submitReview}>
          <div className="agent-preflight-form-head">
            <div><b>销售平台本次申购页</b><small>逐只填写，不沿用上一版报价</small></div>
            <span>{quotes.length} 只基金 · 人民币</span>
          </div>
          <div className="agent-preflight-input-table">
            {quotes.map((item) => (
              <fieldset key={item.code}>
                <legend>{item.code} {item.name}</legend>
                <label><span>销售平台</span><input value={item.platform_name} maxLength={80} placeholder="平台全称" onChange={(event) => updateQuote(item.code, 'platform_name', event.target.value)} /></label>
                <label><span>报价时间</span><input type="datetime-local" value={item.quoted_at} onChange={(event) => updateQuote(item.code, 'quoted_at', event.target.value)} /></label>
                <label><span>拟申购金额</span><input type="number" min="0.01" step="0.01" value={item.order_amount_yuan} onChange={(event) => updateQuote(item.code, 'order_amount_yuan', event.target.value)} /></label>
                <label><span>实际申购费</span><input type="number" min="0" step="0.01" value={item.entry_fee_yuan} placeholder="以确认页为准" onChange={(event) => updateQuote(item.code, 'entry_fee_yuan', event.target.value)} /></label>
                <label><span>当前申购状态</span><select value={item.purchase_status} onChange={(event) => updateQuote(item.code, 'purchase_status', event.target.value)}><option value="">请选择</option><option value="available">可申购</option><option value="limited">限额申购</option><option value="unavailable">暂停申购</option><option value="unknown">平台未明确</option></select></label>
                <label><span>单次限额</span><input type="number" min="0.01" step="0.01" value={item.purchase_limit_yuan} placeholder={item.purchase_status === 'limited' ? '必填' : '无限额可留空'} onChange={(event) => updateQuote(item.code, 'purchase_limit_yuan', event.target.value)} /></label>
                <label><span>预计确认日期</span><input type="date" value={item.expected_confirmation_date} onChange={(event) => updateQuote(item.code, 'expected_confirmation_date', event.target.value)} /></label>
              </fieldset>
            ))}
          </div>
          <label className="agent-preflight-ack">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
            <span>我确认以上金额、费用、限购状态和确认日期逐只来自销售平台本次申购页</span>
          </label>
          {formError && <p className="agent-preflight-form-error"><CircleAlert size={13} aria-hidden="true" />{formError}</p>}
          <div className="agent-preflight-form-actions">
            <button type="button" className="ghost" onClick={() => setFormOpen(false)} disabled={reviewing}>取消</button>
            <button type="submit" disabled={reviewing}>
              {reviewing ? <RefreshCw size={14} className="spin-icon" aria-hidden="true" /> : <ShieldCheck size={14} aria-hidden="true" />}
              {reviewing ? '正在读取真实披露' : '生成执行前复核'}
            </button>
          </div>
        </form>
      )}
      <p className="agent-batch-policy">系统不读取销售账户余额、不自动提交订单；平台报价超过 24 小时，或持仓、IPS、分配变化后，复核会自动失效。</p>
    </section>
  )
}

function Distribution({ label, items }) {
  const values = items || []
  return (
    <div className="agent-batch-distribution">
      <span>{label}</span>
      <div>
        {values.length > 0
          ? values.slice(0, 4).map((item) => <b key={item.key}>{item.key} <em>{item.count}</em></b>)
          : <b>等待结果</b>}
      </div>
    </div>
  )
}

export default function AgentBatchView({
  batch,
  loading = false,
  allocating = false,
  reviewingPurchase = false,
  selectedRunId = '',
  onRefresh,
  onCancel,
  onSelectRun,
  onCreateAllocation,
  onReviewPurchase,
}) {
  if (!batch) return null
  const [statusLabel, statusTone] = statusMeta(batch.status)
  const progress = batch.progress || {}
  const summary = batch.summary || {}
  const overlap = batch.holding_overlap || {}
  const active = !TERMINAL.has(batch.status)

  return (
    <section className="agent-batch-view" aria-label="批量基金研究结果" aria-live="polite">
      <div className="agent-batch-head">
        <div>
          <span className="eyebrow">Multi-fund Research Batch</span>
          <h3><Layers3 size={19} aria-hidden="true" />批量基金决策</h3>
          <small>Batch ID: {batch.id} · {timeText(batch.created_at)}</small>
        </div>
        <div className="agent-batch-head-actions">
          <span className={`agent-status ${statusTone}`}>{statusLabel}</span>
          <button className="ghost" type="button" onClick={onRefresh} disabled={loading} title="刷新批次状态">
            <RefreshCw size={15} className={loading ? 'spin-icon' : ''} aria-hidden="true" />刷新
          </button>
          {active && (
            <button className="ghost" type="button" onClick={onCancel} title="取消批次中尚未结束的任务">
              <Square size={14} aria-hidden="true" />取消批次
            </button>
          )}
        </div>
      </div>

      <div className="agent-batch-progress">
        <div><span style={{ width: `${Math.max(active ? 6 : 0, progress.percent || 0)}%` }} /></div>
        <p>
          已结束 <b>{progress.terminal || 0}/{progress.total || 0}</b>
          <span>完整 {progress.completed || 0}</span>
          <span>部分 {progress.partial || 0}</span>
          <span>失败 {progress.failed || 0}</span>
        </p>
      </div>

      <div className="agent-batch-summary-strip">
        <Distribution label="市场分布" items={summary.markets} />
        <Distribution label="风险分布" items={summary.risk_bands} />
        <Distribution label="动作分布" items={(summary.actions || []).map((item) => ({ ...item, key: ACTIONS[item.key] || item.key }))} />
        <div className="agent-batch-coverage">
          <span>证据覆盖</span>
          <b><Database size={13} aria-hidden="true" />市场情报 {summary.market_intelligence_available || 0}/{progress.total || 0}</b>
          <b><ShieldCheck size={13} aria-hidden="true" />模型研判 {summary.model_available || 0}/{progress.total || 0}</b>
        </div>
      </div>

      {(summary.warnings || []).length > 0 && (
        <div className="agent-batch-warnings">
          {summary.warnings.map((warning) => <p key={warning}><CircleAlert size={14} aria-hidden="true" />{warning}</p>)}
        </div>
      )}

      <div className="agent-batch-table" role="table" aria-label="批量基金逐只结果">
        <div className="agent-batch-row heading" role="row">
          <span>基金</span><span>状态</span><span>市场 / 风险</span><span>研究动作</span><span>近 1 年 / 回撤</span><span>证据</span><span />
        </div>
        {(batch.items || []).map((item) => {
          const [label, tone] = statusMeta(item.status)
          const selected = item.run_id === selectedRunId
          const coverage = item.coverage || {}
          const decision = item.decision || {}
          return (
            <div className={`agent-batch-row ${selected ? 'selected' : ''}`} role="row" key={item.run_id}>
              <span className="agent-batch-fund">
                <b>{item.code} {item.fund?.name || '等待基金信息'}</b>
                <small>数据截至 {item.fund?.as_of || '-'}</small>
              </span>
              <span><em className={`agent-status ${tone}`}>{label}</em></span>
              <span className="agent-batch-market">
                <b>{item.market?.label || '-'}</b>
                <small>{decision.risk_band || '等待风险分析'}</small>
              </span>
              <span className="agent-batch-action">
                <b>{ACTIONS[decision.action] || decision.action || '-'}</b>
                <small>{decision.timing_label || decision.role || '-'}</small>
                {item.portfolio_allocation?.allocated_amount_yuan != null && (
                  <small className="allocated">组合分配 {money(item.portfolio_allocation.allocated_amount_yuan)}</small>
                )}
              </span>
              <span className="agent-batch-metrics">
                <b>{pct(item.metrics?.return_1y)}</b>
                <small>当前回撤 {pct(item.metrics?.current_drawdown)}</small>
              </span>
              <span className="agent-batch-evidence-state">
                {coverage.market_intelligence === 'available' || coverage.market_intelligence === 'partial'
                  ? <CheckCircle2 size={14} aria-label="市场情报可用" />
                  : <Clock3 size={14} aria-label="等待市场情报" />}
                <small>新闻 {coverage.news_count || 0} · AI {coverage.model === 'available' ? '可用' : '未形成'}</small>
              </span>
              <span>
                <button className="ghost icon-text" type="button" onClick={() => onSelectRun(item.run_id)}>
                  <ArrowUpRight size={14} aria-hidden="true" />详情
                </button>
              </span>
              {item.error_message && <p className="agent-batch-row-error"><CircleAlert size={13} aria-hidden="true" />{item.error_message}</p>}
            </div>
          )
        })}
      </div>

      <PortfolioAllocation
        batch={batch}
        active={active}
        allocating={allocating}
        onCreate={onCreateAllocation}
      />

      <BatchPurchasePreflight
        batch={batch}
        reviewing={reviewingPurchase}
        onReview={onReviewPurchase}
      />

      <section className="agent-batch-overlap" aria-label="批次持仓重合分析">
        <div className="agent-section-head">
          <div>
            <span className="eyebrow">Disclosed Holding Overlap</span>
            <h3>披露持仓重合下界</h3>
          </div>
          <span>覆盖 {overlap.covered_fund_count || 0}/{overlap.total_fund_count || 0} 只</span>
        </div>
        {(overlap.pairs || []).length > 0 ? (
          <div className="agent-batch-overlap-list">
            {overlap.pairs.map((pair) => (
              <div key={`${pair.left_code}-${pair.right_code}`}>
                <b>{pair.left_code} ↔ {pair.right_code}</b>
                <strong>{pct(pair.overlap_lower_bound_pct)}</strong>
                <span>{(pair.shared_holdings || []).slice(0, 4).map((holding) => holding.name).join('、')}</span>
                <small>共同披露持仓 {pair.shared_holding_count} 只</small>
              </div>
            ))}
          </div>
        ) : (
          <p className="agent-batch-overlap-empty">
            {active ? '等待至少两只基金完成持仓情报。' : '本批次已覆盖的前 N 大持仓中没有发现共同标的，或持仓数据不足。'}
          </p>
        )}
        <p className="agent-batch-policy">{overlap.policy || '只使用真实披露持仓，不推断缺失部分。'}</p>
      </section>

      <p className="agent-batch-policy">{batch.policy}</p>
    </section>
  )
}
