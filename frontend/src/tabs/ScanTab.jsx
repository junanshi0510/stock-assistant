import { useEffect, useState } from 'react'
import { scan, fetchPresets } from '../api'
import { dirClass, scoreColor } from '../helpers'

export default function ScanTab({ markets, goAnalyze }) {
  const [market, setMarket] = useState('A股')
  const [text, setText] = useState('')
  const [months, setMonths] = useState(12)
  const [presets, setPresets] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)

  useEffect(() => { fetchPresets().then((d) => setPresets(d.presets || {})).catch(() => {}) }, [])

  const presetList = presets[market] || []

  function loadPreset() {
    setText(presetList.map((p) => p.symbol).join(', '))
  }

  async function doScan() {
    const symbols = text.split(/[\s,，、]+/).map((s) => s.trim()).filter(Boolean)
    if (symbols.length === 0) { setError('请先输入或选择股票代码。'); return }
    setLoading(true); setError(''); setData(null)
    try { setData(await scan(market, symbols, months)) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  const nameOf = (sym) => (presetList.find((p) => p.symbol === sym) || {}).name || ''

  return (
    <>
      <div className="panel">
        <div className="form-row">
          <div className="field">
            <label>市场</label>
            <select value={market} onChange={(e) => { setMarket(e.target.value); setData(null) }}>
              {markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field">
            <label>回溯 {months} 个月</label>
            <input type="range" min="6" max="36" value={months} onChange={(e) => setMonths(Number(e.target.value))} />
          </div>
          <button onClick={doScan} disabled={loading}>
            {loading ? <><span className="spinner" /> 扫描中</> : '批量扫描'}
          </button>
        </div>

        <div className="field" style={{ marginTop: 14 }}>
          <label>股票代码列表(逗号/空格分隔,最多 40 只)</label>
          <textarea value={text} placeholder="例如:600519, 000001, 002594 …"
            onChange={(e) => setText(e.target.value)} />
        </div>

        {presetList.length > 0 && (
          <div className="chips">
            <span className="chip on" onClick={loadPreset}>⚡ 一键载入{market}预设股票池</span>
            {presetList.map((p) => (
              <span key={p.symbol} className="chip"
                onClick={() => setText((t) => (t ? t + ', ' : '') + p.symbol)}>
                {p.name} {p.symbol}
              </span>
            ))}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      {!data && !loading && (
        <div className="placeholder">
          <div className="big">🔍</div>
          一次扫描一批股票,按「看涨打分」从高到低排序 —— 一眼看出哪些信号最强、哪些最弱。
        </div>
      )}

      {data && (
        <div className="panel fade-in">
          <h3 className="section-title">
            🏆 扫描结果 <span className="hint">{data.count} 只成功{data.failed_count ? `,${data.failed_count} 只失败` : ''} · 点击任意一行查看详细分析</span>
          </h3>
          <table>
            <thead>
              <tr><th className="rank-idx">#</th><th>代码</th><th>名称</th><th style={{ width: 240 }}>看涨打分</th><th>上涨概率</th><th>方向</th></tr>
            </thead>
            <tbody>
              {data.results.map((r, i) => (
                <tr key={r.symbol} className="clickable" onClick={() => goAnalyze(data.market, r.symbol)}>
                  <td className="rank-idx">{i + 1}</td>
                  <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                  <td className="hint" style={{ color: 'var(--text)' }}>{nameOf(r.symbol)}</td>
                  <td>
                    <div className="rank-score">
                      <span className="rank-num" style={{ color: scoreColor(r.score) }}>{r.score}</span>
                      <div className="bar"><div style={{ width: `${r.score}%`, background: scoreColor(r.score) }} /></div>
                    </div>
                  </td>
                  <td>{r.probability}%</td>
                  <td><span className={`badge ${dirClass(r.direction)}`} style={{ fontSize: 12, padding: '3px 10px' }}>{r.direction}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.failed_count > 0 && (
            <p className="hint" style={{ marginTop: 12 }}>
              失败:{data.failed.map((f) => f.symbol).join(', ')}(可能是代码有误或该源暂时抓不到)
            </p>
          )}
        </div>
      )}
    </>
  )
}
