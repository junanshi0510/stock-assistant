import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Ban,
  BarChart3,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDollarSign,
  Database,
  Fingerprint,
  Gauge,
  History,
  Layers3,
  LockKeyhole,
  Pause,
  Play,
  RefreshCw,
  Scale,
  ShieldCheck,
  Sparkles,
  Target,
  TimerReset,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import {
  createAlphaForecastProgram,
  fetchAlphaForecastOverview,
  fetchAlphaForecastRun,
  runAlphaForecastProgram,
  settleAlphaForecastProgram,
  updateAlphaForecastProgram,
} from '../../api/alphaForecasts'

const STOCK_SAMPLES = {
  A股: '600519,贵州茅台\n300750,宁德时代\n601318,中国平安\n600036,招商银行\n000858,五粮液\n000333,美的集团',
  港股: '00700,腾讯控股\n09988,阿里巴巴-W\n03690,美团-W\n00941,中国移动\n01299,友邦保险\n02318,中国平安',
  美股: 'AAPL,Apple\nMSFT,Microsoft\nNVDA,NVIDIA\nAMZN,Amazon\nGOOGL,Alphabet\nMETA,Meta',
}

const FUND_SAMPLE = '110011,易方达中小盘\n161725,招商中证白酒\n005827,易方达蓝筹精选\n003095,中欧医疗健康\n001938,中欧时代先锋\n007119,睿远成长价值'

const DEFAULT_FORM = {
  name: 'A股多周期 Alpha 研究池',
  asset_type: 'stock',
  market: 'A股',
  symbols: STOCK_SAMPLES.A股,
  benchmark_symbol: '000300.SH',
  history_months: 60,
  cadence_days: 7,
  round_trip_cost_bps: 30,
}

const BENCHMARKS = {
  A股: '000300.SH',
  港股: '02800',
  美股: 'SPY',
}

const RUN_STATUS = {
  queued: ['等待 Worker', 'waiting'],
  running: ['滚动验证中', 'running'],
  succeeded: ['完整完成', 'verified'],
  partial: ['部分完成', 'warning'],
  failed: ['运行失败', 'danger'],
  cancelled: ['已取消', 'danger'],
}

const PROGRAM_STATUS = {
  active: ['自动观察', 'verified'],
  paused: ['已暂停', 'warning'],
  retired: ['已退役', 'danger'],
}

function parseAssets(text) {
  return String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [symbol, ...parts] = line.split(/[,，\t]/)
      return { symbol: String(symbol || '').trim(), name: parts.join(' ').trim() }
    })
    .filter((item) => item.symbol)
}

function number(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function probability(value, digits = 1) {
  const parsed = number(value)
  return parsed == null ? '—' : `${(parsed * 100).toFixed(digits)}%`
}

function pct(value, digits = 2, signed = false) {
  const parsed = number(value)
  if (parsed == null) return '—'
  return `${signed && parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`
}

function ratio(value, digits = 3) {
  const parsed = number(value)
  return parsed == null ? '—' : parsed.toFixed(digits)
}

function dateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleString('zh-CN', { hour12: false })
}

function shortHash(value) {
  return value ? `${String(value).slice(0, 12)}…` : '—'
}

function StateBadge({ value, map }) {
  const [label, tone] = map[value] || [value || '未知', 'waiting']
  return <span className={`alpha-state ${tone}`}><Activity size={12} />{label}</span>
}

function GateSummary({ gate }) {
  if (!gate) return <span className="alpha-state waiting">尚无门禁</span>
  return (
    <span className={`alpha-state ${gate.passed ? 'verified' : 'warning'}`}>
      {gate.passed ? <CheckCircle2 size={12} /> : <Ban size={12} />}
      {gate.passed_count}/{gate.total_count} · {gate.passed ? '历史可发布' : '明确弃权'}
    </span>
  )
}

