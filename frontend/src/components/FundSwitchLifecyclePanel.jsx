import { useState } from 'react'
import {
  CircleAlert,
  CircleCheck,
  ReceiptText,
  RefreshCw,
  Save,
  Scale,
  ShieldAlert,
} from 'lucide-react'

export const TERMINAL_LIFECYCLE_STATUSES = new Set([
  'completed_attribution_available',
  'completed_attribution_blocked',
  'integrity_failed',
])

const EMPTY_SETTLEMENT = {
  transactionId: '',
  submittedAt: '',
  settledOn: '',
  actualReceived: '',
  varianceAcknowledged: false,
}

const EMPTY_PURCHASE_QUOTE = {
  platformName: '',
  quotedAt: '',
  orderAmount: '',
  entryFee: '',
  confirmationDate: '',
  available: false,
  acknowledged: false,
}

const EMPTY_PURCHASE_RECORD = {
  transactionId: '',
  submittedAt: '',
  varianceAcknowledged: false,
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`
}

function pct(value, signed = false) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function tone(value) {
  if (Number(value) > 0) return 'positive'
  if (Number(value) < 0) return 'negative'
  return 'neutral'
}

function displayTime(value) {
  if (!value || Number.isNaN(Date.parse(value))) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function isoTime(value, message) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) throw new Error(message)
  return parsed.toISOString()
}

function lifecycleStatusLabel(status) {
  return {
    settled_purchase_requote_required: '赎回已到账，等待申购重报',
    purchase_requote_blocked: '到账后申购门禁未通过',
    purchase_requote_expired: '到账后申购报价已过期',
    purchase_requote_superseded: '投资政策或持仓已变化',
    ready_for_manual_purchase_review: '可由用户人工复核申购',
    purchase_recorded_reconciliation_pending: '申购已记录，等待持仓对账',
    completed_attribution_pending: '持仓已对账，等待历史归因',
    completed_attribution_available: '替换历史归因可用',
    completed_attribution_blocked: '历史归因证据不完整',
    integrity_failed: '替换批次完整性失败',
  }[status] || '尚未启动替换批次'
}

function lifecycleTone(status) {
  if (['ready_for_manual_purchase_review', 'completed_attribution_available'].includes(status)) return 'ready'
  if (['purchase_requote_expired', 'purchase_requote_superseded', 'completed_attribution_pending'].includes(status)) return 'stale'
  if (status && !['not_started', 'terminal', 'settled_purchase_requote_required', 'purchase_recorded_reconciliation_pending'].includes(status)) return 'blocked'
  return 'pending'
}

function transactionLabel(item) {
  if (!item) return '-'
  const shares = Number(item.shares || 0).toLocaleString('zh-CN', { maximumFractionDigits: 6 })
  return `${item.trade_date || '-'} · ${shares} 份 · ${money(item.cash_amount_yuan)}`
}

function GateIcon({ status }) {
  if (status === 'pass') return <CircleCheck size={14} aria-hidden="true" />
  if (status === 'fail' || status === 'block') return <CircleAlert size={14} aria-hidden="true" />
  return <ShieldAlert size={14} aria-hidden="true" />
}

function SettlementFacts({ settlement, verified }) {
  if (!settlement) return null
  return (
    <dl className="peer-lifecycle-facts">
      <div><dt>赎回确认日</dt><dd>{settlement.trade_date || '-'}</dd></div>
      <div><dt>实际到账日</dt><dd>{settlement.settled_on || '-'}</dd></div>
      <div><dt>实际到账</dt><dd>{money(settlement.actual_received_yuan)}</dd></div>
      <div><dt>实际赎回费</dt><dd>{money(settlement.actual_fee_yuan)}</dd></div>
      <div><dt>报价差额</dt><dd className={tone(settlement.net_variance_yuan)}>{money(settlement.net_variance_yuan)}</dd></div>
      <div><dt>审计链</dt><dd>{verified ? '通过' : '失败'}</dd></div>
    </dl>
  )
}

function PurchaseQuoteFacts({ quote }) {
  if (!quote) return null
  return (
    <dl className="peer-lifecycle-facts">
      <div><dt>到账现金</dt><dd>{money(quote.available_settled_cash_yuan)}</dd></div>
      <div><dt>拟申购总额</dt><dd>{money(quote.order_amount_yuan)}</dd></div>
      <div><dt>申购费用</dt><dd>{money(quote.entry_fee_yuan)}</dd></div>
      <div><dt>留存现金</dt><dd>{money(quote.residual_cash_after_order_yuan)}</dd></div>
      <div><dt>预计确认</dt><dd>{quote.expected_confirmation_date || '-'}</dd></div>
      <div><dt>报价有效至</dt><dd>{displayTime(quote.quote_expires_at)}</dd></div>
    </dl>
  )
}

function PurchaseFacts({ purchase }) {
  if (!purchase) return null
  return (
    <dl className="peer-lifecycle-facts">
      <div><dt>申购确认日</dt><dd>{purchase.confirmation_date || '-'}</dd></div>
      <div><dt>确认份额</dt><dd>{Number(purchase.shares || 0).toLocaleString('zh-CN', { maximumFractionDigits: 6 })}</dd></div>
      <div><dt>实际使用现金</dt><dd>{money(purchase.actual_cash_used_yuan)}</dd></div>
      <div><dt>实际申购费</dt><dd>{money(purchase.actual_fee_yuan)}</dd></div>
      <div><dt>剩余现金</dt><dd>{money(purchase.residual_cash_yuan)}</dd></div>
      <div><dt>订单差额</dt><dd className={tone(purchase.order_variance_yuan)}>{money(purchase.order_variance_yuan)}</dd></div>
    </dl>
  )
}

function AttributionFacts({ attribution }) {
  const metrics = attribution?.metrics || {}
  if (attribution?.status !== 'available') return null
  return (
    <dl className="peer-lifecycle-facts">
      <div><dt>归因截止</dt><dd>{metrics.as_of || '-'}</dd></div>
      <div><dt>替换路径价值</dt><dd>{money(metrics.actual_switch_path_value_yuan)}</dd></div>
      <div><dt>继续持有反事实</dt><dd>{money(metrics.no_switch_counterfactual_value_yuan)}</dd></div>
      <div><dt>相对增量</dt><dd className={tone(metrics.incremental_value_vs_hold_yuan)}>{money(metrics.incremental_value_vs_hold_yuan)}</dd></div>
      <div><dt>相对增量回报</dt><dd className={tone(metrics.incremental_return_vs_hold_pct)}>{pct(metrics.incremental_return_vs_hold_pct, true)}</dd></div>
      <div><dt>实际总费用</dt><dd>{money(metrics.total_switch_fees_yuan)}</dd></div>
    </dl>
  )
}

export default function FundSwitchLifecyclePanel({
  item,
  onConfirmSettlement,
  onConfirmPurchaseRequote,
  onRecordPurchase,
  onReconcile,
  onRefreshAttribution,
}) {
  const context = item.switch_lifecycle || {}
  const switchCase = context.case || null
  const status = switchCase?.status || context.status
  const redemptionOptions = context.eligible_redemption_transactions || []
  const purchaseOptions = switchCase?.eligible_purchase_transactions || []
  const quote = switchCase?.purchase_quote || null
  const purchase = switchCase?.purchase || null
  const preview = switchCase?.reconciliation_preview || null
  const attribution = switchCase?.attribution || null
  const canStartCase = Boolean(context.execution_review_ready && (!switchCase || context.can_start_new))
  const [settlementForm, setSettlementForm] = useState(EMPTY_SETTLEMENT)
  const [quoteForm, setQuoteForm] = useState(EMPTY_PURCHASE_QUOTE)
  const [purchaseForm, setPurchaseForm] = useState(EMPTY_PURCHASE_RECORD)
  const [saving, setSaving] = useState('')
  const [error, setError] = useState('')

  function selectSettlementTransaction(value) {
    const selected = redemptionOptions.find((row) => String(row.id) === value)
    setSettlementForm((current) => ({
      ...current,
      transactionId: value,
      settledOn: current.settledOn || selected?.trade_date || '',
      actualReceived: selected?.cash_amount_yuan == null ? current.actualReceived : String(selected.cash_amount_yuan),
    }))
  }

  async function submitSettlement(event) {
    event.preventDefault()
    setSaving('settlement')
    setError('')
    try {
      await onConfirmSettlement(item.code, {
        expected_execution_review_id: context.execution_review_id,
        expected_execution_review_hash: context.execution_review_hash,
        redemption_transaction_id: Number(settlementForm.transactionId),
        redemption_submitted_at: isoTime(settlementForm.submittedAt, '请填写有效的赎回提交时间'),
        settled_on: settlementForm.settledOn,
        actual_received_yuan: Number(settlementForm.actualReceived),
        acknowledged_quote_variance: settlementForm.varianceAcknowledged,
      })
      setSettlementForm(EMPTY_SETTLEMENT)
    } catch (requestError) {
      setError(requestError?.message || '赎回到账确认失败')
    } finally {
      setSaving('')
    }
  }

  async function submitPurchaseQuote(event) {
    event.preventDefault()
    setSaving('quote')
    setError('')
    try {
      await onConfirmPurchaseRequote(item.code, switchCase.case_id, {
        platform_name: quoteForm.platformName.trim(),
        quoted_at: isoTime(quoteForm.quotedAt, '请填写有效的到账后申购报价时间'),
        candidate_order_amount_yuan: Number(quoteForm.orderAmount),
        candidate_entry_fee_yuan: Number(quoteForm.entryFee),
        expected_confirmation_date: quoteForm.confirmationDate,
        candidate_purchase_available: quoteForm.available,
        acknowledged_platform_quote: quoteForm.acknowledged,
      })
      setQuoteForm(EMPTY_PURCHASE_QUOTE)
    } catch (requestError) {
      setError(requestError?.message || '到账后申购报价复核失败')
    } finally {
      setSaving('')
    }
  }

  async function submitPurchase(event) {
    event.preventDefault()
    setSaving('purchase')
    setError('')
    try {
      const quoteEvent = switchCase.events?.filter((row) => row.event_type === 'purchase_requoted').at(-1)
      await onRecordPurchase(item.code, switchCase.case_id, {
        expected_purchase_quote_event_id: quoteEvent?.id,
        expected_purchase_quote_event_hash: quoteEvent?.event_hash,
        purchase_transaction_id: Number(purchaseForm.transactionId),
        purchase_submitted_at: isoTime(purchaseForm.submittedAt, '请填写有效的申购提交时间'),
        acknowledged_order_variance: purchaseForm.varianceAcknowledged,
      })
      setPurchaseForm(EMPTY_PURCHASE_RECORD)
    } catch (requestError) {
      setError(requestError?.message || '实际申购记录失败')
    } finally {
      setSaving('')
    }
  }

  async function runAction(action, callback) {
    setSaving(action)
    setError('')
    try {
      await callback(item.code, switchCase.case_id)
    } catch (requestError) {
      setError(requestError?.message || '替换批次更新失败')
    } finally {
      setSaving('')
    }
  }

  if (context.status === 'unavailable') {
    return <div className="peer-switch-lifecycle blocked"><small>替换批次读取失败：{context.error || '后端未返回可审计状态'}</small></div>
  }

  return (
    <div className={`peer-switch-lifecycle ${lifecycleTone(status)}`}>
      <div className="peer-lifecycle-head">
        <div><span>真实替换批次</span><b>{lifecycleStatusLabel(status)}</b></div>
        <em>{switchCase ? `第 ${switchCase.revision || 1} 个事件` : '未开始'}</em>
      </div>

      {canStartCase && (
        <form className="peer-lifecycle-form" onSubmit={submitSettlement}>
          <label className="full-width">
            <span>已确认赎回流水</span>
            <select value={settlementForm.transactionId} onChange={(event) => selectSettlementTransaction(event.target.value)} required>
              <option value="">请选择已录入的真实卖出流水</option>
              {redemptionOptions.map((row) => <option key={row.id} value={row.id}>{transactionLabel(row)}</option>)}
            </select>
          </label>
          <label><span>赎回提交时间</span><input type="datetime-local" value={settlementForm.submittedAt} onChange={(event) => setSettlementForm((current) => ({ ...current, submittedAt: event.target.value }))} required /></label>
          <label><span>实际到账日期</span><input type="date" value={settlementForm.settledOn} onChange={(event) => setSettlementForm((current) => ({ ...current, settledOn: event.target.value }))} required /></label>
          <label><span>实际到账金额</span><input type="number" min="0.01" step="0.01" value={settlementForm.actualReceived} onChange={(event) => setSettlementForm((current) => ({ ...current, actualReceived: event.target.value }))} required /></label>
          <label className="peer-quote-check full-width"><input type="checkbox" checked={settlementForm.varianceAcknowledged} onChange={(event) => setSettlementForm((current) => ({ ...current, varianceAcknowledged: event.target.checked }))} /><span>若实际成交明显偏离执行前报价，我已核对成交净值、费用与到账金额</span></label>
          <div className="peer-lifecycle-submit full-width"><small>只绑定已经发生的赎回和到账，不会自动申购。</small><button type="submit" className="primary compact" disabled={saving === 'settlement' || !redemptionOptions.length}><ReceiptText size={14} />{saving === 'settlement' ? '核对中' : '确认真实到账'}</button></div>
        </form>
      )}
      {!switchCase && !context.execution_review_ready && <small>执行前审查尚未通过，不能启动真实替换批次。</small>}
      {switchCase && TERMINAL_LIFECYCLE_STATUSES.has(status) && !context.can_start_new && <small>如需开始新一轮替换，必须重新确认平台报价并生成新的执行前审查；旧审查不能复用。</small>}
      {canStartCase && !redemptionOptions.length && <small>先在交易与复盘中录入覆盖全部确认份额的真实赎回流水，再刷新候选。</small>}

      <SettlementFacts settlement={switchCase?.settlement} verified={switchCase?.integrity?.verified} />

      {switchCase && !purchase && ['settled_purchase_requote_required', 'purchase_requote_blocked', 'purchase_requote_expired', 'purchase_requote_superseded'].includes(status) && (
        <form className="peer-lifecycle-form" onSubmit={submitPurchaseQuote}>
          <label><span>销售平台</span><input value={quoteForm.platformName} onChange={(event) => setQuoteForm((current) => ({ ...current, platformName: event.target.value }))} minLength={2} maxLength={80} required /></label>
          <label><span>到账后报价时间</span><input type="datetime-local" value={quoteForm.quotedAt} onChange={(event) => setQuoteForm((current) => ({ ...current, quotedAt: event.target.value }))} required /></label>
          <label><span>拟申购总额</span><input type="number" min="0.01" step="0.01" value={quoteForm.orderAmount} onChange={(event) => setQuoteForm((current) => ({ ...current, orderAmount: event.target.value }))} required /></label>
          <label><span>申购费用</span><input type="number" min="0" step="0.01" value={quoteForm.entryFee} onChange={(event) => setQuoteForm((current) => ({ ...current, entryFee: event.target.value }))} required /></label>
          <label><span>预计确认日期</span><input type="date" value={quoteForm.confirmationDate} onChange={(event) => setQuoteForm((current) => ({ ...current, confirmationDate: event.target.value }))} required /></label>
          <label className="peer-quote-check full-width"><input type="checkbox" checked={quoteForm.available} onChange={(event) => setQuoteForm((current) => ({ ...current, available: event.target.checked }))} /><span>候选基金当前真实可申购</span></label>
          <label className="peer-quote-check full-width"><input type="checkbox" checked={quoteForm.acknowledged} onChange={(event) => setQuoteForm((current) => ({ ...current, acknowledged: event.target.checked }))} required /><span>金额、费用、限额和确认日期来自赎回到账后的销售平台页面</span></label>
          <div className="peer-lifecycle-submit full-width"><small>系统会重新核验市场、汇率、单品仓位和组合穿透上限。</small><button type="submit" className="primary compact" disabled={saving === 'quote' || !quoteForm.acknowledged}><RefreshCw size={14} />{saving === 'quote' ? '核验中' : quote ? '更新到账后报价' : '核验到账后报价'}</button></div>
        </form>
      )}

      <PurchaseQuoteFacts quote={quote} />
      {quote && <div className="peer-execution-gates">{(switchCase.gates || []).map((gate) => <div className={gate.status} key={gate.code}><GateIcon status={gate.status} /><span><b>{gate.label}</b><small>{gate.detail}</small></span></div>)}</div>}

      {switchCase?.decision_gate?.manual_purchase_review_ready && !purchase && (
        <form className="peer-lifecycle-form" onSubmit={submitPurchase}>
          <label className="full-width"><span>已确认申购流水</span><select value={purchaseForm.transactionId} onChange={(event) => setPurchaseForm((current) => ({ ...current, transactionId: event.target.value }))} required><option value="">请选择已录入的真实买入流水</option>{purchaseOptions.map((row) => <option key={row.id} value={row.id}>{transactionLabel(row)}</option>)}</select></label>
          <label><span>人工提交申购时间</span><input type="datetime-local" value={purchaseForm.submittedAt} onChange={(event) => setPurchaseForm((current) => ({ ...current, submittedAt: event.target.value }))} required /></label>
          <label className="peer-quote-check full-width"><input type="checkbox" checked={purchaseForm.varianceAcknowledged} onChange={(event) => setPurchaseForm((current) => ({ ...current, varianceAcknowledged: event.target.checked }))} /><span>若实际成交金额偏离到账后报价，我已核对份额、确认净值和费用</span></label>
          <div className="peer-lifecycle-submit full-width"><small>这里只记录用户已经完成的交易，不提供下单入口。</small><button type="submit" className="primary compact" disabled={saving === 'purchase' || !purchaseOptions.length}><Save size={14} />{saving === 'purchase' ? '绑定中' : '记录已发生申购'}</button></div>
        </form>
      )}
      {switchCase?.decision_gate?.manual_purchase_review_ready && !purchase && !purchaseOptions.length && <small>先在交易与复盘中录入候选基金的真实买入流水，再刷新批次。</small>}

      <PurchaseFacts purchase={purchase} />

      {status === 'purchase_recorded_reconciliation_pending' && preview && (
        <div className="peer-lifecycle-reconcile">
          <div><span>原基金账本剩余</span><b>{Number(preview.selected?.ledger_open_shares || 0).toLocaleString('zh-CN', { maximumFractionDigits: 6 })} 份</b></div>
          <div><span>候选账本 / 持仓</span><b>{Number(preview.candidate?.ledger_open_shares || 0).toLocaleString('zh-CN', { maximumFractionDigits: 6 })} / {preview.candidate?.confirmed_shares == null ? '-' : Number(preview.candidate.confirmed_shares).toLocaleString('zh-CN', { maximumFractionDigits: 6 })}</b></div>
          {preview.reasons?.length > 0 && <p>{preview.reasons.join('；')}</p>}
          <button type="button" className="primary compact" disabled={!preview.ready || saving === 'reconcile'} onClick={() => runAction('reconcile', onReconcile)}><Scale size={14} />{saving === 'reconcile' ? '对账中' : '确认持仓已对账'}</button>
        </div>
      )}

      {switchCase?.decision_gate?.holdings_reconciled && (
        <div className="peer-lifecycle-attribution">
          <AttributionFacts attribution={attribution} />
          {attribution?.status !== 'available' && <p>{attribution?.reasons?.join('；') || switchCase.attribution_blockers?.join('；') || '尚未按两只基金同日确认净值计算历史归因。'}</p>}
          <button type="button" className="ghost compact" disabled={saving === 'attribution' || !switchCase.decision_gate?.attribution_refresh_ready} onClick={() => runAction('attribution', onRefreshAttribution)} title={switchCase.decision_gate?.attribution_refresh_ready ? '读取两只基金真实确认净值并更新历史归因' : '对账后的原基金或候选基金账本已变化，不能继续按完整持有假设刷新'}><RefreshCw size={14} />{saving === 'attribution' ? '读取真实净值中' : attribution ? '刷新历史归因' : '计算历史归因'}</button>
          <small>归因只比较已发生替换与继续持有原基金的历史结果，不预测未来收益。</small>
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {switchCase && <small>批次 {switchCase.selected_code} → {switchCase.candidate_code} · 所有阶段均禁止自动赎回和自动申购。</small>}
    </div>
  )
}
