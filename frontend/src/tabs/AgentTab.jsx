import { useEffect, useMemo, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  CircleAlert,
  Database,
  FileSearch,
  Filter,
  History,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  X,
} from 'lucide-react'
import {
  cancelAgentRun,
  createFundResearchRun,
  fetchAgentAudit,
  fetchAgentEvidence,
  fetchAgentRun,
  fetchAgentRuns,
  rerunAgentRun,
} from '../api/agent'

const TERMINAL = new Set(['completed', 'partial', 'failed', 'cancelled', 'abstained'])
const EMPTY_HISTORY_FILTERS = { code: '', status: '' }

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
  'fund.alternatives.get': '同类替代候选',
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`
}

function metricValue(item) {
  if (item.value == null) return '-'
  if (item.unit === '%') {
    const number = Number(item.value)
    const prefix = number > 0 && /收益|涨跌/.test(item.label || '') ? '+' : ''
    return `${prefix}${number.toFixed(2)}%`
  }
  if (item.unit === '分') return `${Number(item.value).toFixed(0)} 分`
  return `${Number(item.value).toLocaleString('zh-CN', { maximumFractionDigits: 4 })}${item.unit || ''}`
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
  const [code, setCode] = useState('001480')
  const [months, setMonths] = useState(36)
  const [includeEstimate, setIncludeEstimate] = useState(false)
  const [includeDisclosure, setIncludeDisclosure] = useState(false)
  const [includeAlternatives, setIncludeAlternatives] = useState(false)
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState({ items: [], next_cursor: null, has_more: false })
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyFilterDraft, setHistoryFilterDraft] = useState(EMPTY_HISTORY_FILTERS)
  const [historyFilters, setHistoryFilters] = useState(EMPTY_HISTORY_FILTERS)
  const [error, setError] = useState('')
  const [selectedEvidence, setSelectedEvidence] = useState(null)
  const [loadingEvidence, setLoadingEvidence] = useState(false)
  const [audit, setAudit] = useState(null)
  const [loadingAudit, setLoadingAudit] = useState(false)

  async function loadRun(runId, { quiet = false } = {}) {
    if (!runId) return
    if (!quiet) setLoading(true)
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
    const savedRunId = localStorage.getItem('investment-agent-run-id')
    if (savedRunId) loadRun(savedRunId)
    loadHistory()
  }, [])

  useEffect(() => {
    if (!run?.id || TERMINAL.has(run.status)) return undefined
    const timer = window.setInterval(() => loadRun(run.id, { quiet: true }), 1200)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status])

  useEffect(() => {
    if (run?.id && TERMINAL.has(run.status)) loadHistory()
  }, [run?.id, run?.status])

  const progress = useMemo(() => {
    if (!run) return { completed: 0, total: 0 }
    const completed = (run.steps || []).filter((item) => ['succeeded', 'partial', 'failed'].includes(item.status)).length
    const requested = 1 + Number(Boolean(run.input?.include_estimate))
      + Number(Boolean(run.input?.include_disclosure_changes))
      + Number(Boolean(run.input?.include_alternatives))
    return { completed, total: Math.max(requested, run.steps?.length || 0) }
  }, [run])

  async function startResearch() {
    const clean = code.trim()
    if (!/^\d{6}$/.test(clean)) {
      setError('请输入 6 位基金代码')
      return
    }
    setLoading(true)
    setError('')
    setRun(null)
    setSelectedEvidence(null)
    setAudit(null)
    try {
      const data = await createFundResearchRun({
        code: clean,
        months: Number(months),
        include_estimate: includeEstimate,
        include_disclosure_changes: includeDisclosure,
        include_alternatives: includeAlternatives,
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
          <span className="eyebrow">投资研究工作台</span>
          <h2>基金深度研究任务</h2>
          <p>围绕确认净值、回撤、波动和可选披露数据，形成一份可追溯的基金研究记录。</p>
        </div>
        <div className="agent-readonly-badge"><ShieldCheck size={16} aria-hidden="true" />R0 公共只读</div>
      </section>

      <section className="panel agent-launcher" aria-label="创建基金研究任务">
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
          <button onClick={startResearch} disabled={loading || (run && !TERMINAL.has(run.status))}>
            <Play size={16} aria-hidden="true" />
            <span>{loading ? '正在创建' : '开始研究'}</span>
          </button>
        </div>
        <div className="agent-options" aria-label="研究范围">
          <label><input type="checkbox" checked={includeEstimate} onChange={(event) => setIncludeEstimate(event.target.checked)} />盘中估值核验</label>
          <label><input type="checkbox" checked={includeDisclosure} onChange={(event) => setIncludeDisclosure(event.target.checked)} />披露变化</label>
          <label><input type="checkbox" checked={includeAlternatives} onChange={(event) => setIncludeAlternatives(event.target.checked)} />同类替代品</label>
        </div>
      </section>

      <section className="agent-history-panel" aria-label="Agent 运行历史">
        <div className="agent-section-head">
          <div><span className="eyebrow">Run History</span><h3>最近研究任务</h3></div>
          <button className="ghost" onClick={() => loadHistory()} disabled={loadingHistory} title="刷新历史任务">
            <RefreshCw size={15} className={loadingHistory ? 'spin-icon' : ''} aria-hidden="true" />
            <span>{loadingHistory ? '刷新中' : '刷新'}</span>
          </button>
        </div>
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
                  <small>{item.parent_run_id ? '重跑 · ' : ''}{timeText(item.completed_at || item.created_at)}</small>
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
      </section>

      {error && <div className="error">{error}</div>}

      {run && (
        <section className="agent-run-band" aria-live="polite">
          <div className="agent-run-head">
            <div>
              <span className={`agent-status ${runStatusTone}`}>{runStatusLabel}</span>
              <h3>{result?.fund ? `${result.fund.code} ${result.fund.name}` : `${run.input?.code || '-'} 基金研究`}</h3>
              <small>Run ID: {run.id}</small>
              {run.parent_run_id && <small>来源 Run: {run.parent_run_id}</small>}
            </div>
            <div className="agent-run-actions">
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

      {result && (
        <>
          <section className="agent-result-summary">
            <span className="eyebrow">研究结论</span>
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

          {result.alternatives?.length > 0 && (
            <section className="agent-result-section">
              <div className="agent-section-head"><div><span className="eyebrow">同类比较</span><h3>替代研究候选</h3></div><span>候选不等于自动换仓</span></div>
              <div className="agent-alternative-grid">
                {result.alternatives.map((item) => (
                  <article key={item.code}>
                    <div><b>{item.code} {item.name}</b><span>{item.label || '继续研究'}</span></div>
                    <dl>
                      <div><dt>近 1 年</dt><dd>{pct(item.metrics?.return_1y)}</dd></div>
                      <div><dt>最大回撤</dt><dd>{pct(item.metrics?.max_drawdown)}</dd></div>
                      <div><dt>替代评分</dt><dd>{item.score ?? '-'}</dd></div>
                    </dl>
                    <p>{item.advantages?.[0] || item.cautions?.[0] || '需要继续核对费用和持仓重合。'}</p>
                    <button className="ghost" onClick={() => openEvidence(item.evidence_id)}><FileSearch size={14} aria-hidden="true" />证据</button>
                  </article>
                ))}
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
