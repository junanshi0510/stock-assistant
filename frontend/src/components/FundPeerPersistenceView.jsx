import {
  ArrowRightLeft,
  CircleAlert,
  CircleCheck,
  FileSearch,
  RefreshCw,
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

function gateValue(check) {
  if (Array.isArray(check.observed)) return check.observed.map((value) => pp(value)).join(' / ')
  if (check.observed != null) return `${pp(check.observed)}${check.threshold != null ? ` · 阈值 ${pp(check.threshold, false)}` : ''}`
  return check.status === 'pending' ? '尚未核验' : '当前不可用'
}

function AlternativeRows({ alternatives }) {
  const rows = alternatives?.alternatives || []
  const audit = alternatives?.durability_audit || {}
  const auditSummary = audit.summary || {}
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
      <div className="peer-alternative-list">
        {rows.slice(0, 3).map((item) => {
          const durability = item.durability || {}
          const rolling6 = durability.rolling?.['6m'] || {}
          const rolling12 = durability.rolling?.['12m'] || {}
          return (
            <article key={item.code}>
              <div className="peer-alternative-name">
                <span>{item.code}</span>
                <b>{item.name || item.code}</b>
                <em className={durabilityTone(durability.status)}>{durability.label || '持续性尚未验证'}</em>
              </div>
              <dl>
                <div><dt>近 1 年</dt><dd className={tone(item.metrics?.return_1y)}>{pct(item.metrics?.return_1y, true)}</dd></div>
                <div><dt>滚动 6 月胜率</dt><dd>{pct(rolling6.win_rate_pct)}</dd></div>
                <div><dt>滚动 12 月胜率</dt><dd>{pct(rolling12.win_rate_pct)}</dd></div>
                <div><dt>12 月中位超额</dt><dd className={tone(rolling12.median_excess_pp)}>{pp(rolling12.median_excess_pp)}</dd></div>
                <div><dt>回撤差</dt><dd className={tone(durability.risk?.drawdown_delta_pp)}>{pp(durability.risk?.drawdown_delta_pp)}</dd></div>
                <div><dt>页面申购费</dt><dd>{pct(item.fee?.current_rate)}</dd></div>
              </dl>
              <p><strong>持续性</strong>{durability.rationale || '真实每日收益复核不可用，不能把榜单领先升级为换仓候选。'}</p>
              <p><strong>待核验</strong>{item.cautions?.[0] || '赎回费、销售服务费、持仓重合和基金经理稳定性。'}</p>
            </article>
          )
        })}
      </div>
      <p className="peer-alternative-policy">滚动窗口会重叠，历史胜率不是未来上涨概率。只有持续性门禁通过才继续费用和持仓重合尽调，仍不等于应当换仓。</p>
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
            <AlternativeRows alternatives={alternatives} />
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
