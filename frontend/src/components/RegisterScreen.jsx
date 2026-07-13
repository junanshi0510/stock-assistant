import { Eye, EyeOff, ShieldCheck, TrendingUp, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { registerAccount } from '../api/auth'

export default function RegisterScreen({ readiness, onRegistered, onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    setError('')
    if (password !== confirmation) {
      setError('两次输入的密码不一致')
      return
    }
    setLoading(true)
    try {
      const result = await registerAccount(username.trim(), password)
      onRegistered(result.user.username)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  const unavailable = Boolean(
    readiness && (!readiness.ready || !readiness.self_registration_enabled),
  )

  return (
    <main className="auth-shell">
      <section className="auth-panel" aria-labelledby="register-title">
        <div className="auth-brand">
          <span className="auth-mark" aria-hidden="true"><TrendingUp size={21} strokeWidth={2.5} /></span>
          <div>
            <strong>金融投资助手</strong>
            <span>Investment Decision Workspace</span>
          </div>
        </div>

        <div className="auth-mode-switch" role="tablist" aria-label="账户入口">
          <button className="auth-mode-option" type="button" role="tab" aria-selected="false" onClick={onLogin}>
            登录
          </button>
          <button className="auth-mode-option active" type="button" role="tab" aria-selected="true">
            注册
          </button>
        </div>

        <div className="auth-heading">
          <span className="auth-heading-icon" aria-hidden="true"><ShieldCheck size={17} /></span>
          <h1 id="register-title">创建账户</h1>
          <p>注册后使用独立账户保存你的持仓与研究记录。</p>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            <span>账号</span>
            <input
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={loading || unavailable}
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9_.-]{3,32}"
              title="使用 3-32 位字母、数字、点、下划线或连字符"
              required
            />
          </label>
          <label>
            <span>密码</span>
            <div className="password-field">
              <input
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={loading || unavailable}
                minLength={12}
                maxLength={128}
                required
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => setShowPassword((value) => !value)}
                title={showPassword ? '隐藏密码' : '显示密码'}
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
              >
                {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </label>
          <label>
            <span>确认密码</span>
            <input
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              disabled={loading || unavailable}
              minLength={12}
              maxLength={128}
              required
            />
          </label>
          <div className="password-policy">至少 12 个字符，不能包含账号；建议使用不重复的长密码。</div>

          {unavailable && (
            <div className="auth-message warning-message" role="alert">当前暂未开放注册，请联系系统管理员。</div>
          )}
          {error && <div className="auth-message error-message" role="alert">{error}</div>}

          <button className="auth-submit" type="submit" disabled={loading || unavailable}>
            {loading ? <span className="spinner" /> : <UserPlus size={17} />}
            <span>{loading ? '正在创建' : '注册账户'}</span>
          </button>
        </form>
      </section>
    </main>
  )
}
