import { useEffect, useState } from 'react'
import { fetchHot } from '../api/market'

const TYPE_LABELS = {
  gainers: '📈 涨幅榜',
  losers: '📉 跌幅榜',
  active: '🔥 成交活跃',
}

const PERIOD_LABELS = {
  '1d': '今日',
  '7d': '近7日',
  '30d': '近30日',
}

// 热门股/涨跌幅榜 - 发现有潜力的股票
export default function DiscoverTab({ markets, goAnalyze }) {
  const [market, setMarket] = useState('A股')
  const [period, setPeriod] = useState('1d')
  const [type, setType] = useState('gainers')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)

  useEffect(() => { load() }, [market, period, type])

  async function load() {
    setLoading(true); setError('')
    try { setData(await fetchHot(market, period, type, 50)) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  const items = data?.items || []

  return (
    <>
      <div className="panel">
        <h3 className="section-title">
          🔍 发现股票 <span className="hint">实时热门榜单,快速找到市场焦点</span>
        </h3>
        <div className="form-row" style={{ marginTop: 14 }}>
          <div className="field">
            <label>市场</label>
            <select value={market} onChange={(e) => setMarket(e.target.value)}>
              {markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field">
            <label>周期</label>
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              {Object.entries(PERIOD_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>类型</label>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {Object.entries(TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <button onClick={load} disabled={loading} className="ghost">
            {loading ? <><span className="spinner" /> 加载中</> : '🔄 刷新'}
          </button>
        </div>
        {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
      </div>

      {loading && items.length === 0 && (
        <div className="placeholder">
          <div className="big">⏳</div>
          加载中…
        </div>
      )}

      {!loading && items.length === 0 && !error && (
        <div className="placeholder">
          <div className="big">📭</div>
          暂无数据
        </div>
      )}

      {items.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">
            {TYPE_LABELS[type]} · {market} · {PERIOD_LABELS[period]}
            <span className="hint" style={{ marginLeft: 10 }}>
              {data.count} 只 · 点击任意一行查看详细分析
            </span>
          </h3>
          {period !== '1d' && type !== 'active' && (
            <p className="hint" style={{ marginTop: -6, marginBottom: 12 }}>
              ⚠️ {PERIOD_LABELS[period]}涨跌榜是在「成交最活跃的股票」范围内按 {period === '7d' ? '7' : '30'} 日真实涨幅排序,不是全市场排名(全市场多日榜需付费数据源)。
            </p>
          )}
          <table>
            <thead>
              <tr>
                <th className="rank-idx">#</th>
                <th>代码</th>
                <th>名称</th>
                <th>最新价</th>
                <th>{period === '1d' ? '今日涨跌' : PERIOD_LABELS[period] + '涨跌'}</th>
                <th>成交量</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r, i) => (
                <tr key={r.symbol} className="clickable" onClick={() => goAnalyze(market, r.symbol)}>
                  <td className="rank-idx">{i + 1}</td>
                  <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                  <td className="hint" style={{ color: 'var(--text)' }}>{r.name}</td>
                  <td>{r.price != null ? r.price.toFixed(2) : '—'}</td>
                  <td className={r.change_pct > 0 ? 'delta-pos' : r.change_pct < 0 ? 'delta-neg' : 'delta-zero'}>
                    {r.change_pct != null
                      ? `${r.change_pct > 0 ? '+' : ''}${r.change_pct.toFixed(2)}%`
                      : '—'}
                  </td>
                  <td className="hint">{r.volume ? r.volume.toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