function Workflow() {
  const steps = [
    ['01', '冻结研究协议', '资产池、基准、成本、周期与模型族创建后不可改'],
    ['02', '滚动样本外', '标签结束日必须早于测试起点，禁止随机切分和未来泄漏'],
    ['03', '独立概率校准', '早期 OOS 只校准，后期 OOS 才负责最终评分'],
    ['04', '双重发布门禁', '历史合格先进入 shadow，真实前瞻 6 批/30 条后再晋级'],
  ]
  return (
    <div className="alpha-workflow">
      {steps.map(([index, title, detail]) => (
        <article key={index}>
          <span>{index}</span>
          <div><b>{title}</b><small>{detail}</small></div>
        </article>
      ))}
    </div>
  )
}

function CreateProgram({ busy, onCreate }) {
  const [expanded, setExpanded] = useState(false)
  const [form, setForm] = useState(DEFAULT_FORM)

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))

  const switchAsset = (assetType) => {
    if (assetType === 'fund') {
      setForm((current) => ({
        ...current,
        asset_type: 'fund',
        market: '基金',
        name: '基金中长期 Alpha 研究池',
        symbols: FUND_SAMPLE,
        benchmark_symbol: '',
        history_months: 120,
        cadence_days: 30,
        round_trip_cost_bps: 50,
      }))
    } else {
      setForm(DEFAULT_FORM)
    }
  }

  const switchMarket = (market) => {
    setForm((current) => ({
      ...current,
      market,
      name: `${market}多周期 Alpha 研究池`,
      symbols: STOCK_SAMPLES[market],
      benchmark_symbol: BENCHMARKS[market],
    }))
  }

  const submit = async (event) => {
    event.preventDefault()
    await onCreate({
      ...form,
      history_months: Number(form.history_months),
      cadence_days: Number(form.cadence_days),
      round_trip_cost_bps: Number(form.round_trip_cost_bps),
      symbols: parseAssets(form.symbols),
    })
  }

  return (
    <section className="alpha-create">
      <button type="button" className="alpha-create-toggle" onClick={() => setExpanded((value) => !value)}>
        <span><Sparkles size={17} /><b>创建预登记概率项目</b><small>一次配置，自动滚动与前瞻观察</small></span>
        {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
      </button>
      {expanded && (
        <form onSubmit={submit}>
          <div className="alpha-segment">
            <button type="button" className={form.asset_type === 'stock' ? 'active' : ''} onClick={() => switchAsset('stock')}>股票 5 / 20 / 60 日</button>
            <button type="button" className={form.asset_type === 'fund' ? 'active' : ''} onClick={() => switchAsset('fund')}>基金 20 / 60 / 120 净值日</button>
          </div>
          {form.asset_type === 'stock' && (
            <div className="alpha-segment markets">
              {['A股', '港股', '美股'].map((market) => (
                <button type="button" className={form.market === market ? 'active' : ''} onClick={() => switchMarket(market)} key={market}>{market}</button>
              ))}
            </div>
          )}
          <div className="alpha-form-grid">
            <label><span>项目名称</span><input value={form.name} onChange={(event) => update('name', event.target.value)} maxLength={80} /></label>
            <label><span>{form.asset_type === 'fund' ? '预测目标' : '基准'}</span><input value={form.asset_type === 'fund' ? '扣除成本后正收益' : form.benchmark_symbol} onChange={(event) => update('benchmark_symbol', event.target.value)} disabled={form.asset_type === 'fund'} /></label>
            <label><span>固定历史窗口</span><select value={form.history_months} onChange={(event) => update('history_months', event.target.value)}>
              {(form.asset_type === 'fund' ? [60, 84, 120] : [36, 60, 84, 120]).map((value) => <option value={value} key={value}>{value} 个月</option>)}
            </select></label>
            <label><span>自动运行频率</span><select value={form.cadence_days} onChange={(event) => update('cadence_days', event.target.value)}>
              {[7, 14, 30].map((value) => <option value={value} key={value}>每 {value} 天</option>)}
            </select></label>
            <label><span>往返成本</span><div className="alpha-input-unit"><input type="number" min="0" max="300" step="1" value={form.round_trip_cost_bps} onChange={(event) => update('round_trip_cost_bps', event.target.value)} /><em>bps</em></div></label>
          </div>
          <label className="alpha-assets"><span>冻结资产池 <small>每行“代码,名称”，4–12 个；创建后不能换掉表现差的资产</small></span><textarea rows={7} value={form.symbols} onChange={(event) => update('symbols', event.target.value)} /></label>
          <div className="alpha-create-foot">
            <p><LockKeyhole size={15} />创建即冻结政策并启动首轮。概率不是收益承诺，不连接券商、不自动下单。</p>
            <button type="submit" disabled={busy}>{busy ? <RefreshCw className="spin" size={15} /> : <Play size={15} />}创建并运行</button>
          </div>
        </form>
      )}
    </section>
  )
}

