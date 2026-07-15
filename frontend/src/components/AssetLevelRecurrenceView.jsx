import { Clock3, Database, History } from 'lucide-react'

function value(number) {
  if (number == null || Number.isNaN(Number(number))) return '-'
  const parsed = Number(number)
  return parsed.toFixed(parsed >= 100 ? 2 : 4)
}

function signed(number) {
  if (number == null || Number.isNaN(Number(number))) return '-'
  const parsed = Number(number)
  return `${parsed > 0 ? '+' : ''}${value(parsed)}`
}

function statusMeta(status) {
  if (status === 'reached' || status === 'reached_exact') return ['历史曾到达', 'matched']
  if (status === 'crossed_between') return ['区间覆盖当前估值', 'matched']
  if (status === 'not_found_in_coverage') return ['覆盖期未到达', 'nearest']
  return ['数据不可用', 'unavailable']
}

export default function AssetLevelRecurrenceView({ data, onOpenEvidence }) {
  if (!data) return null
  const target = data.target || {}
  const history = data.history || {}
  const occurrence = data.occurrence
  const nearest = data.nearest
  const [statusLabel, statusTone] = statusMeta(data.status)
  const isFund = data.asset_type === 'fund'

  return (
    <div className="level-recurrence-view">
      <div className="level-recurrence-head">
        <div>
          <span className="eyebrow">Live Level History</span>
          <h4>当前{isFund ? '盘中估值' : '实时价位'}上一次到达时间</h4>
          <small>{data.metric_id}@{data.metric_version}</small>
        </div>
        {data.evidence_id && onOpenEvidence && (
          <button className="ghost" onClick={() => onOpenEvidence(data.evidence_id)}>
            <Database size={14} aria-hidden="true" />查看 Evidence
          </button>
        )}
      </div>

      <div className="level-recurrence-summary">
        <div className="level-recurrence-target">
          <span>{target.label || (isFund ? '盘中估算净值' : '实时成交价')}</span>
          <b>{value(target.value)}</b>
          <small><Clock3 size={12} aria-hidden="true" />{target.as_of || '-'}</small>
        </div>
        <div className="level-recurrence-result">
          <em className={statusTone}>{statusLabel}</em>
          {data.status === 'unavailable' && <p>{data.reason || '真实数据当前不可用'}</p>}
          {(data.status === 'reached' || data.status === 'reached_exact') && (
            <>
              <span>上一次到达日期</span>
              <b>{occurrence?.date || '-'}</b>
              {occurrence?.kind === 'daily_range' ? (
                <small>当日区间 {value(occurrence.low)} - {value(occurrence.high)} · 收盘 {value(occurrence.close)}</small>
              ) : (
                <small>当日确认净值 {value(occurrence?.value)}</small>
              )}
            </>
          )}
          {data.status === 'crossed_between' && (
            <>
              <span>上一次覆盖当前估值的确认净值区间</span>
              <b>{value(occurrence?.from_value)} → {value(occurrence?.to_value)}</b>
              <strong className="level-recurrence-covered-target">
                当前盘中估值 {value(target?.value)} 位于该区间内
              </strong>
              <small>
                {occurrence?.from_date || '-'} 至 {occurrence?.to_date || '-'} · {occurrence?.direction === 'up' ? '向上穿越' : '向下穿越'}
              </small>
            </>
          )}
          {data.status === 'not_found_in_coverage' && (
            <>
              <span>历史最近值</span>
              <b>{nearest?.date || '-'}</b>
              <small>{value(nearest?.value)} · 相差 {signed(nearest?.difference)}</small>
            </>
          )}
        </div>
      </div>

      {history.source && (
        <div className="level-recurrence-meta">
          <History size={13} aria-hidden="true" />
          <span>
            历史源: {history.source} · {history.start_date || '-'} 至 {history.end_date || '-'} · {history.observation_count || 0} 个样本
          </span>
        </div>
      )}
      <p className="level-recurrence-policy">
        实时源: {target.source || '-'}。{data.policy || data.reason || '没有真实数据时不生成替代结果。'}
      </p>
    </div>
  )
}
