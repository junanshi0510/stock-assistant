import { ChevronDown, KeyRound, LogOut, Shield, UserRound } from 'lucide-react'
import { useState } from 'react'

export default function AccountMenu({ user, onAdmin, onChangePassword, onLogout }) {
  const [open, setOpen] = useState(false)
  const initial = (user.display_name || user.username || 'U').slice(0, 1).toUpperCase()
  return (
    <div className="account-menu">
      <button
        type="button"
        className="account-trigger"
        aria-label={`账户菜单：${user.display_name || user.username}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="account-avatar" aria-hidden="true">{initial}</span>
        <span className="account-copy">
          <strong>{user.display_name || user.username}</strong>
          <small>{user.role === 'admin' ? '管理员' : '用户'}</small>
        </span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open && (
        <div className="account-popover" role="menu">
          <div className="account-summary">
            <UserRound size={16} aria-hidden="true" />
            <div><strong>{user.username}</strong><small>{user.role === 'admin' ? '系统管理员' : '标准用户'}</small></div>
          </div>
          {user.role === 'admin' && (
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onAdmin() }}>
              <Shield size={16} /><span>管理控制台</span>
            </button>
          )}
          <button type="button" role="menuitem" onClick={() => { setOpen(false); onChangePassword() }}>
            <KeyRound size={16} /><span>修改密码</span>
          </button>
          <button type="button" role="menuitem" className="danger" onClick={() => { setOpen(false); onLogout() }}>
            <LogOut size={16} /><span>退出登录</span>
          </button>
        </div>
      )}
    </div>
  )
}
