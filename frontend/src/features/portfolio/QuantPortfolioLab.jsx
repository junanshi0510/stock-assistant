import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CircleDollarSign,
  Database,
  Fingerprint,
  History,
  LineChart,
  LockKeyhole,
  Play,
  RefreshCw,
  Scale,
  ShieldCheck,
  SlidersHorizontal,
  TrendingDown,
  WalletCards,
  XCircle,
} from 'lucide-react'
import {
  createPortfolioQuantMandate,
  createPortfolioQuantRun,
  fetchPortfolioQuantOverview,
  fetchPortfolioQuantRun,
} from '../../api/portfolio'

const DEFAULT_FORM = {
  construction_method: 'risk_parity',
  lookback_days: 252,
  rebalance_days: 21,
  commission_bps: 5,
  slippage_bps: 10,
  sell_tax_bps: 0,
  max_turnover_pct: 35,
  max_position_pct: 30,
  minimum_trade_amount_cny: 1000,
}

const METHOD_COPY = {
  risk_parity: {
    label: '风险平价',
    short: '让各持仓对组合风险的贡献更接近',
  },
  minimum_variance: {
    label: '最小方差',
    short: '在单股上限内压低估计组合波动',
  },
  inverse_volatility: {
    label: '逆波动',
    short: '用波动倒数分配，简单且便于复核',
  },
  equal_weight: {
    label: '等权',
    short: '不依赖收益预测的透明基准方案',
  },
  current_weights: {
    label: '当前权重再平衡',
    short: '维持实验开始时的持仓权重',
  },
}

const RUN_STATUS = {
  queued: ['等待行情 Worker', 'waiting'],
  running: ['正在滚动验证', 'running'],
  succeeded: ['完整完成', 'verified'],
  partial: ['部分行情完成', 'warning'],
  failed: ['实验失败', 'danger'],
  cancelled: ['实验取消', 'danger'],
}

