import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  ArrowRightLeft,
  BookOpenCheck,
  CalendarRange,
  ChartNoAxesCombined,
  CircleAlert,
  FileSpreadsheet,
  History,
  Plus,
  ReceiptText,
  RefreshCw,
  Save,
  Scale,
  Trash2,
  Upload,
} from 'lucide-react'
import FundSwitchLifecyclePanel from '../components/FundSwitchLifecyclePanel'
import {
  createFundSwitchAttributionSnapshot,
  createFundSwitchPurchaseRequote,
  createPortfolioSnapshot,
  createPortfolioTransaction,
  deletePortfolioTransaction,
  fetchPortfolioLedger,
  fetchPortfolioBehavior,
  fetchPortfolioAttribution,
  fetchPortfolioPerformance,
  fetchPortfolioSnapshots,
  fetchPortfolioTransactions,
  fetchFundSwitchCases,
  fetchRebalanceReview,
  importPortfolioTransactionCsv,
  previewPortfolioTransactionCsv,
  reconcileFundSwitchCase,
  recordFundSwitchPurchase,
} from '../api/portfolio'

const TRADE_TYPES = [
  { value: 'buy', label: '买入' },
  { value: 'sell', label: '卖出' },
  { value: 'opening', label: '期初持仓' },
]

function localDate() {
  const now = new Date()
  const offset = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - offset).toISOString().slice(0, 10)
}

function blankTransaction() {
  return {
    asset_type: 'fund',
    market: '基金',
    code: '',
    name: '',
    trade_type: 'buy',
    trade_date: localDate(),
    shares: '',
    unit_price: '',
    fee: '0',
    note: '',
  }
}

