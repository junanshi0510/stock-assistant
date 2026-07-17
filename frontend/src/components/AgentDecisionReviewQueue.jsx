import { useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Database,
  ListChecks,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'

const STATUS = {
  blocked: { label: '证据阻塞', tone: 'blocked', icon: AlertTriangle },
  due: { label: '到期待复盘', tone: 'due', icon: Clock3 },
  ready: { label: '真实证据已更新', tone: 'ready', icon: CheckCircle2 },
  upcoming: { label: '等待复盘日', tone: 'upcoming', icon: CalendarClock },
  unscheduled: { label: '未设复盘日', tone: 'unscheduled', icon: CalendarClock },
}

const DECISION_LABELS = {
  undecided: '尚未决定',
  observe: '继续观察',
  hold: '维持持仓',
  add: '计划增加',
  reduce: '计划降低',
  exit: '计划退出',
  no_action: '不采取动作',
}

const BLOCKED_REASON_LABELS = {
  feedback_chain_invalid: '决策日志链校验失败',
  run_evidence_integrity_failed: '原 Run 证据完整性失败',
  outcome_evidence_integrity_failed: '结果 Evidence 校验失败',
  invalid_planned_review_date: '复盘日期无效',
  outcome_collection_failed: '真实净值采集失败',
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function dateText(value) {
  if (!value) return '-'
  const parsed = new Date(`${String(value).slice(0, 10)}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString('zh-CN')
}

function timingText(item) {
  if (item.status === 'unscheduled') return '未设置日期'
  if (item.status === 'ready') return `计划 ${dateText(item.planned_review_at)}`
  if (item.days_to_review == null) return dateText(item.planned_review_at)
  if (item.days_to_review === 0) return '今天到期'
  if (item.days_to_review < 0) return `已到期 ${Math.abs(item.days_to_review)} 天`
  return `${item.days_to_review} 天后`
}

export default function AgentDecisionReviewQueue({
  data,
  loading,
  filter,
  selectedRunId,
  onRefresh,
  onFilterChange,
  onOpenRun,
}) {
  const [expanded, setExpanded] = useState(false)
  const [openingRunId, setOpeningRunId] = useState('')
  const expectedResponseFilter = filter === 'all' ? 'all' : filter
  const responseMatchesFilter = data?.filter === expectedResponseFilter
  const items = responseMatchesFilter ? (data?.items || []) : []
  const counts = data?.counts || {}
  const attentionCount = Number(counts.blocked || 0) + Number(counts.due || 0) + Number(counts.ready || 0)
  const visibleItems = expanded ? items : items.slice(0, 8)

  function changeFilter(nextFilter) {
    setExpanded(false)
    onFilterChange(nextFilter)
  }

  async function openRun(runId) {
    setOpeningRunId(runId)
    try {
      await onOpenRun(runId)
      window.setTimeout(() => {
        document.querySelector('.agent-run-band')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 50)
    } finally {
      setOpeningRunId('')
    }
  }

  return (
    <section className="agent-review-queue" aria-label="决策复盘队列">
      <div className="agent-review-head">
        <div>
          <span className="eyebrow">Decision Review Queue</span>
          <h3>决策复盘队列</h3>
          <small>截止 {data?.as_of || '-'} · 只关联真实确认净值 Evidence</small>
        </div>
        <button
          type="button"
          className="ghost agent-review-refresh"
          onClick={onRefresh}
          disabled={loading}
          title="刷新复盘队列"
          aria-label="刷新复盘队列"
        >
          <RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
        </button>
      </div>

      <div className="agent-review-filters" role="group" aria-label="复盘队列筛选">
        <button type="button" disabled={loading} className={filter === 'attention' ? 'active' : ''} aria-pressed={filter === 'attention'} onClick={() => changeFilter('attention')}>
          <ListChecks size={14} aria-hidden="true" />需处理 <span>{attentionCount}</span>
        </button>
        <button type="button" disabled={loading} className={filter === 'upcoming' ? 'active' : ''} aria-pressed={filter === 'upcoming'} onClick={() => changeFilter('upcoming')}>
          <CalendarClock size={14} aria-hidden="true" />待到期 <span>{counts.upcoming || 0}</span>
        </button>
        <button type="button" disabled={loading} className={filter === 'all' ? 'active' : ''} aria-pressed={filter === 'all'} onClick={() => changeFilter('all')}>
          全部 <span>{data?.total_candidates || 0}</span>
        </button>
      </div>

      {loading && items.length === 0 && (
        <div className="agent-review-empty"><RefreshCw size={16} className="spin-icon" aria-hidden="true" />正在读取复盘状态</div>
      )}

      {!loading && items.length === 0 && (
        <div className="agent-review-empty">
          <ListChecks size={17} aria-hidden="true" />
          {Number(data?.total_candidates || 0) === 0 ? '暂无已记录的复盘计划' : '当前筛选下没有复盘事项'}
        </div>
      )}

      <div className="agent-review-list">
        {visibleItems.map((item) => {
          const status = STATUS[item.status] || STATUS.unscheduled
          const StatusIcon = status.icon
          const outcome = item.current_outcome
          const blockedReasons = item.verification?.blocked_reasons || []
          return (
            <article className={`agent-review-row ${status.tone} ${selectedRunId === item.run_id ? 'selected' : ''}`} key={item.run_id}>
              <div className="agent-review-status">
                <StatusIcon size={15} aria-hidden="true" />
                <span>{status.label}</span>
                <small>{timingText(item)}</small>
              </div>
              <div className="agent-review-subject">
                <b>{item.run?.code || '-'} {item.run?.name || '基金研究'}</b>
                <span>{item.run?.headline || '原研究结论已保存'}</span>
                <small>
                  我的计划：{DECISION_LABELS[item.feedback?.user_decision] || item.feedback?.user_decision || '-'}
                  {item.run?.as_of ? ` · 原证据 ${item.run.as_of}` : ''}
                </small>
              </div>
              <div className="agent-review-outcome">
                {outcome ? (
                  <>
                    <span><Database size={12} aria-hidden="true" />真实净值变化</span>
                    <b className={Number(outcome.return_pct) < 0 ? 'delta-neg' : ''}>{pct(outcome.return_pct)}</b>
                    <small>截至 {dateText(outcome.observed_as_of)} · 非个人盈亏</small>
                  </>
                ) : (
                  <>
                    <span><Database size={12} aria-hidden="true" />真实结果证据</span>
                    <b>-</b>
                    <small>{blockedReasons.length > 0 ? (BLOCKED_REASON_LABELS[blockedReasons[0]] || blockedReasons[0]) : '尚无覆盖复盘日的确认净值'}</small>
                  </>
                )}
              </div>
              <div className="agent-review-action">
                <span className={item.verification?.feedback_verified && item.verification?.evidence_verified ? 'verified' : 'invalid'}>
                  {item.verification?.feedback_verified && item.verification?.evidence_verified
                    ? <ShieldCheck size={13} aria-hidden="true" />
                    : <AlertTriangle size={13} aria-hidden="true" />}
                  {item.verification?.feedback_verified && item.verification?.evidence_verified ? '链路已校验' : '校验未通过'}
                </span>
                <button type="button" onClick={() => openRun(item.run_id)} disabled={openingRunId === item.run_id}>
                  {openingRunId === item.run_id ? <RefreshCw size={14} className="spin-icon" aria-hidden="true" /> : <ArrowRight size={14} aria-hidden="true" />}
                  打开复盘
                </button>
              </div>
            </article>
          )
        })}
      </div>

      {items.length > 8 && (
        <button type="button" className="ghost agent-review-more" onClick={() => setExpanded((current) => !current)}>
          {expanded ? '收起' : `展开本页 ${items.length} 项`}
        </button>
      )}

      {data?.has_more && (
        <div className="agent-review-warning"><AlertTriangle size={14} aria-hidden="true" />当前筛选超过 50 项，优先展示最需要处理的记录。</div>
      )}
      {data?.candidate_window_truncated && (
        <div className="agent-review-warning"><AlertTriangle size={14} aria-hidden="true" />当前仅计算最近 500 个决策版本，请按基金代码从运行历史继续检索。</div>
      )}
      <p className="agent-review-policy">{data?.policy}</p>
    </section>
  )
}
