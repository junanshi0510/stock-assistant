import AnalyzeTab from './AnalyzeTab'
import DiscoverTab from './DiscoverTab'
import MultiCompareTab from './MultiCompareTab'
import ScanTab from './ScanTab'
import SectorTab from './SectorTab'

const VIEWS = [
  ['radar', '市场雷达', '热门股与市场焦点'],
  ['sectors', '板块与概念', '行业热度和上涨归因'],
  ['analyze', '个股研究', '走势、基本面与风险'],
  ['compare', '多股对比', '收益、回撤与相关性'],
  ['scan', '批量筛选', '候选股票的横向筛选'],
]

export default function MarketTab({ activeView, setActiveView, markets, market, setMarket, symbol, setSymbol, months, setMonths, runKey, requestRun, goAnalyze }) {
  const view = activeView || 'radar'
  const current = VIEWS.find(([id]) => id === view) || VIEWS[0]

  return (
    <>
      <section className="workspace-header">
        <div>
          <span className="eyebrow">股票与板块</span>
          <h2>{current[1]}</h2>
          <p>{current[2]}。先观察市场主线，再进入股票研究或横向比较。</p>
        </div>
        <div className="workspace-nav" role="tablist" aria-label="股票与板块功能">
          {VIEWS.map(([id, label]) => (
            <button key={id} className={view === id ? 'active' : ''} onClick={() => setActiveView(id)}>{label}</button>
          ))}
        </div>
      </section>
      {view === 'radar' && <DiscoverTab markets={markets} goAnalyze={goAnalyze} />}
      {view === 'sectors' && <SectorTab goAnalyze={goAnalyze} />}
      {view === 'analyze' && <AnalyzeTab markets={markets} market={market} setMarket={setMarket} symbol={symbol} setSymbol={setSymbol} months={months} setMonths={setMonths} runKey={runKey} requestRun={requestRun} />}
      {view === 'compare' && <MultiCompareTab markets={markets} goAnalyze={goAnalyze} />}
      {view === 'scan' && <ScanTab markets={markets} goAnalyze={goAnalyze} />}
    </>
  )
}
