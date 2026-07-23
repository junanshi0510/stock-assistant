import { useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CircleDollarSign,
  GitCompareArrows,
  History,
  Layers3,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  UsersRound,
  WalletCards,
} from 'lucide-react'
import {
  fetchOpportunityCommittee,
  fetchOpportunityCommitteeMandates,
  freezeOpportunityCommittee,
} from '../../api/opportunities'

const STATUS_META = {
  active: { label: '委员会运行中', tone: 'active', detail: '至少两个策略通过前瞻与独立贡献检查' },
  concentrated: { label: '集中度受限', tone: 'concentrated', detail: '策略数量或独立性不足，主动提高现金比例' },
  degraded: { label: '策略已降级', tone: 'degraded', detail: '至少一个策略触发衰减或熔断检查' },
  collecting: { label: '继续收集证据', tone: 'collecting', detail: '尚无策略获得委员会资金权重' },
}

const STRATEGY_STATE = {
  approved: { label: '入选袖套', tone: 'approved' },
  reserve: { label: '替补观察', tone: 'reserve' },
  suspended: { label: '已熔断', tone: 'suspended' },
  collecting: { label: '证据积累中', tone: 'collecting' },
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

function StrategySleeve({ item }) {
  const meta = STRATEGY_STATE[item.committee_state] || STRATEGY_STATE.collecting
  const recent = item.recent_decay || {}
  const lower = item.familywise_ci95?.lower
  return (
    <article className={`committee-strategy ${meta.tone}`}>
      <div className="committee-strategy-head">
        <span>
          <b>{item.strategy_name || item.strategy_id}</b>
          <small>策略证据 {shortId(item.scorecard_id)} · {item.mature_cohort_count || 0} 个独立批次</small>
        </span>
        <em>{meta.label}</em>
      </div>
      <div className="committee-weight">
        <div><span style={{ width: `${Math.min(100, Number(item.committee_weight_pct) || 0)}%` }} /></div>
        <b>{number(item.committee_weight_pct, 1, '%')}</b>
      </div>
      <dl>
        <div><dt>家族校正下界</dt><dd className={Number(lower) > 0 ? 'positive' : ''}>{signed(lower)}</dd></div>
        <div><dt>胜基准比例</dt><dd>{number(item.positive_excess_rate_pct, 1, '%')}</dd></div>
        <div><dt>独立贡献</dt><dd>{number(item.unique_contribution_pct, 1, '%')}</dd></div>
        <div><dt>最差批次回撤</dt><dd>{number(item.worst_cohort_drawdown_pct, 1, '%')}</dd></div>
        <div><dt>近期 3 期超额</dt><dd className={Number(recent.mean_net_excess_return_pct) >= 0 ? 'positive' : 'negative'}>{signed(recent.mean_net_excess_return_pct)}</dd></div>
        <div><dt>近期状态</dt><dd>{recent.three_consecutive_nonpositive ? '连续失败熔断' : recent.warning ? '衰减降权' : recent.window_cohort_count >= 3 ? '稳定' : '样本未满'}</dd></div>
      </dl>
      {!!item.committee_reasons?.length && (
        <details>
          <summary>查看准入 / 淘汰原因</summary>
          {item.committee_reasons.map((reason) => <p key={reason}>{reason}</p>)}
        </details>
      )}
    </article>
  )
}

export default function InvestmentCommittee() {
  const [committee, setCommittee] = useState(null)
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
      const [current, mandates] = await Promise.all([
        fetchOpportunityCommittee(),
        fetchOpportunityCommitteeMandates(20),
      ])
      setCommittee(current)
      setHistory(mandates.items || [])
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
    setError('')
    setMessage('')
    try {
      const result = await freezeOpportunityCommittee()
      setMessage(result.created
        ? `已冻结委员会指令 ${shortId(result.item.id)}，本次策略、候选、权重与淘汰理由均不可改写。`
        : '当前前瞻证据没有变化，沿用已有不可变委员会指令。')
      await load({ quiet: true })
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="page-loading"><span className="spinner" />正在召开策略投资委员会</div>

  const status = STATUS_META[committee?.status] || STATUS_META.collecting
  const summary = committee?.summary || {}
  const drift = committee?.drift || {}
  const candidates = committee?.candidate_consensus || []
  const strategies = committee?.strategies || []
  const redundancy = committee?.redundancy_matrix || []
  const latest = committee?.persistence?.latest_mandate

  return (
    <div className="investment-committee">
      {error && <div className="error">{error}</div>}
      {message && <div className="committee-message"><CheckCircle2 size={16} />{message}</div>}

      <section className={`committee-hero ${status.tone}`}>
        <div className="committee-hero-icon"><UsersRound size={30} /></div>
        <div>
          <span className="eyebrow">自适应策略投资委员会 · {committee?.engine_version}</span>
          <h2>{status.label}</h2>
          <p>{committee?.headline || status.detail}</p>
          <small>证据截止 {dateTime(committee?.evidence_cutoff_at)} · 只使用冻结后的前瞻结果，不读取回测胜率当作未来概率</small>
        </div>
        <div className="committee-hero-actions">
          <button className="ghost" onClick={() => load({ quiet: true })} disabled={refreshing}>
            {refreshing ? <span className="spinner" /> : <RefreshCw size={15} />}刷新证据
          </button>
          <button onClick={freeze} disabled={saving}>
            {saving ? <><span className="spinner" />冻结中</> : <><Save size={15} />冻结当前指令</>}
          </button>
        </div>
      </section>

      <section className="committee-kpis">
        <article><Layers3 size={18} /><span><small>入选策略袖套</small><b>{summary.selected_strategy_count || 0} / {summary.strategy_count || 0}</b></span></article>
        <article><CircleDollarSign size={18} /><span><small>委员会可投入</small><b>{number(summary.committee_investable_pct, 1, '%')}</b></span></article>
        <article><WalletCards size={18} /><span><small>候选模型投入</small><b>{number(summary.candidate_model_invested_pct, 1, '%')}</b></span></article>
        <article><ShieldCheck size={18} /><span><small>主动现金保留</small><b>{number(summary.cash_reserve_pct, 1, '%')}</b></span></article>
        <article><GitCompareArrows size={18} /><span><small>策略平均冗余</small><b>{number(summary.average_selected_redundancy_pct, 1, '%')}</b></span></article>
        <article><Activity size={18} /><span><small>熔断 / 停用</small><b>{summary.suspended_strategy_count || 0}</b></span></article>
      </section>

      <section className={`committee-drift ${drift.rebalance_required ? 'rebalance' : 'stable'}`}>
        <div>{drift.rebalance_required ? <AlertTriangle size={21} /> : <CheckCircle2 size={21} />}<span><b>{drift.state === 'initial_mandate' ? '首次建立模型组合' : drift.rebalance_required ? '模型组合需要复核再平衡' : '漂移仍在控制带内'}</b><small>候选单边换手 {number(drift.candidate_one_way_turnover_pct, 1, '%')} · 触发阈值 {number(drift.threshold_pct, 0, '%')}</small></span></div>
        <p>{drift.entered_strategy_ids?.length ? `新进入 ${drift.entered_strategy_ids.length} 个策略；` : ''}{drift.exited_strategy_ids?.length ? `退出 ${drift.exited_strategy_ids.length} 个策略；` : ''}没有达到阈值时不追逐小幅排名变化。</p>
      </section>

      <section className="committee-section">
        <div className="committee-section-head">
          <div><Sparkles size={19} /><span><b>策略袖套与自动淘汰</b><small>等权为锚，只允许保守证据与独立贡献做窄幅倾斜</small></span></div>
          <em>单策略最高 50% · 最多 3 个</em>
        </div>
        <div className="committee-strategy-grid">
          {strategies.map((item) => <StrategySleeve key={item.strategy_id} item={item} />)}
          {!strategies.length && <div className="committee-empty">先在收益实验室积累并冻结前瞻记分卡。</div>}
        </div>
      </section>

      <section className="committee-section">
        <div className="committee-section-head">
          <div><BarChart3 size={19} /><span><b>候选共识模型组合</b><small>策略袖套权重 × 冻结组合内权重；相对优先级，不是上涨概率</small></span></div>
          <em>单一候选模型上限 25%</em>
        </div>
        <div className="committee-table-scroll">
          <table className="committee-candidates">
            <thead><tr><th>排名 / 候选</th><th>委员会观点</th><th>模型目标</th><th>策略支持</th><th>一致度</th><th>执行边界</th></tr></thead>
            <tbody>
              {candidates.map((item) => (
                <tr key={`${item.market}:${item.symbol}`}>
                  <td><span className="committee-rank">{item.committee_rank}</span><b>{item.name}</b><small>{item.market} · {item.symbol}</small></td>
                  <td><strong>{item.view_label}</strong><small>{item.candidate_cap_applied ? '触发 25% 单候选上限，超出转现金' : '未触发集中度上限'}</small></td>
                  <td><div className="committee-target"><span style={{ width: `${Math.min(100, Number(item.model_target_weight_pct) * 4 || 0)}%` }} /></div><b>{number(item.model_target_weight_pct, 2, '%')}</b></td>
                  <td>{item.support_count} 个<small>{item.sources?.map((source) => source.strategy_name).join(' / ')}</small></td>
                  <td>{number(item.agreement_pct, 1, '%')}</td>
                  <td><span className="no-probability">无伪概率</span><small>不自动下单</small></td>
                </tr>
              ))}
              {!candidates.length && <tr><td colSpan="6" className="hint">委员会尚未形成可投入候选，资金保持现金。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <div className="committee-lower-grid">
        <section className="committee-section">
          <div className="committee-section-head">
            <div><GitCompareArrows size={19} /><span><b>策略冗余矩阵</b><small>候选重叠优先；共同前瞻月份不足时不计算相关性</small></span></div>
          </div>
          <div className="committee-redundancy-list">
            {redundancy.map((item) => (
              <article key={`${item.first_strategy_id}:${item.second_strategy_id}`}>
                <span><b>{item.first_strategy_name}</b><i>×</i><b>{item.second_strategy_name}</b></span>
                <dl>
                  <div><dt>候选重叠</dt><dd>{number(item.current_position_overlap_pct, 1, '%')}</dd></div>
                  <div><dt>前瞻相关</dt><dd>{item.correlation_decision_eligible ? number(item.cohort_excess_correlation, 2) : `样本 ${item.aligned_cohort_months}/4`}</dd></div>
                  <div><dt>最终冗余</dt><dd>{number(item.redundancy_pct, 1, '%')}</dd></div>
                </dl>
              </article>
            ))}
            {!redundancy.length && <div className="committee-empty">至少两个策略获得准入后才计算相互冗余。</div>}
          </div>
        </section>

        <section className="committee-section">
          <div className="committee-section-head">
            <div><History size={19} /><span><b>不可变委员会指令</b><small>历史权重和淘汰理由不会被后续行情覆盖</small></span></div>
            <em>{latest ? `当前 ${committee?.persistence?.binding_current ? '已绑定' : '证据已变化'}` : '尚未冻结'}</em>
          </div>
          <div className="committee-history">
            {history.slice(0, 8).map((item) => (
              <article key={item.id}>
                <span><b>{STATUS_META[item.status]?.label || item.status}</b><small>{dateTime(item.created_at)}</small></span>
                <code>{shortId(item.id)}</code>
                <em>{item.result?.summary?.selected_strategy_count || 0} 策略 · 现金 {number(item.result?.summary?.cash_reserve_pct, 1, '%')}</em>
              </article>
            ))}
            {!history.length && <div className="committee-empty">点击“冻结当前指令”建立第一条审计基线。</div>}
          </div>
        </section>
      </div>

      <details className="committee-method">
        <summary><ShieldCheck size={16} />方法、赚钱逻辑与边界</summary>
        <div>
          {Object.entries(committee?.methodology || {}).map(([key, value]) => <p key={key}><b>{key}</b><span>{value}</span></p>)}
          <p><b>结果解释</b><span>{committee?.boundaries?.interpretation}</span></p>
        </div>
      </details>
    </div>
  )
}
