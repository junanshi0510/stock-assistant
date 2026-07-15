import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  BookOpenCheck,
  ChevronRight,
  FileText,
  Layers3,
  Pencil,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import AssetLevelRecurrenceView from '../../components/AssetLevelRecurrenceView'
import FundConditionedForwardView from '../../components/FundConditionedForwardView'
import FundPeerPersistenceView from '../../components/FundPeerPersistenceView'
import { fetchFundAlternatives, fetchFundPeerPersistence } from '../../api/funds'

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function percent(value, signed = false) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function deltaClass(value) {
  if (Number(value) > 0) return 'delta-pos'
  if (Number(value) < 0) return 'delta-neg'
  return 'delta-zero'
}

function actionTone(action) {
  if (action === 'reduce_review') return 'critical'
  if (['pause_add', 'risk_review', 'thesis_review'].includes(action)) return 'warning'
  if (action === 'data_required') return 'blocked'
  return 'normal'
}

const THESIS_ROLES = [
  ['core_growth', '核心增长'],
  ['satellite_growth', '卫星增强'],
  ['defensive', '防守稳定'],
  ['income', '现金流'],
  ['diversifier', '分散风险'],
  ['tactical', '阶段机会'],
]

function holdingKey(item) {
  return `${item?.asset_type || ''}:${item?.market || ''}:${item?.code || ''}`
}

function priorityLabel(priority) {
  return { high: '优先', medium: '随后', normal: '例行' }[priority] || priority
}

function reportStatus(status) {
  return { reviewable: '可复核', partial: '部分可用', blocked: '受限', not_generated: '未生成' }[status] || status || '-'
}

function sourceLabel(source) {
  if (source === 'tiantian_fund_export') return '天天基金导出'
  if (source === 'holdings_file_import') return '持仓账单导入'
  if (source === 'manual') return '手动录入'
  if (String(source || '').includes('ocr')) return '截图识别'
  return source || '-'
}

function evidenceValue(item) {
  const label = String(item?.label || '')
  const value = item?.value
  if (value == null || value === '') return '-'
  if (label.includes('占比') || label.includes('收益率') || label.includes('回撤') || label.includes('重合') || label.includes('上限')) {
    return percent(value)
  }
  if (label.includes('金额') || label.includes('收益')) return money(value)
  return String(value)
}

function levelValue(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return number.toFixed(number >= 100 ? 2 : 4)
}

function shortLevelDate(value) {
  if (!value || typeof value !== 'string') return '-'
  const parts = value.split('-')
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : value
}

function recurrenceMeta(record, loading, error) {
  const data = record?.recurrence
  if (!data) {
    if (loading) return { tone: 'loading', primary: '读取中', secondary: '真实行情与历史', title: '正在读取真实来源' }
    if (error) return { tone: 'unavailable', primary: '批量读取失败', secondary: error, title: error }
    return { tone: 'unavailable', primary: '未返回', secondary: '没有收到该持仓结果', title: '没有收到该持仓结果' }
  }

  const target = data.target || {}
  const occurrence = data.occurrence || {}
  const nearest = data.nearest || {}
  const targetText = target.value == null ? '' : `当前 ${levelValue(target.value)}`
  const sourceText = [targetText, target.as_of, target.source].filter(Boolean).join(' · ')
  if (data.status === 'reached' || data.status === 'reached_exact') {
    const days = occurrence.calendar_days_ago
    return {
      tone: 'matched',
      primary: occurrence.date || '-',
      secondary: [targetText, days == null ? '' : `${days} 天前`].filter(Boolean).join(' · '),
      title: sourceText,
    }
  }
  if (data.status === 'crossed_between') {
    const fromValue = levelValue(occurrence.from_value)
    const toValue = levelValue(occurrence.to_value)
    const targetValue = levelValue(data.target?.value)
    return {
      tone: 'matched',
      primary: `${fromValue} → ${toValue}`,
      secondary: `覆盖当前 ${targetValue} · ${shortLevelDate(occurrence.from_date)} 至 ${shortLevelDate(occurrence.to_date)}`,
      title: `确认净值从 ${fromValue} 变动至 ${toValue}，覆盖当前盘中估值 ${targetValue}；确认净值日期 ${occurrence.from_date || '-'} 至 ${occurrence.to_date || '-'}。${sourceText}`,
    }
  }
  if (data.status === 'not_found_in_coverage') {
    return {
      tone: 'nearest',
      primary: '覆盖期未到达',
      secondary: nearest.date ? `最近值 ${nearest.date}` : '没有可比历史值',
      title: sourceText,
    }
  }
  return {
    tone: 'unavailable',
    primary: '数据不可用',
    secondary: data.reason || '真实来源当前不可用',
    title: data.reason || sourceText,
  }
}

