import { useState } from 'react'
import WorkspaceHeader from '../components/WorkspaceHeader'
import HoldingsTab from './HoldingsTab'
import WatchlistTab from './WatchlistTab'

const VIEWS = [
  { id: 'holdings', label: '真实持仓', description: '导入、确认和体检你的基金与股票' },
  { id: 'watchlist', label: '自选与提醒', description: '跟踪研究中的股票和评分变化' },
]

export default function PortfolioTab({ goAnalyze }) {
  const [view, setView] = useState('holdings')
  const current = VIEWS.find((item) => item.id === view) || VIEWS[0]

  return (
    <>
      <WorkspaceHeader
        eyebrow="我的组合"
        title={current.label}
        description={`${current.description}。持仓结论只使用你确认保存的数据。`}
        views={VIEWS}
        activeView={view}
        onViewChange={setView}
        ariaLabel="我的组合功能"
      />
      {view === 'holdings' && <HoldingsTab />}
      {view === 'watchlist' && <WatchlistTab goAnalyze={goAnalyze} />}
    </>
  )
}
