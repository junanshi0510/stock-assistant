import FundMetricCard from './FundMetricCard'
import { FundCompareChart } from './FundCharts'
import { deltaClass, metricText, num, pct } from './fundFormatters'

/** Renders comparisons and disclosed-holding overlap for selected funds. */
export default function FundCompareView({
  compareInput,
  setCompareInput,
  runCompare,
  loadingCompare,
  runOverlap,
  loadingOverlap,
  compareData,
  overlapData,
  loadFund,
  months,
}) {
  return (
    <div className="panel fade-in">
      <h3 className="section-title">
        多基金对比 <span className="hint">共同净值日期重算，首日=100，横向比较收益、回撤、波动和相关性</span>
      </h3>
      <div className="form-row">
        <div className="field fund-compare-input">
          <label>基金代码</label>
          <textarea value={compareInput} onChange={(event) => setCompareInput(event.target.value)}
            placeholder="例如: 110022 001480 006502" />
        </div>
        <button onClick={runCompare} disabled={loadingCompare}>
          {loadingCompare ? <><span className="spinner" /> 对比中</> : '开始对比'}
        </button>
        <button className="ghost" onClick={runOverlap} disabled={loadingOverlap}>
          {loadingOverlap ? <><span className="spinner" /> 分析中</> : '持仓重合度'}
        </button>
      </div>
      {compareData && (
        <>
          <div className="bt-cards quality-cards fund-leader-cards">
            <FundMetricCard label="近3月领先" value={`${compareData.leaders.best_3m.code} ${pct(compareData.leaders.best_3m.return_3m)}`} cls={deltaClass(compareData.leaders.best_3m.return_3m)} />
            <FundMetricCard label="近1年领先" value={`${compareData.leaders.best_1y.code} ${pct(compareData.leaders.best_1y.return_1y)}`} cls={deltaClass(compareData.leaders.best_1y.return_1y)} />
            <FundMetricCard label="低波动" value={`${compareData.leaders.lowest_vol.code} ${pct(compareData.leaders.lowest_vol.annual_volatility)}`} />
            <FundMetricCard label="低回撤" value={`${compareData.leaders.shallowest_drawdown.code} ${pct(compareData.leaders.shallowest_drawdown.max_drawdown)}`} cls="delta-neg" />
          </div>
          {compareData.portfolio_playbook && (
            <div className="fund-playbook-panel fund-batch-playbook">
              <h4 className="fund-subhead">批量投资经验手册</h4>
              <div className="fund-playbook-hero">
                <div>
                  <span className="tag neutral">{compareData.portfolio_playbook.label}</span>
                  <h4>{compareData.portfolio_playbook.conclusion}</h4>
                  <div className="daily-tags">
                    {(compareData.portfolio_playbook.risk_flags || []).map((text) => (
                      <span className="tag neutral" key={text}>{text}</span>
                    ))}
                  </div>
                </div>
                <div className="playbook-review-grid">
                  {(compareData.portfolio_playbook.metrics || []).map((metric) => (
                    <div className="playbook-review" key={metric.name}>
                      <span>{metric.name}</span>
                      <b className={metric.unit === '%' ? deltaClass(metric.value) : ''}>
                        {metricText(metric)}
                      </b>
                    </div>
                  ))}
                </div>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">角色分布</h4>
                  <div className="playbook-rule-list">
                    {(compareData.portfolio_playbook.role_distribution || []).map((row) => (
                      <div className="playbook-rule" key={row.name}>
                        <b>{row.name} · {row.count}只</b>
                        <span>组合占比 {num(row.ratio)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">高相关提示</h4>
                  <div className="playbook-rule-list">
                    {(compareData.portfolio_playbook.high_corr_pairs || []).length > 0 ? (
                      compareData.portfolio_playbook.high_corr_pairs.map((row) => (
                        <div className="playbook-rule danger" key={`${row.a}-${row.b}`}>
                          <b>{row.a} / {row.b}</b>
                          <span>历史收益相关性 {num(row.correlation, 3)}，新增资金前先判断是否重复暴露。</span>
                        </div>
                      ))
                    ) : (
                      <div className="playbook-rule">
                        <b>未触发高相关红旗</b>
                        <span>当前共同净值样本中未发现相关性高于 0.85 的基金组合。</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <h4 className="fund-subhead">单只基金批量动作</h4>
              <div className="corr-wrap">
                <table className="compact-table batch-action-table">
                  <thead>
                    <tr>
                      <th>基金</th>
                      <th>角色</th>
                      <th>动作</th>
                      <th>依据</th>
                      <th>注意</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(compareData.portfolio_playbook.fund_actions || []).map((row) => (
                      <tr key={row.code}>
                        <td><b>{row.code}</b><br />{row.name}</td>
                        <td>{row.risk_band || row.role || '-'}</td>
                        <td><span className="tag neutral">{row.action}</span></td>
                        <td>{row.reason}</td>
                        <td>{(row.cautions || []).join('；') || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="playbook-grid">
                <div>
                  <h4 className="fund-subhead">批量规则</h4>
                  <div className="playbook-rule-list">
                    {(compareData.portfolio_playbook.batch_rules || []).map((row) => (
                      <div className="playbook-rule" key={row.title}>
                        <b>{row.title}</b>
                        <span>{row.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h4 className="fund-subhead">执行步骤</h4>
                  <div className="playbook-rule-list">
                    {(compareData.portfolio_playbook.execution_steps || []).map((row) => (
                      <div className="playbook-rule" key={row.step}>
                        <b>{row.step}</b>
                        <span>{row.action}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <h4 className="fund-subhead">复盘问题</h4>
              <div className="fund-bond-list">
                {(compareData.portfolio_playbook.review_questions || []).map((text) => (
                  <span className="tag neutral" key={text}>{text}</span>
                ))}
              </div>
              <p className="hint" style={{ marginTop: 12 }}>{compareData.portfolio_playbook.method?.note}</p>
            </div>
          )}
          <FundCompareChart data={compareData} />
          <div className="corr-wrap">
            <table className="compact-table fund-compare-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>近1月</th>
                  <th>近3月</th>
                  <th>近6月</th>
                  <th>近1年</th>
                  <th>年化波动</th>
                  <th>最大回撤</th>
                  <th>定投分</th>
                </tr>
              </thead>
              <tbody>
                {compareData.items.map((row) => (
                  <tr key={row.code} className="clickable" onClick={() => loadFund(row.code, months)}>
                    <td style={{ fontWeight: 800 }}>{row.code}</td>
                    <td>{row.name}</td>
                    <td className={deltaClass(row.return_1m)}>{pct(row.return_1m)}</td>
                    <td className={deltaClass(row.return_3m)}>{pct(row.return_3m)}</td>
                    <td className={deltaClass(row.return_6m)}>{pct(row.return_6m)}</td>
                    <td className={deltaClass(row.return_1y)}>{pct(row.return_1y)}</td>
                    <td>{pct(row.annual_volatility)}</td>
                    <td className="delta-neg">{pct(row.max_drawdown)}</td>
                    <td>{row.dca_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {overlapData && (
        <div className="fund-overlap-block">
          <div className="bt-cards quality-cards">
            <FundMetricCard label="平均个股重合" value={pct(overlapData.summary.avg_stock_overlap_weight)} />
            <FundMetricCard label="平均行业重合" value={pct(overlapData.summary.avg_industry_overlap_weight)} />
            <FundMetricCard label="高重合组合" value={`${overlapData.summary.high_overlap_pair_count}/${overlapData.summary.pair_count}`} />
            <FundMetricCard label="结论" value={overlapData.summary.conclusion} />
          </div>
          <div className="fund-holding-grid">
            <div>
              <h4 className="fund-subhead">基金两两重合</h4>
              <div className="corr-wrap">
                <table className="compact-table fund-overlap-table">
                  <thead>
                    <tr>
                      <th>基金组合</th>
                      <th>共同股数</th>
                      <th>个股重合</th>
                      <th>行业重合</th>
                      <th>等级</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overlapData.pairwise.map((row) => (
                      <tr key={`${row.fund_a}-${row.fund_b}`}>
                        <td>{row.fund_a} / {row.fund_b}</td>
                        <td>{row.common_stock_count}</td>
                        <td>{pct(row.stock_overlap_weight)}</td>
                        <td>{pct(row.industry_overlap_weight)}</td>
                        <td><span className="tag neutral">{row.level}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h4 className="fund-subhead">共同重仓股</h4>
              <div className="fund-bond-list">
                {overlapData.shared_stocks.slice(0, 12).map((row) => (
                  <span className="tag neutral" key={row.code}>{row.name} {row.fund_count}只 · {pct(row.max_ratio)}</span>
                ))}
                {!overlapData.shared_stocks.length && <span className="hint">未发现披露重仓股重合</span>}
              </div>
              <h4 className="fund-subhead">共同暴露行业</h4>
              <div className="fund-bar-list">
                {overlapData.shared_industries.slice(0, 8).map((row) => (
                  <div className="fund-bar-row" key={row.name}>
                    <div className="fund-bar-label">{row.name}</div>
                    <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(1, row.max_ratio || 0))}%` }} /></div>
                    <div className="fund-bar-value">{pct(row.max_ratio)}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <p className="hint" style={{ marginTop: 12 }}>{overlapData.method.note} 数据源: {overlapData.source}。</p>
        </div>
      )}
    </div>
  )
}
