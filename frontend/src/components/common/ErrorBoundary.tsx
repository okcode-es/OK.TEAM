import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.warn('[ErrorBoundary]', error, info.componentStack)
  }

  private handleReload = (): void => {
    window.location.reload()
  }

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children
    }

    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
        <div
          role="alert"
          className="w-full max-w-md rounded-md border border-slate-200 bg-white p-6 text-center shadow-xs"
        >
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full border border-amber-200 bg-amber-50 text-amber-700">
            <AlertTriangle className="h-5 w-5" aria-hidden="true" />
          </div>
          <h1 className="mt-4 text-base font-semibold text-slate-900">
            页面加载遇到一点问题 / Algo salió mal al cargar la página
          </h1>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">
            系统已自动保护当前会话，请点击下方按钮重新加载页面。
            <br />
            Hemos protegido tu sesión automáticamente. Pulsa el botón para
            recargar la página.
          </p>
          <div className="mt-5">
            <Button
              type="button"
              onClick={this.handleReload}
              className="bg-slate-900 text-white hover:bg-slate-800"
            >
              <RotateCw className="h-4 w-4" aria-hidden="true" />
              重新加载 / Recargar
            </Button>
          </div>
        </div>
      </div>
    )
  }
}
