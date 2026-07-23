import { useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FlaskConical,
  Gauge,
  History,
  RefreshCw,
  Save,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import {
  fetchOpportunityRegime,
  fetchOpportunityRegimeSnapshots,
  freezeOpportunityRegime,
} from '../../api/opportunities'

const REGIME_META = {
  risk_on: { label: '候选池偏强', tone: 'risk-on', icon: TrendingUp },
  mixed: { label: '震荡 / 分歧', tone: 'mixed', icon: Activity },
  defensive: { label: '防守环境', tone: 'defensive', icon: TrendingDown },
  insufficient: { label: '状态证据不足', tone: 'insufficient', icon: Database },
}

const FIT_META = {
  preferred: { label: '同环境优先', tone: 'preferred' },
  neutral: { label: '中性使用', tone: 'neutral' },
  underweight: { label: '同环境降权', tone: 'underweight' },
  avoid: { label: '环境失配熔断', tone: 'avoid' },
  collecting: { label: '积累同环境样本', tone: 'collecting' },
  unavailable: { label: '当前环境不可判', tone: 'unavailable' },
}

function number(value, digits = 1, suffix = '') {
  if (value == null || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed.toFixed(digits)}${suffix}` : '—'
}

function signed(value, digits = 2) {
  if (value == null || value === '') return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`
}

function dateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function shortId(value) {
  return value ? String(value).slice(-10) : '—'
}

function MarketCard({ item }) {
  const meta = REGIME_META[item.status] || REGIME_META.insufficient
  const StateIcon = meta.icon
  return (
    <article className={`regime-market-card ${meta.tone}`}>
      <header>
        <span className="regime-market-icon"><StateIcon size={20} /></span>
        <span><b>{item.market}</b><small>{meta.label}</small></span>
        <em>{item.evidence_grade === 'strong' ? '证据较厚' : item.evidence_grade === 'usable' ? '证据可用' : item.evidence_grade === 'thin' ? '单一来源' : '不可判'}</em>
      </header>
      <div className="regime-risk-bar">
        <span><i style={{ width: `${Math.min(100, Number(item.risk_budget_pct) || 0)}%` }} /></span>
        <b>{number(item.risk_budget_pct, 0, '%')}</b>
      </div>
      <dl>
        <div><dt>三月收益中位</dt><dd className={Number(item.median_return_3m) >= 0 ? 'positive' : 'negative'}>{signed(item.median_return_3m)}</dd></div>
        <div><dt>上涨广度</dt><dd>{number(item.positive_breadth_pct, 1, '%')}</dd></div>
        <div><dt>年化波动中位</dt><dd>{number(item.median_annual_vol, 1, '%')}</dd></div>
        <div><dt>来源一致度</dt><dd>{number(item.agreement_pct, 1, '%')}</dd></div>
      </dl>
      <footer>
        <span>{item.source_count || 0} 个策略版本 · {item.candidate_sample_count || 0} 个候选样本</span>
        <small>{item.latest_observed_at ? `最新 ${dateTime(item.latest_observed_at)}` : '14 天内无有效状态'}</small>
      </footer>
    </article>
  )
}

export default function MarketRegimeHub() {
  const [hub, setHub] = useState(null)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function load({ quiet = false } = {}) {
    if (quiet) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const [current, snapshots] = await Promise.all([
        fetchOpportunityRegime(),
        fetchOpportunityRegimeSnapshots(20),
      ])
      setHub(current)
      setHistory(snapshots.items || [])
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => { load() }, [])

  async function freeze() {
    setSaving(true)
    setMessage('')
    setError('')
    try {
      const response = await freezeOpportunityRegime()
      setMessage(response.created
        ? `已冻结状态与适配快照 ${shortId(response.item.id)}，来源、风险预算和策略判断均不可改写。`
        : '底层不可变证据没有变化，沿用已有市场状态快照。')
      await load({ quiet: true })
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="page-loading"><span className="spinner" />正在计算市场状态与策略适配</div>

  const status = REGIME_META[hub?.status] || REGIME_META.insufficient
  const StatusIcon = status.icon
  const summary = hub?.summary || {}
  const risk = hub?.portfolio_risk_budget || {}
  const transition = hub?.transition || {}
  const markets = hub?.market_states || []
  const fits = hub?.strategy_fits || []
  const latest = hub?.persistence?.latest_snapshot

  return (
    <div className="market-regime-hub">
      {error && <div className="error">{error}</div>}
      {message && <div className="regime-message"><CheckCircle2 size={16} />{message}</div>}

      <section className={`regime-hero ${status.tone}`}>
        <div className="regime-hero-icon"><StatusIcon size={30} /></div>
        <div>
          <span className="eyebrow">市场状态与策略适配中枢 · {hub?.engine_version}</span>
          <h2>{status.label}</h2>
          <p>{hub?.headline}</p>
          <small>证据截止 {dateTime(hub?.evidence_cutoff_at)} · 状态仅覆盖策略候选池，不是全市场涨跌概率</small>
        </div>
        <div className="regime-hero-actions">
          <button className="ghost" onClick={() => load({ quiet: true })} disabled={refreshing}>
            {refreshing ? <span className="spinner" /> : <RefreshCw size={15} />}刷新证据
          </button>
          <button onClick={freeze} disabled={saving}>
            {saving ? <><span className="spinner" />冻结中</> : <><Save size={15} />冻结状态快照</>}
          </button>
        </div>
      </section>

      <section className="regime-kpis">
        <article><Gauge size={18} /><span><small>委员会风险预算</small><b>{number(risk.budget_pct_of_committee_limit, 0, '%')}</b></span></article>
        <article><WalletCards size={18} /><span><small>额外现金缓冲</small><b>{number(risk.minimum_cash_added_pct_of_committee_limit, 0, '%')}</b></span></article>
        <article><Database size={18} /><span><small>当前状态来源</small><b>{summary.source_count || 0}</b></span></article>
        <article><Activity size={18} /><span><small>候选样本</small><b>{summary.candidate_sample_count || 0}</b></span></article>
        <article><FlaskConical size={18} /><span><small>同环境前瞻批次</small><b>{summary.matched_regime_cohort_count || 0}</b></span></article>
        <article><ShieldCheck size={18} /><span><small>失配熔断策略</small><b>{summary.avoid_strategy_count || 0}</b></span></article>
      </section>

      <section className={`regime-transition ${transition.state === 'changed' ? 'changed' : 'stable'}`}>
        <div>
          {transition.state === 'changed' ? <AlertTriangle size={20} /> : <CheckCircle2 size={20} />}
          <span>
            <b>{transition.state === 'initial' ? '等待首份冻结基线' : transition.state === 'changed' ? '市场状态或风险预算发生实质变化' : '相对上一快照保持稳定'}</b>
            <small>当前风险乘数 {number(transition.current_risk_budget_multiplier, 2)} · 上次 {number(transition.previous_risk_budget_multiplier, 2)}</small>
          </span>
        </div>
        <p>{transition.market_changes?.length
          ? transition.market_changes.map((item) => `${item.market}：${REGIME_META[item.from]?.label || item.from} → ${REGIME_META[item.to]?.label || item.to}`).join('；')
          : '没有市场跨越状态阈值；小幅指标变化不会触发追涨杀跌。'}</p>
      </section>

      <section className="regime-section">
        <div className="regime-section-head">
          <div><Activity size={19} /><span><b>A / H / 美股候选池状态</b><small>多策略版本共识 + 新鲜度 + 候选样本 + 波动折扣</small></span></div>
          <em>风险乘数永远 ≤ 1 · 不加杠杆</em>
        </div>
        <div className="regime-market-grid">
          {markets.map((item) => <MarketCard key={item.market} item={item} />)}
        </div>
      </section>

      <section className="regime-section">
        <div className="regime-section-head">
          <div><FlaskConical size={19} /><span><b>策略 × 当前环境适配矩阵</b><small>只比较冻结时处于同类环境的独立前瞻批次，不混用其他市场阶段</small></span></div>
          <em>4 个样本才倾斜 · 8 个样本达到完整权重</em>
        </div>
        <div className="regime-table-scroll">
          <table className="regime-fit-table">
            <thead><tr><th>策略 / 当前环境</th><th>适配判断</th><th>同环境样本</th><th>平均超额</th><th>跑赢比例</th><th>95% 区间</th><th>策略倾斜</th><th>风险预算</th></tr></thead>
            <tbody>
              {fits.map((item) => {
                const fit = FIT_META[item.fit_status] || FIT_META.unavailable
                const current = item.current_regime || {}
                return (
                  <tr key={item.strategy_id}>
                    <td><b>{item.strategy_name || item.strategy_id}</b><small>{REGIME_META[current.status]?.label || '环境不可判'} · 覆盖 {number(current.coverage_pct, 0, '%')}</small></td>
                    <td><span className={`regime-fit-badge ${fit.tone}`}>{fit.label}</span><small>{item.reasons?.[0]}</small></td>
                    <td><b>{item.matched_cohort_count || 0} / {item.minimum_cohort_count || 4}</b><small>可靠度 {number(item.reliability_pct, 0, '%')}</small></td>
                    <td className={Number(item.mean_net_excess_return_pct) >= 0 ? 'positive' : 'negative'}>{signed(item.mean_net_excess_return_pct)}</td>
                    <td>{number(item.positive_excess_rate_pct, 1, '%')}</td>
                    <td><b>{signed(item.mean_excess_ci95?.lower)}</b><small>至 {signed(item.mean_excess_ci95?.upper)}</small></td>
                    <td>{number(item.allocation_tilt, 2)}×<small>{item.recent_three_nonpositive ? '三期失配熔断' : '向 1.00 收缩'}</small></td>
                    <td>{number(Number(item.market_risk_budget_multiplier) * 100, 0, '%')}<small>只减不增</small></td>
                  </tr>
                )
              })}
              {!fits.length && <tr><td colSpan="8" className="hint">先创建策略、冻结纸面组合并在收益实验室积累前瞻批次。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <div className="regime-lower-grid">
        <section className="regime-section">
          <div className="regime-section-head">
            <div><History size={19} /><span><b>不可变状态历史</b><small>后续行情不会覆盖当时采用的来源、状态和预算</small></span></div>
            <em>{latest ? `当前 ${hub?.persistence?.binding_current ? '已绑定' : '证据已变化'}` : '尚未冻结'}</em>
          </div>
          <div className="regime-history">
            {history.slice(0, 8).map((item) => (
              <article key={item.id}>
                <span><b>{REGIME_META[item.status]?.label || item.status}</b><small>{dateTime(item.created_at)}</small></span>
                <code>{shortId(item.id)}</code>
                <em>风险 {number(Number(item.result?.portfolio_risk_budget?.multiplier) * 100, 0, '%')} · 优先 {item.result?.summary?.preferred_strategy_count || 0} · 熔断 {item.result?.summary?.avoid_strategy_count || 0}</em>
              </article>
            ))}
            {!history.length && <div className="regime-empty">点击“冻结状态快照”建立第一份审计基线。</div>}
          </div>
        </section>

        <section className="regime-guardrail">
          <ShieldCheck size={22} />
          <div>
            <span className="eyebrow">决策护栏</span>
            <h3>偏强不等于“满仓”，防守也不等于预测下跌</h3>
            <p>状态层只决定原委员会限额还保留多少，并按同环境真实前瞻证据重排策略。它不会发布上涨概率、突破人工试运行上限或自动创建订单。</p>
            <dl>
              <div><dt>总风险可高于基线</dt><dd>否</dd></div>
              <div><dt>允许杠杆</dt><dd>否</dd></div>
              <div><dt>自动交易</dt><dd>否</dd></div>
            </dl>
          </div>
        </section>
      </div>

      <details className="regime-method">
        <summary><Database size={16} />查看完整方法与证据边界</summary>
        <div>
          {Object.entries(hub?.methodology || {}).map(([key, value]) => <p key={key}><b>{key}</b><span>{value}</span></p>)}
          <p><b>结果解释</b><span>{hub?.boundaries?.interpretation}</span></p>
        </div>
      </details>
    </div>
  )
}
