import { useEffect, useMemo, useRef, useState } from 'react'
import { FileSpreadsheet, ImageUp, Layers3 } from 'lucide-react'
import { createHoldingsExposureSnapshot, deleteHolding, fetchHoldings, fetchHoldingsInsights, parseHoldingsText, previewHoldingsFile, saveHoldings, uploadHoldingScreenshot } from '../api/portfolio'

function num(v, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return '-'
  return Number(v).toFixed(digits)
}

function pct(v) {
  if (v == null || Number.isNaN(Number(v))) return '-'
  return `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function plainPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '-'
  return `${Number(v).toFixed(2)}%`
}

function pctRange(lower, upper) {
  if (lower == null || upper == null) return '-'
  return `${plainPct(lower)} - ${plainPct(upper)}`
}

function cls(v) {
  if (v > 0) return 'delta-pos'
  if (v < 0) return 'delta-neg'
  return 'delta-zero'
}

function sourceLabel(source) {
  if (source === 'tiantian_fund_export') return '天天基金导出'
  if (source === 'holdings_file_import') return '持仓账单导入'
  if (source === 'manual') return '手动录入'
  if (String(source || '').includes('ocr')) return '截图识别'
  return source || '-'
}

const blankCandidate = {
  asset_type: 'fund',
  market: '基金',
  code: '',
  name: '',
  amount: null,
  cost: null,
  yesterday_profit: null,
  profit: null,
  profit_rate: null,
  shares: null,
  source: 'manual',
  raw_text: '',
}

export default function HoldingsTab() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [ocrLoading, setOcrLoading] = useState(false)
  const [fileLoading, setFileLoading] = useState(false)
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState([])
  const [warnings, setWarnings] = useState([])
  const [filePreview, setFilePreview] = useState(null)
  const [insights, setInsights] = useState(null)
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [exposure, setExposure] = useState(null)
  const [exposureLoading, setExposureLoading] = useState(false)
  const screenshotInputRef = useRef(null)
  const holdingFileInputRef = useRef(null)

  async function load() {
    setLoading(true); setError('')
    try { setData(await fetchHoldings()) }
    catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  const items = data?.items || []
  const summary = data?.summary || {}

  const totalProfit = useMemo(
    () => items.reduce((sum, item) => sum + (item.profit || 0), 0),
    [items],
  )
  const totalYesterdayProfit = useMemo(
    () => items.reduce((sum, item) => sum + (item.yesterday_profit || 0), 0),
    [items],
  )

  function updateParsed(index, key, value) {
    setParsed((rows) => rows.map((row, idx) => {
      if (idx !== index) return row
      const next = { ...row, [key]: ['amount', 'cost', 'yesterday_profit', 'profit', 'profit_rate', 'shares'].includes(key) ? (value === '' ? null : Number(value)) : value }
      if (key === 'asset_type') next.market = value === 'fund' ? '基金' : next.market
      return next
    }))
  }

  async function doParseText() {
    setError(''); setWarnings([])
    try {
      const result = await parseHoldingsText(text)
      setParsed(result.candidates || [])
      setWarnings(result.warnings || [])
      setFilePreview(null)
    } catch (e) {
      setError(e.message)
    }
  }

  async function doUpload(file) {
    if (!file) return
    setOcrLoading(true); setError(''); setWarnings([])
    try {
      const result = await uploadHoldingScreenshot(file)
      setText(result.raw_text || '')
      setParsed(result.candidates || [])
      setWarnings(result.warnings || [])
      setFilePreview(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setOcrLoading(false)
    }
  }

  async function doPreviewHoldingFile(file) {
    if (!file) return
    setFileLoading(true); setError(''); setWarnings([])
    try {
      const result = await previewHoldingsFile(file)
      setParsed(result.candidates || [])
      setWarnings(result.warnings || [])
      setFilePreview(result)
      setText('')
    } catch (e) {
      setError(e.message || '持仓账单预览失败')
      setFilePreview(null)
    } finally {
      setFileLoading(false)
    }
  }

  async function doSaveParsed() {
    const rows = parsed.filter((r) => r.code && r.asset_type)
    if (!rows.length) {
      setError('没有可保存的持仓')
      return
    }
    setError('')
    try {
      const result = await saveHoldings(rows)
      setData({ items: result.items, summary: result.summary })
      setParsed([])
      setWarnings([])
      setFilePreview(null)
      if (holdingFileInputRef.current) holdingFileInputRef.current.value = ''
    } catch (e) {
      setError(e.message)
    }
  }

  async function doAddBlank() {
    setFilePreview(null)
    setParsed((rows) => [{ ...blankCandidate }, ...rows])
  }

  async function doDelete(id) {
    try {
      await deleteHolding(id)
      await load()
    } catch (e) {
      setError(e.message)
    }
  }

  async function loadInsights() {
    setInsightsLoading(true); setError('')
    try {
      setInsights(await fetchHoldingsInsights(6))
    } catch (e) {
      setError(e.message)
    } finally {
      setInsightsLoading(false)
    }
  }

  async function loadExposure() {
    setExposureLoading(true); setError('')
    try {
      setExposure(await createHoldingsExposureSnapshot())
    } catch (e) {
      setError(e.message || '基金穿透真实数据获取失败')
    } finally {
      setExposureLoading(false)
    }
  }

  return (
    <>
      <div className="panel">
        <h3 className="section-title">我的持仓 <span className="hint">上传截图识别或手动录入，保存前请核对识别结果</span></h3>
        <div className="warning" style={{ marginBottom: 14 }}>
          截图可能包含姓名、手机号、账号或资产隐私。建议上传前先打码；系统第一版只保存确认后的持仓结果，不长期保存原图。
        </div>
        <div className="form-row">
          <input ref={screenshotInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={(e) => doUpload(e.target.files?.[0])} />
          <input ref={holdingFileInputRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" style={{ display: 'none' }} onChange={(e) => doPreviewHoldingFile(e.target.files?.[0])} />
          <button className="ghost" disabled={ocrLoading} onClick={() => screenshotInputRef.current?.click()} title="选择持仓截图">
            <ImageUp size={16} aria-hidden="true" />
            <span>{ocrLoading ? 'OCR识别中' : '选择截图'}</span>
          </button>
          <button className="ghost" disabled={fileLoading} onClick={() => holdingFileInputRef.current?.click()} title="导入持仓 CSV 或 Excel 账单">
            <FileSpreadsheet size={16} aria-hidden="true" />
            <span>{fileLoading ? '账单解析中' : '导入持仓账单'}</span>
          </button>
          <button className="ghost" onClick={doAddBlank}>手动添加一行</button>
          <button className="ghost" onClick={load} disabled={loading}>{loading ? '刷新中' : '刷新持仓'}</button>
          <button onClick={loadInsights} disabled={insightsLoading || items.length === 0}>
            {insightsLoading ? <><span className="spinner" /> 组合体检中</> : '组合体检'}
          </button>
          <button className="ghost" onClick={loadExposure} disabled={exposureLoading || items.length === 0} title="按基金定期报告查看披露重仓股和行业">
            <Layers3 size={16} aria-hidden="true" />
            <span>{exposureLoading ? '快照生成中' : '生成穿透快照'}</span>
          </button>
        </div>
        {filePreview && (
          <div className="warning" style={{ marginTop: 12 }}>
            已识别为{filePreview.template?.label || '持仓账单'}：{filePreview.candidates?.length || 0} 条待确认。{filePreview.privacy}
          </div>
        )}
        {error && <div className="error">{error}</div>}
        {warnings.map((w, idx) => <div className="hint" key={idx} style={{ marginTop: 8 }}>{w}</div>)}
      </div>

      <div className="panel fade-in">
        <h3 className="section-title">粘贴识别文本 <span className="hint">截图 OCR 不可用时，可以用手机/系统识别文字后粘贴</span></h3>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="粘贴基金/股票持仓截图识别出的文字，例如基金名称、代码、持有金额、收益率..."
          style={{ minHeight: 110 }}
        />
        <div className="form-row" style={{ marginTop: 12 }}>
          <button onClick={doParseText}>解析文本</button>
          {parsed.length > 0 && <button onClick={doSaveParsed}>保存识别结果</button>}
        </div>
      </div>

      {parsed.length > 0 && (
        <div className="panel fade-in">
          <h3 className="section-title">{filePreview ? `${filePreview.template?.label || '持仓账单'}预览` : '待确认持仓'} <span className="hint">{parsed.length} 条 · 保存前可以直接修改</span></h3>
          {filePreview?.errors?.length > 0 && <div className="error" style={{ marginBottom: 12 }}>未纳入导入：{filePreview.errors.slice(0, 5).map((row) => `第 ${row.row} 行 ${row.message}`).join('；')}</div>}
          <div className="corr-wrap">
            <table className="compact-table holdings-edit-table">
              <thead>
                <tr>
                  <th>类型</th><th>市场</th><th>代码</th><th>名称</th><th>金额</th><th>成本</th><th>昨日收益</th><th>持仓收益</th><th>收益率</th><th>份额</th><th></th>
                </tr>
              </thead>
              <tbody>
                {parsed.map((row, idx) => (
                  <tr key={`${row.code}-${idx}`}>
                    <td>
                      <select value={row.asset_type || 'fund'} onChange={(e) => updateParsed(idx, 'asset_type', e.target.value)}>
                        <option value="fund">基金</option>
                        <option value="stock">股票</option>
                      </select>
                    </td>
                    <td><input value={row.market || ''} onChange={(e) => updateParsed(idx, 'market', e.target.value)} /></td>
                    <td><input value={row.code || ''} onChange={(e) => updateParsed(idx, 'code', e.target.value)} /></td>
                    <td><input value={row.name || ''} onChange={(e) => updateParsed(idx, 'name', e.target.value)} /></td>
                    <td><input type="number" value={row.amount ?? ''} onChange={(e) => updateParsed(idx, 'amount', e.target.value)} /></td>
                    <td><input type="number" value={row.cost ?? ''} onChange={(e) => updateParsed(idx, 'cost', e.target.value)} /></td>
                    <td><input type="number" value={row.yesterday_profit ?? ''} onChange={(e) => updateParsed(idx, 'yesterday_profit', e.target.value)} /></td>
                    <td><input type="number" value={row.profit ?? ''} onChange={(e) => updateParsed(idx, 'profit', e.target.value)} /></td>
                    <td><input type="number" value={row.profit_rate ?? ''} onChange={(e) => updateParsed(idx, 'profit_rate', e.target.value)} /></td>
                    <td><input type="number" value={row.shares ?? ''} onChange={(e) => updateParsed(idx, 'shares', e.target.value)} /></td>
                    <td><button className="ghost" onClick={() => setParsed((rows) => rows.filter((_, i) => i !== idx))}>删除</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filePreview && <p className="hint" style={{ marginTop: 12 }}>来源：{filePreview.template?.label || '用户导出持仓账单'}。确认保存后只保留核对过的持仓字段与来源。</p>}
        </div>
      )}

      <div className="panel fade-in">
        <h3 className="section-title">持仓构成 <span className="hint">{summary.count || 0} 条持仓</span></h3>
        <div className="bt-cards quality-cards">
          <div className="bt-card"><div className="k">总金额</div><div className="v">{summary.total_amount != null ? num(summary.total_amount) : '-'}</div></div>
          <div className="bt-card"><div className="k">昨日收益</div><div className={`v ${cls(totalYesterdayProfit)}`}>{num(totalYesterdayProfit)}</div></div>
          <div className="bt-card"><div className="k">累计收益</div><div className={`v ${cls(totalProfit)}`}>{num(totalProfit)}</div></div>
          <div className="bt-card"><div className="k">最大单一占比</div><div className="v">{pct(summary.top_concentration)}</div></div>
          <div className="bt-card"><div className="k">风险提示</div><div className="v">{summary.risk_notes?.length || 0}</div></div>
        </div>
        {summary.risk_notes?.length > 0 && (
          <div className="fund-bond-list" style={{ marginTop: 12 }}>
            {summary.risk_notes.map((note, idx) => <span className="tag neutral" key={idx}>{note}</span>)}
          </div>
        )}
      </div>

      {insights && (
        <div className="panel fade-in">
          <h3 className="section-title">
            组合体检 <span className="hint">{insights.source}</span>
          </h3>
          <div className="bt-cards quality-cards">
            <div className="bt-card"><div className="k">持仓数量</div><div className="v">{insights.summary.holding_count}</div></div>
            <div className="bt-card"><div className="k">总金额</div><div className="v">{num(insights.summary.total_amount)}</div></div>
            <div className="bt-card"><div className="k">累计收益率</div><div className={`v ${cls(insights.summary.weighted_profit_rate)}`}>{pct(insights.summary.weighted_profit_rate)}</div></div>
            <div className="bt-card"><div className="k">第一大占比</div><div className="v">{pct(insights.summary.top1_ratio)}</div></div>
            <div className="bt-card"><div className="k">前三大占比</div><div className="v">{pct(insights.summary.top3_ratio)}</div></div>
            <div className="bt-card"><div className="k">集中度</div><div className="v">{insights.summary.concentration_level}</div></div>
          </div>

          {insights.notes?.length > 0 && (
            <div className="fund-bond-list" style={{ marginTop: 12 }}>
              {insights.notes.map((note, idx) => <span className="tag neutral" key={idx}>{note}</span>)}
            </div>
          )}

          <div className="fund-holding-grid" style={{ marginTop: 16 }}>
            <div>
              <h4 className="fund-subhead">真实配置与收益贡献</h4>
              <div className="corr-wrap">
                <table className="compact-table holdings-insight-table">
                  <thead>
                    <tr>
                      <th>代码</th><th>名称</th><th>金额</th><th>占比</th><th>持仓收益</th><th>收益率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {insights.allocation.slice(0, 10).map((row) => (
                      <tr key={`${row.asset_type}-${row.code}`}>
                        <td style={{ fontWeight: 800 }}>{row.code}</td>
                        <td>{row.name || '-'}</td>
                        <td>{num(row.amount)}</td>
                        <td>{pct(row.ratio)}</td>
                        <td className={cls(row.profit)}>{num(row.profit)}</td>
                        <td className={cls(row.profit_rate)}>{pct(row.profit_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h4 className="fund-subhead">基金趋势体检</h4>
              <div className="corr-wrap">
                <table className="compact-table holdings-insight-table">
                  <thead>
                    <tr>
                      <th>基金</th><th>趋势</th><th>近3月</th><th>近1年</th><th>最大回撤</th><th>定投分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {insights.fund_trends.map((row) => (
                      <tr key={row.code}>
                        <td>{row.code}</td>
                        <td>{row.trend_state || '-'}</td>
                        <td className={cls(row.return_3m)}>{pct(row.return_3m)}</td>
                        <td className={cls(row.return_1y)}>{pct(row.return_1y)}</td>
                        <td className="delta-neg">{pct(row.max_drawdown)}</td>
                        <td>{row.dca_score != null ? `${row.dca_score} · ${row.dca_label}` : '-'}</td>
                      </tr>
                    ))}
                    {!insights.fund_trends.length && (
                      <tr><td colSpan="6" className="hint">暂无可体检的基金持仓</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {insights.fund_errors?.length > 0 && (
                <div className="error" style={{ marginTop: 10 }}>
                  {insights.fund_errors.slice(0, 3).map((e) => `${e.code || e.scope}: ${e.error}`).join('；')}
                </div>
              )}
            </div>
          </div>

          {insights.overlap && (
            <div className="fund-overlap-block">
              <div className="bt-cards quality-cards">
                <div className="bt-card"><div className="k">平均个股重合</div><div className="v">{pct(insights.overlap.summary.avg_stock_overlap_weight)}</div></div>
                <div className="bt-card"><div className="k">平均行业重合</div><div className="v">{pct(insights.overlap.summary.avg_industry_overlap_weight)}</div></div>
                <div className="bt-card"><div className="k">高重合组合</div><div className="v">{`${insights.overlap.summary.high_overlap_pair_count}/${insights.overlap.summary.pair_count}`}</div></div>
                <div className="bt-card"><div className="k">结论</div><div className="v">{insights.overlap.summary.conclusion}</div></div>
              </div>
              <div className="fund-bond-list" style={{ marginTop: 12 }}>
                {insights.overlap.shared_stocks.slice(0, 10).map((r) => (
                  <span className="tag neutral" key={r.code}>{r.name} · {r.fund_count}只</span>
                ))}
              </div>
            </div>
          )}
          {insights.overlap_error && <div className="error">基金重合度真实数据获取失败: {insights.overlap_error}</div>}
          <p className="hint" style={{ marginTop: 12 }}>{insights.method.overlap}</p>
        </div>
      )}

      {exposure && (
        <div className="panel fade-in">
          <h3 className="section-title">
            组合穿透快照 <span className="hint">{exposure.snapshot?.id || '未持久化'} · {exposure.evaluated_on || '-'}</span>
          </h3>
          <div className={`exposure-integrity ${exposure.integrity?.verified ? 'verified' : 'invalid'}`}>
            <span>{exposure.integrity?.verified ? '完整性已验证' : '完整性未通过'}</span>
            <code>{exposure.snapshot?.payload_sha256?.slice(0, 16) || '-'}…</code>
            <span>{exposure.quality?.decision_eligible ? '可用于约束判断' : '仅供查看，不可用于放行金额'}</span>
          </div>
          <div className="bt-cards quality-cards">
            <div className="bt-card"><div className="k">权益暴露区间</div><div className="v">{pctRange(exposure.summary?.equity?.lower_ratio, exposure.summary?.equity?.upper_ratio)}</div></div>
            <div className="bt-card"><div className="k">行业集中区间</div><div className="v">{pctRange(exposure.summary?.industry?.max_lower_ratio, exposure.summary?.industry?.max_upper_ratio)}</div></div>
            <div className="bt-card"><div className="k">未分类权益</div><div className="v">{plainPct(exposure.summary?.industry?.unknown_equity_ratio)}</div></div>
            <div className="bt-card"><div className="k">市场待识别权益</div><div className="v">{plainPct(exposure.summary?.market?.unknown_equity_ratio)}</div></div>
            <div className="bt-card"><div className="k">数据状态</div><div className="v">{{ complete: '完整', partial: '部分可用', unavailable: '不可用' }[exposure.status] || exposure.status}</div></div>
          </div>

          {exposure.quality?.reasons?.length > 0 && (
            <div className="fund-bond-list" style={{ marginTop: 12 }}>
              {exposure.quality.reasons.map((reason, index) => <span className="tag neutral" key={`${reason}-${index}`}>{reason}</span>)}
            </div>
          )}

          <div className="fund-holding-grid" style={{ marginTop: 16 }}>
            <div>
              <h4 className="fund-subhead">行业暴露区间</h4>
              <div className="corr-wrap">
                <table className="compact-table holdings-insight-table">
                  <thead><tr><th>行业</th><th>已披露下界</th><th>最坏上界</th><th>主要来源</th></tr></thead>
                  <tbody>
                    {exposure.industries?.slice(0, 12).map((row) => (
                      <tr key={row.name}>
                        <td style={{ fontWeight: 800 }}>{row.name}</td>
                        <td>{plainPct(row.lower_ratio)}</td>
                        <td>{plainPct(row.upper_ratio)}</td>
                        <td>{row.contributors?.slice(0, 2).map((item) => item.code).join('、') || '-'}</td>
                      </tr>
                    ))}
                    {!exposure.industries?.length && <tr><td colSpan="4" className="hint">没有可用于行业穿透的真实披露。</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h4 className="fund-subhead">底层市场暴露</h4>
              <div className="corr-wrap">
                <table className="compact-table holdings-insight-table">
                  <thead><tr><th>市场</th><th>已识别下界</th><th>最坏上界</th><th>主要来源</th></tr></thead>
                  <tbody>
                    {exposure.markets?.map((row) => (
                      <tr key={row.market}>
                        <td>{({ mainland: 'A股', hong_kong: '港股', united_states: '美股' }[row.market] || row.market)}</td>
                        <td>{plainPct(row.lower_ratio)}</td>
                        <td>{plainPct(row.upper_ratio)}</td>
                        <td>{row.contributors?.slice(0, 2).map((item) => item.code).join('、') || '-'}</td>
                      </tr>
                    ))}
                    {!exposure.markets?.length && <tr><td colSpan="4" className="hint">没有可识别的真实底层市场披露。</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {exposure.funds?.length > 0 && (
            <div className="fund-bond-list" style={{ marginTop: 14 }}>
              {exposure.funds.map((row) => (
                <span className="tag neutral" key={row.code}>
                  {row.code} {row.name} · 权益 {pctRange(row.equity_interval?.lower_ratio, row.equity_interval?.upper_ratio)} · {row.periods?.asset || row.periods?.stock || '未返回报告期'}
                </span>
              ))}
            </div>
          )}
          {exposure.failed_sources?.length > 0 && <div className="error" style={{ marginTop: 12 }}>{exposure.failed_sources.slice(0, 4).map((row) => `${row.code}: ${row.error}`).join('；')}</div>}
          <p className="hint" style={{ marginTop: 12 }}>{exposure.policy}</p>
          <p className="hint">{exposure.method?.industry}</p>
        </div>
      )}

      {items.length > 0 ? (
        <div className="panel fade-in">
          <h3 className="section-title">持仓明细</h3>
          <div className="corr-wrap">
            <table className="compact-table holdings-table">
              <thead>
                <tr>
                  <th>类型</th><th>市场</th><th>代码</th><th>名称</th><th>金额</th><th>成本</th><th>昨日收益</th><th>持仓收益</th><th>收益率</th><th>来源</th><th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id}>
                    <td>{row.asset_type === 'fund' ? '基金' : '股票'}</td>
                    <td>{row.market || '-'}</td>
                    <td style={{ fontWeight: 800 }}>{row.code}</td>
                    <td>{row.name || '-'}</td>
                    <td>{num(row.amount)}</td>
                    <td>{num(row.cost)}</td>
                    <td className={cls(row.yesterday_profit)}>{num(row.yesterday_profit)}</td>
                    <td className={cls(row.profit)}>{num(row.profit)}</td>
                    <td className={cls(row.profit_rate)}>{pct(row.profit_rate)}</td>
                    <td className="hint">{sourceLabel(row.source)}</td>
                    <td><button className="ghost" onClick={() => doDelete(row.id)}>删除</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="placeholder">
          <div className="big">📦</div>
          还没有持仓。可以上传截图识别，也可以手动添加。
        </div>
      )}
    </>
  )
}
