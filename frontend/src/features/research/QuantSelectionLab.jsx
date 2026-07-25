import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CalendarClock,
  CheckCircle2,
  Database,
  Fingerprint,
  FlaskConical,
  Gauge,
  History,
  LineChart,
  LockKeyhole,
  Play,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  TrendingUp,
  UsersRound,
  WalletCards,
  XCircle,
} from 'lucide-react'
import {
  createQuantSelectionForwardValidation,
  createQuantSelectionRun,
  createQuantSelectionShadowMandate,
  fetchQuantSelectionForwardValidations,
  fetchQuantSelectionOverview,
  fetchQuantSelectionRun,
  observeQuantSelectionForwardValidation,
} from '../../api/quantSelection'

const DEFAULT_FORM = {
  name: '沪深300历史成分多因子',
  market: 'A股',
  universe_mode: 'tushare_index',
  universe_attestation: 'current_snapshot',
  index_code: '000300.SH',
  index_member_limit: 12,
  benchmark_symbol: '510300',
  history_months: 60,
  lookback_days: 252,
  minimum_history_days: 252,
  rebalance_days: 21,
  oos_segment_days: 126,
  factor_weights: {
    momentum: 35,
    trend_quality: 25,
    low_volatility: 25,
    liquidity: 15,
  },
  minimum_composite_score: 55,
  minimum_price: 1,
  minimum_average_turnover: 50000000,
  max_price_staleness_days: 7,
  construction_method: 'score_inverse_volatility',
  max_positions: 6,
  max_position_pct: 20,
  minimum_cash_pct: 10,
  initial_capital: 1000000,
  minimum_order_notional: 1000,
  commission_bps: 5,
  slippage_bps: 8,
  impact_bps: 20,
  sell_tax_bps: 10,
  max_volume_participation_pct: 2.5,
  max_order_age_sessions: 3,
  maximum_drawdown_pct: 25,
}

const SAMPLE_SYMBOLS = {
  A股: '600519,贵州茅台\n300750,宁德时代\n601318,中国平安\n600036,招商银行\n000858,五粮液\n601166,兴业银行\n000333,美的集团\n600900,长江电力',
  港股: '00700,腾讯控股\n09988,阿里巴巴-W\n03690,美团-W\n00941,中国移动\n01299,友邦保险\n02318,中国平安\n00005,汇丰控股\n00883,中国海洋石油\n01810,小米集团-W\n09618,京东集团-SW',
  美股: 'AAPL,Apple\nMSFT,Microsoft\nNVDA,NVIDIA\nAMZN,Amazon\nGOOGL,Alphabet\nMETA,Meta\nBRK.B,Berkshire Hathaway\nJPM,JPMorgan\nLLY,Eli Lilly\nAVGO,Broadcom\nXOM,Exxon Mobil\nUNH,UnitedHealth',
}

const RUN_STATUS = {
  queued: ['等待行情 Worker', 'waiting'],
  running: ['历史回放中', 'running'],
  succeeded: ['完整完成', 'verified'],
  partial: ['部分完成', 'warning'],
  failed: ['实验失败', 'danger'],
  cancelled: ['实验取消', 'danger'],
}

const FORWARD_STATE = {
  awaiting_observation: ['等待首轮观察', 'waiting'],
  awaiting_entry: ['等待下一开盘', 'warning'],
  collecting: ['积累前向批次', 'running'],
  complete: ['60 日观察完成', 'verified'],
}

const FACTOR_LABELS = {
  momentum: '中期动量',
  trend_quality: '趋势质量',
  low_volatility: '低波动',
  liquidity: '流动性',
}

function formFromPolicy(policy) {
  const source = policy && typeof policy === 'object' ? policy : {}
  const normalized = Object.fromEntries(
    Object.keys(DEFAULT_FORM).map((key) => [
      key,
      source[key] == null ? DEFAULT_FORM[key] : source[key],
    ]),
  )
  normalized.factor_weights = {
    ...DEFAULT_FORM.factor_weights,
    ...(source.factor_weights || {}),
  }
  return normalized
}

