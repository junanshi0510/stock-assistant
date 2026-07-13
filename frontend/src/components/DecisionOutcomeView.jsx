import { useEffect, useState } from 'react'
import { CalendarClock, Database, Play, RefreshCw, Save, Scale } from 'lucide-react'

function nav(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toFixed(4)
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function pp(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}`
}

const STATUS_LABEL = {
  pending: '等待后续确认净值',
  observing: '持续观察中',
  evaluable: '已达到最低观察样本',
}

const ELIGIBILITY_REASON = {
  run_not_terminal: '研究任务尚未形成终态结果',
  unsupported_intent: '当前任务类型不支持结果观察',
  missing_fund_code: '缺少有效基金代码',
  missing_confirmed_nav_baseline: '缺少不可变确认净值基线',
  source_integrity_failed: '原 Run 的 Evidence 或审计链校验失败',
  decision_not_directional: '原决策不是可评分的方向性动作',
}

function time(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

export default function DecisionOutcomeView({
  evaluation,
  loading,
  onEvaluate,
  onOpenEvidence,
  schedule,
  eligibility,
  loadingSchedule,
  onConfigureSchedule,
}) {
  const [intervalHours, setIntervalHours] = useState(24)

  useEffect(() => {
    setIntervalHours(Number(schedule?.interval_hours || 24))
  }, [schedule?.interval_hours])

  const scheduleActive = schedule?.status === 'active'
  const canSchedule = Boolean(eligibility?.eligible)

  return (
    <section className="agent-outcome-panel" aria-label="决策结果评估">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Outcome Evidence</span>
          <h3>决策之后真实发生了什么</h3>
          <small>{evaluation ? `${evaluation.evaluator_id}@${evaluation.evaluator_version}` : '尚未建立结果观察'}</small>
        </div>
        <div className="agent-outcome-actions">
          {evaluation?.evidence_id && (
            <button className="ghost" onClick={() => onOpenEvidence(evaluation.evidence_id)}>
              <Database size={14} aria-hidden="true" />查看 Outcome Evidence
            </button>
          )}
          <button className="ghost" onClick={onEvaluate} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
            {loading ? '评估中' : evaluation ? '按最新确认净值重评' : '建立结果观察'}
          </button>
        </div>
      </div>

      {!evaluation && (
        <div className="agent-outcome-empty">
          <CalendarClock size={18} aria-hidden="true" />
          <span>评估会冻结原 Run 的确认净值和动作，只追加后续真实净值，不修改原结论。</span>
        </div>
      )}

      {evaluation && (
        <>
          <div className="agent-outcome-summary">
            <div><span>评估状态</span><b>{STATUS_LABEL[evaluation.status] || evaluation.status}</b></div>
            <div><span>原决策基线</span><b>{nav(evaluation.baseline?.unit_nav)}</b><small>{evaluation.baseline?.as_of || '-'}</small></div>
            <div><span>最新确认净值</span><b>{nav(evaluation.observed?.unit_nav)}</b><small>{evaluation.observed?.as_of || '暂无后续净值'}</small></div>
            <div><span>单位净值变化</span><b className={Number(evaluation.observed?.return_pct) < 0 ? 'delta-neg' : ''}>{pct(evaluation.observed?.return_pct)}</b><small>未含分红 · {evaluation.observed?.confirmed_nav_count || 0} 个样本</small></div>
          </div>
          <div className="agent-outcome-interpretation">
            <Scale size={16} aria-hidden="true" />
            <div><b>{evaluation.interpretation?.label || '-'}</b><p>{evaluation.interpretation?.reason}</p></div>
          </div>
          <div className={`agent-outcome-peer ${evaluation.peer_comparison?.status || 'unavailable'}`}>
            <div className="agent-outcome-peer-head">
              <div>
                <span>来源原生同类基准</span>
                <b>{evaluation.peer_comparison?.name || '同类平均'}</b>
              </div>
              <small>
                {evaluation.peer_comparison?.status === 'available'
                  ? '日期精确对齐'
                  : evaluation.peer_comparison?.status === 'pending'
                    ? '等待后续净值'
                    : '相对评价不可用'}
              </small>
            </div>
            {evaluation.peer_comparison?.status === 'available' ? (
              <div className="agent-outcome-peer-grid">
                <div><span>同类区间收益</span><b>{pct(evaluation.peer_comparison?.period_return_pct)}</b></div>
                <div><span>基金同口径收益</span><b>{pct(evaluation.peer_comparison?.fund_return_pct)}</b></div>
                <div><span>收益差</span><b>{pp(evaluation.peer_comparison?.return_spread_pp)}</b><small>个百分点</small></div>
                <div><span>相对超额</span><b>{pct(evaluation.peer_comparison?.relative_excess_return_pct)}</b></div>
              </div>
            ) : (
              <p>{evaluation.peer_comparison?.reason || '来源未返回可精确对齐的同类序列，没有选择其他指数替代。'}</p>
            )}
            <p>同类平均不是基金合同业绩基准；单次相对表现不能证明 Agent 具备持续超额能力。</p>
          </div>
          <div className="agent-outcome-milestones">
            {(evaluation.milestones || []).map((item) => (
              <div className={item.status} key={item.confirmed_nav_count}>
                <span>{item.confirmed_nav_count} 个净值样本</span>
                <b>{pct(item.return_pct)}</b>
                <small>{item.as_of || '等待数据'}</small>
                {item.peer_status === 'available' && (
                  <small>同类 {pct(item.peer_return_pct)} · 相对 {pct(item.relative_excess_return_pct)}</small>
                )}
              </div>
            ))}
          </div>
          <p className="agent-outcome-policy">{evaluation.policy}</p>
        </>
      )}

      <div className="agent-outcome-schedule">
        <div className="agent-outcome-schedule-head">
          <div>
            <b>持久化自动观察</b>
            <small>{canSchedule ? '数据库计划 · Worker 租约 · 失败退避' : (ELIGIBILITY_REASON[eligibility?.reason] || '当前 Run 不具备自动观察条件')}</small>
          </div>
          <label className={`toggle-line ${!canSchedule && !schedule ? 'disabled' : ''}`}>
            <input
              type="checkbox"
              checked={scheduleActive}
              disabled={loadingSchedule || (!canSchedule && !schedule)}
              onChange={(event) => onConfigureSchedule({
                enabled: event.target.checked,
                interval_hours: intervalHours,
                run_immediately: false,
              })}
            />
            自动观察
          </label>
        </div>

        {schedule && (
          <div className="agent-outcome-schedule-grid">
            <div><span>计划状态</span><b>{scheduleActive ? '运行中' : '已暂停'}</b></div>
            <div><span>下次执行</span><b>{time(schedule.next_run_at)}</b></div>
            <div><span>最近成功</span><b>{time(schedule.last_success_at)}</b></div>
            <div><span>连续失败</span><b>{schedule.consecutive_failures || 0}</b></div>
          </div>
        )}

        {schedule?.last_error_message && (
          <p className="agent-outcome-schedule-error">
            {schedule.last_error_code}: {schedule.last_error_message}
          </p>
        )}

        {canSchedule && (
          <div className="agent-outcome-schedule-controls">
            <label>
              <span>检查频率</span>
              <select value={intervalHours} onChange={(event) => setIntervalHours(Number(event.target.value))}>
                <option value={24}>每 24 小时</option>
                <option value={48}>每 48 小时</option>
                <option value={72}>每 72 小时</option>
                <option value={168}>每 7 天</option>
              </select>
            </label>
            <button
              className="ghost"
              disabled={loadingSchedule || !scheduleActive}
              onClick={() => onConfigureSchedule({ enabled: true, interval_hours: intervalHours, run_immediately: false })}
            >
              <Save size={14} aria-hidden="true" />保存频率
            </button>
            <button
              className="ghost"
              disabled={loadingSchedule}
              onClick={() => onConfigureSchedule({ enabled: true, interval_hours: intervalHours, run_immediately: true })}
            >
              <Play size={14} aria-hidden="true" />立即排队
            </button>
          </div>
        )}
        <p className="agent-outcome-policy">自动观察只更新真实结果证据，不重写原决策、不推断用户成交，也不会自动下单。</p>
      </div>
    </section>
  )
}
