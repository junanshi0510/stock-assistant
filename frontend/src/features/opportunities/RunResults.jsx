import { useMemo, useState } from 'react'
import { AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, Clock3, DatabaseZap, ExternalLink, GitCompareArrows, Layers3, ShieldAlert, WalletCards } from 'lucide-react'

const STATUS = {
  qualified: ['入围', 'qualified'],
  watch: ['观察', 'watch'],
  rejected: ['淘汰', 'rejected'],
  unavailable: ['数据失败', 'unavailable'],
}

function number(value, digits = 1, suffix = '') {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed.toFixed(digits)}${suffix}` : '—'
}

function signed(value, digits = 1) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`
}

function shortDate(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 16)
}

function StatusBadge({ value }) {
  const item = STATUS[value] || [value || '未知', 'unknown']
  return <span className={`opp-status ${item[1]}`}>{item[0]}</span>
}

function Progress({ run }) {
  const progress = run.progress || {}
  const total = Number(progress.total) || 0
  const completed = Number(progress.completed) || 0
  const pct = total > 0 ? Math.min(100, completed / total * 100) : 4
  return (
    <section className="opp-run-progress">
      <div className="opp-progress-icon"><DatabaseZap size={24} /></div>
      <div>
        <span className="eyebrow">持久扫描任务 · {run.status === 'queued' ? '等待 Worker' : '正在运行'}</span>
        <h3>{progress.message || '正在准备机会扫描'}</h3>
        <p>页面可以离开后再回来；策略版本、任务状态和最终结果都保存在数据库中。</p>
        <div className="opp-progress-track"><span style={{ width: `${pct}%` }} /></div>
        <small>{total ? `${completed} / ${total} 只候选` : '正在构建候选池'} · run {String(run.id).slice(-8)}</small>
      </div>
    </section>
  )
}
function Funnel({ funnel }) {
  const stages = [
    ['候选池', funnel.universe],
    ['数据可用', funnel.evaluated],
    ['硬门槛后', Math.max(0, funnel.evaluated - funnel.hard_rejected)],
    ['综合入围', funnel.qualified],
    ['组合持仓', funnel.portfolio],
  ]
  return (
    <div className="opp-funnel" aria-label="候选淘汰漏斗">
      {stages.map(([label, value], index) => (
        <div key={label} className="opp-funnel-stage">
          <span>{label}</span><b>{value ?? 0}</b>
          {index < stages.length - 1 && <i>→</i>}
        </div>
      ))}
    </div>
  )
}

function CandidateDetail({ candidate, goAnalyze }) {
  if (!candidate) return null
  const factors = Object.values(candidate.factors || {})
  return (
    <aside className="opp-candidate-detail">
      <div className="opp-detail-head">
        <div><span>{candidate.market}</span><h4>{candidate.name || candidate.symbol} <small>{candidate.symbol}</small></h4></div>
        <StatusBadge value={candidate.status} />
      </div>
      {candidate.error && <div className="error">{candidate.error}</div>}
      <div className="opp-detail-score">
        <div><small>综合分</small><b>{number(candidate.composite_score)}</b></div>
        <div><small>因子覆盖</small><b>{number(Number(candidate.factor_coverage) * 100, 0, '%')}</b></div>
        <div><small>三月收益</small><b>{signed(candidate.metrics?.return_3m)}</b></div>
        <div><small>最大回撤</small><b>{number(candidate.metrics?.max_drawdown_abs, 1, '%')}</b></div>
      </div>
      {factors.length > 0 && <div className="opp-factor-detail">
        {factors.map((factor) => (
          <div key={factor.label} className={!factor.available ? 'missing' : ''}>
            <span><b>{factor.label}</b><small>{factor.available ? `权重 ${factor.weight}` : '证据缺失'}</small></span>
            <em>{factor.available ? number(factor.score) : '中性 50'}</em>
            <div><i style={{ width: `${factor.available ? factor.score : 50}%` }} /></div>
            {factor.parts?.length > 0 && <small>{factor.parts.map((part) => `${part.label} ${number(part.peer_grade, 0)}`).join(' · ')}</small>}
          </div>
        ))}
      </div>}
      {candidate.disqualifiers?.length > 0 && <div className="opp-disqualifiers"><b>未通过的门槛</b>{candidate.disqualifiers.map((item) => <div key={item.code}><ShieldAlert size={14} /><span>{item.label}<small>实际 {String(item.actual ?? '缺失')} · 要求 {item.threshold}</small></span></div>)}</div>}
      <div className="opp-evidence-meta">
        <span>行情源 <b>{candidate.data?.source || '不可用'}</b></span>
        <span>数据日 <b>{candidate.data?.last_date || '—'}</b></span>
        <span>基本面 <b>{candidate.fundamentals?.available ? `${candidate.fundamentals.provider_rating || '可用'} · ${candidate.fundamentals.as_of || '日期未知'}` : candidate.fundamentals?.source_error || '不可用'}</b></span>
      </div>
      {candidate.status !== 'unavailable' && <button type="button" className="ghost opp-open-stock" onClick={() => goAnalyze(candidate.market, candidate.symbol)}>打开完整个股研究 <ExternalLink size={14} /></button>}
    </aside>
  )
}

