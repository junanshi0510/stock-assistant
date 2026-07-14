import { useState } from 'react'
import {
  AlertTriangle,
  ArrowUpRight,
  BadgeCheck,
  ChartNoAxesCombined,
  CheckCheck,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Clock3,
  DatabaseZap,
  History,
  Layers3,
  ReceiptText,
  RefreshCw,
  RotateCcw,
  Settings2,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'
import { fetchDecisionTasks, updateDecisionTask } from '../../api/portfolio'

const PRIORITY_META = {
  high: { label: '优先处理', icon: AlertTriangle },
  medium: { label: '需要复盘', icon: CircleAlert },
  normal: { label: '研究队列', icon: BadgeCheck },
}

const TASK_STATUS_META = {
  open: '待处理',
  snoozed: '稍后处理',
  acknowledged: '已确认',
  resolved: '已解决',
}

function actionIcon(category) {
  if (category.includes('组合') || category.includes('收益')) return WalletCards
  if (category.includes('成本') || category.includes('仓位')) return ReceiptText
  if (category.includes('基金')) return Layers3
  if (category.includes('市场')) return ChartNoAxesCombined
  if (category.includes('数据')) return DatabaseZap
  return CircleAlert
}

function formatTime(value) {
  if (!value) return '刚刚生成'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value).replace('T', ' ')
  return parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function DecisionCenter({ data, loading, error, onRefresh, onNavigate, onTaskUpdated }) {
  const [showAll, setShowAll] = useState(false)
  const [onlyOpen, setOnlyOpen] = useState(false)
  const [taskBusy, setTaskBusy] = useState('')
  const [taskError, setTaskError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [resolvedHistory, setResolvedHistory] = useState([])
  const profile = data?.profile
  const summary = data?.summary || {}
  const actions = data?.actions || []
  const unavailable = data?.unavailable || []
  const inbox = data?.task_inbox || {}
  const inboxSummary = inbox?.summary || {}
  const filteredActions = onlyOpen
    ? actions.filter((action) => !action.task || action.task.status === 'open')
    : actions
  const visibleActions = showAll ? filteredActions : filteredActions.slice(0, 5)

  async function changeTask(task, status, snoozeHours = null) {
    if (!task?.id || taskBusy) return
    setTaskBusy(task.id)
    setTaskError('')
    try {
      const result = await updateDecisionTask(task.id, status, task.revision, snoozeHours)
      onTaskUpdated?.(result)
    } catch (requestError) {
      setTaskError(requestError.message || '投资任务状态更新失败')
    } finally {
      setTaskBusy('')
    }
  }

  async function loadResolvedHistory() {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const result = await fetchDecisionTasks({ status: 'resolved', includeResolved: true, limit: 30 })
      setResolvedHistory(result.items || [])
    } catch (requestError) {
      setHistoryError(requestError.message || '投资任务历史读取失败')
    } finally {
      setHistoryLoading(false)
    }
  }

  function toggleHistory() {
    const next = !historyOpen
    setHistoryOpen(next)
    if (next) loadResolvedHistory()
  }

  return (
    <section className="decision-center" aria-label="今日行动清单">
      <div className="decision-center-head">
        <div>
          <span className="eyebrow">投资任务收件箱</span>
          <h3>真实条件触发，风险消失才自动解决</h3>
          <p>{data?.policy || '正在根据真实持仓与真实市场数据生成行动顺序。'}</p>
        </div>
        <div className="decision-center-tools">
          <button className="ghost" onClick={toggleHistory} disabled={historyLoading} title="查看自动解决的历史任务">
            <History size={16} aria-hidden="true" /><span>历史</span>
          </button>
          <button className="ghost decision-refresh" onClick={onRefresh} disabled={loading} title="刷新真实数据">
            <RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
            <span>{loading ? '刷新中' : '刷新'}</span>
          </button>
        </div>
      </div>

      <div className="decision-summary" aria-live="polite">
        <div className="decision-summary-item high"><span>优先处理</span><b>{summary.high_count ?? '-'}</b></div>
        <div className="decision-summary-item medium"><span>需要复盘</span><b>{summary.medium_count ?? '-'}</b></div>
        <div className="decision-summary-item normal"><span>研究队列</span><b>{summary.normal_count ?? '-'}</b></div>
        <div className="decision-summary-item unavailable"><span>不可用来源</span><b>{summary.unavailable_count ?? '-'}</b></div>
      </div>

      {inbox.status === 'available' && (
        <div className="decision-task-toolbar" aria-label="任务状态">
          <div><span>待处理</span><b>{inboxSummary.open_count ?? 0}</b></div>
          <div><span>稍后处理</span><b>{inboxSummary.snoozed_count ?? 0}</b></div>
          <div><span>已确认</span><b>{inboxSummary.acknowledged_count ?? 0}</b></div>
          <div><span>自动解决</span><b>{inboxSummary.resolved_count ?? 0}</b></div>
          <label>
            <input type="checkbox" checked={onlyOpen} onChange={(event) => setOnlyOpen(event.target.checked)} />
            <span>只看待处理</span>
          </label>
        </div>
      )}
      {inbox.status === 'unavailable' && <div className="warning-line">任务收件箱不可用：{inbox.error}</div>}

      <div className={`decision-policy-gate ${profile?.configured ? 'active' : 'inactive'}`}>
        <ShieldCheck size={19} aria-hidden="true" />
        <div>
          <span>投资政策门禁</span>
          <b>{profile?.configured ? `V${profile.version_no} 已激活` : '尚未激活，仓位动作保持受限'}</b>
          <small>{profile?.configured
            ? `单品 ${profile.max_single_ratio}% · 权益 ${profile.max_equity_ratio}% · 最大回撤 ${profile.max_drawdown_pct}%`
            : '完整设置已移动到“我的资产 → 投资政策”，首页不再堆叠表单。'}</small>
        </div>
        <button className="ghost" onClick={() => onNavigate('profile')}><Settings2 size={15} aria-hidden="true" />{profile?.configured ? '查看政策' : '建立政策'}</button>
      </div>

      {error && <div className="error">{error}</div>}
      {taskError && <div className="error">{taskError}</div>}
      {!error && loading && !data && <div className="decision-loading"><span className="spinner" />正在汇总真实持仓与市场数据</div>}

      {!loading && visibleActions.length > 0 && (
        <div className="decision-actions">
          {visibleActions.map((action) => {
              const priority = PRIORITY_META[action.priority] || PRIORITY_META.normal
              const PriorityIcon = priority.icon
              const CategoryIcon = actionIcon(action.category || '')
              const task = action.task
              const isTaskBusy = taskBusy === task?.id
              return (
              <article className={`decision-action ${action.priority}`} key={action.id}>
                <div className="decision-action-icon"><CategoryIcon size={18} strokeWidth={2} aria-hidden="true" /></div>
                <div className="decision-action-content">
                  <div className="decision-action-meta">
                    <span className={`decision-priority ${action.priority}`}><PriorityIcon size={13} aria-hidden="true" />{priority.label}</span>
                    <span>{action.category}</span>
                    {task && <span className={`decision-task-state ${task.status}`}>{TASK_STATUS_META[task.status] || task.status}</span>}
                  </div>
                  <h4>{action.title}</h4>
                  <p>{action.detail}</p>
                  {action.evidence?.length > 0 && <div className="decision-evidence">{action.evidence.map((item, index) => <span key={`${action.id}-${index}`}>{item}</span>)}</div>}
                  {task?.status === 'snoozed' && <small>稍后至：{formatTime(task.snoozed_until)}</small>}
                  <small>来源：{action.source}</small>
                </div>
                <div className="decision-action-tools">
                  {task?.status === 'open' && (
                    <>
                      <button className="ghost" onClick={() => changeTask(task, 'acknowledged')} disabled={Boolean(taskBusy)} title="确认已知晓" aria-label="确认已知晓">
                        {isTaskBusy ? <RefreshCw size={16} className="spin-icon" aria-hidden="true" /> : <CheckCheck size={17} aria-hidden="true" />}
                      </button>
                      <button className="ghost" onClick={() => changeTask(task, 'snoozed', 24)} disabled={Boolean(taskBusy)} title="稍后 24 小时处理" aria-label="稍后 24 小时处理">
                        <Clock3 size={17} aria-hidden="true" />
                      </button>
                    </>
                  )}
                  {task && ['acknowledged', 'snoozed'].includes(task.status) && (
                    <button className="ghost" onClick={() => changeTask(task, 'open')} disabled={Boolean(taskBusy)} title="重新加入待处理" aria-label="重新加入待处理">
                      {isTaskBusy ? <RefreshCw size={16} className="spin-icon" aria-hidden="true" /> : <RotateCcw size={17} aria-hidden="true" />}
                    </button>
                  )}
                  <button className="ghost decision-open" onClick={() => onNavigate(action.target)} title={action.action_label} aria-label={action.action_label}>
                    <ArrowUpRight size={17} aria-hidden="true" />
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}

      {!loading && filteredActions.length === 0 && actions.length > 0 && (
        <div className="decision-task-empty">当前没有待处理任务；可关闭筛选查看已确认或稍后处理事项。</div>
      )}

      {filteredActions.length > 5 && (
        <button type="button" className="ghost decision-expand" onClick={() => setShowAll((value) => !value)}>
          {showAll ? <ChevronUp size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
          {showAll ? '收起次要事项' : `查看其余 ${filteredActions.length - 5} 项`}
        </button>
      )}

      {historyOpen && (
        <div className="decision-task-history">
          <div className="decision-task-history-head">
            <div><History size={16} aria-hidden="true" /><b>自动解决记录</b></div>
            <span>条件从最新真实证据中消失后写入</span>
          </div>
          {historyLoading && <div className="decision-task-empty"><span className="spinner" />正在读取任务历史</div>}
          {historyError && <div className="error">{historyError}</div>}
          {!historyLoading && !historyError && resolvedHistory.length === 0 && <div className="decision-task-empty">暂无自动解决记录</div>}
          {!historyLoading && resolvedHistory.map((task) => (
            <div className="decision-task-history-row" key={task.id}>
              <span className={`decision-priority ${task.priority}`}>{PRIORITY_META[task.priority]?.label || '研究队列'}</span>
              <div><b>{task.title}</b><small>{task.source}</small></div>
              <time>{formatTime(task.resolved_at)}</time>
            </div>
          ))}
        </div>
      )}

      {unavailable.length > 0 && (
        <details className="decision-unavailable">
          <summary>未参与结论的数据源 · {unavailable.length}</summary>
          <div>{unavailable.slice(0, 6).map((item, index) => <span key={`${item.scope}-${index}`}>{item.scope}: {item.error || '暂不可用'}</span>)}</div>
        </details>
      )}

      <div className="decision-footnote">更新于 {formatTime(data?.generated_at)}。确认仅表示用户已知晓；只有触发条件不再出现在真实证据中，任务才会自动解决。</div>
    </section>
  )
}