const ACTION_LABELS = {
  increase: '增加',
  reduce: '降低',
  hold_small_delta: '忽略小额偏差',
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

function ratio(value, digits = 2) {
  const number = numeric(value)
  return number == null ? '—' : number.toFixed(digits)
}

function money(value) {
  const number = numeric(value)
  if (number == null) return '—'
  const sign = number < 0 ? '-' : ''
  return `${sign}¥${Math.abs(number).toLocaleString('zh-CN', {
    maximumFractionDigits: 2,
  })}`
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

function formFromDefaults(defaults) {
  return Object.fromEntries(
    Object.entries(DEFAULT_FORM).map(([key, fallback]) => [
      key,
      defaults?.[key] ?? fallback,
    ]),
  )
}

function RunState({ run }) {
  const [label, tone] = RUN_STATUS[run?.status] || ['尚未运行', 'waiting']
  return (
    <span className={`quant-state ${tone}`}>
      {['succeeded', 'partial'].includes(run?.status)
        ? <CheckCircle2 size={13} />
        : run?.status === 'failed'
          ? <XCircle size={13} />
          : <Activity size={13} />}
      {label}
    </span>
  )
}

function Workflow() {
  const steps = [
    ['01', '冻结真实组合', '绑定持仓、投资政策与可信人民币估值'],
    ['02', '滚动样本外', '前窗估计权重，后窗只负责检验'],
    ['03', '扣除交易摩擦', '佣金、滑点、卖出税费与换手同时入账'],
    ['04', '纸面授权门禁', '只冻结金额目标，不连接券商或生成订单'],
  ]
  return (
    <div className="quant-workflow" aria-label="量化组合实验流程">
      {steps.map(([number, title, detail]) => (
        <article key={number}>
          <span>{number}</span>
          <div><b>{title}</b><small>{detail}</small></div>
        </article>
      ))}
    </div>
  )
}

function ComparisonMetric({
  label,
  current,
  selected,
  formatter = pct,
  lowerIsBetter = false,
}) {
  const currentValue = numeric(current)
  const selectedValue = numeric(selected)
  const improved = currentValue != null && selectedValue != null
    ? (lowerIsBetter ? selectedValue < currentValue : selectedValue > currentValue)
    : false
  return (
    <article className={`quant-compare-metric ${improved ? 'improved' : ''}`}>
      <span>{label}</span>
      <div>
        <small>当前权重</small>
        <b>{formatter(current)}</b>
      </div>
      <i>→</i>
      <div>
        <small>所选模型</small>
        <b>{formatter(selected)}</b>
      </div>
    </article>
  )
}

function EmptyResult({ directStockCount }) {
  return (
    <section className="quant-empty">
      <LineChart size={34} />
      <h3>用真实持仓建立第一份滚动样本外实验</h3>
      <p>
        当前识别到 {directStockCount} 只直接股票。至少需要 2 只具备可信人民币估值和足够共同交易日，
        才能估计协方差并比较组合构建方法。
      </p>
    </section>
  )
}

function PendingRun({ run }) {
  const progress = run?.progress || {}
  const completed = Number(progress.completed) || 0
  const total = Number(progress.total) || 0
  const width = total > 0 ? Math.min(100, completed / total * 100) : 12
  return (
    <section className="quant-pending">
      <span className="quant-pending-icon"><RefreshCw size={22} className="spin-icon" /></span>
      <div>
        <span className="eyebrow">DURABLE MARKET-DATA JOB</span>
        <h3>{progress.message || '正在准备量化组合实验'}</h3>
        <p>页面可以离开；运行、输入证据与进度均已持久化，返回后会继续读取同一任务。</p>
        <div className="quant-progress"><i style={{ width: `${width}%` }} /></div>
        <small>{total > 0 ? `${completed}/${total} 只股票行情已处理` : '等待专业行情源响应'}</small>
      </div>
      <RunState run={run} />
    </section>
  )
}

function ModelTable({ models }) {
  return (
    <div className="quant-table-wrap">
      <table className="quant-table">
        <thead>
          <tr>
            <th>构建方法</th>
            <th>年化收益*</th>
            <th>年化波动</th>
            <th>最大回撤</th>
            <th>Sharpe</th>
            <th>Sortino</th>
            <th>95% CVaR/日</th>
            <th>PSR</th>
            <th>平均单边换手</th>
            <th>成本拖累</th>
          </tr>
        </thead>
        <tbody>
          {(models || []).map((model) => {
            const performance = model.performance || {}
            return (
              <tr key={model.method} className={model.selected ? 'selected' : ''}>
                <td>
                  <b>{model.label || METHOD_COPY[model.method]?.label || model.method}</b>
                  <small>{model.selected ? '用户选择' : model.method === 'current_weights' ? '对照组' : '并行研究'}</small>
                </td>
                <td>{pct(performance.annualized_return_pct)}</td>
                <td>{pct(performance.annualized_volatility_pct)}</td>
                <td>{pct(performance.max_drawdown_pct)}</td>
                <td>{ratio(performance.sharpe_ratio)}</td>
                <td>{ratio(performance.sortino_ratio)}</td>
                <td>{pct(performance.cvar_95_pct)}</td>
                <td>{pct(performance.probabilistic_sharpe_pct, 1)}</td>
                <td>{pct(performance.average_one_way_turnover_pct)}</td>
                <td>{pct(performance.estimated_cost_drag_pct)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="quant-table-note">
        * 年化收益是成本后历史样本外结果，不是收益预测。系统不会根据这张表自动选择“历史最优”模型。
      </p>
    </div>
  )
}

function RiskContribution({ result }) {
  const selected = (result?.models || []).find((item) => item.selected)
  const weights = selected?.latest_stock_sleeve_weights_pct || []
  const contributions = selected?.risk?.risk_contribution_pct || []
  return (
    <section className="quant-panel quant-risk-panel">
      <div className="quant-section-head">
        <div>
          <span className="eyebrow">LATEST TRAINING WINDOW</span>
          <h3>目标权重与边际风险贡献</h3>
          <p>权重来自最新训练窗口；风险贡献不是涨跌预测，会随协方差状态改变。</p>
        </div>
        <span>风险 HHI {ratio(selected?.risk?.risk_concentration_hhi, 4)}</span>
      </div>
      <div className="quant-risk-list">
        {(result?.universe || []).map((asset, index) => {
          const weight = numeric(weights[index]) || 0
          const contribution = numeric(contributions[index]) || 0
          return (
            <article key={asset.key}>
              <header>
                <span><b>{asset.name || asset.code}</b><small>{asset.market} · {asset.code}</small></span>
                <strong>{pct(weight)}</strong>
              </header>
              <div className="quant-risk-bars">
                <span><i style={{ width: `${Math.max(0, Math.min(100, weight))}%` }} /></span>
                <span><i style={{ width: `${Math.max(0, Math.min(100, contribution))}%` }} /></span>
              </div>
              <footer>
                <span>目标袖套权重 {pct(weight)}</span>
                <span>风险贡献 {pct(contribution)}</span>
              </footer>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function TargetActions({ target }) {
  return (
    <section className="quant-panel">
      <div className="quant-section-head">
        <div>
          <span className="eyebrow">PAPER REBALANCE TARGET · CNY ONLY</span>
          <h3>人民币目标金额，不是券商订单</h3>
          <p>没有生成股数；执行前仍需实时重报价、整手校验、现金校验并由用户确认。</p>
        </div>
        <div className="quant-target-summary">
          <span><small>单边换手</small><b>{pct(target?.one_way_turnover_pct)}</b></span>
          <span><small>估算成本</small><b>{money(target?.estimated_cost_cny)}</b></span>
        </div>
      </div>
      <div className="quant-table-wrap">
        <table className="quant-table quant-actions-table">
          <thead>
            <tr>
              <th>持仓</th>
              <th>动作</th>
              <th>当前金额</th>
              <th>纸面目标</th>
              <th>金额差</th>
              <th>当前袖套权重</th>
              <th>目标袖套权重</th>
              <th>总组合目标权重</th>
            </tr>
          </thead>
          <tbody>
            {(target?.actions || []).map((item) => (
              <tr key={`${item.market}:${item.code}`}>
                <td><b>{item.name || item.code}</b><small>{item.market} · {item.code}</small></td>
                <td><em className={`quant-action ${item.action}`}>{ACTION_LABELS[item.action] || item.action}</em></td>
                <td>{money(item.current_amount_cny)}</td>
                <td>{money(item.target_amount_cny)}</td>
                <td className={(numeric(item.delta_amount_cny) || 0) > 0 ? 'positive' : (numeric(item.delta_amount_cny) || 0) < 0 ? 'negative' : ''}>
                  {money(item.delta_amount_cny)}
                </td>
                <td>{pct(item.current_stock_sleeve_weight_pct)}</td>
                <td>{pct(item.target_stock_sleeve_weight_pct)}</td>
                <td>{pct(item.target_total_portfolio_weight_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="quant-target-footer">
        <span>计划增加 {money(target?.buy_amount_cny)}</span>
        <span>计划降低 {money(target?.sell_amount_cny)}</span>
        <span>袖套现金释放 {money(target?.cash_release_cny)}</span>
        <strong><LockKeyhole size={13} />实盘执行始终关闭</strong>
      </div>
    </section>
  )
}

function FoldHistory({ result }) {
  const selected = result?.selected_method
  const folds = [...(result?.walk_forward?.folds || [])].slice(-12).reverse()
  return (
    <section className="quant-panel">
      <div className="quant-section-head">
        <div>
          <span className="eyebrow">WALK-FORWARD HOLDOUT WINDOWS</span>
          <h3>每个测试窗都只使用过去估计</h3>
          <p>
            训练 {result?.walk_forward?.training_window_days || '—'} 日，测试 {result?.walk_forward?.holdout_window_days || '—'} 日，
            共 {result?.walk_forward?.fold_count || 0} 个完整样本外窗口。
          </p>
        </div>
        <span>最近 {folds.length} 个窗口</span>
      </div>
      <div className="quant-fold-grid">
        {folds.map((fold) => {
          const current = fold.methods?.current_weights || {}
          const chosen = fold.methods?.[selected] || {}
          return (
            <article key={fold.fold_no}>
              <header><b>Fold {fold.fold_no}</b><span>{fold.test_start} → {fold.test_end}</span></header>
              <div>
                <span><small>当前权重</small><strong className={(numeric(current.net_return_pct) || 0) >= 0 ? 'positive' : 'negative'}>{pct(current.net_return_pct, 2, true)}</strong></span>
                <span><small>{METHOD_COPY[selected]?.label || selected}</small><strong className={(numeric(chosen.net_return_pct) || 0) >= 0 ? 'positive' : 'negative'}>{pct(chosen.net_return_pct, 2, true)}</strong></span>
              </div>
              <footer>训练截止 {fold.train_end} · 成本 {pct(chosen.estimated_cost_pct, 3)}</footer>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function PromotionGate({
  run,
  result,
  mandate,
  acknowledged,
  onAcknowledge,
  onFreeze,
  freezing,
}) {
  const gate = result?.promotion_gate || {}
  const eligible = Boolean(gate.paper_mandate_eligible)
  return (
    <section className={`quant-panel quant-gate ${eligible ? 'eligible' : 'blocked'}`}>
      <div className="quant-section-head">
        <div>
          <span className="eyebrow">PAPER MANDATE GATE</span>
          <h3>{gate.label || '正在核对纸面调仓准入'}</h3>
          <p>通过只代表可以冻结不可变纸面金额目标；不代表未来盈利，也不授权真实下单。</p>
        </div>
        <span className={`quant-state ${eligible ? 'verified' : 'warning'}`}>
          {eligible ? <ShieldCheck size={13} /> : <AlertTriangle size={13} />}
          {eligible ? '门禁通过' : '研究模式'}
        </span>
      </div>
      <div className="quant-check-grid">
        {(gate.checks || []).map((check) => (
          <article key={check.code} className={check.passed ? 'passed' : 'failed'}>
            {check.passed ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <span><b>{check.label}</b><small>{check.detail}</small></span>
          </article>
        ))}
      </div>
      {mandate ? (
        <div className="quant-mandate-frozen">
          <LockKeyhole size={19} />
          <span>
            <b>纸面调仓指令已冻结</b>
            <small>{dateTime(mandate.created_at)} · 目标哈希 {shortHash(mandate.target_sha256)}</small>
          </span>
          <em>不可修改 · 不可执行</em>
        </div>
      ) : eligible ? (
        <div className="quant-mandate-action">
          <label>
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => onAcknowledge(event.target.checked)}
            />
            <span>
              <b>我确认这只是纸面调仓研究</b>
              <small>系统会再次核对持仓哈希、估值快照与投资政策版本；任何一项变化都会拒绝冻结。</small>
            </span>
          </label>
          <button type="button" onClick={onFreeze} disabled={!acknowledged || freezing || !run?.result_sha256}>
            {freezing ? <><span className="spinner" />正在复核绑定</> : <><LockKeyhole size={15} />冻结纸面指令</>}
          </button>
        </div>
      ) : (
        <div className="quant-gate-blocked">
          <AlertTriangle size={17} />
          <span>未通过项：{(gate.failed_codes || []).join('、') || '等待完整实验结果'}。当前仍可用于研究模型差异。</span>
        </div>
      )}
    </section>
  )
}

function AuditLineage({ run, result }) {
  return (
    <section className="quant-panel">
      <div className="quant-section-head">
        <div>
          <span className="eyebrow">AUDIT & DATA LINEAGE</span>
          <h3>复现所需的版本、行情区间与内容哈希</h3>
          <p>历史价格序列、策略参数、输入证据、结果和事件链分别校验，避免界面刷新后静默改写。</p>
        </div>
        <span className={`quant-state ${run?.integrity?.verified ? 'verified' : 'danger'}`}>
          <Fingerprint size={13} />{run?.integrity?.verified ? '完整性已验证' : '完整性异常'}
        </span>
      </div>
      <div className="quant-audit-grid">
        <article><Fingerprint size={15} /><span><b>持仓指纹</b><code>{run?.holdings_sha256 || '—'}</code></span></article>
        <article><SlidersHorizontal size={15} /><span><b>政策哈希</b><code>{run?.policy_sha256 || '—'}</code></span></article>
        <article><Database size={15} /><span><b>证据哈希</b><code>{run?.evidence_sha256 || '—'}</code></span></article>
        <article><LockKeyhole size={15} /><span><b>结果哈希</b><code>{run?.result_sha256 || '—'}</code></span></article>
      </div>
      <div className="quant-table-wrap">
        <table className="quant-table quant-lineage-table">
          <thead><tr><th>资产</th><th>专业来源</th><th>历史区间</th><th>价格条数</th><th>序列哈希</th></tr></thead>
          <tbody>
            {(result?.market_data || []).map((item) => (
              <tr key={`${item.market}:${item.code}`}>
                <td><b>{item.name || item.code}</b><small>{item.market} · {item.code}</small></td>
                <td>{item.source || 'unknown'}</td>
                <td>{item.first_date} → {item.last_date}</td>
                <td>{item.price_count}</td>
                <td><code>{shortHash(item.price_sha256)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="quant-methodology">
        <summary>方法说明、已知限制与审计事件</summary>
        <div>
          <section>
            <h4>方法</h4>
            {Object.entries(result?.methodology || {}).map(([key, value]) => (
              <p key={key}><b>{key}</b><span>{value}</span></p>
            ))}
          </section>
          <section>
            <h4>限制</h4>
            {(result?.limitations || []).map((item) => <p key={item}>{item}</p>)}
          </section>
          <section>
            <h4>事件链</h4>
            {(run?.events || []).map((event) => (
              <p key={event.id}><b>#{event.sequence_no} {event.event_type}</b><span>{dateTime(event.created_at)} · {shortHash(event.event_hash)}</span></p>
            ))}
          </section>
        </div>
      </details>
    </section>
  )
}

export default function QuantPortfolioLab() {
  const [overview, setOverview] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [runs, setRuns] = useState([])
  const [mandates, setMandates] = useState([])
  const [activeRun, setActiveRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [launching, setLaunching] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [freezing, setFreezing] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState('')

  async function loadOverview({ initial = false } = {}) {
    const data = await fetchPortfolioQuantOverview(30)
    setOverview(data)
    setRuns(data.runs || [])
    setMandates(data.mandates || [])
    if (initial) {
      setForm(formFromDefaults(data.defaults))
      setActiveRun(data.latest_run || null)
    }
    return data
  }

  useEffect(() => {
    let active = true
    async function load() {
      try {
        const data = await fetchPortfolioQuantOverview(30)
        if (!active) return
        setOverview(data)
        setRuns(data.runs || [])
        setMandates(data.mandates || [])
        setForm(formFromDefaults(data.defaults))
        setActiveRun(data.latest_run || null)
      } catch (reason) {
        if (active) setError(reason.message || '量化组合实验室加载失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!activeRun?.id || !['queued', 'running'].includes(activeRun.status)) return undefined
    let cancelled = false
    let polling = false
    const timer = globalThis.setInterval(async () => {
      if (polling) return
      polling = true
      try {
        const next = await fetchPortfolioQuantRun(activeRun.id)
        if (cancelled) return
        setActiveRun(next)
        if (!['queued', 'running'].includes(next.status)) {
          const data = await fetchPortfolioQuantOverview(30)
          if (!cancelled) {
            setOverview(data)
            setRuns(data.runs || [])
            setMandates(data.mandates || [])
          }
        }
      } catch (reason) {
        if (!cancelled) setError(reason.message || '量化实验状态刷新失败')
      } finally {
        polling = false
      }
    }, 1800)
    return () => {
      cancelled = true
      globalThis.clearInterval(timer)
    }
  }, [activeRun?.id, activeRun?.status])

  const result = activeRun?.result
  const comparison = result?.selected_comparison || {}
  const current = comparison.current_weights || {}
  const selected = comparison.selected || {}
  const mandate = useMemo(
    () => mandates.find((item) => item.run_id === activeRun?.id) || null,
    [mandates, activeRun?.id],
  )
  const directStockCount = overview?.summary?.direct_stock_holding_count || 0

  function setField(key, value) {
    setForm((currentForm) => ({ ...currentForm, [key]: value }))
  }

  async function runExperiment() {
    if (launching) return
    setLaunching(true)
    setAcknowledged(false)
    setError('')
    try {
      const payload = {
        ...form,
        lookback_days: Number(form.lookback_days),
        rebalance_days: Number(form.rebalance_days),
        commission_bps: Number(form.commission_bps),
        slippage_bps: Number(form.slippage_bps),
        sell_tax_bps: Number(form.sell_tax_bps),
        max_turnover_pct: Number(form.max_turnover_pct),
        max_position_pct: Number(form.max_position_pct),
        minimum_trade_amount_cny: Number(form.minimum_trade_amount_cny),
      }
      const created = await createPortfolioQuantRun(payload)
      setActiveRun(created)
      setRuns((items) => [created, ...items.filter((item) => item.id !== created.id)].slice(0, 30))
      if (!['queued', 'running'].includes(created.status)) {
        await loadOverview()
      }
    } catch (reason) {
      setError(reason.message || '量化组合实验创建失败')
    } finally {
      setLaunching(false)
    }
  }

  async function openRun(run) {
    if (!run?.id || historyLoading) return
    setHistoryLoading(true)
    setAcknowledged(false)
    setError('')
    try {
      setActiveRun(await fetchPortfolioQuantRun(run.id))
    } catch (reason) {
      setError(reason.message || '历史量化实验读取失败')
    } finally {
      setHistoryLoading(false)
    }
  }

  async function freezeMandate() {
    if (!activeRun?.id || !activeRun.result_sha256 || freezing) return
    setFreezing(true)
    setError('')
    try {
      const response = await createPortfolioQuantMandate(activeRun.id, {
        acknowledged: true,
        expected_result_sha256: activeRun.result_sha256,
      })
      setMandates((items) => [
        response.item,
        ...items.filter((item) => item.id !== response.item.id),
      ])
      setAcknowledged(false)
    } catch (reason) {
      setError(reason.message || '纸面调仓指令冻结失败')
    } finally {
      setFreezing(false)
    }
  }

  if (loading) {
    return <div className="page-loading"><span className="spinner" />正在加载量化组合实验室</div>
  }

  return (
    <div className="quant-lab">
      <section className="quant-hero">
        <div className="quant-hero-main">
          <span className="eyebrow">PORTFOLIO QUANT LAB · WALK-FORWARD V1</span>
          <h2>把“哪种组合更稳”交给样本外检验，而不是回测幻觉</h2>
          <p>
            基于你的真实直接股票持仓，并行比较风险平价、最小方差、逆波动与等权；
            每个窗口只用过去估计，随后扣除换手、佣金、滑点和卖出税费。
          </p>
        </div>
        <div className="quant-hero-boundary">
          <ShieldCheck size={20} />
          <span>
            <b>风险研究，不承诺赚钱</b>
            <small>不优化历史收益、不自动挑选赢家、不连接券商、不生成实盘订单。</small>
          </span>
        </div>
      </section>

      <Workflow />

      {error && (
        <div className="quant-notice error">
          <AlertTriangle size={17} /><span>{error}</span>
          <button type="button" onClick={() => setError('')}>关闭</button>
        </div>
      )}

      <section className="quant-builder">
        <div className="quant-builder-head">
          <div>
            <span className="eyebrow">EXPERIMENT POLICY</span>
            <h3>你先选方法，系统负责公平地并行验证</h3>
            <p>模型不会因为历史结果更好而被自动替换；这样可以降低多次尝试后只展示赢家的偏差。</p>
          </div>
          <div className="quant-builder-facts">
            <span><small>直接股票</small><b>{directStockCount} 只</b></span>
            <span><small>历史实验</small><b>{overview?.summary?.run_count || 0} 次</b></span>
            <span><small>纸面指令</small><b>{overview?.summary?.mandate_count || 0} 份</b></span>
          </div>
        </div>

        <div className="quant-method-grid">
          {(overview?.methods || Object.keys(METHOD_COPY).filter((item) => item !== 'current_weights').map((id) => ({ id }))).map((method) => {
            const copy = METHOD_COPY[method.id] || { label: method.label || method.id, short: '' }
            return (
              <button
                type="button"
                key={method.id}
                className={form.construction_method === method.id ? 'active' : ''}
                onClick={() => setField('construction_method', method.id)}
              >
                <span>{form.construction_method === method.id ? <CheckCircle2 size={16} /> : <Scale size={16} />}</span>
                <b>{method.label || copy.label}</b>
                <small>{copy.short}</small>
              </button>
            )
          })}
        </div>

        <div className="quant-settings">
          <section>
            <header><History size={16} /><span><b>滚动验证</b><small>训练窗永远早于测试窗</small></span></header>
            <div>
              <label>
                <span>协方差估计窗口</span>
                <select value={form.lookback_days} onChange={(event) => setField('lookback_days', Number(event.target.value))}>
                  <option value={126}>126 交易日</option>
                  <option value={252}>252 交易日</option>
                  <option value={504}>504 交易日</option>
                </select>
              </label>
              <label>
                <span>再平衡 / 测试窗口</span>
                <select value={form.rebalance_days} onChange={(event) => setField('rebalance_days', Number(event.target.value))}>
                  <option value={21}>21 交易日（月度）</option>
                  <option value={63}>63 交易日（季度）</option>
                </select>
              </label>
            </div>
          </section>

          <section>
            <header><CircleDollarSign size={16} /><span><b>交易摩擦</b><small>每次样本外调仓都真实扣减</small></span></header>
            <div className="three">
              <label><span>佣金 bps</span><input type="number" min="0" max="100" value={form.commission_bps} onChange={(event) => setField('commission_bps', event.target.value)} /></label>
              <label><span>滑点 bps</span><input type="number" min="0" max="200" value={form.slippage_bps} onChange={(event) => setField('slippage_bps', event.target.value)} /></label>
              <label><span>卖出税费 bps</span><input type="number" min="0" max="200" value={form.sell_tax_bps} onChange={(event) => setField('sell_tax_bps', event.target.value)} /></label>
            </div>
          </section>

          <section>
            <header><SlidersHorizontal size={16} /><span><b>组合约束</b><small>还会自动叠加已激活投资政策</small></span></header>
            <div className="three">
              <label><span>最大单边换手 %</span><input type="number" min="5" max="100" value={form.max_turnover_pct} onChange={(event) => setField('max_turnover_pct', event.target.value)} /></label>
              <label><span>请求单股上限 %</span><input type="number" min="5" max="100" value={form.max_position_pct} onChange={(event) => setField('max_position_pct', event.target.value)} /></label>
              <label><span>最小动作金额 ¥</span><input type="number" min="0" max="10000000" step="100" value={form.minimum_trade_amount_cny} onChange={(event) => setField('minimum_trade_amount_cny', event.target.value)} /></label>
            </div>
          </section>
        </div>

        <div className="quant-run-row">
          <span>
            <Database size={16} />
            运行会冻结当前持仓、可信估值、投资政策、参数和每只股票的历史价格序列哈希。
          </span>
          <button type="button" onClick={runExperiment} disabled={launching || directStockCount < 2 || ['queued', 'running'].includes(activeRun?.status)}>
            {launching
              ? <><span className="spinner" />正在冻结输入</>
              : <><Play size={16} />运行量化组合实验</>}
          </button>
        </div>
      </section>

      <section className="quant-history">
        <span><History size={16} /><b>不可变实验历史</b><small>{runs.length} 次</small></span>
        <div>
          {runs.length ? runs.map((run) => (
            <button
              type="button"
              key={run.id}
              className={run.id === activeRun?.id ? 'active' : ''}
              onClick={() => openRun(run)}
            >
              <b>{METHOD_COPY[run.policy_summary?.construction_method || run.policy?.construction_method]?.label || '量化组合实验'}</b>
              <small>{dateTime(run.created_at)}</small>
              <RunState run={run} />
            </button>
          )) : <em>第一份实验完成后，输入与结果会留在这里。</em>}
        </div>
        {historyLoading && <RefreshCw size={15} className="spin-icon" />}
      </section>

      {['queued', 'running'].includes(activeRun?.status) ? (
        <PendingRun run={activeRun} />
      ) : activeRun?.status === 'failed' ? (
        <section className="quant-empty failed">
          <AlertTriangle size={32} />
          <h3>本次实验没有形成可用结果</h3>
          <p>{activeRun.error_message || '请检查专业行情可用性、共同交易日和持仓估值后重试。'}</p>
        </section>
      ) : !result ? (
        <EmptyResult directStockCount={directStockCount} />
      ) : (
        <div className="quant-results">
          <section className="quant-result-hero">
            <div>
              <span className="eyebrow">OUT-OF-SAMPLE RESULT</span>
              <h2>{result.selected_method_label} vs 当前权重</h2>
              <p>
                {result.walk_forward?.fold_count} 个成本后样本外窗口 ·
                {result.data_quality?.eligible_asset_count}/{result.data_quality?.requested_asset_count} 只资产可用 ·
                共同收益 {result.data_quality?.aligned_return_days} 日
              </p>
            </div>
            <div>
              <RunState run={activeRun} />
              <span className={`quant-state ${result.promotion_gate?.paper_mandate_eligible ? 'verified' : 'warning'}`}>
                {result.promotion_gate?.paper_mandate_eligible ? <ShieldCheck size={13} /> : <AlertTriangle size={13} />}
                {result.promotion_gate?.label}
              </span>
            </div>
          </section>

          <section className="quant-comparison">
            <ComparisonMetric label="成本后年化收益*" current={current.annualized_return_pct} selected={selected.annualized_return_pct} />
            <ComparisonMetric label="年化波动" current={current.annualized_volatility_pct} selected={selected.annualized_volatility_pct} lowerIsBetter />
            <ComparisonMetric label="最大回撤" current={current.max_drawdown_pct} selected={selected.max_drawdown_pct} lowerIsBetter />
            <ComparisonMetric label="Sharpe" current={current.sharpe_ratio} selected={selected.sharpe_ratio} formatter={ratio} />
            <ComparisonMetric label="PSR 统计诊断" current={current.probabilistic_sharpe_pct} selected={selected.probabilistic_sharpe_pct} />
          </section>

          <section className="quant-panel">
            <div className="quant-section-head">
              <div>
                <span className="eyebrow">PARALLEL MODEL COMPARISON</span>
                <h3>相同窗口、相同成本、相同股票池</h3>
                <p>所有模型接受同一套检验条件；“用户选择”在看结果前已冻结。</p>
              </div>
              <span>{result.data_quality?.first_aligned_date} → {result.data_quality?.last_aligned_date}</span>
            </div>
            <ModelTable models={result.models} />
          </section>

          <div className="quant-dual">
            <RiskContribution result={result} />
            <section className="quant-panel quant-diagnostic-panel">
              <div className="quant-section-head">
                <div>
                  <span className="eyebrow">RISK DIAGNOSTICS</span>
                  <h3>改善来自哪里</h3>
                  <p>只比较风险结构，不把统计指标解释成未来盈利概率。</p>
                </div>
              </div>
              <div className="quant-diagnostics">
                <article><TrendingDown size={18} /><span><small>波动变化</small><b>{pct(comparison.annualized_volatility_change_pct_points, 2, true)}</b><em>百分点</em></span></article>
                <article><BarChart3 size={18} /><span><small>回撤变化</small><b>{pct(comparison.max_drawdown_change_pct_points, 2, true)}</b><em>百分点</em></span></article>
                <article><Scale size={18} /><span><small>风险 HHI 变化</small><b>{ratio(comparison.risk_concentration_change, 4)}</b><em>越低通常越分散</em></span></article>
                <article><WalletCards size={18} /><span><small>最新袖套现金</small><b>{pct((result.models || []).find((item) => item.selected)?.latest_cash_within_sleeve_pct)}</b><em>单股上限无法容纳时保留</em></span></article>
              </div>
            </section>
          </div>

          <TargetActions target={result.target} />
          <FoldHistory result={result} />
          <PromotionGate
            run={activeRun}
            result={result}
            mandate={mandate}
            acknowledged={acknowledged}
            onAcknowledge={setAcknowledged}
            onFreeze={freezeMandate}
            freezing={freezing}
          />
          <AuditLineage run={activeRun} result={result} />
        </div>
      )}
    </div>
  )
}