function CandidateTable({ candidates, goAnalyze }) {
  const [filter, setFilter] = useState('all')
  const [selectedKey, setSelectedKey] = useState(null)
  const visible = useMemo(() => candidates.filter((item) => filter === 'all' || item.status === filter), [candidates, filter])
  const selected = candidates.find((item) => `${item.market}:${item.symbol}` === selectedKey) || visible[0]
  const counts = Object.fromEntries(Object.keys(STATUS).map((key) => [key, candidates.filter((item) => item.status === key).length]))
  return (
    <section className="opp-results-section">
      <div className="opp-section-head"><div><span className="eyebrow">候选漏斗</span><h3>每只股票为什么入围或被淘汰</h3></div><div className="opp-filter-tabs"><button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部 {candidates.length}</button>{Object.entries(STATUS).map(([key, [label]]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label} {counts[key]}</button>)}</div></div>
      <div className="opp-candidate-layout">
        <div className="opp-table-scroll">
          <table className="opp-candidate-table">
            <thead><tr><th>#</th><th>股票</th><th>状态</th><th>综合分</th><th>动量</th><th>估值</th><th>质量</th><th>成长</th><th>风险</th><th>覆盖</th></tr></thead>
            <tbody>{visible.map((item) => {
              const key = `${item.market}:${item.symbol}`
              return <tr key={key} className={selected === item ? 'selected' : ''} onClick={() => setSelectedKey(key)}>
                <td>{item.rank || '—'}</td>
                <td><b>{item.name || item.symbol}</b><small>{item.market} · {item.symbol}</small></td>
                <td><StatusBadge value={item.status} /></td>
                <td><strong>{number(item.composite_score)}</strong></td>
                {['momentum', 'value', 'quality', 'growth', 'risk'].map((factor) => <td key={factor} className={!item.factors?.[factor]?.available ? 'factor-missing' : ''}>{item.factors?.[factor]?.available ? number(item.factors[factor].score, 0) : '缺失'}</td>)}
                <td>{number(Number(item.factor_coverage) * 100, 0, '%')}</td>
              </tr>
            })}</tbody>
          </table>
          {!visible.length && <div className="opp-empty-small">当前筛选没有候选。</div>}
        </div>
        <CandidateDetail candidate={selected} goAnalyze={goAnalyze} />
      </div>
    </section>
  )
}

function PortfolioProposal({ portfolio, onCreatePaper, paperBusy }) {
  const positions = portfolio.positions || []
  return (
    <section className="opp-results-section opp-portfolio-proposal">
      <div className="opp-section-head">
        <div><span className="eyebrow">组合实验室</span><h3>通过约束后的纸面组合提案</h3><p>这不是订单；权重只用于继续观察策略是否真的有效。</p></div>
        {positions.length > 0 && <button type="button" onClick={onCreatePaper} disabled={paperBusy}>{paperBusy ? <><span className="spinner" />冻结中</> : <><WalletCards size={15} />启动纸面跟踪</>}</button>}
      </div>
      <div className="opp-portfolio-kpis">
        <div><small>组合状态</small><b>{portfolio.status === 'ready' ? '可观察' : '分散不足'}</b></div>
        <div><small>股票数</small><b>{portfolio.position_count || 0}</b></div>
        <div><small>纸面现金</small><b>{number(portfolio.cash_pct, 1, '%')}</b></div>
        <div><small>历史估算年化波动</small><b>{number(portfolio.estimated_annual_vol_pct, 1, '%')}</b></div>
        <div><small>协方差对齐</small><b>{portfolio.covariance_aligned_days || 0} 日</b></div>
      </div>
      {positions.length > 0 ? <div className="opp-allocation-list">{positions.map((position) => <div key={`${position.market}:${position.symbol}`}>
        <span><b>{position.name || position.symbol}</b><small>{position.market} · {position.symbol} · 分数 {position.composite_score}</small></span>
        <div><i style={{ width: `${Math.min(100, Number(position.weight_pct) * 4)}%` }} /></div><strong>{number(position.weight_pct, 1, '%')}</strong>
      </div>)}<div className="cash"><span><b>现金</b><small>{portfolio.defensive_cash_applied ? '候选市场多数处于防守状态，已增加现金缓冲' : '最低现金与仓位上限后的剩余'}</small></span><div><i style={{ width: `${Math.min(100, Number(portfolio.cash_pct) * 4)}%` }} /></div><strong>{number(portfolio.cash_pct, 1, '%')}</strong></div></div> : <div className="opp-empty-small">没有足够候选形成纸面组合，请查看具体淘汰原因，而不是放宽门槛凑仓位。</div>}
      {portfolio.correlation_exclusions?.length > 0 && <details className="opp-correlation-exclusions"><summary>相关性约束排除了 {portfolio.correlation_exclusions.length} 只股票</summary>{portfolio.correlation_exclusions.map((item) => <div key={`${item.market}:${item.symbol}`}><b>{item.market} {item.symbol}</b><span>{item.conflicts.map((conflict) => `与 ${conflict.with} 相关 ${conflict.correlation}`).join('；')}</span></div>)}</details>}
      <div className="opp-warning-list">{portfolio.warnings?.map((warning) => <div key={warning}><AlertTriangle size={14} /><span>{warning}</span></div>)}</div>
    </section>
  )
}

