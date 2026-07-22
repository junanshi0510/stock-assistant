import { deltaClass, num, pct } from './fundFormatters'

const RISK_OPTIONS = [
  ['stable', '稳健'],
  ['balanced', '均衡'],
  ['aggressive', '进取'],
]

/** Renders the real-data fund discovery workflow only. */
export default function FundDiscoveryView({
  opportunityRisk,
  setOpportunityRisk,
  loadOpportunities,
  loadingOpportunities,
  opportunities,
  categoryHeat,
  category,
  setCategory,
  sort,
  loadHot,
  categoryError,
  rows,
  hot,
  code,
  months,
  loadFund,
}) {
  return (
    <>
      <div className="panel fade-in">
        <h3 className="section-title">
          基金候选初筛 <span className="hint">基于真实榜单做固定规则排序，必须进入单基金研究后才能形成结论</span>
        </h3>
        <div className="form-row" style={{ marginBottom: 14 }}>
          <div className="field">
            <label>风险偏好</label>
            <select value={opportunityRisk} onChange={(event) => {
              setOpportunityRisk(event.target.value)
              loadOpportunities(event.target.value)
            }}>
              {RISK_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <button onClick={() => loadOpportunities()} disabled={loadingOpportunities}>
            {loadingOpportunities ? <><span className="spinner" /> 筛选中</> : '刷新候选'}
          </button>
          {opportunities && <span className="hint">数据源: {opportunities.source} · 截至 {opportunities.as_of || '-'}</span>}
        </div>

        {opportunities && (
          <>
            <div className="fund-opportunity-grid">
              {opportunities.buckets.map((bucket) => (
                <div className="fund-opportunity-card" key={bucket.key}>
                  <h4 className="fund-subhead">{bucket.name} <span className="hint">{bucket.profile}</span></h4>
                  <div className="corr-wrap">
                    <table className="compact-table fund-opportunity-table">
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>名称</th>
                          <th>初筛强度</th>
                          <th>近3月</th>
                          <th>近1年</th>
                          <th>规模</th>
                          <th>提示</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bucket.items.map((row) => (
                          <tr key={row.code} className="clickable" onClick={() => loadFund(row.code, months)}>
                            <td style={{ fontWeight: 800 }}>{row.code}</td>
                            <td>{row.name}</td>
                            <td>{num(row.screening_score, 1)}</td>
                            <td className={deltaClass(row.return_3m)}>{pct(row.return_3m)}</td>
                            <td className={deltaClass(row.return_1y)}>{pct(row.return_1y)}</td>
                            <td>{row.scale_yi != null ? `${num(row.scale_yi)}亿` : '-'}</td>
                            <td>{row.cautions?.slice(-1)[0] || '-'}</td>
                          </tr>
                        ))}
                        {!bucket.items.length && (
                          <tr><td colSpan="7" className="hint">当前真实榜单下没有满足筛选条件的候选</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
            {opportunities.failed?.length > 0 && (
              <div className="error" style={{ marginTop: 12 }}>
                {opportunities.failed.map((item) => `${item.name}: ${item.error}`).join('；')}
              </div>
            )}
            <p className="hint" style={{ marginTop: 12 }}>
              {opportunities.method.score} {opportunities.risk_note}
            </p>
          </>
        )}
      </div>

      {categoryHeat.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">基金分类热度</h3>
          <div className="fund-category-grid">
            {categoryHeat.map((item) => (
              <button key={item.category} className={`fund-category ${category === item.category ? 'active' : ''}`}
                onClick={() => { setCategory(item.category); loadHot(item.category, sort) }}>
                <span>{item.name}</span>
                <b className={deltaClass(item.avg_3m)}>{pct(item.avg_3m)}</b>
                <small>{item.heat} · 领涨 {item.leader_name || '-'}</small>
              </button>
            ))}
          </div>
        </div>
      )}
      {categoryError && (
        <div className="error">真实基金分类热度数据获取失败: {categoryError}</div>
      )}

      {rows.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">热门基金榜 <span className="hint">{hot.category_name} · {hot.sort}</span></h3>
          <div className="corr-wrap">
            <table className="compact-table fund-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>代码</th>
                  <th>基金简称</th>
                  <th>日期</th>
                  <th>单位净值</th>
                  <th>近1月</th>
                  <th>近3月</th>
                  <th>近6月</th>
                  <th>近1年</th>
                  <th>今年来</th>
                  <th>趋势</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.code} className={`clickable ${row.code === code ? 'row-active' : ''}`}
                    onClick={() => loadFund(row.code, months)}>
                    <td className="rank-idx">{row.rank}</td>
                    <td style={{ fontWeight: 800 }}>{row.code}</td>
                    <td>{row.name}</td>
                    <td>{row.date}</td>
                    <td>{num(row.unit_nav, 4)}</td>
                    <td className={deltaClass(row.return_1m)}>{pct(row.return_1m)}</td>
                    <td className={deltaClass(row.return_3m)}>{pct(row.return_3m)}</td>
                    <td className={deltaClass(row.return_6m)}>{pct(row.return_6m)}</td>
                    <td className={deltaClass(row.return_1y)}>{pct(row.return_1y)}</td>
                    <td className={deltaClass(row.return_ytd)}>{pct(row.return_ytd)}</td>
                    <td><span className="tag neutral">{row.trend}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
