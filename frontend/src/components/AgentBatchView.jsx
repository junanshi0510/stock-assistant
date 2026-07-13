import {
  ArrowUpRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Layers3,
  RefreshCw,
  ShieldCheck,
  Square,
} from 'lucide-react'

const TERMINAL = new Set(['completed', 'partial', 'failed', 'cancelled', 'abstained'])

const STATUS = {
  queued: ['等待执行', 'queued'],
  running: ['正在研究', 'running'],
  completed: ['证据完整', 'complete'],
  partial: ['部分完成', 'partial'],
  failed: ['执行失败', 'failed'],
  cancelled: ['已取消', 'cancelled'],
  abstained: ['数据不足', 'partial'],
}

const ACTIONS = {
  consider_tranche: '满足条件后分批',
  hold_review: '持有并复核',
  hold_no_add: '持有但不加仓',
  wait: '等待改善',
  do_not_add: '当前不加仓',
  reduce_exposure: '降低暴露',
  research_only: '仅作研究',
  setup_required: '先完善约束',
  strategy_not_released: '策略未发布',
  market_data_required: '等待市场数据',
  exposure_data_required: '补齐组合穿透',
  budget_required: '确认预算',
  research: '可继续研究',
  avoid_for_now: '当前暂缓',
  data_required: '数据不足',
}