function ProgramList({ programs, selectedId, onSelect }) {
  if (!programs.length) {
    return <div className="alpha-empty"><BrainCircuit size={30} /><b>尚无概率研究项目</b><p>从上方创建一个冻结资产池，系统会启动首轮滚动样本外验证。</p></div>
  }
  return (
    <div className="alpha-program-list">
      {programs.map((program) => {
        const run = program.latest_run
        const qualified = (program.forward_scorecard?.horizons || []).filter((item) => item.decision_eligible).length
        return (
          <button type="button" className={selectedId === program.id ? 'active' : ''} onClick={() => onSelect(program.id)} key={program.id}>
            <span className="alpha-program-title"><b>{program.name}</b><StateBadge value={program.status} map={PROGRAM_STATUS} /></span>
            <small>{program.market} · {(program.policy?.symbols || []).length} 个资产 · {program.policy?.horizons?.join('/')} 期</small>
            <span className="alpha-program-meta">
              <em>{run ? RUN_STATUS[run.status]?.[0] || run.status : '尚未运行'}</em>
              <em>{qualified ? `${qualified} 个周期已晋级` : '前瞻证据积累中'}</em>
            </span>
          </button>
        )
      })}
    </div>
  )
}

function ForwardScorecard({ scorecard }) {
  const horizons = scorecard?.horizons || []
  return (
    <section className="alpha-panel">
      <header>
        <div><span className="eyebrow">Forward release gate</span><h3><ShieldCheck size={18} />真实前瞻发布门禁</h3></div>
        <span className={`alpha-release ${scorecard?.status === 'qualified' ? 'qualified' : ''}`}>
          {scorecard?.status === 'qualified' ? <CheckCircle2 size={14} /> : <TimerReset size={14} />}
          {scorecard?.status === 'qualified' ? '已进入决策层' : 'Shadow · 积累中'}
        </span>
      </header>
      {!horizons.length ? <p className="alpha-hint">首轮预测完成并成熟后，这里会只用冻结后的真实结果评分。</p> : (
        <div className="alpha-forward-grid">
          {horizons.map((item) => (
            <article key={item.horizon_sessions}>
              <span><b>{item.horizon_sessions} 期</b><em className={item.decision_eligible ? 'good' : ''}>{item.decision_eligible ? '可进入决策层' : '继续观察'}</em></span>
              <div><strong>{item.outcome_count}</strong><small>真实结果</small></div>
              <div><strong>{item.run_date_count}</strong><small>独立批次</small></div>
              <div><strong>{ratio(item.brier_skill_score)}</strong><small>Brier Skill</small></div>
              <div><strong>{pct(item.high_low_return_spread_pct, 2, true)}</strong><small>高低组收益差</small></div>
              {!!item.source_excluded_outcome_count && <small className="alpha-source-excluded">{item.source_excluded_outcome_count} 个结果因来源等级不足，仅保留审计</small>}
              <footer>{(item.checks || []).map((check) => <i className={check.passed ? 'passed' : ''} title={`${check.label}: ${check.value ?? '—'} / ${check.threshold}`} key={check.code} />)}</footer>
            </article>
          ))}
        </div>
      )}
      <p className="alpha-notice"><Scale size={14} />不足 6 个独立运行日或 30 个结果时不会晋级；历史结果无法代替这里的前瞻证据。</p>
    </section>
  )
}

