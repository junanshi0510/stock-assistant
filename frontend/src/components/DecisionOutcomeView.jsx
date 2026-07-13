import { CalendarClock, Database, RefreshCw, Scale } from 'lucide-react'

function nav(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toFixed(4)
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

const STATUS_LABEL = {
  pending: '等待后续确认净值',
  observing: '持续观察中',
  evaluable: '已达到最低观察样本',
}

export default function DecisionOutcomeView({ evaluation, loading, onEvaluate, onOpenEvidence }) {
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
            <div><span>基线后绝对变化</span><b className={Number(evaluation.observed?.return_pct) < 0 ? 'delta-neg' : ''}>{pct(evaluation.observed?.return_pct)}</b><small>{evaluation.observed?.confirmed_nav_count || 0} 个确认净值样本</small></div>
          </div>
          <div className="agent-outcome-interpretation">
            <Scale size={16} aria-hidden="true" />
            <div><b>{evaluation.interpretation?.label || '-'}</b><p>{evaluation.interpretation?.reason}</p></div>
          </div>
          <div className="agent-outcome-milestones">
            {(evaluation.milestones || []).map((item) => (
              <div className={item.status} key={item.confirmed_nav_count}>
                <span>{item.confirmed_nav_count} 个净值样本</span>
                <b>{pct(item.return_pct)}</b>
                <small>{item.as_of || '等待数据'}</small>
              </div>
            ))}
          </div>
          <p className="agent-outcome-policy">{evaluation.policy}</p>
        </>
      )}
    </section>
  )
}