function RunComparison({ comparison }) {
  if (!comparison?.available) return <div className="opp-comparison-empty"><GitCompareArrows size={17} /><span>首次运行：{comparison?.reason || '还没有上一期结果'}。下一次会显示新入围、退出和排名变化。</span></div>
  return (
    <section className="opp-run-comparison">
      <div><GitCompareArrows size={18} /><span><b>和上一期比较</b><small>{shortDate(comparison.prior_completed_at)}</small></span></div>
      <div className="opp-change-groups">
        <span className="entered"><ArrowUpRight size={14} />新入围 {comparison.entered.length}<small>{comparison.entered.join('、') || '无'}</small></span>
        <span className="exited"><ArrowDownRight size={14} />退出 {comparison.exited.length}<small>{comparison.exited.join('、') || '无'}</small></span>
        <span>保留 {comparison.retained.length}<small>{comparison.retained.join('、') || '无'}</small></span>
      </div>
    </section>
  )
}

export default function RunResults({ run, goAnalyze, onCreatePaper, paperBusy }) {
  if (!run) return <section className="opp-no-run"><Layers3 size={28} /><h3>选择一个策略并启动扫描</h3><p>系统会冻结策略版本，构建明确候选池，再依次完成数据门槛、同市场多因子、组合约束和纸面跟踪。</p></section>
  if (run.status === 'queued' || run.status === 'running') return <Progress run={run} />
  if (run.status === 'failed') return <section className="opp-run-failed"><ShieldAlert size={25} /><div><span className="eyebrow">扫描失败</span><h3>{run.error_message || '机会扫描未完成'}</h3><p>错误已保留在持久任务和运行审计中，没有生成候选结果或模拟数据。</p><code>{run.error_code || 'OPPORTUNITY_RUN_FAILED'}</code></div></section>
  const result = run.result
  if (!result) return <section className="opp-no-run"><AlertTriangle size={25} /><h3>运行没有可展示结果</h3></section>
  return (
    <div className="opp-run-results">
      <section className="opp-run-summary">
        <div className="opp-run-title"><div><span className="eyebrow">冻结结果 · 策略 v{result.strategy.version_no}</span><h2>{result.strategy.name}</h2><p>{result.strategy.definition.description}</p></div><div className="opp-run-integrity"><CheckCircle2 size={17} /><span>结果哈希已校验<small>{String(run.result_sha256).slice(0, 12)}…</small></span></div></div>
        <Funnel funnel={result.funnel} />
        <div className="opp-regime-grid">{result.market_regimes.map((regime) => <div key={regime.market} className={regime.status}><span>{regime.market}</span><b>{regime.label}</b><small>{regime.status === 'insufficient' ? `仅 ${regime.sample_count} 个样本` : `三月中位 ${signed(regime.median_return_3m)} · 上涨广度 ${number(regime.positive_breadth_pct, 0, '%')}`}</small></div>)}</div>
        {result.universe.warnings?.length > 0 && <div className="opp-source-warning"><Clock3 size={16} /><span>本次为部分完成：{result.universe.warnings.map((item) => `${item.source}：${item.message}`).join('；')}</span></div>}
      </section>
      <RunComparison comparison={result.comparison} />
      <CandidateTable candidates={result.candidates || []} goAnalyze={goAnalyze} />
      <PortfolioProposal portfolio={result.portfolio || {}} onCreatePaper={onCreatePaper} paperBusy={paperBusy} />
      <details className="opp-methodology"><summary>方法、证据边界与未解决限制</summary><div><b>同业范围</b><p>{result.methodology.peer_scope}</p><b>缺失因子</b><p>{result.methodology.missing_factor_treatment}</p><b>排序顺序</b><p>{result.methodology.ranking}</p>{result.limitations.map((item) => <p key={item}>• {item}</p>)}</div></details>
    </div>
  )
}
