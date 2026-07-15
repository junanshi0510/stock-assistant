import { useState } from 'react'
import {
  ArrowRightLeft,
  CircleAlert,
  CircleCheck,
  Clock3,
  FileSearch,
  ReceiptText,
  RefreshCw,
  Save,
  Scale,
  ShieldAlert,
} from 'lucide-react'

const WINDOW_LABELS = {
  '3m': '近 3 个月',
  '6m': '近 6 个月',
  '12m': '近 12 个月',
  latest_3m: '最近 3 个月',
  previous_3m: '此前 3 个月',
}

const REASON_LABELS = {
  aligned_observations_below_minimum: '基金与同类平均没有足够的共同日期观察值。',
  comparable_windows_below_minimum: '共同序列不足以覆盖两个季度和至少两个观察窗口。',
}

function pct(value, signed = false) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function pp(value, signed = true) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)} 个百分点`
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`
}

function months(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(1)} 个月`
}

function tone(value) {
  if (Number(value) > 0) return 'positive'
  if (Number(value) < 0) return 'negative'
  return 'neutral'
}

function diagnosisTone(status) {
  if (status === 'replacement_review') return 'negative'
  if (status === 'underperformance_watch') return 'warning'
  if (status === 'relative_strength') return 'positive'
  return 'mixed'
}

function windowRange(item) {
  if (item.period_basis === 'provider_defined_trailing_period') {
    return `来源阶段口径 · 截至 ${item.end_date || '-'}`
  }
  return item.status === 'available' ? `${item.start_date} 至 ${item.end_date}` : '覆盖不足'
}

function reasonText(data, error) {
  if (error) return error
  const reason = String(data?.reason || '')
  if (REASON_LABELS[reason]) return REASON_LABELS[reason]
  if (reason.startsWith('provider_native_peer_series_unavailable:')) return '真实同类平均序列当前不可用，本轮不生成相对能力结论。'
  if (reason.startsWith('peer_persistence_calculation_failed:')) return '同类持续性计算失败，本轮不生成替代审查结论。'
  return '真实同类可比数据不足，本轮只保留数据缺口。'
}

function GateIcon({ status }) {
  if (status === 'pass') return <CircleAlert size={14} aria-hidden="true" />
  if (status === 'fail') return <CircleCheck size={14} aria-hidden="true" />
  return <ShieldAlert size={14} aria-hidden="true" />
}

function durabilityTone(status) {
  if (status === 'durable_advantage') return 'positive'
  if (status === 'advantage_but_hot') return 'warning'
  if (status === 'recent_leader_only') return 'negative'
  if (status === 'mixed_evidence') return 'mixed'
  return 'unavailable'
}

function dueDiligenceTone(status) {
  if (status === 'distinct_candidate' || status === 'partial_overlap_candidate') return 'positive'
  if (status === 'duplicate_but_cost_edge' || status === 'distinct_but_costlier') return 'warning'
  if (status === 'duplicate_without_cost_edge' || status === 'blocked_by_durability') return 'negative'
  return 'unavailable'
}

function switchCostTone(status) {
  if (status === 'ready_for_platform_quote') return 'positive'
  if (['transaction_lots_missing', 'lot_date_unverified', 'confirmed_shares_missing'].includes(status)) return 'warning'
  return 'negative'
}

function gateValue(check) {
  if (Array.isArray(check.observed)) return check.observed.map((value) => pp(value)).join(' / ')
  if (check.observed != null) return `${pp(check.observed)}${check.threshold != null ? ` · 阈值 ${pp(check.threshold, false)}` : ''}`
  return check.status === 'pending' ? '尚未核验' : '当前不可用'
}

const EMPTY_QUOTE = {
  platformName: '',
  quotedAt: '',
  redemptionFee: '',
  entryFee: '',
  arrivalDate: '',
  candidateAvailable: false,
  platformAcknowledged: false,
  varianceAcknowledged: false,
  note: '',
}

function quoteStatus(latest) {
  if (!latest) return null
  const expiresAt = latest.payload?.platform_quote?.quote_expires_at
  if (latest.status === 'confirmed_current' && expiresAt && Date.parse(expiresAt) < Date.now()) return 'expired'
  return latest.status
}

function quoteStatusLabel(status) {
  if (status === 'confirmed_current') return '成本证据当前有效'
  if (status === 'confirmed_with_blocker') return '报价已记录，申购受限'
  if (status === 'expired') return '平台报价已过期'
  if (status === 'superseded') return '持仓或成本证据已变化'
  if (status === 'integrity_failed') return '审计完整性失败'
  return '尚未确认平台报价'
}

function displayTime(value) {
  if (!value || Number.isNaN(Date.parse(value))) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function SwitchQuotePanel({ item, costReady, onConfirm }) {
  const binding = item.switch_cost_binding || null
  const latest = item.latest_platform_quote || null
  const latestPayload = latest?.payload || {}
  const status = quoteStatus(latest)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_QUOTE)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const eligible = Boolean(costReady && binding?.integrity_verified && onConfirm)

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function submit(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const parsedTime = new Date(form.quotedAt)
      if (Number.isNaN(parsedTime.getTime())) throw new Error('请填写有效的平台报价时间')
      await onConfirm(item.code, {
        review_id: binding.review_id,
        expected_review_payload_sha256: binding.payload_sha256,
        platform_name: form.platformName.trim(),
        quoted_at: parsedTime.toISOString(),
        redemption_fee_yuan: Number(form.redemptionFee),
        candidate_entry_fee_yuan: Number(form.entryFee),
        expected_redemption_arrival_date: form.arrivalDate,
        candidate_purchase_available: form.candidateAvailable,
        acknowledged_platform_quote: form.platformAcknowledged,
        acknowledged_fee_variance: form.varianceAcknowledged,
        note: form.note.trim(),
      })
      setForm(EMPTY_QUOTE)
      setOpen(false)
    } catch (requestError) {
      setError(requestError?.message || '平台报价保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`peer-platform-quote ${String(status || 'not-recorded').replaceAll('_', '-')}`}>
      <div className="peer-platform-quote-head">
        <div>
          <span>销售平台真实报价</span>
          <b>{quoteStatusLabel(status)}</b>
        </div>
        {eligible && (
          <button type="button" className="ghost compact" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
            <ReceiptText size={14} /> {latest ? '更新报价' : '录入报价'}
          </button>
        )}
      </div>

      {latest && (
        <>
          <dl className="peer-platform-quote-facts">
            <div><dt>平台报价总成本</dt><dd>{money(latestPayload.confirmed_cost?.total_switching_cost_yuan)}</dd></div>
            <div><dt>成本率</dt><dd>{pct(latestPayload.confirmed_cost?.total_switching_cost_rate_pct)}</dd></div>
            <div><dt>预计到账</dt><dd>{latestPayload.settlement?.expected_redemption_arrival_date || '-'}</dd></div>
            <div><dt>现金在途</dt><dd>{latestPayload.settlement?.cash_gap_days == null ? '-' : `${latestPayload.settlement.cash_gap_days} 天`}</dd></div>
            <div><dt>历史覆盖期</dt><dd>{months(latestPayload.historical_cost_hurdle?.confirmed_cost_coverage_months)}</dd></div>
            <div><dt>审计链</dt><dd>{latest.integrity?.verified ? '通过' : '失败'}</dd></div>
          </dl>
          <small>
            {latestPayload.platform_quote?.platform_name || '销售平台'} · 报价于 {displayTime(latestPayload.platform_quote?.quoted_at)} ·
            有效至 {displayTime(latestPayload.platform_quote?.quote_expires_at)} · 第 {latest.revision || 1} 版
          </small>
        </>
      )}

      {!latest && eligible && <small>成本快照完整且哈希校验通过，尚未记录销售平台本次交易页报价。</small>}
      {!eligible && costReady && <small>成本快照绑定或完整性校验失败，请刷新真实候选后再核对。</small>}

      {open && eligible && (
        <form className="peer-platform-quote-form" onSubmit={submit}>
          <label>
            <span>销售平台</span>
            <input value={form.platformName} onChange={(event) => update('platformName', event.target.value)} minLength={2} maxLength={80} required />
          </label>
          <label>
            <span>报价时间</span>
            <input type="datetime-local" value={form.quotedAt} onChange={(event) => update('quotedAt', event.target.value)} required />
          </label>
          <label>
            <span>平台赎回费</span>
            <input type="number" inputMode="decimal" min="0" step="0.01" value={form.redemptionFee} onChange={(event) => update('redemptionFee', event.target.value)} required />
          </label>
          <label>
            <span>候选申购费</span>
            <input type="number" inputMode="decimal" min="0" step="0.01" value={form.entryFee} onChange={(event) => update('entryFee', event.target.value)} required />
          </label>
          <label>
            <span>预计赎回到账日</span>
            <input type="date" value={form.arrivalDate} onChange={(event) => update('arrivalDate', event.target.value)} required />
          </label>
          <label className="full-width">
            <span>报价备注</span>
            <input value={form.note} onChange={(event) => update('note', event.target.value)} maxLength={300} placeholder="可选：订单页、限额或到账说明" />
          </label>
          <label className="peer-quote-check full-width">
            <input type="checkbox" checked={form.candidateAvailable} onChange={(event) => update('candidateAvailable', event.target.checked)} />
            <span>候选基金当前可申购</span>
          </label>
          <label className="peer-quote-check full-width">
            <input type="checkbox" checked={form.platformAcknowledged} onChange={(event) => update('platformAcknowledged', event.target.checked)} required />
            <span>费用来自销售平台本次交易确认页，不是上方披露费率</span>
          </label>
          <label className="peer-quote-check full-width">
            <input type="checkbox" checked={form.varianceAcknowledged} onChange={(event) => update('varianceAcknowledged', event.target.checked)} />
            <span>若报价明显偏离披露区间，我已复核并确认差异</span>
          </label>
          {error && <div className="error full-width">{error}</div>}
          <div className="peer-quote-submit full-width">
            <small><Clock3 size={12} /> 报价保存后 24 小时自动过期，不触发自动交易。</small>
            <button type="submit" className="primary compact" disabled={saving || !form.platformAcknowledged}>
              <Save size={14} /> {saving ? '保存中' : '确认并留痕'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}

function AlternativeRows({ alternatives, onConfirmSwitchQuote }) {
  const rows = alternatives?.alternatives || []
  const audit = alternatives?.durability_audit || {}
  const auditSummary = audit.summary || {}
  const dueAudit = alternatives?.due_diligence_audit || {}
  const dueSummary = dueAudit.summary || {}
  const costAudit = alternatives?.switch_cost_audit || null
  const costSummary = costAudit?.summary || {}
  if (!rows.length) return null
  return (
    <div className="peer-alternative-results">
      <div className="peer-alternative-head">
        <div><span>真实同类候选</span><b>{alternatives.selected?.category_name || alternatives.selected?.category || '同类基金'}</b></div>
        <small>榜单截至 {alternatives.as_of || '-'}</small>
      </div>
      <div className={`peer-durability-summary ${audit.status || 'unavailable'}`}>
        <div><span>滚动持续性复核</span><b>{audit.status === 'evaluated' ? `${auditSummary.evaluated_count || 0} 只已验证` : '真实日收益验证不完整'}</b></div>
        <dl>
          <div><dt>进入尽调</dt><dd>{auditSummary.due_diligence_count ?? 0}</dd></div>
          <div><dt>追涨区</dt><dd>{auditSummary.hot_count ?? 0}</dd></div>
          <div><dt>仅近期领先</dt><dd>{auditSummary.recent_leader_only_count ?? 0}</dd></div>
        </dl>
      </div>
      <div className={`peer-due-diligence-summary ${dueAudit.status || 'unavailable'}`}>
        <div><span>费率与披露持仓复核</span><b>{dueAudit.status === 'evaluated' ? `${dueSummary.evaluated_count || 0} 只已验证` : '真实尽调证据不完整'}</b></div>
        <dl>
          <div><dt>进入持有期核验</dt><dd>{dueSummary.holding_period_cost_review_count ?? 0}</dd></div>
          <div><dt>高重合无优势</dt><dd>{dueSummary.duplicate_without_cost_edge_count ?? 0}</dd></div>
          <div><dt>费率缺口</dt><dd>{dueSummary.incomplete_fee_count ?? 0}</dd></div>
        </dl>
      </div>
      {costAudit && (
        <div className={`peer-switch-cost-summary ${costAudit.status || 'unavailable'}`}>
          <div><span>我的 FIFO 换仓成本</span><b>{costSummary.ready_for_platform_quote_count || 0} 只完成披露成本核算</b></div>
          <dl>
            <div><dt>披露成本完整</dt><dd>{costSummary.ready_for_platform_quote_count ?? 0}</dd></div>
            <div><dt>平台报价有效</dt><dd>{costSummary.current_platform_quote_count ?? 0}</dd></div>
            <div><dt>报价需重算</dt><dd>{costSummary.stale_platform_quote_count ?? 0}</dd></div>
          </dl>
        </div>
      )}
      <div className="peer-alternative-list">
        {rows.slice(0, 3).map((item) => {
          const durability = item.durability || {}
          const dueDiligence = item.due_diligence || {}
          const overlap = dueDiligence.overlap || {}
          const fees = dueDiligence.fees || {}
          const rolling6 = durability.rolling?.['6m'] || {}
          const rolling12 = durability.rolling?.['12m'] || {}
          const commonStocks = (overlap.common_stocks || []).map((row) => row.name).filter(Boolean).slice(0, 3)
          const switchCost = item.switch_cost_review || null
          const promotional = switchCost?.cost_snapshots?.page_promotional || null
          const standard = switchCost?.cost_snapshots?.standard_disclosed || null
          const hurdle = switchCost?.historical_cost_hurdle || {}
          const costReady = switchCost?.status === 'ready_for_platform_quote'
          const platformStatus = quoteStatus(item.latest_platform_quote)
          return (
            <article key={item.code}>
              <div className="peer-alternative-name">
                <span>{item.code}</span>
                <b>{item.name || item.code}</b>
                <em className={durabilityTone(durability.status)}>{durability.label || '持续性尚未验证'}</em>
                <em className={dueDiligenceTone(dueDiligence.status)}>{dueDiligence.label || '替换价值尚未验证'}</em>
              </div>
              <dl>
                <div><dt>滚动 6 月胜率</dt><dd>{pct(rolling6.win_rate_pct)}</dd></div>
                <div><dt>滚动 12 月胜率</dt><dd>{pct(rolling12.win_rate_pct)}</dd></div>
                <div><dt>12 月中位超额</dt><dd className={tone(rolling12.median_excess_pp)}>{pp(rolling12.median_excess_pp)}</dd></div>
                <div><dt>持股重合下界</dt><dd>{pct(overlap.stock_overlap_lower_bound_pct)}</dd></div>
                <div><dt>年度费率差</dt><dd className={tone(fees.annual_rate_delta_pp == null ? null : -fees.annual_rate_delta_pp)}>{pp(fees.annual_rate_delta_pp)}</dd></div>
                <div><dt>候选明确运作费</dt><dd>{pct(fees.candidate_declared_annual_rate_pct)}</dd></div>
              </dl>
              <p><strong>持续性</strong>{durability.rationale || '真实每日收益复核不可用，不能把榜单领先升级为换仓候选。'}</p>
              <p><strong>替换价值</strong>{dueDiligence.rationale || '真实费率或定期报告持仓不完整，本轮停止换仓尽调。'}</p>
              <p>
                <strong>{commonStocks.length ? '共同披露持股' : costReady ? '成本核验' : '下一步缺口'}</strong>
                {commonStocks.length
                  ? commonStocks.join('、')
                  : costReady
                    ? 'FIFO 批次与披露费率已匹配，下一步只确认销售平台当日报价和到账时间。'
                    : (dueDiligence.decision_gate?.remaining_requirements?.[0] || '用户逐笔持有天数与销售平台当日赎回报价。')}
              </p>
              {switchCost && (
                <div className={`peer-switch-cost ${switchCostTone(switchCost.status)}`}>
                  <div className="peer-switch-cost-head">
                    <div><span>我的换仓成本</span><b>{switchCost.label || '成本核算未完成'}</b></div>
                    <em>{platformStatus === 'confirmed_current' ? '成本已确认' : ['expired', 'superseded'].includes(platformStatus) ? '报价需重算' : '不可执行'}</em>
                  </div>
                  {switchCost.status === 'ready_for_platform_quote' ? (
                    <>
                      <dl>
                        <div><dt>FIFO 批次</dt><dd>{switchCost.coverage?.remaining_lot_count ?? '-'}</dd></div>
                        <div><dt>确认净值</dt><dd>{switchCost.valuation?.unit_nav ?? '-'}</dd></div>
                        <div><dt>披露赎回费</dt><dd>{money(switchCost.redemption?.disclosed_fee_yuan)}</dd></div>
                        <div><dt>页面优惠总成本</dt><dd>{money(promotional?.total_switching_cost_yuan)}</dd></div>
                        <div><dt>标准费率总成本</dt><dd>{money(standard?.total_switching_cost_yuan)}</dd></div>
                        <div><dt>历史超额覆盖期</dt><dd>{months(hurdle.page_promotional_coverage_months)}</dd></div>
                      </dl>
                      <div className="peer-switch-lots">
                        {(switchCost.redemption?.lot_breakdown || []).map((lot) => (
                          <div key={`${item.code}-${lot.transaction_id}-${lot.confirmation_date}`}>
                            <span>{lot.confirmation_date}</span>
                            <span>{lot.holding_days} 天</span>
                            <span>{lot.matched_band}</span>
                            <b>{pct(lot.rate_pct)} · {money(lot.fee_yuan)}</b>
                          </div>
                        ))}
                      </div>
                      <small>确认净值截至 {switchCost.valuation?.as_of || '-'}；页面优惠费率和历史覆盖期只用于复核，提交前仍以销售平台报价为准。</small>
                      <SwitchQuotePanel item={item} costReady={costReady} onConfirm={onConfirmSwitchQuote} />
                    </>
                  ) : (
                    <>
                      <p>{switchCost.reason || '真实成本证据不完整，本轮停止核算。'}</p>
                      <small>{switchCost.decision_gate?.remaining_requirements?.[0] || '补齐成本证据后再核算。'}</small>
                    </>
                  )}
                </div>
              )}
            </article>
          )
        })}
      </div>
      <p className="peer-alternative-policy">历史胜率不是未来上涨概率，定期报告也不是实时持仓。FIFO 披露成本通过后仍必须核对销售平台当日费用和到账时间，不等于应当换仓。</p>
    </div>
  )
}

export default function FundPeerPersistenceView({
  data,
  loading = false,
  error = '',
  onRetry,
  onLoadAlternatives,
  alternatives,
  alternativesLoading = false,
  alternativesError = '',
  onOpenEvidence,
  onConfirmSwitchQuote,
}) {
  const evaluated = data?.status === 'evaluated'
  const diagnosis = data?.diagnosis || {}
  const horizons = data?.horizons || []
  const quarters = data?.quarters || []
  const review = data?.replacement_review || {}

  return (
    <div className="fund-peer-persistence">
      <div className="fund-peer-head">
        <div>
          <span className="eyebrow">PEER PERSISTENCE</span>
          <h4>基金自身还是同类都弱</h4>
          <small>{data?.diagnostic_id || 'fund_peer_relative_persistence'}@{data?.diagnostic_version || '1.0.0'} · 按需读取</small>
        </div>
        <div className="fund-peer-tools">
          {onOpenEvidence && data?.evidence_id && (
            <button type="button" className="icon-button" onClick={() => onOpenEvidence(data.evidence_id)} title="查看同类诊断 Evidence" aria-label="查看同类诊断 Evidence">
              <FileSearch size={15} />
            </button>
          )}
          {onRetry && (
            <button type="button" className="icon-button" onClick={onRetry} disabled={loading} title="刷新真实同类诊断" aria-label="刷新真实同类诊断">
              <RefreshCw size={15} className={loading ? 'spin-icon' : ''} />
            </button>
          )}
        </div>
      </div>

      {loading && !data && <div className="fund-peer-loading"><span className="spinner" />正在对齐基金与同类平均的真实日期序列</div>}

      {!loading && !evaluated && (
        <div className="fund-peer-unavailable">
          <Scale size={17} aria-hidden="true" />
          <div><strong>{data?.status === 'insufficient_data' ? '可比区间不足' : '同类诊断不可用'}</strong><p>{reasonText(data, error)}</p></div>
        </div>
      )}

      {evaluated && (
        <>
          <div className={`fund-peer-diagnosis ${diagnosisTone(diagnosis.status)}`}>
            <div><span>相对能力诊断</span><strong>{diagnosis.label || '-'}</strong><p>{diagnosis.rationale || '-'}</p></div>
            <small>截至 {data.as_of || '-'} · {data.peer_name || '同类平均'} · 置信度 {data.confidence?.level === 'medium' ? '中' : '低'}{data.stage_validation?.status === 'verified' ? ' · 年度口径已交叉校验' : ''}</small>
          </div>

          <div className="fund-peer-horizons">
            {horizons.map((item) => (
              <article className={item.status === 'available' ? tone(item.excess_return_pp) : 'unavailable'} key={item.window}>
                <header><b>{WINDOW_LABELS[item.window] || item.window}</b><span>{windowRange(item)}</span></header>
                {item.status === 'available' ? (
                  <>
                    <dl>
                      <div><dt>本基金</dt><dd className={tone(item.fund_return_pct)}>{pct(item.fund_return_pct, true)}</dd></div>
                      <div><dt>同类平均</dt><dd className={tone(item.peer_return_pct)}>{pct(item.peer_return_pct, true)}</dd></div>
                    </dl>
                    <p>相对同类 <strong className={tone(item.excess_return_pp)}>{pp(item.excess_return_pp)}</strong></p>
                  </>
                ) : <p>对应共同日期端点不可用，不用附近的单边日期补齐。</p>}
              </article>
            ))}
          </div>

          <div className="fund-peer-quarter-band">
            <div className="fund-peer-quarter-title"><ArrowRightLeft size={15} aria-hidden="true" /><span>互不重叠季度</span></div>
            {quarters.map((item) => (
              <div key={item.window}>
                <span>{WINDOW_LABELS[item.window] || item.window}</span>
                <b className={tone(item.excess_return_pp)}>{item.status === 'available' ? pp(item.excess_return_pp) : '覆盖不足'}</b>
                <small>{item.start_date || '-'} 至 {item.end_date || '-'}</small>
              </div>
            ))}
          </div>

          <div className={`fund-peer-review ${review.triggered ? 'triggered' : ''}`}>
            <div className="fund-peer-review-head">
              <div><span>替代审查门禁</span><b>{review.triggered ? '已满足研究触发条件' : '尚未满足完整触发条件'}</b></div>
              <em>{review.automatic_redemption_allowed ? '允许自动赎回' : '禁止自动赎回'}</em>
            </div>
            <div className="fund-peer-gates">
              {(review.checks || []).map((check) => (
                <div className={check.status} key={check.code}>
                  <GateIcon status={check.status} />
                  <span><b>{check.label}</b><small>{gateValue(check)}</small></span>
                </div>
              ))}
            </div>
            {onLoadAlternatives && (
              <button type="button" className="ghost fund-peer-alternative-button" onClick={onLoadAlternatives} disabled={alternativesLoading || !evaluated}>
                <FileSearch size={15} /> {alternativesLoading ? '读取真实候选中' : alternatives ? '刷新替代候选' : '继续核验替代候选'}
              </button>
            )}
            {alternativesError && <div className="error fund-peer-alternative-error">{alternativesError}</div>}
            <AlternativeRows alternatives={alternatives} onConfirmSwitchQuote={onConfirmSwitchQuote} />
          </div>
        </>
      )}

      <p className="fund-peer-policy">
        <ShieldAlert size={13} aria-hidden="true" />
        同类平均不是可投资基准；历史相对表现不能预测未来。替换前仍必须核验费用、份额类别、组合重合、基金经理与投资合同变化。
      </p>
    </div>
  )
}
