import { useEffect, useMemo, useState } from 'react'
import {
  ArrowUpRight,
  ChartNoAxesCombined,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Layers3,
  ReceiptText,
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

const EXECUTION_STATUS = {
  awaiting_execution_record: ['等待真实成交回填', 'queued'],
  purchases_recorded_reconciliation_pending: ['真实成交已绑定', 'partial'],
  completed_no_purchase: ['本批次未成交', 'complete'],
  completed_reconciled: ['真实持仓已对账', 'complete'],
  completed_reconciliation_stale: ['对账后事实已变化', 'partial'],
  integrity_failed: ['执行完整性失败', 'failed'],
}

const NOT_PURCHASED_REASON = {
  platform_unavailable: '平台暂停申购',
  limit_insufficient: '平台限额不足',
  insufficient_cash: '可用资金不足',
  risk_reassessment: '风险复核后取消',
  user_cancelled: '主动取消',
  other: '其他真实原因',
}

const ATTRIBUTION_STATUS = {
  awaiting_reconciliation: ['等待持仓对账', 'queued'],
  ready_for_snapshot: ['可刷新真实收益', 'queued'],
  ledger_reconciliation_required: ['账本需要核对', 'failed'],
  available: ['真实绩效可用', 'complete'],
  partial: ['部分绩效可用', 'partial'],
  unavailable: ['绩效证据不可用', 'failed'],
  stale_refresh_required: ['交易事实已变化', 'partial'],
  integrity_failed: ['绩效审计失败', 'failed'],
}

const RESULT_CLASS = {
  positive: '正收益',
  negative: '负收益',
  flat: '持平',
}

function localDateTime(value = new Date()) {
  const parsed = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  const offset = parsed.getTimezoneOffset() * 60_000
  return new Date(parsed.getTime() - offset).toISOString().slice(0, 16)
}

function quantity(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 8 })
}

