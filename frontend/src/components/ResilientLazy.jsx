import { Component, lazy, Suspense } from 'react'
import { importWithRetry } from '../utils/importWithRetry'

export function resilientLazy(loader) {
  return lazy(() => importWithRetry(loader))
}

class LazyLoadErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(previousProps) {
    if (
      this.state.error
      && previousProps.resetKey !== this.props.resetKey
    ) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) {
      return this.props.children
    }

    return (
      <section className="chunk-load-error" role="alert">
        <strong>页面资源暂时加载失败</strong>
        <p>网络恢复后重新加载即可继续；所有已经保存的研究、持仓和审计记录都不会丢失。</p>
        <button type="button" className="ghost" onClick={() => globalThis.location.reload()}>
          重新加载页面
        </button>
      </section>
    )
  }
}

export default function ResilientSuspense({
  children,
  fallbackText = '正在加载页面',
  resetKey,
}) {
  return (
    <LazyLoadErrorBoundary resetKey={resetKey}>
      <Suspense fallback={<div className="page-loading"><span className="spinner" />{fallbackText}</div>}>
        {children}
      </Suspense>
    </LazyLoadErrorBoundary>
  )
}
