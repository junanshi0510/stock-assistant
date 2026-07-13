import { KeyRound, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { changeAccountPassword } from '../api/auth'

export default function ChangePasswordScreen({ forced = false, onCancel, onChanged }) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致')
      return
    }
    setLoading(true)
    setError('')
    try {
      await changeAccountPassword(currentPassword, newPassword)
      onChanged()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel" aria-labelledby="password-title">
        <div className="auth-brand compact">
          <span className="auth-mark secure" aria-hidden="true"><ShieldCheck size={21} /></span>
          <div>
            <strong>{forced ? '完成账户激活' : '修改登录密码'}</strong>
            <span>Security Verification</span>
          </div>
        </div>
        <div className="auth-heading">
          <span className="auth-heading-icon" aria-hidden="true"><KeyRound size={17} /></span>
          <h1 id="password-title">设置新密码</h1>
          <p>{forced ? '临时密码只能使用一次，修改后需要重新登录。' : '修改后所有设备上的会话都会退出。'}</p>
        </div>
        <form className="auth-form" onSubmit={submit}>
          <label>
            <span>当前密码</span>
            <input type="password" autoComplete="current-password" value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)} maxLength={128} required />
          </label>
          <label>
            <span>新密码</span>
            <input type="password" autoComplete="new-password" value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)} minLength={12} maxLength={128} required />
          </label>
          <label>
            <span>确认新密码</span>
            <input type="password" autoComplete="new-password" value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)} minLength={12} maxLength={128} required />
          </label>
          <small className="password-policy">至少 12 个字符，不能包含用户名或使用常见密码。</small>
          {error && <div className="auth-message error-message" role="alert">{error}</div>}
          <div className="auth-actions">
            {!forced && <button type="button" className="ghost" onClick={onCancel} disabled={loading}>取消</button>}
            <button type="submit" disabled={loading}>
              {loading ? <span className="spinner" /> : <ShieldCheck size={17} />}
              <span>{loading ? '正在更新' : '更新密码'}</span>
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}
