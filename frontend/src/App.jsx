import { lazy, Suspense, useEffect, useState } from 'react'
import { BriefcaseBusiness, ChartNoAxesCombined, FlaskConical, Info, Landmark, LayoutDashboard, TrendingUp } from 'lucide-react'
import { fetchMarkets } from './api/market'

const BacktestTab = lazy(() => import('./tabs/BacktestTab'))
const FundTab = lazy(() => import('./tabs/FundTab'))
const DashboardTab = lazy(() => import('./tabs/DashboardTab'))
const MarketTab = lazy(() => import('./tabs/MarketTab'))
const PortfolioTab = lazy(() => import('./tabs/PortfolioTab'))

const TABS = [
  { id: 'overview', label: '投资总览', icon: LayoutDashboard },
  { id: 'funds', label: '基金中心', icon: Landmark },
  { id: 'market', label: '股票与板块', icon: ChartNoAxesCombined },
  { id: 'portfolio', label: '我的组合', icon: BriefcaseBusiness },
  { id: 'tools', label: '研究工具', icon: FlaskConical },
]

export default function App() {
  const [markets, setMarkets] = useState(['A股', '港股', '美股'])
  const [tab, setTab] = useState('overview')
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
    setMarket(m); setSymbol(s); setTab('market'); setMarketView('analyze'); setRunKey((k) => k + 1)
  }
  const goPortfolio = (view = 'holdings') => {
    setPortfolioView(view)
    setTab('portfolio')
  }
  const goFunds = () => setTab('funds')
  const goMarket = () => { setTab('market'); setMarketView('radar') }

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
          <span>风险提示：所有结论均来自已标注的数据源和历史计算，不代表未来涨跌，也不构成投资建议。</span>
        </div>

        <Suspense fallback={<div className="page-loading"><span className="spinner" />正在加载工作区</div>}>
          {tab === 'overview' && <DashboardTab goPortfolio={goPortfolio} goFunds={goFunds} goMarket={goMarket} />}
          {tab === 'funds' && <FundTab />}
          {tab === 'market' && <MarketTab activeView={marketView} setActiveView={setMarketView} markets={markets}
            market={market} setMarket={setMarket} symbol={symbol} setSymbol={setSymbol}
            months={months} setMonths={setMonths} runKey={runKey} requestRun={requestRun} goAnalyze={goAnalyze} />}
          {tab === 'portfolio' && <PortfolioTab goAnalyze={goAnalyze} activeView={portfolioView} onViewChange={setPortfolioView} />}
          {tab === 'tools' && <BacktestTab markets={markets} />}
        </Suspense>
      </main>
    </>
  )
}