function statusMeta(status) {
  return STATUS[status] || [status || '未知', 'partial']
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function timeText(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? String(value).replace('T', ' ')
    : parsed.toLocaleString('zh-CN', { hour12: false })
}

function Distribution({ label, items }) {
  const values = items || []
  return (
    <div className="agent-batch-distribution">
      <span>{label}</span>
      <div>
        {values.length > 0
          ? values.slice(0, 4).map((item) => <b key={item.key}>{item.key} <em>{item.count}</em></b>)
          : <b>等待结果</b>}
      </div>
    </div>
  )
}

export default function AgentBatchView({
  batch,
  loading = false,
  selectedRunId = '',
  onRefresh,
  onCancel,
  onSelectRun,
}) {
  if (!batch) return null
  const [statusLabel, statusTone] = statusMeta(batch.status)
  const progress = batch.progress || {}
  const summary = batch.summary || {}
  const overlap = batch.holding_overlap || {}
  const active = !TERMINAL.has(batch.status)

  return (
    <section className="agent-batch-view" aria-label="批量基金研究结果" aria-live="polite">
      <div className="agent-batch-head">
        <div>
          <span className="eyebrow">Multi-fund Research Batch</span>
          <h3><Layers3 size={19} aria-hidden="true" />批量基金决策</h3>
          <small>Batch ID: {batch.id} · {timeText(batch.created_at)}</small>
        </div>
        <div className="agent-batch-head-actions">
          <span className={`agent-status ${statusTone}`}>{statusLabel}</span>
          <button className="ghost" type="button" onClick={onRefresh} disabled={loading} title="刷新批次状态">
            <RefreshCw size={15} className={loading ? 'spin-icon' : ''} aria-hidden="true" />刷新
          </button>
          {active && (
            <button className="ghost" type="button" onClick={onCancel} title="取消批次中尚未结束的任务">
              <Square size={14} aria-hidden="true" />取消批次
            </button>
          )}
        </div>
      </div>

      <div className="agent-batch-progress">
        <div><span style={{ width: `${Math.max(active ? 6 : 0, progress.percent || 0)}%` }} /></div>
        <p>
          已结束 <b>{progress.terminal || 0}/{progress.total || 0}</b>
          <span>完整 {progress.completed || 0}</span>
          <span>部分 {progress.partial || 0}</span>
          <span>失败 {progress.failed || 0}</span>
        </p>
      </div>

      <div className="agent-batch-summary-strip">
        <Distribution label="市场分布" items={summary.markets} />
        <Distribution label="风险分布" items={summary.risk_bands} />
        <Distribution label="动作分布" items={(summary.actions || []).map((item) => ({ ...item, key: ACTIONS[item.key] || item.key }))} />
        <div className="agent-batch-coverage">
          <span>证据覆盖</span>
          <b><Database size={13} aria-hidden="true" />市场情报 {summary.market_intelligence_available || 0}/{progress.total || 0}</b>
          <b><ShieldCheck size={13} aria-hidden="true" />模型研判 {summary.model_available || 0}/{progress.total || 0}</b>
        </div>
      </div>

      {(summary.warnings || []).length > 0 && (
        <div className="agent-batch-warnings">
          {summary.warnings.map((warning) => <p key={warning}><CircleAlert size={14} aria-hidden="true" />{warning}</p>)}
        </div>
      )}

      <div className="agent-batch-table" role="table" aria-label="批量基金逐只结果">
        <div className="agent-batch-row heading" role="row">
          <span>基金</span><span>状态</span><span>市场 / 风险</span><span>研究动作</span><span>近 1 年 / 回撤</span><span>证据</span><span />
        </div>
        {(batch.items || []).map((item) => {
          const [label, tone] = statusMeta(item.status)
          const selected = item.run_id === selectedRunId
          const coverage = item.coverage || {}
          const decision = item.decision || {}
          return (
            <div className={`agent-batch-row ${selected ? 'selected' : ''}`} role="row" key={item.run_id}>
              <span className="agent-batch-fund">
                <b>{item.code} {item.fund?.name || '等待基金信息'}</b>
                <small>数据截至 {item.fund?.as_of || '-'}</small>
              </span>
              <span><em className={`agent-status ${tone}`}>{label}</em></span>
              <span className="agent-batch-market">
                <b>{item.market?.label || '-'}</b>
                <small>{decision.risk_band || '等待风险分析'}</small>
              </span>
              <span className="agent-batch-action">
                <b>{ACTIONS[decision.action] || decision.action || '-'}</b>
                <small>{decision.timing_label || decision.role || '-'}</small>
              </span>
              <span className="agent-batch-metrics">
                <b>{pct(item.metrics?.return_1y)}</b>
                <small>当前回撤 {pct(item.metrics?.current_drawdown)}</small>
              </span>
              <span className="agent-batch-evidence-state">
                {coverage.market_intelligence === 'available' || coverage.market_intelligence === 'partial'
                  ? <CheckCircle2 size={14} aria-label="市场情报可用" />
                  : <Clock3 size={14} aria-label="等待市场情报" />}
                <small>新闻 {coverage.news_count || 0} · AI {coverage.model === 'available' ? '可用' : '未形成'}</small>
              </span>
              <span>
                <button className="ghost icon-text" type="button" onClick={() => onSelectRun(item.run_id)}>
                  <ArrowUpRight size={14} aria-hidden="true" />详情
                </button>
              </span>
              {item.error_message && <p className="agent-batch-row-error"><CircleAlert size={13} aria-hidden="true" />{item.error_message}</p>}
            </div>
          )
        })}
      </div>

      <section className="agent-batch-overlap" aria-label="批次持仓重合分析">
        <div className="agent-section-head">
          <div>
            <span className="eyebrow">Disclosed Holding Overlap</span>
            <h3>披露持仓重合下界</h3>
          </div>
          <span>覆盖 {overlap.covered_fund_count || 0}/{overlap.total_fund_count || 0} 只</span>
        </div>
        {(overlap.pairs || []).length > 0 ? (
          <div className="agent-batch-overlap-list">
            {overlap.pairs.map((pair) => (
              <div key={`${pair.left_code}-${pair.right_code}`}>
                <b>{pair.left_code} ↔ {pair.right_code}</b>
                <strong>{pct(pair.overlap_lower_bound_pct)}</strong>
                <span>{(pair.shared_holdings || []).slice(0, 4).map((holding) => holding.name).join('、')}</span>
                <small>共同披露持仓 {pair.shared_holding_count} 只</small>
              </div>
            ))}
          </div>
        ) : (
          <p className="agent-batch-overlap-empty">
            {active ? '等待至少两只基金完成持仓情报。' : '本批次已覆盖的前 N 大持仓中没有发现共同标的，或持仓数据不足。'}
          </p>
        )}
        <p className="agent-batch-policy">{overlap.policy || '只使用真实披露持仓，不推断缺失部分。'}</p>
      </section>

      <p className="agent-batch-policy">{batch.policy}</p>
    </section>
  )
}
