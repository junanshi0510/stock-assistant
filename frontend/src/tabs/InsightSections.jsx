import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import { fetchFundamentals, fetchMl, fetchNews, fetchCompare, fetchQuote } from '../api'

function useLazy(fetcher, market, symbol, trigger) {
  const [state, setState] = useState({ loading: false, error: '', data: null })
  useEffect(() => {
    if (!trigger || !symbol) return
    let alive = true
    setState({ loading: true, error: '', data: null })
    fetcher(market, symbol)
      .then((d) => alive && setState({ loading: false, error: '', data: d }))
      .catch((e) => alive && setState({ loading: false, error: e.message, data: null }))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trigger])
  return state
}

function tagClass(label) {
  return label === '利好' ? 'up' : label === '利空' ? 'down' : 'neutral'
}

function fmtMetric(v, suffix = '') {
  if (v === null || v === undefined || v === '') return '—'
  return `${v}${suffix}`
}

function fmtMoney(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return v
  if (Math.abs(n) >= 1e12) return `${(n / 1e12).toFixed(2)}万亿`
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(2)
}

/* ===== 行情快照 ===== */
export function QuoteSection({ market, symbol, trigger }) {
  const { loading, error, data } = useLazy(fetchQuote, market, symbol, trigger)
  return (
    <div className="panel">
      <h3 className="section-title">⚡ 行情快照 <span className="hint">真实单股行情源</span></h3>
      {loading && <div className="hint"><span className="spinner" /> 加载中…</div>}
      {error && <div className="hint">{error}</div>}
      {data && data.available && (
        <>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
            <span className={`badge ${data.change_pct > 0 ? 'up' : data.change_pct < 0 ? 'down' : 'neutral'}`}>
              {data.name || data.symbol} {data.price ?? '—'}
            </span>
            <span className={data.change_pct > 0 ? 'delta-pos' : data.change_pct < 0 ? 'delta-neg' : 'delta-zero'}>
              {data.change > 0 ? '+' : ''}{data.change ?? '—'} / {data.change_pct > 0 ? '+' : ''}{data.change_pct ?? '—'}%
            </span>
            <span className="hint">{data.source} · {data.as_of || '—'} {data.delay_note || ''}</span>
          </div>
          <div className="ind-grid">
            <div className="ind"><div className="k">今开</div><div className="v">{data.open ?? '—'}</div></div>
            <div className="ind"><div className="k">昨收</div><div className="v">{data.prev_close ?? '—'}</div></div>
            <div className="ind"><div className="k">最高</div><div className="v">{data.high ?? '—'}</div></div>
            <div className="ind"><div className="k">最低</div><div className="v">{data.low ?? '—'}</div></div>
            <div className="ind"><div className="k">成交额</div><div className="v">{fmtMoney(data.amount)}</div></div>
            <div className="ind"><div className="k">成交量</div><div className="v">{fmtMoney(data.volume)}</div></div>
            <div className="ind"><div className="k">买一</div><div className="v">{data.bid ?? '—'}</div></div>
            <div className="ind"><div className="k">卖一</div><div className="v">{data.ask ?? '—'}</div></div>
            <div className="ind"><div className="k">PE</div><div className="v">{data.pe ?? '—'}</div></div>
            <div className="ind"><div className="k">市值</div><div className="v">{fmtMoney(data.market_cap)}</div></div>
          </div>
        </>
      )}
    </div>
  )
}

