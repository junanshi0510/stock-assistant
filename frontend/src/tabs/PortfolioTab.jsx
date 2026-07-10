import { useState } from 'react'
import HoldingsTab from './HoldingsTab'
import WatchlistTab from './WatchlistTab'

const VIEWS = [
  ['holdings', '真实持仓', '导入、确认和体检你的基金与股票'],
  ['watchlist', '自选与提醒', '跟踪研究中的股票和评分变化'],
]

export default function PortfolioTab({ goAnalyze }) {
  const [view, setView] = useState('holdings')
  const current = VIEWS.find(([id]) => id === view) || VIEWS[0]

  return (
    <>
      <section className="workspace-header">
        <div>
          <span className="eyebrow">我的组合</span>
          <h2>{current[1]}</h2>
          <p>{current[2]}。持仓结论只使用你确认保存的数据。</p>
        </div>
        <div className="workspace-nav" role="tablist" aria-label="我的组合功能">
          {VIEWS.map(([id, label]) => (
            <button key={id} className={view === id ? 'active' : ''} onClick={() => setView(id)}>{label}</button>
          ))}
        </div>
      </section>
      {view === 'holdings' && <HoldingsTab />}
      {view === 'watchlist' && <WatchlistTab goAnalyze={goAnalyze} />}
    </>
  )
}
