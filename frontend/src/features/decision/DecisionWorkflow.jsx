import { ArrowRight, CheckCircle2, CircleAlert, CircleDashed, LockKeyhole } from 'lucide-react'

const STATE_META = {
  complete: { label: '已完成', icon: CheckCircle2 },
  incomplete: { label: '待完成', icon: CircleDashed },
  blocked: { label: '等待前序', icon: LockKeyhole },
  unavailable: { label: '暂不可用', icon: CircleAlert },
}

export default function DecisionWorkflow({ workflow, onNavigate }) {
  if (!workflow) {
    return (
      <section className="decision-workflow" aria-label="投资决策闭环" aria-busy="true">
        <div className="decision-workflow-head">
          <div>
            <span className="eyebrow">投资决策闭环</span>
            <h3>正在核对决策基础</h3>
            <p>等待持仓、政策、纪律、账本和组合报告的真实状态，不提前生成可执行结论。</p>
          </div>
        </div>
        <div className="decision-workflow-loading"><span className="spinner" />正在读取证据门禁</div>
      </section>
    )
  }

  const stages = workflow?.stages || []
  const next = workflow?.next_action
  const progress = Number(workflow?.progress_pct || 0)

  return (
    <section className="decision-workflow" aria-label="投资决策闭环">
      <div className="decision-workflow-head">
        <div>
          <span className="eyebrow">投资决策闭环</span>
          <h3>{workflow?.decision_ready ? '决策基础已经完整' : '先补齐基础，再让 Agent 给结论'}</h3>
          <p>每一步都绑定真实数据或用户确认版本；前序不完整时，后续阶段不会冒充可执行建议。</p>
        </div>
        <div className="decision-workflow-progress" aria-label={`已完成 ${progress}%`}>
          <b>{workflow?.completed_count ?? 0}/{workflow?.total_count ?? 5}</b>
          <span>已完成</span>
          <div><i style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div>
        </div>
      </div>

      <div className="decision-workflow-stages">
        {stages.map((stage) => {
          const meta = STATE_META[stage.state] || STATE_META.incomplete
          const Icon = meta.icon
          return (
            <button
              type="button"
              className={`decision-workflow-stage ${stage.state}`}
              key={stage.id}
              onClick={() => onNavigate(stage.target)}
            >
              <span className="decision-workflow-order">{String(stage.order).padStart(2, '0')}</span>
              <Icon size={18} aria-hidden="true" />
              <span className="decision-workflow-copy">
                <b>{stage.title}</b>
                <small>{stage.metric}</small>
              </span>
              <span className="decision-workflow-state">{meta.label}</span>
            </button>
          )
        })}
      </div>

      {next ? (
        <div className="decision-next-action">
          <div>
            <span>当前唯一下一步</span>
            <b>{next.title}</b>
            <p>{next.description}</p>
          </div>
          <button type="button" onClick={() => onNavigate(next.target)}>
            {next.action_label}<ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      ) : (
        <div className="decision-next-action ready">
          <div><span>闭环状态</span><b>可以生成基于当前证据的组合研判</b></div>
          <button type="button" onClick={() => onNavigate('agent')}>进入投资 Agent<ArrowRight size={16} aria-hidden="true" /></button>
        </div>
      )}
    </section>
  )
}
