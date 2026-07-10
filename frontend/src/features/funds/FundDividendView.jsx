import FundMetricCard from './FundMetricCard'
import { num } from './fundFormatters'

/** Renders provider disclosures for dividends and fund splits. */
export default function FundDividendView({ dividends, loadingDividends }) {
  return (
    <div className="panel fade-in">
      <h3 className="section-title">
        分红送配 <span className="hint">现金分配记录、拆分折算和累计分红画像</span>
      </h3>
      {loadingDividends && !dividends && <div className="placeholder"><div className="big">⌛</div>正在读取真实分红记录</div>}
      {dividends?.error && <div className="error">{dividends.error}</div>}
      {dividends && !dividends.error && (
        <>
          <div className="bt-cards quality-cards">
            <FundMetricCard label="分红特征" value={dividends.summary.label} />
            <FundMetricCard label="分红次数" value={dividends.summary.dividend_count} />
            <FundMetricCard label="累计每份分红" value={dividends.summary.total_cash_per_share != null ? `${num(dividends.summary.total_cash_per_share, 4)}元` : '-'} />
            <FundMetricCard label="拆分次数" value={dividends.summary.split_count} />
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
                  {dividends.dividends.slice(0, 12).map((row, index) => (
                    <tr key={`${row.ex_dividend_date}-${index}`}>
                      <td>{row.year}</td>
                      <td>{row.record_date}</td>
                      <td>{row.ex_dividend_date}</td>
                      <td>{row.cash_per_share != null ? `${num(row.cash_per_share, 4)}元` : row.cash_text}</td>
                      <td>{row.payment_date}</td>
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
              {dividends.splits.slice(0, 6).map((row, index) => (
                <span className="tag neutral" key={`${row.date}-${index}`}>{row.date} {row.type} {row.ratio}</span>
              ))}
            </div>
          )}
          <p className="hint" style={{ marginTop: 12 }}>{dividends.method.note} 数据源: {dividends.source}。</p>
        </>
      )}
    </div>
  )
}