function fallbackRows(items, total) {
  return items.map((item) => ({
    ...item,
    allocation_ratio: total > 0 && Number(item.amount) > 0 ? Number(item.amount) / total * 100 : null,
    decision: {
      action: 'data_required',
      label: '等待生成行动报告',
      rationale: '尚无与当前持仓绑定且完整性通过的行动报告。',
      blockers: ['report_not_current'],
    },
    evidence: [],
    overlap: [],
  }))
}

function HoldingThesisSection({ row, record, assessment, saving, onSave }) {
  const payload = record?.payload || {}
  const [editing, setEditing] = useState(!record)
  const [message, setMessage] = useState('')
  const [form, setForm] = useState({
    role: '',
    thesis_summary: '',
    expected_holding_months: '',
    review_date: '',
    max_loss_pct: '',
    max_drawdown_pct: '',
    add_condition: '',
    exit_condition: '',
  })

  useEffect(() => {
    const current = record?.payload || {}
    setForm({
      role: current.role || '',
      thesis_summary: current.thesis_summary || '',
      expected_holding_months: current.expected_holding_months ?? '',
      review_date: current.review_date || '',
      max_loss_pct: current.max_loss_pct ?? '',
      max_drawdown_pct: current.max_drawdown_pct ?? '',
      add_condition: current.add_condition || '',
      exit_condition: current.exit_condition || '',
    })
    setEditing(!record)
    setMessage('')
  }, [record?.id, row.asset_type, row.market, row.code])

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const canSave = Boolean(
    form.role
    && form.thesis_summary.trim().length >= 12
    && Number(form.expected_holding_months) >= 1
    && form.review_date
    && Number(form.max_loss_pct) >= 1
    && Number(form.max_drawdown_pct) >= 1
    && form.add_condition.trim().length >= 6
    && form.exit_condition.trim().length >= 6
  )

  async function submit(event) {
    event.preventDefault()
    setMessage('')
    try {
      await onSave({
        asset_type: row.asset_type,
        market: row.market || '',
        code: row.code,
        ...form,
        expected_holding_months: Number(form.expected_holding_months),
        max_loss_pct: Number(form.max_loss_pct),
        max_drawdown_pct: Number(form.max_drawdown_pct),
      })
      setEditing(false)
      setMessage('已保存新版本；旧行动报告已失效。')
    } catch (error) {
      setMessage(error?.message || '持有逻辑保存失败')
    }
  }

  const assessmentLabel = assessment?.label || (record ? '等待刷新行动报告' : '尚未建立计划')

  return (
    <section className="portfolio-detail-section thesis-section">
      <div className="thesis-section-head">
        <div>
          <h4><BookOpenCheck size={16} /> 持有逻辑与退出纪律</h4>
          <span className={`thesis-review-status ${assessment?.status || 'pending'}`}>{assessmentLabel}</span>
        </div>
        {record && !editing && (
          <button type="button" className="ghost compact-action" onClick={() => setEditing(true)}>
            <Pencil size={15} /> 修订计划
          </button>
        )}
      </div>

      {!editing && record ? (
        <div className="thesis-read-view">
          <dl className="portfolio-detail-facts">
            <div><dt>组合角色</dt><dd>{payload.role_label || '-'}</dd></div>
            <div><dt>计划持有</dt><dd>{payload.expected_holding_months || '-'} 个月</dd></div>
            <div><dt>下次复核</dt><dd>{payload.review_date || '-'}</dd></div>
            <div><dt>持仓亏损边界</dt><dd>-{percent(payload.max_loss_pct)}</dd></div>
            <div><dt>标的回撤边界</dt><dd>-{percent(payload.max_drawdown_pct)}</dd></div>
            <div><dt>版本</dt><dd>v{record.version_no || '-'} · {record.integrity_verified ? '哈希已验证' : '完整性失败'}</dd></div>
          </dl>
          <div className="thesis-statement">
            <span>买入与持有逻辑</span>
            <p>{payload.thesis_summary}</p>
          </div>
          <div className="thesis-condition-grid">
            <div><span>新增条件</span><p>{payload.add_condition}</p></div>
            <div><span>退出条件</span><p>{payload.exit_condition}</p></div>
          </div>
          {assessment?.breaches?.length > 0 && (
            <div className="thesis-breach-list">
              {assessment.breaches.map((item) => (
                <div key={item.code}>
                  <AlertTriangle size={15} />
                  <span>{item.label}</span>
                  <b>{percent(item.actual, true)} / 边界 {percent(item.limit, true)}</b>
                </div>
              ))}
            </div>
          )}
          <small className="thesis-version-hash">{record.payload_sha256?.slice(0, 16) || '-'}…</small>
        </div>
      ) : (
        <form className="thesis-form" onSubmit={submit}>
          <label>
            <span>组合角色</span>
            <select value={form.role} onChange={(event) => update('role', event.target.value)} required>
              <option value="">请选择</option>
              {THESIS_ROLES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <label className="thesis-form-wide">
            <span>买入与持有逻辑</span>
            <textarea value={form.thesis_summary} onChange={(event) => update('thesis_summary', event.target.value)} maxLength={600} required />
          </label>
          <label>
            <span>计划持有月数</span>
            <input type="number" min="1" max="240" value={form.expected_holding_months} onChange={(event) => update('expected_holding_months', event.target.value)} required />
          </label>
          <label>
            <span>下次复核日期</span>
            <input type="date" value={form.review_date} onChange={(event) => update('review_date', event.target.value)} required />
          </label>
          <label>
            <span>最大可接受持仓亏损 %</span>
            <input type="number" min="1" max="80" step="0.1" value={form.max_loss_pct} onChange={(event) => update('max_loss_pct', event.target.value)} required />
          </label>
          <label>
            <span>最大可接受标的回撤 %</span>
            <input type="number" min="1" max="80" step="0.1" value={form.max_drawdown_pct} onChange={(event) => update('max_drawdown_pct', event.target.value)} required />
          </label>
          <label className="thesis-form-wide">
            <span>允许新增的前提</span>
            <textarea value={form.add_condition} onChange={(event) => update('add_condition', event.target.value)} maxLength={600} required />
          </label>
          <label className="thesis-form-wide">
            <span>需要退出或降低仓位的条件</span>
            <textarea value={form.exit_condition} onChange={(event) => update('exit_condition', event.target.value)} maxLength={600} required />
          </label>
          <div className="thesis-form-actions thesis-form-wide">
            {record && <button type="button" className="ghost" onClick={() => setEditing(false)}>取消</button>}
            <button type="submit" disabled={!canSave || saving}>
              <Save size={15} /> {saving ? '保存中' : '保存新版本'}
            </button>
          </div>
          <p className="thesis-policy thesis-form-wide">自由文本条件由你人工确认；系统只自动核验复核日期、持仓亏损和真实净值回撤，不自动交易。</p>
        </form>
      )}
      {message && <div className="thesis-save-message">{message}</div>}
    </section>
  )
}

function HoldingDetail({
  row,
  report,
  thesisRecord,
  thesisSaving,
  onSaveThesis,
  levelRecord,
  levelLoading,
  levelError,
  onClose,
  onDelete,
}) {
  const [peerPersistence, setPeerPersistence] = useState(null)
  const [peerPersistenceLoading, setPeerPersistenceLoading] = useState(false)
  const [peerPersistenceError, setPeerPersistenceError] = useState('')
  const [peerReloadKey, setPeerReloadKey] = useState(0)
  const [peerAlternatives, setPeerAlternatives] = useState(null)
  const [peerAlternativesLoading, setPeerAlternativesLoading] = useState(false)
  const [peerAlternativesError, setPeerAlternativesError] = useState('')

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => {
    let active = true
    setPeerPersistence(null)
    setPeerPersistenceError('')
    setPeerAlternatives(null)
    setPeerAlternativesError('')
    if (row?.asset_type !== 'fund' || !row?.code) {
      setPeerPersistenceLoading(false)
      return () => { active = false }
    }
    setPeerPersistenceLoading(true)
    fetchFundPeerPersistence(row.code)
      .then((result) => {
        if (active) setPeerPersistence(result)
      })
      .catch((requestError) => {
        if (active) setPeerPersistenceError(requestError?.message || '真实同类持续性诊断失败')
      })
      .finally(() => {
        if (active) setPeerPersistenceLoading(false)
      })
    return () => { active = false }
  }, [row?.asset_type, row?.code, peerReloadKey])

  if (!row) return null
  const decision = row.decision || {}
  const trend = row.trend || null
  const reportFund = (report?.overlap?.funds || []).find((item) => item.code === row.code)

  async function remove() {
    if (!window.confirm(`确认删除 ${row.name || row.code} 的持仓记录？`)) return
    await onDelete(row.id)
    onClose()
  }

  async function loadPeerAlternatives() {
    setPeerAlternativesLoading(true)
    setPeerAlternativesError('')
    try {
      setPeerAlternatives(await fetchFundAlternatives(row.code, '1y', 3, 36))
    } catch (requestError) {
      setPeerAlternativesError(requestError?.message || '真实同类替代候选读取失败')
    } finally {
      setPeerAlternativesLoading(false)
    }
  }

  return (
    <div className="portfolio-detail-layer">
      <button className="portfolio-detail-backdrop" onClick={onClose} aria-label="关闭持仓详情" />
      <aside className="portfolio-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="holding-detail-title">
        <div className="portfolio-detail-head">
          <div>
            <span className="portfolio-code">{row.code}</span>
            <h3 id="holding-detail-title">{row.name || row.code}</h3>
            <p>{row.asset_type === 'fund' ? '基金' : '股票'} · {row.market || '-'} · {sourceLabel(row.source)}</p>
          </div>
          <button className="icon-button" onClick={onClose} title="关闭" aria-label="关闭">
            <X size={18} />
          </button>
        </div>

        <div className={`portfolio-decision-callout ${actionTone(decision.action)}`}>
          <span>当前操作状态</span>
          <strong>{decision.label || '待复核'}</strong>
          <p>{decision.rationale || '-'}</p>
          {decision.review_amount != null && <b>复核超限金额约 {money(decision.review_amount)}</b>}
        </div>

        <HoldingThesisSection
          row={row}
          record={thesisRecord}
          assessment={row.thesis_review}
          saving={thesisSaving}
          onSave={onSaveThesis}
        />

        <section className="portfolio-detail-section">
          <h4>持仓事实</h4>
          <dl className="portfolio-detail-facts">
            <div><dt>确认金额</dt><dd>{money(row.amount)}</dd></div>
            <div><dt>组合占比</dt><dd>{percent(row.allocation_ratio)}</dd></div>
            <div><dt>累计收益</dt><dd className={deltaClass(row.profit)}>{money(row.profit)}</dd></div>
            <div><dt>累计收益率</dt><dd className={deltaClass(row.profit_rate)}>{percent(row.profit_rate, true)}</dd></div>
            <div><dt>昨日收益</dt><dd className={deltaClass(row.yesterday_profit)}>{money(row.yesterday_profit)}</dd></div>
            <div><dt>已确认份额</dt><dd>{row.shares == null ? '-' : Number(row.shares).toLocaleString('zh-CN', { maximumFractionDigits: 6 })}</dd></div>
          </dl>
        </section>

        <section className="portfolio-detail-section">
          <h4>真实市场证据</h4>
          {levelRecord?.recurrence ? (
            <AssetLevelRecurrenceView data={levelRecord.recurrence} />
          ) : (
            <div className={`portfolio-level-placeholder ${levelError ? 'error-state' : ''}`}>
              <RefreshCw size={15} className={levelLoading ? 'spin-icon' : ''} />
              <span>{levelLoading ? '正在读取当前估值与历史到达时间' : levelError || '该持仓尚未返回估值历史到达结果'}</span>
            </div>
          )}
          {trend ? (
            <dl className="portfolio-detail-facts">
              <div><dt>净值日期</dt><dd>{trend.as_of || '-'}</dd></div>
              <div><dt>趋势状态</dt><dd>{trend.trend_state || '-'}</dd></div>
              <div><dt>近3月</dt><dd className={deltaClass(trend.return_3m)}>{percent(trend.return_3m, true)}</dd></div>
              <div><dt>近1年</dt><dd className={deltaClass(trend.return_1y)}>{percent(trend.return_1y, true)}</dd></div>
              <div><dt>当前回撤</dt><dd className="delta-neg">{percent(trend.current_drawdown)}</dd></div>
              <div><dt>历史最大回撤</dt><dd className="delta-neg">{percent(trend.max_drawdown)}</dd></div>
            </dl>
          ) : <p className="portfolio-empty-line">{row.asset_type === 'fund' ? '没有成功返回的真实基金趋势证据，相关操作结论已停止。' : '当前行动报告没有绑定股票趋势证据。'}</p>}
          {row.asset_type === 'fund' && levelRecord?.conditioned_forward && (
            <FundConditionedForwardView data={levelRecord.conditioned_forward} />
          )}
          {row.asset_type === 'fund' && (
            <FundPeerPersistenceView
              data={peerPersistence}
              loading={peerPersistenceLoading}
              error={peerPersistenceError}
              onRetry={() => setPeerReloadKey((value) => value + 1)}
              onLoadAlternatives={loadPeerAlternatives}
              alternatives={peerAlternatives}
              alternativesLoading={peerAlternativesLoading}
              alternativesError={peerAlternativesError}
            />
          )}
          {reportFund && (
            <p className="portfolio-source-line">
              持仓披露期：{reportFund.stock_period || '-'} · 行业披露期：{reportFund.industry_period || '-'}
            </p>
          )}
        </section>

        <section className="portfolio-detail-section">
          <h4>重复暴露</h4>
          {row.overlap?.length ? row.overlap.map((pair) => (
            <div className="portfolio-overlap-detail" key={`${row.code}-${pair.peer_code}`}>
              <div>
                <strong>{pair.peer_name || pair.peer_code}</strong>
                <span>{pair.peer_code} · {pair.level}</span>
              </div>
              <div className="portfolio-overlap-numbers">
                <span>个股 {percent(pair.stock_overlap_weight)}</span>
                <span>行业 {percent(pair.industry_overlap_weight)}</span>
              </div>
              {pair.common_stocks?.length > 0 && (
                <p>共同持股：{pair.common_stocks.slice(0, 6).map((stock) => `${stock.name || stock.code} ${percent(stock.min_ratio)}`).join('、')}</p>
              )}
            </div>
          )) : <p className="portfolio-empty-line">当前报告未发现该持仓与其他基金的成对重复证据。</p>}
        </section>

        <section className="portfolio-detail-section">
          <h4>规则与证据</h4>
          <div className="portfolio-evidence-list">
            {(row.evidence || []).map((item, index) => (
              <div key={`${item.label}-${index}`}>
                <span>{item.label}</span>
                <b>{evidenceValue(item)}</b>
                <small>{item.source}</small>
              </div>
            ))}
            {!row.evidence?.length && <p className="portfolio-empty-line">刷新行动报告后查看绑定证据。</p>}
          </div>
        </section>

        <footer className="portfolio-detail-footer">
          <div>
            <span>持仓更新 {row.updated_at || '-'}</span>
            <span>规则 {report?.ruleset_version || '-'}</span>
          </div>
          <button className="danger-button" onClick={remove}>
            <Trash2 size={16} /> 删除持仓
          </button>
        </footer>
      </aside>
    </div>
  )
}

