import { Activity, AlertTriangle, CheckCircle2, CloudCog, Gauge, KeyRound, PlayCircle, RefreshCw, ServerCog, Shield, UserPlus, UsersRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { fetchAdminAvailability, runAdminAvailabilityProbe } from '../api/availability'
import {
  createAdminUser,
  fetchAdminAuthAudit,
  fetchAdminOverview,
  fetchAdminUsers,
  resetAdminUserPassword,
  updateAdminUser,
} from '../api/auth'
import WorkspaceHeader from '../components/WorkspaceHeader'

const EVENT_LABELS = {
  admin_bootstrapped: '初始化管理员',
  user_created: '创建用户',
  user_updated: '更新用户',
  password_reset_by_admin: '重置密码',
  admin_recovered_offline: '离线恢复管理员',
  password_changed: '用户修改密码',
  login_succeeded: '登录成功',
  login_failed: '登录失败',
  logout: '退出登录',
}

const AVAILABILITY_LABELS = {
  operational: '正常',
  degraded: '降级',
  outage: '中断',
  unknown: '待确认',
}

const CAPABILITY_LABELS = {
  api_traffic: 'API 流量与副本',
  saved_data_read: '已保存事实读取',
  market_refresh: '市场数据刷新',
  portfolio_valuation_refresh: '组合估值刷新',
  agent_research: '投资 Agent',
  private_ocr_import: '私有 OCR 导入',
  durable_scheduling: '持久调度',
}

const CAPABILITY_MODE_LABELS = {
  normal: '正常',
  partial: '部分可用',
  deterministic_only: '确定性模式',
  redundant: '双副本',
  reduced_redundancy: '冗余降低',
  single_instance_unmonitored: '本地单实例',
  unavailable: '不可用',
}

function timeText(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function PasswordDialog({ title, confirmText, onClose, onConfirm }) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  async function submit(event) {
    event.preventDefault()
    if (password !== confirm) return setError('两次输入的临时密码不一致')
    setLoading(true); setError('')
    try { await onConfirm(password); onClose() } catch (requestError) { setError(requestError.message) } finally { setLoading(false) }
  }
  return (
    <div className="modal-backdrop" role="presentation">
      <form className="admin-dialog" role="dialog" aria-modal="true" aria-labelledby="password-dialog-title" onSubmit={submit}>
        <div className="admin-dialog-title"><KeyRound size={18} /><h3 id="password-dialog-title">{title}</h3></div>
        <label><span>临时密码</span><input type="password" minLength={12} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} autoFocus required /></label>
        <label><span>再次输入</span><input type="password" minLength={12} maxLength={128} value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></label>
        <small>用户首次登录后必须立即修改，保存时不会展示密码明文。</small>
        {error && <div className="error">{error}</div>}
        <div className="admin-dialog-actions"><button className="ghost" type="button" onClick={onClose}>取消</button><button type="submit" disabled={loading}>{confirmText}</button></div>
      </form>
    </div>
  )
}

