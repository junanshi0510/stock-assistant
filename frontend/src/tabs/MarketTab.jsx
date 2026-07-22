import AnalyzeTab from './AnalyzeTab'
import WorkspaceHeader from '../components/WorkspaceHeader'
import DiscoverTab from './DiscoverTab'
import MultiCompareTab from './MultiCompareTab'
import SectorTab from './SectorTab'

const VIEWS = [
  { id: 'radar', label: '市场雷达', description: '热门股与市场焦点' },
  { id: 'sectors', label: '板块与概念', description: '行业热度和上涨归因' },
  { id: 'analyze', label: '个股研究', description: '走势、基本面与风险' },
  { id: 'compare', label: '多股对比', description: '收益、回撤与相关性' },
]

export default function MarketTab({ activeView, setActiveView, markets, market, setMarket, symbol, setSymbol, months, setMonths, runKey, requestRun, goAnalyze }) {
  const view = activeView || 'radar'
  const current = VIEWS.find((item) => item.id === view) || VIEWS[0]

  return (
    <>
      <WorkspaceHeader
        eyebrow="股票与板块"
        title={current.label}
        description={`${current.description}。先观察市场主线，再进入股票研究或横向比较。`}
        views={VIEWS}
        activeView={view}
        onViewChange={setActiveView}
        ariaLabel="股票与板块功能"
      />
      {view === 'radar' && <DiscoverTab markets={markets} goAnalyze={goAnalyze} />}
      {view === 'sectors' && <SectorTab goAnalyze={goAnalyze} />}
      {view === 'analyze' && <AnalyzeTab markets={markets} market={market} setMarket={setMarket} symbol={symbol} setSymbol={setSymbol} months={months} setMonths={setMonths} runKey={runKey} requestRun={requestRun} />}
      {view === 'compare' && <MultiCompareTab markets={markets} goAnalyze={goAnalyze} />}
    </>
  )
}