function signedMoney(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  const sign = number > 0 ? '+' : number < 0 ? '-' : ''
  return `${sign}¥${Math.abs(number).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
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

function initialExecutionOutcomes(preflight, execution) {
  const saved = execution?.purchase_outcomes || execution?.outcomes || []
  const savedByCode = new Map(saved.map((item) => [item.code, item]))
  return (preflight?.quotes || []).map((quote) => {
    const existing = savedByCode.get(quote.code) || {}
    return {
      code: quote.code,
      name: quote.name,
      resolution: existing.resolution || '',
      transaction_id: existing.transaction?.id ? String(existing.transaction.id) : '',
      purchase_submitted_at: existing.purchase_submitted_at
        ? localDateTime(existing.purchase_submitted_at)
        : '',
      acknowledged_order_variance: Boolean(existing.variance_acknowledged),
      not_purchased_reason: existing.not_purchased_reason || '',
      not_purchased_detail: existing.not_purchased_detail || '',
    }
  })
}

function BatchPurchaseExecution({
  batch,
  recording,
  reconciling,
  onRecord,
  onReconcile,
}) {
  const preflight = batch.purchase_preflight
  const execution = batch.purchase_execution
  const executionKey = execution?.snapshot?.event_hash || preflight?.snapshot?.event_hash || ''
  const initialOutcomes = useMemo(
    () => initialExecutionOutcomes(preflight, execution),
    [executionKey],
  )
  const [formOpen, setFormOpen] = useState(false)
  const [outcomes, setOutcomes] = useState(initialOutcomes)
  const [formError, setFormError] = useState('')

  useEffect(() => {
    setOutcomes(initialOutcomes)
    setFormError('')
    setFormOpen(false)
  }, [executionKey])

  if (!preflight?.snapshot) {
    return (
      <section className="agent-purchase-execution" aria-label="批量基金真实成交与持仓对账">
        <div className="agent-section-head">
          <div><span className="eyebrow">Execution Ledger</span><h3><ReceiptText size={18} aria-hidden="true" />真实成交与持仓对账</h3></div>
        </div>
        <div className="agent-preflight-empty">
          <Clock3 size={17} aria-hidden="true" />
          <div><b>等待申购执行前复核</b><p>成交链只接受已通过复核批次对应的真实买入流水。</p></div>
        </div>
      </section>
    )
  }

  const [statusLabel, statusTone] = EXECUTION_STATUS[execution?.status] || ['等待真实成交回填', 'queued']
  const purchaseSummary = execution?.purchase_summary || execution?.summary || {}
  const savedOutcomes = execution?.purchase_outcomes || execution?.outcomes || []
  const reconciliation = execution?.current_reconciliation
    || execution?.reconciliation_preview
    || execution?.reconciliation
  const canRevise = execution?.status !== 'integrity_failed'
    && execution?.snapshot?.event_type !== 'holdings_reconciled'
  const canReconcile = execution?.snapshot?.event_type === 'purchases_recorded'
    && Number(purchaseSummary.purchased_count) > 0
  const eligible = execution?.eligible_transactions_by_code || {}

  function updateOutcome(code, field, value) {
    setOutcomes((current) => current.map((item) => {
      if (item.code !== code) return item
      const next = { ...item, [field]: value }
      if (field === 'resolution' && value === 'purchased') {
        next.not_purchased_reason = ''
        next.not_purchased_detail = ''
      }
      if (field === 'resolution' && value === 'not_purchased') {
        next.transaction_id = ''
        next.purchase_submitted_at = ''
        next.acknowledged_order_variance = false
      }
      return next
    }))
  }

  async function submitExecution(event) {
    event.preventDefault()
    const invalid = outcomes.find((item) => (
      !item.resolution
      || (item.resolution === 'purchased' && (!item.transaction_id || !item.purchase_submitted_at))
      || (item.resolution === 'not_purchased' && !item.not_purchased_reason)
    ))
    if (invalid) {
      setFormError(`${invalid.code} 的成交结果、真实流水或未申购原因不完整`)
      return
    }
    const normalized = outcomes.map((item) => (
      item.resolution === 'purchased'
        ? {
            code: item.code,
            resolution: 'purchased',
            transaction_id: Number(item.transaction_id),
            purchase_submitted_at: new Date(item.purchase_submitted_at).toISOString(),
            acknowledged_order_variance: item.acknowledged_order_variance,
          }
        : {
            code: item.code,
            resolution: 'not_purchased',
            not_purchased_reason: item.not_purchased_reason,
            not_purchased_detail: item.not_purchased_detail.trim(),
          }
    ))
    setFormError('')
    const succeeded = await onRecord({
      expected_preflight_event_id: preflight.snapshot.id,
      expected_preflight_event_hash: preflight.snapshot.event_hash,
      expected_previous_event_hash: execution?.snapshot?.event_hash || null,
      outcomes: normalized,
    })
    if (succeeded) setFormOpen(false)
  }

  async function reconcileHoldings() {
    if (!canReconcile || !reconciliation?.ready) return
    await onReconcile({
      expected_purchase_event_id: execution.snapshot.id,
      expected_purchase_event_hash: execution.snapshot.event_hash,
      expected_previous_event_hash: execution.snapshot.event_hash,
    })
  }

  return (
    <section className="agent-purchase-execution" aria-label="批量基金真实成交与持仓对账">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Execution Ledger</span>
          <h3><ReceiptText size={18} aria-hidden="true" />真实成交与持仓对账</h3>
          <small>{execution?.snapshot ? `执行链修订 ${execution.snapshot.revision} · ${timeText(execution.snapshot.created_at)}` : `绑定复核 ${preflight.snapshot.id}`}</small>
        </div>
        <div className="agent-preflight-head-actions">
          <span className={`agent-status ${statusTone}`}>{statusLabel}</span>
          {canRevise && (
            <button type="button" className="ghost" onClick={() => setFormOpen((value) => !value)} disabled={recording}>
              {recording ? <RefreshCw size={14} className="spin-icon" aria-hidden="true" /> : <ReceiptText size={14} aria-hidden="true" />}
              {execution?.snapshot ? '修订成交绑定' : '回填真实结果'}
            </button>
          )}
        </div>
      </div>

      {execution?.snapshot && (
        <>
          <div className="agent-execution-metrics">
            <div><span>实际申购</span><b>{purchaseSummary.purchased_count ?? 0} 只</b></div>
            <div><span>未申购</span><b>{purchaseSummary.not_purchased_count ?? 0} 只</b></div>
            <div><span>实际占用资金</span><b>{money(purchaseSummary.actual_cash_total_yuan)}</b></div>
            <div><span>未使用分配</span><b>{money(purchaseSummary.unused_allocated_cash_yuan)}</b></div>
          </div>

          {savedOutcomes.length > 0 && (
            <div className="agent-execution-table" role="table" aria-label="逐只基金真实成交结果">
              <div className="agent-execution-row heading" role="row">
                <span>基金</span><span>真实结果</span><span>成交金额 / 费用</span><span>确认份额 / 日期</span><span>偏差</span>
              </div>
              {savedOutcomes.map((item) => (
                <div className={`agent-execution-row ${item.resolution}`} role="row" key={item.code}>
                  <span><b>{item.code} {item.name}</b><small>分配 {money(item.allocated_amount_yuan)}</small></span>
                  <span><b>{item.resolution === 'purchased' ? `已绑定流水 #${item.transaction?.id}` : '未申购'}</b><small>{item.resolution === 'purchased' ? timeText(item.purchase_submitted_at) : NOT_PURCHASED_REASON[item.not_purchased_reason] || item.not_purchased_reason}</small></span>
                  <span><b>{money(item.actual_cash_amount_yuan)}</b><small>费用 {money(item.actual_fee_yuan)}</small></span>
                  <span><b>{quantity(item.transaction?.shares)}</b><small>{item.transaction?.trade_date || '-'}</small></span>
                  <span><b>{item.material_variance ? '已确认显著偏差' : item.resolution === 'purchased' ? '无显著偏差' : '-'}</b><small>{item.resolution === 'purchased' ? `${money(item.order_variance_yuan)} / ${money(item.fee_variance_yuan)}` : item.not_purchased_detail || '-'}</small></span>
                </div>
              ))}
            </div>
          )}

          {(execution.blockers || []).length > 0 && (
            <div className="agent-preflight-blockers">
              {execution.blockers.map((item, index) => <p key={`${item}-${index}`}><CircleAlert size={14} aria-hidden="true" />{item}</p>)}
            </div>
          )}

          {reconciliation && Number(purchaseSummary.purchased_count) > 0 && (
            <div className="agent-reconciliation-panel">
              <div className="agent-reconciliation-head">
                <div><b>FIFO 份额对账</b><small>{reconciliation.matched_fund_count || 0}/{reconciliation.purchased_fund_count || 0} 只匹配</small></div>
                {canReconcile && (
                  <button type="button" onClick={reconcileHoldings} disabled={!reconciliation.ready || reconciling}>
                    {reconciling ? <RefreshCw size={14} className="spin-icon" aria-hidden="true" /> : <Scale size={14} aria-hidden="true" />}
                    {reconciling ? '正在对账' : '确认当前持仓对账'}
                  </button>
                )}
              </div>
              <div className="agent-reconciliation-table">
                {(reconciliation.items || []).map((item) => (
                  <div className={item.shares_match ? 'matched' : 'mismatch'} key={item.code}>
                    <span><b>{item.code} {item.name}</b><small>{item.fifo_integrity_issue_count ? `${item.fifo_integrity_issue_count} 个账本问题` : 'FIFO 完整'}</small></span>
                    <span><small>当前确认份额</small><b>{quantity(item.confirmed_shares)}</b></span>
                    <span><small>FIFO 未平仓份额</small><b>{quantity(item.fifo_open_shares)}</b></span>
                    <em>{item.shares_match ? '匹配' : '待对账'}</em>
                    {(item.blockers || []).length > 0 && <p>{item.blockers.join('；')}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="agent-preflight-integrity">
            <ShieldCheck size={15} aria-hidden="true" />
            <span>{execution.snapshot?.integrity_verified && execution.snapshot?.audit_chain_verified ? `执行内容与审计链已验证 · ${execution.snapshot.audit_event_count} 个不可变事件` : '执行完整性未通过'}</span>
            <span>绑定流水 {execution.transaction_integrity?.checked_count ?? 0} 笔</span>
          </div>
        </>
      )}

      {!execution?.snapshot && (
        <div className="agent-execution-empty">
          <Database size={17} aria-hidden="true" />
          <div><b>尚未绑定销售平台真实结果</b><p>可用流水只来自当前用户的交易台账，并排除已被其他批次占用的记录。</p></div>
        </div>
      )}

      {formOpen && canRevise && (
        <form className="agent-execution-form" onSubmit={submitExecution}>
          <div className="agent-preflight-form-head">
            <div><b>逐只回填真实结果</b><small>先在交易复盘中录入销售平台确认的买入流水</small></div>
            <span>{outcomes.length} 只基金</span>
          </div>
          <div className="agent-execution-inputs">
            {outcomes.map((item) => {
              const options = eligible[item.code] || []
              return (
                <fieldset key={item.code}>
                  <legend>{item.code} {item.name}</legend>
                  <label><span>实际结果</span><select value={item.resolution} onChange={(event) => updateOutcome(item.code, 'resolution', event.target.value)}><option value="">请选择</option><option value="purchased">已申购并确认流水</option><option value="not_purchased">本次未申购</option></select></label>
                  {item.resolution === 'purchased' && (
                    <>
                      <label className="wide"><span>真实买入流水</span><select value={item.transaction_id} onChange={(event) => updateOutcome(item.code, 'transaction_id', event.target.value)}><option value="">请选择当前用户未占用流水</option>{options.map((option) => <option value={option.id} key={option.id}>#{option.id} · {option.trade_date} · {quantity(option.shares)} 份 · {money(option.cash_amount_yuan)}{option.bound_to_current_batch ? ' · 本批次已绑定' : ''}</option>)}</select><small>{options.length ? `${options.length} 笔可绑定流水` : '没有符合代码、方向和确认日期的可用流水'}</small></label>
                      <label><span>平台提交时间</span><input type="datetime-local" value={item.purchase_submitted_at} onChange={(event) => updateOutcome(item.code, 'purchase_submitted_at', event.target.value)} /></label>
                      <label className="check"><input type="checkbox" checked={item.acknowledged_order_variance} onChange={(event) => updateOutcome(item.code, 'acknowledged_order_variance', event.target.checked)} /><span>实际金额或费用有显著偏差时，我已核对平台成交确认</span></label>
                    </>
                  )}
                  {item.resolution === 'not_purchased' && (
                    <>
                      <label><span>未申购原因</span><select value={item.not_purchased_reason} onChange={(event) => updateOutcome(item.code, 'not_purchased_reason', event.target.value)}><option value="">请选择</option>{Object.entries(NOT_PURCHASED_REASON).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                      <label className="wide"><span>事实备注</span><input value={item.not_purchased_detail} maxLength={200} placeholder="可选，记录平台或人工复核事实" onChange={(event) => updateOutcome(item.code, 'not_purchased_detail', event.target.value)} /></label>
                    </>
                  )}
                </fieldset>
              )
            })}
          </div>
          {formError && <p className="agent-preflight-form-error"><CircleAlert size={13} aria-hidden="true" />{formError}</p>}
          <div className="agent-preflight-form-actions">
            <button type="button" className="ghost" onClick={() => setFormOpen(false)} disabled={recording}>取消</button>
            <button type="submit" disabled={recording}>
              {recording ? <RefreshCw size={14} className="spin-icon" aria-hidden="true" /> : <ShieldCheck size={14} aria-hidden="true" />}
              {recording ? '正在验证真实流水' : '保存不可变成交事件'}
            </button>
          </div>
        </form>
      )}
      <p className="agent-batch-policy">执行链只记录已发生的销售平台事实；系统不连接账户、不自动下单。持仓必须与真实交易流水的 FIFO 未平仓份额一致后才会关闭批次。</p>
    </section>
  )
}

function BatchPurchaseAttribution({ batch, refreshing, onRefresh }) {
  const attribution = batch.purchase_attribution
  if (!attribution) return null
  const [statusLabel, statusTone] = ATTRIBUTION_STATUS[attribution.status]
    || [attribution.status || '等待绩效证据', 'queued']
  const snapshot = attribution.snapshot
  const coverage = attribution.coverage || {}
  const aggregate = attribution.aggregate || {}
  const aggregateMetrics = aggregate.metrics || {}
  const decisionGate = attribution.decision_gate || {}
  const canRefresh = Boolean(
    attribution.refresh_ready
    && attribution.expected_reconciliation_event_id
    && attribution.expected_reconciliation_event_hash,
  )

  function refreshAttribution() {
    if (!canRefresh || refreshing) return
    onRefresh({
      expected_reconciliation_event_id: attribution.expected_reconciliation_event_id,
      expected_reconciliation_event_hash: attribution.expected_reconciliation_event_hash,
      expected_previous_snapshot_hash: snapshot?.event_hash || null,
    })
  }

  return (
    <section className="agent-purchase-attribution" aria-label="批量基金成交后绩效归因">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Post-trade Attribution</span>
          <h3><ChartNoAxesCombined size={18} aria-hidden="true" />成交后绩效归因</h3>
          <small>{snapshot ? `快照修订 ${snapshot.revision} · ${timeText(snapshot.created_at)}` : '只追踪本批次已绑定的真实买入流水'}</small>
        </div>
        <div className="agent-preflight-head-actions">
          <span className={`agent-status ${statusTone}`}>{statusLabel}</span>
          {canRefresh && (
            <button type="button" className="ghost" onClick={refreshAttribution} disabled={refreshing}>
              <RefreshCw size={14} className={refreshing ? 'spin-icon' : ''} aria-hidden="true" />
              {refreshing ? '正在读取真实净值' : snapshot ? '刷新真实收益' : '生成绩效快照'}
            </button>
          )}
        </div>
      </div>

      {!snapshot && (
        <div className="agent-attribution-empty">
          <ChartNoAxesCombined size={18} aria-hidden="true" />
          <div>
            <b>{canRefresh ? '真实成交已经具备归因条件' : '绩效归因尚未通过账本门禁'}</b>
            <p>{canRefresh
              ? '刷新后按具体买入流水计算 FIFO 已实现回款、剩余份额价值、费用和回撤。'
              : (attribution.blockers || []).join('；') || '先完成真实成交与当前持仓 FIFO 对账。'}</p>
          </div>
        </div>
      )}

      {snapshot && (
        <>
          {aggregate.status === 'available' ? (
            <div className="agent-attribution-metrics">
              <div><span>本批实际成本</span><b>{money(aggregateMetrics.original_cost_yuan)}</b></div>
              <div><span>已实现回款</span><b>{money(aggregateMetrics.realized_proceeds_yuan)}</b></div>
              <div><span>剩余份额价值</span><b>{money(aggregateMetrics.current_remaining_value_yuan)}</b></div>
              <div className={Number(aggregateMetrics.total_profit_yuan) >= 0 ? 'positive' : 'negative'}><span>批次实际收益</span><b>{signedMoney(aggregateMetrics.total_profit_yuan)} · {pct(aggregateMetrics.total_return_pct)}</b></div>
            </div>
          ) : (
            <div className="agent-attribution-coverage">
              <span>真实成交覆盖 <b>{coverage.available_fund_count || 0}/{coverage.purchased_fund_count || 0}</b> 只</span>
              <span>数据源失败 <b>{coverage.source_error_count || 0}</b> 项</span>
              <span>不完整基金不进入合计收益</span>
            </div>
          )}

          {(attribution.items || []).length > 0 && (
            <div className="agent-attribution-table" role="table" aria-label="逐只基金真实成交绩效">
              <div className="agent-attribution-row heading" role="row">
                <span>基金 / 观察期</span><span>批次份额</span><span>实现 / 未实现</span><span>总结果</span><span>风险与复盘</span>
              </div>
              {attribution.items.map((item) => {
                const metrics = item.metrics || {}
                const review = item.decision_review || {}
                const resultClass = metrics.result_class || 'unavailable'
                return (
                  <div className={`agent-attribution-row ${item.status} ${resultClass}`} role="row" key={`${item.code}-${item.transaction_id}`}>
                    <span>
                      <b>{item.code} {item.name}</b>
                      <small>{item.as_of ? `截至 ${item.as_of} · ${metrics.observation_days ?? '-'} 天` : '缺少可验证截止日'}</small>
                      {item.sources?.nav_source_url && <a href={item.sources.nav_source_url} target="_blank" rel="noreferrer">净值来源 <ArrowUpRight size={11} aria-hidden="true" /></a>}
                    </span>
                    <span><b>{quantity(item.lot?.remaining_shares)} 份剩余</b><small>{quantity(item.lot?.realized_shares)} 份已卖出 · 流水 #{item.transaction_id}</small></span>
                    <span><b>{signedMoney(metrics.realized_profit_yuan)}</b><small>未实现 {signedMoney(metrics.unrealized_profit_yuan)}</small></span>
                    <span><b>{item.status === 'available' ? signedMoney(metrics.total_profit_yuan) : '-'}</b><small>{item.status === 'available' ? `${pct(metrics.total_return_pct)} · ${RESULT_CLASS[resultClass] || resultClass}` : (item.reasons || []).slice(0, 2).join('；')}</small></span>
                    <span><b>最大回撤 {pct(metrics.max_drawdown_pct)}</b><small>{review.eligible ? '已达到策略复盘窗口' : review.days_until_review != null ? `距离 30 天复盘还差 ${review.days_until_review} 天` : '证据不足，暂不评价策略'}</small></span>
                  </div>
                )
              })}
            </div>
          )}

          {(aggregate.blockers || []).length > 0 && (
            <div className="agent-preflight-blockers">
              {aggregate.blockers.map((item, index) => <p key={`${item}-${index}`}><CircleAlert size={14} aria-hidden="true" />{item}</p>)}
            </div>
          )}
          {(attribution.blockers || []).length > 0 && (
            <div className="agent-preflight-blockers">
              {attribution.blockers.map((item, index) => <p key={`${item}-${index}`}><CircleAlert size={14} aria-hidden="true" />{item}</p>)}
            </div>
          )}

          <div className={`agent-attribution-review ${decisionGate.decision_review_eligible ? 'ready' : 'monitoring'}`}>
            <Scale size={15} aria-hidden="true" />
            <div>
              <b>{decisionGate.decision_review_eligible ? '已进入策略有效性复盘窗口' : '当前仅作成交后监控'}</b>
              <span>{decisionGate.decision_review_eligible
                ? '可以结合原始决策证据复盘收益与风险，但单个批次不能证明策略长期有效。'
                : '至少观察 30 天且全部真实证据完整后，才评价这次批量决策。'}</span>
            </div>
          </div>

          <div className="agent-preflight-integrity">
            <ShieldCheck size={15} aria-hidden="true" />
            <span>{snapshot.integrity_verified && snapshot.audit_chain_verified ? `绩效内容与审计链已验证 · ${snapshot.audit_event_count} 个不可变快照` : '绩效快照完整性未通过'}</span>
            {attribution.current_bindings && <span>成交 {attribution.current_bindings.execution_current ? '当前' : '已变化'} · 账本 {attribution.current_bindings.ledger_current ? '当前' : '已变化'} · 份额 {attribution.current_bindings.holding_shares_current ? '当前' : '已变化'}</span>}
          </div>
        </>
      )}
      <p className="agent-batch-policy">{attribution.policy || '只归因本批次已发生的真实成交；未成交基金不生成收益，历史结果不预测未来。'}</p>
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
  recordingPurchase = false,
  reconcilingPurchase = false,
  refreshingAttribution = false,
  selectedRunId = '',
  onRefresh,
  onCancel,
  onSelectRun,
  onCreateAllocation,
  onReviewPurchase,
  onRecordPurchase,
  onReconcilePurchase,
  onRefreshAttribution,
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

      <BatchPurchaseExecution
        batch={batch}
        recording={recordingPurchase}
        reconciling={reconcilingPurchase}
        onRecord={onRecordPurchase}
        onReconcile={onReconcilePurchase}
      />

      <BatchPurchaseAttribution
        batch={batch}
        refreshing={refreshingAttribution}
        onRefresh={onRefreshAttribution}
      />

      <p className="agent-batch-policy">{batch.policy}</p>
    </section>
  )
}
