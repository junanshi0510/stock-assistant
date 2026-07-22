import { useState } from 'react'
import { runBacktest } from '../api/market'

const PLACEHOLDER = { 'A股': '如 600519', '港股': '如 00700', '美股': '如 AAPL' }

const DEFAULT_ASSUMPTIONS = {
  entry_score: 65,
  stop_atr: 2,
  target_atr: 3,
  commission_bps: 5,
  slippage_bps: 5,
  sell_tax_bps: 0,
  risk_per_trade_pct: 1,
  max_position_pct: 30,
}

const EXIT_LABELS = {
  target: '止盈',
  gap_target: '跳空止盈',
  stop: '止损',
  gap_stop: '跳空穿透止损',
  stop_first_ambiguous: '同日双触发·止损优先',
  time_exit: '到期退出',
}

function acolor(v) { return v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--faint)' }

function pct(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(digits)}%`
}

function number(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

function EquityCurve({ points }) {
  if (!points?.length) return <div className="hint">暂无可绘制的交易净值。</div>
  const width = 820
  const height = 190
  const pad = 18
  const values = [100, ...points.map((item) => Number(item.equity))]
  const low = Math.min(...values)
  const high = Math.max(...values)
  const range = Math.max(high - low, 0.01)
  const x = (index) => pad + index / Math.max(1, values.length - 1) * (width - pad * 2)
  const y = (value) => pad + (high - value) / range * (height - pad * 2)
  const line = values.map((value, index) => `${x(index)},${y(value)}`).join(' ')
  const baseline = y(100)
  const positive = values[values.length - 1] >= 100

  return (
    <div className="bt-equity-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="按风险仓位复利的历史交易净值曲线">
        <line x1={pad} x2={width - pad} y1={baseline} y2={baseline} className="bt-equity-baseline" />
        <polyline points={line} className={positive ? 'positive' : 'negative'} />
        <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="4" className={positive ? 'positive' : 'negative'} />
      </svg>
      <div className="bt-equity-caption">
        <span>起点 100</span>
        <span>{points[0]?.date || '—'} → {points[points.length - 1]?.date || '—'}</span>
        <b style={{ color: acolor(values[values.length - 1] - 100) }}>期末 {values[values.length - 1].toFixed(2)}</b>
      </div>
    </div>
  )
}

export default function BacktestTab({ markets }) {
  const [market, setMarket] = useState('A股')
  const [symbol, setSymbol] = useState('')
  const [horizon, setHorizon] = useState(20)
  const [assumptions, setAssumptions] = useState(DEFAULT_ASSUMPTIONS)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [bt, setBt] = useState(null)

  function updateAssumption(key, value) {
    setAssumptions((current) => ({ ...current, [key]: value }))
  }

  async function doBacktest() {
    if (!symbol.trim()) { setError('请先输入股票代码。'); return }
    const request = { horizon }
    for (const [key, value] of Object.entries(assumptions)) {
      const parsed = Number(value)
      if (!Number.isFinite(parsed)) { setError('请完整填写执行仿真参数。'); return }
      request[key] = parsed
    }
    setLoading(true); setError(''); setBt(null)
    try { setBt(await runBacktest(market, symbol.trim(), request)) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  const maxBucketRet = bt ? Math.max(...bt.buckets.map((b) => Math.abs(b.avg_return || 0)), 0.5) : 1
  const bull = bt?.by_signal?.['看涨'] || {}
  const bear = bt?.by_signal?.['看跌'] || {}
  const execution = bt?.execution
  const gate = execution?.research_gate
  const gateTone = gate?.historically_positive ? 'up' : gate?.status === 'insufficient_samples' ? 'neutral' : 'down'

  return (
    <>
      <div className="warning">
        <b>历史验证分两层</b>:方向统计检查过去每天的信号,执行仿真则只在下一交易日开盘买入,
        不重叠持仓,并扣除你设定的佣金、滑点和卖出税费。它用于排除“看起来很准、实际无法赚钱”的参数,
        仍不包含停牌、涨跌停排队、整手、公司行为和实际券商规则,<b>不代表未来表现</b>。
      </div>

      <div className="panel">
        <div className="form-row">
          <div className="field">
            <label>市场</label>
            <select value={market} onChange={(e) => setMarket(e.target.value)}>
              {markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field" style={{ flex: 1, minWidth: 160 }}>
            <label>股票代码</label>
            <input value={symbol} placeholder={PLACEHOLDER[market]}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doBacktest()} />
          </div>
          <div className="field">
            <label>最长持有:{horizon} 个交易日</label>
            <input type="range" min="3" max="60" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
          </div>
          <button onClick={doBacktest} disabled={loading}>
            {loading ? <><span className="spinner" /> 回测中</> : '开始验证'}
          </button>
        </div>

        <div className="bt-config-head">
          <div>
            <b>执行与风险假设</b>
            <span>成本填基点(bps),10 bps = 0.10%;所有默认值只是压力测试场景。</span>
          </div>
          <button className="ghost" type="button" onClick={() => setAssumptions(DEFAULT_ASSUMPTIONS)}>恢复默认</button>
        </div>
        <div className="bt-assumption-grid">
          <div className="field"><label>入场打分 ≥</label><input type="number" min="50" max="90" step="1" value={assumptions.entry_score} onChange={(e) => updateAssumption('entry_score', e.target.value)} /></div>
          <div className="field"><label>止损 ATR 倍数</label><input type="number" min="0.5" max="6" step="0.1" value={assumptions.stop_atr} onChange={(e) => updateAssumption('stop_atr', e.target.value)} /></div>
          <div className="field"><label>止盈 ATR 倍数</label><input type="number" min="0.5" max="12" step="0.1" value={assumptions.target_atr} onChange={(e) => updateAssumption('target_atr', e.target.value)} /></div>
          <div className="field"><label>佣金/单边 bps</label><input type="number" min="0" max="100" step="1" value={assumptions.commission_bps} onChange={(e) => updateAssumption('commission_bps', e.target.value)} /></div>
          <div className="field"><label>滑点/单边 bps</label><input type="number" min="0" max="100" step="1" value={assumptions.slippage_bps} onChange={(e) => updateAssumption('slippage_bps', e.target.value)} /></div>
          <div className="field"><label>卖出税费 bps</label><input type="number" min="0" max="200" step="1" value={assumptions.sell_tax_bps} onChange={(e) => updateAssumption('sell_tax_bps', e.target.value)} /></div>
          <div className="field"><label>单笔账户风险 %</label><input type="number" min="0.1" max="5" step="0.1" value={assumptions.risk_per_trade_pct} onChange={(e) => updateAssumption('risk_per_trade_pct', e.target.value)} /></div>
          <div className="field"><label>单股仓位上限 %</label><input type="number" min="1" max="100" step="1" value={assumptions.max_position_pct} onChange={(e) => updateAssumption('max_position_pct', e.target.value)} /></div>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {!bt && !loading && (
        <div className="placeholder">
          <div className="big">🧪</div>
          输入一只股票,同时检验方向信号和成本后的历史交易期望。
        </div>
      )}

      {bt && (
        <div className="fade-in">
          {execution && (
            <>
              <div className="panel bt-gate-panel">
                <div className="bt-gate-head">
                  <div>
                    <span className={`badge ${gateTone}`}>{gate?.label || '未评估'}</span>
                    <h3>成本后执行研究门槛</h3>
                    <p>{gate?.detail}</p>
                  </div>
                  <div className="bt-policy-version">{execution.policy_version}</div>
                </div>
                <div className="bt-assumption-summary">
                  <span>下一交易日开盘</span>
                  <span>只做多·持仓不重叠</span>
                  <span>入场分 ≥ {execution.assumptions.entry_score}</span>
                  <span>止损/止盈 {execution.assumptions.stop_atr}/{execution.assumptions.target_atr} ATR</span>
                  <span>风险预算 {execution.assumptions.risk_per_trade_pct}%</span>
                  <span>仓位上限 {execution.assumptions.max_position_pct}%</span>
                </div>
              </div>

              <div className="bt-cards bt-execution-cards">
                <div className="bt-card"><div className="k">风险仓位复利结果</div><div className="v" style={{ color: acolor(execution.strategy_return_pct) }}>{pct(execution.strategy_return_pct)}</div><div className="hint">{execution.trade_count} 笔非重叠交易</div></div>
                <div className="bt-card"><div className="k">交易净值最大回撤</div><div className="v" style={{ color: 'var(--down)' }}>{pct(execution.max_drawdown_pct)}</div><div className="hint">按每笔风险倒推仓位</div></div>
                <div className="bt-card"><div className="k">单笔净期望</div><div className="v" style={{ color: acolor(execution.net_expectancy_pct) }}>{pct(execution.net_expectancy_pct, 3)}</div><div className="hint">成本拖累均值 {pct(-Number(execution.average_cost_drag_pct || 0), 3)}</div></div>
                <div className="bt-card"><div className="k">盈利因子</div><div className="v" style={{ color: execution.profit_factor > 1 ? 'var(--up)' : 'var(--down)' }}>{number(execution.profit_factor, 3)}</div><div className="hint">平均盈亏比 {number(execution.payoff_ratio, 3)}</div></div>
              </div>

              <div className="panel">
                <h3 className="section-title">📈 历史执行净值 <span className="hint">起点为100,仓位由止损距离和账户风险预算倒推</span></h3>
                <EquityCurve points={execution.equity_curve} />
                <div className="bt-exit-grid">
                  <div><span>止盈退出</span><b>{execution.exit_reasons.target}</b></div>
                  <div><span>止损退出</span><b>{execution.exit_reasons.stop}</b></div>
                  <div><span>到期退出</span><b>{execution.exit_reasons.time}</b></div>
                  <div><span>成本后胜率</span><b>{execution.win_rate == null ? '—' : `${execution.win_rate}%`}</b></div>
                  <div><span>平均持有</span><b>{execution.average_holding_days ?? '—'}日</b></div>
                  <div><span>平均仓位</span><b>{execution.average_position_pct ?? '—'}%</b></div>
                </div>
              </div>

              <div className="panel">
                <h3 className="section-title">🧧 最近交易路径 <span className="hint">最多返回最近30笔;所有价格均来自历史日线</span></h3>
                {!execution.trades.length ? <div className="hint">当前参数下没有形成可仿真交易。</div> : (
                  <div className="corr-wrap">
                    <table className="bt-trade-table">
                      <thead><tr><th>信号/入场</th><th>退出</th><th>价格路径</th><th>退出原因</th><th>净收益</th><th>仓位</th><th>账户贡献</th><th>MFE / MAE</th></tr></thead>
                      <tbody>
                        {execution.trades.slice().reverse().map((trade) => (
                          <tr key={`${trade.signal_date}-${trade.entry_date}-${trade.exit_date}`}>
                            <td><b>{trade.signal_date}</b><small>{trade.entry_date} 开盘·分数 {trade.signal_score}</small></td>
                            <td>{trade.exit_date}<small>持有 {trade.holding_days} 日</small></td>
                            <td>{number(trade.entry_price, 3)} → {number(trade.exit_price, 3)}<small>止损 {number(trade.stop_price, 3)}·止盈 {number(trade.target_price, 3)}</small></td>
                            <td><span className={`badge ${trade.exit_reason.includes('target') ? 'up' : trade.exit_reason.includes('stop') ? 'down' : 'neutral'}`}>{EXIT_LABELS[trade.exit_reason] || trade.exit_reason}</span>{trade.risk_budget_breached && <small className="delta-neg">超出风险预算</small>}</td>
                            <td style={{ color: acolor(trade.net_return_pct) }}><b>{pct(trade.net_return_pct, 3)}</b><small>毛收益 {pct(trade.gross_return_pct, 3)}</small></td>
                            <td>{trade.position_pct}%<small>计划止损 {pct(-trade.planned_loss_pct, 3)}</small></td>
                            <td style={{ color: acolor(trade.account_return_pct) }}>{pct(trade.account_return_pct, 3)}</td>
                            <td><span className="delta-pos">{pct(trade.mfe_pct, 2)}</span><small className="delta-neg">{pct(trade.mae_pct, 2)}</small></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="warning bt-limitations">
                <b>必须阅读的限制</b>
                <ul>{execution.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            </>
          )}

          <div className="bt-cards">
            <div className="bt-card"><div className="k">方向准确率</div><div className="v" style={{ color: bt.directional_accuracy >= 50 ? 'var(--up)' : 'var(--down)' }}>{bt.directional_accuracy ?? '—'}%</div><div className="hint">{bt.directional_count} 个明确信号·样本重叠</div></div>
            <div className="bt-card"><div className="k">看涨信号胜率</div><div className="v">{bull.win_rate ?? '—'}%</div><div className="hint">共 {bull.count ?? 0} 次</div></div>
            <div className="bt-card"><div className="k">看跌信号胜率</div><div className="v">{bear.win_rate ?? '—'}%</div><div className="hint">共 {bear.count ?? 0} 次</div></div>
            <div className="bt-card"><div className="k">样本基础上涨率</div><div className="v" style={{ color: 'var(--muted)' }}>{bt.benchmark.up_rate}%</div><div className="hint">样本 {bt.samples}</div></div>
          </div>

          <div className="panel">
            <h3 className="section-title">📊 分数分档 vs 之后{bt.horizon}日平均收益 <span className="hint">方向研究样本会重叠,不能当成可执行收益</span></h3>
            {bt.buckets.map((bucket) => (
              <div className="bucket-row" key={bucket.range}>
                <div className="label">{bucket.range}</div>
                <div className="track">{bucket.avg_return != null && <div className="fill" style={{ width: `${Math.min(100, Math.abs(bucket.avg_return) / maxBucketRet * 100)}%`, background: acolor(bucket.avg_return) }} />}</div>
                <div className="val" style={{ color: acolor(bucket.avg_return || 0) }}>{bucket.count ? `${bucket.avg_return > 0 ? '+' : ''}${bucket.avg_return}% · ${bucket.count}样本` : '无样本'}</div>
              </div>
            ))}
          </div>

          <div className="panel">
            <h3 className="section-title">📋 各方向信号表现</h3>
            <table>
              <thead><tr><th>信号</th><th>出现次数</th><th>胜率</th><th>平均收益</th><th>收益中位数</th></tr></thead>
              <tbody>
                {['看涨', '看跌', '中性'].map((signal) => {
                  const stats = bt.by_signal[signal] || {}
                  if (!stats.count) return <tr key={signal}><td>{signal}</td><td colSpan="4" className="hint">无样本</td></tr>
                  return <tr key={signal}><td><b>{signal}</b></td><td>{stats.count}</td><td>{stats.win_rate}%</td><td style={{ color: acolor(stats.avg_return) }}>{pct(stats.avg_return)}</td><td style={{ color: acolor(stats.median_return) }}>{pct(stats.median_return)}</td></tr>
                })}
              </tbody>
            </table>
            <p className="hint">回测区间:{bt.date_range[0]} ~ {bt.date_range[1]}。方向准确率与执行净期望回答不同问题;真正决策前应优先看成本后执行结果、样本数和限制。</p>
          </div>
        </div>
      )}
    </>
  )
}
