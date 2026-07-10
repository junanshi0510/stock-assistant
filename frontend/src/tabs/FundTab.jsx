import WorkspaceHeader from '../components/WorkspaceHeader'
import { useFundWorkspace } from '../features/funds/useFundWorkspace'
import FundCompareView from '../features/funds/FundCompareView'
import FundDecisionView from '../features/funds/FundDecisionView'
import FundDividendView from '../features/funds/FundDividendView'
import FundDiscoveryView from '../features/funds/FundDiscoveryView'
import FundFactSheetView from '../features/funds/FundFactSheetView'
import FundInsightsView from '../features/funds/FundInsightsView'
import FundPeerEvidenceView from '../features/funds/FundPeerEvidenceView'
import FundPortfolioEvidenceView from '../features/funds/FundPortfolioEvidenceView'
import FundRiskEvidenceView from '../features/funds/FundRiskEvidenceView'
import FundWorkspaceControls from '../features/funds/FundWorkspaceControls'
import MetricCard from '../features/funds/FundMetricCard'
import { FundLineChart } from '../features/funds/FundCharts'
import { deltaClass, num, pct } from '../features/funds/fundFormatters'

const FUND_VIEWS = [
  { id: 'discover', label: '发现基金', description: '从真实榜单和分类热度中建立候选池' },
  { id: 'research', label: '研究基金', description: '将单只基金的数据转化为可复盘的决策框架' },
  { id: 'compare', label: '比较与替换', description: '比较多只基金的风险、相关性与重复暴露' },
]

export default function FundTab() {
  const {
    fundView, setFundView, researchLayer, setResearchLayer,
    category, setCategory, sort, setSort, limit, setLimit, months, setMonths, code, setCode,
    hot, categories, categoryError, fund, portfolio, portfolioError, peers, peerSort, setPeerSort,
    dividends, searchKeyword, setSearchKeyword, searchResults, compareInput, setCompareInput,
    compareData, overlapData, opportunityRisk, setOpportunityRisk, opportunities, alternatives,
    loadingHot, loadingFund, loadingPortfolio, loadingPeers, loadingDividends, loadingSearch,
    loadingCompare, loadingOverlap, loadingOpportunities, loadingAlternatives, error,
    loadHot, loadFund, loadPeers, loadAlternatives, loadOpportunities, runSearch, runCompare, runOverlap,
  } = useFundWorkspace()

  const rows = hot?.items || []
  const selectedName = fund?.name || rows.find((r) => r.code === code)?.name || ''
  const categoryHeat = categories || []
  const factSheet = fund?.fact_sheet || null
  const currentView = FUND_VIEWS.find((item) => item.id === fundView) || FUND_VIEWS[0]

  return (
    <>
      <WorkspaceHeader
        eyebrow="基金中心"
        title={currentView.label}
        description={`${currentView.description}。所有排序、净值和持仓披露均标注真实来源。`}
        views={FUND_VIEWS}
        activeView={fundView}
        onViewChange={setFundView}
        ariaLabel="基金中心功能"
      />

      <FundWorkspaceControls
        fundView={fundView}
        category={category}
        setCategory={setCategory}
        sort={sort}
        setSort={setSort}
        limit={limit}
        setLimit={setLimit}
        loadHot={loadHot}
        loadingHot={loadingHot}
        code={code}
        setCode={setCode}
        months={months}
        setMonths={setMonths}
        loadFund={loadFund}
        loadingFund={loadingFund}
        searchKeyword={searchKeyword}
        setSearchKeyword={setSearchKeyword}
        runSearch={runSearch}
        loadingSearch={loadingSearch}
        searchResults={searchResults}
        hot={hot}
        error={error}
      />

      {fundView === 'discover' && (
        <FundDiscoveryView
          opportunityRisk={opportunityRisk}
          setOpportunityRisk={setOpportunityRisk}
          loadOpportunities={loadOpportunities}
          loadingOpportunities={loadingOpportunities}
          opportunities={opportunities}
          categoryHeat={categoryHeat}
          category={category}
          setCategory={setCategory}
          sort={sort}
          loadHot={loadHot}
          categoryError={categoryError}
          rows={rows}
          hot={hot}
          code={code}
          months={months}
          loadFund={loadFund}
        />
      )}

      {fundView === 'compare' && (
        <FundCompareView
          compareInput={compareInput}
          setCompareInput={setCompareInput}
          runCompare={runCompare}
          loadingCompare={loadingCompare}
          runOverlap={runOverlap}
          loadingOverlap={loadingOverlap}
          compareData={compareData}
          overlapData={overlapData}
          loadFund={loadFund}
          months={months}
        />
      )}

      {fundView === 'research' && fund && (
        <>
          <div className="panel fade-in">
            <h3 className="section-title">
              {fund.code} {selectedName} <span className="hint">{fund.trend_state} · 截至 {fund.as_of} · 样本 {fund.sample_count} 条</span>
            </h3>
            <div className="bt-cards quality-cards">
              <MetricCard label="最新单位净值" value={num(fund.latest.unit_nav, 4)} />
              <MetricCard label="近1月" value={pct(fund.metrics.return_1m)} cls={deltaClass(fund.metrics.return_1m)} />
              <MetricCard label="近3月" value={pct(fund.metrics.return_3m)} cls={deltaClass(fund.metrics.return_3m)} />
              <MetricCard label="近1年" value={pct(fund.metrics.return_1y)} cls={deltaClass(fund.metrics.return_1y)} />
              <MetricCard label="最大回撤" value={pct(fund.metrics.max_drawdown)} cls="delta-neg" />
              <MetricCard label="定投适配" value={`${fund.metrics.dca_score} · ${fund.metrics.dca_label}`} />
            </div>
            <FundLineChart data={fund.nav} />
          </div>

          <div className="research-layer-nav" role="tablist" aria-label="基金研究层级">
            <button className={researchLayer === 'decision' ? 'active' : ''} onClick={() => setResearchLayer('decision')}>投资决策</button>
            <button className={researchLayer === 'evidence' ? 'active' : ''} onClick={() => setResearchLayer('evidence')}>数据证据</button>
          </div>

          {researchLayer === 'decision' && <FundDecisionView fund={fund} />}

          {researchLayer === 'evidence' && <>
          <FundPeerEvidenceView
            fund={fund}
            months={months}
            peers={peers}
            peerSort={peerSort}
            setPeerSort={setPeerSort}
            loadPeers={loadPeers}
            loadingPeers={loadingPeers}
            loadFund={loadFund}
            alternatives={alternatives}
            loadAlternatives={loadAlternatives}
            loadingAlternatives={loadingAlternatives}
            setCompareInput={setCompareInput}
          />

          <FundFactSheetView factSheet={factSheet} asOf={fund.as_of} />

          <FundDividendView dividends={dividends} loadingDividends={loadingDividends} />

          <FundRiskEvidenceView fund={fund} />

          <FundPortfolioEvidenceView
            portfolio={portfolio}
            portfolioError={portfolioError}
            loadingPortfolio={loadingPortfolio}
          />
          </>}

          <FundInsightsView fund={fund} />
        </>
      )}
    </>
  )
}
