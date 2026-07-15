import { BarChart3, ShieldAlert } from 'lucide-react'

const HORIZON_LABELS = {
  '3m': '3 个月',
  '6m': '6 个月',
  '12m': '12 个月',
}

const REASON_LABELS = {
  history_shorter_than_ma_window: '确认净值历史少于 60 个观察值，无法识别当前状态。',
  current_condition_unavailable: '当前确认净值状态无法计算。',
  analog_samples_below_minimum: '与当前趋势和回撤状态一致的历史月末样本少于 6 个。',
  live_estimate_unavailable_history_context_not_requested: '当前盘中估值不可用，本轮未加载历史状态样本。',
}

function percent(value, signed = false) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function percentagePoints(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)} 个百分点`
}

function navValue(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toFixed(4)
}

function deltaTone(value) {
  if (Number(value) > 0) return 'positive'
  if (Number(value) < 0) return 'negative'
  return 'neutral'
}

function trendLabel(value) {
  return value === 'above_ma60' ? '位于 MA60 上方' : value === 'below_ma60' ? '位于 MA60 下方' : '-'
}

function drawdownLabel(value) {
  return {
    near_high: '接近阶段高位',
    normal_pullback: '普通回撤区',
    deep_drawdown: '深度回撤区',
  }[value] || '-'
}

function confidenceLabel(value) {
  return { low: '低', medium: '中', high: '高' }[value] || '不可用'
}

function reasonLabel(data) {
  const reason = data?.reason || data?.confidence?.reasons?.[0] || ''
  if (REASON_LABELS[reason]) return REASON_LABELS[reason]
  if (reason.startsWith('confirmed_nav_history_unavailable:')) return '真实确认净值历史源当前不可用。'
  if (reason.startsWith('conditioned_forward_calculation_failed:')) return '历史状态分布计算失败，本轮不生成替代结论。'
  return '真实历史样本当前不足，系统不生成方向结论。'
}

function verdict(data) {
  const direction = data?.signal?.direction
  if (direction === 'positive') {
    return {
      tone: 'positive',
      label: '同状态样本偏正',
      text: '主要观察窗口的历史正收益占比和中位收益同时为正，但这不是加仓信号。',
    }
  }
  if (direction === 'negative') {
    return {
      tone: 'negative',
      label: '同状态样本偏弱',
      text: '主要观察窗口的历史正收益占比偏低且中位收益为负，应优先复核风险。',
    }
  }
  return {
    tone: 'mixed',
    label: '同状态样本分化',
    text: '历史同状态没有形成一致方向，当前状态本身不足以支持仓位动作。',
  }
}

export default function FundConditionedForwardView({ data }) {
  if (!data) return null
  const condition = data.condition || {}
  const horizons = data.horizons || []
  const evaluated = data.status === 'evaluated'
  const summary = evaluated ? verdict(data) : null

  return (
    <div className="fund-forward-view">
      <div className="fund-forward-head">
        <div>
          <span className="eyebrow">CONFIRMED NAV ANALOGS</span>
          <h4>同状态后的历史表现</h4>
          <small>{data.strategy_id || 'fund_conditioned_forward_return'}@{data.strategy_version || '-'}</small>
        </div>
        <span className="fund-forward-shadow"><BarChart3 size={13} aria-hidden="true" />影子研究 · 不驱动仓位</span>
      </div>

      {condition.as_of && (
        <dl className="fund-forward-condition">
          <div><dt>确认净值日期</dt><dd>{condition.as_of}</dd></div>
          <div><dt>状态</dt><dd>{trendLabel(condition.trend)} · {drawdownLabel(condition.drawdown_band)}</dd></div>
          <div><dt>确认净值 / 回撤</dt><dd>{navValue(condition.latest_nav)} / {percent(condition.current_drawdown)}</dd></div>
        </dl>
      )}

      {evaluated ? (
        <div className={`fund-forward-verdict ${summary.tone}`}>
          <div><span>{summary.label}</span><b>{summary.text}</b></div>
          <small>主要窗口 {HORIZON_LABELS[data.primary_horizon] || '-'} · 数据置信 {confidenceLabel(data.confidence?.level)}</small>
        </div>
      ) : (
        <div className="fund-forward-verdict unavailable">
          <div><span>{data.status === 'insufficient_data' ? '样本不足' : '真实数据不可用'}</span><b>{reasonLabel(data)}</b></div>
          <small>未形成可展示的历史方向证据</small>
        </div>
      )}

      {horizons.length > 0 && (
        <div className="fund-forward-horizons">
          {horizons.map((item) => {
            const analog = item.analog || {}
            const available = item.status === 'available'
            return (
              <article className={`${available ? 'available' : 'insufficient'} ${item.horizon === data.primary_horizon ? 'primary' : ''}`} key={item.horizon}>
                <header>
                  <b>{HORIZON_LABELS[item.horizon] || item.horizon}</b>
                  <span>{item.horizon === data.primary_horizon ? '主要窗口' : `样本 ${analog.sample_count || 0}`}</span>
                </header>
                {available ? (
                  <>
                    <div className="fund-forward-measures">
                      <div><span>正收益占比</span><strong>{percent(analog.positive_rate)}</strong></div>
                      <div><span>中位收益</span><strong className={deltaTone(analog.median_return)}>{percent(analog.median_return, true)}</strong></div>
                    </div>
                    <p>中间 50%：{percent(analog.p25_return, true)} 至 {percent(analog.p75_return, true)}</p>
                    <small>同状态月末样本 {analog.sample_count} 个 · {analog.sample_start || '-'} 至 {analog.sample_end || '-'}</small>
                    <small>较自身全历史：正收益占比差 {percentagePoints(item.edge?.positive_rate)} · 中位收益差 {percentagePoints(item.edge?.median_return)}</small>
                  </>
                ) : (
                  <p>只有 {analog.sample_count || 0} 个同状态月末样本，少于最低门槛 6 个。</p>
                )}
              </article>
            )
          })}
        </div>
      )}

      <p className="fund-forward-policy">
        <ShieldAlert size={13} aria-hidden="true" />
        只使用当时已经存在的确认净值，并按月末抽样；前瞻窗口可能重叠，基金经理或策略变化尚未标准化。历史分布不是未来收益预测。
      </p>
    </div>
  )
}
