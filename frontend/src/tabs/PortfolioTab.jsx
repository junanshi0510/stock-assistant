import { useEffect, useState } from 'react'
import WorkspaceHeader from '../components/WorkspaceHeader'
import InvestmentPolicyPanel from '../features/portfolio/InvestmentPolicyPanel'
import PortfolioDecisionTwin from '../features/portfolio/PortfolioDecisionTwin'
import CapitalLearningHub from '../features/portfolio/CapitalLearningHub'
import HoldingsTab from './HoldingsTab'
import PortfolioLedgerTab from './PortfolioLedgerTab'
import WatchlistTab from './WatchlistTab'

const VIEWS = [
  { id: 'holdings', label: '持仓与纪律', description: '确认资产事实并为每项持仓建立持有与退出规则' },
  { id: 'policy', label: '投资政策', description: '确认预算、期限和组合风险边界' },
  { id: 'ledger', label: '交易账本', description: '记录真实现金流、费用和成本变化' },
  { id: 'twin', label: '情景实验室', description: '把真实组合放进压力情景并反推最小降险动作' },
  { id: 'learning', label: '决策学习', description: '对账冻结计划与真实成交，并用 5 / 20 / 60 交易日结果改进下一轮决策' },
  { id: 'watchlist', label: '观察清单', description: '跟踪尚未进入组合的研究对象' },
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
      {view === 'policy' && <InvestmentPolicyPanel />}
      {view === 'ledger' && <PortfolioLedgerTab />}
      {view === 'twin' && <PortfolioDecisionTwin />}
      {view === 'learning' && <CapitalLearningHub />}
      {view === 'watchlist' && <WatchlistTab goAnalyze={goAnalyze} />}
    </>
  )
}
