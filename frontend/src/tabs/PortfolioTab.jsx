import { useEffect, useState } from 'react'
import WorkspaceHeader from '../components/WorkspaceHeader'
import HoldingsTab from './HoldingsTab'
import PortfolioLedgerTab from './PortfolioLedgerTab'
import WatchlistTab from './WatchlistTab'

const VIEWS = [
  { id: 'holdings', label: '真实持仓', description: '导入、确认和体检你的基金与股票' },
  { id: 'ledger', label: '交易与复盘', description: '记录成本、核对份额并复盘仓位纪律' },
  { id: 'watchlist', label: '自选与提醒', description: '跟踪研究中的股票和评分变化' },
]

export default function PortfolioTab({ goAnalyze, activeView = 'holdings', onViewChange }) {
  const [view, setView] = useState('holdings')
  const current = VIEWS.find((item) => item.id === view) || VIEWS[0]

  useEffect(() => {
    if (VIEWS.some((item) => item.id === activeView)) setView(activeView)
  }, [activeView])

  function changeView(nextView) {
    setView(nextView)
    onViewChange?.(nextView)
  }

  return (
    <>
      <WorkspaceHeader
        eyebrow="我的组合"
        title={current.label}
        description={`${current.description}。持仓结论只使用你确认保存的数据。`}
        views={VIEWS}
        activeView={view}
        onViewChange={changeView}
        ariaLabel="我的组合功能"
      />
      {view === 'holdings' && <HoldingsTab />}
      {view === 'ledger' && <PortfolioLedgerTab />}
      {view === 'watchlist' && <WatchlistTab goAnalyze={goAnalyze} />}
    </>
  )
}
