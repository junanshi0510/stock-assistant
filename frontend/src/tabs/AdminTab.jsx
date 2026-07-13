import { CheckCircle2, KeyRound, RefreshCw, Shield, UserPlus, UsersRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [resetTarget, setResetTarget] = useState(null)
  const [createForm, setCreateForm] = useState({ username: '', display_name: '', role: 'user', temporary_password: '' })

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [overviewData, userData, auditData] = await Promise.all([
        fetchAdminOverview(), fetchAdminUsers(), fetchAdminAuthAudit(60),
      ])
      setOverview(overviewData); setUsers(userData.items || []); setAudit(auditData)
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

  const counts = overview?.users || {}
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
