import { useEffect, useRef, useState } from 'react'
import { FileSpreadsheet, ImageUp, X } from 'lucide-react'
import PortfolioActionCenter from '../features/portfolio/PortfolioActionCenter'
import {
  createPortfolioActionReport,
  deleteHolding,
  fetchHoldings,
  fetchHoldingTheses,
  fetchLatestPortfolioActionReport,
  parseHoldingsText,
  previewHoldingsFile,
  saveHoldings,
  saveHoldingThesis,
  uploadHoldingScreenshot,
} from '../api/portfolio'

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

const NUMBER_FIELDS = new Set([
  'amount',
  'cost',
  'yesterday_profit',
  'profit',
  'profit_rate',
  'shares',
])

export default function HoldingsTab() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [actionReport, setActionReport] = useState(null)
  const [actionReportLoading, setActionReportLoading] = useState(false)
  const [theses, setTheses] = useState(null)
  const [thesisSaving, setThesisSaving] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [ocrLoading, setOcrLoading] = useState(false)
  const [fileLoading, setFileLoading] = useState(false)
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState([])
  const [warnings, setWarnings] = useState([])
  const [filePreview, setFilePreview] = useState(null)
  const screenshotInputRef = useRef(null)
  const holdingFileInputRef = useRef(null)

  const items = data?.items || []

  async function loadHoldings() {
    setLoading(true)
    setError('')
    try {
      const result = await fetchHoldings()
      setData(result)
      if (!(result.items || []).length) setImportOpen(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function loadLatestActionReport() {
    try {
      setActionReport(await fetchLatestPortfolioActionReport())
    } catch (e) {
      setError(e.message)
    }
  }

  async function loadTheses() {
    try {
      setTheses(await fetchHoldingTheses())
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    loadHoldings()
    loadLatestActionReport()
    loadTheses()
  }, [])

  function updateParsed(index, key, value) {
    setParsed((rows) => rows.map((row, rowIndex) => {
      if (rowIndex !== index) return row
      const parsedValue = NUMBER_FIELDS.has(key) ? (value === '' ? null : Number(value)) : value
      const next = { ...row, [key]: parsedValue }
      if (key === 'asset_type' && value === 'fund') next.market = '基金'
      return next
    }))
  }

  function resetMaintenance() {
    setText('')
    setParsed([])
    setWarnings([])
    setFilePreview(null)
    if (screenshotInputRef.current) screenshotInputRef.current.value = ''
    if (holdingFileInputRef.current) holdingFileInputRef.current.value = ''
  }

  async function doParseText() {
    setError('')
    setWarnings([])
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
    setOcrLoading(true)
    setError('')
    setWarnings([])
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
    setFileLoading(true)
    setError('')
    setWarnings([])
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
    const rows = parsed.filter((row) => row.code && row.asset_type)
    if (!rows.length) {
      setError('没有可保存的持仓')
      return
    }
    setError('')
    try {
      const result = await saveHoldings(rows)
      setData({ items: result.items, summary: result.summary })
      resetMaintenance()
      await Promise.all([loadLatestActionReport(), loadTheses()])
    } catch (e) {
      setError(e.message)
    }
  }

  async function doDelete(id) {
    setError('')
    try {
      await deleteHolding(id)
      await Promise.all([loadHoldings(), loadLatestActionReport(), loadTheses()])
    } catch (e) {
      setError(e.message)
      throw e
    }
  }

  async function refreshActionReport() {
    setActionReportLoading(true)
    setError('')
    try {
      setActionReport(await createPortfolioActionReport(8))
    } catch (e) {
      setError(e.message || '真实持仓行动报告生成失败')
    } finally {
      setActionReportLoading(false)
    }
  }

  async function doSaveThesis(payload) {
    setThesisSaving(true)
    setError('')
    try {
      const result = await saveHoldingThesis(payload)
      await Promise.all([loadTheses(), loadLatestActionReport()])
      return result
    } catch (e) {
      setError(e.message || '持有逻辑保存失败')
      throw e
    } finally {
      setThesisSaving(false)
    }
  }

  return (
    <>
      <PortfolioActionCenter
        report={actionReport}
        items={items}
        loading={actionReportLoading}
        onRefresh={refreshActionReport}
        onOpenImport={() => setImportOpen((open) => !open)}
        onDelete={doDelete}
        theses={theses}
        thesisSaving={thesisSaving}
        onSaveThesis={doSaveThesis}
      />

      {error && <div className="error portfolio-page-error">{error}</div>}

      {importOpen && (
        <section className="portfolio-maintenance panel fade-in">
          <div className="portfolio-section-head">
            <div>
              <h3>维护持仓</h3>
              <p>导入后逐行核对；保存会使旧行动报告立即失效。</p>
            </div>
            <button className="icon-button" onClick={() => setImportOpen(false)} title="关闭维护" aria-label="关闭维护">
              <X size={18} />
            </button>
          </div>

          <div className="portfolio-import-toolbar">
            <input ref={screenshotInputRef} type="file" accept="image/*" hidden onChange={(event) => doUpload(event.target.files?.[0])} />
            <input ref={holdingFileInputRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden onChange={(event) => doPreviewHoldingFile(event.target.files?.[0])} />
            <button className="ghost" disabled={ocrLoading} onClick={() => screenshotInputRef.current?.click()}>
              <ImageUp size={16} /> {ocrLoading ? 'OCR识别中' : '上传持仓截图'}
            </button>
            <button className="ghost" disabled={fileLoading} onClick={() => holdingFileInputRef.current?.click()}>
              <FileSpreadsheet size={16} /> {fileLoading ? '账单解析中' : '导入 CSV / Excel'}
            </button>
            <button className="ghost" onClick={() => { setFilePreview(null); setParsed((rows) => [{ ...blankCandidate }, ...rows]) }}>手动添加一行</button>
            <button className="ghost" onClick={loadHoldings} disabled={loading}>{loading ? '刷新中' : '刷新持仓'}</button>
          </div>

          <div className="portfolio-privacy-note">
            截图可能包含姓名、手机号或账户信息。上传前应打码；系统只保存你确认后的持仓字段，不长期保存原图。
          </div>

          <div className="portfolio-paste-area">
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="粘贴持仓截图识别文字，包含基金或股票名称、代码、金额、收益和份额"
            />
            <button className="ghost" onClick={doParseText} disabled={!text.trim()}>解析文本</button>
          </div>

          {filePreview && (
            <div className="warning">
              已识别为{filePreview.template?.label || '持仓账单'}：{filePreview.candidates?.length || 0} 条待确认。{filePreview.privacy}
            </div>
          )}
          {warnings.map((warning, index) => <p className="hint" key={`${warning}-${index}`}>{warning}</p>)}

          {parsed.length > 0 && (
            <div className="portfolio-import-preview">
              <div className="portfolio-section-head">
                <div>
                  <h4>{filePreview ? `${filePreview.template?.label || '持仓账单'}预览` : '待确认持仓'}</h4>
                  <p>{parsed.length} 条，保存前可以直接修改。</p>
                </div>
                <button onClick={doSaveParsed}>确认并保存</button>
              </div>
              {filePreview?.errors?.length > 0 && (
                <div className="error">未纳入导入：{filePreview.errors.slice(0, 5).map((row) => `第 ${row.row} 行 ${row.message}`).join('；')}</div>
              )}
              <div className="corr-wrap">
                <table className="compact-table holdings-edit-table">
                  <thead>
                    <tr>
                      <th>类型</th><th>市场</th><th>代码</th><th>名称</th><th>金额</th><th>成本</th><th>昨日收益</th><th>累计收益</th><th>收益率</th><th>份额</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {parsed.map((row, index) => (
                      <tr key={`${row.code}-${index}`}>
                        <td>
                          <select value={row.asset_type || 'fund'} onChange={(event) => updateParsed(index, 'asset_type', event.target.value)}>
                            <option value="fund">基金</option>
                            <option value="stock">股票</option>
                          </select>
                        </td>
                        <td><input value={row.market || ''} onChange={(event) => updateParsed(index, 'market', event.target.value)} /></td>
                        <td><input value={row.code || ''} onChange={(event) => updateParsed(index, 'code', event.target.value)} /></td>
                        <td><input value={row.name || ''} onChange={(event) => updateParsed(index, 'name', event.target.value)} /></td>
                        {['amount', 'cost', 'yesterday_profit', 'profit', 'profit_rate', 'shares'].map((field) => (
                          <td key={field}><input type="number" value={row[field] ?? ''} onChange={(event) => updateParsed(index, field, event.target.value)} /></td>
                        ))}
                        <td><button className="icon-button" onClick={() => setParsed((rows) => rows.filter((_, rowIndex) => rowIndex !== index))} title="删除此行" aria-label="删除此行"><X size={16} /></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}
    </>
  )
}
