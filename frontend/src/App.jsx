import { lazy, Suspense, useEffect, useState } from 'react'
import { Bot, BriefcaseBusiness, Info, LayoutDashboard, Search, TrendingUp } from 'lucide-react'
import { fetchMarkets } from './api/market'

const AgentTab = lazy(() => import('./tabs/AgentTab'))
const DashboardTab = lazy(() => import('./tabs/DashboardTab'))
const PortfolioTab = lazy(() => import('./tabs/PortfolioTab'))
const ResearchTab = lazy(() => import('./tabs/ResearchTab'))

const TABS = [
  { id: 'overview', label: '今日决策', icon: LayoutDashboard },
  { id: 'portfolio', label: '我的资产', icon: BriefcaseBusiness },
  { id: 'research', label: '研究中心', icon: Search },
  { id: 'agent', label: '投资 Agent', icon: Bot },
]

export default function App() {
  const [markets, setMarkets] = useState(['A股', '港股', '美股'])
  const [tab, setTab] = useState('overview')
  const [researchDomain, setResearchDomain] = useState('funds')
  const [marketView, setMarketView] = useState('radar')
  const [portfolioView, setPortfolioView] = useState('holdings')

  // 单股分析的共享状态(供扫描页点击跳转使用)
  const [market, setMarket] = useState('A股')
  const [symbol, setSymbol] = useState('')
  const [months, setMonths] = useState(12)
  const [runKey, setRunKey] = useState(0)

  useEffect(() => {
    fetchMarkets().then((d) => d.markets && setMarkets(d.markets)).catch(() => {})
  }, [])

  const requestRun = () => setRunKey((k) => k + 1)
  const goAnalyze = (m, s) => {
    setMarket(m); setSymbol(s); setTab('research'); setResearchDomain('market'); setMarketView('analyze'); setRunKey((k) => k + 1)
  }
  const goPortfolio = (view = 'holdings') => {
    setPortfolioView(view)
    setTab('portfolio')
  }
  const goFunds = () => { setTab('research'); setResearchDomain('funds') }
  const goMarket = () => { setTab('research'); setResearchDomain('market'); setMarketView('radar') }
  const goAgent = () => setTab('agent')

  return (
    <>
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <div className="mark" aria-hidden="true"><TrendingUp size={18} strokeWidth={2.5} /></div>
            <div>
              <h1>金融投资助手</h1>
              <div className="sub">真实数据驱动的个人投资决策工作台</div>
            </div>
          </div>
          <nav className="tabs" aria-label="主导航">
            {TABS.map((t) => {
              const Icon = t.icon
              return (
                <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`}
                  onClick={() => setTab(t.id)}>
                  <Icon size={16} strokeWidth={2} aria-hidden="true" />
                  <span>{t.label}</span>
                </button>
              )
            })}
          </nav>
          <div className="header-status" aria-label="数据来源说明">
            <span className="header-status-dot" aria-hidden="true" />
            <span>真实数据</span>
          </div>
        </div>
      </header>

      <main className="container">
        <div className="risk-disclosure" role="note">
          <Info size={15} strokeWidth={2.2} aria-hidden="true" />
          <span>风险提示：市场与基金事实来自已标注数据源；AI 仅解释 Evidence，不代表未来涨跌，也不构成投资建议。</span>
        </div>

        <Suspense fallback={<div className="page-loading"><span className="spinner" />正在加载工作区</div>}>
          {tab === 'overview' && <DashboardTab goPortfolio={goPortfolio} goFunds={goFunds} goMarket={goMarket} goAgent={goAgent} />}
          {tab === 'agent' && <AgentTab />}
          {tab === 'portfolio' && <PortfolioTab goAnalyze={goAnalyze} activeView={portfolioView} onViewChange={setPortfolioView} />}
          {tab === 'research' && <ResearchTab
            domain={researchDomain} onDomainChange={setResearchDomain}
            marketView={marketView} setMarketView={setMarketView} markets={markets}
            market={market} setMarket={setMarket} symbol={symbol} setSymbol={setSymbol}
            months={months} setMonths={setMonths} runKey={runKey} requestRun={requestRun} goAnalyze={goAnalyze}
          />}
        </Suspense>
      </main>
    </>
  )
}
