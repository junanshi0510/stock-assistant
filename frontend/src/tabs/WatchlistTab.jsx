import { useEffect, useState } from 'react'
import { clearAlerts, fetchAlerts, fetchWatchlist, removeWatch } from '../api/portfolio'
import { dirClass, scoreColor } from '../helpers'

// 自选股:从本地数据库读取收藏的股票,并显示每只的当前打分。
// 关掉重开、重启后端都还在(数据存在 backend/stock_assistant.db)。
export default function WatchlistTab({ goAnalyze }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [alertsLoading, setAlertsLoading] = useState(false)

  async function load() {
    setLoading(true); setError('')
    try { setData(await fetchWatchlist()) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  async function loadAlerts() {
    setAlertsLoading(true)
    try { setAlerts((await fetchAlerts(20)).alerts || []) }
    catch (e) { setError(e.message) } finally { setAlertsLoading(false) }
  }

  useEffect(() => { load(); loadAlerts() }, [])

  async function doRemove(e, item) {
    e.stopPropagation()  // 别触发行的跳转
    try {
      await removeWatch(item.market, item.symbol)
      setData((d) => ({
        ...d,
        items: d.items.filter((x) => !(x.market === item.market && x.symbol === item.symbol)),
        count: d.count - 1,
      }))
    } catch (err) { setError(err.message) }
  }

  async function doClearAlerts() {
    try {
      await clearAlerts()
      setAlerts([])
    } catch (err) { setError(err.message) }
  }

  const items = data?.items || []

  const alertBadgeClass = (type) => {
    if (type === 'bullish') return 'up'
    if (type === 'bearish') return 'down'
    return 'neutral'
  }

  return (
    <>
      {alerts.length > 0 && (
        <div className="panel" style={{ background: 'rgba(255,193,7,0.08)', borderLeft: '3px solid #ffc107' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 className="section-title" style={{ margin: 0 }}>🔔 打分变化提醒</h3>
            <button className="ghost" onClick={doClearAlerts}>清空已读</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {alerts.slice(0, 10).map((a, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', background: 'var(--bg)', borderRadius: 6 }}>
                <span className={`badge ${alertBadgeClass(a.event_type)}`} style={{ fontSize: 11, padding: '2px 8px', minWidth: 50 }}>
                  {a.event_type === 'bullish' ? '看涨' : a.event_type === 'bearish' ? '看跌' : '中性'}
                </span>
                <span style={{ fontWeight: 600 }}>{a.market} {a.symbol}</span>
                <span className="hint" style={{ flex: 1 }}>{a.message}</span>
                <span className="hint" style={{ fontSize: 11 }}>{a.triggered_at.slice(5, 16)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <div className="form-row" style={{ justifyContent: 'space-between' }}>
          <div className="hint">
            ⭐ 你收藏的股票（本地保存,关机重开都在）· 每只显示当前看涨打分
          </div>
          <button className="ghost" onClick={load} disabled={loading}>
            {loading ? <><span className="spinner" /> 刷新中</> : '🔄 刷新打分'}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {!loading && items.length === 0 && (
        <div className="placeholder">
          <div className="big">⭐</div>
          还没有自选股。去「单股分析」页分析一只股票,点标题旁的 ☆ 就能收藏到这里。
        </div>
      )}

      {items.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">
            ⭐ 我的自选 <span className="hint">{data.count} 只 · 点击任意一行查看详细分析</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>市场</th><th>代码</th><th>名称</th>
                <th style={{ width: 220 }}>看涨打分</th><th>上涨概率</th><th>方向</th><th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={`${r.market}-${r.symbol}`} className="clickable"
                  onClick={() => goAnalyze(r.market, r.symbol)}>
                  <td className="hint" style={{ color: 'var(--text)' }}>{r.market}</td>
                  <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                  <td className="hint" style={{ color: 'var(--text)' }}>{r.name || '—'}</td>
                  {r.error ? (
                    <td colSpan={3} className="hint">抓取失败:{r.error}</td>
                  ) : (
                    <>
                      <td>
                        <div className="rank-score">
                          <span className="rank-num" style={{ color: scoreColor(r.score) }}>{r.score}</span>
                          <div className="bar"><div style={{ width: `${r.score}%`, background: scoreColor(r.score) }} /></div>
                        </div>
                      </td>
                      <td>{r.probability}%</td>
                      <td><span className={`badge ${dirClass(r.direction)}`} style={{ fontSize: 12, padding: '3px 10px' }}>{r.direction}</span></td>
                    </>
                  )}
                  <td>
                    <button className="ghost" title="取消收藏"
                      onClick={(e) => doRemove(e, r)}
                      style={{ padding: '4px 10px' }}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