function money(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function number(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`
}

function ratioPct(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(digits)}%`
}

function deltaClass(value) {
  if (value > 0) return 'delta-pos'
  if (value < 0) return 'delta-neg'
  return 'delta-zero'
}

function holdingShareState(value) {
  if (value === true) return '已匹配'
  if (value === false) return '待对账'
  return '未提供现有份额'
}

function behaviorStatusLabel(value) {
  if (value === 'available') return '完整匹配'
  if (value === 'partial') return '部分匹配'
  return '待补流水'
}

function switchCaseStatusLabel(value) {
  return {
    settled_purchase_requote_required: '到账后重报',
    purchase_requote_blocked: '申购门禁受阻',
    purchase_requote_expired: '申购报价过期',
    purchase_requote_superseded: '持仓或政策变化',
    ready_for_manual_purchase_review: '人工复核申购',
    purchase_recorded_reconciliation_pending: '等待持仓对账',
    completed_attribution_pending: '等待历史归因',
    completed_attribution_available: '历史归因可用',
    completed_attribution_blocked: '历史归因受阻',
    integrity_failed: '完整性失败',
  }[value] || value || '-'
}

function Metric({ label, value, tone = '' }) {
  return <div className="ledger-metric"><span>{label}</span><b className={tone}>{value}</b></div>
}

export default function PortfolioLedgerTab() {
  const [form, setForm] = useState(blankTransaction)
  const [transactions, setTransactions] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [behavior, setBehavior] = useState(null)
  const [attribution, setAttribution] = useState(null)
  const [performance, setPerformance] = useState(null)
  const [rebalance, setRebalance] = useState(null)
  const [snapshots, setSnapshots] = useState(null)
  const [switchCases, setSwitchCases] = useState(null)
  const [selectedSwitchCaseId, setSelectedSwitchCaseId] = useState('')
  const [importAssetType, setImportAssetType] = useState('fund')
  const [importMarket, setImportMarket] = useState('基金')
  const [importPreview, setImportPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [snapshotting, setSnapshotting] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const csvInputRef = useRef(null)

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [transactionData, ledgerData, behaviorData, attributionData, performanceData, rebalanceData, snapshotData, switchCaseData] = await Promise.all([
        fetchPortfolioTransactions(),
        fetchPortfolioLedger(),
        fetchPortfolioBehavior(),
        fetchPortfolioAttribution(),
        fetchPortfolioPerformance(),
        fetchRebalanceReview(),
        fetchPortfolioSnapshots(),
        fetchFundSwitchCases(),
      ])
      setTransactions(transactionData)
      setLedger(ledgerData)
      setBehavior(behaviorData)
      setAttribution(attributionData)
      setPerformance(performanceData)
      setRebalance(rebalanceData)
      setSnapshots(snapshotData)
      setSwitchCases(switchCaseData)
    } catch (requestError) {
      setError(requestError.message || '真实组合账本获取失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const grossAmount = useMemo(() => {
    const shares = Number(form.shares)
    const unitPrice = Number(form.unit_price)
    if (!Number.isFinite(shares) || !Number.isFinite(unitPrice) || shares <= 0 || unitPrice <= 0) return null
    return shares * unitPrice
  }, [form.shares, form.unit_price])

  function update(field, value) {
    setForm((current) => {
      const next = { ...current, [field]: value }
      if (field === 'asset_type') next.market = value === 'fund' ? '基金' : (current.market === '基金' ? '' : current.market)
      return next
    })
  }

  async function saveTransaction() {
    setSaving(true)
    setError('')
    try {
      await createPortfolioTransaction({
        ...form,
        code: form.code.trim(),
        name: form.name.trim(),
        shares: Number(form.shares),
        unit_price: Number(form.unit_price),
        fee: Number(form.fee || 0),
      })
      setForm(blankTransaction())
      await load()
    } catch (requestError) {
      setError(requestError.message || '交易流水保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function removeTransaction(id) {
    if (!window.confirm('删除后会重算对应资产的 FIFO 成本和已实现收益，确认删除？')) return
    setError('')
    try {
      await deletePortfolioTransaction(id)
      await load()
    } catch (requestError) {
      setError(requestError.message || '交易流水删除失败')
    }
  }

  async function captureSnapshot() {
    setSnapshotting(true)
    setError('')
    try {
      await createPortfolioSnapshot('manual_review')
      await load()
    } catch (requestError) {
      setError(requestError.message || '组合快照保存失败')
    } finally {
      setSnapshotting(false)
    }
  }

  function updateSwitchCase(saved) {
    setSwitchCases((current) => {
      if (!current) return { status: 'available', items: [saved], summary: {} }
      const exists = (current.items || []).some((item) => item.case_id === saved.case_id)
      return {
        ...current,
        status: 'available',
        items: exists
          ? current.items.map((item) => item.case_id === saved.case_id ? saved : item)
          : [saved, ...(current.items || [])],
      }
    })
  }

  async function requoteSwitchPurchase(candidateCode, caseId, payload) {
    const saved = await createFundSwitchPurchaseRequote(caseId, payload)
    updateSwitchCase(saved)
    return saved
  }

  async function recordSwitchPurchase(candidateCode, caseId, payload) {
    const saved = await recordFundSwitchPurchase(caseId, payload)
    updateSwitchCase(saved)
    return saved
  }

  async function reconcileSwitch(candidateCode, caseId) {
    const saved = await reconcileFundSwitchCase(caseId)
    updateSwitchCase(saved)
    return saved
  }

  async function refreshSwitchAttribution(candidateCode, caseId) {
    const saved = await createFundSwitchAttributionSnapshot(caseId)
    updateSwitchCase(saved)
    return saved
  }

  function updateImportSettings(nextAssetType) {
    setImportAssetType(nextAssetType)
    setImportMarket((current) => nextAssetType === 'fund' ? '基金' : (current === '基金' ? '' : current))
  }

  function updateImportCandidate(index, field, value) {
    setImportPreview((current) => {
      if (!current) return current
      const candidates = current.candidates.map((row, rowIndex) => {
        if (rowIndex !== index) return row
        const next = {
          ...row,
          [field]: ['shares', 'unit_price', 'fee'].includes(field) ? (value === '' ? null : Number(value)) : value,
        }
        if (field === 'asset_type') next.market = value === 'fund' ? '基金' : (row.market === '基金' ? '' : row.market)
        return next
      })
      return { ...current, candidates }
    })
  }

  async function previewCsv(file) {
    if (!file) return
    setPreviewing(true)
    setError('')
    try {
      setImportPreview(await previewPortfolioTransactionCsv(file, importAssetType, importMarket))
    } catch (requestError) {
      setError(requestError.message || '交易账单预览失败')
      setImportPreview(null)
    } finally {
      setPreviewing(false)
    }
  }

  async function importCsv() {
    if (!importPreview?.candidates?.length) {
      setError('没有可确认导入的交易流水')
      return
    }
    setImporting(true)
    setError('')
    try {
      await importPortfolioTransactionCsv(importPreview.candidates, importPreview.file_sha256, importPreview.filename)
      setImportPreview(null)
      if (csvInputRef.current) csvInputRef.current.value = ''
      await load()
    } catch (requestError) {
      setError(requestError.message || '交易账单导入失败')
    } finally {
      setImporting(false)
    }
  }

  const ledgerSummary = ledger?.summary || {}
  const behaviorSummary = behavior?.summary || {}
  const attributionSummary = attribution?.summary || {}
  const performanceSummary = performance?.summary || {}
  const rebalanceSummary = rebalance?.summary || {}
  const snapshotChange = snapshots?.latest_change
  const selectedSwitchCase = (switchCases?.items || []).find((item) => item.case_id === selectedSwitchCaseId) || null

  return (
    <div className="ledger-workspace">
      <section className="ledger-hero" aria-label="交易与复盘">
        <div>
          <span className="eyebrow">交易与复盘</span>
          <h3>用可追溯成本复盘，而不是只看一张收益截图</h3>
          <p>交易记录、当前确认持仓和仓位上限分别展示。成本、已实现收益和快照变化都只以你保存的数据为依据。</p>
        </div>
        <div className="ledger-hero-actions">
          <button className="ghost" onClick={load} disabled={loading} title="刷新交易与复盘数据">
            <RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
            <span>{loading ? '刷新中' : '刷新'}</span>
          </button>
          <button onClick={captureSnapshot} disabled={snapshotting}>
            <History size={16} aria-hidden="true" />
            <span>{snapshotting ? '记录中' : '记录当前快照'}</span>
          </button>
        </div>
      </section>

      {error && <div className="error">{error}</div>}

      <section className="ledger-grid ledger-metrics-grid" aria-label="账本摘要">
        <Metric label="已录入交易" value={`${ledgerSummary.transaction_count ?? '-'} 笔`} />
        <Metric label="剩余成本" value={money(ledgerSummary.open_cost)} />
        <Metric label="已实现收益" value={money(ledgerSummary.realized_profit)} tone={deltaClass(ledgerSummary.realized_profit)} />
        <Metric label="交易费用" value={money(ledgerSummary.total_fee)} />
        <Metric label="份额待对账" value={`${ledgerSummary.share_mismatch_count ?? '-'} 项`} tone={(ledgerSummary.share_mismatch_count || 0) > 0 ? 'delta-neg' : ''} />
      </section>

      <section className="ledger-section ledger-switch-cases">
        <div className="ledger-section-head">
          <div>
            <span className="eyebrow">基金替换批次</span>
            <h4>从真实到账追踪到历史归因</h4>
          </div>
          <ArrowRightLeft size={19} aria-hidden="true" />
        </div>
        <div className="corr-wrap">
          <table className="compact-table ledger-table ledger-switch-case-table">
            <thead><tr><th>替换路径</th><th>当前阶段</th><th>赎回到账</th><th>候选成交</th><th>历史相对结果</th><th></th></tr></thead>
            <tbody>
              {switchCases?.items?.map((item) => (
                <tr key={item.case_id}>
                  <td><b>{item.selected_code} → {item.candidate_code}</b><small>{item.candidate_name || item.candidate_code}</small></td>
                  <td><span className={`ledger-reconcile ${item.status === 'integrity_failed' ? 'mismatch' : item.decision_gate?.holdings_reconciled ? 'matched' : ''}`}>{switchCaseStatusLabel(item.status)}</span><small>{item.revision || 0} 个不可变事件</small></td>
                  <td>{item.settlement ? money(item.settlement.actual_received_yuan) : '-'}<small>{item.settlement?.settled_on || '未确认'}</small></td>
                  <td>{item.purchase ? number(item.purchase.shares, 6) : '-'}<small>{item.purchase?.confirmation_date || '未记录'}</small></td>
                  <td className={deltaClass(item.attribution?.metrics?.incremental_value_vs_hold_yuan)}>{item.attribution?.status === 'available' ? money(item.attribution.metrics?.incremental_value_vs_hold_yuan) : '-'}<small>{item.attribution?.status === 'available' ? `截至 ${item.attribution.metrics?.as_of || '-'}` : '等待真实同日净值'}</small></td>
                  <td><button type="button" className="ghost compact" onClick={() => setSelectedSwitchCaseId((current) => current === item.case_id ? '' : item.case_id)}>{selectedSwitchCaseId === item.case_id ? '收起' : '查看批次'}</button></td>
                </tr>
              ))}
              {!switchCases?.items?.length && <tr><td colSpan="6" className="hint">尚无基金替换批次。只有执行前审查通过并绑定真实赎回到账后才会写入。</td></tr>}
            </tbody>
          </table>
        </div>
        {selectedSwitchCase && (
          <div className="ledger-switch-case-detail">
            <FundSwitchLifecyclePanel
              item={{ code: selectedSwitchCase.candidate_code, switch_lifecycle: { status: 'available', case: selectedSwitchCase } }}
              onConfirmPurchaseRequote={requoteSwitchPurchase}
              onRecordPurchase={recordSwitchPurchase}
              onReconcile={reconcileSwitch}
              onRefreshAttribution={refreshSwitchAttribution}
            />
          </div>
        )}
        <p className="ledger-method">批次事件只追加不改写；删除或修改绑定流水会使完整性校验失败。人工复核状态不授权交易，历史归因不代表未来收益。</p>
      </section>

      <section className="ledger-section">
        <div className="ledger-section-head">
          <div>
            <span className="eyebrow">录入事实</span>
            <h4>新增交易流水</h4>
          </div>
          <span className="ledger-source">来源：用户录入交易流水</span>
        </div>
        <div className="trade-type-switch" role="group" aria-label="交易方向">
          {TRADE_TYPES.map((item) => (
            <button
              type="button"
              key={item.value}
              className={form.trade_type === item.value ? 'active' : ''}
              onClick={() => update('trade_type', item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="ledger-form-grid">
          <label className="field"><span>资产类型</span><select value={form.asset_type} onChange={(event) => update('asset_type', event.target.value)}><option value="fund">基金</option><option value="stock">股票</option></select></label>
          <label className="field"><span>市场</span><input value={form.market} onChange={(event) => update('market', event.target.value)} placeholder="基金 / A股 / 港股 / 美股" /></label>
          <label className="field"><span>代码</span><input value={form.code} onChange={(event) => update('code', event.target.value)} placeholder="例如 013403" /></label>
          <label className="field"><span>名称</span><input value={form.name} onChange={(event) => update('name', event.target.value)} placeholder="可选，但建议填写" /></label>
          <label className="field"><span>交易日期</span><input type="date" value={form.trade_date} onChange={(event) => update('trade_date', event.target.value)} /></label>
          <label className="field"><span>份额 / 股数</span><input type="number" min="0" step="any" value={form.shares} onChange={(event) => update('shares', event.target.value)} /></label>
          <label className="field"><span>成交单价</span><input type="number" min="0" step="any" value={form.unit_price} onChange={(event) => update('unit_price', event.target.value)} /></label>
          <label className="field"><span>费用</span><input type="number" min="0" step="0.01" value={form.fee} onChange={(event) => update('fee', event.target.value)} /></label>
          <label className="field ledger-note-field"><span>备注</span><input value={form.note} onChange={(event) => update('note', event.target.value)} maxLength="300" placeholder="例如 定投、调仓、券商导入核对" /></label>
        </div>
        <div className="ledger-form-footer">
          <span>本次成交额：<b>{money(grossAmount)}</b></span>
          <button onClick={saveTransaction} disabled={saving || !form.code || !form.shares || !form.unit_price}>
            <Save size={16} aria-hidden="true" />
            <span>{saving ? '保存中' : '保存流水'}</span>
          </button>
        </div>
      </section>

      <section className="ledger-section ledger-import-section">
        <div className="ledger-section-head">
          <div>
              <span className="eyebrow">账单导入</span>
              <h4>预览交易账单</h4>
          </div>
          <FileSpreadsheet size={19} aria-hidden="true" />
        </div>
        <div className="ledger-import-settings">
          <label className="field"><span>默认资产类型</span><select value={importAssetType} onChange={(event) => updateImportSettings(event.target.value)}><option value="fund">基金</option><option value="stock">股票</option></select></label>
          <label className="field"><span>默认市场</span><input value={importMarket} onChange={(event) => setImportMarket(event.target.value)} placeholder="基金 / A股 / 港股 / 美股" /></label>
          <div className="ledger-import-upload">
            <input ref={csvInputRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => previewCsv(event.target.files?.[0])} />
            <button className="ghost" type="button" onClick={() => csvInputRef.current?.click()} disabled={previewing} title="选择 CSV 或 Excel 交易账单">
              <Upload size={16} aria-hidden="true" />
              <span>{previewing ? '解析中' : '选择交易账单'}</span>
            </button>
          </div>
        </div>
        <p className="ledger-method">支持 CSV/XLSX 与天天基金常见确认流水字段。转换、分红、未确认或失败记录会明确排除，避免把现金流不完整的数据写入账本；原始文件不会保存。</p>
      </section>

      {importPreview && (
        <section className="ledger-section ledger-import-preview">
          <div className="ledger-section-head">
            <div>
              <span className="eyebrow">导入预览</span>
              <h4>{importPreview.template?.label || importPreview.filename || '未命名账单'} · {importPreview.candidates.length} 条待确认</h4>
            </div>
            <span className="ledger-source">{importPreview.format || importPreview.delimiter} · {importPreview.encoding}</span>
          </div>
          <div className="ledger-import-notice">{importPreview.privacy}</div>
          {importPreview.warnings?.map((warning, index) => <div className="ledger-import-warning" key={`${warning}-${index}`}>{warning}</div>)}
          {importPreview.errors?.length > 0 && <div className="ledger-import-errors">未纳入导入：{importPreview.errors.slice(0, 5).map((row) => `第 ${row.row} 行 ${row.message}`).join('；')}</div>}
          <div className="corr-wrap">
            <table className="compact-table ledger-import-table">
              <thead><tr><th>类型</th><th>市场</th><th>代码</th><th>名称</th><th>方向</th><th>日期</th><th>份额</th><th>单价</th><th>费用</th><th></th></tr></thead>
              <tbody>
                {importPreview.candidates.map((row, index) => (
                  <tr key={`${row.csv_row}-${index}`}>
                    <td><select value={row.asset_type} onChange={(event) => updateImportCandidate(index, 'asset_type', event.target.value)}><option value="fund">基金</option><option value="stock">股票</option></select></td>
                    <td><input value={row.market || ''} onChange={(event) => updateImportCandidate(index, 'market', event.target.value)} /></td>
                    <td><input value={row.code || ''} onChange={(event) => updateImportCandidate(index, 'code', event.target.value)} /></td>
                    <td><input value={row.name || ''} onChange={(event) => updateImportCandidate(index, 'name', event.target.value)} /></td>
                    <td><select value={row.trade_type} onChange={(event) => updateImportCandidate(index, 'trade_type', event.target.value)}>{TRADE_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></td>
                    <td><input type="date" value={row.trade_date || ''} onChange={(event) => updateImportCandidate(index, 'trade_date', event.target.value)} /></td>
                    <td><input type="number" min="0" step="any" value={row.shares ?? ''} onChange={(event) => updateImportCandidate(index, 'shares', event.target.value)} /></td>
                    <td><input type="number" min="0" step="any" value={row.unit_price ?? ''} onChange={(event) => updateImportCandidate(index, 'unit_price', event.target.value)} /></td>
                    <td><input type="number" min="0" step="0.01" value={row.fee ?? ''} onChange={(event) => updateImportCandidate(index, 'fee', event.target.value)} /></td>
                    <td><button className="ghost ledger-delete" type="button" onClick={() => setImportPreview((current) => current ? { ...current, candidates: current.candidates.filter((_, rowIndex) => rowIndex !== index) } : current)} title="移出导入列表" aria-label="移出导入列表"><Trash2 size={15} aria-hidden="true" /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ledger-form-footer">
            <span>确认后会先进行份额序列校验；任何一条不通过都不会部分写入。</span>
            <div className="ledger-inline-actions">
              <button onClick={importCsv} disabled={importing || !importPreview.candidates.length}><Save size={16} aria-hidden="true" /><span>{importing ? '导入中' : `确认导入 ${importPreview.candidates.length} 条`}</span></button>
              <button className="ghost" onClick={() => setImportPreview(null)} disabled={importing}>取消</button>
            </div>
          </div>
        </section>
      )}

      <section className="ledger-grid ledger-review-grid">
        <div className="ledger-section">
          <div className="ledger-section-head">
            <div>
              <span className="eyebrow">成本复盘</span>
              <h4>FIFO 成本与份额对账</h4>
            </div>
            <ReceiptText size={19} aria-hidden="true" />
          </div>
          {ledger?.integrity_issues?.length > 0 && (
            <div className="ledger-issues">
              {ledger.integrity_issues.map((issue, index) => <div key={`${issue.code}-${index}`}><CircleAlert size={15} aria-hidden="true" /><span>{issue.name}：{issue.message}</span></div>)}
            </div>
          )}
          <div className="corr-wrap">
            <table className="compact-table ledger-table">
              <thead><tr><th>资产</th><th>剩余份额</th><th>平均成本</th><th>剩余成本</th><th>已实现收益</th><th>估算未实现</th><th>份额对账</th></tr></thead>
              <tbody>
                {ledger?.positions?.map((row) => (
                  <tr key={`${row.asset_type}-${row.code}`}>
                    <td><b>{row.code}</b><small>{row.name}</small></td>
                    <td>{number(row.open_shares, 6)}</td>
                    <td>{money(row.average_cost, 6)}</td>
                    <td>{money(row.remaining_cost)}</td>
                    <td className={deltaClass(row.realized_profit)}>{money(row.realized_profit)}</td>
                    <td className={deltaClass(row.estimated_unrealized_profit)}>{money(row.estimated_unrealized_profit)}</td>
                    <td><span className={`ledger-reconcile ${row.shares_match === false ? 'mismatch' : row.shares_match === true ? 'matched' : ''}`}>{holdingShareState(row.shares_match)}</span></td>
                  </tr>
                ))}
                {!ledger?.positions?.length && <tr><td colSpan="7" className="hint">尚未录入交易流水。已有历史持仓可先录入“期初持仓”。</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="ledger-method">{ledger?.method?.cost_basis}</p>
        </div>

        <div className="ledger-section">
          <div className="ledger-section-head">
            <div>
              <span className="eyebrow">仓位纪律</span>
              <h4>单品上限复盘</h4>
            </div>
            <Scale size={19} aria-hidden="true" />
          </div>
          <div className="ledger-mini-metrics">
            <Metric label="已计入金额" value={money(rebalanceSummary.total_amount)} />
            <Metric label="缺失金额" value={`${rebalanceSummary.missing_amount_count ?? '-'} 项`} />
            <Metric label="月度预算" value={rebalanceSummary.monthly_budget == null ? '未设置' : money(rebalanceSummary.monthly_budget, 0)} />
          </div>
          <div className="rebalance-actions">
            {rebalance?.actions?.map((action, index) => <div key={`${action.title}-${index}`} className={`rebalance-action ${action.level}`}><b>{action.title}</b><p>{action.detail}</p></div>)}
            {!rebalance?.actions?.length && <div className="overview-empty">等待真实持仓和投资约束。</div>}
          </div>
          <div className="corr-wrap" style={{ marginTop: 12 }}>
            <table className="compact-table ledger-table">
              <thead><tr><th>资产</th><th>当前占比</th><th>单品上限</th><th>高出上限</th><th>上限空间</th></tr></thead>
              <tbody>
                {rebalance?.allocations?.slice(0, 8).map((row) => (
                  <tr key={`${row.asset_type}-${row.code}`}>
                    <td><b>{row.code}</b><small>{row.name}</small></td>
                    <td>{pct(row.current_ratio)}</td>
                    <td>{pct(row.max_single_ratio)}</td>
                    <td className={(row.excess_amount || 0) > 0 ? 'delta-neg' : ''}>{money(row.excess_amount)}</td>
                    <td>{money(row.room_before_cap)}</td>
                  </tr>
                ))}
                {!rebalance?.allocations?.length && <tr><td colSpan="5" className="hint">补全持仓金额后显示仓位上限与空间。</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="ledger-method">{rebalance?.policy}</p>
        </div>
      </section>

      <section className="ledger-section ledger-performance-section">
        <div className="ledger-section-head">
          <div>
            <span className="eyebrow">现金流收益</span>
            <h4>资金加权收益率</h4>
          </div>
          <ChartNoAxesCombined size={19} aria-hidden="true" />
        </div>
        <div className="ledger-performance-grid">
          <Metric label="年化资金加权收益率" value={performance?.status === 'available' ? pct(performanceSummary.money_weighted_return_annualized) : '-'} tone={deltaClass(performanceSummary.money_weighted_return_annualized)} />
          <Metric label="现金流净投入" value={money(performanceSummary.net_invested)} />
          <Metric label="当前确认市值" value={money(performanceSummary.current_value)} />
          <Metric label="现金流账面盈亏" value={performance?.status === 'available' ? money(performanceSummary.cashflow_profit) : '-'} tone={deltaClass(performanceSummary.cashflow_profit)} />
          <Metric label="数据覆盖" value={`${performanceSummary.valued_holding_count ?? '-'} 项持仓`} />
        </div>
        {performance?.status !== 'available' && performance?.reasons?.length > 0 && (
          <div className="ledger-performance-notes">{performance.reasons.map((reason, index) => <span key={`${reason}-${index}`}><CircleAlert size={14} aria-hidden="true" />{reason}</span>)}</div>
        )}
        <p className="ledger-method">{performance?.policy || performance?.method?.money_weighted_return}</p>
      </section>

      <section className="ledger-section ledger-attribution-section">
        <div className="ledger-section-head">
          <div>
            <span className="eyebrow">区间复盘</span>
            <h4>区分新增资金与持仓变化</h4>
          </div>
          <CalendarRange size={19} aria-hidden="true" />
        </div>
        <div className="ledger-attribution-period">
          <span>{attributionSummary.start_at?.replace('T', ' ') || '待记录起始快照'}</span>
          <b>至</b>
          <span>{attributionSummary.end_at?.replace('T', ' ') || '待记录结束快照'}</span>
          <small>{attributionSummary.period_days == null ? '-' : `${attributionSummary.period_days} 天`} · 区间流水 {attributionSummary.transaction_count ?? '-'} 笔</small>
        </div>
        <div className="ledger-attribution-grid">
          <Metric label="持仓账面变化" value={money(attributionSummary.asset_value_change)} tone={deltaClass(attributionSummary.asset_value_change)} />
          <Metric label="净外部资金流" value={money(attributionSummary.net_cash_flow)} />
          <Metric label="资金流调整后变动" value={attribution?.status === 'available' ? money(attributionSummary.flow_adjusted_change) : '-'} tone={deltaClass(attributionSummary.flow_adjusted_change)} />
          <Metric label="资金流调整区间回报" value={attribution?.status === 'available' ? pct(attributionSummary.modified_dietz_return) : '-'} tone={deltaClass(attributionSummary.modified_dietz_return)} />
          <Metric label="快照资产覆盖" value={`${attribution?.coverage?.tracked_snapshot_asset_count ?? '-'} / ${attribution?.coverage?.snapshot_asset_count ?? '-'} 项`} />
        </div>
        {attribution?.reasons?.length > 0 && (
          <div className="ledger-performance-notes">{attribution.reasons.map((reason, index) => <span key={`${reason}-${index}`}><CircleAlert size={14} aria-hidden="true" />{reason}</span>)}</div>
        )}
        <p className="ledger-method">{attribution?.policy || attribution?.method?.return}</p>
      </section>

      <section className="ledger-section ledger-behavior-section">
        <div className="ledger-section-head">
          <div>
            <span className="eyebrow">交易行为</span>
            <h4>用已匹配成交复盘执行质量</h4>
          </div>
          <Activity size={19} aria-hidden="true" />
        </div>
        <div className="ledger-behavior-grid">
          <Metric label="已匹配卖出" value={`${behaviorSummary.fully_matched_sell_count ?? '-'} / ${behaviorSummary.sell_count ?? '-'} 笔`} />
          <Metric label="已匹配实现盈亏" value={money(behaviorSummary.matched_realized_profit)} tone={deltaClass(behaviorSummary.matched_realized_profit)} />
          <Metric label="已匹配胜率" value={ratioPct(behaviorSummary.win_rate)} />
          <Metric label="费用占成交额" value={ratioPct(behaviorSummary.fee_rate, 3)} />
          <Metric label="已匹配平均持有" value={behaviorSummary.average_holding_days == null ? '-' : `${number(behaviorSummary.average_holding_days, 1)} 天`} />
        </div>
        <div className="ledger-behavior-coverage">
          <span className={`ledger-reconcile ${behavior?.status === 'partial' ? 'mismatch' : behavior?.status === 'available' ? 'matched' : ''}`}>{behaviorStatusLabel(behavior?.status)}</span>
          <span>交易日 {behaviorSummary.trade_day_count ?? '-'} 天</span>
          <span>已匹配份额 {number(behavior?.coverage?.matched_shares, 6)}</span>
          <span>待补份额 {number(behavior?.coverage?.unmatched_shares, 6)}</span>
        </div>
        {behavior?.reasons?.length > 0 && (
          <div className="ledger-performance-notes">{behavior.reasons.map((reason, index) => <span key={`${reason}-${index}`}><CircleAlert size={14} aria-hidden="true" />{reason}</span>)}</div>
        )}
        <div className="corr-wrap ledger-behavior-table-wrap">
          <table className="compact-table ledger-table">
            <thead><tr><th>资产</th><th>完整匹配卖出</th><th>已匹配盈亏</th><th>已匹配胜率</th><th>平均持有</th><th>交易费用</th><th>覆盖状态</th></tr></thead>
            <tbody>
              {behavior?.asset_reviews?.filter((row) => row.sell_count > 0).slice(0, 10).map((row) => (
                <tr key={`${row.asset_type}-${row.code}`}>
                  <td><b>{row.code}</b><small>{row.name}</small></td>
                  <td>{row.fully_matched_sell_count} / {row.sell_count} 笔</td>
                  <td className={deltaClass(row.matched_realized_profit)}>{money(row.matched_realized_profit)}</td>
                  <td>{ratioPct(row.win_rate)}</td>
                  <td>{row.average_holding_days == null ? '-' : `${number(row.average_holding_days, 1)} 天`}</td>
                  <td>{money(row.total_fee)}</td>
                  <td><span className={`ledger-reconcile ${row.status === 'partial' ? 'mismatch' : row.status === 'available' ? 'matched' : ''}`}>{behaviorStatusLabel(row.status)}</span></td>
                </tr>
              ))}
              {!behavior?.asset_reviews?.some((row) => row.sell_count > 0) && <tr><td colSpan="7" className="hint">录入至少一笔完整匹配的卖出流水后，这里才会形成真实交易行为复盘。</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="ledger-method">{behavior?.policy || behavior?.method?.matching}</p>
      </section>

      <section className="ledger-grid ledger-history-grid">
        <div className="ledger-section">
          <div className="ledger-section-head">
            <div>
              <span className="eyebrow">组合快照</span>
              <h4>已确认持仓的历史时点</h4>
            </div>
            <BookOpenCheck size={19} aria-hidden="true" />
          </div>
          {snapshotChange && <div className="ledger-snapshot-change"><span>最近两次账面变化</span><b className={deltaClass(snapshotChange.amount_change)}>{money(snapshotChange.amount_change)}</b><small>{snapshotChange.from} 至 {snapshotChange.to}</small></div>}
          <div className="corr-wrap">
            <table className="compact-table ledger-table">
              <thead><tr><th>记录时间</th><th>持仓数</th><th>总金额</th><th>累计收益</th><th>原因</th></tr></thead>
              <tbody>
                {snapshots?.items?.slice(0, 8).map((row) => (
                  <tr key={row.id}>
                    <td>{row.captured_at?.replace('T', ' ') || '-'}</td>
                    <td>{row.holding_count}</td>
                    <td>{money(row.total_amount)}</td>
                    <td className={deltaClass(row.total_profit)}>{money(row.total_profit)}</td>
                    <td>{row.reason === 'holding_saved' ? '持仓更新' : row.reason === 'holding_deleted' ? '持仓删除' : '手动复盘'}</td>
                  </tr>
                ))}
                {!snapshots?.items?.length && <tr><td colSpan="5" className="hint">保存持仓或点击“记录当前快照”后，这里才会出现真实历史时点。</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="ledger-method">{snapshots?.method}</p>
        </div>

        <div className="ledger-section">
          <div className="ledger-section-head">
            <div>
              <span className="eyebrow">交易明细</span>
              <h4>最近录入流水</h4>
            </div>
            <Plus size={19} aria-hidden="true" />
          </div>
          <div className="corr-wrap">
            <table className="compact-table ledger-table">
              <thead><tr><th>日期</th><th>资产</th><th>方向</th><th>份额</th><th>单价</th><th>费用</th><th>来源</th><th></th></tr></thead>
              <tbody>
                {transactions?.items?.slice(0, 12).map((row) => (
                  <tr key={row.id}>
                    <td>{row.trade_date}</td>
                    <td><b>{row.code}</b><small>{row.name || '-'}</small></td>
                    <td><span className={`trade-kind ${row.trade_type}`}>{row.trade_label}</span></td>
                    <td>{number(row.shares, 6)}</td>
                    <td>{money(row.unit_price, 6)}</td>
                    <td>{money(row.fee)}</td>
                    <td><span className="ledger-transaction-source">{row.source_label || '-'}</span></td>
                    <td><button className="ghost ledger-delete" onClick={() => removeTransaction(row.id)} title="删除流水" aria-label="删除流水"><Trash2 size={15} aria-hidden="true" /></button></td>
                  </tr>
                ))}
                {!transactions?.items?.length && <tr><td colSpan="8" className="hint">暂无已录入交易流水。</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  )
}
