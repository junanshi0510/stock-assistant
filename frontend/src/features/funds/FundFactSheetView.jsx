import FundMetricCard from './FundMetricCard'
import { deltaClass, num, pct } from './fundFormatters'

/** Renders disclosures from the provider's fund detail page. */
export default function FundFactSheetView({ factSheet, asOf }) {
  if (!factSheet) return null

  const assetLatest = factSheet.asset_latest || {}
  const manager = factSheet.managers?.[0]
  const flowSummary = factSheet.flow_summary || {}
  const fundEvaluation = factSheet.performance_evaluation || null
  const similarPercentile = factSheet.similar_percentile || null
  const benchmarkComparison = factSheet.benchmark_comparison || null

  return (
    <div className="panel fade-in">
      <h3 className="section-title">
        基金档案 <span className="hint">{factSheet.source} · {assetLatest.date || factSheet.scale_latest?.date || asOf}</span>
      </h3>
      <div className="bt-cards quality-cards">
        <FundMetricCard label="股票占比" value={pct(assetLatest.stock_ratio)} />
        <FundMetricCard label="债券占比" value={pct(assetLatest.bond_ratio)} />
        <FundMetricCard label="现金占比" value={pct(assetLatest.cash_ratio)} />
        <FundMetricCard label="净资产" value={assetLatest.net_asset_yi != null ? `${num(assetLatest.net_asset_yi)}亿` : '-'} />
        <FundMetricCard label="当前费率" value={factSheet.fee?.current_rate != null ? `${num(factSheet.fee.current_rate)}%` : '-'} />
        <FundMetricCard label="原始费率" value={factSheet.fee?.source_rate != null ? `${num(factSheet.fee.source_rate)}%` : '-'} />
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
              {manager.score_breakdown.map((row) => (
                <div className="fund-manager-score-row" key={row.label}>
                  <span>{row.label}</span>
                  <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(2, row.score || 0))}%` }} /></div>
                  <b>{num(row.score)}</b>
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
              {(manager.strengths || []).map((row) => <span className="tag up" key={`s-${row.label}`}>强项 {row.label} {num(row.score)}</span>)}
              {(manager.weaknesses || []).map((row) => <span className="tag neutral" key={`w-${row.label}`}>短板 {row.label} {num(row.score)}</span>)}
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
                {(fundEvaluation?.scores || []).map((row) => (
                  <div className="fund-manager-score-row" key={`fund-${row.label}`}>
                    <span>{row.label}</span>
                    <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(2, row.score || 0))}%` }} /></div>
                    <b>{num(row.score)}</b>
                  </div>
                ))}
              </div>
              <div className="fund-bond-list" style={{ marginTop: 12 }}>
                {(fundEvaluation?.strengths || []).map((row) => <span className="tag up" key={`fs-${row.label}`}>强项 {row.label} {num(row.score)}</span>)}
                {(fundEvaluation?.weaknesses || []).map((row) => <span className="tag neutral" key={`fw-${row.label}`}>短板 {row.label} {num(row.score)}</span>)}
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
                      {benchmarkComparison.series.map((row, index) => (
                        <tr key={`${row.name}-${index}`}>
                          <td>{row.name}</td>
                          <td>{row.start_date} ~ {row.end_date}</td>
                          <td className={deltaClass(row.latest_return)}>{pct(row.latest_return)}</td>
                          <td className={deltaClass(row.fund_excess)}>{index === 0 ? '-' : pct(row.fund_excess)}</td>
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
                {factSheet.flow_rows.map((row) => {
                  const maxAbs = Math.max(1, ...factSheet.flow_rows.map((item) => Math.abs(item.net_subscribe_yi || 0)))
                  const width = Math.min(100, Math.abs(row.net_subscribe_yi || 0) / maxAbs * 100)
                  return (
                    <div className="fund-flow-row" key={row.date}>
                      <span>{row.date}</span>
                      <div className={`fund-flow-track ${row.net_subscribe_yi >= 0 ? 'in' : 'out'}`}>
                        <i style={{ width: `${width}%` }} />
                      </div>
                      <b className={deltaClass(row.net_subscribe_yi)}>{row.net_subscribe_yi != null ? `${num(row.net_subscribe_yi)}亿` : '-'}</b>
                    </div>
                  )
                })}
              </div>
            </div>
            <div>
              <h4 className="fund-subhead">规模变化</h4>
              <div className="fund-bar-list">
                {(factSheet.scale_rows || []).map((row) => {
                  const maxScale = Math.max(1, ...(factSheet.scale_rows || []).map((item) => item.scale_yi || 0))
                  return (
                    <div className="fund-bar-row" key={row.date}>
                      <div className="fund-bar-label">{row.date}</div>
                      <div className="fund-bar-track"><i style={{ width: `${Math.min(100, (row.scale_yi || 0) / maxScale * 100)}%` }} /></div>
                      <div className="fund-bar-value">{row.scale_yi != null ? `${num(row.scale_yi)}亿` : '-'}</div>
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
  )
}
