import { lazy, Suspense, useEffect, useState } from 'react'
import { Bot, BriefcaseBusiness, Info, LayoutDashboard, Search, Shield, Telescope, TrendingUp } from 'lucide-react'
import { fetchAuthSession, logoutAccount } from './api/auth'
import { fetchMarkets } from './api/market'
import { fetchDecisionTaskSummary } from './api/portfolio'
import AccountMenu from './components/AccountMenu'
import ChangePasswordScreen from './components/ChangePasswordScreen'
import LoginScreen from './components/LoginScreen'
import RegisterScreen from './components/RegisterScreen'

const AdminTab = lazy(() => import('./tabs/AdminTab'))
const AgentTab = lazy(() => import('./tabs/AgentTab'))
const DashboardTab = lazy(() => import('./tabs/DashboardTab'))
const PortfolioTab = lazy(() => import('./tabs/PortfolioTab'))
const ResearchTab = lazy(() => import('./tabs/ResearchTab'))
const OpportunityTab = lazy(() => import('./tabs/OpportunityTab'))

const BASE_TABS = [
  { id: 'overview', label: '今日决策', icon: LayoutDashboard },
  { id: 'portfolio', label: '我的资产', icon: BriefcaseBusiness },
  { id: 'research', label: '研究中心', icon: Search },
  { id: 'opportunities', label: '机会工厂', icon: Telescope },
  { id: 'agent', label: '投资 Agent', icon: Bot },
]

