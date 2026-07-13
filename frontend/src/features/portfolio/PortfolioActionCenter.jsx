import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ChevronRight,
  FileText,
  Layers3,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from 'lucide-react'

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
  if (['pause_add', 'risk_review'].includes(action)) return 'warning'
  if (action === 'data_required') return 'blocked'
  return 'normal'
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

function HoldingDetail({ row, report, onClose, onDelete }) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  if (!row) return null
  const decision = row.decision || {}
  const trend = row.trend || null
  const reportFund = (report?.overlap?.funds || []).find((item) => item.code === row.code)

  async function remove() {
    if (!window.confirm(`确认删除 ${row.name || row.code} 的持仓记录？`)) return
    await onDelete(row.id)
    onClose()
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
          {trend ? (
            <dl className="portfolio-detail-facts">
              <div><dt>净值日期</dt><dd>{trend.as_of || '-'}</dd></div>
              <div><dt>趋势状态</dt><dd>{trend.trend_state || '-'}</dd></div>
              <div><dt>近3月</dt><dd className={deltaClass(trend.return_3m)}>{percent(trend.return_3m, true)}</dd></div>
              <div><dt>近1年</dt><dd className={deltaClass(trend.return_1y)}>{percent(trend.return_1y, true)}</dd></div>
              <div><dt>当前回撤</dt><dd className="delta-neg">{percent(trend.current_drawdown)}</dd></div>
              <div><dt>历史最大回撤</dt><dd className="delta-neg">{percent(trend.max_drawdown)}</dd></div>
            </dl>
          ) : <p className="portfolio-empty-line">没有成功返回的真实基金趋势证据，相关操作结论已停止。</p>}
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
            <p>每只持仓一行，按操作优先级排序；点击查看规则、趋势和重复暴露。</p>
          </div>
          <span>{rows.length} 项</span>
        </div>
        {rows.length > 0 ? (
          <div className="portfolio-holding-list">
            <div className="portfolio-holding-columns" aria-hidden="true">
              <span>持仓</span><span>金额</span><span>占比</span><span>累计收益</span><span>操作状态</span><span></span>
            </div>
            {rows.map((row) => (
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
                <span data-label="操作状态"><i className={`portfolio-action-badge ${actionTone(row.decision?.action)}`}>{row.decision?.label || '待复核'}</i></span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            ))}
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
        <HoldingDetail row={selected} report={activeReport} onClose={() => setSelectedCode(null)} onDelete={onDelete} />
      )}
    </>
  )
}
