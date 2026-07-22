import { AlertTriangle, BadgeCheck, DatabaseZap, RefreshCw, ShieldCheck } from 'lucide-react'

function money(value) {
  if (value == null || value === '') return '-'
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 2,
  }).format(number)
}

function number(value, digits = 2) {
  if (value == null || value === '') return '-'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '-'
}

function dateTime(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value).replace('T', ' ')
  return parsed.toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const METHOD_LABEL = {
  automatic_confirmed_price: '自动估值',
  manual_confirmed_amount: '确认金额',
  unavailable: '不可用',
}

export default function PortfolioValuationPanel({ data, loading, error, onRefresh }) {
  const snapshot = data?.snapshot
  const payload = snapshot?.payload || {}
  const summary = payload.summary || {}
  const coverage = payload.coverage || {}
  const gate = data?.runtime_gate || {}
  const binding = data?.binding || {}
  const positions = payload.positions || []

  let state = 'empty'
  let stateLabel = '等待首次估值'
  let stateDetail = '保存持仓后生成不可变估值快照。'
  if (snapshot && !binding.current) {
    state = 'stale'
    stateLabel = '持仓已变化'
    stateDetail = '旧快照不会继续影响当前风险结论。'
  } else if (snapshot && gate.risk_analysis_eligible) {
    state = gate.trade_amount_eligible ? 'ready' : 'review'
    stateLabel = gate.trade_amount_eligible ? '估值门禁通过' : '可做风险复盘'
    stateDetail = gate.trade_amount_eligible
      ? '价格、净值、汇率和自动覆盖率均达到精确金额门槛。'
      : '组合比例可复盘；精确交易金额仍受自动覆盖率门禁限制。'
  } else if (snapshot) {
    state = 'stale'
    stateLabel = '估值需要修复'
    stateDetail = '存在过期、缺价、缺汇率或覆盖不足。'
  }

  const StateIcon = state === 'ready' ? BadgeCheck : state === 'review' ? ShieldCheck : state === 'empty' ? DatabaseZap : AlertTriangle

  return (
    <section className={`portfolio-valuation panel ${state}`} aria-label="可信组合估值">
      <div className="portfolio-valuation-head">
        <div>
          <span className="eyebrow">可信组合估值 · CNY</span>
          <h3><StateIcon size={19} aria-hidden="true" />{stateLabel}</h3>
          <p>{stateDetail}</p>
        </div>
        <button type="button" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
          {loading ? '正在读取真实行情' : snapshot ? '刷新真实估值' : '生成组合估值'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {snapshot && (
        <>
          <div className="portfolio-valuation-metrics">
            <div><span>组合总值</span><b>{money(summary.total_value)}</b><small>统一人民币口径</small></div>
            <div><span>持仓覆盖</span><b>{number(coverage.count_coverage_pct)}%</b><small>{coverage.valued_count || 0}/{coverage.holding_count || 0} 项</small></div>
            <div><span>自动估值</span><b>{number(coverage.automatic_value_pct)}%</b><small>{coverage.automatic_count || 0} 项份额×价格</small></div>
            <div><span>专业/确认来源</span><b>{number(coverage.professional_value_pct)}%</b><small>过期 {coverage.stale_count || 0} 项</small></div>
          </div>

          <div className={`portfolio-valuation-gate ${gate.risk_analysis_eligible ? 'passed' : 'blocked'}`}>
            <ShieldCheck size={18} aria-hidden="true" />
            <div>
              <b>{gate.risk_analysis_eligible ? '风险分析可用' : '风险分析已暂停'}</b>
              <span>{gate.trade_amount_eligible ? '精确金额门禁通过，但仍不授权交易。' : '精确金额门禁未通过，不输出可执行交易金额。'}</span>
            </div>
            <small>快照 {String(snapshot.id || '').slice(-8)} · 有效至 {dateTime(snapshot.fresh_until || payload.fresh_until)}</small>
          </div>

          {(gate.reasons || []).length > 0 && (
            <div className="portfolio-valuation-reasons">
              {gate.reasons.slice(0, 4).map((reason) => <span key={reason}>{reason}</span>)}
            </div>
          )}

          <div className="corr-wrap portfolio-valuation-table-wrap">
            <table className="compact-table portfolio-valuation-table">
              <thead>
                <tr><th>资产</th><th>估值方式</th><th>份额</th><th>价格/NAV</th><th>汇率</th><th>人民币市值</th><th>占比</th><th>来源时点</th></tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={`${position.holding_id}-${position.code}`} className={position.freshness !== 'current' ? 'stale' : ''}>
                    <td><b>{position.name || position.code}</b><small>{position.market} · {position.code}</small></td>
                    <td><span className={`valuation-method ${position.valuation_method}`}>{METHOD_LABEL[position.valuation_method] || position.valuation_method}</span>{position.issues?.[0] && <small title={position.issues.join('；')}>{position.issues[0]}</small>}</td>
                    <td>{number(position.shares, 4)}</td>
                    <td>{position.unit_price == null ? '-' : `${number(position.unit_price, 4)} ${position.currency}`}</td>
                    <td>{position.currency === 'CNY' ? '1.0000' : number(position.fx_rate_to_cny, 4)}</td>
                    <td>{money(position.base_value)}</td>
                    <td>{number(position.ratio)}%</td>
                    <td><span>{position.price_as_of || position.holding_updated_at?.slice(0, 10) || '-'}</span><small>{position.price_source || '用户确认金额'}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="portfolio-valuation-footnote">
            自动估值使用确认净值或未复权日线，并按持久汇率换算；它用于配置和风险复盘，不等于券商可成交金额，也不会自动下单。
          </p>
        </>
      )}

      {!snapshot && !loading && !error && (
        <div className="portfolio-valuation-empty">尚无估值快照。补全份额后可提高自动估值覆盖；没有份额时会明确使用最近一次用户确认金额。</div>
      )}
    </section>
  )
}