export default function App() {
  const [authLoading, setAuthLoading] = useState(true)
  const [user, setUser] = useState(null)
  const [authReadiness, setAuthReadiness] = useState(null)
  const [authView, setAuthView] = useState('login')
  const [loginPrefill, setLoginPrefill] = useState('')
  const [authNotice, setAuthNotice] = useState('')
  const [changePasswordOpen, setChangePasswordOpen] = useState(false)
  const [markets, setMarkets] = useState(['A股', '港股', '美股'])
  const [tab, setTab] = useState('overview')
  const [researchDomain, setResearchDomain] = useState('funds')
  const [marketView, setMarketView] = useState('radar')
  const [portfolioView, setPortfolioView] = useState('holdings')
  const [taskSummary, setTaskSummary] = useState(null)

  // 单股分析的共享状态(供扫描页点击跳转使用)
  const [market, setMarket] = useState('A股')
  const [symbol, setSymbol] = useState('')
  const [months, setMonths] = useState(12)
  const [runKey, setRunKey] = useState(0)

  useEffect(() => {
    let active = true
    fetchAuthSession()
      .then((result) => {
        if (!active) return
        setUser(result.authenticated ? result.user : null)
        setAuthReadiness(result.readiness || null)
      })
      .catch(() => { if (active) setUser(null) })
      .finally(() => { if (active) setAuthLoading(false) })
    const unauthorized = () => {
      setUser(null)
      setTaskSummary(null)
      setAuthView('login')
      setAuthNotice('')
      setTab('overview')
    }
    globalThis.addEventListener('stock-assistant:unauthorized', unauthorized)
    return () => {
      active = false
      globalThis.removeEventListener('stock-assistant:unauthorized', unauthorized)
    }
  }, [])

  useEffect(() => {
    if (!user) return
    fetchMarkets().then((d) => d.markets && setMarkets(d.markets)).catch(() => {})
  }, [user])

  useEffect(() => {
    if (!user || user.must_change_password) {
      setTaskSummary(null)
      return undefined
    }
    let active = true
    const refreshSummary = () => {
      fetchDecisionTaskSummary()
        .then((result) => { if (active) setTaskSummary(result) })
        .catch(() => {})
    }
    refreshSummary()
    const timer = globalThis.setInterval(refreshSummary, 60000)
    return () => {
      active = false
      globalThis.clearInterval(timer)
    }
  }, [user])

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

  async function logout() {
    try { await logoutAccount() } catch { /* session may already be invalid */ }
    setUser(null)
    setTaskSummary(null)
    setAuthView('login')
    setAuthNotice('')
    setTab('overview')
  }

  function authenticated(nextUser) {
    setUser(nextUser)
    setAuthView('login')
    setAuthNotice('')
  }

  function registered(username) {
    setLoginPrefill(username)
    setAuthNotice('注册成功，请使用账号和密码登录。')
    setAuthView('login')
  }

  if (authLoading) {
    return <main className="auth-shell"><div className="auth-loading"><span className="spinner" />正在验证会话</div></main>
  }
  if (!user) {
    const canRegister = Boolean(
      authReadiness?.ready && authReadiness?.self_registration_enabled,
    )
    if (authView === 'register' && canRegister) {
      return <RegisterScreen
        readiness={authReadiness}
        onRegistered={registered}
        onLogin={() => setAuthView('login')}
      />
    }
    return <LoginScreen
      readiness={authReadiness}
      initialUsername={loginPrefill}
      notice={authNotice}
      onAuthenticated={authenticated}
      onRegister={canRegister ? () => { setAuthNotice(''); setAuthView('register') } : null}
    />
  }
  if (user.must_change_password || changePasswordOpen) {
    return <ChangePasswordScreen
      forced={Boolean(user.must_change_password)}
      onCancel={() => setChangePasswordOpen(false)}
      onChanged={() => { setChangePasswordOpen(false); setUser(null) }}
    />
  }

  const tabs = user.role === 'admin'
    ? [...BASE_TABS, { id: 'admin', label: '系统管理', icon: Shield }]
    : BASE_TABS
  const openTaskCount = Math.max(0, Number(taskSummary?.open_count) || 0)

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
            {tabs.map((t) => {
              const Icon = t.icon
              return (
                <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`}
                  onClick={() => setTab(t.id)}>
                  <Icon size={16} strokeWidth={2} aria-hidden="true" />
                  <span>{t.label}</span>
                  {t.id === 'overview' && openTaskCount > 0 && (
                    <span className="tab-task-badge" aria-label={`${openTaskCount} 项待处理任务`}>
                      {openTaskCount > 99 ? '99+' : openTaskCount}
                    </span>
                  )}
                </button>
              )
            })}
          </nav>
          <div className="header-status" aria-label="数据来源说明">
            <span className="header-status-dot" aria-hidden="true" />
            <span>真实数据</span>
          </div>
          <AccountMenu
            user={user}
            onAdmin={() => setTab('admin')}
            onChangePassword={() => setChangePasswordOpen(true)}
            onLogout={logout}
          />
        </div>
      </header>

      <main className="container">
        <div className="risk-disclosure" role="note">
          <Info size={15} strokeWidth={2.2} aria-hidden="true" />
          <span>风险提示：市场与基金事实来自已标注数据源；AI 仅解释 Evidence，不代表未来涨跌，也不构成投资建议。</span>
        </div>

        <Suspense fallback={<div className="page-loading"><span className="spinner" />正在加载工作区</div>}>
          {tab === 'overview' && <DashboardTab goPortfolio={goPortfolio} goFunds={goFunds} goMarket={goMarket} goAgent={goAgent} onTaskSummaryChange={setTaskSummary} />}
          {tab === 'agent' && <AgentTab />}
          {tab === 'admin' && user.role === 'admin' && <AdminTab currentUser={user} />}
          {tab === 'portfolio' && <PortfolioTab goAnalyze={goAnalyze} activeView={portfolioView} onViewChange={setPortfolioView} />}
          {tab === 'research' && <ResearchTab
            domain={researchDomain} onDomainChange={setResearchDomain}
            marketView={marketView} setMarketView={setMarketView} markets={markets}
            market={market} setMarket={setMarket} symbol={symbol} setSymbol={setSymbol}
            months={months} setMonths={setMonths} runKey={runKey} requestRun={requestRun} goAnalyze={goAnalyze}
          />}
          {tab === 'opportunities' && <OpportunityTab goAnalyze={goAnalyze} />}
        </Suspense>
      </main>
    </>
  )
}
