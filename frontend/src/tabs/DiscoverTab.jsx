import { useEffect, useState } from 'react'
import { fetchHot, scan } from '../api/market'
import { dirClass, scoreColor } from '../helpers'

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
  const [selected, setSelected] = useState([])
  const [healthLoading, setHealthLoading] = useState(false)
  const [healthError, setHealthError] = useState('')
  const [healthData, setHealthData] = useState(null)

  useEffect(() => {
    setSelected([])
    setHealthData(null)
    setHealthError('')
    load()
  }, [market, period, type])

  async function load() {
    setLoading(true); setError('')
    try { setData(await fetchHot(market, period, type, 50)) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  const items = data?.items || []
  const selectedSet = new Set(selected)
  const retrievedAt = data?.retrieved_at
    ? new Date(data.retrieved_at).toLocaleString('zh-CN', { hour12: false })
    : ''

  function toggleSelected(symbol) {
    setHealthData(null)
    setHealthError('')
    setSelected((current) => {
      if (current.includes(symbol)) return current.filter((item) => item !== symbol)
      if (current.length >= 10) {
        setHealthError('一次最多体检 10 只，避免外部行情请求过多。')
        return current
      }
      return [...current, symbol]
    })
  }

  function selectTop() {
    setSelected(items.slice(0, 10).map((item) => item.symbol))
    setHealthData(null)
    setHealthError('')
  }

  async function runHealthCheck() {
    if (selected.length === 0) {
      setHealthError('请先勾选至少一只股票。')
      return
    }
    setHealthLoading(true)
    setHealthError('')
    setHealthData(null)
    try {
      setHealthData(await scan(market, selected, 12))
    } catch (err) {
      setHealthError(err.message)
    } finally {
      setHealthLoading(false)
    }
  }

  function healthLabel(row) {
    const hot = items.find((item) => item.symbol === row.symbol)
    const change = Number(hot?.change_pct)
    if (type === 'gainers' && change >= 5 && row.score < 50) return '涨幅高但技术分偏低，谨防追高'
    if (type === 'gainers' && change >= 5) return '短期涨幅较高，需防回撤'
    if (type === 'losers' && change <= -5 && row.score >= 65) return '大跌后技术转强，仍需确认止跌'
    if (type === 'losers' && change <= -5) return '跌幅较大，先观察止跌结构'
    if (type === 'active' && row.score >= 65) return '成交活跃且技术偏强'
    return row.direction
  }

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
          <p className="hint" style={{ marginTop: -6, marginBottom: 12 }}>
            数据源：{data.source || '东方财富'}{retrievedAt ? ` · 获取于 ${retrievedAt}` : ''}
            {data.scope ? ` · 范围：${data.scope}` : ''}
          </p>
          {data.stale && (
            <div className="warning" style={{ marginBottom: 12 }}>
              ⚠️ {data.warning || '实时数据暂不可用，当前展示最近缓存。'}
            </div>
          )}
          {period !== '1d' && type !== 'active' && (
            <p className="hint" style={{ marginTop: -6, marginBottom: 12 }}>
              ⚠️ {PERIOD_LABELS[period]}涨跌榜是在「成交最活跃的股票」范围内按 {period === '7d' ? '7' : '30'} 日真实涨幅排序,不是全市场排名(全市场多日榜需付费数据源)。
            </p>
          )}
          {period !== '1d' && type === 'active' && (
            <p className="hint" style={{ marginTop: -6, marginBottom: 12 }}>
              成交活跃度按最新交易日排序；涨跌列按 {period === '7d' ? '7' : '30'} 个交易日的真实日K计算。
            </p>
          )}
          <div className="hot-health-toolbar">
            <div>
              <b>热门股量化体检</b>
              <span>已选 {selected.length}/10，只复用技术评分，不把热度当买入信号</span>
            </div>
            <div className="hot-health-actions">
              <button type="button" className="ghost" onClick={selectTop}>选择前 10</button>
              <button type="button" className="ghost" onClick={() => { setSelected([]); setHealthData(null) }} disabled={selected.length === 0}>清空</button>
              <button type="button" onClick={runHealthCheck} disabled={healthLoading || selected.length === 0}>
                {healthLoading ? <><span className="spinner" /> 体检中</> : `量化体检 (${selected.length})`}
              </button>
            </div>
          </div>
          {healthError && <div className="error" style={{ marginBottom: 12 }}>{healthError}</div>}
          <table>
            <thead>
              <tr>
                <th className="hot-select-col">选择</th>
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
                  <td className="hot-select-col" onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedSet.has(r.symbol)}
                      onChange={() => toggleSelected(r.symbol)}
                      aria-label={`选择 ${r.name || r.symbol}`}
                    />
                  </td>
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

      {healthData && (
        <div className="panel fade-in">
          <h3 className="section-title">
            🩺 热门股量化体检
            <span className="hint" style={{ marginLeft: 10 }}>
              成功 {healthData.count} 只{healthData.failed_count ? ` · 失败 ${healthData.failed_count} 只` : ''}
            </span>
          </h3>
          <div className="warning" style={{ marginBottom: 14 }}>
            热门榜反映市场关注或涨跌，不等于未来收益；技术评分也可能失效。这里用于排除明显冲突，不构成买卖建议。
          </div>
          <table>
            <thead>
              <tr><th>#</th><th>代码</th><th>名称</th><th>榜单涨跌</th><th>技术评分</th><th>估计概率</th><th>方向</th><th>风险标签</th></tr>
            </thead>
            <tbody>
              {healthData.results.map((row, index) => {
                const hot = items.find((item) => item.symbol === row.symbol) || {}
                return (
                  <tr key={row.symbol} className="clickable" onClick={() => goAnalyze(market, row.symbol)}>
                    <td>{index + 1}</td>
                    <td><b>{row.symbol}</b></td>
                    <td>{hot.name || '—'}</td>
                    <td className={hot.change_pct > 0 ? 'delta-pos' : hot.change_pct < 0 ? 'delta-neg' : 'delta-zero'}>
                      {hot.change_pct == null ? '—' : `${hot.change_pct > 0 ? '+' : ''}${Number(hot.change_pct).toFixed(2)}%`}
                    </td>
                    <td>
                      <div className="rank-score">
                        <span className="rank-num" style={{ color: scoreColor(row.score) }}>{row.score}</span>
                        <div className="bar"><div style={{ width: `${row.score}%`, background: scoreColor(row.score) }} /></div>
                      </div>
                    </td>
                    <td>{row.probability}%</td>
                    <td><span className={`badge ${dirClass(row.direction)}`}>{row.direction}</span></td>
                    <td className="hint" style={{ color: 'var(--text)' }}>{healthLabel(row)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {healthData.failed_count > 0 && (
            <p className="hint" style={{ marginTop: 12 }}>
              未完成：{healthData.failed.map((item) => item.symbol).join('、')}。可能是代码格式或行情源暂时不可用。
            </p>
          )}
        </div>
      )}
    </>
  )
}
