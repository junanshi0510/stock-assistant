import FundMetricCard from './FundMetricCard'
import { deltaClass, num, pct } from './fundFormatters'

/** Renders historical risk, drawdown recovery, and return-calendar evidence. */
export default function FundRiskEvidenceView({ fund }) {
  const recovery = fund.drawdown_recovery
  const calendar = fund.calendar_returns

  return (
    <>
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

      {recovery && (
        <div className="panel fade-in">
          <h3 className="section-title">
            回撤修复画像 <span className="hint">从真实历史净值统计创新高、回撤深度和修复耗时</span>
          </h3>
          <div className="bt-cards quality-cards">
            <FundMetricCard label="修复特征" value={recovery.label} />
            <FundMetricCard label="最近新高" value={recovery.latest_high_date || '-'} />
            <FundMetricCard label="离新高天数" value={recovery.days_since_high != null ? `${recovery.days_since_high}天` : '-'} />
            <FundMetricCard label="历史回撤段" value={recovery.episode_count} />
            <FundMetricCard label="已修复比例" value={pct(recovery.recovery_rate)} />
            <FundMetricCard label="平均修复" value={recovery.avg_recovery_days != null ? `${num(recovery.avg_recovery_days, 0)}天` : '-'} />
          </div>
          <div className="fund-recovery-grid">
            <div>
              <h4 className="fund-subhead">回撤分布</h4>
              <div className="fund-bond-list">
                <span className="tag neutral">超过5%: {recovery.deep_drawdown_count_5}次</span>
                <span className="tag neutral">超过10%: {recovery.deep_drawdown_count_10}次</span>
                <span className="tag neutral">超过20%: {recovery.deep_drawdown_count_20}次</span>
                <span className="tag neutral">最长修复: {recovery.max_recovery_days != null ? `${recovery.max_recovery_days}天` : '-'}</span>
              </div>
              <p className="hint">当前仍在回撤时，开放回撤天数为 {recovery.open_drawdown_days != null ? `${recovery.open_drawdown_days}天` : '-'}，当前开放回撤深度 {pct(recovery.open_drawdown_depth)}。</p>
            </div>
            <div>
              <h4 className="fund-subhead">最深回撤区间</h4>
              {recovery.episodes?.length ? (
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
                      {recovery.episodes.map((row, index) => (
                        <tr key={`${row.peak_date}-${row.trough_date}-${index}`}>
                          <td>{row.peak_date}</td>
                          <td>{row.trough_date}</td>
                          <td className="delta-neg">{pct(row.depth)}</td>
                          <td>{row.recovered ? row.recovery_date : '未修复'}</td>
                          <td>{row.recovery_days != null ? `${row.recovery_days}天` : '-'}</td>
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

      {calendar && (
        <div className="panel fade-in">
          <h3 className="section-title">
            收益日历 <span className="hint">自然年、最近月份和月份胜率，均由真实单位净值计算</span>
          </h3>
          <div className="bt-cards quality-cards">
            <FundMetricCard label="年度胜率" value={pct(calendar.summary?.positive_year_ratio)} />
            <FundMetricCard label="上涨年份" value={calendar.summary?.positive_years ?? '-'} />
            <FundMetricCard label="下跌年份" value={calendar.summary?.negative_years ?? '-'} />
            <FundMetricCard label="最好年份" value={calendar.summary?.best_year ? `${calendar.summary.best_year.year} ${pct(calendar.summary.best_year.return)}` : '-'} cls={deltaClass(calendar.summary?.best_year?.return)} />
            <FundMetricCard label="最差年份" value={calendar.summary?.worst_year ? `${calendar.summary.worst_year.year} ${pct(calendar.summary.worst_year.return)}` : '-'} cls="delta-neg" />
            <FundMetricCard label="最好月份" value={calendar.summary?.best_month ? `${calendar.summary.best_month.month} ${pct(calendar.summary.best_month.return)}` : '-'} cls={deltaClass(calendar.summary?.best_month?.return)} />
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
                    {calendar.years.map((row) => (
                      <tr key={row.year}>
                        <td>{row.year}</td>
                        <td>{row.start_date}</td>
                        <td>{row.end_date}</td>
                        <td className={deltaClass(row.return)}>{pct(row.return)}</td>
                        <td>{row.sample_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h4 className="fund-subhead">月份统计</h4>
              <div className="fund-bar-list">
                {calendar.month_stats.map((row) => (
                  <div className="fund-bar-row" key={row.month}>
                    <div className="fund-bar-label">{row.month}月</div>
                    <div className="fund-bar-track"><i style={{ width: `${Math.min(100, Math.max(4, Math.abs(row.avg_return || 0) * 4))}%` }} /></div>
                    <div className={`fund-bar-value ${deltaClass(row.avg_return)}`}>{pct(row.avg_return)} · 胜率 {pct(row.win_rate)}</div>
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
                {calendar.recent_months.map((row) => (
                  <tr key={row.month}>
                    <td>{row.month}</td>
                    <td>{row.start_date}</td>
                    <td>{row.end_date}</td>
                    <td className={deltaClass(row.return)}>{pct(row.return)}</td>
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
