import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  BadgeDollarSign,
  CalendarClock,
  CheckCircle2,
  Database,
  FlaskConical,
  RefreshCw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import {
  createOpportunityProfitPolicy,
  createOpportunityProfitScorecard,
  fetchOpportunityProfitLab,
} from '../../api/opportunities'

const GATE_META = {
  empty: { label: '尚未开始', tone: 'neutral', detail: '先从完成的机会扫描冻结纸面组合。' },
  collecting: { label: '前瞻样本积累中', tone: 'collecting', detail: '自动观察正在积累独立、不可变的前瞻批次。' },
  watch: { label: '继续观察', tone: 'watch', detail: '平均结果已过线，但统计区间仍覆盖零超额。' },
  suspended: { label: '暂停资金', tone: 'suspended', detail: '成本后超额、命中率或回撤没有通过。' },
  limited_manual_pilot: { label: '可小额人工试运行', tone: 'eligible', detail: '只开放受限预算研究，不授权交易。' },
}

const CHECK_LABELS = {
  minimum_mature_baskets: '独立成熟批次',
  positive_mean_net_excess: '成本后平均超额',
  positive_excess_rate: '胜过基准比例',
  drawdown_within_limit: '回撤上限',
  confidence_interval_above_zero: '单策略 95% 区间',
  multiple_testing_guard: '跨策略假阳性校正',
}

