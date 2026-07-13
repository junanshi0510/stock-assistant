import { Eye, EyeOff, LockKeyhole, LogIn, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import { loginAccount } from '../api/auth'

export default function LoginScreen({ readiness, onAuthenticated, onRegister, initialUsername = '', notice = '' }) {
  const [username, setUsername] = useState(initialUsername)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const result = await loginAccount(username.trim(), password)
      onAuthenticated(result.user)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  const unavailable = readiness && !readiness.ready
  return (
    <main className="auth-shell">
      <section className="auth-panel" aria-labelledby="login-title">
        <div className="auth-brand">
          <span className="auth-mark" aria-hidden="true"><TrendingUp size={21} strokeWidth={2.5} /></span>
          <div>
            <strong>金融投资助手</strong>
            <span>Investment Decision Workspace</span>
          </div>
        </div>

        {onRegister && (
          <div className="auth-mode-switch" role="tablist" aria-label="账户入口">
            <button className="auth-mode-option active" type="button" role="tab" aria-selected="true">
              登录
            </button>
            <button className="auth-mode-option" type="button" role="tab" aria-selected="false" onClick={onRegister}>
              注册
            </button>
          </div>
        )}

        <div className="auth-heading">
          <span className="auth-heading-icon" aria-hidden="true"><LockKeyhole size={17} /></span>
          <h1 id="login-title">登录账户</h1>
          <p>访问你的持仓、研究记录与投资 Agent。</p>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            <span>用户名</span>
            <input
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={loading || unavailable}
              maxLength={32}
              required
            />
          </label>
          <label>
            <span>密码</span>
            <div className="password-field">
              <input
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={loading || unavailable}
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

          {unavailable && (
            <div className="auth-message warning-message" role="alert">
              {readiness.configured ? '系统尚未初始化管理员，请先在服务器执行初始化命令。' : '认证安全配置尚未完成，请联系服务器管理员。'}
            </div>
          )}
          {notice && <div className="auth-message success-message" role="status">{notice}</div>}
          {error && <div className="auth-message error-message" role="alert">{error}</div>}

          <button className="auth-submit" type="submit" disabled={loading || unavailable}>
            {loading ? <span className="spinner" /> : <LogIn size={17} />}
            <span>{loading ? '正在验证' : '登录'}</span>
          </button>
        </form>
      </section>
    </main>
  )
}
