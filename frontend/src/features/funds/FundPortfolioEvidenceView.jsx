import FundMetricCard from './FundMetricCard'
import { num, pct } from './fundFormatters'

/** Renders disclosed fund holdings and industry exposure. */
export default function FundPortfolioEvidenceView({ portfolio, portfolioError, loadingPortfolio }) {
  return (
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
            <FundMetricCard label="前3大重仓" value={pct(portfolio.summary.top3_stock_ratio)} />
            <FundMetricCard label="前10大重仓" value={pct(portfolio.summary.top10_stock_ratio)} />
            <FundMetricCard label="集中度" value={portfolio.summary.concentration} />
            <FundMetricCard label="风格提示" value={portfolio.summary.style_note} />
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
                    {portfolio.stocks.slice(0, 10).map((row) => (
                      <tr key={`${row.code}-${row.name}`}>
                        <td style={{ fontWeight: 800 }}>{row.code}</td>
                        <td>{row.name}</td>
                        <td>{pct(row.nav_ratio)}</td>
                        <td>{num(row.market_value_wan)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h4 className="fund-subhead">行业配置 <span className="hint">{portfolio.industry_period}</span></h4>
              <div className="fund-bar-list">
                {portfolio.industries.slice(0, 8).map((row) => (
                  <div className="fund-bar-row" key={row.name}>
                    <div className="fund-bar-label">{row.name}</div>
                    <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(1, row.nav_ratio || 0))}%` }} /></div>
                    <div className="fund-bar-value">{pct(row.nav_ratio)}</div>
                  </div>
                ))}
              </div>
              {portfolio.bonds.length > 0 && (
                <>
                  <h4 className="fund-subhead">债券持仓 <span className="hint">{portfolio.bond_period}</span></h4>
                  <div className="fund-bond-list">
                    {portfolio.bonds.slice(0, 5).map((row) => (
                      <span className="tag neutral" key={`${row.code}-${row.name}`}>{row.name} {pct(row.nav_ratio)}</span>
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
  )
}