export default function AdminTab({ currentUser }) {
  const [overview, setOverview] = useState(null)
  const [users, setUsers] = useState([])
  const [audit, setAudit] = useState(null)
  const [availability, setAvailability] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [resetTarget, setResetTarget] = useState(null)
  const [probeMode, setProbeMode] = useState('')
  const [createForm, setCreateForm] = useState({ username: '', display_name: '', role: 'user', temporary_password: '' })

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [overviewData, userData, auditData, availabilityData] = await Promise.all([
        fetchAdminOverview(), fetchAdminUsers(), fetchAdminAuthAudit(60), fetchAdminAvailability(),
      ])
      setOverview(overviewData); setUsers(userData.items || []); setAudit(auditData); setAvailability(availabilityData)
    } catch (requestError) { setError(requestError.message) } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function createUser(event) {
    event.preventDefault(); setError(''); setMessage('')
    try {
      await createAdminUser(createForm)
      setCreateForm({ username: '', display_name: '', role: 'user', temporary_password: '' })
      setMessage('用户已创建，临时密码只应通过可信渠道交付。')
      await load()
    } catch (requestError) { setError(requestError.message) }
  }

  async function updateUser(userId, changes) {
    setError(''); setMessage('')
    try { await updateAdminUser(userId, changes); setMessage('用户权限已更新，会话已按安全规则处理。'); await load() }
    catch (requestError) { setError(requestError.message) }
  }

  async function runProbe(mode) {
    setProbeMode(mode); setError(''); setMessage('')
    try {
      const result = await runAdminAvailabilityProbe(mode)
      if (result.summary) {
        globalThis.dispatchEvent(new CustomEvent('stock-assistant:availability-updated', { detail: result.summary }))
      }
      setMessage(mode === 'deep' ? '三市场专业源主动探测已记录。' : '平台可用性探测已记录。')
      await load()
    } catch (requestError) { setError(requestError.message) } finally { setProbeMode('') }
  }

  const counts = overview?.users || {}
  const availabilityState = availability?.monitoring_stale ? 'unknown' : (availability?.status || 'unknown')
  const components = availability?.latest?.payload?.components || []
  const apiReplicas = components.filter((item) => item.category === 'api_replica')
  const apiReplicaSummary = availability?.latest?.payload?.metadata?.api_replicas || {}
  const apiReplicaReadyCount = apiReplicaSummary.ready_count
    ?? apiReplicas.filter((item) => Boolean(item.details?.ready)).length
  const apiReplicaConfiguredCount = apiReplicaSummary.configured_count ?? apiReplicas.length
  const capabilities = availability?.capabilities || {}
  const incidents = availability?.incidents || []
  const openIncidents = incidents.filter((item) => item.status === 'open')
  const coreWindow = availability?.slos?.groups?.core_access?.windows?.['24h']
  const sloRows = Object.entries(availability?.slos?.groups || {})
  return (
    <div className="admin-workspace">
      <WorkspaceHeader eyebrow="Access Control" title="管理控制台" description="账户、角色、会话与认证审计。所有权限变更都在服务端执行并写入哈希审计链。" />
      <div className="admin-toolbar"><span className={`integrity-state ${audit?.verification?.verified ? 'ok' : 'bad'}`}><CheckCircle2 size={15} />认证审计链 {audit?.verification?.verified ? '完整' : '异常'}</span><button className="ghost icon-text" onClick={load} disabled={loading}><RefreshCw size={15} />刷新</button></div>
      {error && <div className="admin-notice error-message">{error}</div>}
      {message && <div className="admin-notice success-message">{message}</div>}

      <section className="admin-metrics" aria-label="系统权限概览">
        <div><span>启用管理员</span><strong>{counts.active_admins ?? '-'}</strong></div>
        <div><span>启用用户</span><strong>{counts.active_users ?? '-'}</strong></div>
        <div><span>活动会话</span><strong>{overview?.active_sessions ?? '-'}</strong></div>
        <div><span>Agent Run</span><strong>{Object.values(overview?.agent_runs || {}).reduce((sum, value) => sum + value, 0)}</strong></div>
      </section>

      <section className="admin-section availability-control">
        <div className="admin-section-heading">
          <div><span className="eyebrow">Availability Control Plane</span><h3><CloudCog size={18} />高可用控制中心</h3></div>
          <div className="availability-actions">
            <button type="button" className="ghost icon-text" onClick={() => runProbe('standard')} disabled={Boolean(probeMode)}><PlayCircle size={15} />{probeMode === 'standard' ? '探测中' : '立即探测'}</button>
            <button type="button" className="ghost icon-text" onClick={() => runProbe('deep')} disabled={Boolean(probeMode)}><Activity size={15} />{probeMode === 'deep' ? '专业源探测中' : '三市场深度探测'}</button>
          </div>
        </div>

        <div className="availability-metrics" aria-label="平台可用性概览">
          <div><span>当前状态</span><strong className={`availability-state state-${availabilityState}`}>{AVAILABILITY_LABELS[availabilityState]}</strong><small>{availability?.monitoring_stale ? '监测快照已过期' : timeText(availability?.observed_at)}</small></div>
          <div><span>开放事故</span><strong>{openIncidents.length}</strong><small>连续两次失败才开启</small></div>
          <div><span>24 小时核心 SLI</span><strong>{coreWindow?.availability_pct == null ? '-' : `${coreWindow.availability_pct}%`}</strong><small>{coreWindow?.enough_samples ? `目标 ${coreWindow.target_pct}%` : '样本积累中'}</small></div>
          <div><span>审计完整性</span><strong>{availability?.verification?.latest_probe?.verified && availability?.verification?.incident_events?.verified ? '完整' : '异常'}</strong><small>{availability?.history_count || 0} 份近期快照</small></div>
        </div>

        <div className="availability-mode-banner">
          <Gauge size={17} />
          <div><strong>{capabilities?.decision_mode?.mode === 'normal' ? '正常决策模式' : capabilities?.decision_mode?.mode === 'read_only_degraded' ? '只读降级模式' : '事实服务不可用'}</strong><span>{capabilities?.decision_mode?.message || availability?.notice}</span></div>
        </div>

        <div className="availability-capabilities">
          {Object.entries(CAPABILITY_LABELS).map(([key, label]) => {
            const item = capabilities[key] || {}
            return <article key={key} className={`availability-capability ${item.available ? 'available' : 'unavailable'}`}>
              <span>{item.available ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}{label}</span>
              <strong>{CAPABILITY_MODE_LABELS[item.mode] || (item.available ? '可用' : '不可用')}</strong>
              {item.markets && <small>{Object.entries(item.markets).map(([market, state]) => `${market} ${AVAILABILITY_LABELS[state] || state}`).join(' · ')}</small>}
              {key === 'api_traffic' && <small>{item.expected_replicas ? `${item.ready_replicas || 0}/${item.expected_replicas} 副本 · ${item.release_consistent ? '版本一致' : '需要核对版本'}` : '生产环境启用双副本探测'}</small>}
            </article>
          })}
        </div>

        {apiReplicas.length > 0 && <>
          <div className="admin-section-heading availability-subheading"><div><span className="eyebrow">Traffic Plane</span><h3><ServerCog size={17} />API 双副本流量层</h3></div><small>{apiReplicaReadyCount}/{apiReplicaConfiguredCount} 可接流量</small></div>
          <div className="availability-replica-grid">
            {apiReplicas.map((item) => {
              const details = item.details || {}
              return <article key={item.component_id} className={`state-${item.observed_state}`}>
                <div><span className={`availability-pill state-${item.observed_state}`}>{AVAILABILITY_LABELS[item.observed_state]}</span><strong>{item.label}</strong></div>
                <dl><div><dt>发布版本</dt><dd>{details.release_id ? details.release_id.slice(0, 12) : '-'}</dd></div><div><dt>探测延迟</dt><dd>{details.latency_ms == null ? '-' : `${details.latency_ms} ms`}</dd></div><div><dt>副本身份</dt><dd>{details.replica_id || '-'}</dd></div></dl>
                <small>{item.message}</small>
              </article>
            })}
          </div>
        </>}

        <div className="admin-section-heading availability-subheading"><div><span className="eyebrow">Components</span><h3><Activity size={17} />关键组件</h3></div><small>{components.length} 个组件</small></div>
        <div className="admin-table-wrap">
          <table className="admin-table availability-table"><thead><tr><th>组件</th><th>类别</th><th>观测</th><th>确认状态</th><th>连续失败/恢复</th><th>说明</th></tr></thead>
            <tbody>{components.map((item) => <tr key={item.component_id}>
              <td><strong>{item.label}</strong><small>{item.component_id}</small></td>
              <td>{item.category}</td>
              <td><span className={`availability-pill state-${item.observed_state}`}>{AVAILABILITY_LABELS[item.observed_state]}</span></td>
              <td><span className={`availability-pill state-${item.effective_state}`}>{AVAILABILITY_LABELS[item.effective_state]}</span>{item.pending_transition && <small>待二次确认</small>}</td>
              <td>{item.failure_streak || 0} / {item.success_streak || 0}</td>
              <td><span>{item.message}</span>{item.incident_id && <small>{item.incident_id.slice(0, 26)}…</small>}</td>
            </tr>)}</tbody>
          </table>
        </div>

        <div className="availability-two-column">
          <div>
            <div className="admin-section-heading availability-subheading"><div><span className="eyebrow">Internal SLO</span><h3><Gauge size={17} />错误预算</h3></div></div>
            <div className="availability-slo-list">{sloRows.map(([key, item]) => {
              const window = item.windows?.['24h'] || {}
              return <div key={key}><span><strong>{item.label}</strong><small>{window.enough_samples ? `${window.sample_count} 个有效样本` : '样本积累中'}</small></span><b>{window.availability_pct == null ? '-' : `${window.availability_pct}%`}</b><small>目标 {item.target_pct}% · Burn {window.burn_rate ?? '-'}×</small></div>
            })}</div>
          </div>
          <div>
            <div className="admin-section-heading availability-subheading"><div><span className="eyebrow">Incident Timeline</span><h3><AlertTriangle size={17} />事故与恢复</h3></div><small>{incidents.length} 条</small></div>
            <div className="availability-incident-list">{incidents.length ? incidents.slice(0, 12).map((item) => <div key={item.incident_id} className={item.status}><span className={`availability-pill state-${item.current_state}`}>{item.status === 'open' ? '处理中' : '已恢复'}</span><div><strong>{item.component_id}</strong><small>{timeText(item.opened_at)}{item.resolved_at ? ` → ${timeText(item.resolved_at)}` : ''}</small><span>{item.latest_message || '状态转换已记录'}</span></div></div>) : <p className="empty-inline">尚无确认事故。</p>}</div>
          </div>
        </div>
      </section>

      <section className="admin-section">
        <div className="admin-section-heading"><div><span className="eyebrow">User Provisioning</span><h3><UserPlus size={18} />创建账户</h3></div></div>
        <form className="admin-create-form" onSubmit={createUser}>
          <label><span>用户名</span><input value={createForm.username} onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })} minLength={3} maxLength={32} required /></label>
          <label><span>显示名称</span><input value={createForm.display_name} onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })} maxLength={80} required /></label>
          <label><span>角色</span><select value={createForm.role} onChange={(e) => setCreateForm({ ...createForm, role: e.target.value })}><option value="user">标准用户</option><option value="admin">管理员</option></select></label>
          <label><span>临时密码</span><input type="password" value={createForm.temporary_password} onChange={(e) => setCreateForm({ ...createForm, temporary_password: e.target.value })} minLength={12} maxLength={128} required /></label>
          <button type="submit"><UserPlus size={16} />创建</button>
        </form>
      </section>

      <section className="admin-section">
        <div className="admin-section-heading"><div><span className="eyebrow">Role Management</span><h3><UsersRound size={18} />用户与权限</h3></div><small>{users.length} 个账户</small></div>
        <div className="admin-table-wrap">
          <table className="admin-table"><thead><tr><th>账户</th><th>角色</th><th>状态</th><th>数据</th><th>最后登录</th><th>操作</th></tr></thead>
            <tbody>{users.map((user) => {
              const self = user.id === currentUser.id
              return <tr key={user.id}>
                <td><strong>{user.display_name}</strong><small>@{user.username}{user.must_change_password ? ' · 待改密' : ''}</small></td>
                <td><select aria-label={`${user.username}角色`} value={user.role} disabled={self} onChange={(e) => updateUser(user.id, { role: e.target.value })}><option value="user">用户</option><option value="admin">管理员</option></select></td>
                <td><button type="button" className={`status-toggle ${user.status}`} disabled={self} onClick={() => updateUser(user.id, { status: user.status === 'active' ? 'disabled' : 'active' })}>{user.status === 'active' ? '已启用' : '已停用'}</button></td>
                <td><span>{user.data?.holding_count || 0} 持仓</span><small>{user.data?.agent_run_count || 0} Run</small></td>
                <td>{timeText(user.last_login_at)}</td>
                <td><button type="button" className="ghost icon-only" title="重置密码" aria-label={`重置${user.username}密码`} onClick={() => setResetTarget(user)}><KeyRound size={15} /></button></td>
              </tr>
            })}</tbody>
          </table>
        </div>
      </section>

      <section className="admin-section">
        <div className="admin-section-heading"><div><span className="eyebrow">Immutable Audit</span><h3><Shield size={18} />认证审计</h3></div><small>{audit?.verification?.event_count || 0} 条事件</small></div>
        <div className="audit-list">{(audit?.items || []).map((event) => <div className="audit-row" key={event.id}><span className="audit-sequence">#{event.sequence_no}</span><div><strong>{EVENT_LABELS[event.event_type] || event.event_type}</strong><small>{timeText(event.created_at)} · Actor {event.actor_user_id || 'system'} · Target {event.target_user_id || '-'}</small></div><code>{event.event_hash?.slice(0, 12)}</code></div>)}</div>
      </section>

      {resetTarget && <PasswordDialog title={`重置 ${resetTarget.username} 的密码`} confirmText="确认重置" onClose={() => setResetTarget(null)} onConfirm={(password) => resetAdminUserPassword(resetTarget.id, password).then(load)} />}
    </div>
  )
}
