import { Database, History } from 'lucide-react'
import { deltaClass, pct } from './fundFormatters'

function pp(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)} 个百分点`
}

function occurrenceMeta(status) {
  if (status === 'matched') return ['同水平命中', 'matched']
  if (status === 'nearest_only') return ['仅最接近', 'nearest']
  return ['无更早区间', 'unavailable']
}

export default function FundReturnRecurrenceView({ data, onOpenEvidence }) {
  const items = data?.items || []
  if (!items.length) return null
  return (
    <div className="fund-recurrence-view">
      <div className="fund-recurrence-head">
        <div>
          <span className="eyebrow">Return Recurrence</span>
          <h4>当前收益率上一次达到时间</h4>
          <small>{data.metric_id}@{data.metric_version} · 数据截至 {data.as_of || '-'}</small>
        </div>
        {data.evidence_id && onOpenEvidence && (
          <button className="ghost" onClick={() => onOpenEvidence(data.evidence_id)}>
            <Database size={14} aria-hidden="true" />查看 Evidence
          </button>
        )}
      </div>
      <div className="fund-recurrence-grid">
        {items.map((item) => {
          const recurrence = item.recurrence || {}
          const previous = recurrence.previous
          const episode = recurrence.current_episode
          const [statusLabel, statusTone] = occurrenceMeta(recurrence.status)
          return (
            <article className={item.status === 'available' ? '' : 'insufficient'} key={item.key || item.label}>
              <header>
                <div><span>{item.label}</span><b className={deltaClass(item.current_return)}>{pct(item.current_return)}</b></div>
                <em className={statusTone}>{item.status === 'available' ? statusLabel : '历史不足'}</em>
              </header>
              {item.status === 'available' ? (
                <dl>
                  <div>
                    <dt>本轮首次进入该水平</dt>
                    <dd>{episode?.start_date || '-'}</dd>
                    <small>连续 {episode?.observation_count || 0} 个净值样本</small>
                  </div>
                  <div>
                    <dt>{recurrence.status === 'nearest_only' ? '历史最接近日期' : '上一次同水平日期'}</dt>
                    <dd>{previous?.date || '-'}</dd>
                    <small>{previous ? `${pct(previous.return)} · 相差 ${pp(previous.difference_pp)}` : '没有更早独立区间'}</small>
                  </div>
                </dl>
              ) : (
                <p>需要至少 {item.observations + 1} 个真实净值样本。</p>
              )}
              <footer>
                <History size={12} aria-hidden="true" />
                {previous?.calendar_days_ago != null ? `距今 ${previous.calendar_days_ago} 天` : '暂无可比日期'}
                {recurrence.tolerance_pp != null ? ` · 容差 ±${recurrence.tolerance_pp.toFixed(2)} 个百分点` : ''}
              </footer>
            </article>
          )
        })}
      </div>
      <p className="fund-recurrence-policy">
        系统先跳过当前连续收益区间，并至少间隔 {data.method?.minimum_separation_observations || 5} 个净值样本再寻找更早出现；未进入容差带时只报告“最接近”，不会当作相同收益率。历史重现不代表未来收益。
      </p>
    </div>
  )
}
