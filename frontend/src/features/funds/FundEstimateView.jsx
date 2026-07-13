import { Gauge, RefreshCw } from 'lucide-react'
import AssetLevelRecurrenceView from '../../components/AssetLevelRecurrenceView'
import FundMetricCard from './FundMetricCard'
import { deltaClass, num, pct } from './fundFormatters'

/** Renders provider-estimated NAV separately from confirmed fund NAV. */
export default function FundEstimateView({ code, estimate, error, loading, onLoad }) {
  const available = estimate?.status === 'available'
  const current = estimate?.estimate || {}
  const confirmed = estimate?.confirmed || {}

  return (
    <div className="panel fade-in">
      <div className="fund-estimate-head">
        <div>
          <h3 className="section-title">
            最新可得盘中估值 <span className="hint">与上一确认净值分开展示，不将估算视为正式净值</span>
          </h3>
          <p className="hint fund-estimate-caption">按需读取东方财富基金估值，数据最多缓存 30 秒以保护真实数据源。</p>
        </div>
        <button className="ghost" onClick={() => onLoad(code)} disabled={loading} title="读取最新可得基金盘中估值">
          {loading
            ? <><RefreshCw size={16} className="spin-icon" aria-hidden="true" /> 读取中</>
            : <><Gauge size={16} aria-hidden="true" /> {estimate ? '刷新估值' : '查看估值'}</>}
        </button>
      </div>

      {loading && !estimate && <div className="placeholder"><div className="big">⌛</div>正在读取真实基金估值</div>}
      {error && <div className="error">{error}</div>}
      {estimate && !available && (
        <>
          <div className="placeholder">{estimate.reason || '数据源当前未提供可用基金估值'}</div>
          <p className="hint" style={{ marginTop: 12 }}>{estimate.policy}</p>
        </>
      )}
      {available && (
        <>
          <div className="bt-cards quality-cards">
            <FundMetricCard label="上一确认净值" value={confirmed.unit_nav != null ? num(confirmed.unit_nav, 4) : '-'} />
            <FundMetricCard label="最新可得估值" value={current.unit_nav != null ? num(current.unit_nav, 4) : '-'} />
            <FundMetricCard label="估算涨跌" value={pct(current.change_pct)} cls={deltaClass(current.change_pct)} />
            <FundMetricCard label="估值差额" value={current.change_value != null ? num(current.change_value, 4) : '-'} cls={deltaClass(current.change_value)} />
          </div>
          <div className="fund-estimate-meta">
            <span className="tag neutral">确认净值日期 {confirmed.date || '-'}</span>
            <span className="tag neutral">估值时间 {current.time || '-'}</span>
          </div>
          <AssetLevelRecurrenceView data={estimate.level_recurrence} />
          <p className="hint" style={{ marginTop: 12 }}>{estimate.policy} 数据源: {estimate.source}。</p>
        </>
      )}
    </div>
  )
}