function HorizonEvidence({ item }) {
  const metrics = item.evaluation || {}
  const gate = item.historical_gate
  return (
    <article className="alpha-horizon-card">
      <header>
        <div><b>{item.horizon_sessions} 个确认观察期</b><small>{item.calibration?.start || '—'} → {metrics.end || '—'}</small></div>
        <GateSummary gate={gate} />
      </header>
      <div className="alpha-metric-grid">
        <span><small>最终评估样本</small><b>{metrics.sample_count ?? 0}</b><em>{metrics.date_count ?? 0} 个日期</em></span>
        <span><small>Brier Skill</small><b>{ratio(metrics.brier_skill_score)}</b><em>必须优于 0</em></span>
        <span><small>ROC AUC</small><b>{ratio(metrics.roc_auc)}</b><em>门槛 0.52</em></span>
        <span><small>校准误差 ECE</small><b>{ratio(metrics.expected_calibration_error)}</b><em>上限 0.12</em></span>
        <span><small>高低概率组收益差</small><b>{pct(metrics.high_low_return_spread_pct, 2, true)}</b><em>已扣冻结成本</em></span>
        <span><small>跨折稳定率</small><b>{probability(metrics.fold_stability)}</b><em>门槛 50%</em></span>
      </div>
      <div className="alpha-gates">
        {(gate?.checks || []).map((check) => (
          <span className={check.passed ? 'passed' : ''} key={check.code}>
            {check.passed ? <CheckCircle2 size={13} /> : <Ban size={13} />}
            <b>{check.label}</b><small>{check.value ?? '—'} / {check.threshold}</small>
          </span>
        ))}
      </div>
    </article>
  )
}

