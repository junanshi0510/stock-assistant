import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  DatabaseZap,
  GitCompareArrows,
  History,
  Layers3,
  Play,
  Plus,
  Radar,
  RefreshCw,
  ShieldAlert,
  SlidersHorizontal,
  Trash2,
  WalletCards,
} from 'lucide-react'
import {
  createPortfolioTwinRun,
  fetchHoldings,
  fetchInvestmentProfile,
  fetchPortfolioTwinPresets,
  fetchPortfolioTwinRun,
  fetchPortfolioTwinRuns,
} from '../../api/portfolio'

const MARKET_ORDER = ['mainland', 'hong_kong', 'united_states', 'global', 'unknown']
const MARKET_LABELS = {
  mainland: 'A股',
  hong_kong: '港股',
  united_states: '美股',
  global: '全球',
  unknown: '未识别权益',
}

const money = (value) => {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  const sign = number < 0 ? '-' : ''
  return `${sign}¥${Math.abs(number).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

const percent = (value, digits = 1) => {
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toFixed(digits)}%` : '—'
}

const shortTime = (value) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function formFromPreset(preset, policy) {
  const policyBudget = policy?.configured ? Number(policy.max_drawdown_pct) : null
  const presetBudget = Number(preset?.loss_budget_pct) || 15
  return {
    name: preset?.name || '自定义组合压力情景',
    presetId: preset?.id || 'custom',
    marketShocks: Object.fromEntries(
      MARKET_ORDER.map((market) => [
        market,
        Number((preset?.market_shocks || []).find((item) => item.market === market)?.shock_pct) || 0,
      ]),
    ),
    lossBudget: Number.isFinite(policyBudget) ? Math.min(presetBudget, policyBudget) : presetBudget,
    minimumTrade: 0,
  }
}

function RiskBudgetCard({ title, value, changed }) {
  const utilization = Number(value?.risk_budget?.utilization_pct) || 0
  const interval = value?.pnl_interval || {}
  const breached = Boolean(value?.risk_budget?.breached)
  return (
    <article className={`twin-budget-card ${breached ? 'breached' : 'safe'}`}>
      <div className="twin-budget-title">
        <span>{title}</span>
        <em>{changed ? 'WHAT-IF' : 'BASELINE'}</em>
      </div>
      <strong>{money(interval.lower_amount)}</strong>
      <small>最坏边界 {percent(interval.lower_pct)} · 最好边界 {money(interval.upper_amount)}</small>
      <div className="twin-budget-track" aria-label={`风险预算使用 ${percent(utilization)}`}>
        <i style={{ width: `${Math.min(100, Math.max(0, utilization))}%` }} />
        <b style={{ left: `${Math.min(100, Math.max(0, utilization))}%` }} />
      </div>
      <div className="twin-budget-meta">
        <span>预算使用 {percent(utilization)}</span>
        <span>{breached ? '已越线' : `余量 ${money(value?.risk_budget?.remaining_amount)}`}</span>
      </div>
    </article>
  )
}

function EvidenceStatus({ run }) {
  const gate = run?.result?.decision_gate
  const verified = Boolean(run?.integrity?.verified)
  const eligible = Boolean(gate?.decision_eligible)
  return (
    <div className={`twin-evidence-status ${verified && eligible ? 'verified' : 'limited'}`}>
      {verified && eligible ? <CheckCircle2 size={17} /> : <ShieldAlert size={17} />}
      <span>
        <b>{verified && eligible ? '证据链完整，可做情景比较' : '结果可查看，但决策资格受限'}</b>
        <small>{verified ? '不可变运行哈希已验证' : '运行完整性未通过'} · {gate?.reasons?.[0] || '持仓、穿透快照和投资政策已对齐'}</small>
      </span>
    </div>
  )
}