function numeric(value) {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function percent(value, digits = 2, signed = false) {
  const parsed = numeric(value)
  if (parsed == null) return '—'
  return `${signed && parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`
}

function money(value) {
  const parsed = numeric(value)
  return parsed == null ? '—' : `¥${parsed.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}

function dateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function fieldNumber(value) {
  const parsed = numeric(value)
  return parsed == null ? '' : parsed
}

function performanceClass(value, positive = 'positive', negative = 'negative') {
  const parsed = numeric(value)
  if (parsed == null) return ''
  return parsed >= 0 ? positive : negative
}

function PolicyEditor({ scorecard, saving, onSave }) {
  const values = scorecard.policy?.values || {}
  const [form, setForm] = useState(values)

  useEffect(() => {
    setForm(values)
  }, [scorecard.strategy?.id, scorecard.policy?.version_no])

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function submit(event) {
    event.preventDefault()
    onSave({
      ...form,
      evaluation_horizons: values.evaluation_horizons || [5, 20, 60],
      primary_horizon: Number(form.primary_horizon),
      round_trip_cost_bps: Number(form.round_trip_cost_bps),
      minimum_coverage_pct: Number(form.minimum_coverage_pct),
      minimum_mature_baskets: Number(form.minimum_mature_baskets),
      minimum_mean_excess_return_pct: Number(form.minimum_mean_excess_return_pct),
      minimum_positive_excess_rate_pct: Number(form.minimum_positive_excess_rate_pct),
      maximum_cohort_drawdown_pct: Number(form.maximum_cohort_drawdown_pct),
      maximum_manual_pilot_pct: Number(form.maximum_manual_pilot_pct),
      latest_basket_max_age_days: Number(form.latest_basket_max_age_days),
    })
  }

  return (
    <details className="profit-policy">
      <summary>
        <span><SlidersHorizontal size={17} /><b>收益验证政策</b><small>v{scorecard.policy?.version_no || 0} · {scorecard.policy?.persisted ? '已保存不可变版本' : '系统默认，尚未保存'}</small></span>
        <em>成本、样本、超额与回撤门槛</em>
      </summary>
      <form onSubmit={submit}>
        <label>主验证窗口<select value={form.primary_horizon ?? 20} onChange={(event) => update('primary_horizon', event.target.value)}>
          {(values.evaluation_horizons || [5, 20, 60]).map((item) => <option key={item} value={item}>{item} 个交易日</option>)}
        </select></label>
        <label>往返成本情景（bps）<input type="number" min="10" max="500" step="1" value={fieldNumber(form.round_trip_cost_bps)} onChange={(event) => update('round_trip_cost_bps', event.target.value)} /></label>
        <label>最低数据覆盖（%）<input type="number" min="80" max="100" step="1" value={fieldNumber(form.minimum_coverage_pct)} onChange={(event) => update('minimum_coverage_pct', event.target.value)} /></label>
        <label>最少成熟批次<input type="number" min="6" max="100" step="1" value={fieldNumber(form.minimum_mature_baskets)} onChange={(event) => update('minimum_mature_baskets', event.target.value)} /></label>
        <label>最低平均净超额（%）<input type="number" min="0" max="20" step="0.1" value={fieldNumber(form.minimum_mean_excess_return_pct)} onChange={(event) => update('minimum_mean_excess_return_pct', event.target.value)} /></label>
        <label>最低胜基准比例（%）<input type="number" min="50" max="100" step="1" value={fieldNumber(form.minimum_positive_excess_rate_pct)} onChange={(event) => update('minimum_positive_excess_rate_pct', event.target.value)} /></label>
        <label>最大批次回撤（%）<input type="number" min="3" max="25" step="1" value={fieldNumber(form.maximum_cohort_drawdown_pct)} onChange={(event) => update('maximum_cohort_drawdown_pct', event.target.value)} /></label>
        <label>人工试运行上限（%）<input type="number" min="0.5" max="5" step="0.5" value={fieldNumber(form.maximum_manual_pilot_pct)} onChange={(event) => update('maximum_manual_pilot_pct', event.target.value)} /></label>
        <label>最新组合有效期（日）<input type="number" min="3" max="30" step="1" value={fieldNumber(form.latest_basket_max_age_days)} onChange={(event) => update('latest_basket_max_age_days', event.target.value)} /></label>
        <div className="profit-policy-action">
          <p>保存会创建新版本，不覆盖旧门槛；旧记分卡继续绑定原政策。</p>
          <button type="submit" disabled={saving}>{saving ? <><span className="spinner" />保存中</> : <><Save size={15} />保存新版本</>}</button>
        </div>
      </form>
    </details>
  )
}

function HorizonCard({ item, primary }) {
  const ci = item.mean_excess_ci95 || {}
  const familyCi = item.mean_excess_familywise_ci95 || {}
  return (
    <article className={`profit-horizon ${primary ? 'primary' : ''}`}>
      <div className="profit-horizon-head">
        <span><b>{item.horizon_trading_days} 日</b>{primary && <em>主窗口</em>}</span>
        <small>{item.mature_count} 成熟 · {item.pending_count} 观察中{item.overlap_excluded_count ? ` · ${item.overlap_excluded_count} 重叠排除` : ''}</small>
      </div>
      <div className="profit-horizon-value">
        <span><small>成本后平均超额</small><b className={performanceClass(item.mean_net_excess_return_pct)}>{percent(item.mean_net_excess_return_pct, 2, true)}</b></span>
        <span><small>成本后平均收益</small><b>{percent(item.mean_net_return_pct, 2, true)}</b></span>
      </div>
      <dl>
        <div><dt>胜过基准</dt><dd>{percent(item.positive_excess_rate_pct, 1)}</dd></div>
        <div><dt>最差批次回撤</dt><dd>{percent(item.worst_cohort_drawdown_pct, 1)}</dd></div>
        <div><dt>95% 超额区间</dt><dd>{ci.lower == null ? '样本不足' : `${percent(ci.lower, 2, true)} ~ ${percent(ci.upper, 2, true)}`}</dd></div>
        {Number(familyCi.strategy_family_size) > 1 && <div><dt>{familyCi.strategy_family_size} 策略校正区间</dt><dd>{familyCi.lower == null ? '样本不足' : `${percent(familyCi.lower, 2, true)} ~ ${percent(familyCi.upper, 2, true)}`}</dd></div>}
        <div><dt>独立起点间隔</dt><dd>≥ {item.independence_spacing_days || '—'} 天</dd></div>
      </dl>
    </article>
  )
}

function CapitalPlan({ scorecard }) {
  const plan = scorecard.capital_plan || {}
  const gate = scorecard.capital_gate || {}
  if (plan.status !== 'available') {
    return (
      <section className="profit-capital blocked">
        <div className="profit-section-head"><span><WalletCards size={18} /><b>资金试运行计划</b></span><em>当前资金资格 0%</em></div>
        <div className="profit-capital-blocked"><ShieldCheck size={28} /><div><b>不会为了“给建议”强行分配资金</b><p>{(plan.reasons || gate.reasons || ['收益证据尚未通过']).join('；')}</p></div></div>
      </section>
    )
  }
  return (
    <section className="profit-capital available">
      <div className="profit-section-head"><span><WalletCards size={18} /><b>受限人工试运行计划</b></span><em>仍不授权下单</em></div>
      <div className="profit-capital-kpis">
        <span><small>当前组合</small><b>{money(plan.portfolio_value_cny)}</b></span>
        <span><small>月度预算</small><b>{money(plan.monthly_budget_cny)}</b></span>
        <span><small>试运行上限</small><b>{percent(plan.pilot_cap_pct, 1)}</b></span>
        <span><small>本次计划</small><b>{money(plan.planned_budget_cny)}</b></span>
        <span><small>继续留现金</small><b>{money(plan.unallocated_cash_cny)}</b></span>
      </div>
      <div className="profit-allocation-list">
        {(plan.positions || []).map((position) => (
          <div key={`${position.market}:${position.symbol}`}>
            <span><b>{position.name}</b><small>{position.market} · {position.symbol} · 来源权重 {percent(position.source_weight_pct, 1)}</small></span>
            <strong>{money(position.planned_amount_cny)}</strong>
          </div>
        ))}
      </div>
      {!!plan.reasons?.length && <div className="profit-plan-reasons">{plan.reasons.map((reason) => <span key={reason}><AlertTriangle size={13} />{reason}</span>)}</div>}
      <p className="profit-plan-notice">{plan.notice}</p>
    </section>
  )
}

export default function ProfitLab() {
  const [lab, setLab] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [policySaving, setPolicySaving] = useState(false)
  const [scoreSaving, setScoreSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function load({ quiet = false } = {}) {
    if (quiet) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const result = await fetchOpportunityProfitLab()
      setLab(result)
      setSelectedId((current) => result.items?.some((item) => item.strategy.id === current) ? current : result.items?.[0]?.strategy.id || null)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => { load() }, [])

  const items = lab?.items || []
  const selected = items.find((item) => item.strategy.id === selectedId) || items[0] || null
  const primaryHorizon = selected?.policy?.values?.primary_horizon
  const primaryCohorts = useMemo(
    () => selected?.cohorts?.[String(primaryHorizon)] || [],
    [selected, primaryHorizon],
  )
  const gateMeta = GATE_META[selected?.capital_gate?.status] || GATE_META.empty

  async function savePolicy(policy) {
    if (!selected) return
    setPolicySaving(true); setError(''); setMessage('')
    try {
      const saved = await createOpportunityProfitPolicy(selected.strategy.id, policy)
      setMessage(`已保存收益验证政策 v${saved.version_no}，后续记分卡会绑定该版本。`)
      await load({ quiet: true })
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setPolicySaving(false)
    }
  }

  async function freezeScorecard() {
    if (!selected) return
    setScoreSaving(true); setError(''); setMessage('')
    try {
      const result = await createOpportunityProfitScorecard(selected.strategy.id)
      setMessage(result.created ? `已冻结记分卡 ${result.item.id.slice(-8)}，历史证据不会被后续行情覆盖。` : '当前证据与已有记分卡一致，没有重复写入。')
      await load({ quiet: true })
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setScoreSaving(false)
    }
  }

  if (loading) return <div className="page-loading"><span className="spinner" />正在汇总前瞻收益证据</div>
  if (!items.length) return (
    <section className="profit-empty">
      <FlaskConical size={32} />
      <h3>还没有可验证的机会策略</h3>
      <p>先建立并运行机会策略，再冻结至少一个纸面组合。系统会自动采集 5/20/60 交易日观察，不用手工每天刷新。</p>
    </section>
  )

  return (
    <div className="profit-lab">
      <section className="profit-summary">
        <div><Database size={18} /><span><small>活动策略</small><b>{lab.summary?.strategy_count || 0}</b></span></div>
        <div><FlaskConical size={18} /><span><small>历史试验版本</small><b>{lab.summary?.tested_strategy_version_count || 0}</b></span></div>
        <div><BadgeDollarSign size={18} /><span><small>有资金资格</small><b>{lab.summary?.capital_eligible_count || 0}</b></span></div>
        <div><CalendarClock size={18} /><span><small>观察中批次</small><b>{lab.summary?.collecting_basket_count || 0}</b></span></div>
        <div><Activity size={18} /><span><small>有效观察点</small><b>{lab.summary?.valid_observation_count || 0}</b></span></div>
        <button className="ghost" onClick={() => load({ quiet: true })} disabled={refreshing}>{refreshing ? <span className="spinner" /> : <RefreshCw size={15} />}刷新证据</button>
      </section>
      {error && <div className="error">{error}</div>}
      {message && <div className="profit-message"><CheckCircle2 size={15} />{message}</div>}

      <div className="profit-layout">
        <aside className="profit-strategy-list">
          <div><span className="eyebrow">策略收益榜</span><b>不按回测收益排序</b></div>
          {items.map((item) => {
            const meta = GATE_META[item.capital_gate?.status] || GATE_META.empty
            const primary = item.horizons?.find((row) => row.horizon_trading_days === item.policy?.values?.primary_horizon)
            return (
              <button key={item.strategy.id} className={item.strategy.id === selected?.strategy.id ? 'active' : ''} onClick={() => setSelectedId(item.strategy.id)}>
                <span><b>{item.strategy.name}</b><small>策略 v{item.strategy.version_no} · {primary?.mature_count || 0} 个成熟批次</small></span>
                <em className={meta.tone}>{meta.label}</em>
                <strong className={performanceClass(primary?.mean_net_excess_return_pct)}>{percent(primary?.mean_net_excess_return_pct, 2, true)}</strong>
              </button>
            )
          })}
        </aside>

        {selected && <main className="profit-main">
          <section className={`profit-gate ${gateMeta.tone}`}>
            <div className="profit-gate-icon">{selected.capital_gate?.capital_eligible ? <TrendingUp size={28} /> : selected.capital_gate?.status === 'suspended' ? <TrendingDown size={28} /> : <FlaskConical size={28} />}</div>
            <div>
              <span className="eyebrow">资金资格门禁 · {selected.strategy.name}</span>
              <h2>{gateMeta.label}</h2>
              <p>{selected.capital_gate?.reasons?.join('；') || gateMeta.detail}</p>
              <div className="profit-gate-tags">
                {Object.entries(selected.capital_gate?.checks || {}).map(([key, passed]) => <span key={key} className={passed ? 'pass' : 'fail'}>{passed ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}{CHECK_LABELS[key] || key.replaceAll('_', ' ')}</span>)}
              </div>
            </div>
            <div className="profit-gate-budget"><small>最大人工试运行</small><b>{percent(selected.capital_gate?.maximum_manual_pilot_pct, 1)}</b><em>自动交易始终关闭</em></div>
          </section>

          <section className="profit-automation">
            <div><RefreshCw size={18} /><span><b>日终自动前瞻观察</b><small>{selected.automation?.observation_interval}</small></span></div>
            <dl>
              <div><dt>冻结批次</dt><dd>{selected.automation?.basket_count || 0}</dd></div>
              <div><dt>观察中</dt><dd>{selected.automation?.collecting_basket_count || 0}</dd></div>
              <div><dt>完成 60 日</dt><dd>{selected.automation?.completed_basket_count || 0}</dd></div>
              <div><dt>证据截止</dt><dd>{dateTime(selected.evidence_cutoff_at)}</dd></div>
            </dl>
          </section>

          <section className="profit-horizons">
            <div className="profit-section-head"><span><Activity size={18} /><b>成本后前瞻收益与基准超额</b></span><em>每个冻结组合只算一个独立批次</em></div>
            <div className="profit-horizon-grid">{(selected.horizons || []).map((item) => <HorizonCard key={item.horizon_trading_days} item={item} primary={item.horizon_trading_days === primaryHorizon} />)}</div>
          </section>

          <CapitalPlan scorecard={selected} />

          <section className="profit-cohorts">
            <div className="profit-section-head"><span><Database size={18} /><b>{primaryHorizon} 日独立批次证据</b></span><em>{primaryCohorts.length} 个冻结样本</em></div>
            <div className="profit-table-scroll">
              <table>
                <thead><tr><th>冻结批次</th><th>成熟状态</th><th>成本后收益</th><th>基准</th><th>净超额</th><th>批次回撤</th><th>覆盖</th></tr></thead>
                <tbody>
                  {primaryCohorts.map((cohort) => <tr key={cohort.basket_id}>
                    <td><b>{String(cohort.basket_id).slice(-8)}</b><small>{dateTime(cohort.frozen_at)}</small></td>
                    <td><span className={`profit-cohort-status ${cohort.status}`}>{cohort.status === 'mature' ? `${cohort.trading_days_observed} 日成熟` : cohort.status === 'pending' ? `${cohort.trading_days_observed || 0} 日观察中` : '证据排除'}</span>{cohort.reasons?.[0] && <small>{cohort.reasons[0]}</small>}</td>
                    <td>{percent(cohort.net_return_pct, 2, true)}</td>
                    <td>{percent(cohort.benchmark_return_pct, 2, true)}</td>
                    <td className={performanceClass(cohort.net_excess_return_pct, 'delta-pos', 'delta-neg')}>{percent(cohort.net_excess_return_pct, 2, true)}</td>
                    <td>{percent(cohort.cohort_max_drawdown_pct, 2)}</td>
                    <td>{percent(cohort.position_coverage_pct, 0)}<small>基准 {percent(cohort.benchmark_coverage_pct, 0)}</small></td>
                  </tr>)}
                  {!primaryCohorts.length && <tr><td colSpan="7" className="hint">先冻结纸面组合，自动观察会在真实交易日推进成熟度。</td></tr>}
                </tbody>
              </table>
            </div>
          </section>

          <PolicyEditor scorecard={selected} saving={policySaving} onSave={savePolicy} />

          <section className="profit-audit">
            <div><ShieldCheck size={20} /><span><b>冻结本次收益证据</b><p>把当前政策、全部成熟/未成熟批次、统计门禁和资金资格写入不可变记分卡，便于以后验证策略是否真的持续有效。</p></span></div>
            <span>{selected.latest_persisted ? selected.latest_persisted.binding_current ? <>当前证据已冻结 {dateTime(selected.latest_persisted.created_at)} · <b>{String(selected.latest_persisted.id).slice(-8)}</b></> : <>存在历史记分卡，但当前策略、政策或证据已变化</> : '尚未冻结历史记分卡'}</span>
            <button onClick={freezeScorecard} disabled={scoreSaving}>{scoreSaving ? <><span className="spinner" />冻结中</> : <><Save size={15} />冻结当前记分卡</>}</button>
          </section>

          <details className="profit-method">
            <summary>方法、基准和限制</summary>
            <div>{Object.entries(selected.methodology || {}).map(([key, value]) => <p key={key}><b>{key}</b>{value}</p>)}</div>
            <div className="profit-limitations">{(selected.limitations || []).map((item) => <span key={item}><AlertTriangle size={13} />{item}</span>)}</div>
          </details>
        </main>}
      </div>
    </div>
  )
}
