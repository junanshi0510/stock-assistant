import { GitCompareArrows, RefreshCw } from 'lucide-react'
import FundMetricCard from './FundMetricCard'
import { deltaClass, pct } from './fundFormatters'

function DisclosureList({ title, rows, emptyLabel, removed = false }) {
  return (
    <div>
      <h4 className="fund-subhead">{title}</h4>
      {rows?.length ? (
        <div className="corr-wrap">
          <table className="compact-table fund-disclosure-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>{removed ? '上期披露占净值' : '本期披露占净值'}</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 8).map((row) => (
                <tr key={`${row.code || row.name}-${row.name}`}>
                  <td style={{ fontWeight: 800 }}>{row.code || '-'}</td>
                  <td>{row.name || '-'}</td>
                  <td>{pct(row.nav_ratio)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="placeholder fund-disclosure-empty">{emptyLabel}</div>}
    </div>
  )
}

/** Compares two provider disclosures; it never represents list changes as trades. */
export default function FundDisclosureChangesView({ code, changes, error, loading, onLoad }) {
  const available = changes?.status === 'available'
  const summary = changes?.summary || {}

  return (
    <div className="panel fade-in">
      <div className="fund-disclosure-head">
        <div>
          <h3 className="section-title">
            披露变化 <span className="hint">跨两期定期报告观察重仓与行业风格是否发生变化</span>
          </h3>
          <p className="hint fund-disclosure-caption">按需读取两期真实披露，避免在每次打开基金时增加额外数据请求。</p>
        </div>
        <button className="ghost" onClick={() => onLoad(code)} disabled={loading} title="读取两期基金定期披露并比较">
          {loading
            ? <><RefreshCw size={16} className="spin-icon" aria-hidden="true" /> 读取中</>
            : <><GitCompareArrows size={16} aria-hidden="true" /> {changes ? '重新读取' : '查看披露变化'}</>}
        </button>
      </div>

      {loading && !changes && <div className="placeholder"><div className="big">⌛</div>正在读取两期真实基金定期披露</div>}
      {error && <div className="error">{error}</div>}
      {changes && !available && (
        <>
          <div className="placeholder">暂时无法形成可靠的两期对比</div>
          <div className="fund-bond-list" style={{ marginTop: 12 }}>
            {(changes.reasons || []).map((reason) => <span className="tag neutral" key={reason}>{reason}</span>)}
          </div>
          <p className="hint" style={{ marginTop: 12 }}>{changes.policy}</p>
        </>
      )}
      {available && (
        <>
          <div className="fund-disclosure-periods">
            <span className="tag neutral">本期 {changes.latest.stock_period || changes.latest.industry_period || changes.latest.year}</span>
            <span className="tag neutral">上期 {changes.previous.stock_period || changes.previous.industry_period || changes.previous.year}</span>
          </div>
          <div className="bt-cards quality-cards">
            <FundMetricCard label="前10披露权重变化" value={pct(summary.top10_stock_ratio_change)} cls={deltaClass(summary.top10_stock_ratio_change)} />
            <FundMetricCard label="共同披露重仓" value={`${summary.common_stock_count || 0}只`} />
            <FundMetricCard label="本期披露前列新增" value={`${summary.added_stock_count || 0}只`} />
            <FundMetricCard label="本期首要行业" value={summary.latest_top_industry || '-'} />
          </div>
          {summary.industry_focus_changed && (
            <p className="hint fund-disclosure-caption">首要披露行业由“{summary.previous_top_industry}”切换为“{summary.latest_top_industry}”。</p>
          )}

          {changes.comparison_scope?.includes('stocks') && (
            <>
              <div className="fund-holding-grid">
                <DisclosureList title="本期披露前列新增" rows={changes.added_stocks} emptyLabel="两期披露前列未见新增项" />
                <DisclosureList title="本期披露前列退出" rows={changes.removed_stocks} emptyLabel="两期披露前列未见退出项" removed />
              </div>
              <h4 className="fund-subhead" style={{ marginTop: 18 }}>共同披露重仓的占净值比例变化</h4>
              {changes.stock_changes?.length ? (
                <div className="corr-wrap">
                  <table className="compact-table fund-disclosure-change-table">
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th>本期</th>
                        <th>上期</th>
                        <th>变化</th>
                      </tr>
                    </thead>
                    <tbody>
                      {changes.stock_changes.slice(0, 10).map((row) => (
                        <tr key={`${row.code}-${row.name}`}>
                          <td style={{ fontWeight: 800 }}>{row.code || '-'}</td>
                          <td>{row.name || '-'}</td>
                          <td>{pct(row.latest_nav_ratio)}</td>
                          <td>{pct(row.previous_nav_ratio)}</td>
                          <td className={deltaClass(row.delta)}>{pct(row.delta)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div className="placeholder">两期披露没有可比较的共同重仓项</div>}
            </>
          )}

          {changes.comparison_scope?.includes('industries') && (
            <>
              <h4 className="fund-subhead" style={{ marginTop: 18 }}>共同披露行业的占净值比例变化</h4>
              {changes.industry_changes?.length ? (
                <div className="corr-wrap">
                  <table className="compact-table fund-disclosure-change-table">
                    <thead>
                      <tr>
                        <th>行业</th>
                        <th>本期</th>
                        <th>上期</th>
                        <th>变化</th>
                      </tr>
                    </thead>
                    <tbody>
                      {changes.industry_changes.slice(0, 10).map((row) => (
                        <tr key={row.name}>
                          <td>{row.name || '-'}</td>
                          <td>{pct(row.latest_nav_ratio)}</td>
                          <td>{pct(row.previous_nav_ratio)}</td>
                          <td className={deltaClass(row.delta)}>{pct(row.delta)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div className="placeholder">两期披露没有可比较的共同配置行业</div>}
            </>
          )}
          <p className="hint" style={{ marginTop: 14 }}>{changes.policy} 数据源: {changes.source}。</p>
        </>
      )}
    </div>
  )
}
