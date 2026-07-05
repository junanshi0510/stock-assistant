import { useState } from 'react'
import { runBacktest } from '../api'

const PLACEHOLDER = { 'A股': '如 600519', '港股': '如 00700', '美股': '如 AAPL' }

function acolor(v) { return v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--faint)' }

export default function BacktestTab({ markets }) {
  const [market, setMarket] = useState('A股')
  const [symbol, setSymbol] = useState('')
  const [horizon, setHorizon] = useState(20)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [bt, setBt] = useState(null)

  async function doBacktest() {
    if (!symbol.trim()) { setError('请先输入股票代码。'); return }
    setLoading(true); setError(''); setBt(null)
    try { setBt(await runBacktest(market, symbol.trim(), horizon)) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  // 用于柱状图归一化
  const maxBucketRet = bt ? Math.max(...bt.buckets.map((b) => Math.abs(b.avg_return || 0)), 0.5) : 1
  const bull = bt?.by_signal?.['看涨'] || {}
  const bear = bt?.by_signal?.['看跌'] || {}

  return (
    <>
      <div className="warning">
        🔬 <b>回测在做什么</b>:用过去约 4 年的数据,对历史上<b>每一天</b>都算出当时的信号,
        再看它之后 N 天<b>实际涨跌</b>,统计这套信号的真实命中率。这是判断"准不准"唯一靠谱的方式。
        回测不含交易成本,且<b>历史表现不代表未来</b>。
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
            <label>预测前瞻:{horizon} 个交易日</label>
            <input type="range" min="3" max="60" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
          </div>
          <button onClick={doBacktest} disabled={loading}>
            {loading ? <><span className="spinner" /> 回测中</> : '开始回测'}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {!bt && !loading && (
        <div className="placeholder">
          <div className="big">🔬</div>
          输入一只股票,检验这套打分信号过去预测涨跌的真实命中率。
        </div>
      )}

      {bt && (
        <div className="fade-in">
          <div className="bt-cards">
            <div className="bt-card">
              <div className="k">方向准确率</div>
              <div className="v" style={{ color: bt.directional_accuracy >= 50 ? 'var(--up)' : 'var(--down)' }}>
                {bt.directional_accuracy ?? '—'}%
              </div>
              <div className="hint" style={{ marginTop: 6 }}>{bt.directional_count} 个明确信号</div>
            </div>
            <div className="bt-card">
              <div className="k">看涨信号胜率</div>
              <div className="v">{bull.win_rate ?? '—'}%</div>
              <div className="hint" style={{ marginTop: 6 }}>共 {bull.count ?? 0} 次</div>
            </div>
            <div className="bt-card">
              <div className="k">看跌信号胜率</div>
              <div className="v">{bear.win_rate ?? '—'}%</div>
              <div className="hint" style={{ marginTop: 6 }}>共 {bear.count ?? 0} 次</div>
            </div>
            <div className="bt-card">
              <div className="k">基准(随机上涨率)</div>
              <div className="v" style={{ color: 'var(--muted)' }}>{bt.benchmark.up_rate}%</div>
              <div className="hint" style={{ marginTop: 6 }}>样本 {bt.samples}</div>
            </div>
          </div>

          <div className="panel">
            <h3 className="section-title">
              📊 分数分档 vs 之后{bt.horizon}日平均收益
              <span className="hint">理想情况:分数越高,之后收益越高(单调向上)</span>
            </h3>
            {bt.buckets.map((b) => (
              <div className="bucket-row" key={b.range}>
                <div className="label">{b.range}</div>
                <div className="track">
                  {b.avg_return != null && (
                    <div className="fill" style={{
                      width: `${Math.min(100, Math.abs(b.avg_return) / maxBucketRet * 100)}%`,
                      background: acolor(b.avg_return),
                    }} />
                  )}
                </div>
                <div className="val" style={{ color: acolor(b.avg_return || 0) }}>
                  {b.count ? `${b.avg_return > 0 ? '+' : ''}${b.avg_return}% · ${b.count}样本` : '无样本'}
                </div>
              </div>
            ))}
          </div>

          <div className="panel">
            <h3 className="section-title">📋 各信号详细表现</h3>
            <table>
              <thead><tr><th>信号</th><th>出现次数</th><th>胜率</th><th>平均收益</th><th>收益中位数</th></tr></thead>
              <tbody>
                {['看涨', '看跌', '中性'].map((sig) => {
                  const s = bt.by_signal[sig] || {}
                  if (!s.count) return <tr key={sig}><td>{sig}</td><td colSpan="4" className="hint">无样本</td></tr>
                  return (
                    <tr key={sig}>
                      <td><b>{sig}</b></td>
                      <td>{s.count}</td>
                      <td>{s.win_rate}%</td>
                      <td style={{ color: acolor(s.avg_return) }}>{s.avg_return > 0 ? '+' : ''}{s.avg_return}%</td>
                      <td style={{ color: acolor(s.median_return) }}>{s.median_return > 0 ? '+' : ''}{s.median_return}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <p className="hint" style={{ marginTop: 12 }}>
              回测区间:{bt.date_range[0]} ~ {bt.date_range[1]}。若方向准确率接近或低于基准,说明这套信号
              对该股该周期预测力有限 —— 这很正常,务必结合自身判断,不要盲目依赖。
            </p>
          </div>
        </div>
      )}
    </>
  )
}
