import { lazy, Suspense } from 'react'
import { ChartNoAxesCombined, FlaskConical, Landmark } from 'lucide-react'
import WorkspaceHeader from '../components/WorkspaceHeader'

const FundTab = lazy(() => import('./FundTab'))
const MarketTab = lazy(() => import('./MarketTab'))
const BacktestTab = lazy(() => import('./BacktestTab'))

const DOMAINS = [
  { id: 'funds', label: '基金研究', description: '筛选、研究、比较与替代', icon: Landmark },
  { id: 'market', label: '股票与板块', description: '市场主线、板块和个股证据', icon: ChartNoAxesCombined },
  { id: 'tools', label: '策略验证', description: '用历史数据检验信号', icon: FlaskConical },
]

export default function ResearchTab({
  domain,
  onDomainChange,
  marketView,
  setMarketView,
  markets,
  market,
  setMarket,
  symbol,
  setSymbol,
  months,
  setMonths,
  runKey,
  requestRun,
  goAnalyze,
}) {
  return (
    <div className="research-workspace">
      <nav className="research-domain-nav" aria-label="研究中心分类">
        <div className="research-domain-copy">
          <span className="eyebrow">研究中心</span>
          <b>先选研究对象，再使用对应工具</b>
        </div>
        <div role="tablist">
          {DOMAINS.map((item) => {
            const Icon = item.icon
            return (
              <button type="button" role="tab" aria-selected={domain === item.id} className={domain === item.id ? 'active' : ''} onClick={() => onDomainChange(item.id)} key={item.id}>
                <Icon size={16} aria-hidden="true" />
                <span><b>{item.label}</b><small>{item.description}</small></span>
              </button>
            )
          })}
        </div>
      </nav>

      <Suspense fallback={<div className="page-loading"><span className="spinner" />正在加载研究工具</div>}>
        {domain === 'funds' && <FundTab />}
        {domain === 'market' && (
          <MarketTab
            activeView={marketView}
            setActiveView={setMarketView}
            markets={markets}
            market={market}
            setMarket={setMarket}
            symbol={symbol}
            setSymbol={setSymbol}
            months={months}
            setMonths={setMonths}
            runKey={runKey}
            requestRun={requestRun}
            goAnalyze={goAnalyze}
          />
        )}
        {domain === 'tools' && <><WorkspaceHeader eyebrow="研究中心" title="策略验证" description="使用真实历史数据检验既有信号，不把回测结果当作未来承诺。" /><BacktestTab markets={markets} /></>}
      </Suspense>
    </div>
  )
}
