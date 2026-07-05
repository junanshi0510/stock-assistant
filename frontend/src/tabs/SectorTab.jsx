import { useEffect, useMemo, useState } from 'react'
import { fetchSectors } from '../api'

function pct(v) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function num(v, suffix = '') {
  if (v == null) return '—'
  return `${Number(v).toFixed(2)}${suffix}`
}

function deltaClass(v) {
  if (v > 0) return 'delta-pos'
  if (v < 0) return 'delta-neg'
  return 'delta-zero'
}

function DriverTag({ driver }) {
  const label = driver?.label || '数据不足'
  const cls = driver?.concept_hype ? 'neutral' : driver?.profit_supported ? 'up' : 'down'
  return <span className={`tag ${cls}`}>{label}</span>
}

function StockTable({ title, rows, goAnalyze }) {
  return (
    <div>
      <h4 className="fund-subhead">{title}</h4>
      <div className="corr-wrap">
        <table className="compact-table sector-stock-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>涨跌幅</th>
              <th>换手</th>
              <th>成交额</th>
              <th>PE</th>
              <th>归因</th>
              <th>盈利证据</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${title}-${r.symbol}`} className="clickable" onClick={() => goAnalyze('A股', r.symbol)}>
                <td style={{ fontWeight: 700 }}>{r.symbol}</td>
                <td>{r.name}</td>
                <td className={deltaClass(r.change_pct)}>{pct(r.change_pct)}</td>
                <td>{num(r.turnover, '%')}</td>
                <td>{r.amount != null ? `${(r.amount / 100000000).toFixed(2)}亿` : '—'}</td>
                <td>{r.pe_ttm != null && r.pe_ttm > 0 ? r.pe_ttm.toFixed(1) : '—'}</td>
                <td><DriverTag driver={r.driver} /></td>
                <td className="hint">
                  {r.profit
                    ? `净利${num(r.profit.net_profit_yi, '亿')} / ROE ${num(r.profit.roe, '%')}`
                    : r.pe_ttm != null && r.pe_ttm > 0
                      ? `PE(TTM) ${r.pe_ttm.toFixed(1)}，TTM盈利为正`
                      : '未取到可验证盈利指标'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function SectorTab({ goAnalyze }) {
  const [sectorLimit, setSectorLimit] = useState(12)
  const [stockLimit, setStockLimit] = useState(8)
  const [includeConcepts, setIncludeConcepts] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState('')

  async function load() {
    setLoading(true); setError('')
    try {
      const next = await fetchSectors('A股', sectorLimit, stockLimit, includeConcepts)
      setData(next)
      setSelected((cur) => cur || next.industries?.items?.[0]?.name || '')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const sectors = data?.industries?.items || []
  const activeSector = useMemo(
    () => sectors.find((s) => s.name === selected) || sectors[0],
    [sectors, selected],
  )

  return (
    <>
      <div className="panel">
        <h3 className="section-title">
          板块热点 <span className="hint">行业热度、板块内热门股、概念源状态与上涨/下跌归因</span>
        </h3>
        <div className="form-row">
          <div className="field">
            <label>热门行业数量</label>
            <input type="number" min="5" max="30" value={sectorLimit}
              onChange={(e) => setSectorLimit(Number(e.target.value))} />
          </div>
          <div className="field">
            <label>每板块股票数</label>
            <input type="number" min="3" max="15" value={stockLimit}
              onChange={(e) => setStockLimit(Number(e.target.value))} />
          </div>
          <label className="toggle-line">
            <input type="checkbox" checked={includeConcepts} onChange={(e) => setIncludeConcepts(e.target.checked)} />
            拉取概念板块
          </label>
          <button onClick={load} disabled={loading}>
            {loading ? <><span className="spinner" /> 加载中</> : '刷新板块'}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {loading && !data && (
        <div className="placeholder">
          <div className="big">⌛</div>
          正在拉取真实板块行情与财务指标
        </div>
      )}

      {data && (
        <>
          <div className="panel fade-in">
            <h3 className="section-title">
              数据口径 <span className="hint">{data.as_of}</span>
            </h3>
            <div className="bt-cards quality-cards">
              <div className="bt-card">
                <div className="k">行业数量</div>
                <div className="v">{data.industries.sector_count}</div>
              </div>
              <div className="bt-card">
                <div className="k">报价成功/分类股票</div>
                <div className="v">{data.industries.stock_count}/{data.industries.classification_stock_count || data.industries.stock_count}</div>
              </div>
              <div className="bt-card">
                <div className="k">行业分类日</div>
                <div className="v fund-date">{data.industries.classification_date || '—'}</div>
              </div>
              <div className="bt-card">
                <div className="k">行情时间</div>
                <div className="v fund-date">{data.industries.quote_time || '—'}</div>
              </div>
            </div>
            <p className="hint">{data.industries.source}。{data.method.driver}</p>
            {data.industries.quote_missing_count > 0 && (
              <p className="hint" style={{ marginTop: 8 }}>
                有 {data.industries.quote_missing_count} 只股票本次未取到腾讯实时报价，已从板块统计中剔除；页面只展示成功返回的真实报价。
              </p>
            )}
          </div>

          <div className="panel fade-in">
            <h3 className="section-title">热门行业</h3>
            <div className="corr-wrap">
              <table className="sector-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>行业</th>
                    <th>热度分</th>
                    <th>平均涨跌</th>
                    <th>上涨占比</th>
                    <th>上涨/下跌</th>
                    <th>成交额</th>
                    <th>领涨股</th>
                  </tr>
                </thead>
                <tbody>
                  {sectors.map((s, i) => (
                    <tr key={s.name} className="clickable" onClick={() => setSelected(s.name)}>
                      <td className="rank-idx">{i + 1}</td>
                      <td style={{ fontWeight: 800 }}>{s.name}</td>
                      <td>{num(s.heat_score)}</td>
                      <td className={deltaClass(s.avg_change_pct)}>{pct(s.avg_change_pct)}</td>
                      <td>{num(s.up_ratio, '%')}</td>
                      <td>{s.up_count}/{s.down_count}</td>
                      <td>{num(s.total_amount_yi, '亿')}</td>
                      <td className="hint">{s.leaders.slice(0, 3).map((r) => `${r.name}${pct(r.change_pct)}`).join('、')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {activeSector && (
            <div className="panel fade-in">
              <h3 className="section-title">
                {activeSector.name} <span className="hint">
                  {activeSector.stock_count} 只股票 · 平均涨跌 {pct(activeSector.avg_change_pct)} · 上涨占比 {num(activeSector.up_ratio, '%')}
                </span>
              </h3>
              <StockTable title="板块内热门股" rows={activeSector.leaders || []} goAnalyze={goAnalyze} />
              <StockTable title="板块内下跌股" rows={activeSector.laggards || []} goAnalyze={goAnalyze} />
            </div>
          )}

          <div className="panel fade-in">
            <h3 className="section-title">
              概念板块 <span className="hint">{data.concepts.source}</span>
            </h3>
            {data.concepts.fallback_reason && (
              <div className="warning">
                {data.concepts.fallback_reason}
              </div>
            )}
            {!data.concepts.available && (
              <div className="warning">
                {data.concepts.error}
              </div>
            )}
            {data.concepts.available && data.concepts.mode === 'timeline' && (
              <div className="corr-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>概念</th>
                      <th>日期</th>
                      <th>成分股</th>
                      <th>龙头股</th>
                      <th>驱动事件</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.concepts.items.map((c, i) => (
                      <tr key={c.code || c.name}>
                        <td className="rank-idx">{i + 1}</td>
                        <td style={{ fontWeight: 800 }}>{c.name}</td>
                        <td>{c.date || '—'}</td>
                        <td>{c.stock_count || '—'}</td>
                        <td>{c.leader || '—'}</td>
                        <td className="hint" style={{ color: 'var(--text)' }}>{c.event || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="hint" style={{ marginTop: 10 }}>
                  当前为同花顺真实概念时间表，口径是新增/事件驱动概念；东方财富恢复后会自动切回概念涨跌榜。
                </p>
              </div>
            )}
            {data.concepts.available && data.concepts.mode !== 'timeline' && (
              <div className="corr-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>概念</th>
                      <th>涨跌幅</th>
                      <th>上涨/下跌</th>
                      <th>领涨股</th>
                      <th>领涨幅</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.concepts.items.map((c, i) => (
                      <tr key={c.code || c.name}>
                        <td className="rank-idx">{i + 1}</td>
                        <td style={{ fontWeight: 800 }}>{c.name}</td>
                        <td className={deltaClass(c.change_pct)}>{pct(c.change_pct)}</td>
                        <td>{c.up_count}/{c.down_count}</td>
                        <td>{c.leader || '—'}</td>
                        <td className={deltaClass(c.leader_change_pct)}>{pct(c.leader_change_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
