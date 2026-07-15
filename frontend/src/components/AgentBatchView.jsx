import {
  ArrowUpRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Layers3,
  RefreshCw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Square,
  WalletCards,
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
  batch_allocation_pending: '进入组合资金分配',
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

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function PortfolioAllocation({ batch, active, allocating, onCreate }) {
  const allocation = batch.allocation
  const input = batch.input || {}
  const hasBudget = Number(input.planned_amount) > 0 && input.acknowledged_available_cash === true
  const canCreate = !active && hasBudget && input.include_portfolio_context === true

  if (!allocation) {
    return (
      <section className="agent-batch-allocation" aria-label="组合资金分配">
        <div className="agent-section-head">
          <div>
            <span className="eyebrow">Portfolio Capital Allocation</span>
            <h3><WalletCards size={18} aria-hidden="true" />组合资金分配复核</h3>
          </div>
          {hasBudget && <strong className="agent-allocation-budget">总预算 {money(input.planned_amount)}</strong>}
        </div>
        <div className="agent-allocation-empty">
          <ShieldAlert size={18} aria-hidden="true" />
          <div>
            <b>{active
              ? '等待全部基金完成真实数据研究'
              : hasBudget
                ? '尚未固化组合级资金分配'
                : '本批次是仅研究模式'}</b>
            <p>{active
              ? '组合分配必须绑定全部子 Run 的最终结果哈希，运行期间不会提前猜测金额。'
              : hasBudget
                ? '生成后将形成一次不可修改的分配快照；大模型只能解释，不能改变金额。'
                : '批次创建时没有确认唯一总预算，因此不会生成任何投入金额。需要金额时应新建批次并确认真实可用资金。'}</p>
          </div>
          {canCreate && (
            <button type="button" onClick={onCreate} disabled={allocating}>
              {allocating ? <RefreshCw size={15} className="spin-icon" aria-hidden="true" /> : <Scale size={15} aria-hidden="true" />}
              {allocating ? '正在固化' : '生成分配快照'}
            </button>
          )}
        </div>
      </section>
    )
  }

  const budget = allocation.budget || {}
  const allocationDetail = allocation.allocation || {}
  const items = allocationDetail.items || []
  const ready = allocation.status === 'ready'
  const integrityFailed = allocation.status === 'integrity_failed'

  return (
    <section className="agent-batch-allocation" aria-label="组合资金分配">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Portfolio Capital Allocation</span>
          <h3><WalletCards size={18} aria-hidden="true" />组合资金分配复核</h3>
          <small>
            {allocation.snapshot?.created_at ? `快照 ${timeText(allocation.snapshot.created_at)}` : '不可变分配快照'}
            {allocation.snapshot?.integrity_verified ? ' · 完整性已验证' : ''}
          </small>
        </div>
        <span className={`agent-status ${ready ? 'complete' : 'failed'}`}>
          {ready ? '可人工复核' : integrityFailed ? '完整性失败' : '门禁阻断'}
        </span>
      </div>

      <div className="agent-allocation-metrics">
        <div><span>批次唯一总预算</span><b>{money(budget.requested_total_yuan)}</b></div>
        <div><span>已通过约束分配</span><b>{money(budget.allocated_total_yuan)}</b></div>
        <div><span>保持未投入</span><b>{money(budget.unallocated_total_yuan)}</b></div>
        <div><span>联合约束缩放</span><b>{allocationDetail.constraint_scale == null ? '-' : pct(Number(allocationDetail.constraint_scale) * 100)}</b></div>
      </div>

      {(allocation.gates || []).length > 0 && (
        <div className="agent-allocation-gates" aria-label="资金分配门禁">
          {allocation.gates.map((gate) => (
            <span key={gate.code} className={gate.status} title={gate.detail}>
              {gate.status === 'pass' ? <CheckCircle2 size={13} aria-hidden="true" /> : <CircleAlert size={13} aria-hidden="true" />}
              {gate.label}
            </span>
          ))}
        </div>
      )}

      {(allocation.blockers || []).length > 0 && (
        <div className="agent-allocation-blockers">
          {(allocation.blockers || []).map((blocker) => <p key={blocker}><CircleAlert size={14} aria-hidden="true" />{blocker}</p>)}
        </div>
      )}

      {items.length > 0 && (
        <div className="agent-allocation-table" role="table" aria-label="逐只基金组合分配">
          <div className="agent-allocation-row heading" role="row">
            <span>基金</span><span>真实风险 / 重合</span><span>可验证容量</span><span>组合分配</span><span>首批复核</span>
          </div>
          {items.map((item) => (
            <div className={`agent-allocation-row ${item.eligible ? '' : 'ineligible'}`} role="row" key={item.run_id || item.code}>
              <span><b>{item.code} {item.name}</b><small>{item.eligible ? '通过单基金门禁' : '未进入资金分配'}</small></span>
              <span><b>{pct(item.annual_volatility_pct)}</b><small>披露重合累计 {pct(item.known_overlap_sum_pct)}</small></span>
              <span><b>{money(item.capacity_yuan)}</b><small>单品与组合上限中的较小值</small></span>
              <span><strong>{money(item.allocated_amount_yuan)}</strong><small>{item.normalized_risk_weight_pct == null ? '-' : `占已分配 ${pct(item.normalized_risk_weight_pct)}`}</small></span>
              <span><b>{money(item.first_tranche_amount_yuan)}</b><small>{item.tranche_count ? `共 ${item.tranche_count} 批` : '不形成投入批次'}</small></span>
              {(item.reasons || []).length > 0 && <p>{item.reasons.join('；')}</p>}
            </div>
          ))}
        </div>
      )}

      {(allocation.warnings || []).length > 0 && (
        <div className="agent-allocation-warnings">
          {allocation.warnings.map((warning) => <p key={warning}><CircleAlert size={13} aria-hidden="true" />{warning}</p>)}
        </div>
      )}
      <p className="agent-batch-policy">{allocation.policy}</p>
    </section>
  )
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
  allocating = false,
  selectedRunId = '',
  onRefresh,
  onCancel,
  onSelectRun,
  onCreateAllocation,
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
                {item.portfolio_allocation?.allocated_amount_yuan != null && (
                  <small className="allocated">组合分配 {money(item.portfolio_allocation.allocated_amount_yuan)}</small>
                )}
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

      <PortfolioAllocation
        batch={batch}
        active={active}
        allocating={allocating}
        onCreate={onCreateAllocation}
      />

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