function ForecastMatrix({ result }) {
  const horizons = (result?.horizons || []).map((item) => item.horizon_sessions)
  const forecasts = result?.forecasts || []
  const consensus = new Map((result?.consensus || []).map((item) => [item.symbol, item]))
  const symbols = [...new Map(forecasts.map((item) => [item.symbol, item])).values()]
  if (!forecasts.length) return <div className="alpha-empty compact"><Ban size={24} /><b>本轮没有可形成的概率事实</b><p>历史不足或全部周期未形成有效滚动样本外折。</p></div>
  return (
    <section className="alpha-panel">
      <header>
        <div><span className="eyebrow">Current shadow forecast</span><h3><Target size={18} />当前多周期概率矩阵</h3></div>
        <span className="alpha-state warning"><LockKeyhole size={12} />未自动下单</span>
      </header>
      <div className="alpha-table-wrap">
        <table className="alpha-table">
          <thead><tr><th>资产 / 多周期结论</th>{horizons.map((horizon) => <th key={horizon}>{horizon} 期</th>)}<th>治理状态</th></tr></thead>
          <tbody>
            {symbols.map((asset) => {
              const summary = consensus.get(asset.symbol)
              return (
                <tr key={asset.symbol}>
                  <td><b>{asset.name || asset.symbol}</b><small>{asset.symbol} · 截至 {asset.as_of_date}</small><em className={`alpha-consensus ${summary?.state || 'abstain'}`}>{summary?.label || '无共识'}</em></td>
                  {horizons.map((horizon) => {
                    const item = forecasts.find((row) => row.symbol === asset.symbol && row.horizon_sessions === horizon)
                    if (!item) return <td key={horizon}>—</td>
                    const published = item.published_probability
                    return (
                      <td key={horizon}>
                        <strong className={published == null ? 'abstain' : number(published) >= 0.57 ? 'bull' : number(published) <= 0.43 ? 'bear' : ''}>
                          {published == null ? '弃权' : probability(published)}
                        </strong>
                        <small>{item.stance}</small>
                        {published != null && <em>区间命中 {probability(item.neighborhood?.wilson_low)}–{probability(item.neighborhood?.wilson_high)}</em>}
                      </td>
                    )
                  })}
                  <td><span className={`alpha-release mini ${asset.decision_eligible ? 'qualified' : ''}`}>{asset.decision_eligible ? '前瞻合格' : 'Shadow'}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="alpha-notice"><AlertTriangle size={14} />“概率”指冻结目标在指定观察期后跑赢基准并覆盖成本（股票），或取得成本后正收益（基金）；不是“必涨概率”。长短周期冲突时应降低仓位或继续观察。</p>
    </section>
  )
}

function RunResult({ run }) {
  if (!run) return <div className="alpha-empty"><History size={28} /><b>选择一个运行查看证据</b></div>
  if (['queued', 'running'].includes(run.status)) {
    return (
      <div className="alpha-running">
        <span className="spinner" />
        <div><b>{run.progress?.message || '正在运行'}</b><small>{run.progress?.completed || 0}/{run.progress?.total || 0} · 任务只传 Run ID，结果写回权威数据库</small></div>
      </div>
    )
  }
  if (run.status === 'failed') {
    return <div className="alpha-error"><AlertTriangle size={22} /><div><b>{run.error_code || '运行失败'}</b><p>{run.error_message || '请检查真实数据源可用性。'}</p></div></div>
  }
  const result = run.result
  if (!result) return <div className="alpha-empty compact"><Database size={24} /><b>结果尚未写入</b></div>
  return (
    <div className="alpha-results">
      <section className="alpha-result-head">
        <div><span className="eyebrow">Audited run</span><h2>{result.policy?.name}</h2><p>{result.policy?.market} · {result.policy?.objective === 'benchmark_excess_after_cost' ? `跑赢 ${result.policy?.benchmark_symbol} 并覆盖成本` : '确认净值取得成本后正收益'}</p></div>
        <div><StateBadge value={run.status} map={RUN_STATUS} /><small><Fingerprint size={12} />{shortHash(run.result_sha256)}</small><small>{dateTime(run.completed_at)}</small></div>
      </section>
      <ForecastMatrix result={result} />
      <section className="alpha-evidence-section">
        <div className="alpha-section-heading"><div><span className="eyebrow">Historical evidence</span><h3><BarChart3 size={18} />逐周期样本外证据</h3></div><small>10 项固定统计门槛和数据发布边界全部通过才发布 shadow 概率</small></div>
        <div className="alpha-horizons">{(result.horizons || []).map((item) => <HorizonEvidence item={item} key={item.horizon_sessions} />)}</div>
      </section>
      <section className="alpha-panel">
        <header><div><span className="eyebrow">Data lineage</span><h3><Database size={18} />真实数据与方法审计</h3></div><span className="alpha-state verified"><Fingerprint size={12} />输入 {shortHash(result.input_sha256)}</span></header>
        <div className="alpha-audit-grid">
          <span><small>资产覆盖</small><b>{result.data_quality?.loaded_assets}/{result.data_quality?.requested_assets}</b><em>{result.data_quality?.panel_rows?.toLocaleString('zh-CN')} 个面板事实</em></span>
          <span><small>历史区间</small><b>{result.data_quality?.panel_start}</b><em>至 {result.data_quality?.panel_end}</em></span>
          <span><small>最低来源等级</small><b>{result.data_quality?.minimum_source_tier || 'unknown'}</b><em>基准 {result.data_quality?.benchmark_source_tier || '—'}</em></span>
          <span><small>切分与净化</small><b>Chronological OOS</b><em>label_end &lt; test_start</em></span>
          <span><small>参数搜索</small><b>{result.methodology?.parameter_search ? '存在' : '关闭'}</b><em>固定 Logistic + Sigmoid</em></span>
        </div>
        {result.source_release_gate && !result.source_release_gate.shadow_release_eligible && <div className="alpha-warning"><AlertTriangle size={15} /><span><b>数据发布边界未通过，本次所有周期弃权</b><small>{result.source_release_gate.notice}</small></span></div>}
        {!!result.fetch_failures?.length && <div className="alpha-warning"><AlertTriangle size={15} /><span><b>部分真实数据源失败</b>{result.fetch_failures.map((item) => <small key={item.symbol}>{item.symbol}: {item.error}</small>)}</span></div>}
      </section>
    </div>
  )
}

export default function AlphaForecastLab() {
  const [overview, setOverview] = useState(null)
  const [selectedProgramId, setSelectedProgramId] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [runDetail, setRunDetail] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setBusy('load')
    try {
      const data = await fetchAlphaForecastOverview(40)
      setOverview(data)
      setError('')
      setSelectedProgramId((current) => current && data.programs.some((item) => item.id === current) ? current : data.programs[0]?.id || '')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      if (!quiet) setBusy('')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const programs = overview?.programs || []
  const runs = overview?.runs || []
  const selectedProgram = programs.find((item) => item.id === selectedProgramId) || programs[0]
  const programRuns = useMemo(
    () => runs.filter((item) => item.program_id === selectedProgram?.id),
    [runs, selectedProgram?.id],
  )

  useEffect(() => {
    setSelectedRunId((current) => current && programRuns.some((item) => item.id === current) ? current : programRuns[0]?.id || '')
  }, [selectedProgramId, programRuns])

  const selectedRun = runDetail?.id === selectedRunId
    ? runDetail
    : programRuns.find((item) => item.id === selectedRunId)

  useEffect(() => {
    if (!selectedRunId) {
      setRunDetail(null)
      return undefined
    }
    let cancelled = false
    fetchAlphaForecastRun(selectedRunId)
      .then((data) => { if (!cancelled) setRunDetail(data) })
      .catch((requestError) => { if (!cancelled) setError(requestError.message) })
    return () => { cancelled = true }
  }, [selectedRunId])

  const active = runs.some((item) => ['queued', 'running'].includes(item.status))
  useEffect(() => {
    if (!active) return undefined
    const timer = globalThis.setInterval(() => load({ quiet: true }), 4000)
    return () => globalThis.clearInterval(timer)
  }, [active, load])

  const perform = async (key, action, success) => {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      const result = await action()
      setNotice(success)
      await load({ quiet: true })
      if (result?.id && key === 'run') setSelectedRunId(result.id)
      if (result?.initial_run?.id) setSelectedRunId(result.initial_run.id)
      if (result?.program?.id) setSelectedProgramId(result.program.id)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="alpha-lab">
      <section className="alpha-hero">
        <div>
          <span className="eyebrow">Calibrated Alpha · Model governance</span>
          <h1><BrainCircuit size={25} />多周期 Alpha 概率实验室</h1>
          <p>把“会涨吗”改造成可审计的条件概率问题：滚动样本外、独立校准、经济性门槛、真实前瞻再验证。证据不足时，系统有义务回答“不知道”。</p>
        </div>
        <div className="alpha-hero-badges">
          <span><LockKeyhole size={14} />策略预登记</span>
          <span><Gauge size={14} />Brier / ECE</span>
          <span><CircleDollarSign size={14} />成本后目标</span>
          <span><ShieldCheck size={14} />双重发布门禁</span>
        </div>
      </section>
      <Workflow />

      <div className="alpha-summary">
        <span><Layers3 size={17} /><b>{overview?.summary?.program_count || 0}</b><small>研究项目</small></span>
        <span><Activity size={17} /><b>{overview?.summary?.active_run_count || 0}</b><small>运行中</small></span>
        <span><Target size={17} /><b>{overview?.summary?.published_shadow_forecast_count || 0}</b><small>历史合格 Shadow</small></span>
        <span><ShieldCheck size={17} /><b>{overview?.summary?.decision_eligible_horizon_count || 0}</b><small>前瞻合格周期</small></span>
        <button type="button" onClick={() => load()} disabled={busy === 'load'}><RefreshCw className={busy === 'load' ? 'spin' : ''} size={15} />刷新</button>
      </div>

      {error && <div className="alpha-error"><AlertTriangle size={20} /><div><b>操作未完成</b><p>{error}</p></div></div>}
      {notice && <div className="alpha-success"><CheckCircle2 size={18} />{notice}</div>}

      <CreateProgram busy={busy === 'create'} onCreate={(payload) => perform('create', () => createAlphaForecastProgram(payload), '项目已冻结，首轮研究已经启动。')} />

      <div className="alpha-layout">
        <aside>
          <div className="alpha-aside-title"><div><span className="eyebrow">Programs</span><b>预登记项目</b></div><small>{programs.length}</small></div>
          <ProgramList programs={programs} selectedId={selectedProgram?.id} onSelect={(id) => { setSelectedProgramId(id); setRunDetail(null) }} />
        </aside>
        <main>
          {selectedProgram ? (
            <>
              <section className="alpha-program-head">
                <div><span className="eyebrow">{selectedProgram.asset_type === 'fund' ? 'Fund program' : `${selectedProgram.market} program`}</span><h2>{selectedProgram.name}</h2><p>{(selectedProgram.policy?.symbols || []).map((item) => item.symbol).join(' · ')}</p></div>
                <div className="alpha-program-actions">
                  <StateBadge value={selectedProgram.status} map={PROGRAM_STATUS} />
                  {selectedProgram.status === 'active' && <button type="button" onClick={() => perform('run', () => runAlphaForecastProgram(selectedProgram.id), '新一轮概率研究已派发。')} disabled={!!busy || active}><Play size={14} />立即运行</button>}
                  {selectedProgram.status === 'active' && <button type="button" className="secondary" onClick={() => perform('pause', () => updateAlphaForecastProgram(selectedProgram.id, 'pause'), '自动运行已暂停。')} disabled={!!busy}><Pause size={14} />暂停</button>}
                  {selectedProgram.status === 'paused' && <button type="button" onClick={() => perform('resume', () => updateAlphaForecastProgram(selectedProgram.id, 'resume'), '项目已恢复并进入待运行状态。')} disabled={!!busy}><Play size={14} />恢复</button>}
                  {selectedProgram.status !== 'retired' && <button type="button" className="secondary" onClick={() => perform('settle', () => settleAlphaForecastProgram(selectedProgram.id), '已提交真实结果核对，生产环境由行情 Worker 执行。')} disabled={!!busy}><TimerReset size={14} />核对结果</button>}
                </div>
              </section>
              <div className="alpha-policy-strip">
                <span><CalendarClock size={14} /><b>{selectedProgram.policy?.history_months} 个月</b><small>固定历史起点 {selectedProgram.policy?.training_start_date}</small></span>
                <span><Target size={14} /><b>{selectedProgram.policy?.horizons?.join(' / ')}</b><small>冻结预测周期</small></span>
                <span><CircleDollarSign size={14} /><b>{selectedProgram.policy?.round_trip_cost_bps} bps</b><small>标签内往返成本</small></span>
                <span><RefreshCw size={14} /><b>每 {selectedProgram.policy?.cadence_days} 天</b><small>下次 {dateTime(selectedProgram.next_run_at)}</small></span>
                <span><Fingerprint size={14} /><b>{shortHash(selectedProgram.policy_sha256)}</b><small>不可变政策摘要</small></span>
              </div>
              <ForwardScorecard scorecard={selectedProgram.forward_scorecard} />
              <div className="alpha-run-nav">
                <div><span className="eyebrow">Immutable runs</span><b>历史运行</b></div>
                <div>{programRuns.map((run) => <button type="button" className={selectedRunId === run.id ? 'active' : ''} onClick={() => { setSelectedRunId(run.id); setRunDetail(null) }} key={run.id}><StateBadge value={run.status} map={RUN_STATUS} /><small>{run.as_of_date}</small></button>)}</div>
              </div>
              <RunResult run={selectedRun} />
            </>
          ) : <div className="alpha-empty"><BrainCircuit size={30} /><b>从创建一个研究项目开始</b><p>建议先用同市场、流动性较高且逻辑相近的 6–12 个资产。</p></div>}
        </main>
      </div>
    </div>
  )
}