export default function PortfolioActionCenter({
  report,
  items,
  loading,
  onRefresh,
  onOpenImport,
  onDelete,
  theses,
  thesisSaving,
  onSaveThesis,
  levelRecurrence,
  levelRecurrenceLoading,
  levelRecurrenceError,
  onRefreshLevelRecurrence,
}) {
  const [selectedCode, setSelectedCode] = useState(null)
  const total = useMemo(
    () => items.reduce((sum, item) => sum + (Number(item.amount) || 0), 0),
    [items],
  )
  const reportCurrent = Boolean(report?.binding?.current && report?.integrity?.verified)
  const activeReport = reportCurrent ? report : null
  const rows = activeReport?.holdings?.length ? activeReport.holdings : fallbackRows(items, total)
  const selected = rows.find((row) => `${row.asset_type}:${row.market}:${row.code}` === selectedCode) || null
  const thesisMap = useMemo(
    () => new Map((theses?.items || []).map((item) => [holdingKey(item), item])),
    [theses],
  )
  const levelRecurrenceMap = useMemo(
    () => new Map((levelRecurrence?.items || []).map((item) => [item.key || holdingKey(item), item])),
    [levelRecurrence],
  )
  const selectedThesis = selected ? thesisMap.get(holdingKey(selected)) || null : null
  const selectedLevelRecord = selected ? levelRecurrenceMap.get(holdingKey(selected)) || null : null
  const summary = activeReport?.summary || {
    holding_count: items.length,
    total_amount: total || null,
    total_profit: items.reduce((sum, item) => sum + (Number(item.profit) || 0), 0),
  }
  const steps = activeReport?.strategy?.steps || []
  const pairs = activeReport?.overlap?.pairs || []
  const overlapFunds = Object.fromEntries((activeReport?.overlap?.funds || []).map((fund) => [fund.code, fund]))

  return (
    <>
      <section className="portfolio-command-band">
        <div>
          <span className="eyebrow">PORTFOLIO ACTION CENTER</span>
          <h3>持仓行动中心</h3>
          <p>{activeReport?.objective || '从真实持仓、投资政策、基金净值和定期报告生成可审计的操作顺序。'}</p>
        </div>
        <div className="portfolio-command-actions">
          <button onClick={onRefresh} disabled={loading || items.length === 0}>
            <RefreshCw size={16} className={loading ? 'spin-icon' : ''} />
            {loading ? '分析中' : '刷新行动报告'}
          </button>
          <button className="ghost" onClick={onOpenImport}>
            <Upload size={16} /> 维护持仓
          </button>
        </div>
      </section>

      {!reportCurrent && items.length > 0 && (
        <div className="portfolio-report-alert">
          <AlertTriangle size={17} />
          <div>
            <strong>{report?.report ? '持仓或投资政策已变化，旧报告停止使用' : '尚未生成持仓行动报告'}</strong>
            <span>刷新后才会展示操作优先级和基金重复暴露；系统不会沿用旧结论。</span>
          </div>
        </div>
      )}

      <section className="portfolio-summary-band" aria-label="组合摘要">
        <div><span>持仓</span><strong>{summary.holding_count || 0}</strong></div>
        <div><span>确认总额</span><strong>{money(summary.total_amount)}</strong></div>
        <div><span>累计收益</span><strong className={deltaClass(summary.total_profit)}>{money(summary.total_profit)}</strong></div>
        <div><span>第一大占比</span><strong>{percent(summary.top1_ratio)}</strong></div>
        <div><span>中高重合</span><strong>{summary.high_overlap_pair_count ?? '-'}</strong></div>
        <div><span>报告状态</span><strong>{reportStatus(activeReport?.status || 'not_generated')}</strong></div>
      </section>

      {activeReport && (
        <section className="portfolio-action-section">
          <div className="portfolio-section-head">
            <div>
              <h3>{activeReport.strategy?.title || '下一步操作'}</h3>
              <p>{activeReport.policy}</p>
            </div>
            <div className={`portfolio-integrity ${activeReport.integrity?.verified ? 'verified' : ''}`}>
              <ShieldCheck size={15} />
              <span>{activeReport.integrity?.verified ? '完整性已验证' : '完整性失败'}</span>
              <code>{activeReport.report?.payload_sha256?.slice(0, 12) || '-'}…</code>
            </div>
          </div>
          <div className="portfolio-action-list">
            {steps.map((step, index) => (
              <article className={`portfolio-action-row ${step.priority}`} key={step.id}>
                <span className="portfolio-action-index">{String(index + 1).padStart(2, '0')}</span>
                <div className="portfolio-action-copy">
                  <div><span>{priorityLabel(step.priority)}</span><strong>{step.title}</strong></div>
                  <p>{step.instruction}</p>
                  <small>{step.why}</small>
                </div>
                {step.target_codes?.length > 0 && <code>{step.target_codes.join(' / ')}</code>}
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="portfolio-holdings-section">
        <div className="portfolio-section-head">
          <div>
            <h3>持仓清单</h3>
            <p>每只持仓一行，按操作优先级排序；点击查看估值回溯、趋势、规则和重复暴露。</p>
          </div>
          <div className="portfolio-level-status">
            <span>
              {levelRecurrenceLoading
                ? '估值回溯读取中'
                : `${levelRecurrence?.summary?.available_count ?? 0}/${rows.length} 项可用`}
            </span>
            <button
              className="icon-button"
              onClick={onRefreshLevelRecurrence}
              disabled={levelRecurrenceLoading || rows.length === 0}
              title="刷新当前估值历史到达时间"
              aria-label="刷新当前估值历史到达时间"
            >
              <RefreshCw size={15} className={levelRecurrenceLoading ? 'spin-icon' : ''} />
            </button>
          </div>
        </div>
        {rows.length > 0 ? (
          <div className="portfolio-holding-list">
            <div className="portfolio-holding-columns" aria-hidden="true">
              <span>持仓</span><span>金额</span><span>占比</span><span>累计收益</span><span>上次到达当前估值</span><span>操作状态</span><span></span>
            </div>
            {rows.map((row) => {
              const level = recurrenceMeta(
                levelRecurrenceMap.get(holdingKey(row)),
                levelRecurrenceLoading,
                levelRecurrenceError,
              )
              return (
                <button
                  className="portfolio-holding-row"
                  key={`${row.asset_type}-${row.market}-${row.code}`}
                  onClick={() => setSelectedCode(`${row.asset_type}:${row.market}:${row.code}`)}
                >
                  <span className="portfolio-holding-name">
                    <b>{row.name || row.code}</b>
                    <small>{row.code} · {row.asset_type === 'fund' ? '基金' : '股票'} · {row.market || '-'}</small>
                  </span>
                  <span data-label="金额"><b>{money(row.amount)}</b></span>
                  <span data-label="占比"><b>{percent(row.allocation_ratio)}</b></span>
                  <span data-label="累计收益" className={deltaClass(row.profit)}><b>{money(row.profit)}</b><small>{percent(row.profit_rate, true)}</small></span>
                  <span data-label="上次同估值" className={`portfolio-level-cell ${level.tone}`} title={level.title}>
                    <b>{level.primary}</b><small>{level.secondary}</small>
                  </span>
                  <span data-label="操作状态"><i className={`portfolio-action-badge ${actionTone(row.decision?.action)}`}>{row.decision?.label || '待复核'}</i></span>
                  <ChevronRight size={17} aria-hidden="true" />
                </button>
              )
            })}
          </div>
        ) : (
          <div className="portfolio-empty-state">
            <FileText size={24} />
            <strong>还没有已确认持仓</strong>
            <button className="ghost" onClick={onOpenImport}>导入或手动添加</button>
          </div>
        )}
      </section>

      {activeReport && (
        <section className="portfolio-overlap-section">
          <div className="portfolio-section-head">
            <div>
              <h3>基金重复暴露</h3>
              <p>{activeReport.overlap?.summary?.conclusion || '按基金定期报告逐对计算共同持股和行业重合。'}</p>
            </div>
            <span className="portfolio-source-status"><Layers3 size={15} /> {activeReport.overlap?.source || '基金定期报告'}</span>
          </div>
          {pairs.length > 0 ? (
            <div className="portfolio-overlap-table">
              <div className="portfolio-overlap-header"><span>基金组合</span><span>披露期</span><span>个股重合</span><span>行业重合</span><span>结论</span></div>
              {pairs.map((pair) => (
                <div className="portfolio-overlap-row" key={`${pair.fund_a}-${pair.fund_b}`}>
                  <span><b>{pair.fund_a_name || pair.fund_a}</b><small>{pair.fund_a} / {pair.fund_b}</small></span>
                  <span>{overlapFunds[pair.fund_a]?.stock_period || '-'}<small>{overlapFunds[pair.fund_b]?.stock_period || '-'}</small></span>
                  <span>{percent(pair.stock_overlap_weight)}<small>{pair.common_stock_count || 0} 只共同持股</small></span>
                  <span>{percent(pair.industry_overlap_weight)}</span>
                  <span><i className={`portfolio-overlap-level ${pair.level?.includes('高') || pair.level?.includes('中') ? 'elevated' : ''}`}>{pair.level || '-'}</i></span>
                </div>
              ))}
            </div>
          ) : (
            <p className="portfolio-empty-line">少于两只有可用定期报告的基金，当前没有可计算的成对重合度。</p>
          )}
          {activeReport.overlap?.error && <div className="error">真实基金重合度不可用：{activeReport.overlap.error}</div>}
          <p className="portfolio-method-line">{activeReport.overlap?.method?.note || activeReport.method?.overlap}</p>
        </section>
      )}

      {selected && (
        <HoldingDetail
          row={selected}
          report={activeReport}
          thesisRecord={selectedThesis}
          thesisSaving={thesisSaving}
          onSaveThesis={onSaveThesis}
          levelRecord={selectedLevelRecord}
          levelLoading={levelRecurrenceLoading}
          levelError={levelRecurrenceError}
          onClose={() => setSelectedCode(null)}
          onDelete={onDelete}
        />
      )}
    </>
  )
}
