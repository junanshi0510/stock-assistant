import {
  BrainCircuit,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  LockKeyhole,
  ServerCog,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'

const ACTION_LABELS = {
  consider_tranche: '满足条件后考虑分批',
  hold_review: '持有并定期复核',
  hold_no_add: '持有但暂不加仓',
  wait: '等待条件改善',
  do_not_add: '当前不加仓',
  reduce_exposure: '复核并降低暴露',
  research_only: '仅作研究，不形成仓位动作',
  setup_required: '先完善投资约束',
  strategy_not_released: '策略尚未获准用于决策',
  market_data_required: '等待市场数据补齐',
  exposure_data_required: '先补齐组合穿透数据',
  budget_required: '先确认可用预算',
}

const CONFIDENCE_LABELS = { low: '较低', medium: '中等' }
const DIRECTION_LABELS = {
  positive: '支持',
  negative: '风险',
  mixed: '分化',
  neutral: '中性',
}
const COVERAGE_LABELS = {
  used: '已使用',
  partial: '部分使用',
  unavailable: '不可用',
}
const COVERAGE_NAMES = {
  market: '市场',
  holdings: '底层持仓',
  news: '新闻',
  portfolio: '个人组合',
}

function EvidenceLinks({ ids, onOpenEvidence }) {
  const evidenceIds = [...new Set((ids || []).filter(Boolean))]
  if (!evidenceIds.length) return null
  return (
    <span className="ai-evidence-links">
      {evidenceIds.map((id, index) => (
        <button key={id} type="button" onClick={() => onOpenEvidence(id)} title={`查看 Evidence ${id}`}>
          <Database size={12} aria-hidden="true" />E{index + 1}
        </button>
      ))}
    </span>
  )
}

function Assessment({ item, onOpenEvidence, compact = false }) {
  if (!item) return null
  return (
    <article className={`ai-assessment ${item.direction || 'neutral'} ${compact ? 'compact' : ''}`}>
      <header>
        <div>
          <span>{DIRECTION_LABELS[item.direction] || '研判'} · {item.horizon || '待复核'}</span>
          <h4>{item.title}</h4>
        </div>
        <EvidenceLinks ids={item.evidence_ids} onOpenEvidence={onOpenEvidence} />
      </header>
      <p>{item.assessment}</p>
    </article>
  )
}

function AssessmentGroup({ title, items, icon: Icon, emptyText, onOpenEvidence }) {
  return (
    <section className="ai-assessment-group">
      <div className="ai-group-title"><Icon size={15} aria-hidden="true" /><h4>{title}</h4></div>
      <div className="ai-assessment-list">
        {(items || []).map((item, index) => (
          <Assessment key={`${item.title}-${index}`} item={item} compact onOpenEvidence={onOpenEvidence} />
        ))}
        {(!items || items.length === 0) && <p className="ai-empty-line">{emptyText}</p>}
      </div>
    </section>
  )
}

export function ModelStatusStrip({ status, loading = false, error = '' }) {
  const model = status?.model || {}
  const configured = Boolean(model.configured)
  return (
    <div className={`agent-model-status ${configured ? 'ready' : 'unavailable'}`}>
      <span className="agent-model-status-icon">
        {configured ? <BrainCircuit size={18} aria-hidden="true" /> : <ServerCog size={18} aria-hidden="true" />}
      </span>
      <span className="agent-model-status-copy">
        <b>{loading ? '正在核验模型网关' : configured ? '大模型网关已连接' : '大模型网关未启用'}</b>
        <small>
          {loading
            ? '读取服务器运行配置'
            : configured
              ? `${model.provider || '-'} · ${model.model || '-'} · ${model.data_region || '区域未标注'}`
              : error || model.reason || '服务器不会用模板文本冒充 AI 研判'}
        </small>
      </span>
      <span className="agent-model-status-policy">
        {configured
          ? <><ShieldCheck size={13} aria-hidden="true" />结构化输出</>
          : <><CircleAlert size={13} aria-hidden="true" />仅保留确定性研究</>}
      </span>
    </div>
  )
}

export default function AISynthesisView({ analysis, modelStatus, onOpenEvidence }) {
  if (!analysis) return null
  const available = analysis.status === 'available' && analysis.synthesis
  const provider = analysis.provider || modelStatus?.model || {}
  const historicalConfigurationGap = analysis.reason_code === 'model_not_configured'
    && Boolean(modelStatus?.model?.configured)

  if (!available) {
    return (
      <section className="agent-ai-synthesis unavailable" aria-label="大模型研判状态">
        <div className="ai-synthesis-head">
          <div>
            <span className="eyebrow">AI Evidence Synthesis</span>
            <h3>本轮没有生成大模型研判</h3>
          </div>
          <span className="ai-call-state"><CircleAlert size={14} aria-hidden="true" />未调用或未通过门禁</span>
        </div>
        <div className="ai-unavailable-body">
          <ServerCog size={23} aria-hidden="true" />
          <div>
            <b>{historicalConfigurationGap ? '本轮创建时模型尚未配置；当前大模型网关已经连接。' : analysis.reason || '大模型结果不可用'}</b>
            <p>{historicalConfigurationGap
              ? '历史 Run 与 Evidence 不会被改写；需要创建新的 Run 才会使用当前 DeepSeek 配置。'
              : '确定性基金分析和风险门禁仍可查看；系统没有生成任何兜底 AI 文本。'}</p>
            {analysis.reason_code && <code>{analysis.reason_code}</code>}
          </div>
        </div>
      </section>
    )
  }

  const synthesis = analysis.synthesis
  const plan = synthesis.action_plan || {}
  const usage = analysis.invocation?.usage || {}
  const quality = analysis.quality || {}
  const privateContextUsed = Boolean(provider.private_context_used)

  return (
    <section className="agent-ai-synthesis" aria-label="大模型证据研判">
      <div className="ai-synthesis-head">
        <div>
          <span className="eyebrow">AI Evidence Synthesis</span>
          <h3>大模型证据研判</h3>
        </div>
        <span className="ai-call-state ready"><Sparkles size={14} aria-hidden="true" />真实模型调用</span>
      </div>

      <div className={`ai-decision-band action-${synthesis.action}`}>
        <div className="ai-decision-label">
          <span>本轮允许动作</span>
          <b>{ACTION_LABELS[synthesis.action] || synthesis.action}</b>
        </div>
        <div className="ai-decision-copy">
          <h4>{synthesis.headline}</h4>
          <p>{synthesis.answer}</p>
        </div>
        <div className="ai-confidence">
          <span>置信度</span>
          <b>{CONFIDENCE_LABELS[synthesis.confidence] || synthesis.confidence}</b>
        </div>
      </div>

      <div className="ai-coverage-row" aria-label="模型使用的数据范围">
        {Object.entries(synthesis.coverage || {}).map(([key, value]) => (
          <span className={value} key={key}>
            {value === 'used' ? <CheckCircle2 size={12} aria-hidden="true" /> : <CircleAlert size={12} aria-hidden="true" />}
            {COVERAGE_NAMES[key] || key} {COVERAGE_LABELS[value] || value}
          </span>
        ))}
      </div>

      <div className="ai-core-views">
        <Assessment item={synthesis.market_view} onOpenEvidence={onOpenEvidence} />
        <Assessment item={synthesis.fund_view} onOpenEvidence={onOpenEvidence} />
        <Assessment item={synthesis.portfolio_view} onOpenEvidence={onOpenEvidence} />
      </div>

      <div className="ai-research-grid">
        <AssessmentGroup title="可能催化" items={synthesis.catalysts} icon={Sparkles} emptyText="没有足够证据形成催化判断" onOpenEvidence={onOpenEvidence} />
        <AssessmentGroup title="主要风险" items={synthesis.risks} icon={TriangleAlert} emptyText="风险证据不足" onOpenEvidence={onOpenEvidence} />
        <AssessmentGroup title="反证" items={synthesis.counter_evidence} icon={ShieldCheck} emptyText="本轮没有识别出独立反证" onOpenEvidence={onOpenEvidence} />
        <AssessmentGroup title="未知项" items={synthesis.unknowns} icon={CircleAlert} emptyText="没有额外未知项" onOpenEvidence={onOpenEvidence} />
      </div>

      <section className="ai-action-plan">
        <div className="ai-action-plan-head">
          <div><span>Action Plan</span><h4>{plan.rationale}</h4></div>
          <span><Clock3 size={14} aria-hidden="true" />{plan.review_after_days || '-'} 天后复核</span>
        </div>
        <div className="ai-action-columns">
          <AssessmentGroup title="允许增加的前提" items={plan.add_conditions} icon={CheckCircle2} emptyText="当前没有可验证的增加条件" onOpenEvidence={onOpenEvidence} />
          <AssessmentGroup title="需要降低暴露的条件" items={plan.reduce_conditions} icon={TriangleAlert} emptyText="当前没有新增降低条件" onOpenEvidence={onOpenEvidence} />
          <AssessmentGroup title="结论失效条件" items={plan.invalidation_conditions} icon={CircleAlert} emptyText="未形成额外失效条件" onOpenEvidence={onOpenEvidence} />
        </div>
      </section>

      <footer className="ai-audit-footer">
        <span><BrainCircuit size={13} aria-hidden="true" />{provider.provider}/{provider.model}</span>
        <span><LockKeyhole size={13} aria-hidden="true" />{privateContextUsed ? '已使用聚合组合上下文' : '未向模型发送私有组合'}</span>
        <span><Clock3 size={13} aria-hidden="true" />{analysis.invocation?.latency_ms ? `${analysis.invocation.latency_ms} ms` : '时延未记录'}</span>
        <span><Database size={13} aria-hidden="true" />{quality.evidence_reference_count ?? synthesis.all_evidence_ids?.length ?? 0} 条证据引用</span>
        {(usage.input_tokens != null || usage.output_tokens != null) && (
          <span>Tokens {usage.input_tokens ?? '-'} / {usage.output_tokens ?? '-'}</span>
        )}
        {analysis.evidence_id && (
          <button type="button" onClick={() => onOpenEvidence(analysis.evidence_id)}>
            查看模型调用 Evidence
          </button>
        )}
      </footer>
    </section>
  )
}
