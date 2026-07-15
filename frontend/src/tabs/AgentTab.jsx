import { useEffect, useMemo, useState } from 'react'
import {
  ArrowRight,
  ArrowRightLeft,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Database,
  FileSearch,
  Filter,
  History,
  Layers3,
  MessageSquareText,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  X,
} from 'lucide-react'
import {
  cancelAgentBatch,
  cancelAgentRun,
  createFundResearchBatch,
  createFundResearchRun,
  fetchAgentBatch,
  fetchAgentBatches,
  fetchAgentModelStatus,
  fetchAgentAudit,
  fetchAgentEvidence,
  fetchAgentRun,
  fetchAgentRunComparison,
  fetchAgentRunEvaluations,
  fetchAgentStrategyShadowOutcome,
  fetchAgentOutcomeSchedule,
  fetchAgentRuns,
  configureAgentOutcomeSchedule,
  evaluateAgentRun,
  rerunAgentRun,
} from '../api/agent'
import AssetLevelRecurrenceView from '../components/AssetLevelRecurrenceView'
import FundPeerPersistenceView from '../components/FundPeerPersistenceView'
import PersonalizedDecisionView from '../components/PersonalizedDecisionView'
import FundMarketProfileView from '../components/FundMarketProfileView'
import DecisionOutcomeView from '../components/DecisionOutcomeView'
import AISynthesisView, { ModelStatusStrip } from '../components/AISynthesisView'
import AgentBatchView from '../components/AgentBatchView'

const TERMINAL = new Set(['completed', 'partial', 'failed', 'cancelled', 'abstained'])
const EMPTY_HISTORY_FILTERS = { code: '', status: '' }

function parseBatchCodes(value) {
  const tokens = String(value || '').split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean)
  const invalid = tokens.filter((item) => !/^\d{6}$/.test(item))
  const codes = [...new Set(tokens.filter((item) => /^\d{6}$/.test(item)))]
  return { codes, invalid, duplicateCount: tokens.length - invalid.length - codes.length }
}

const STATUS = {
  queued: ['等待执行', 'queued'],
  running: ['正在研究', 'running'],
  succeeded: ['已完成', 'complete'],
  completed: ['证据完整', 'complete'],
  partial: ['部分完成', 'partial'],
  failed: ['执行失败', 'failed'],
  cancelled: ['已取消', 'cancelled'],
  abstained: ['数据不足', 'partial'],
}

const QUALITY = {
  complete: '完整',
  partial: '部分可用',
  unavailable: '不可用',
}

const STEP_LABELS = {
  'fund.analysis.get': '真实净值与风险分析',
  'fund.estimate.get': '盘中估值核验',
  'fund.disclosure_changes.get': '定期报告披露变化',
  'fund.peer_persistence.get': '同类相对表现持续性',
  'fund.alternatives.get': '同类替代候选',
  'portfolio.context.get': '真实持仓与投资约束',
  'fund.personalized_decision.evaluate': '个人风险门禁与金额策略',
  'fund.market_profile.get': '真实基金投资市场识别',
  'fund.intelligence.get': '底层持仓、板块、新闻与行情情报',
  'llm.fund_decision.synthesize': '大模型证据约束合成',
  'strategy.release.check': '策略注册与发布门禁',
}

const STRATEGY_DECISION = {
  research: ['可继续研究', 'positive', '历史相似条件偏正面，但仍需结合估值、持仓和个人风险约束。'],
  avoid_for_now: ['当前暂缓', 'negative', '历史相似条件偏弱，优先等待条件变化，不因回撤自动加仓。'],
  hold_review: ['等待复核', 'mixed', '历史结果分化，当前没有足够一致的方向优势。'],
  data_required: ['数据不足', 'unavailable', '历史相似样本不足，策略拒绝输出方向。'],
}

const STRATEGY_SIGNAL = {
  positive: '历史分布偏正面',
  negative: '历史分布偏负面',
  mixed: '历史分布分化',
  unavailable: '不可判断',
}

const STRATEGY_CONFIDENCE = {
  medium: '中等',
  low: '较低',
  unavailable: '不可用',
}

const STRATEGY_RELEASE_STATUS = {
  draft: '草稿',
  review: '评审中',
  shadow: 'Shadow 观察',
  canary: '灰度发布',
  active: '正式发布',
  paused: '已暂停',
  retired: '已退役',
  unregistered: '未注册',
}

const SHADOW_ENROLLMENT_STATUS = {
  scheduled: '已入组，等待窗口',
  retry_wait: '真实数据重试中',
  observed: '已完成观测',
  excluded: '非重叠规则排除',
  blocked: '完整性门禁阻断',
}

const CONDITION_LABELS = {
  above_ma60: '净值位于 60 日均值上方',
  below_ma60: '净值位于 60 日均值下方',
  near_high: '接近历史高位',
  normal_pullback: '普通回撤区',
  deep_drawdown: '深回撤区',
}

const HORIZON_LABELS = { '3m': '随后 3 个月', '6m': '随后 6 个月', '12m': '随后 12 个月' }

const COHORT_VEHICLE_LABELS = {
  domestic: '境内基金载体',
  qdii: 'QDII 载体',
  cross_border_non_qdii: '非 QDII 跨境载体',
}

const INVALIDATION_LABELS = {
  trend: '净值与 60 日均值的关系发生变化',
  drawdown_band: '当前回撤跨入新的区间',
  as_of: '发布新的确认净值后需要重新计算',
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`
}

function pp(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)} 个百分点`
}

function metricValue(item) {
  if (item.value == null) return '-'
  if (item.unit === '%') {
    const number = Number(item.value)
    const prefix = number > 0 && /收益|涨跌/.test(item.label || '') && !/比例|胜率/.test(item.label || '') ? '+' : ''
    return `${prefix}${number.toFixed(2)}%`
  }
  if (item.unit === '分') return `${Number(item.value).toFixed(0)} 分`
  return `${Number(item.value).toLocaleString('zh-CN', { maximumFractionDigits: 4 })}${item.unit || ''}`
}

function comparisonValue(value, unit = '') {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (unit === '%') return `${number.toFixed(2)}%`
  if (unit === '分') return `${number.toFixed(0)} 分`
  return `${number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })}${unit}`
}

