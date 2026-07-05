import { useEffect, useState } from 'react'
import { fetchMarkets } from './api'
import AnalyzeTab from './tabs/AnalyzeTab'
import ScanTab from './tabs/ScanTab'
import BacktestTab from './tabs/BacktestTab'
import WatchlistTab from './tabs/WatchlistTab'
import DiscoverTab from './tabs/DiscoverTab'
import MultiCompareTab from './tabs/MultiCompareTab'
import SectorTab from './tabs/SectorTab'
import FundTab from './tabs/FundTab'
import HoldingsTab from './tabs/HoldingsTab'

const TABS = [
  { id: 'analyze', label: '单股分析' },
  { id: 'watchlist', label: '⭐ 自选' },
  { id: 'holdings', label: '我的持仓' },
  { id: 'discover', label: '🔍 发现' },
  { id: 'sectors', label: '板块热点' },
  { id: 'funds', label: '基金分析' },
  { id: 'multi', label: '多股对比' },
  { id: 'scan', label: '批量扫描' },
  { id: 'backtest', label: '信号回测' },
]

export default function App() {
  const [markets, setMarkets] = useState(['A股', '港股', '美股'])
  const [tab, setTab] = useState('analyze')

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
    setMarket(m); setSymbol(s); setTab('analyze'); setRunKey((k) => k + 1)
  }

  return (
    <>
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <div className="mark">📈</div>
            <div>
              <h1>金融投资助手</h1>
              <div className="sub">A股 · 港股 · 美股　|　多因子量化信号</div>
            </div>
          </div>
          <div className="tabs">
            {TABS.map((t) => (
              <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`}
                onClick={() => setTab(t.id)}>{t.label}</button>
            ))}
          </div>
        </div>
      </header>

      <div className="container">
        <div className="warning">
          ⚠️ <b>风险提示</b>:本工具基于历史价格计算<b>量化信号与估计概率</b>,
          <b>不是涨跌预测,更不构成投资建议</b>。没有任何模型能准确预测股市;
          请用「信号回测」查看历史命中率,理性参考,盈亏自负。
        </div>

        {tab === 'analyze' && (
          <AnalyzeTab markets={markets}
            market={market} setMarket={setMarket}
            symbol={symbol} setSymbol={setSymbol}
            months={months} setMonths={setMonths}
            runKey={runKey} requestRun={requestRun} />
        )}
        {tab === 'watchlist' && <WatchlistTab goAnalyze={goAnalyze} />}
        {tab === 'holdings' && <HoldingsTab />}
        {tab === 'discover' && <DiscoverTab markets={markets} goAnalyze={goAnalyze} />}
        {tab === 'sectors' && <SectorTab goAnalyze={goAnalyze} />}
        {tab === 'funds' && <FundTab />}
        {tab === 'multi' && <MultiCompareTab markets={markets} goAnalyze={goAnalyze} />}
        {tab === 'scan' && <ScanTab markets={markets} goAnalyze={goAnalyze} />}
        {tab === 'backtest' && <BacktestTab markets={markets} />}
      </div>
    </>
  )
}
