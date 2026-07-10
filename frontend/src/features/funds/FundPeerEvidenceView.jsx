import FundMetricCard from './FundMetricCard'
import { deltaClass, num, pct } from './fundFormatters'

const SORTS = [
  ['1y', '近1年'],
  ['ytd', '今年来'],
  ['6m', '近6月'],
  ['3m', '近3月'],
  ['1m', '近1月'],
]

/** Renders peer ranking and replacement candidates for the selected fund. */
export default function FundPeerEvidenceView({
  fund,
  months,
  peers,
  peerSort,
  setPeerSort,
  loadPeers,
  loadingPeers,
  loadFund,
  alternatives,
  loadAlternatives,
  loadingAlternatives,
  setCompareInput,
}) {
  return (
    <>
      <div className="panel fade-in">
        <h3 className="section-title">
          同类定位 <span className="hint">在同类型基金排行中查看当前基金的位置</span>
        </h3>
        <div className="form-row" style={{ marginBottom: 14 }}>
          <div className="field">
            <label>同类排序</label>
            <select value={peerSort} onChange={(event) => {
              setPeerSort(event.target.value)
              loadPeers(fund.code, event.target.value)
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
              <FundMetricCard label="同类类型" value={peers.category_name || '-'} />
              <FundMetricCard label="同类排名" value={peers.rank ? `${peers.rank}/${peers.sample_count}` : `未进前${peers.sample_count}`} />
              <FundMetricCard label="击败同类" value={peers.beat_ratio != null ? pct(peers.beat_ratio) : '-'} />
              <FundMetricCard label="位置判断" value={peers.position_label} />
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
                      {peers.leaders.slice(0, 10).map((row) => (
                        <tr key={`leader-${row.code}`} className="clickable" onClick={() => loadFund(row.code, months)}>
                          <td>{row.rank}</td>
                          <td style={{ fontWeight: 800 }}>{row.code}</td>
                          <td>{row.name}</td>
                          <td className={deltaClass(row.return_1y)}>{pct(row.return_1y)}</td>
                          <td className={deltaClass(row.return_3m)}>{pct(row.return_3m)}</td>
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
                        {peers.neighbors.map((row) => (
                          <tr key={`neighbor-${row.code}`} className={`clickable ${row.code === fund.code ? 'row-active' : ''}`} onClick={() => loadFund(row.code, months)}>
                            <td>{row.rank}</td>
                            <td style={{ fontWeight: 800 }}>{row.code}</td>
                            <td>{row.name}</td>
                            <td className={deltaClass(row.return_1y)}>{pct(row.return_1y)}</td>
                            <td className={deltaClass(row.return_3m)}>{pct(row.return_3m)}</td>
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
            setCompareInput([fund.code, ...(alternatives?.alternatives || []).slice(0, 3).map((row) => row.code)].join(' '))
          }} disabled={!alternatives?.alternatives?.length}>
            加入多基金对比
          </button>
          {alternatives && <span className="hint">同类 {alternatives.selected?.category_name || '-'} · 排序 {alternatives.sort} · 截至 {alternatives.as_of || '-'}</span>}
        </div>
        {loadingAlternatives && !alternatives && <div className="placeholder"><div className="big">⌛</div>正在读取真实同类基金和净值指标</div>}
        {alternatives && (
          <>
            <div className="bt-cards quality-cards">
              <FundMetricCard label="当前基金" value={`${alternatives.selected.code} ${alternatives.selected.name || ''}`} />
              <FundMetricCard label="当前同类排名" value={alternatives.selected.rank ? `${alternatives.selected.rank}/${alternatives.selected.sample_count}` : `未进前${alternatives.selected.sample_count}`} />
              <FundMetricCard label="评分最高候选" value={`${alternatives.summary.best_score.code} · ${alternatives.summary.best_score.score}`} />
              <FundMetricCard label="低波候选" value={`${alternatives.summary.lower_volatility.code} ${pct(alternatives.summary.lower_volatility.metrics.annual_volatility)}`} />
              <FundMetricCard label="一年收益候选" value={`${alternatives.summary.better_1y.code} ${pct(alternatives.summary.better_1y.metrics.return_1y)}`} cls={deltaClass(alternatives.summary.better_1y.metrics.return_1y)} />
              <FundMetricCard label="低回撤候选" value={`${alternatives.summary.shallower_drawdown.code} ${pct(alternatives.summary.shallower_drawdown.metrics.max_drawdown)}`} cls="delta-neg" />
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
                {alternatives.failed.map((item) => `${item.code || item.name}: ${item.error}`).join('；')}
              </div>
            )}
            <p className="hint" style={{ marginTop: 12 }}>{alternatives.method?.score} {alternatives.method?.note}</p>
          </>
        )}
      </div>
    </>
  )
}