function ratePct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(2)}%`
}

function comparisonDelta(item) {
  if (item.direction === 'added') return '本次新增'
  if (item.direction === 'removed') return '本次缺失'
  if (item.delta == null || Math.abs(Number(item.delta)) < 1e-9) return '无变化'
  const prefix = Number(item.delta) > 0 ? '+' : ''
  return `${prefix}${comparisonValue(item.delta, item.unit)}`
}

function timeText(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value).replace('T', ' ')
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function statusMeta(value) {
  return STATUS[value] || [value || '未知', 'partial']
}

function StrategyPanel({ strategy, shadowOutcome, onOpenEvidence, personalized = false }) {
  const [decisionLabel, decisionTone, decisionNote] = STRATEGY_DECISION[strategy.decision]
    || STRATEGY_DECISION.data_required
  const condition = strategy.condition || {}
  const coverage = strategy.coverage || {}
  const governance = strategy.governance || {}
  const governedStrategy = governance.strategy || {}
  const execution = governance.execution || {}
  const release = governance.release || {}
  const released = Boolean(execution.decision_use_allowed)
  const shadowEnrollment = shadowOutcome?.enrollment || null
  const shadowSummary = shadowOutcome?.strategy_summary || {}
  const shadowObservation = shadowSummary.observation || {}
  const shadowGate = shadowSummary.disclosure_gate || {}
  const shadowVerification = shadowOutcome?.verification || null
  const shadowEvidence = shadowOutcome?.observations?.[0] || null
  const shadowCohort = shadowOutcome?.cohort || null
  const cohortDimensions = shadowCohort?.dimensions || {}
  const cohortMarket = cohortDimensions.market || {}
  const cohortAsset = cohortDimensions.asset_class || {}
  const cohortVehicle = cohortDimensions.vehicle || {}
  const cohortRegime = cohortDimensions.signal_regime || {}
  const cohortRelease = shadowCohort?.release_classification || {}
  const cohortVerification = shadowOutcome?.cohort_verification || null
  const cohortSummary = shadowSummary.cohort_binding || {}
  const cohortSegments = shadowSummary.segments || []
  return (
    <section className="agent-strategy-panel" aria-label="基金历史条件策略">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Strategy Evidence</span>
          <h3>当前条件的历史前瞻统计</h3>
          <small>{strategy.strategy_id}@{strategy.strategy_version} · 数据截至 {condition.as_of || coverage.end_date || '-'}</small>
        </div>
        <div className="agent-section-actions">
          {strategy.evidence_id && (
            <button className="ghost" onClick={() => onOpenEvidence(strategy.evidence_id)}>
              <Database size={14} aria-hidden="true" />查看策略 Evidence
            </button>
          )}
          {governance.evidence_id && (
            <button className="ghost" onClick={() => onOpenEvidence(governance.evidence_id)}>
              <ShieldCheck size={14} aria-hidden="true" />查看治理 Evidence
            </button>
          )}
        </div>
      </div>

      <div className={`agent-strategy-release ${released ? 'released' : 'restricted'}`}>
        <ShieldCheck size={17} aria-hidden="true" />
        <div>
          <span>生产发布状态</span>
          <b>{STRATEGY_RELEASE_STATUS[governedStrategy.status] || governedStrategy.status || '不可验证'}</b>
          <small>{execution.reason || '没有形成可验证的策略发布快照'}</small>
        </div>
        <div>
          <span>发布检查</span>
          <b>{release.passed_check_count ?? 0} / {release.required_check_count ?? '-'}</b>
          <small>{released ? '允许进入个人决策门禁' : '研究结果不生成投入金额'}</small>
        </div>
      </div>

      <div className={`agent-shadow-enrollment ${shadowEnrollment?.status || 'unavailable'}`}>
        <History size={17} aria-hidden="true" />
        <div>
          <span>Shadow Outcome 入组</span>
          <b>
            {shadowEnrollment
              ? SHADOW_ENROLLMENT_STATUS[shadowEnrollment.status] || shadowEnrollment.status
              : shadowOutcome?.eligibility?.eligible
                ? '等待持久化入组'
                : '当前信号不入组'}
          </b>
          <small>
            {shadowEnrollment
              ? `${shadowEnrollment.baseline_as_of} 基线 · ${shadowEnrollment.horizon} / ${shadowEnrollment.observation_days} 个后续确认净值`
              : shadowOutcome?.eligibility?.reason || '未形成可验证入组状态'}
          </small>
        </div>
        <div>
          <span>可比 Cohort 样本</span>
          <b>{shadowObservation.observed_count ?? 0} 已观测 / {shadowObservation.release_grade_count ?? 0} 完整基准</b>
          <small>
            {shadowGate.aggregate_available
              ? '单一可比 Cohort 达到门槛，仍需人工发布评审'
              : cohortSegments.length > 1
                ? `${cohortSegments.length} 个 Cohort 分开统计，禁止混合总体胜率`
                : '未达门槛，不展示胜率或平均收益'}
          </small>
        </div>
        <div className="agent-shadow-integrity">
          <span>入组完整性</span>
          <b>{shadowVerification?.verified ? '快照与审计已验证' : shadowEnrollment ? '校验未通过' : '-'}</b>
          {shadowEvidence?.evidence_id && (
            <button className="ghost" onClick={() => onOpenEvidence(shadowEvidence.evidence_id)}>
              <Database size={13} aria-hidden="true" />查看 Outcome Evidence
            </button>
          )}
        </div>
      </div>

      <div className={`agent-shadow-cohort ${cohortRelease.eligible ? 'complete' : 'restricted'}`}>
        <Layers3 size={17} aria-hidden="true" />
        <div>
          <span>样本可比性</span>
          <b>{cohortMarket.label || '市场待绑定'} · {cohortAsset.label || '资产类别待绑定'}</b>
          <small>{COHORT_VEHICLE_LABELS[cohortVehicle.type] || cohortVehicle.type || '-'}</small>
        </div>
        <div>
          <span>预测周期与信号状态</span>
          <b>{HORIZON_LABELS[cohortDimensions.horizon?.name] || '-'} · {cohortRegime.trend_label || '-'}</b>
          <small>{cohortRegime.drawdown_label || '-'}</small>
        </div>
        <div>
          <span>Cohort 绑定</span>
          <b>{cohortVerification?.verified ? 'Evidence 与审计已验证' : shadowCohort ? '绑定校验未通过' : '等待自动绑定'}</b>
          <small>
            {cohortRelease.eligible
              ? `分类完整 · 当前版本已绑定 ${cohortSummary.bound_count ?? 0} 条`
              : (cohortRelease.reasons || []).join('、') || '当前不可进入绩效披露'}
          </small>
        </div>
        {shadowCohort?.evidence_id && (
          <button className="ghost" onClick={() => onOpenEvidence(shadowCohort.evidence_id)}>
            <Database size={13} aria-hidden="true" />查看 Cohort Evidence
          </button>
        )}
      </div>

      <div className="agent-strategy-summary">
        <div className={`decision ${decisionTone}`}>
          <span>策略研究判断</span><b>{decisionLabel}</b><small>{decisionNote}</small>
        </div>
        <div><span>历史方向</span><b>{STRATEGY_SIGNAL[strategy.signal?.direction] || '-'}</b><small>方向一致度 {strategy.signal?.strength ?? '-'} / 100</small></div>
        <div><span>统计置信度</span><b>{STRATEGY_CONFIDENCE[strategy.confidence?.level] || '-'}</b><small>最高仅标记中等，不把重叠样本当独立预测</small></div>
        <div><span>主观察窗口</span><b>{HORIZON_LABELS[strategy.primary_horizon] || '暂不可用'}</b><small>优先使用 6 个月，样本不足时按规则降级</small></div>
      </div>

      <div className="agent-strategy-condition">
        <div><span>趋势条件</span><b>{CONDITION_LABELS[condition.trend] || '-'}</b></div>
        <div><span>回撤条件</span><b>{CONDITION_LABELS[condition.drawdown_band] || '-'}</b></div>
        <div><span>当前回撤</span><b>{pct(condition.current_drawdown)}</b></div>
        <div><span>近 3 月</span><b>{pct(condition.return_3m)}</b></div>
        <div><span>净值 / 60 日均值</span><b>{condition.latest_nav ?? '-'} / {condition.ma60 ?? '-'}</b></div>
      </div>

      <div className="agent-strategy-horizons">
        {(strategy.horizons || []).map((item) => {
          const analog = item.analog || {}
          const baseline = item.baseline || {}
          return (
            <article className={item.status === 'available' ? '' : 'insufficient'} key={item.horizon}>
              <header>
                <div><span>{HORIZON_LABELS[item.horizon] || item.horizon}</span><b>{item.status === 'available' ? `${analog.sample_count} 个相似月末样本` : `仅 ${analog.sample_count || 0} 个样本`}</b></div>
                <em>{item.status === 'available' ? '可评估' : '样本不足'}</em>
              </header>
              <dl>
                <div><dt>历史正收益比例</dt><dd>{ratePct(analog.positive_rate)}</dd></div>
                <div><dt>历史中位收益</dt><dd>{pct(analog.median_return)}</dd></div>
                <div><dt>中间 50% 区间</dt><dd>{pct(analog.p25_return)} 至 {pct(analog.p75_return)}</dd></div>
                <div><dt>历史最差结果</dt><dd>{pct(analog.worst_return)}</dd></div>
                <div><dt>无条件基准正收益比例</dt><dd>{ratePct(baseline.positive_rate)}</dd></div>
                <div><dt>相对基准差</dt><dd>{pct(item.edge?.positive_rate)}</dd></div>
              </dl>
              <small>信号样本区间 {analog.sample_start || '-'} 至 {analog.sample_end || '-'}</small>
            </article>
          )
        })}
      </div>

      <div className="agent-strategy-gates">
        <div>
          <h4>策略何时失效</h4>
          {(strategy.invalidation_conditions || []).map((item) => (
            <p key={item.field}><CircleAlert size={13} aria-hidden="true" />{INVALIDATION_LABELS[item.field] || item.field}</p>
          ))}
        </div>
        <div>
          <h4>适用性缺口</h4>
          <p>
            <ShieldCheck size={13} aria-hidden="true" />
            {personalized
              ? '个人风险、期限和仓位约束已由上方决策策略单独校验'
              : '尚未应用你的风险偏好、预算、已有仓位和组合重合度'}
          </p>
          <p><ShieldCheck size={13} aria-hidden="true" />历史月末样本的前瞻窗口可能重叠，不能当作独立预测次数</p>
        </div>
      </div>
      <p className="agent-strategy-policy">
        这里只回答“过去处于相同趋势和回撤区间后发生过什么”，不回答未来一定涨跌；历史收益不代表未来表现。
      </p>
    </section>
  )
}

function StepState({ step }) {
  const [label, tone] = statusMeta(step.status)
  const Icon = step.status === 'succeeded'
    ? CheckCircle2
    : step.status === 'failed'
      ? CircleAlert
      : step.status === 'cancelled'
        ? Square
        : RefreshCw
  return (
    <div className="agent-step">
      <span className={`agent-step-icon ${tone}`}>
        <Icon size={16} className={step.status === 'running' ? 'spin-icon' : ''} aria-hidden="true" />
      </span>
      <div>
        <b>{STEP_LABELS[step.tool_name] || step.tool_name}</b>
        <small>{step.tool_name}@{step.tool_version}</small>
        {step.error_message && <p>{step.error_message}</p>}
      </div>
      <span className={`agent-status ${tone}`}>{label}</span>
    </div>
  )
}

export default function AgentTab() {
  const [researchMode, setResearchMode] = useState('single')
  const [code, setCode] = useState('001480')
  const [batchCodes, setBatchCodes] = useState('')
  const [months, setMonths] = useState(60)
  const [includeEstimate, setIncludeEstimate] = useState(false)
  const [includeDisclosure, setIncludeDisclosure] = useState(true)
  const [includeAlternatives, setIncludeAlternatives] = useState(true)
  const [includeMarketIntelligence, setIncludeMarketIntelligence] = useState(true)
  const [includeAiSynthesis, setIncludeAiSynthesis] = useState(true)
  const [includePortfolioContext, setIncludePortfolioContext] = useState(true)
  const [question, setQuestion] = useState('结合未来 3-12 个月的市场、底层持仓、新闻和我的组合约束，我现在应该如何管理这只基金？')
  const [plannedAmount, setPlannedAmount] = useState('')
  const [run, setRun] = useState(null)
  const [batch, setBatch] = useState(null)
  const [batchHistory, setBatchHistory] = useState([])
  const [loadingBatch, setLoadingBatch] = useState(false)
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState({ items: [], next_cursor: null, has_more: false })
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyFilterDraft, setHistoryFilterDraft] = useState(EMPTY_HISTORY_FILTERS)
  const [historyFilters, setHistoryFilters] = useState(EMPTY_HISTORY_FILTERS)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [error, setError] = useState('')
  const [modelStatus, setModelStatus] = useState(null)
  const [modelStatusError, setModelStatusError] = useState('')
  const [loadingModelStatus, setLoadingModelStatus] = useState(true)
  const [selectedEvidence, setSelectedEvidence] = useState(null)
  const [loadingEvidence, setLoadingEvidence] = useState(false)
  const [audit, setAudit] = useState(null)
  const [loadingAudit, setLoadingAudit] = useState(false)
  const [comparison, setComparison] = useState(null)
  const [loadingComparison, setLoadingComparison] = useState(false)
  const [evaluations, setEvaluations] = useState([])
  const [loadingEvaluation, setLoadingEvaluation] = useState(false)
  const [outcomeSchedule, setOutcomeSchedule] = useState(null)
  const [outcomeEligibility, setOutcomeEligibility] = useState(null)
  const [loadingOutcomeSchedule, setLoadingOutcomeSchedule] = useState(false)
  const [strategyShadowOutcome, setStrategyShadowOutcome] = useState(null)
  const batchCodeState = useMemo(() => parseBatchCodes(batchCodes), [batchCodes])

  async function loadModelStatus() {
    setLoadingModelStatus(true)
    try {
      const data = await fetchAgentModelStatus()
      setModelStatus(data)
      setModelStatusError('')
    } catch (requestError) {
      setModelStatus(null)
      setModelStatusError(requestError.message || '模型网关状态获取失败')
    } finally {
      setLoadingModelStatus(false)
    }
  }

  async function loadBatch(batchId, { quiet = false } = {}) {
    if (!batchId) return
    if (!quiet) setLoadingBatch(true)
    try {
      const data = await fetchAgentBatch(batchId)
      setBatch(data)
      setError('')
      localStorage.setItem('investment-agent-batch-id', data.id)
    } catch (requestError) {
      if (!quiet) setError(requestError.message || '批量 Agent 任务获取失败')
    } finally {
      if (!quiet) setLoadingBatch(false)
    }
  }

  async function loadBatchHistory() {
    try {
      const data = await fetchAgentBatches({ limit: 6 })
      setBatchHistory(data.items || [])
    } catch (requestError) {
      setError(requestError.message || '批量 Agent 历史获取失败')
    }
  }

  async function loadRun(runId, { quiet = false } = {}) {
    if (!runId) return
    if (!quiet) {
      setLoading(true)
      setComparison(null)
      setStrategyShadowOutcome(null)
    }
    try {
      const data = await fetchAgentRun(runId)
      setRun(data)
      setError('')
      localStorage.setItem('investment-agent-run-id', data.id)
    } catch (requestError) {
      if (!quiet) setError(requestError.message || 'Agent Run 获取失败')
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  async function loadHistory({ append = false, cursor = '', filters = historyFilters } = {}) {
    setLoadingHistory(true)
    try {
      const data = await fetchAgentRuns({
        limit: 6,
        cursor,
        code: filters.code,
        status: filters.status,
      })
      setHistory((current) => ({
        ...data,
        items: append ? [...current.items, ...(data.items || [])] : (data.items || []),
      }))
    } catch (requestError) {
      setError(requestError.message || 'Agent 历史任务获取失败')
    } finally {
      setLoadingHistory(false)
    }
  }

  async function loadEvaluations(runId) {
    if (!runId) return
    try {
      const data = await fetchAgentRunEvaluations(runId)
      setEvaluations(data.items || [])
    } catch (requestError) {
      setError(requestError.message || '决策结果评估记录获取失败')
    }
  }

  async function loadOutcomeSchedule(runId) {
    if (!runId) return
    try {
      const data = await fetchAgentOutcomeSchedule(runId)
      setOutcomeSchedule(data.schedule || null)
      setOutcomeEligibility(data.eligibility || null)
    } catch (requestError) {
      setError(requestError.message || '自动结果观察计划获取失败')
    }
  }

  async function loadStrategyShadowOutcome(runId) {
    if (!runId) return
    try {
      setStrategyShadowOutcome(await fetchAgentStrategyShadowOutcome(runId))
    } catch (requestError) {
      setError(requestError.message || '策略 Shadow Outcome 状态获取失败')
    }
  }

  function applyHistoryFilters(event) {
    event.preventDefault()
    const cleanCode = historyFilterDraft.code.trim()
    if (cleanCode && !/^\d{6}$/.test(cleanCode)) {
      setError('历史筛选的基金代码需要是 6 位数字')
      return
    }
    const next = { code: cleanCode, status: historyFilterDraft.status }
    setHistoryFilterDraft(next)
    setHistoryFilters(next)
    setError('')
    loadHistory({ filters: next })
  }

  function clearHistoryFilters() {
    const next = { ...EMPTY_HISTORY_FILTERS }
    setHistoryFilterDraft(next)
    setHistoryFilters(next)
    setError('')
    loadHistory({ filters: next })
  }

  useEffect(() => {
    const savedMode = localStorage.getItem('investment-agent-mode')
    if (savedMode === 'batch') setResearchMode('batch')
    const savedRunId = localStorage.getItem('investment-agent-run-id')
    if (savedRunId) loadRun(savedRunId)
    const savedBatchId = localStorage.getItem('investment-agent-batch-id')
    if (savedBatchId) loadBatch(savedBatchId)
    loadHistory()
    loadBatchHistory()
    loadModelStatus()
  }, [])

  useEffect(() => {
    if (!run?.id || TERMINAL.has(run.status)) return undefined
    const timer = window.setInterval(() => loadRun(run.id, { quiet: true }), 1200)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status])

  useEffect(() => {
    if (!batch?.id || TERMINAL.has(batch.status)) return undefined
    const timer = window.setInterval(() => loadBatch(batch.id, { quiet: true }), 1500)
    return () => window.clearInterval(timer)
  }, [batch?.id, batch?.status])

  useEffect(() => {
    if (!batch?.id || !TERMINAL.has(batch.status)) return
    loadBatchHistory()
    loadHistory()
  }, [batch?.id, batch?.status])

  useEffect(() => {
    if (run?.id && TERMINAL.has(run.status)) {
      loadHistory()
      loadEvaluations(run.id)
      loadOutcomeSchedule(run.id)
      loadStrategyShadowOutcome(run.id)
    } else if (run?.id) {
      setEvaluations([])
      setOutcomeSchedule(null)
      setOutcomeEligibility(null)
      setStrategyShadowOutcome(null)
    }
  }, [run?.id, run?.status])

  useEffect(() => {
    if (!run?.id || !strategyShadowOutcome?.eligibility?.eligible || strategyShadowOutcome?.enrollment) return undefined
    const timer = window.setInterval(() => loadStrategyShadowOutcome(run.id), 5000)
    return () => window.clearInterval(timer)
  }, [run?.id, strategyShadowOutcome?.eligibility?.eligible, strategyShadowOutcome?.enrollment?.id])

  useEffect(() => {
    if (!run?.id || outcomeSchedule?.status !== 'active') return undefined
    const timer = window.setInterval(() => {
      loadOutcomeSchedule(run.id)
      loadEvaluations(run.id)
      loadRun(run.id, { quiet: true })
    }, 15000)
    return () => window.clearInterval(timer)
  }, [run?.id, outcomeSchedule?.status])

  const progress = useMemo(() => {
    if (!run) return { completed: 0, total: 0 }
    const completed = (run.steps || []).filter((item) => ['succeeded', 'partial', 'failed'].includes(item.status)).length
    const requested = 3 + (run.input?.include_portfolio_context === false ? 0 : 3)
      + Number(Boolean(run.input?.include_estimate))
      + Number(Boolean(run.input?.include_disclosure_changes))
      + Number(Boolean(run.input?.include_alternatives))
      + Number(Boolean(run.input?.include_market_intelligence))
      + Number(Boolean(run.input?.include_ai_synthesis))
    return { completed, total: Math.max(requested, run.steps?.length || 0) }
  }, [run])

  async function startResearch() {
    const clean = code.trim()
    if (!/^\d{6}$/.test(clean)) {
      setError('请输入 6 位基金代码')
      return
    }
    const cleanQuestion = question.replace(/\s+/g, ' ').trim()
    if (cleanQuestion.length < 8) {
      setError('请用至少 8 个字符说明本次要解决的投资问题')
      return
    }
    setLoading(true)
    setError('')
    setBatch(null)
    setRun(null)
    setSelectedEvidence(null)
    setAudit(null)
    setComparison(null)
    setEvaluations([])
    setOutcomeSchedule(null)
    setOutcomeEligibility(null)
    setStrategyShadowOutcome(null)
    try {
      const data = await createFundResearchRun({
        code: clean,
        months: Number(months),
        include_estimate: includeEstimate,
        include_disclosure_changes: includeDisclosure,
        include_alternatives: includeAlternatives,
        include_market_intelligence: includeMarketIntelligence,
        include_ai_synthesis: includeAiSynthesis,
        include_portfolio_context: includePortfolioContext,
        question: cleanQuestion,
        planned_amount: plannedAmount === '' ? null : Number(plannedAmount),
        alternative_limit: 5,
      })
      setRun(data.run)
      localStorage.setItem('investment-agent-run-id', data.run.id)
      loadHistory()
    } catch (requestError) {
      setError(requestError.message || '基金研究任务创建失败')
    } finally {
      setLoading(false)
    }
  }

  async function startBatchResearch() {
    const { codes, invalid } = batchCodeState
    if (invalid.length > 0) {
      setError(`无法识别这些基金代码：${invalid.slice(0, 3).join('、')}。请只输入 6 位基金代码。`)
      return
    }
    if (codes.length < 2 || codes.length > 6) {
      setError('一次批量研究需要 2-6 只不同基金。')
      return
    }
    const cleanQuestion = question.replace(/\s+/g, ' ').trim()
    if (cleanQuestion.length < 8) {
      setError('请用至少 8 个字符说明本次要解决的投资问题。')
      return
    }
    setLoadingBatch(true)
    setError('')
    setBatch(null)
    setRun(null)
    setSelectedEvidence(null)
    setAudit(null)
    setComparison(null)
    setEvaluations([])
    setOutcomeSchedule(null)
    setOutcomeEligibility(null)
    setStrategyShadowOutcome(null)
    try {
      const data = await createFundResearchBatch({
        codes,
        months: Number(months),
        include_estimate: includeEstimate,
        include_disclosure_changes: includeDisclosure,
        include_alternatives: includeAlternatives,
        include_market_intelligence: includeMarketIntelligence,
        include_ai_synthesis: includeAiSynthesis,
        include_portfolio_context: includePortfolioContext,
        question: cleanQuestion,
        alternative_limit: 5,
      })
      setBatch(data.batch)
      localStorage.setItem('investment-agent-batch-id', data.batch.id)
      loadBatchHistory()
      loadHistory()
    } catch (requestError) {
      setError(requestError.message || '批量基金研究任务创建失败')
    } finally {
      setLoadingBatch(false)
    }
  }

  async function cancelBatch() {
    if (!batch?.id) return
    setLoadingBatch(true)
    try {
      const data = await cancelAgentBatch(batch.id)
      setBatch(data.batch)
      setError('')
    } catch (requestError) {
      setError(requestError.message || '取消批量 Agent 任务失败')
    } finally {
      setLoadingBatch(false)
    }
  }

  async function cancelRun() {
    if (!run?.id) return
    try {
      await cancelAgentRun(run.id)
      await loadRun(run.id, { quiet: true })
    } catch (requestError) {
      setError(requestError.message || '取消 Agent Run 失败')
    }
  }

  async function rerunCurrent() {
    if (!run?.id || !TERMINAL.has(run.status)) return
    setLoading(true)
    setError('')
    setSelectedEvidence(null)
    setAudit(null)
    setComparison(null)
    setEvaluations([])
    setOutcomeSchedule(null)
    setOutcomeEligibility(null)
    setStrategyShadowOutcome(null)
    try {
      const data = await rerunAgentRun(run.id)
      setRun(data.run)
      localStorage.setItem('investment-agent-run-id', data.run.id)
      loadHistory()
    } catch (requestError) {
      setError(requestError.message || 'Agent 任务重新运行失败')
    } finally {
      setLoading(false)
    }
  }

  async function openEvidence(evidenceId) {
    if (!run?.id || !evidenceId) return
    setLoadingEvidence(true)
    try {
      setSelectedEvidence(await fetchAgentEvidence(run.id, evidenceId))
    } catch (requestError) {
      setError(requestError.message || 'Evidence 获取失败')
    } finally {
      setLoadingEvidence(false)
    }
  }

  async function openAudit() {
    if (!run?.id) return
    setLoadingAudit(true)
    try {
      setAudit(await fetchAgentAudit(run.id))
    } catch (requestError) {
      setError(requestError.message || '审计链获取失败')
    } finally {
      setLoadingAudit(false)
    }
  }

  async function openComparison() {
    if (!run?.id || !run.parent_run_id || !TERMINAL.has(run.status)) return
    setLoadingComparison(true)
    setError('')
    try {
      setComparison(await fetchAgentRunComparison(run.id))
    } catch (requestError) {
      setError(requestError.message || '重跑结果对比失败')
    } finally {
      setLoadingComparison(false)
    }
  }

  async function evaluateOutcome() {
    if (!run?.id || !result || !TERMINAL.has(run.status)) return
    setLoadingEvaluation(true)
    setError('')
    try {
      const data = await evaluateAgentRun(run.id)
      setEvaluations((current) => {
        const withoutCurrent = current.filter((item) => item.evidence_id !== data.evaluation.evidence_id)
        return [data.evaluation, ...withoutCurrent]
      })
      setAudit(null)
      await loadRun(run.id, { quiet: true })
    } catch (requestError) {
      setError(requestError.message || '真实确认净值结果评估失败')
    } finally {
      setLoadingEvaluation(false)
    }
  }

  async function configureOutcomeSchedule(payload) {
    if (!run?.id || !TERMINAL.has(run.status)) return
    setLoadingOutcomeSchedule(true)
    setError('')
    try {
      const data = await configureAgentOutcomeSchedule(run.id, payload)
      setOutcomeSchedule(data.schedule || null)
      setOutcomeEligibility(data.eligibility || null)
      setAudit(null)
      await loadRun(run.id, { quiet: true })
    } catch (requestError) {
      setError(requestError.message || '自动结果观察计划更新失败')
    } finally {
      setLoadingOutcomeSchedule(false)
    }
  }

  const result = run?.result
  const [runStatusLabel, runStatusTone] = statusMeta(run?.status)
  const hasHistoryFilters = Boolean(
    historyFilters.code
    || historyFilters.status
    || historyFilterDraft.code
    || historyFilterDraft.status,
  )

  return (
    <div className="agent-workspace">
      <section className="agent-heading">
        <div>
          <span className="eyebrow">Evidence-bound Investment Agent</span>
          <h2>基金决策研究台</h2>
          <p>让模型解释真实市场、底层持仓、新闻与个人组合证据；仓位和动作仍由确定性风险门禁控制。</p>
        </div>
        <div className="agent-readonly-badge"><ShieldCheck size={16} aria-hidden="true" />R0 公共数据 · R1 私有只读</div>
      </section>

      <ModelStatusStrip
        status={modelStatus}
        loading={loadingModelStatus}
        error={modelStatusError}
      />

      <section className="panel agent-launcher" aria-label="创建基金研究任务">
        <div className="agent-launcher-head">
          <div><span className="eyebrow">New Decision Run</span><h3>发起一次可审计研判</h3></div>
          <button className="ghost" type="button" onClick={loadModelStatus} disabled={loadingModelStatus} title="重新核验模型网关">
            <RefreshCw size={14} className={loadingModelStatus ? 'spin-icon' : ''} aria-hidden="true" />核验模型
          </button>
        </div>
        <div className="agent-research-mode" role="tablist" aria-label="基金研究模式">
          <button
            type="button"
            role="tab"
            aria-selected={researchMode === 'single'}
            className={researchMode === 'single' ? 'active' : ''}
            onClick={() => {
              setResearchMode('single')
              localStorage.setItem('investment-agent-mode', 'single')
            }}
          ><FileSearch size={15} aria-hidden="true" />单基金</button>
          <button
            type="button"
            role="tab"
            aria-selected={researchMode === 'batch'}
            className={researchMode === 'batch' ? 'active' : ''}
            onClick={() => {
              setResearchMode('batch')
              localStorage.setItem('investment-agent-mode', 'batch')
            }}
          ><Layers3 size={15} aria-hidden="true" />批量基金</button>
        </div>

        {researchMode === 'single' ? (
          <div className="form-row">
            <label className="field">
              <span>基金代码</span>
              <input value={code} inputMode="numeric" maxLength={6} onChange={(event) => setCode(event.target.value)} placeholder="例如 001480" />
            </label>
            <label className="field">
              <span>净值研究窗口</span>
              <select value={months} onChange={(event) => setMonths(Number(event.target.value))}>
                <option value={12}>12 个月</option>
                <option value={24}>24 个月</option>
                <option value={36}>36 个月</option>
                <option value={60}>60 个月</option>
              </select>
            </label>
            <label className="field">
              <span>本次计划投入</span>
              <input
                value={plannedAmount}
                type="number"
                min="0"
                step="100"
                placeholder="未填则用月度预算"
                onChange={(event) => setPlannedAmount(event.target.value)}
              />
            </label>
            <button onClick={startResearch} disabled={loading || (run && !TERMINAL.has(run.status))}>
              <Play size={16} aria-hidden="true" />
              <span>{loading ? '正在创建' : '开始研究'}</span>
            </button>
          </div>
        ) : (
          <div className="agent-batch-launcher">
            <label className="agent-batch-code-field">
              <span>基金代码 · 每行一个，最多 6 只</span>
              <textarea
                value={batchCodes}
                inputMode="numeric"
                rows={3}
                onChange={(event) => setBatchCodes(event.target.value)}
                placeholder={'例如：\n013403\n014089\n001056'}
              />
            </label>
            <div className="agent-batch-code-preview" aria-live="polite">
              <div>
                {batchCodeState.codes.map((item) => <span key={item}>{item}</span>)}
                {batchCodeState.codes.length === 0 && <small>支持换行、空格或逗号分隔</small>}
              </div>
              <b>{batchCodeState.codes.length}/6</b>
              {batchCodeState.duplicateCount > 0 && <small>已自动去除 {batchCodeState.duplicateCount} 个重复代码</small>}
              {batchCodeState.invalid.length > 0 && <small className="invalid">存在无法识别的内容</small>}
            </div>
            <div className="form-row agent-batch-settings">
              <label className="field">
                <span>统一研究窗口</span>
                <select value={months} onChange={(event) => setMonths(Number(event.target.value))}>
                  <option value={12}>12 个月</option>
                  <option value={24}>24 个月</option>
                  <option value={36}>36 个月</option>
                  <option value={60}>60 个月</option>
                </select>
              </label>
              <div className="agent-batch-budget-policy">
                <ShieldCheck size={16} aria-hidden="true" />
                <span><b>批次不复制单笔金额</b><small>逐只研究后再由组合风险门禁决定金额</small></span>
              </div>
              <button
                onClick={startBatchResearch}
                disabled={
                  loadingBatch
                  || (batch && !TERMINAL.has(batch.status))
                  || batchCodeState.codes.length < 2
                  || batchCodeState.codes.length > 6
                  || batchCodeState.invalid.length > 0
                }
              >
                <Play size={16} aria-hidden="true" />
                <span>{loadingBatch
                  ? '正在创建'
                  : batchCodeState.codes.length < 2
                    ? '至少输入 2 只基金'
                    : `研究 ${batchCodeState.codes.length} 只基金`}</span>
              </button>
            </div>
          </div>
        )}
        <label className="agent-question-field">
          <span><MessageSquareText size={14} aria-hidden="true" />这次要解决的投资问题</span>
          <textarea
            value={question}
            maxLength={500}
            rows={3}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={researchMode === 'batch'
              ? '例如：比较这些基金的风险、底层持仓重合和当前证据，分别应该持有、等待还是降低暴露？'
              : '例如：我已经持有这只基金，未来 6 个月应继续持有、分批增加，还是降低暴露？'}
          />
          <small>{question.trim().length}/500</small>
        </label>
        <div className="agent-options" aria-label="研究范围">
          <label><input type="checkbox" checked={includePortfolioContext} onChange={(event) => setIncludePortfolioContext(event.target.checked)} />应用真实持仓与约束</label>
          <label><input type="checkbox" checked={includeMarketIntelligence} onChange={(event) => setIncludeMarketIntelligence(event.target.checked)} />市场、板块与新闻</label>
          <label><input type="checkbox" checked={includeAiSynthesis} onChange={(event) => setIncludeAiSynthesis(event.target.checked)} />大模型证据研判</label>
          <label><input type="checkbox" checked={includeEstimate} onChange={(event) => setIncludeEstimate(event.target.checked)} />盘中估值核验</label>
          <label><input type="checkbox" checked={includeDisclosure} onChange={(event) => setIncludeDisclosure(event.target.checked)} />披露变化</label>
          <label><input type="checkbox" checked={includeAlternatives} onChange={(event) => setIncludeAlternatives(event.target.checked)} />同类替代品</label>
        </div>
      </section>

      {(batch || batchHistory.length > 0) && (
        <section className="agent-batch-history" aria-label="批量研究历史">
          <div className="agent-section-head">
            <div><span className="eyebrow">Batch History</span><h3>最近批量任务</h3></div>
            <button className="ghost" type="button" onClick={loadBatchHistory} title="刷新批量任务">
              <RefreshCw size={14} aria-hidden="true" />刷新
            </button>
          </div>
          <div className="agent-batch-history-list">
            {batchHistory.map((item) => {
              const [label, tone] = statusMeta(item.status)
              return (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === batch?.id ? 'selected' : ''}
                  onClick={() => {
                    setResearchMode('batch')
                    localStorage.setItem('investment-agent-mode', 'batch')
                    loadBatch(item.id)
                  }}
                >
                  <Layers3 size={15} aria-hidden="true" />
                  <span><b>{(item.items || []).map((row) => row.code).join(' · ')}</b><small>{timeText(item.created_at)}</small></span>
                  <em className={`agent-status ${tone}`}>{label} {item.progress?.terminal || 0}/{item.progress?.total || 0}</em>
                </button>
              )
            })}
          </div>
        </section>
      )}

      <AgentBatchView
        batch={batch}
        loading={loadingBatch}
        selectedRunId={run?.id || ''}
        onRefresh={() => loadBatch(batch?.id)}
        onCancel={cancelBatch}
        onSelectRun={loadRun}
      />

      <section className="agent-history-panel" aria-label="Agent 运行历史">
        <div className="agent-section-head">
          <div><span className="eyebrow">Run History</span><h3>最近研究任务 <small>{history.items.length}</small></h3></div>
          <div className="agent-section-actions">
            {historyOpen && (
              <button className="ghost" onClick={() => loadHistory()} disabled={loadingHistory} title="刷新历史任务">
                <RefreshCw size={15} className={loadingHistory ? 'spin-icon' : ''} aria-hidden="true" />
                <span>{loadingHistory ? '刷新中' : '刷新'}</span>
              </button>
            )}
            <button className="ghost" type="button" onClick={() => setHistoryOpen((current) => !current)} aria-expanded={historyOpen}>
              {historyOpen ? <ChevronUp size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
              {historyOpen ? '收起' : '查看历史'}
            </button>
          </div>
        </div>
        {historyOpen && (
          <>
        <form className="agent-history-filters" onSubmit={applyHistoryFilters}>
          <label>
            <span>基金代码</span>
            <input
              value={historyFilterDraft.code}
              inputMode="numeric"
              maxLength={6}
              placeholder="6 位代码"
              onChange={(event) => setHistoryFilterDraft((current) => ({ ...current, code: event.target.value }))}
            />
          </label>
          <label>
            <span>任务状态</span>
            <select
              value={historyFilterDraft.status}
              onChange={(event) => setHistoryFilterDraft((current) => ({ ...current, status: event.target.value }))}
            >
              <option value="">全部状态</option>
              <option value="queued">等待执行</option>
              <option value="running">正在研究</option>
              <option value="completed">证据完整</option>
              <option value="partial">部分完成</option>
              <option value="failed">执行失败</option>
              <option value="cancelled">已取消</option>
              <option value="abstained">数据不足</option>
            </select>
          </label>
          <div className="agent-history-filter-actions">
            <button type="submit" disabled={loadingHistory}>
              <Filter size={14} aria-hidden="true" />筛选
            </button>
            <button type="button" className="ghost" onClick={clearHistoryFilters} disabled={loadingHistory || !hasHistoryFilters}>
              <X size={14} aria-hidden="true" />清除
            </button>
          </div>
        </form>
        <div className="agent-history-list">
          {history.items.map((item) => {
            const [label, tone] = statusMeta(item.status)
            const selected = item.id === run?.id
            return (
              <button
                key={item.id}
                className={selected ? 'selected' : ''}
                onClick={() => loadRun(item.id)}
                aria-current={selected ? 'true' : undefined}
              >
                <span className="agent-history-main">
                  <b>{item.summary?.code || item.input?.code || '-'} {item.summary?.name || '基金研究'}</b>
                  <small>{item.input?.batch_id ? '批次 · ' : item.parent_run_id ? '重跑 · ' : ''}{timeText(item.completed_at || item.created_at)}</small>
                </span>
                <span className={`agent-status ${tone}`}>{label}</span>
              </button>
            )
          })}
          {!loadingHistory && history.items.length === 0 && (
            <div className="agent-history-empty">
              <History size={16} aria-hidden="true" />
              {historyFilters.code || historyFilters.status ? '没有符合筛选条件的任务' : '还没有研究任务'}
            </div>
          )}
        </div>
        {history.has_more && (
          <button
            className="ghost agent-history-more"
            onClick={() => loadHistory({ append: true, cursor: history.next_cursor })}
            disabled={loadingHistory}
          >
            <History size={14} aria-hidden="true" />加载更早任务
          </button>
        )}
          </>
        )}
      </section>

      {error && <div className="error">{error}</div>}

      {run && (
        <section className="agent-run-band" aria-live="polite">
          <div className="agent-run-head">
            <div>
              <span className={`agent-status ${runStatusTone}`}>{runStatusLabel}</span>
              <h3>{result?.fund ? `${result.fund.code} ${result.fund.name}` : `${run.input?.code || '-'} 基金研究`}</h3>
              <small>Run ID: {run.id}</small>
              {run.input?.batch_id && <small>所属 Batch: {run.input.batch_id}</small>}
              {run.parent_run_id && <small>来源 Run: {run.parent_run_id}</small>}
            </div>
            <div className="agent-run-actions">
              {run.parent_run_id && result && TERMINAL.has(run.status) && (
                <button className="ghost" onClick={openComparison} disabled={loadingComparison} title="对比本次与来源任务的已保存结果">
                  <ArrowRightLeft size={15} aria-hidden="true" />
                  <span>{loadingComparison ? '对比中' : '与来源任务对比'}</span>
                </button>
              )}
              {TERMINAL.has(run.status) && (
                <button className="ghost" onClick={rerunCurrent} disabled={loading} title="按原配置创建新的研究任务">
                  <Play size={15} aria-hidden="true" />
                  <span>按原配置重跑</span>
                </button>
              )}
              <button className="ghost" onClick={() => loadRun(run.id)} disabled={loading} title="刷新任务状态">
                <RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
                <span>刷新</span>
              </button>
              {!TERMINAL.has(run.status) && (
                <button className="ghost" onClick={cancelRun} title="取消任务">
                  <Square size={14} aria-hidden="true" />
                  <span>取消</span>
                </button>
              )}
            </div>
          </div>

          {!TERMINAL.has(run.status) && (
            <div className="agent-progress">
              <div><span style={{ width: `${progress.total ? Math.max(8, progress.completed / progress.total * 100) : 8}%` }} /></div>
              <small>已完成 {progress.completed}/{progress.total} 个真实数据步骤，页面可以离开后再返回。</small>
            </div>
          )}

          <div className="agent-steps">
            {(run.steps || []).map((step) => <StepState key={step.id} step={step} />)}
            {run.steps?.length === 0 && !TERMINAL.has(run.status) && <div className="agent-waiting"><Bot size={18} aria-hidden="true" />任务已持久化，等待 Worker 领取</div>}
          </div>

          {run.status === 'failed' && <div className="error">{run.error_message || '必需真实数据未能形成 Evidence，本次研究已停止。'}</div>}
        </section>
      )}

      {comparison && (
        <section className="agent-comparison-panel" aria-label="Agent 重跑结果对比">
          <div className="agent-section-head">
            <div>
              <span className="eyebrow">Run Comparison</span>
              <h3>与来源任务的变化</h3>
              <small>
                {comparison.summary?.stable
                  ? '数据日期、关键指标与研究结论均未变化'
                  : `指标变化 ${comparison.summary?.metric_changed_count || 0} 项 · 结论变化 ${comparison.summary?.dimension_changed_count || 0} 项`}
              </small>
            </div>
            <button className="ghost" onClick={() => setComparison(null)}>关闭</button>
          </div>

          <div className="agent-comparison-overview">
            <div><span>来源数据日期</span><b>{comparison.period?.previous_as_of || '-'}</b></div>
            <ArrowRight size={17} aria-hidden="true" />
            <div><span>本次数据日期</span><b>{comparison.period?.current_as_of || '-'}</b></div>
            <div className={comparison.summary?.stable ? 'stable' : 'changed'}>
              <span>对比状态</span>
              <b>{comparison.summary?.stable ? '结果稳定' : '发现变化'}</b>
            </div>
          </div>

          <div className="agent-comparison-block">
            <div className="agent-comparison-title">
              <h4>关键指标</h4>
              <span>数值方向只表示变化，不代表利好或利空</span>
            </div>
            <div className="agent-comparison-table">
              <div className="agent-comparison-row heading">
                <span>指标</span><span>来源 Run</span><span>本次 Run</span><span>变化量</span>
              </div>
              {(comparison.metrics || []).map((item) => (
                <div className={`agent-comparison-row ${item.changed ? 'changed' : ''}`} key={`${item.label}-${item.unit}`}>
                  <b>{item.label}</b>
                  <span>{comparisonValue(item.previous, item.unit)}</span>
                  <span>{comparisonValue(item.current, item.unit)}</span>
                  <em>{comparisonDelta(item)}</em>
                </div>
              ))}
            </div>
          </div>

          <div className="agent-comparison-block">
            <div className="agent-comparison-title"><h4>研究结论</h4><span>基于两个 Run 各自保存的结论快照</span></div>
            <div className="agent-comparison-dimensions">
              {(comparison.dimensions || []).map((item) => (
                <div className={item.changed ? 'changed' : ''} key={item.key}>
                  <span>{item.label}</span>
                  <p><b>{item.previous ?? '-'}</b><ArrowRight size={13} aria-hidden="true" /><b>{item.current ?? '-'}</b></p>
                  <small>{item.changed ? '本次结论已变化' : '保持一致'}</small>
                </div>
              ))}
            </div>
          </div>

          <div className="agent-comparison-integrity">
            <ShieldCheck size={16} aria-hidden="true" />
            <span>
              父子 Evidence 与审计链校验通过 · 共核验 {(comparison.integrity?.parent?.evidence_count || 0) + (comparison.integrity?.current?.evidence_count || 0)} 条 Evidence
            </span>
          </div>
          <p className="agent-comparison-policy">{comparison.policy}</p>
        </section>
      )}

      {result && (
        <>
          {result.ai_synthesis && (
            <AISynthesisView
              analysis={result.ai_synthesis}
              modelStatus={modelStatus}
              onOpenEvidence={openEvidence}
            />
          )}

          <section className="agent-result-summary">
            <span className="eyebrow">Deterministic Risk Gate</span>
            <h3>{result.conclusion?.headline}</h3>
            <p>{result.conclusion?.role_reason || result.scope?.statement}</p>
            <div className="agent-decision-grid">
              <div><span>组合角色</span><b>{result.conclusion?.role || '-'}</b></div>
              <div><span>风险带</span><b>{result.conclusion?.risk_band || '-'}</b></div>
              <div><span>投入节奏</span><b>{result.conclusion?.timing_label || '-'}</b></div>
              <div><span>最低观察周期</span><b>{result.conclusion?.minimum_holding_period || '-'}</b></div>
            </div>
            <div className="agent-scope-note"><CircleAlert size={15} aria-hidden="true" />{result.scope?.statement}</div>
          </section>

          <DecisionOutcomeView
            evaluation={evaluations[0] || null}
            loading={loadingEvaluation}
            onEvaluate={evaluateOutcome}
            onOpenEvidence={openEvidence}
            schedule={outcomeSchedule}
            eligibility={outcomeEligibility}
            loadingSchedule={loadingOutcomeSchedule}
            onConfigureSchedule={configureOutcomeSchedule}
          />

          {result.personalized_decision && (
            <PersonalizedDecisionView decision={result.personalized_decision} onOpenEvidence={openEvidence} />
          )}

          {result.market_profile && (
            <FundMarketProfileView profile={result.market_profile} onOpenEvidence={openEvidence} />
          )}

          {result.strategy && (
            <StrategyPanel
              strategy={result.strategy}
              shadowOutcome={strategyShadowOutcome}
              onOpenEvidence={openEvidence}
              personalized={Boolean(result.personalized_decision)}
            />
          )}

          {result.level_recurrence && (
            <section className="agent-result-section">
              <AssetLevelRecurrenceView data={result.level_recurrence} onOpenEvidence={openEvidence} />
            </section>
          )}

          <section className="agent-result-section">
            <div className="agent-section-head">
              <div><span className="eyebrow">可验证事实</span><h3>关键指标与 Claim</h3></div>
              <span>{result.fund?.as_of ? `截至 ${result.fund.as_of}` : ''}</span>
            </div>
            <div className="agent-fact-grid">
              {(result.facts || []).map((fact) => (
                <button key={fact.claim_id} className="agent-fact" onClick={() => openEvidence(fact.evidence_id)}>
                  <span>{fact.label}</span>
                  <b className={Number(fact.value) < 0 ? 'delta-neg' : ''}>{metricValue(fact)}</b>
                  <small><Database size={12} aria-hidden="true" />查看 Evidence</small>
                </button>
              ))}
            </div>
          </section>

          <section className="agent-result-grid">
            <div className="agent-result-section">
              <div className="agent-section-head"><div><span className="eyebrow">风险门禁</span><h3>红旗与退出条件</h3></div></div>
              <div className="agent-risk-list">
                {(result.risk_review?.red_flags || []).map((item) => <div key={item}><CircleAlert size={15} aria-hidden="true" /><span>{item}</span></div>)}
                {(result.risk_review?.exit_rules || []).slice(0, 3).map((item) => <div key={item.title}><ShieldCheck size={15} aria-hidden="true" /><span><b>{item.title}</b>{item.text}</span></div>)}
              </div>
            </div>
            <div className="agent-result-section">
              <div className="agent-section-head"><div><span className="eyebrow">执行框架</span><h3>下一步研究任务</h3></div></div>
              <div className="agent-action-list">
                {(result.next_actions || []).map((item) => <div key={item.step}><b>{item.step}</b><p>{item.action}</p></div>)}
              </div>
            </div>
          </section>

          {result.peer_persistence && (
            <section className="agent-result-section">
              <FundPeerPersistenceView
                data={result.peer_persistence}
                onOpenEvidence={openEvidence}
              />
            </section>
          )}

          {result.alternatives?.length > 0 && (
            <section className="agent-result-section">
              <div className="agent-section-head"><div><span className="eyebrow">同类比较</span><h3>替代研究候选</h3></div><span>候选不等于自动换仓</span></div>
              <div className="agent-alternative-grid">
                {result.alternatives.map((item) => {
                  const durability = item.durability || {}
                  const dueDiligence = item.due_diligence || {}
                  return (
                    <article key={item.code}>
                      <div><b>{item.code} {item.name}</b><span>{dueDiligence.label || durability.label || '替换价值尚未验证'}</span></div>
                      <dl>
                        <div><dt>滚动 6 月胜率</dt><dd>{pct(durability.rolling?.['6m']?.win_rate_pct)}</dd></div>
                        <div><dt>持股重合下界</dt><dd>{pct(dueDiligence.overlap?.stock_overlap_lower_bound_pct)}</dd></div>
                        <div><dt>年度费率差</dt><dd>{pp(dueDiligence.fees?.annual_rate_delta_pp)}</dd></div>
                      </dl>
                      <p>{dueDiligence.rationale || durability.rationale || '真实持续性、费率或披露持仓复核未完成，不能依据榜单直接换入。'}</p>
                      <button className="ghost" onClick={() => openEvidence(item.evidence_id)}><FileSearch size={14} aria-hidden="true" />证据</button>
                    </article>
                  )
                })}
              </div>
            </section>
          )}

          {result.unavailable?.length > 0 && (
            <section className="agent-unavailable">
              <strong>未参与完整结论的数据</strong>
              {result.unavailable.map((item) => <p key={item.step}>{STEP_LABELS[item.tool] || item.tool}: {item.reason}</p>)}
            </section>
          )}

          <section className="agent-evidence-index">
            <div className="agent-section-head">
              <div><span className="eyebrow">Evidence & Audit</span><h3>本次运行的证据索引</h3></div>
              <button className="ghost" onClick={openAudit} disabled={loadingAudit}><History size={15} aria-hidden="true" />{loadingAudit ? '读取中' : '查看审计链'}</button>
            </div>
            <div className="agent-evidence-list">
              {(result.evidence_refs || []).map((item) => (
                <button key={item.evidence_id} onClick={() => openEvidence(item.evidence_id)}>
                  <Database size={15} aria-hidden="true" />
                  <span><b>{STEP_LABELS[item.tool_step] || item.tool_step}</b><small>{item.provider} · {item.as_of || '时间见证据'}</small></span>
                  <em>{QUALITY[item.quality_status] || item.quality_status}</em>
                </button>
              ))}
            </div>
          </section>
        </>
      )}

      {loadingEvidence && <div className="page-loading"><span className="spinner" />正在读取 Evidence</div>}
      {selectedEvidence && !loadingEvidence && (
        <section className="agent-evidence-detail">
          <div className="agent-section-head">
            <div><span className="eyebrow">Evidence</span><h3>{selectedEvidence.provider}</h3></div>
            <button className="ghost" onClick={() => setSelectedEvidence(null)}>关闭</button>
          </div>
          <div className="agent-evidence-meta">
            <span>有效时间 <b>{selectedEvidence.as_of || '-'}</b></span>
            <span>获取时间 <b>{timeText(selectedEvidence.observed_at)}</b></span>
            <span>质量 <b>{QUALITY[selectedEvidence.quality_status] || selectedEvidence.quality_status}</b></span>
            <span>完整性 <b className={selectedEvidence.integrity_verified ? 'integrity-ok' : 'integrity-failed'}>{selectedEvidence.integrity_verified ? '摘要一致' : '校验失败'}</b></span>
            <span>Schema <b>{selectedEvidence.schema_version}</b></span>
          </div>
          <code className="agent-hash">SHA-256 {selectedEvidence.payload_sha256}</code>
          <details>
            <summary>查看结构化证据数据</summary>
            <pre>{JSON.stringify(selectedEvidence.payload, null, 2)}</pre>
          </details>
        </section>
      )}

      {audit && (
        <section className="agent-audit-detail">
          <div className="agent-section-head">
            <div>
              <span className="eyebrow">Audit</span>
              <h3>追加式审计时间线</h3>
              <small className={`agent-chain-state ${audit.verification?.verified ? 'verified' : 'invalid'}`}>
                {audit.verification?.verified ? '哈希链校验通过' : `哈希链校验失败 · 序号 ${audit.verification?.failing_sequence || '-'}`}
              </small>
            </div>
            <button className="ghost" onClick={() => setAudit(null)}>关闭</button>
          </div>
          <div className="agent-audit-list">
            {audit.items?.map((item) => (
              <div key={item.id}>
                <span>{item.sequence_no}</span>
                <div><b>{item.event_type}</b><small>{timeText(item.created_at)}</small></div>
                <code>{item.event_hash.slice(0, 12)}</code>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
