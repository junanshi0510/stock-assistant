import FundMetricCard from './FundMetricCard'
import { deltaClass, num, pct } from './fundFormatters'

/** Renders decision guidance derived from a single fund's real historical data. */
export default function FundDecisionView({ fund }) {
  return (
    <>
      {fund.timing && (
        <div className="panel fade-in">
          <h3 className="section-title">
            买入节奏 <span className="hint">基于真实净值历史计算回撤分位、均线结构和滚动收益，不做模拟预测</span>
          </h3>
          <div className="bt-cards quality-cards">
            <FundMetricCard label="节奏评分" value={fund.timing.score != null ? `${fund.timing.score} · ${fund.timing.label}` : fund.timing.label} />
            <FundMetricCard label="当前回撤" value={pct(fund.timing.zones?.current_drawdown)} cls="delta-neg" />
            <FundMetricCard label="回撤分位" value={pct(fund.timing.zones?.drawdown_percentile)} />
            <FundMetricCard label="阶段高点" value={fund.timing.zones?.high_nav != null ? `${num(fund.timing.zones.high_nav, 4)} · ${fund.timing.zones.high_date}` : '-'} />
            <FundMetricCard label="20日均值" value={fund.timing.zones?.ma20 != null ? num(fund.timing.zones.ma20, 4) : '-'} />
            <FundMetricCard label="60日均值" value={fund.timing.zones?.ma60 != null ? num(fund.timing.zones.ma60, 4) : '-'} />
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
              {fund.timing.signals.map((signal, index) => (
                <div className={`fund-signal ${signal.level || 'neutral'}`} key={`${signal.name}-${index}`}>
                  <b>{signal.name}</b>
                  <span>{signal.text}</span>
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
                  {fund.timing.rolling_returns.map((row) => (
                    <tr key={row.label}>
                      <td>{row.label}</td>
                      <td className={deltaClass(row.current_return)}>{pct(row.current_return)}</td>
                      <td>{pct(row.historical_percentile)}</td>
                      <td className={deltaClass(row.avg_return)}>{pct(row.avg_return)}</td>
                      <td>{pct(row.positive_ratio)}</td>
                      <td>{row.sample_count}</td>
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
                {(fund.playbook.role?.risk_labels || []).map((label) => <span className="tag neutral" key={label}>{label}</span>)}
                {(fund.playbook.role?.style_labels || []).map((label) => <span className="tag neutral" key={`style-${label}`}>{label}</span>)}
              </div>
            </div>
            <div className="playbook-review-grid">
              {(fund.playbook.review_metrics || []).slice(0, 8).map((metric) => (
                <div className="playbook-review" key={metric.name}>
                  <span>{metric.name}</span>
                  <b className={metric.unit === '%' ? deltaClass(metric.value) : ''}>{metric.value == null ? '-' : `${num(metric.value)}${metric.unit || ''}`}</b>
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
    </>
  )
}