export default function PortfolioDecisionTwin() {
  const [presets, setPresets] = useState([])
  const [holdings, setHoldings] = useState([])
  const [profile, setProfile] = useState(null)
  const [runs, setRuns] = useState([])
  const [activeRun, setActiveRun] = useState(null)
  const [form, setForm] = useState(null)
  const [industries, setIndustries] = useState([])
  const [targets, setTargets] = useState({})
  const [positionShocks, setPositionShocks] = useState({})
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [error, setError] = useState('')
  const nextIndustryId = useRef(0)

  useEffect(() => {
    let active = true
    async function loadWorkspace() {
      try {
        const [presetResult, holdingResult, policyResult, runResult] = await Promise.all([
          fetchPortfolioTwinPresets(),
          fetchHoldings(),
          fetchInvestmentProfile(),
          fetchPortfolioTwinRuns(20),
        ])
        const nextPresets = presetResult.items || []
        const nextRuns = runResult.items || []
        let latestRun = null
        let historyError = ''
        if (nextRuns[0]) {
          try {
            latestRun = await fetchPortfolioTwinRun(nextRuns[0].id)
          } catch (reason) {
            historyError = reason.message || '最新历史运行读取失败'
          }
        }
        if (!active) return
        setPresets(nextPresets)
        setHoldings(holdingResult.items || [])
        setProfile(policyResult)
        setRuns(nextRuns)
        setActiveRun(latestRun)
        setForm(formFromPreset(nextPresets[0], policyResult))
        if (historyError) setError(historyError)
      } catch (reason) {
        if (active) setError(reason.message || '组合数字孪生工作台加载失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    loadWorkspace()
    return () => { active = false }
  }, [])

  const totalAmount = useMemo(
    () => holdings.reduce((sum, item) => sum + (Number(item.amount) || 0), 0),
    [holdings],
  )
  const result = activeRun?.result

  function applyPreset(preset) {
    setForm(formFromPreset(preset, profile))
    setIndustries([])
    setPositionShocks({})
    setError('')
  }

  function setShock(market, value) {
    setForm((current) => ({
      ...current,
      presetId: 'custom',
      marketShocks: { ...current.marketShocks, [market]: value },
    }))
  }

  function updateIndustry(index, field, value) {
    setIndustries((items) => items.map((item, itemIndex) => (
      itemIndex === index ? { ...item, [field]: value } : item
    )))
  }

  async function runTwin() {
    if (!form || running) return
    setRunning(true)
    setError('')
    try {
      const hypothetical = holdings
        .filter((item) => String(item.asset_type || '').toLowerCase() !== 'cash')
        .filter((item) => targets[item.id] !== undefined && targets[item.id] !== '')
        .map((item) => ({ holding_id: item.id, target_amount: Number(targets[item.id]) }))
      const overrides = holdings
        .filter((item) => positionShocks[item.id] !== undefined && positionShocks[item.id] !== '')
        .map((item) => ({ holding_id: item.id, shock_pct: Number(positionShocks[item.id]) }))
      const payload = {
        name: form.name,
        preset_id: form.presetId,
        market_shocks: MARKET_ORDER.map((market) => ({
          market,
          shock_pct: Number(form.marketShocks[market]),
        })),
        industry_shocks: industries
          .filter((item) => item.industry.trim() && item.shock !== '')
          .map((item) => ({ industry: item.industry.trim(), shock_pct: Number(item.shock) })),
        position_shocks: overrides,
        hypothetical_positions: hypothetical,
        loss_budget_pct: Number(form.lossBudget),
        minimum_trade_amount: Number(form.minimumTrade) || 0,
      }
      const created = await createPortfolioTwinRun(payload)
      setActiveRun(created)
      setRuns((items) => [created, ...items.filter((item) => item.id !== created.id)].slice(0, 20))
    } catch (reason) {
      setError(reason.message || '组合压力测试运行失败')
    } finally {
      setRunning(false)
    }
  }

  async function openRun(run) {
    if (run.id === activeRun?.id && run.holdings) return
    setHistoryLoading(true)
    setError('')
    try {
      setActiveRun(await fetchPortfolioTwinRun(run.id))
    } catch (reason) {
      setError(reason.message || '历史运行读取失败')
    } finally {
      setHistoryLoading(false)
    }
  }

  if (loading) return <div className="page-loading"><span className="spinner" />正在加载组合数字孪生</div>

  return (
    <div className="twin-workspace">
      <section className="twin-product-banner">
        <div className="twin-product-icon"><Radar size={25} /></div>
        <div>
          <span className="eyebrow">PORTFOLIO DECISION TWIN · V1</span>
          <h2>先让组合经历坏情景，再决定是否动手</h2>
          <p>用你确认的真实金额和基金披露穿透作为基线，比较调整前后、反推亏损预算破线条件，并生成可验证的最小名义金额降险草案。</p>
        </div>
        <div className="twin-product-boundary">
          <ShieldAlert size={17} />
          <span><b>研究工具，不自动交易</b><small>不输出未来涨跌概率；不填补缺失持仓、Beta 或相关性。</small></span>
        </div>
      </section>

      {error && <div className="twin-error"><AlertTriangle size={17} /><span>{error}</span></div>}

      <section className="twin-builder">
        <aside className="twin-preset-rail">
          <div className="twin-rail-title">
            <Layers3 size={17} />
            <span><b>情景假设库</b><small>全部可编辑，并非行情预测</small></span>
          </div>
          {presets.map((preset) => (
            <button
              type="button"
              key={preset.id}
              className={form?.presetId === preset.id ? 'active' : ''}
              onClick={() => applyPreset(preset)}
            >
              <b>{preset.name}</b>
              <small>{preset.description}</small>
              <span>{percent(Math.min(...preset.market_shocks.map((item) => item.shock_pct)), 0)} 最深市场冲击</span>
            </button>
          ))}
          <div className="twin-portfolio-facts">
            <span><small>确认持仓</small><b>{holdings.length} 项</b></span>
            <span><small>组合金额</small><b>{money(totalAmount)}</b></span>
            <span><small>政策状态</small><b>{profile?.configured ? '已激活' : '未激活'}</b></span>
          </div>
        </aside>

        <div className="twin-builder-main">
          <div className="twin-builder-head">
            <div>
              <span className="eyebrow">SCENARIO INPUTS</span>
              <h3>多市场冲击与决策边界</h3>
              <p>市场冲击作用于已识别暴露；行业冲击作为叠加项。未识别权益使用独立的保守冲击。</p>
            </div>
            <label className="twin-name-field">
              <span>运行名称</span>
              <input value={form?.name || ''} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </label>
          </div>

          <div className="twin-market-grid">
            {MARKET_ORDER.map((market) => {
              const shock = Number(form?.marketShocks?.[market]) || 0
              return (
                <label key={market} className={market === 'unknown' ? 'unknown' : ''}>
                  <span>{MARKET_LABELS[market]}<small>{market === 'unknown' ? '披露盲区' : '一阶价格冲击'}</small></span>
                  <b className={shock < 0 ? 'negative' : shock > 0 ? 'positive' : ''}>{percent(shock, 0)}</b>
                  <input
                    type="range"
                    min="-50"
                    max="20"
                    step="1"
                    value={shock}
                    onChange={(event) => setShock(market, Number(event.target.value))}
                  />
                  <input
                    type="number"
                    min="-80"
                    max="50"
                    step="1"
                    value={shock}
                    onChange={(event) => setShock(market, event.target.value)}
                  />
                </label>
              )
            })}
          </div>

          <div className="twin-control-row">
            <label>
              <span>亏损预算 <small>与投资政策取更严格者</small></span>
              <div><input type="number" min="1" max="50" value={form?.lossBudget ?? 15} onChange={(event) => setForm({ ...form, lossBudget: event.target.value })} /><b>%</b></div>
            </label>
            <label>
              <span>最小调整金额 <small>避免无法执行的碎片动作</small></span>
              <div><b>¥</b><input type="number" min="0" step="100" value={form?.minimumTrade ?? 0} onChange={(event) => setForm({ ...form, minimumTrade: event.target.value })} /></div>
            </label>
            <div className="twin-effective-budget">
              <SlidersHorizontal size={17} />
              <span><small>有效预算上限</small><b>{percent(Math.min(Number(form?.lossBudget) || 0, profile?.configured ? Number(profile.max_drawdown_pct) : 50))}</b></span>
            </div>
          </div>

          <div className="twin-overlay-section">
            <div className="twin-section-title">
              <div><Activity size={17} /><span><b>行业叠加冲击</b><small>可选；例如“信息技术 -8%”，叠加在市场冲击之上</small></span></div>
              <button type="button" className="ghost" onClick={() => setIndustries((items) => [...items, { id: `industry-${++nextIndustryId.current}`, industry: '', shock: -5 }])}><Plus size={14} />添加行业</button>
            </div>
            {industries.length === 0 ? <p className="twin-inline-empty">当前只应用市场冲击。基金未分类行业不会被猜测。</p> : (
              <div className="twin-industry-rows">
                {industries.map((item, index) => (
                  <div key={item.id}>
                    <input placeholder="披露中的行业名称" value={item.industry} onChange={(event) => updateIndustry(index, 'industry', event.target.value)} />
                    <input type="number" min="-50" max="50" value={item.shock} onChange={(event) => updateIndustry(index, 'shock', event.target.value)} />
                    <span>%</span>
                    <button type="button" aria-label="删除行业冲击" onClick={() => setIndustries((items) => items.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={14} /></button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <details className="twin-what-if">
            <summary>
              <GitCompareArrows size={17} />
              <span><b>调仓前后 WHAT-IF</b><small>只允许在当前总金额内调整已有持仓，差额自动进入现金；不会隐含杠杆或外部注资</small></span>
            </summary>
            {holdings.length ? (
              <div className="twin-position-table-wrap">
                <table className="twin-position-table">
                  <thead><tr><th>持仓</th><th>当前金额</th><th>假设目标金额</th><th>个券总冲击（可选）</th></tr></thead>
                  <tbody>
                    {holdings.map((item) => {
                      const isCash = ['cash', 'currency', '现金', '货币'].includes(String(item.asset_type || '').toLowerCase())
                      return (
                        <tr key={item.id}>
                          <td><b>{item.name || item.code}</b><small>{item.code} · {item.market || item.asset_type}</small></td>
                          <td>{money(item.amount)}</td>
                          <td><input disabled={isCash} type="number" min="0" placeholder={isCash ? '自动平衡' : String(item.amount ?? '')} value={targets[item.id] ?? ''} onChange={(event) => setTargets({ ...targets, [item.id]: event.target.value })} /></td>
                          <td><div className="twin-percent-input"><input disabled={isCash} type="number" min="-95" max="100" placeholder="使用暴露模型" value={positionShocks[item.id] ?? ''} onChange={(event) => setPositionShocks({ ...positionShocks, [item.id]: event.target.value })} /><span>%</span></div></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : <p className="twin-inline-empty">请先在“持仓与纪律”中导入并确认真实持仓。</p>}
          </details>

          <div className="twin-run-actions">
            <span><DatabaseZap size={16} />运行时会刷新真实基金披露，并冻结持仓、政策、情景、结果与哈希。</span>
            <button type="button" onClick={runTwin} disabled={running || !holdings.length}>
              {running ? <><span className="spinner" />正在刷新暴露并求解</> : <><Play size={16} />运行组合数字孪生</>}
            </button>
          </div>
        </div>
      </section>

      <section className="twin-history-strip">
        <span><History size={16} /><b>不可变运行历史</b><small>{runs.length} 次</small></span>
        <div>
          {runs.length ? runs.map((run) => (
            <button type="button" key={run.id} className={activeRun?.id === run.id ? 'active' : ''} onClick={() => openRun(run)}>
              <b>{run.scenario?.name || '组合情景'}</b>
              <small>{shortTime(run.created_at)} · {run.status === 'complete' ? '完整' : '受限'}</small>
            </button>
          )) : <em>运行第一个情景后，证据快照会保留在这里。</em>}
        </div>
        {historyLoading && <RefreshCw className="spin" size={15} />}
      </section>

      {!result ? (
        <section className="twin-empty-result">
          <WalletCards size={31} />
          <h3>组合还没有经历过你的压力情景</h3>
          <p>选择一个假设并运行后，这里会显示损益区间、预算破线点、脆弱持仓、调仓前后差异和最小降险草案。</p>
        </section>
      ) : (
        <div className="twin-results">
          <section className="twin-result-head">
            <div>
              <span className="eyebrow">IMMUTABLE RUN · {activeRun.id?.slice(-8)}</span>
              <h2>{result.scenario?.name}</h2>
              <p>{shortTime(activeRun.created_at)} · 方法 {result.method_version} · 有效亏损预算 {percent(result.budget?.effective_loss_budget_pct)}</p>
            </div>
            <EvidenceStatus run={activeRun} />
          </section>

          <section className="twin-kpi-grid">
            <div><small>当前组合最坏边界</small><b className="negative">{money(result.current?.pnl_interval?.lower_amount)}</b><span>{percent(result.current?.pnl_interval?.lower_pct)}</span></div>
            <div><small>WHAT-IF 最坏边界</small><b className="negative">{money(result.proposed?.pnl_interval?.lower_amount)}</b><span>{result.comparison?.what_if_changed ? `改善 ${money(result.comparison?.worst_loss_improvement_amount)}` : '未设置调仓假设'}</span></div>
            <div><small>损益不确定性宽度</small><b>{money(result.proposed?.pnl_interval?.width_amount)}</b><span>来自未披露/未分类暴露</span></div>
            <div><small>破线统一冲击倍数</small><b>{result.reverse_stress?.breach_multiplier ? `${Number(result.reverse_stress.breach_multiplier).toFixed(2)}×` : '不可求解'}</b><span>{result.reverse_stress?.status === 'already_breached' ? '当前情景已越线' : result.reverse_stress?.status === 'unsupported_mixed_direction' ? '混合方向情景不伪造阈值' : result.reverse_stress?.status === 'unreachable_within_model' ? '模型上限内未破线' : '按当前冲击结构反推'}</span></div>
            <div><small>未识别市场暴露</small><b>{percent(result.proposed?.allocation?.unknown_market_ratio)}</b><span>越高，结论区间越宽</span></div>
          </section>

          <section className="twin-comparison-panel">
            <div className="twin-panel-head">
              <div><GitCompareArrows size={18} /><span><b>调仓前后预算对照</b><small>同一冻结情景、同一总金额、同一证据快照</small></span></div>
              <span className={`twin-comparison-delta ${(result.comparison?.worst_loss_improvement_amount || 0) >= 0 ? 'better' : 'worse'}`}>
                最坏损失变化 {money(result.comparison?.worst_loss_improvement_amount)}
              </span>
            </div>
            <div className="twin-budget-pair">
              <RiskBudgetCard title="当前真实组合" value={result.current} />
              <RiskBudgetCard title="假设调整后" value={result.proposed} changed />
            </div>
          </section>

          <div className="twin-decision-grid">
            <section className="twin-reverse-card">
              <div className="twin-panel-head"><div><Radar size={18} /><span><b>反向压力：什么程度会破线</b><small>不是预测发生概率，而是求解组合脆弱阈值</small></span></div></div>
              {result.reverse_stress?.breach_multiplier ? (
                <>
                  <div className="twin-reverse-number"><strong>{Number(result.reverse_stress.breach_multiplier).toFixed(2)}×</strong><span>{result.reverse_stress.status === 'already_breached' ? '当前设定已经超过预算阈值' : `当前冲击整体再变化 ${percent(result.reverse_stress.distance_from_current_scenario_pct)}`}</span></div>
                  <div className="twin-reverse-markets">
                    {(result.reverse_stress.scaled_market_shocks || []).map((item) => <span key={item.market}><small>{item.label}</small><b>{percent(item.shock_pct)}</b></span>)}
                  </div>
                </>
              ) : <p className="twin-inline-empty">{result.reverse_stress?.reason || '当前冲击方向按模型上限放大后仍未触碰预算，无法给出有限阈值。'}</p>}
            </section>

            <section className={`twin-repair-card ${result.repair_plan?.status}`}>
              <div className="twin-panel-head"><div><WalletCards size={18} /><span><b>最小名义金额降险草案</b><small>按最坏损失边际效率排序，减持后转入零冲击现金</small></span></div></div>
              {result.repair_plan?.status === 'not_needed' ? (
                <div className="twin-repair-safe"><CheckCircle2 size={25} /><span><b>当前方案未越过亏损预算</b><small>无需为了本情景机械减仓；仍应检查其他政策门槛。</small></span></div>
              ) : (
                <>
                  <div className="twin-repair-total"><span><small>建议转现金总额</small><strong>{money(result.repair_plan?.total_shift_to_cash)}</strong></span><b>{result.repair_plan?.status === 'available' ? '修复后回到预算内' : '现有持仓无法完全修复'}</b></div>
                  <div className="twin-repair-actions">
                    {(result.repair_plan?.actions || []).map((action, index) => (
                      <div key={`${action.holding_id}-${index}`}><span><b>减持 {action.name}</b><small>{action.code} · 最坏冲击 {percent(action.worst_shock_pct)}</small></span><strong>{money(action.reduce_amount)}</strong></div>
                    ))}
                  </div>
                  <div className="twin-frontier">
                    {(result.repair_plan?.frontier || []).map((point) => (
                      <span key={point.repair_pct}><i style={{ height: `${Math.min(100, Number(point.budget_utilization_pct) || 0)}%` }} /><b>{percent(point.worst_loss_pct)}</b><small>{point.repair_pct}% 动作</small></span>
                    ))}
                  </div>
                </>
              )}
            </section>
          </div>

          <section className="twin-fragility-panel">
            <div className="twin-panel-head">
              <div><Activity size={18} /><span><b>组合脆弱性地图</b><small>按 WHAT-IF 组合的最坏损失贡献排序；区间宽度显示数据不确定性</small></span></div>
              <span>现金 {percent(result.proposed?.allocation?.cash_ratio)} · 权益上界 {percent(result.proposed?.allocation?.equity_upper_ratio)}</span>
            </div>
            <div className="twin-fragility-table-wrap">
              <table className="twin-fragility-table">
                <thead><tr><th>#</th><th>持仓</th><th>情景损益区间</th><th>最坏冲击</th><th>最坏损失贡献</th><th>不确定性宽度</th></tr></thead>
                <tbody>
                  {(result.fragility_map || []).map((item) => (
                    <tr key={item.holding_id}>
                      <td>{item.rank}</td>
                      <td><b>{item.name}</b><small>{item.code} · {money(item.amount)}</small></td>
                      <td><span className="negative">{money(item.pnl_lower)}</span><small>至 {money(item.pnl_upper)}</small></td>
                      <td>{percent(item.shock_lower_pct)}</td>
                      <td><div className="twin-contribution-bar"><i style={{ width: `${Math.min(100, item.worst_loss_contribution_pct || 0)}%` }} /><span>{percent(item.worst_loss_contribution_pct)}</span></div></td>
                      <td>{money(item.interval_width)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="twin-method-panel">
            <details open={!result.decision_gate?.decision_eligible}>
              <summary><DatabaseZap size={17} /><span><b>证据边界与方法说明</b><small>查看为什么可用或受限，以及模型没有计算什么</small></span></summary>
              <div className="twin-method-grid">
                <div><b>证据门禁</b>{Object.entries(result.decision_gate?.checks || {}).map(([key, value]) => <span key={key} className={value ? 'pass' : 'fail'}>{value ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}{key}</span>)}</div>
                <div><b>未建模事项</b>{(result.methodology?.not_modeled || []).map((item) => <span key={item}><AlertTriangle size={13} />{item}</span>)}</div>
                <div><b>数据血缘</b><span>持仓 {result.data_lineage?.holdings_sha256?.slice(0, 12)}…</span><span>暴露快照 {result.data_lineage?.exposure_snapshot_id || '—'}</span><span>投资政策 {result.data_lineage?.profile_version_id || '未激活'}</span></div>
              </div>
              <p>{result.methodology?.decision_boundary}</p>
            </details>
          </section>
        </div>
      )}
    </div>
  )
}
