import { useCallback, useEffect, useMemo, useState } from 'react'
import { Archive, Boxes, History, Pencil, Play, Plus, Radar, RefreshCw, ShieldCheck, WalletCards } from 'lucide-react'
import WorkspaceHeader from '../components/WorkspaceHeader'
import MarketProviderStatus from '../components/MarketProviderStatus'
import { fetchMarketProviders } from '../api/market'
import {
  archiveOpportunityStrategy,
  createOpportunityPaperBasket,
  fetchOpportunityOverview,
  fetchOpportunityRun,
  fetchOpportunityTemplates,
  startOpportunityRun,
} from '../api/opportunities'
import PaperTracker from '../features/opportunities/PaperTracker'
import RunResults from '../features/opportunities/RunResults'
import StrategyBuilder from '../features/opportunities/StrategyBuilder'

const VIEWS = [
  { id: 'campaigns', label: '策略与扫描', description: '定义候选池、因子、淘汰门槛和组合约束' },
  { id: 'paper', label: '纸面跟踪', description: '只看冻结之后的前瞻表现' },
]

function dateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function runStatus(value) {
  return {
    queued: '排队', running: '扫描中', succeeded: '完成', partial: '部分完成', failed: '失败', cancelled: '已取消',
  }[value] || value
}

export default function OpportunityTab({ goAnalyze }) {
  const [view, setView] = useState('campaigns')
  const [templates, setTemplates] = useState([])
  const [overview, setOverview] = useState(null)
  const [selectedStrategyId, setSelectedStrategyId] = useState(null)
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [selectedBasketId, setSelectedBasketId] = useState(null)
  const [run, setRun] = useState(null)
  const [builderMode, setBuilderMode] = useState(null)
  const [loading, setLoading] = useState(true)
  const [runLoading, setRunLoading] = useState(false)
  const [paperBusy, setPaperBusy] = useState(false)
  const [error, setError] = useState('')
  const [providerStatus, setProviderStatus] = useState(null)

  const refreshOverview = useCallback(async () => {
    const result = await fetchOpportunityOverview()
    setOverview(result)
    setSelectedStrategyId((current) => current || result.strategies?.[0]?.id || null)
    return result
  }, [])

  useEffect(() => {
    let live = true
    Promise.all([
      fetchOpportunityTemplates(),
      fetchOpportunityOverview(),
      fetchMarketProviders().catch(() => null),
    ])
      .then(([templateResult, overviewResult, providerResult]) => {
        if (!live) return
        setTemplates(templateResult.items || [])
        setOverview(overviewResult)
        setProviderStatus(providerResult)
        setSelectedStrategyId(overviewResult.strategies?.[0]?.id || null)
        if (!overviewResult.strategies?.length) setBuilderMode('new')
      })
      .catch((requestError) => { if (live) setError(requestError.message) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  const strategies = overview?.strategies || []
  const runs = overview?.runs || []
  const baskets = overview?.paper_baskets || []
  const selectedStrategy = strategies.find((item) => item.id === selectedStrategyId) || null
  const strategyRuns = useMemo(
    () => runs.filter((item) => item.strategy_id === selectedStrategyId),
    [runs, selectedStrategyId],
  )

  useEffect(() => {
    if (!selectedStrategyId) {
      setSelectedRunId(null); setRun(null)
      return
    }
    const latest = strategyRuns[0]?.id || null
    setSelectedRunId((current) => strategyRuns.some((item) => item.id === current) ? current : latest)
  }, [selectedStrategyId, strategyRuns])

  useEffect(() => {
    if (!selectedRunId) {
      setRun(null)
      return
    }
    let live = true
    setRunLoading(true); setError('')
    fetchOpportunityRun(selectedRunId)
      .then((result) => { if (live) setRun(result) })
      .catch((requestError) => { if (live) setError(requestError.message) })
      .finally(() => { if (live) setRunLoading(false) })
    return () => { live = false }
  }, [selectedRunId])

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return undefined
    let live = true
    const timer = globalThis.setInterval(async () => {
      try {
        const next = await fetchOpportunityRun(run.id)
        if (!live) return
        setRun(next)
        if (!['queued', 'running'].includes(next.status)) await refreshOverview()
      } catch (requestError) {
        if (live) setError(requestError.message)
      }
    }, 2000)
    return () => { live = false; globalThis.clearInterval(timer) }
  }, [run, refreshOverview])

  async function savedStrategy(saved) {
    setBuilderMode(null)
    setSelectedStrategyId(saved.id)
    try { await refreshOverview() } catch (requestError) { setError(requestError.message) }
  }

  async function startRun() {
    if (!selectedStrategy) return
    setRunLoading(true); setError('')
    try {
      const created = await startOpportunityRun(selectedStrategy.id)
      setRun(created)
      setSelectedRunId(created.id)
      await refreshOverview()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setRunLoading(false)
    }
  }

  async function archiveStrategy() {
    if (!selectedStrategy || !globalThis.confirm(`归档策略“${selectedStrategy.definition.name}”？历史版本和扫描结果会保留。`)) return
    setError('')
    try {
      await archiveOpportunityStrategy(selectedStrategy.id)
      setSelectedStrategyId(null)
      setSelectedRunId(null)
      setRun(null)
      await refreshOverview()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function createPaper() {
    if (!run) return
    setPaperBusy(true); setError('')
    try {
      const response = await createOpportunityPaperBasket(run.id)
      setSelectedBasketId(response.item.id)
      await refreshOverview()
      setView('paper')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setPaperBusy(false)
    }
  }

  const currentView = VIEWS.find((item) => item.id === view) || VIEWS[0]

  return (
    <div className="opp-workspace">
      <WorkspaceHeader
        eyebrow="机会工厂"
        title={currentView.label}
        description={`${currentView.description}。所有结论都绑定真实数据、策略版本和明确候选范围。`}
        views={VIEWS}
        activeView={view}
        onViewChange={setView}
        ariaLabel="机会工厂功能"
      />
      <div className="opp-product-banner">
        <Radar size={19} />
        <div><b>从“看一只股票”升级为可复现的投资研究流水线</b><span>候选发现 → 数据门禁 → 同市场多因子 → 相关性与仓位约束 → 前瞻纸面复盘</span></div>
        <em><ShieldCheck size={14} />不承诺涨跌，不自动交易</em>
      </div>
      <MarketProviderStatus data={providerStatus} />
      {error && <div className="error opp-global-error">{error}</div>}
      {loading && <div className="page-loading"><span className="spinner" />正在加载机会工厂</div>}

      {!loading && view === 'paper' && <PaperTracker baskets={baskets} selectedId={selectedBasketId} onSelect={setSelectedBasketId} onRefresh={refreshOverview} />}

      {!loading && view === 'campaigns' && builderMode && <StrategyBuilder
        templates={templates}
        strategy={builderMode === 'edit' ? selectedStrategy : null}
        onSaved={savedStrategy}
        onCancel={() => setBuilderMode(null)}
      />}

      {!loading && view === 'campaigns' && !builderMode && <div className="opp-campaign-layout">
        <aside className="opp-strategy-rail">
          <div className="opp-rail-head"><div><span className="eyebrow">策略库</span><b>{strategies.length} 个活动策略</b></div><button className="icon-button" onClick={() => setBuilderMode('new')} aria-label="新建策略"><Plus size={17} /></button></div>
          {strategies.map((strategyItem) => {
            const latestRun = runs.find((item) => item.strategy_id === strategyItem.id)
            return <button key={strategyItem.id} className={strategyItem.id === selectedStrategyId ? 'active' : ''} onClick={() => setSelectedStrategyId(strategyItem.id)}>
              <span><Boxes size={16} /><b>{strategyItem.definition.name}</b></span>
              <small>v{strategyItem.current_version_no} · {strategyItem.definition.markets.join(' / ')}</small>
              <em>{latestRun ? `${runStatus(latestRun.status)} · ${dateTime(latestRun.created_at)}` : '尚未运行'}</em>
            </button>
          })}
          {!strategies.length && <div className="opp-rail-empty">还没有策略。</div>}
        </aside>

        <main className="opp-campaign-main">
          {selectedStrategy ? <>
            <section className="opp-strategy-head">
              <div><span className="eyebrow">当前策略 · 不可变版本 v{selectedStrategy.current_version_no}</span><h2>{selectedStrategy.definition.name}</h2><p>{selectedStrategy.definition.description}</p><div className="opp-strategy-meta"><span>{selectedStrategy.definition.markets.join(' / ')}</span><span>{selectedStrategy.definition.history_months} 个月历史</span><span>最低综合分 {selectedStrategy.definition.gates.min_composite_score}</span><span>最多 {selectedStrategy.definition.portfolio.max_positions} 只</span></div></div>
              <div className="opp-strategy-actions"><button className="ghost" onClick={() => setBuilderMode('edit')}><Pencil size={14} />新版本</button><button className="ghost danger-text" onClick={archiveStrategy}><Archive size={14} />归档</button><button onClick={startRun} disabled={runLoading || ['queued', 'running'].includes(run?.status)}>{runLoading ? <><span className="spinner" />启动中</> : <><Play size={15} />运行策略</>}</button></div>
            </section>
            <nav className="opp-run-history" aria-label="扫描历史">
              <span><History size={15} />运行历史</span>
              <div>{strategyRuns.map((item) => <button key={item.id} className={item.id === selectedRunId ? 'active' : ''} onClick={() => setSelectedRunId(item.id)}><b>{runStatus(item.status)}</b><small>{dateTime(item.created_at)}</small></button>)}{!strategyRuns.length && <em>保存策略后点击“运行策略”</em>}</div>
              {selectedRunId && <button className="icon-button ghost" onClick={async () => { setRunLoading(true); try { setRun(await fetchOpportunityRun(selectedRunId)) } finally { setRunLoading(false) } }} aria-label="刷新运行"><RefreshCw size={15} /></button>}
            </nav>
            {runLoading && !run ? <div className="page-loading"><span className="spinner" />读取扫描结果</div> : <RunResults run={run} goAnalyze={goAnalyze} onCreatePaper={createPaper} paperBusy={paperBusy} />}
          </> : <section className="opp-no-strategy"><WalletCards size={30} /><h3>建立第一个可复现策略</h3><p>从模板开始，明确候选范围、证据门槛和组合上限。</p><button onClick={() => setBuilderMode('new')}><Plus size={15} />新建策略</button></section>}
        </main>
      </div>}
    </div>
  )
}
