/** Renders text insights derived from the selected fund's real data. */
export default function FundInsightsView({ fund }) {
  return (
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
  )
}
