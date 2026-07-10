import WorkspaceHeader from '../components/WorkspaceHeader'
import { useFundWorkspace } from '../features/funds/useFundWorkspace'
import FundCompareView from '../features/funds/FundCompareView'
import FundDiscoveryView from '../features/funds/FundDiscoveryView'
import FundWorkspaceControls from '../features/funds/FundWorkspaceControls'
import MetricCard from '../features/funds/FundMetricCard'
import { FundLineChart } from '../features/funds/FundCharts'
import { deltaClass, metricText, num, pct } from '../features/funds/fundFormatters'

const SORTS = [
  ['1y', '近1年'],
  ['ytd', '今年来'],
  ['6m', '近6月'],
  ['3m', '近3月'],
  ['1m', '近1月'],
]

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
  const assetLatest = factSheet?.asset_latest || {}
  const manager = factSheet?.managers?.[0]
  const flowSummary = factSheet?.flow_summary || {}
  const fundEvaluation = factSheet?.performance_evaluation || null
  const similarPercentile = factSheet?.similar_percentile || null
  const benchmarkComparison = factSheet?.benchmark_comparison || null
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

          {researchLayer === 'decision' && <>
          {fund.timing && (
            <div className="panel fade-in">
              <h3 className="section-title">
                买入节奏 <span className="hint">基于真实净值历史计算回撤分位、均线结构和滚动收益，不做模拟预测</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="节奏评分" value={fund.timing.score != null ? `${fund.timing.score} · ${fund.timing.label}` : fund.timing.label} />
                <MetricCard label="当前回撤" value={pct(fund.timing.zones?.current_drawdown)} cls="delta-neg" />
                <MetricCard label="回撤分位" value={pct(fund.timing.zones?.drawdown_percentile)} />
                <MetricCard label="阶段高点" value={fund.timing.zones?.high_nav != null ? `${num(fund.timing.zones.high_nav, 4)} · ${fund.timing.zones.high_date}` : '-'} />
                <MetricCard label="20日均值" value={fund.timing.zones?.ma20 != null ? num(fund.timing.zones.ma20, 4) : '-'} />
                <MetricCard label="60日均值" value={fund.timing.zones?.ma60 != null ? num(fund.timing.zones.ma60, 4) : '-'} />
              </div>
              <div className="fund-timing-grid">
                <div>
                  <h4 className="fund-subhead">当前判断</h4>
                  <p className="fund-timing-summary">{fund.timing.summary}</p>
                  <div className="fund-timing-actions">
                    {(fund.timing.actions || []).map((item) => (
                      <div className="fund-timing-action" key={item.title}>
                        <b>{item.title}</b>
                        <span>{item.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">净值位置</h4>
                  <div className="fund-zone-list">
                    <div><span>最新净值</span><b>{fund.timing.zones?.latest_nav != null ? num(fund.timing.zones.latest_nav, 4) : '-'}</b></div>
                    <div><span>接近高位线</span><b>{fund.timing.zones?.near_high_nav != null ? num(fund.timing.zones.near_high_nav, 4) : '-'}</b></div>
                    <div><span>普通回撤线</span><b>{fund.timing.zones?.normal_pullback_nav != null ? num(fund.timing.zones.normal_pullback_nav, 4) : '-'}</b></div>
                    <div><span>深度回撤线</span><b>{fund.timing.zones?.deep_pullback_nav != null ? num(fund.timing.zones.deep_pullback_nav, 4) : '-'}</b></div>
                  </div>
                  <p className="hint">这些阈值由真实阶段高点折算，用于控制买入节奏，不代表目标价。</p>
                </div>
              </div>
              {(fund.timing.signals || []).length > 0 && (
                <div className="fund-signal-grid">
                  {fund.timing.signals.map((s, idx) => (
                    <div className={`fund-signal ${s.level || 'neutral'}`} key={`${s.name}-${idx}`}>
                      <b>{s.name}</b>
                      <span>{s.text}</span>
                    </div>
                  ))}
                </div>
              )}
              {(fund.timing.rolling_returns || []).length > 0 && (
                <div className="corr-wrap" style={{ marginTop: 14 }}>
                  <table className="compact-table fund-timing-table">
                    <thead>
                      <tr>
                        <th>窗口</th>
                        <th>当前收益</th>
                        <th>历史分位</th>
                        <th>平均收益</th>
                        <th>正收益占比</th>
                        <th>样本</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fund.timing.rolling_returns.map((r) => (
                        <tr key={r.label}>
                          <td>{r.label}</td>
                          <td className={deltaClass(r.current_return)}>{pct(r.current_return)}</td>
                          <td>{pct(r.historical_percentile)}</td>
                          <td className={deltaClass(r.avg_return)}>{pct(r.avg_return)}</td>
                          <td>{pct(r.positive_ratio)}</td>
                          <td>{r.sample_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="hint" style={{ marginTop: 12 }}>{fund.timing.method}</p>
            </div>
          )}

          {fund.playbook && (
            <div className="panel fade-in fund-playbook-panel">
              <h3 className="section-title">
                投资经验手册 <span className="hint">把真实数据转成投前、买入、持有、退出的操作框架，不做收益承诺</span>
              </h3>
              <div className="fund-playbook-hero">
                <div>
                  <span className="tag neutral">{fund.playbook.role?.risk_band}</span>
                  <h4>{fund.playbook.role?.label}</h4>
                  <p>{fund.playbook.role?.reason}</p>
                  <div className="daily-tags">
                    {(fund.playbook.role?.risk_labels || []).map((x) => <span className="tag neutral" key={x}>{x}</span>)}
                    {(fund.playbook.role?.style_labels || []).map((x) => <span className="tag neutral" key={`style-${x}`}>{x}</span>)}
                  </div>
                </div>
                <div className="playbook-review-grid">
                  {(fund.playbook.review_metrics || []).slice(0, 8).map((m) => (
                    <div className="playbook-review" key={m.name}>
                      <span>{m.name}</span>
                      <b className={m.unit === '%' ? deltaClass(m.value) : ''}>{m.value == null ? '-' : `${num(m.value)}${m.unit || ''}`}</b>
                    </div>
                  ))}
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">仓位经验区间</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.position_ranges || []).map((row) => (
                      <div className="playbook-rule" key={row.investor}>
                        <b>{row.investor} · {row.range}</b>
                        <span>{row.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">建仓规则</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.entry_rules || []).map((row) => (
                      <div className="playbook-rule" key={row.level}>
                        <b>{row.level}</b>
                        <span>{row.rule}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">持有纪律</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.hold_rules || []).map((row) => (
                      <div className="playbook-rule" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">退出/降仓规则</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.exit_rules || []).map((row) => (
                      <div className="playbook-rule danger" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <h4 className="fund-subhead">情景预案</h4>
              <div className="corr-wrap">
                <table className="compact-table playbook-table">
                  <thead>
                    <tr>
                      <th>情景</th>
                      <th>观察什么</th>
                      <th>怎么处理</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(fund.playbook.scenario_plan || []).map((row) => (
                      <tr key={row.scenario}>
                        <td>{row.scenario}</td>
                        <td>{row.watch}</td>
                        <td>{row.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">执行步骤</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.execution_steps || []).map((row) => (
                      <div className="playbook-rule" key={row.step}>
                        <b>{row.step}</b>
                        <span>{row.action}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">经验提醒</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.experience_notes || []).map((row) => (
                      <div className="playbook-rule" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">红旗清单</h4>
                  <div className="fund-bond-list">
                    {(fund.playbook.red_flags || []).map((text) => <span className="tag neutral" key={text}>{text}</span>)}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">买前五问</h4>
                  <div className="playbook-rule-list">
                    {(fund.playbook.checklist || []).map((row) => (
                      <div className="playbook-rule" key={row.item}>
                        <b>{row.item}</b>
                        <span>{row.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <p className="hint" style={{ marginTop: 12 }}>{fund.playbook.disclaimer}</p>
            </div>
          )}
          </>}

          {researchLayer === 'evidence' && <>
          <div className="panel fade-in">
            <h3 className="section-title">
              同类定位 <span className="hint">在同类型基金排行中查看当前基金的位置</span>
            </h3>
            <div className="form-row" style={{ marginBottom: 14 }}>
              <div className="field">
                <label>同类排序</label>
                <select value={peerSort} onChange={(e) => {
                  setPeerSort(e.target.value)
                  loadPeers(fund.code, e.target.value)
                }}>
                  {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </div>
              <button className="ghost" onClick={() => loadPeers(fund.code, peerSort)} disabled={loadingPeers}>
                {loadingPeers ? <><span className="spinner" /> 定位中</> : '刷新同类定位'}
              </button>
            </div>
            {loadingPeers && !peers && <div className="placeholder"><div className="big">⌛</div>正在获取真实同类基金排行</div>}
            {peers?.error && <div className="error">{peers.error}</div>}
            {peers && !peers.error && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="同类类型" value={peers.category_name || '-'} />
                  <MetricCard label="同类排名" value={peers.rank ? `${peers.rank}/${peers.sample_count}` : `未进前${peers.sample_count}`} />
                  <MetricCard label="击败同类" value={peers.beat_ratio != null ? pct(peers.beat_ratio) : '-'} />
                  <MetricCard label="位置判断" value={peers.position_label} />
                </div>
                <div className="fund-peer-grid">
                  <div>
                    <h4 className="fund-subhead">同类前十 <span className="hint">{peers.as_of}</span></h4>
                    <div className="corr-wrap">
                      <table className="compact-table fund-peer-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            <th>代码</th>
                            <th>名称</th>
                            <th>近1年</th>
                            <th>近3月</th>
                          </tr>
                        </thead>
                        <tbody>
                          {peers.leaders.slice(0, 10).map((r) => (
                            <tr key={`leader-${r.code}`} className="clickable" onClick={() => loadFund(r.code, months)}>
                              <td>{r.rank}</td>
                              <td style={{ fontWeight: 800 }}>{r.code}</td>
                              <td>{r.name}</td>
                              <td className={deltaClass(r.return_1y)}>{pct(r.return_1y)}</td>
                              <td className={deltaClass(r.return_3m)}>{pct(r.return_3m)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">当前位置附近</h4>
                    {peers.neighbors?.length ? (
                      <div className="corr-wrap">
                        <table className="compact-table fund-peer-table">
                          <thead>
                            <tr>
                              <th>#</th>
                              <th>代码</th>
                              <th>名称</th>
                              <th>近1年</th>
                              <th>近3月</th>
                            </tr>
                          </thead>
                          <tbody>
                            {peers.neighbors.map((r) => (
                              <tr key={`neighbor-${r.code}`} className={`clickable ${r.code === fund.code ? 'row-active' : ''}`} onClick={() => loadFund(r.code, months)}>
                                <td>{r.rank}</td>
                                <td style={{ fontWeight: 800 }}>{r.code}</td>
                                <td>{r.name}</td>
                                <td className={deltaClass(r.return_1y)}>{pct(r.return_1y)}</td>
                                <td className={deltaClass(r.return_3m)}>{pct(r.return_3m)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="placeholder">当前基金未进入本次同类样本榜单</div>
                    )}
                  </div>
                </div>
                <p className="hint" style={{ marginTop: 12 }}>{peers.method?.ranking} {peers.method?.limit_note}</p>
              </>
            )}
          </div>

          <div className="panel fade-in">
            <h3 className="section-title">
              基金替代品对比 <span className="hint">从同类真实榜单里筛候选，再读取真实净值横向比较收益、波动、回撤和买入节奏</span>
            </h3>
            <div className="form-row" style={{ marginBottom: 14 }}>
              <button onClick={() => loadAlternatives(fund.code, peerSort)} disabled={loadingAlternatives}>
                {loadingAlternatives ? <><span className="spinner" /> 查找中</> : '查找替代基金'}
              </button>
              <button className="ghost" onClick={() => {
                setCompareInput([fund.code, ...(alternatives?.alternatives || []).slice(0, 3).map((r) => r.code)].join(' '))
              }} disabled={!alternatives?.alternatives?.length}>
                加入多基金对比
              </button>
              {alternatives && <span className="hint">同类 {alternatives.selected?.category_name || '-'} · 排序 {alternatives.sort} · 截至 {alternatives.as_of || '-'}</span>}
            </div>
            {loadingAlternatives && !alternatives && <div className="placeholder"><div className="big">⌛</div>正在读取真实同类基金和净值指标</div>}
            {alternatives && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="当前基金" value={`${alternatives.selected.code} ${alternatives.selected.name || ''}`} />
                  <MetricCard label="当前同类排名" value={alternatives.selected.rank ? `${alternatives.selected.rank}/${alternatives.selected.sample_count}` : `未进前${alternatives.selected.sample_count}`} />
                  <MetricCard label="评分最高候选" value={`${alternatives.summary.best_score.code} · ${alternatives.summary.best_score.score}`} />
                  <MetricCard label="低波候选" value={`${alternatives.summary.lower_volatility.code} ${pct(alternatives.summary.lower_volatility.metrics.annual_volatility)}`} />
                  <MetricCard label="一年收益候选" value={`${alternatives.summary.better_1y.code} ${pct(alternatives.summary.better_1y.metrics.return_1y)}`} cls={deltaClass(alternatives.summary.better_1y.metrics.return_1y)} />
                  <MetricCard label="低回撤候选" value={`${alternatives.summary.shallower_drawdown.code} ${pct(alternatives.summary.shallower_drawdown.metrics.max_drawdown)}`} cls="delta-neg" />
                </div>
                <div className="corr-wrap">
                  <table className="compact-table fund-alternative-table">
                    <thead>
                      <tr>
                        <th>候选</th>
                        <th>评分</th>
                        <th>近3月</th>
                        <th>近1年</th>
                        <th>波动</th>
                        <th>最大回撤</th>
                        <th>相对优势</th>
                        <th>风险点</th>
                      </tr>
                    </thead>
                    <tbody>
                      {alternatives.alternatives.map((row) => (
                        <tr key={row.code} className="clickable" onClick={() => loadFund(row.code, months)}>
                          <td>
                            <b>{row.code}</b>
                            <span className="table-sub">{row.name}</span>
                          </td>
                          <td>{row.score} · {row.label}</td>
                          <td className={deltaClass(row.metrics.return_3m)}>{pct(row.metrics.return_3m)}</td>
                          <td className={deltaClass(row.metrics.return_1y)}>{pct(row.metrics.return_1y)}</td>
                          <td>{pct(row.metrics.annual_volatility)}</td>
                          <td className="delta-neg">{pct(row.metrics.max_drawdown)}</td>
                          <td>{row.advantages?.[0] || '-'}</td>
                          <td>{row.cautions?.[0] || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="fund-alt-card-grid">
                  {alternatives.alternatives.slice(0, 4).map((row) => (
                    <div className="fund-alt-card" key={`alt-card-${row.code}`}>
                      <h4>{row.code} {row.name}</h4>
                      <div className="daily-metrics">
                        <span>评分 {row.score}</span>
                        <span>{row.timing_label || '-'}</span>
                        <span>规模 {row.scale_yi != null ? `${num(row.scale_yi)}亿` : '-'}</span>
                      </div>
                      <div className="fund-bond-list">
                        {(row.advantages || []).slice(0, 3).map((text) => <span className="tag up" key={text}>{text}</span>)}
                        {(row.cautions || []).slice(0, 2).map((text) => <span className="tag neutral" key={text}>{text}</span>)}
                      </div>
                    </div>
                  ))}
                </div>
                {alternatives.failed?.length > 0 && (
                  <div className="error" style={{ marginTop: 12 }}>
                    {alternatives.failed.map((x) => `${x.code || x.name}: ${x.error}`).join('；')}
                  </div>
                )}
                <p className="hint" style={{ marginTop: 12 }}>{alternatives.method?.score} {alternatives.method?.note}</p>
              </>
            )}
          </div>

          {factSheet && (
            <div className="panel fade-in">
              <h3 className="section-title">
                基金档案 <span className="hint">{factSheet.source} · {assetLatest.date || factSheet.scale_latest?.date || fund.as_of}</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="股票占比" value={pct(assetLatest.stock_ratio)} />
                <MetricCard label="债券占比" value={pct(assetLatest.bond_ratio)} />
                <MetricCard label="现金占比" value={pct(assetLatest.cash_ratio)} />
                <MetricCard label="净资产" value={assetLatest.net_asset_yi != null ? `${num(assetLatest.net_asset_yi)}亿` : '-'} />
                <MetricCard label="当前费率" value={factSheet.fee?.current_rate != null ? `${num(factSheet.fee.current_rate)}%` : '-'} />
                <MetricCard label="原始费率" value={factSheet.fee?.source_rate != null ? `${num(factSheet.fee.source_rate)}%` : '-'} />
              </div>
              {manager && (
                <div className="fund-manager-card">
                  <div>
                    <div className="hint">现任基金经理</div>
                    <h4>{manager.name} <span className="tag neutral">{manager.label || '任期中性'}</span></h4>
                    <p>{manager.work_time} · 管理规模 {manager.fund_size} · 评分日期 {manager.score_date || '-'}</p>
                  </div>
                  <div className="fund-manager-metrics">
                    <span>评分 <b>{num(manager.score)}</b></span>
                    <span>星级 <b>{manager.star || '-'}</b></span>
                    <span>任期收益 <b className={deltaClass(manager.tenure_return)}>{pct(manager.tenure_return)}</b></span>
                    <span>超同类 <b className={deltaClass(manager.excess_vs_peer)}>{pct(manager.excess_vs_peer)}</b></span>
                    <span>超沪深300 <b className={deltaClass(manager.excess_vs_hs300)}>{pct(manager.excess_vs_hs300)}</b></span>
                  </div>
                </div>
              )}
              {manager?.score_breakdown?.length > 0 && (
                <div className="fund-manager-detail">
                  <div>
                    <h4 className="fund-subhead">能力评分</h4>
                    <div className="fund-manager-score-list">
                      {manager.score_breakdown.map((r) => (
                        <div className="fund-manager-score-row" key={r.label}>
                          <span>{r.label}</span>
                          <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(2, r.score || 0))}%` }} /></div>
                          <b>{num(r.score)}</b>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">任期表现对比</h4>
                    <div className="ind-grid manager-mini-grid">
                      <div className="ind"><div className="k">本基金</div><div className={`v ${deltaClass(manager.tenure_return)}`}>{pct(manager.tenure_return)}</div></div>
                      <div className="ind"><div className="k">同类平均</div><div className={`v ${deltaClass(manager.tenure_peer_avg)}`}>{pct(manager.tenure_peer_avg)}</div></div>
                      <div className="ind"><div className="k">沪深300</div><div className={`v ${deltaClass(manager.tenure_hs300)}`}>{pct(manager.tenure_hs300)}</div></div>
                    </div>
                    <div className="fund-bond-list" style={{ marginTop: 12 }}>
                      {(manager.strengths || []).map((r) => <span className="tag up" key={`s-${r.label}`}>强项 {r.label} {num(r.score)}</span>)}
                      {(manager.weaknesses || []).map((r) => <span className="tag neutral" key={`w-${r.label}`}>短板 {r.label} {num(r.score)}</span>)}
                    </div>
                  </div>
                </div>
              )}
              {(fundEvaluation?.scores?.length > 0 || benchmarkComparison?.series?.length > 0) && (
                <div className="fund-evaluation-panel">
                  <div className="fund-evaluation-head">
                    <div>
                      <h4>基金能力画像</h4>
                      <p className="hint">{fundEvaluation?.label || '暂无评分'} · 同类百分位 {similarPercentile?.latest != null ? num(similarPercentile.latest) : '-'}</p>
                    </div>
                    <div className="fund-flow-summary">
                      <span>综合评分 <b>{num(fundEvaluation?.avg_score)}</b></span>
                      <span>20日均值 <b>{num(similarPercentile?.avg_20)}</b></span>
                      <span>20日变化 <b className={deltaClass(similarPercentile?.change_20)}>{num(similarPercentile?.change_20)}</b></span>
                    </div>
                  </div>
                  <div className="fund-evaluation-grid">
                    <div>
                      <h4 className="fund-subhead">基金五项评分</h4>
                      <div className="fund-manager-score-list">
                        {(fundEvaluation?.scores || []).map((r) => (
                          <div className="fund-manager-score-row" key={`fund-${r.label}`}>
                            <span>{r.label}</span>
                            <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(2, r.score || 0))}%` }} /></div>
                            <b>{num(r.score)}</b>
                          </div>
                        ))}
                      </div>
                      <div className="fund-bond-list" style={{ marginTop: 12 }}>
                        {(fundEvaluation?.strengths || []).map((r) => <span className="tag up" key={`fs-${r.label}`}>强项 {r.label} {num(r.score)}</span>)}
                        {(fundEvaluation?.weaknesses || []).map((r) => <span className="tag neutral" key={`fw-${r.label}`}>短板 {r.label} {num(r.score)}</span>)}
                      </div>
                    </div>
                    <div>
                      <h4 className="fund-subhead">累计收益对比 <span className="hint">{benchmarkComparison?.as_of || ''}</span></h4>
                      {benchmarkComparison?.series?.length > 0 ? (
                        <div className="corr-wrap">
                          <table className="compact-table fund-benchmark-table">
                            <thead>
                              <tr>
                                <th>序列</th>
                                <th>区间</th>
                                <th>累计收益</th>
                                <th>本基金超额</th>
                              </tr>
                            </thead>
                            <tbody>
                              {benchmarkComparison.series.map((r, idx) => (
                                <tr key={`${r.name}-${idx}`}>
                                  <td>{r.name}</td>
                                  <td>{r.start_date} ~ {r.end_date}</td>
                                  <td className={deltaClass(r.latest_return)}>{pct(r.latest_return)}</td>
                                  <td className={deltaClass(r.fund_excess)}>{idx === 0 ? '-' : pct(r.fund_excess)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="placeholder">暂无累计收益对比数据</div>
                      )}
                      <p className="hint">{similarPercentile?.label || ''}。{similarPercentile?.method || ''}</p>
                    </div>
                  </div>
                </div>
              )}
              {factSheet.flow_rows?.length > 0 && (
                <div className="fund-flow-panel">
                  <div className="fund-flow-head">
                    <div>
                      <h4>规模与申赎压力</h4>
                      <p className="hint">{flowSummary.latest_date} · {flowSummary.pressure || '申赎状态待观察'}</p>
                    </div>
                    <div className="fund-flow-summary">
                      <span>最新净申赎 <b className={deltaClass(flowSummary.latest_net_subscribe_yi)}>{flowSummary.latest_net_subscribe_yi != null ? `${num(flowSummary.latest_net_subscribe_yi)}亿` : '-'}</b></span>
                      <span>近几期合计 <b className={deltaClass(flowSummary.total_net_subscribe_yi)}>{flowSummary.total_net_subscribe_yi != null ? `${num(flowSummary.total_net_subscribe_yi)}亿` : '-'}</b></span>
                      <span>总份额 <b>{flowSummary.latest_total_share_yi != null ? `${num(flowSummary.latest_total_share_yi)}亿份` : '-'}</b></span>
                    </div>
                  </div>
                  <div className="fund-flow-grid">
                    <div>
                      <h4 className="fund-subhead">申购/赎回</h4>
                      <div className="fund-flow-bars">
                        {factSheet.flow_rows.map((r) => {
                          const maxAbs = Math.max(1, ...factSheet.flow_rows.map((x) => Math.abs(x.net_subscribe_yi || 0)))
                          const width = Math.min(100, Math.abs(r.net_subscribe_yi || 0) / maxAbs * 100)
                          return (
                            <div className="fund-flow-row" key={r.date}>
                              <span>{r.date}</span>
                              <div className={`fund-flow-track ${r.net_subscribe_yi >= 0 ? 'in' : 'out'}`}>
                                <i style={{ width: `${width}%` }} />
                              </div>
                              <b className={deltaClass(r.net_subscribe_yi)}>{r.net_subscribe_yi != null ? `${num(r.net_subscribe_yi)}亿` : '-'}</b>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    <div>
                      <h4 className="fund-subhead">规模变化</h4>
                      <div className="fund-bar-list">
                        {(factSheet.scale_rows || []).map((r) => {
                          const maxScale = Math.max(1, ...(factSheet.scale_rows || []).map((x) => x.scale_yi || 0))
                          return (
                            <div className="fund-bar-row" key={r.date}>
                              <div className="fund-bar-label">{r.date}</div>
                              <div className="fund-bar-track"><i style={{ width: `${Math.min(100, (r.scale_yi || 0) / maxScale * 100)}%` }} /></div>
                              <div className="fund-bar-value">{r.scale_yi != null ? `${num(r.scale_yi)}亿` : '-'}</div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              <p className="hint" style={{ marginTop: 12 }}>基金档案来自东方财富基金详情页，资产配置和规模以基金披露日期为准。</p>
            </div>
          )}

          <div className="panel fade-in">
            <h3 className="section-title">
              分红送配 <span className="hint">现金分配记录、拆分折算和累计分红画像</span>
            </h3>
            {loadingDividends && !dividends && <div className="placeholder"><div className="big">⌛</div>正在读取真实分红记录</div>}
            {dividends?.error && <div className="error">{dividends.error}</div>}
            {dividends && !dividends.error && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="分红特征" value={dividends.summary.label} />
                  <MetricCard label="分红次数" value={dividends.summary.dividend_count} />
                  <MetricCard label="累计每份分红" value={dividends.summary.total_cash_per_share != null ? `${num(dividends.summary.total_cash_per_share, 4)}元` : '-'} />
                  <MetricCard label="拆分次数" value={dividends.summary.split_count} />
                </div>
                <p className="hint" style={{ marginTop: -4 }}>{dividends.summary.note}</p>
                {dividends.dividends.length > 0 ? (
                  <div className="corr-wrap">
                    <table className="compact-table fund-dividend-table">
                      <thead>
                        <tr>
                          <th>年份</th>
                          <th>权益登记日</th>
                          <th>除息日</th>
                          <th>每份分红</th>
                          <th>发放日</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dividends.dividends.slice(0, 12).map((r, idx) => (
                          <tr key={`${r.ex_dividend_date}-${idx}`}>
                            <td>{r.year}</td>
                            <td>{r.record_date}</td>
                            <td>{r.ex_dividend_date}</td>
                            <td>{r.cash_per_share != null ? `${num(r.cash_per_share, 4)}元` : r.cash_text}</td>
                            <td>{r.payment_date}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="placeholder">该基金分红页面暂无分红信息</div>
                )}
                {dividends.splits.length > 0 && (
                  <div className="fund-bond-list" style={{ marginTop: 12 }}>
                    {dividends.splits.slice(0, 6).map((r, idx) => (
                      <span className="tag neutral" key={`${r.date}-${idx}`}>{r.date} {r.type} {r.ratio}</span>
                    ))}
                  </div>
                )}
                <p className="hint" style={{ marginTop: 12 }}>{dividends.method.note} 数据源: {dividends.source}。</p>
              </>
            )}
          </div>

          <div className="panel fade-in">
            <h3 className="section-title">风险与持有体验</h3>
            <div className="ind-grid">
              <div className="ind"><div className="k">当前回撤</div><div className="v delta-neg">{pct(fund.metrics.current_drawdown)}</div></div>
              <div className="ind"><div className="k">年化波动</div><div className="v">{pct(fund.metrics.annual_volatility)}</div></div>
              <div className="ind"><div className="k">日胜率</div><div className="v">{pct(fund.metrics.win_rate)}</div></div>
              <div className="ind"><div className="k">月度胜率</div><div className="v">{pct(fund.metrics.positive_month_ratio)}</div></div>
              <div className="ind"><div className="k">最差单日</div><div className="v delta-neg">{pct(fund.metrics.worst_day)}</div></div>
            </div>
          </div>

          {fund.drawdown_recovery && (
            <div className="panel fade-in">
              <h3 className="section-title">
                回撤修复画像 <span className="hint">从真实历史净值统计创新高、回撤深度和修复耗时</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="修复特征" value={fund.drawdown_recovery.label} />
                <MetricCard label="最近新高" value={fund.drawdown_recovery.latest_high_date || '-'} />
                <MetricCard label="离新高天数" value={fund.drawdown_recovery.days_since_high != null ? `${fund.drawdown_recovery.days_since_high}天` : '-'} />
                <MetricCard label="历史回撤段" value={fund.drawdown_recovery.episode_count} />
                <MetricCard label="已修复比例" value={pct(fund.drawdown_recovery.recovery_rate)} />
                <MetricCard label="平均修复" value={fund.drawdown_recovery.avg_recovery_days != null ? `${num(fund.drawdown_recovery.avg_recovery_days, 0)}天` : '-'} />
              </div>
              <div className="fund-recovery-grid">
                <div>
                  <h4 className="fund-subhead">回撤分布</h4>
                  <div className="fund-bond-list">
                    <span className="tag neutral">超过5%: {fund.drawdown_recovery.deep_drawdown_count_5}次</span>
                    <span className="tag neutral">超过10%: {fund.drawdown_recovery.deep_drawdown_count_10}次</span>
                    <span className="tag neutral">超过20%: {fund.drawdown_recovery.deep_drawdown_count_20}次</span>
                    <span className="tag neutral">最长修复: {fund.drawdown_recovery.max_recovery_days != null ? `${fund.drawdown_recovery.max_recovery_days}天` : '-'}</span>
                  </div>
                  <p className="hint">当前仍在回撤时，开放回撤天数为 {fund.drawdown_recovery.open_drawdown_days != null ? `${fund.drawdown_recovery.open_drawdown_days}天` : '-'}，当前开放回撤深度 {pct(fund.drawdown_recovery.open_drawdown_depth)}。</p>
                </div>
                <div>
                  <h4 className="fund-subhead">最深回撤区间</h4>
                  {fund.drawdown_recovery.episodes?.length ? (
                    <div className="corr-wrap">
                      <table className="compact-table fund-recovery-table">
                        <thead>
                          <tr>
                            <th>高点</th>
                            <th>低点</th>
                            <th>深度</th>
                            <th>修复日</th>
                            <th>修复耗时</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fund.drawdown_recovery.episodes.map((r, idx) => (
                            <tr key={`${r.peak_date}-${r.trough_date}-${idx}`}>
                              <td>{r.peak_date}</td>
                              <td>{r.trough_date}</td>
                              <td className="delta-neg">{pct(r.depth)}</td>
                              <td>{r.recovered ? r.recovery_date : '未修复'}</td>
                              <td>{r.recovery_days != null ? `${r.recovery_days}天` : '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="placeholder">当前净值窗口内暂未形成有效回撤区间</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {fund.calendar_returns && (
            <div className="panel fade-in">
              <h3 className="section-title">
                收益日历 <span className="hint">自然年、最近月份和月份胜率，均由真实单位净值计算</span>
              </h3>
              <div className="bt-cards quality-cards">
                <MetricCard label="年度胜率" value={pct(fund.calendar_returns.summary?.positive_year_ratio)} />
                <MetricCard label="上涨年份" value={fund.calendar_returns.summary?.positive_years ?? '-'} />
                <MetricCard label="下跌年份" value={fund.calendar_returns.summary?.negative_years ?? '-'} />
                <MetricCard label="最好年份" value={fund.calendar_returns.summary?.best_year ? `${fund.calendar_returns.summary.best_year.year} ${pct(fund.calendar_returns.summary.best_year.return)}` : '-'} cls={deltaClass(fund.calendar_returns.summary?.best_year?.return)} />
                <MetricCard label="最差年份" value={fund.calendar_returns.summary?.worst_year ? `${fund.calendar_returns.summary.worst_year.year} ${pct(fund.calendar_returns.summary.worst_year.return)}` : '-'} cls="delta-neg" />
                <MetricCard label="最好月份" value={fund.calendar_returns.summary?.best_month ? `${fund.calendar_returns.summary.best_month.month} ${pct(fund.calendar_returns.summary.best_month.return)}` : '-'} cls={deltaClass(fund.calendar_returns.summary?.best_month?.return)} />
              </div>
              <div className="fund-calendar-grid">
                <div>
                  <h4 className="fund-subhead">年度收益</h4>
                  <div className="corr-wrap">
                    <table className="compact-table fund-calendar-table">
                      <thead>
                        <tr>
                          <th>年份</th>
                          <th>起始日</th>
                          <th>结束日</th>
                          <th>收益</th>
                          <th>样本</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fund.calendar_returns.years.map((r) => (
                          <tr key={r.year}>
                            <td>{r.year}</td>
                            <td>{r.start_date}</td>
                            <td>{r.end_date}</td>
                            <td className={deltaClass(r.return)}>{pct(r.return)}</td>
                            <td>{r.sample_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">月份统计</h4>
                  <div className="fund-bar-list">
                    {fund.calendar_returns.month_stats.map((r) => (
                      <div className="fund-bar-row" key={r.month}>
                        <div className="fund-bar-label">{r.month}月</div>
                        <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(4, Math.abs(r.avg_return || 0) * 4))}%` }} /></div>
                        <div className={`fund-bar-value ${deltaClass(r.avg_return)}`}>{pct(r.avg_return)} · 胜率 {pct(r.win_rate)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <h4 className="fund-subhead" style={{ marginTop: 16 }}>最近月份</h4>
              <div className="corr-wrap">
                <table className="compact-table fund-calendar-table">
                  <thead>
                    <tr>
                      <th>月份</th>
                      <th>起始日</th>
                      <th>结束日</th>
                      <th>收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fund.calendar_returns.recent_months.map((r) => (
                      <tr key={r.month}>
                        <td>{r.month}</td>
                        <td>{r.start_date}</td>
                        <td>{r.end_date}</td>
                        <td className={deltaClass(r.return)}>{pct(r.return)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="panel fade-in">
            <h3 className="section-title">
              持仓画像 <span className="hint">
                {loadingPortfolio ? '正在读取基金定期报告披露持仓' : portfolio ? `${portfolio.year} · 股票 ${portfolio.summary.stock_count} 只 · 行业 ${portfolio.summary.industry_count} 个` : '基金定期报告披露数据'}
              </span>
            </h3>
            {loadingPortfolio && <div className="placeholder"><div className="big">⌛</div>正在获取真实持仓数据</div>}
            {portfolioError && <div className="error">{portfolioError}</div>}
            {portfolio && (
              <>
                <div className="bt-cards quality-cards">
                  <MetricCard label="前3大重仓" value={pct(portfolio.summary.top3_stock_ratio)} />
                  <MetricCard label="前10大重仓" value={pct(portfolio.summary.top10_stock_ratio)} />
                  <MetricCard label="集中度" value={portfolio.summary.concentration} />
                  <MetricCard label="风格提示" value={portfolio.summary.style_note} />
                </div>
                <div className="fund-holding-grid">
                  <div>
                    <h4 className="fund-subhead">重仓股票 <span className="hint">{portfolio.stock_period}</span></h4>
                    <div className="corr-wrap">
                      <table className="compact-table fund-holding-table">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>名称</th>
                            <th>占净值</th>
                            <th>持仓市值(万)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {portfolio.stocks.slice(0, 10).map((r) => (
                            <tr key={`${r.code}-${r.name}`}>
                              <td style={{ fontWeight: 800 }}>{r.code}</td>
                              <td>{r.name}</td>
                              <td>{pct(r.nav_ratio)}</td>
                              <td>{num(r.market_value_wan)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    <h4 className="fund-subhead">行业配置 <span className="hint">{portfolio.industry_period}</span></h4>
                    <div className="fund-bar-list">
                      {portfolio.industries.slice(0, 8).map((r) => (
                        <div className="fund-bar-row" key={r.name}>
                          <div className="fund-bar-label">{r.name}</div>
                          <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(1, r.nav_ratio || 0))}%` }} /></div>
                          <div className="fund-bar-value">{pct(r.nav_ratio)}</div>
                        </div>
                      ))}
                    </div>
                    {portfolio.bonds.length > 0 && (
                      <>
                        <h4 className="fund-subhead">债券持仓 <span className="hint">{portfolio.bond_period}</span></h4>
                        <div className="fund-bond-list">
                          {portfolio.bonds.slice(0, 5).map((r) => (
                            <span className="tag neutral" key={`${r.code}-${r.name}`}>{r.name} {pct(r.nav_ratio)}</span>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                </div>
                <p className="hint" style={{ marginTop: 12 }}>{portfolio.method.note} 数据源: {portfolio.source}。</p>
              </>
            )}
          </div>
          </>}

          <div className="panel fade-in">
            <h3 className="section-title">投资分析</h3>
            <div className="fund-insight-grid">
              {fund.insights.map((item) => (
                <div className="fund-insight" key={item.title}>
                  <h4>{item.title}</h4>
                  <p>{item.text}</p>
                </div>
              ))}
            </div>
            <p className="hint" style={{ marginTop: 12 }}>
              {fund.method.note} 数据源: {fund.source}。申购状态: {fund.latest.subscribe_status || '-'}；赎回状态: {fund.latest.redeem_status || '-'}。
            </p>
          </div>
        </>
      )}
    </>
  )
}