/* ===== 基本面 ===== */
export function FundamentalsSection({ market, symbol, trigger }) {
  const { loading, error, data } = useLazy(fetchFundamentals, market, symbol, trigger)
  const enhanced = data?.enhanced
  const valuationItems = enhanced?.valuation_percentiles?.items || {}
  return (
    <div className="panel">
      <h3 className="section-title">🏦 基本面 <span className="hint">公司质地与估值(适合中长期)</span></h3>
      {loading && <div className="hint"><span className="spinner" /> 加载中…</div>}
      {error && <div className="hint">{error}</div>}
      {data && !data.available && <div className="hint">{data.message}</div>}
      {data && data.available && (
        <>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
            <span className={`badge ${data.score >= 60 ? 'up' : data.score <= 40 ? 'down' : 'neutral'}`}>
              {data.rating} {data.score}
            </span>
            <span className="hint">报告期 {data.as_of || '—'}</span>
          </div>
          <div className="ind-grid">
            {Object.entries(data.metrics).map(([k, v]) => (
              <div className="ind" key={k}><div className="k">{k}</div><div className="v">{v ?? '—'}</div></div>
            ))}
          </div>
          {enhanced && (
            <>
              <div className="fund-subhead">质量概览</div>
              <div className="bt-cards fund-cards">
                <div className="bt-card">
                  <div className="k">营收连续增长</div>
                  <div className="v">{fmtMetric(enhanced.growth_streaks?.revenue_years, '年')}</div>
                </div>
                <div className="bt-card">
                  <div className="k">净利润连续增长</div>
                  <div className="v">{fmtMetric(enhanced.growth_streaks?.profit_years, '年')}</div>
                </div>
                <div className="bt-card">
                  <div className="k">ROE趋势</div>
                  <div className="v">{fmtMetric(enhanced.trend_summary?.roe)}</div>
                </div>
                <div className="bt-card">
                  <div className="k">负债率趋势</div>
                  <div className="v">{fmtMetric(enhanced.trend_summary?.debt_ratio)}</div>
                </div>
              </div>
              {enhanced.trends?.length > 0 && (
                <>
                  <div className="fund-subhead">年度财务趋势 <span className="hint">金额单位:亿元</span></div>
                  <div className="corr-wrap">
                    <table className="fund-table">
                      <thead>
                        <tr>
                          <th>报告期</th><th>营收</th><th>净利润</th><th>ROE</th><th>毛利率</th><th>净利率</th><th>负债率</th><th>现金流质量</th>
                        </tr>
                      </thead>
                      <tbody>
                        {enhanced.trends.map((r) => (
                          <tr key={r.period}>
                            <td>{r.period}</td>
                            <td>{fmtMetric(r.revenue_yi)}</td>
                            <td>{fmtMetric(r.profit_yi)}</td>
                            <td>{fmtMetric(r.roe, '%')}</td>
                            <td>{fmtMetric(r.gross_margin, '%')}</td>
                            <td>{fmtMetric(r.net_margin, '%')}</td>
                            <td>{fmtMetric(r.debt_ratio, '%')}</td>
                            <td>{fmtMetric(r.cashflow_quality)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="hint" style={{ marginTop: 8 }}>{enhanced.cashflow_quality_note}</div>
                </>
              )}
              <div className="fund-subhead">估值历史分位 <span className="hint">{enhanced.valuation_percentiles?.window || ''}</span></div>
              <div className="bt-cards fund-cards">
                <div className="bt-card">
                  <div className="k">PE分位</div>
                  <div className="v">{fmtMetric(valuationItems.pe?.percentile, '%')}</div>
                  <div className="hint" style={{ marginTop: 6 }}>PE {fmtMetric(valuationItems.pe?.current)}</div>
                </div>
                <div className="bt-card">
                  <div className="k">PB分位</div>
                  <div className="v">{fmtMetric(valuationItems.pb?.percentile, '%')}</div>
                  <div className="hint" style={{ marginTop: 6 }}>PB {fmtMetric(valuationItems.pb?.current)}</div>
                </div>
                <div className="bt-card">
                  <div className="k">PS分位</div>
                  <div className="v">—</div>
                  <div className="hint" style={{ marginTop: 6 }}>
                    {enhanced.valuation_percentiles?.unavailable?.ps || '真实源暂未返回'}
                  </div>
                </div>
                <div className="bt-card">
                  <div className="k">估值日期</div>
                  <div className="v fund-date">{valuationItems.pe?.as_of || valuationItems.pb?.as_of || '—'}</div>
                  <div className="hint" style={{ marginTop: 6 }}>
                    样本 {valuationItems.pe?.sample_size || valuationItems.pb?.sample_size || '—'}
                  </div>
                </div>
              </div>
            </>
          )}
          {data.reasons?.length > 0 && (
            <table style={{ marginTop: 14 }}>
              <tbody>
                {data.reasons.map((r, i) => (
                  <tr key={i}>
                    <td style={{ width: 90 }}>{r.name}</td>
                    <td className={r.delta > 0 ? 'delta-pos' : r.delta < 0 ? 'delta-neg' : 'delta-zero'} style={{ width: 60 }}>
                      {r.delta > 0 ? `+${r.delta}` : r.delta}
                    </td>
                    <td className="hint" style={{ color: 'var(--text)' }}>{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}

/* ===== AI / 机器学习预测 ===== */
export function MLSection({ market, symbol, trigger }) {
  const { loading, error, data } = useLazy(fetchMl, market, symbol, trigger)
  return (
    <div className="panel">
      <h3 className="section-title">🤖 AI 模型预测 <span className="hint">梯度提升 · 严格样本外验证</span></h3>
      {loading && <div className="hint"><span className="spinner" /> 训练与验证中(约 1-3 秒)…</div>}
      {error && <div className="hint">{error}</div>}
      {data && (
        <>
          <div className="bt-cards" style={{ marginBottom: 14 }}>
            <div className="bt-card">
              <div className="k">最新上涨概率</div>
              <div className="v" style={{ color: data.latest_up_probability >= 50 ? 'var(--up)' : 'var(--down)' }}>
                {data.latest_up_probability}%
              </div>
              <div className="hint" style={{ marginTop: 6 }}>未来{data.horizon}日</div>
            </div>
            <div className="bt-card">
              <div className="k">样本外准确率</div>
              <div className="v">{data.test_accuracy}%</div>
              <div className="hint" style={{ marginTop: 6 }}>测试集 {data.test_size} 天</div>
            </div>
            <div className="bt-card">
              <div className="k">基准准确率</div>
              <div className="v" style={{ color: 'var(--muted)' }}>{data.baseline_accuracy}%</div>
              <div className="hint" style={{ marginTop: 6 }}>AUC {data.auc ?? '—'}</div>
            </div>
            <div className="bt-card">
              <div className="k">超越基准</div>
              <div className="v" style={{ color: data.edge_vs_baseline > 0 ? 'var(--up)' : 'var(--down)' }}>
                {data.edge_vs_baseline > 0 ? '+' : ''}{data.edge_vs_baseline}
              </div>
              <div className="hint" style={{ marginTop: 6 }}>百分点</div>
            </div>
          </div>
          <div className="warning" style={{ margin: 0 }}>
            <b>判定:{data.verdict}。</b> 只有「样本外准确率」明显高于「基准」时,模型才算真的有预测力。
            单股技术面模型通常和抛硬币差不多,请勿仅凭这个概率重仓。
          </div>
        </>
      )}
    </div>
  )
}

/* ===== 新闻情绪 ===== */
export function NewsSection({ market, symbol, trigger }) {
  const { loading, error, data } = useLazy(fetchNews, market, symbol, trigger)
  return (
    <div className="panel">
      <h3 className="section-title">📰 新闻情绪 <span className="hint">近期个股新闻 + 粗略情绪打分</span></h3>
      {loading && <div className="hint"><span className="spinner" /> 加载中…</div>}
      {error && <div className="hint">{error}</div>}
      {data && !data.available && <div className="hint">{data.message}</div>}
      {data && data.available && (
        <>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
            <span className={`badge ${data.score >= 60 ? 'up' : data.score <= 40 ? 'down' : 'neutral'}`}>
              {data.mood} {data.score}
            </span>
            <span className="hint">利好 {data.pos_count} · 利空 {data.neg_count} · 共 {data.total} 条</span>
          </div>
          <div className="news-list">
            {data.news.map((it, i) => (
              <a className="news-item" key={i} href={it.url || '#'} target="_blank" rel="noreferrer">
                <span className={`tag ${tagClass(it.label)}`}>{it.label}</span>
                <span className="news-title">{it.title}</span>
                <span className="news-meta">{it.source} {it.time?.slice(5, 16)}</span>
              </a>
            ))}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            ⚠️ 情绪分用关键词词典粗略统计,不理解语义/反讽,仅供参考。
          </p>
        </>
      )}
    </div>
  )
}

/* ===== 个股 vs 大盘 对比 ===== */
function RebasedChart({ rebased }) {
  const containerRef = useRef(null)
  useEffect(() => {
    if (!containerRef.current || !rebased || rebased.length === 0) return
    const chart = createChart(containerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#8896a8', fontSize: 11 },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.05)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)' },
      crosshair: { mode: 1 },
      autoSize: true,
    })
    const stock = chart.addLineSeries({ color: '#ff4d5e', lineWidth: 2, priceLineVisible: false })
    stock.setData(rebased.map((r) => ({ time: r.date, value: r.stock })))
    const index = chart.addLineSeries({ color: '#5b8cff', lineWidth: 2, priceLineVisible: false })
    index.setData(rebased.map((r) => ({ time: r.date, value: r.index })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [rebased])
  return <div ref={containerRef} className="chart" />
}

export function CompareSection({ market, symbol, trigger }) {
  const { loading, error, data } = useLazy(fetchCompare, market, symbol, trigger)
  const verdictClass = (v) =>
    v?.includes('跑赢') ? 'up' : v?.includes('跑输') ? 'down' : 'neutral'
  return (
    <div className="panel">
      <h3 className="section-title">⚖️ 个股 vs 大盘 <span className="hint">放进市场背景里看,判断更可信</span></h3>
      {loading && <div className="hint"><span className="spinner" /> 加载中…</div>}
      {error && <div className="hint">{error}</div>}
      {data && (
        <>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
            <span className={`badge ${verdictClass(data.verdict)}`}>{data.verdict}</span>
            <span className="hint">对比基准:{data.benchmark}</span>
          </div>
          <div className="bt-cards" style={{ marginBottom: 14 }}>
            <div className="bt-card">
              <div className="k">Beta(波动敏感度)</div>
              <div className="v">{data.beta ?? '—'}</div>
              <div className="hint" style={{ marginTop: 6 }}>&gt;1 比大盘更猛,&lt;1 更稳</div>
            </div>
            <div className="bt-card">
              <div className="k">相关性</div>
              <div className="v">{data.correlation ?? '—'}</div>
              <div className="hint" style={{ marginTop: 6 }}>与大盘同向程度(-1~1)</div>
            </div>
          </div>
          {data.periods?.length > 0 && (
            <table style={{ marginBottom: 14 }}>
              <thead><tr><th>周期</th><th>个股</th><th>大盘</th><th>超额收益</th></tr></thead>
              <tbody>
                {data.periods.map((p, i) => (
                  <tr key={i}>
                    <td>{p.period}</td>
                    <td className={p.stock > 0 ? 'delta-pos' : p.stock < 0 ? 'delta-neg' : 'delta-zero'}>{p.stock}%</td>
                    <td className={p.index > 0 ? 'delta-pos' : p.index < 0 ? 'delta-neg' : 'delta-zero'}>{p.index}%</td>
                    <td className={p.excess > 0 ? 'delta-pos' : p.excess < 0 ? 'delta-neg' : 'delta-zero'}>
                      {p.excess > 0 ? `+${p.excess}` : p.excess}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div style={{ display: 'flex', gap: 16, marginBottom: 8, flexWrap: 'wrap' }}>
            <span className="hint"><span style={{ color: '#ff4d5e' }}>━</span> 个股</span>
            <span className="hint"><span style={{ color: '#5b8cff' }}>━</span> {data.benchmark}</span>
            <span className="hint">(都从 100 起步,看谁走得更高)</span>
          </div>
          <RebasedChart rebased={data.rebased} />
        </>
      )}
    </div>
  )
}