function numeric(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function pct(value, digits = 2, signed = false) {
  const number = numeric(value)
  if (number == null) return '—'
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(digits)}%`
}

function ratio(value, digits = 3) {
  const number = numeric(value)
  return number == null ? '—' : number.toFixed(digits)
}

function money(value) {
  const number = numeric(value)
  if (number == null) return '—'
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function dateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleString('zh-CN', { hour12: false })
}

function shortHash(value) {
  return value ? `${String(value).slice(0, 14)}…` : '—'
}

function parseSymbols(text) {
  return String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [symbol, ...nameParts] = line.split(/[,，\t]/)
      return {
        symbol: String(symbol || '').trim(),
        name: nameParts.join(' ').trim(),
      }
    })
    .filter((item) => item.symbol)
}

function symbolsText(items) {
  return (items || [])
    .map((item) => `${item.symbol}${item.name && item.name !== item.symbol ? `,${item.name}` : ''}`)
    .join('\n')
}

function RunState({ run }) {
  const [label, tone] = RUN_STATUS[run?.status] || ['尚未运行', 'waiting']
  const Icon = ['succeeded', 'partial'].includes(run?.status)
    ? CheckCircle2
    : run?.status === 'failed'
      ? XCircle
      : Activity
  return <span className={`qsel-state ${tone}`}><Icon size={13} />{label}</span>
}

function Workflow() {
  const steps = [
    ['01', '历史股票池', '信号日只看当时已生效的成分，不拿今天名单回填过去'],
    ['02', '横截面选股', '动量、趋势质量、低波动和流动性只用信号日前数据'],
    ['03', '事件驱动撮合', '收盘出信号、次日开盘成交，受容量、成本和超时约束'],
    ['04', '前向纸面门禁', '样本外、Rank IC、压力成本和回撤全过才允许冻结'],
  ]
  return (
    <div className="qsel-workflow" aria-label="组合选股实验流程">
      {steps.map(([number, title, detail]) => (
        <article key={number}>
          <span>{number}</span>
          <div><b>{title}</b><small>{detail}</small></div>
        </article>
      ))}
    </div>
  )
}

function EquityComparisonChart({ strategy, benchmark }) {
  if (!strategy?.length) return <div className="hint">暂无可绘制的组合净值。</div>
  const width = 920
  const height = 230
  const pad = 22
  const strategyValues = strategy.map((item) => Number(item.equity))
  const benchmarkValues = (benchmark || []).map((item) => Number(item.equity))
  const all = [...strategyValues, ...benchmarkValues].filter(Number.isFinite)
  const low = Math.min(...all)
  const high = Math.max(...all)
  const range = Math.max(high - low, 1)
  const x = (index, length) => pad + index / Math.max(1, length - 1) * (width - pad * 2)
  const y = (value) => pad + (high - value) / range * (height - pad * 2)
  const points = (values) => values.map((value, index) => `${x(index, values.length)},${y(value)}`).join(' ')
  const baseline = y(strategyValues[0])
  return (
    <div className="qsel-equity">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="成本后策略净值与基准净值">
        <line x1={pad} x2={width - pad} y1={baseline} y2={baseline} className="baseline" />
        <polyline points={points(benchmarkValues)} className="benchmark" />
        <polyline points={points(strategyValues)} className="strategy" />
      </svg>
      <div className="qsel-chart-caption">
        <span><i className="strategy" />成本后策略</span>
        <span><i className="benchmark" />基准</span>
        <b>{strategy[0]?.date} → {strategy[strategy.length - 1]?.date}</b>
      </div>
    </div>
  )
}

function Metric({ icon: Icon, label, value, detail, tone = '' }) {
  return (
    <article className={`qsel-metric ${tone}`}>
      <Icon size={18} />
      <span><small>{label}</small><b>{value}</b><em>{detail}</em></span>
    </article>
  )
}

function GatePanel({ gate }) {
  if (!gate) return null
  return (
    <section className="qsel-section">
      <div className="qsel-section-head">
        <div><span className="eyebrow">Promotion gate</span><h3><ShieldCheck size={18} />前向纸面资格</h3></div>
        <span className={`qsel-gate-summary ${gate.paper_shadow_eligible ? 'ready' : 'blocked'}`}>
          {gate.paper_shadow_eligible ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          {gate.passed_count}/{gate.total_count} · {gate.paper_shadow_eligible ? '可冻结' : '仅研究'}
        </span>
      </div>
      <div className="qsel-gates">
        {(gate.checks || []).map((item) => (
          <article className={item.passed ? 'passed' : 'failed'} key={item.code}>
            {item.passed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <span><b>{item.label}</b><small>{item.detail}</small></span>
          </article>
        ))}
      </div>
      <p className="qsel-notice">{gate.notice}</p>
    </section>
  )
}

function LatestBasket({ signal }) {
  if (!signal) return null
  const targets = signal.targets || []
  return (
    <section className="qsel-section">
      <div className="qsel-section-head">
        <div><span className="eyebrow">Latest rebalance</span><h3><Target size={18} />最新目标篮子</h3></div>
        <div className="qsel-head-meta"><b>{signal.signal_date}</b><small>{targets.length} 只 · 目标现金 {pct(signal.target_cash_pct)}</small></div>
      </div>
      {!targets.length ? <div className="warning">最新调仓日没有股票同时通过数据、流动性和综合分门槛。</div> : (
        <div className="qsel-table-wrap">
          <table className="qsel-table">
            <thead><tr><th>排名 / 股票</th><th>目标权重</th><th>综合分</th><th>动量</th><th>趋势质量</th><th>低波动</th><th>流动性</th><th>年化波动</th><th>近三月均额</th></tr></thead>
            <tbody>
              {targets.map((item) => {
                const full = (signal.ranked || []).find((row) => row.symbol === item.symbol) || item
                return (
                  <tr key={item.symbol}>
                    <td><b>#{item.rank} {item.name || item.symbol}</b><small>{item.symbol} · 截至 {item.last_date}</small></td>
                    <td><strong>{pct(item.target_weight_pct)}</strong></td>
                    <td><span className="qsel-score">{ratio(item.composite_score, 1)}</span></td>
                    {Object.keys(FACTOR_LABELS).map((factor) => <td key={factor}>{ratio(full.factors?.[factor]?.percentile, 1)}</td>)}
                    <td>{pct(item.annual_volatility_pct)}</td>
                    <td>{money(item.average_turnover)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="qsel-funnel">
        <span>当期成员 <b>{signal.member_count}</b></span>
        <i>→</i>
        <span>因子可算 <b>{signal.eligible_count}</b></span>
        <i>→</i>
        <span>最终入选 <b>{signal.selected_count}</b></span>
        <i>+</i>
        <span>排除记录 <b>{signal.exclusions?.length || 0}</b></span>
      </div>
    </section>
  )
}

function WalkForwardPanel({ walkForward, rankIc }) {
  return (
    <section className="qsel-section">
      <div className="qsel-section-head">
        <div><span className="eyebrow">Out-of-sample</span><h3><BarChart3 size={18} />非重叠样本外窗口</h3></div>
        <div className="qsel-head-meta"><b>{walkForward?.segment_count || 0} 段</b><small>跑赢占比 {pct(walkForward?.positive_excess_rate_pct)}</small></div>
      </div>
      <div className="qsel-ic-strip">
        <span><small>Rank IC 均值</small><b>{ratio(rankIc?.mean_rank_ic, 4)}</b></span>
        <span><small>IC 正值占比</small><b>{pct(rankIc?.positive_rate_pct)}</b></span>
        <span><small>IC 观察数</small><b>{rankIc?.observation_count || 0}</b></span>
        <span><small>IC 信息比</small><b>{ratio(rankIc?.rank_ic_information_ratio)}</b></span>
      </div>
      <div className="qsel-table-wrap">
        <table className="qsel-table compact">
          <thead><tr><th>窗口</th><th>日期</th><th>交易日</th><th>策略</th><th>基准</th><th>净超额</th><th>最大回撤</th><th>结论</th></tr></thead>
          <tbody>
            {(walkForward?.segments || []).map((item) => (
              <tr key={item.segment_no}>
                <td>#{item.segment_no}</td>
                <td>{item.start_date}<small>至 {item.end_date}</small></td>
                <td>{item.trading_days}</td>
                <td>{pct(item.strategy_return_pct, 2, true)}</td>
                <td>{pct(item.benchmark_return_pct, 2, true)}</td>
                <td className={item.net_excess_return_pct > 0 ? 'positive' : 'negative'}>{pct(item.net_excess_return_pct, 2, true)}</td>
                <td>{pct(item.max_drawdown_pct)}</td>
                <td><span className={`qsel-mini-state ${item.positive_excess ? 'passed' : 'failed'}`}>{item.positive_excess ? '跑赢' : '未跑赢'}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">{walkForward?.detail}</p>
    </section>
  )
}

function ExecutionPanel({ result }) {
  const execution = result.execution || {}
  const stress = result.stress_test || {}
  return (
    <section className="qsel-section">
      <div className="qsel-section-head">
        <div><span className="eyebrow">Execution ledger</span><h3><Gauge size={18} />成交容量与成本审计</h3></div>
        <div className="qsel-head-meta"><b>{execution.fill_count || 0} 笔成交</b><small>{execution.partial_fill_count || 0} 次部分成交</small></div>
      </div>
      <div className="qsel-execution-grid">
        <span><small>总成交金额</small><b>{money(execution.total_filled_notional)}</b></span>
        <span><small>累计换手</small><b>{pct(execution.turnover_pct)}</b></span>
        <span><small>未成交申请</small><b>{pct(execution.unfilled_requested_pct)}</b></span>
        <span><small>总交易摩擦</small><b>{money(execution.total_cost)}</b></span>
        <span><small>容量利用率均值</small><b>{pct(execution.average_capacity_utilization_pct)}</b></span>
        <span><small>零量/停牌拒单</small><b>{execution.zero_volume_rejection_count || 0}</b></span>
      </div>
      <div className="qsel-stress">
        <AlertTriangle size={17} />
        <span><b>成本翻倍压力测试</b><small>佣金、基础滑点、冲击与卖出税同时乘 2</small></span>
        <strong>{pct(stress.performance?.net_excess_return_pct, 2, true)} 超额</strong>
        <em>{money(stress.execution?.total_cost)} 成本</em>
      </div>
      <details className="qsel-details">
        <summary>查看最近成交明细（{Math.min(result.fills?.length || 0, 20)} / {result.fills?.length || 0}）</summary>
        <div className="qsel-table-wrap">
          <table className="qsel-table compact">
            <thead><tr><th>信号 / 成交</th><th>股票</th><th>方向</th><th>参考开盘</th><th>有效成交价</th><th>金额</th><th>滑点</th><th>容量利用</th><th>状态</th></tr></thead>
            <tbody>
              {(result.fills || []).slice(-20).reverse().map((fill) => (
                <tr key={fill.fill_id}>
                  <td>{fill.signal_date}<small>{fill.fill_date}</small></td>
                  <td><b>{fill.symbol}</b><small>{fill.order_id}</small></td>
                  <td>{fill.side === 'buy' ? '买入' : '卖出'}</td>
                  <td>{ratio(fill.reference_open, 4)}</td>
                  <td>{ratio(fill.effective_price, 4)}</td>
                  <td>{money(fill.notional)}</td>
                  <td>{ratio(fill.slippage_bps, 2)} bps</td>
                  <td>{pct(fill.capacity_utilization_pct)}</td>
                  <td>{fill.partial ? <span className="qsel-mini-state warning">部分</span> : <span className="qsel-mini-state passed">完成</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

function EvidencePanel({ run }) {
  const result = run.result || {}
  const universe = result.universe || {}
  const quality = result.data_quality || {}
  return (
    <section className="qsel-section">
      <div className="qsel-section-head">
        <div><span className="eyebrow">Evidence & integrity</span><h3><Fingerprint size={18} />股票池、数据源与哈希证据</h3></div>
        <span className={`qsel-gate-summary ${run.integrity?.verified ? 'ready' : 'blocked'}`}>
          {run.integrity?.verified ? <LockKeyhole size={15} /> : <XCircle size={15} />}
          {run.integrity?.verified ? '完整性通过' : '完整性异常'}
        </span>
      </div>
      <div className="qsel-evidence-grid">
        <article><Database size={17} /><span><small>股票池口径</small><b>{universe.label || universe.mode}</b><em>{universe.verification_detail}</em></span></article>
        <article><History size={17} /><span><small>历史快照</small><b>{universe.snapshot_count || 0} 期 / {universe.unique_symbol_count || 0} 只</b><em>{universe.first_snapshot_date} → {universe.last_snapshot_date}</em></span></article>
        <article><ShieldCheck size={17} /><span><small>专业复权 / 独立未复权</small><b>{pct(quality.professional_adjusted_source_coverage_pct)} / {pct(quality.independent_raw_source_coverage_pct)}</b><em>双价格同时满足 {pct(quality.professional_source_coverage_pct)}</em></span></article>
        <article><Fingerprint size={17} /><span><small>结果 SHA-256</small><b><code>{shortHash(run.result_sha256)}</code></b><em>输入 <code>{shortHash(run.policy_sha256)}</code></em></span></article>
      </div>
      {universe.warning && <div className="warning">{universe.warning}</div>}
      {!!result.fetch_failures?.length && <div className="warning">行情失败：{result.fetch_failures.map((item) => `${item.symbol}: ${item.error}`).join('；')}</div>}
      <details className="qsel-details">
        <summary>查看逐股票行情来源（{quality.assets?.length || 0}）</summary>
        <div className="qsel-source-list">
          {(quality.assets || []).map((item) => (
            <article key={item.symbol} className={item.professional_pair ? 'verified' : 'degraded'}>
              <b>{item.symbol}</b>
              <span>{item.adjusted_source || '复权源缺失'}<small>{item.raw_note || item.raw_source || '未复权源缺失'}</small></span>
              <em>{item.row_count} 日 · {item.first_date} → {item.last_date}</em>
            </article>
          ))}
        </div>
      </details>
    </section>
  )
}

function ForwardValidationPanel({ validation, busy, onObserve }) {
  if (!validation) return null
  const [stateLabel, stateTone] = FORWARD_STATE[validation.observation_state] || ['状态待核验', 'warning']
  const scorecard = validation.scorecard || {}
  const policy = scorecard.policy?.values || {}
  const capitalGate = scorecard.capital_gate || {}
  const latest = validation.latest_observation || {}
  const latestHorizons = Object.fromEntries((latest.horizons || []).map((item) => [Number(item.trading_days), item]))
  const aggregateHorizons = Object.fromEntries((scorecard.horizons || []).map((item) => [Number(item.horizon_trading_days), item]))
  const primary = aggregateHorizons[20] || {}
  const entryRules = validation.entry?.rules || {}
  const gateLabel = {
    empty: '尚无批次',
    collecting: '证据积累中',
    watch: '继续观察',
    suspended: '策略暂停',
    limited_manual_pilot: '受限人工试运行',
  }[capitalGate.status] || capitalGate.status || '尚未计算'

  return (
    <section className="qsel-section qsel-forward">
      <div className="qsel-section-head">
        <div><span className="eyebrow">Causal forward validation</span><h3><FlaskConical size={18} />量化策略前向验证中枢</h3></div>
        <span className={`qsel-state ${stateTone}`}><CalendarClock size={13} />{stateLabel}</span>
      </div>

      <div className="qsel-forward-kpis">
        <article><CalendarClock size={17} /><span><small>最早建仓规则</small><b>{entryRules.entry_after_date ? `${entryRules.entry_after_date} 之后首个开盘` : '冻结后下一真实开盘'}</b><em>同日成交禁止 · 历史补填禁止</em></span></article>
        <article><BarChart3 size={17} /><span><small>20 日独立成熟批次</small><b>{primary.mature_count || 0} / {policy.minimum_mature_baskets || 6}</b><em>重叠批次排除 {primary.overlap_excluded_count || 0} 份</em></span></article>
        <article><WalletCards size={17} /><span><small>冻结往返成本压力</small><b>{ratio(policy.round_trip_cost_bps, 1)} bps</b><em>策略与基准从同一入场会话计算</em></span></article>
        <article><UsersRound size={17} /><span><small>资金 / 委员会门禁</small><b>{gateLabel}</b><em>{validation.committee_ready ? '当前记分卡已具备委员会证据' : '不会生成订单或自动连接券商'}</em></span></article>
      </div>

      <div className="qsel-forward-horizons">
        {[5, 20, 60].map((horizon) => {
          const live = latestHorizons[horizon] || {}
          const aggregate = aggregateHorizons[horizon] || {}
          const complete = Boolean(live.complete)
          return (
            <article className={complete ? 'complete' : 'pending'} key={horizon}>
              <div><b>{horizon} 交易日</b><span className={`qsel-mini-state ${complete ? 'passed' : 'warning'}`}>{complete ? '已精确结算' : '等待成熟'}</span></div>
              <strong>{complete ? pct(live.net_excess_return_pct, 3, true) : `${live.covered_position_weight_pct || 0}% 覆盖`}</strong>
              <small>本批成本后超额 · 历史独立成熟 {aggregate.mature_count || 0} 份</small>
              <em>平均超额 {pct(aggregate.mean_net_excess_return_pct, 3, true)} · 胜基准 {pct(aggregate.positive_excess_rate_pct, 1)}</em>
            </article>
          )
        })}
      </div>

      <div className="qsel-forward-action">
        <span><b>{validation.next_action}</b><small>系统每小时自动观察；相同行情截面幂等去重，离开页面后任务仍会继续。</small></span>
        <button type="button" disabled={busy} onClick={onObserve}>
          {busy ? <Activity size={14} /> : <RefreshCw size={14} />}
          {busy ? '正在派发' : '立即刷新前向结果'}
        </button>
      </div>
      <p className="qsel-forward-boundary"><ShieldCheck size={14} />冻结前已知价格不会进入前向收益；只有 6 个独立 20 日批次、成本后超额、命中率、回撤与多重检验同时通过，才可能进入受限人工试运行评审。</p>
    </section>
  )
}

function ResultView({
  run,
  mandate,
  validation,
  onFreeze,
  onEnroll,
  onObserve,
  freezeBusy,
  forwardBusy,
  observeBusy,
  acknowledged,
  setAcknowledged,
}) {
  const result = run?.result
  if (!run) return (
    <section className="qsel-empty">
      <LineChart size={34} />
      <h3>运行第一轮组合选股实验</h3>
      <p>系统会保留逐期股票池、每次排名、目标权重、订单、成交、样本外窗口和全部失败门槛。</p>
    </section>
  )
  if (['queued', 'running'].includes(run.status)) {
    const progress = run.progress || {}
    const percent = progress.total ? Math.min(100, progress.completed / progress.total * 100) : 8
    return (
      <section className="qsel-running">
        <Activity size={30} />
        <h3>{progress.message || '组合选股实验运行中'}</h3>
        <div><i style={{ width: `${percent}%` }} /></div>
        <p>{progress.stage || 'queued'} · {progress.completed || 0}/{progress.total || 0}</p>
      </section>
    )
  }
  if (run.status === 'failed') {
    return <section className="qsel-empty danger"><XCircle size={34} /><h3>实验失败</h3><p>{run.error_message || '后台任务未能完成'}</p></section>
  }
  if (!result) return <section className="qsel-empty"><AlertTriangle size={34} /><h3>结果尚未加载</h3></section>
  const performance = result.performance || {}
  const execution = result.execution || {}
  const gate = result.promotion_gate || {}
  return (
    <div className="qsel-results">
      <section className="qsel-result-hero">
        <div>
          <span className="eyebrow">Point-in-time selection</span>
          <h2>{result.policy?.name || '组合选股实验'}</h2>
          <p>{result.universe?.label} · {result.policy?.rebalance_days} 交易日调仓 · {result.policy?.construction_method}</p>
        </div>
        <div>
          <RunState run={run} />
          <small>完成于 {dateTime(run.completed_at)}</small>
        </div>
      </section>

      <div className="qsel-metrics">
        <Metric icon={TrendingUp} label="全期成本后超额" value={pct(performance.net_excess_return_pct, 2, true)} detail={`策略 ${pct(performance.total_return_pct, 2, true)} · 基准 ${pct(performance.benchmark_return_pct, 2, true)}`} tone={performance.net_excess_return_pct > 0 ? 'positive' : 'negative'} />
        <Metric icon={LineChart} label="年化收益 / 波动" value={`${pct(performance.annualized_return_pct)} / ${pct(performance.annualized_volatility_pct)}`} detail={`Sharpe ${ratio(performance.sharpe)} · IR ${ratio(performance.information_ratio)}`} />
        <Metric icon={Gauge} label="最大回撤" value={pct(performance.max_drawdown_pct)} detail={`预算 ${pct(result.policy?.maximum_drawdown_pct)}`} tone="negative" />
        <Metric icon={WalletCards} label="成交与成本" value={`${execution.fill_count || 0} 笔 / ${money(execution.total_cost)}`} detail={`未成交 ${pct(execution.unfilled_requested_pct)} · 部分 ${execution.partial_fill_count || 0}`} />
      </div>

      <section className="qsel-section">
        <div className="qsel-section-head">
          <div><span className="eyebrow">Net performance</span><h3><LineChart size={18} />成本后策略与基准</h3></div>
          <div className="qsel-head-meta"><b>{performance.trading_days || 0} 个交易日</b><small>不展示“最优参数”曲线</small></div>
        </div>
        <EquityComparisonChart strategy={result.equity_curve} benchmark={result.benchmark_curve} />
      </section>

      <LatestBasket signal={result.latest_signal} />
      <WalkForwardPanel walkForward={result.walk_forward} rankIc={result.rank_ic} />
      <ExecutionPanel result={result} />
      <GatePanel gate={gate} />

      <section className="qsel-section qsel-shadow">
        <div>
          <LockKeyhole size={22} />
          <span><b>冻结前向纸面策略</b><small>只保存最新目标、实验摘要和执行规则，不连接券商、不自动下单。</small></span>
        </div>
        {mandate ? (
          <div>
            <span className="qsel-shadow-created"><CheckCircle2 size={16} />已冻结 · {dateTime(mandate.created_at)}</span>
            {!validation && (
              <button type="button" disabled={forwardBusy} onClick={onEnroll}>
                {forwardBusy ? <Activity size={15} /> : <FlaskConical size={15} />}
                {forwardBusy ? '正在接入' : '接入 5/20/60 日前向验证'}
              </button>
            )}
          </div>
        ) : (
          <div>
            <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我理解历史结果不保证未来收益，本策略仅用于前向纸面验证。</label>
            <button type="button" disabled={!gate.paper_shadow_eligible || !acknowledged || freezeBusy} onClick={onFreeze}>
              {freezeBusy ? <Activity size={15} /> : <LockKeyhole size={15} />}
              {gate.paper_shadow_eligible ? '冻结纸面策略' : '门槛未通过'}
            </button>
          </div>
        )}
      </section>

      <ForwardValidationPanel validation={validation} busy={observeBusy} onObserve={onObserve} />
      <EvidencePanel run={run} />
      <div className="warning qsel-limitations">
        <b>模型边界</b>
        <ul>{(result.limitations || []).map((item) => <li key={item}>{item}</li>)}</ul>
      </div>
    </div>
  )
}

export default function QuantSelectionLab() {
  const [overview, setOverview] = useState(null)
  const [forwardOverview, setForwardOverview] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [symbols, setSymbols] = useState(SAMPLE_SYMBOLS.A股)
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [runBusy, setRunBusy] = useState(false)
  const [freezeBusy, setFreezeBusy] = useState(false)
  const [forwardBusy, setForwardBusy] = useState(false)
  const [observeBusy, setObserveBusy] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const refreshOverview = useCallback(async () => {
    const [result, forward] = await Promise.all([
      fetchQuantSelectionOverview(),
      fetchQuantSelectionForwardValidations(),
    ])
    setOverview(result)
    setForwardOverview(forward)
    setSelectedRunId((current) => current || result.runs?.[0]?.id || null)
    return result
  }, [])

  useEffect(() => {
    let active = true
    refreshOverview()
      .then((result) => {
        if (!active) return
        const first = result.presets?.[0]
        if (first?.policy) {
          setForm(formFromPolicy(first.policy))
          setSymbols(symbolsText(first.policy.symbols) || SAMPLE_SYMBOLS.A股)
        }
      })
      .catch((requestError) => { if (active) setError(requestError.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [refreshOverview])

  useEffect(() => {
    if (!selectedRunId) {
      setRun(null)
      return undefined
    }
    let active = true
    setError('')
    fetchQuantSelectionRun(selectedRunId)
      .then((result) => { if (active) setRun(result) })
      .catch((requestError) => { if (active) setError(requestError.message) })
    return () => { active = false }
  }, [selectedRunId])

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return undefined
    let active = true
    const timer = globalThis.setInterval(async () => {
      try {
        const next = await fetchQuantSelectionRun(run.id)
        if (!active) return
        setRun(next)
        if (!['queued', 'running'].includes(next.status)) await refreshOverview()
      } catch (requestError) {
        if (active) setError(requestError.message)
      }
    }, 2500)
    return () => { active = false; globalThis.clearInterval(timer) }
  }, [run, refreshOverview])

  const mandates = overview?.shadow_mandates || []
  const mandate = mandates.find((item) => item.run_id === run?.id) || null
  const validation = (forwardOverview?.items || []).find((item) => item.quant_mandate_id === mandate?.id) || null
  const manualSymbols = useMemo(() => parseSymbols(symbols), [symbols])

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function updateFactor(key, value) {
    setForm((current) => ({
      ...current,
      factor_weights: { ...current.factor_weights, [key]: value },
    }))
  }

  function applyPreset(preset) {
    const policy = preset.policy || {}
    setForm(formFromPolicy(policy))
    setSymbols(symbolsText(policy.symbols) || SAMPLE_SYMBOLS[policy.market] || '')
    setError('')
  }

  function changeMarket(market) {
    const isA = market === 'A股'
    setForm((current) => ({
      ...current,
      market,
      universe_mode: isA ? current.universe_mode : 'frozen_symbols',
      benchmark_symbol: { A股: '510300', 港股: '02800', 美股: 'SPY' }[market],
      minimum_average_turnover: { A股: 50000000, 港股: 5000000, 美股: 10000000 }[market],
      sell_tax_bps: market === 'A股' ? 10 : 0,
    }))
    setSymbols(SAMPLE_SYMBOLS[market])
  }

  async function startRun() {
    if (form.universe_mode === 'frozen_symbols' && manualSymbols.length < 6) {
      setError('冻结自定义股票池至少需要 6 只股票，每行格式为“代码,名称”。')
      return
    }
    const payload = {
      ...formFromPolicy(form),
      symbols: form.universe_mode === 'frozen_symbols' ? manualSymbols : [],
      benchmark_symbol: form.benchmark_symbol || null,
    }
    setRunBusy(true); setError(''); setMessage(''); setAcknowledged(false)
    try {
      const created = await createQuantSelectionRun(payload)
      setRun(created)
      setSelectedRunId(created.id)
      await refreshOverview()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setRunBusy(false)
    }
  }

  async function freezeShadow() {
    if (!run?.result_sha256) return
    setFreezeBusy(true); setError(''); setMessage('')
    try {
      const response = await createQuantSelectionShadowMandate(run.id, run.result_sha256)
      const frozen = response.item
      await createQuantSelectionForwardValidation(frozen.id, frozen.snapshot_sha256)
      setMessage('纸面指令已冻结，并已接入无前视污染的 5/20/60 交易日前向验证。')
      await refreshOverview()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setFreezeBusy(false)
    }
  }

  async function enrollForward() {
    if (!mandate?.id || !mandate?.snapshot_sha256) return
    setForwardBusy(true); setError(''); setMessage('')
    try {
      await createQuantSelectionForwardValidation(mandate.id, mandate.snapshot_sha256)
      setMessage('量化纸面指令已接入前向证据链；系统将等待冻结后的下一真实交易日开盘。')
      await refreshOverview()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setForwardBusy(false)
    }
  }

  async function observeForward() {
    if (!validation?.id) return
    setObserveBusy(true); setError(''); setMessage('')
    try {
      const response = await observeQuantSelectionForwardValidation(validation.id)
      setMessage(response.created === false ? '本小时的前向观察任务已存在，无需重复派发。' : '前向观察已进入持久化行情队列，离开页面后仍会继续。')
      await refreshOverview()
      globalThis.setTimeout(() => {
        refreshOverview().catch((requestError) => setError(requestError.message))
      }, 3000)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setObserveBusy(false)
    }
  }

  if (loading) return <div className="page-loading"><span className="spinner" />正在读取组合选股实验室</div>

  return (
    <div className="qsel-lab">
      <section className="qsel-intro">
        <div>
          <span className="eyebrow">Point-in-time quant selection</span>
          <h2>从历史股票池到可审计的组合成交</h2>
          <p>这不是“今天挑几只股票再回看”的排行榜。系统逐期冻结可投资股票池，只用信号日当时可见的数据排名，并把目标权重放进真实成本与容量约束的逐日撮合账本。</p>
        </div>
        <span><ShieldCheck size={17} />不承诺涨跌 · 不连接券商 · 不自动下单</span>
      </section>
      <Workflow />

      {error && <div className="error"><AlertTriangle size={16} />{error}</div>}
      {message && <div className="qsel-message"><CheckCircle2 size={16} />{message}</div>}

      <div className="qsel-layout">
        <aside className="qsel-builder">
          <div className="qsel-builder-head">
            <div><span className="eyebrow">Research design</span><h3><SlidersHorizontal size={18} />实验设计</h3></div>
          </div>

          <div className="qsel-presets">
            {(overview?.presets || []).map((preset) => (
              <button type="button" key={preset.id} onClick={() => applyPreset(preset)}>
                <b>{preset.label}</b>
                <small>{preset.description}</small>
                <em>{preset.promotion_capable ? '可验证历史股票池' : '仅研究股票池'}</em>
              </button>
            ))}
          </div>

          <label className="qsel-field"><span>实验名称</span><input value={form.name} onChange={(event) => update('name', event.target.value)} /></label>
          <div className="qsel-form-grid">
            <label className="qsel-field"><span>市场</span><select value={form.market} onChange={(event) => changeMarket(event.target.value)}><option>A股</option><option>港股</option><option>美股</option></select></label>
            <label className="qsel-field"><span>股票池口径</span><select value={form.universe_mode} onChange={(event) => update('universe_mode', event.target.value)}><option value="frozen_symbols">冻结自定义名单</option>{form.market === 'A股' && <option value="tushare_index">Tushare 历史指数成分</option>}</select></label>
          </div>

          {form.universe_mode === 'tushare_index' ? (
            <div className="qsel-index-box">
              <label className="qsel-field"><span>历史母指数</span><select value={form.index_code} onChange={(event) => {
                const code = event.target.value
                update('index_code', code)
                update('benchmark_symbol', { '000300.SH': '510300', '000905.SH': '510500', '000852.SH': '512100' }[code])
              }}><option value="000300.SH">沪深300</option><option value="000905.SH">中证500</option><option value="000852.SH">中证1000</option></select></label>
              <label className="qsel-field"><span>每期按指数权重保留</span><input type="number" min="8" max="24" value={form.index_member_limit} onChange={(event) => update('index_member_limit', Number(event.target.value))} /></label>
              <p><Database size={15} />逐月读取历史 `index_weight`，而不是使用今天的成分名单。</p>
            </div>
          ) : (
            <label className="qsel-field qsel-symbols"><span>冻结股票池 <em>{manualSymbols.length} 只</em></span><textarea rows="9" value={symbols} onChange={(event) => setSymbols(event.target.value)} placeholder="AAPL,Apple&#10;MSFT,Microsoft" /><small>每行一只：代码,名称。当前名单回看历史会保留幸存者偏差警告，不能升级为纸面策略。</small></label>
          )}

          <div className="qsel-form-grid three">
            <label className="qsel-field"><span>历史月数</span><select value={form.history_months} onChange={(event) => update('history_months', Number(event.target.value))}><option value="36">36</option><option value="60">60</option><option value="84">84</option><option value="120">120</option></select></label>
            <label className="qsel-field"><span>因子回看</span><select value={form.lookback_days} onChange={(event) => { update('lookback_days', Number(event.target.value)); update('minimum_history_days', Number(event.target.value)) }}><option value="126">126 日</option><option value="252">252 日</option></select></label>
            <label className="qsel-field"><span>调仓频率</span><select value={form.rebalance_days} onChange={(event) => update('rebalance_days', Number(event.target.value))}><option value="21">约每月</option><option value="63">约每季</option></select></label>
          </div>

          <fieldset className="qsel-factors">
            <legend>固定因子权重</legend>
            {Object.entries(FACTOR_LABELS).map(([key, label]) => (
              <label key={key}><span>{label}</span><input type="number" min="0" max="100" value={form.factor_weights[key]} onChange={(event) => updateFactor(key, Number(event.target.value))} /><em>%</em></label>
            ))}
          </fieldset>

          <div className="qsel-form-grid three">
            <label className="qsel-field"><span>最多持仓</span><input type="number" min="2" max="12" value={form.max_positions} onChange={(event) => update('max_positions', Number(event.target.value))} /></label>
            <label className="qsel-field"><span>单股上限 %</span><input type="number" min="5" max="50" value={form.max_position_pct} onChange={(event) => update('max_position_pct', Number(event.target.value))} /></label>
            <label className="qsel-field"><span>最低现金 %</span><input type="number" min="0" max="60" value={form.minimum_cash_pct} onChange={(event) => update('minimum_cash_pct', Number(event.target.value))} /></label>
          </div>
          <label className="qsel-field"><span>组合加权</span><select value={form.construction_method} onChange={(event) => update('construction_method', event.target.value)}><option value="score_inverse_volatility">综合分 × 逆波动</option><option value="inverse_volatility">逆波动</option><option value="equal_weight">等权</option></select></label>

          <button type="button" className="qsel-advanced-toggle" onClick={() => setShowAdvanced((value) => !value)}><SlidersHorizontal size={14} />{showAdvanced ? '收起' : '展开'}成交、容量与门槛</button>
          {showAdvanced && (
            <div className="qsel-advanced">
              <div className="qsel-form-grid">
                <label className="qsel-field"><span>基准代码</span><input value={form.benchmark_symbol} onChange={(event) => update('benchmark_symbol', event.target.value)} /></label>
                <label className="qsel-field"><span>样本外窗口</span><select value={form.oos_segment_days} onChange={(event) => update('oos_segment_days', Number(event.target.value))}><option value="126">126 日</option><option value="252">252 日</option></select></label>
                <label className="qsel-field"><span>最低综合分</span><input type="number" value={form.minimum_composite_score} onChange={(event) => update('minimum_composite_score', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>最低均成交额</span><input type="number" value={form.minimum_average_turnover} onChange={(event) => update('minimum_average_turnover', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>模拟资金</span><input type="number" value={form.initial_capital} onChange={(event) => update('initial_capital', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>最小订单</span><input type="number" value={form.minimum_order_notional} onChange={(event) => update('minimum_order_notional', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>佣金 bps</span><input type="number" value={form.commission_bps} onChange={(event) => update('commission_bps', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>基础滑点 bps</span><input type="number" value={form.slippage_bps} onChange={(event) => update('slippage_bps', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>满容量冲击 bps</span><input type="number" value={form.impact_bps} onChange={(event) => update('impact_bps', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>卖出税 bps</span><input type="number" value={form.sell_tax_bps} onChange={(event) => update('sell_tax_bps', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>量能参与上限 %</span><input type="number" step="0.1" value={form.max_volume_participation_pct} onChange={(event) => update('max_volume_participation_pct', Number(event.target.value))} /></label>
                <label className="qsel-field"><span>最大回撤预算 %</span><input type="number" value={form.maximum_drawdown_pct} onChange={(event) => update('maximum_drawdown_pct', Number(event.target.value))} /></label>
              </div>
            </div>
          )}

          <button type="button" className="qsel-run-button" disabled={runBusy || ['queued', 'running'].includes(run?.status)} onClick={startRun}>
            {runBusy ? <Activity size={16} /> : <Play size={16} />}
            {runBusy ? '正在创建实验' : '运行组合选股实验'}
          </button>
          <p className="qsel-builder-foot">计算量与股票并集、历史长度成正比。任务进入持久化 market-data 队列，离开页面后不会丢失。</p>
        </aside>

        <main className="qsel-main">
          <nav className="qsel-run-history">
            <span><History size={15} />运行历史</span>
            <div>
              {(overview?.runs || []).map((item) => (
                <button type="button" className={item.id === selectedRunId ? 'active' : ''} key={item.id} onClick={() => setSelectedRunId(item.id)}>
                  <RunState run={item} />
                  <b>{item.policy?.name || '组合选股实验'}</b>
                  <small>{dateTime(item.created_at)}</small>
                </button>
              ))}
              {!overview?.runs?.length && <em>还没有实验记录</em>}
            </div>
            {selectedRunId && <button type="button" className="icon-button ghost" onClick={async () => { try { setRun(await fetchQuantSelectionRun(selectedRunId)) } catch (requestError) { setError(requestError.message) } }} aria-label="刷新实验"><RefreshCw size={15} /></button>}
          </nav>
          <ResultView
            run={run}
            mandate={mandate}
            validation={validation}
            onFreeze={freezeShadow}
            onEnroll={enrollForward}
            onObserve={observeForward}
            freezeBusy={freezeBusy}
            forwardBusy={forwardBusy}
            observeBusy={observeBusy}
            acknowledged={acknowledged}
            setAcknowledged={setAcknowledged}
          />
        </main>
      </div>
    </div>
  )
}
