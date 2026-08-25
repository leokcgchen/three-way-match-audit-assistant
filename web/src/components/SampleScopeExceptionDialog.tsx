import { useEffect, useRef } from 'react'

import type { SampleScopeException } from '../types'

type Props = {
  exceptions: SampleScopeException[]
  deletingId?: string | null
  onDelete: (exception: SampleScopeException) => void | Promise<void>
  onDismiss: () => void
}

export function SampleScopeExceptionDialog({ exceptions, deletingId, onDelete, onDismiss }: Props) {
  const recommendedRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLElement>(null)

  useEffect(() => {
    recommendedRef.current?.focus()
  }, [])

  if (!exceptions.length) return null

  return (
    <div className="scope-dialog-backdrop" role="presentation">
      <section
        ref={dialogRef}
        className="scope-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="scope-dialog-title"
        aria-describedby="scope-dialog-description"
        onKeyDown={(event) => {
          if (event.key === 'Escape') onDismiss()
          if (event.key !== 'Tab') return
          const buttons = Array.from(
            dialogRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [],
          )
          if (!buttons.length) return
          const first = buttons[0]
          const last = buttons[buttons.length - 1]
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault()
            last.focus()
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault()
            first.focus()
          }
        }}
      >
        <header className="scope-dialog-head">
          <div className="scope-dialog-mark" aria-hidden="true">!</div>
          <div>
            <p className="scope-dialog-eyebrow">抽样边界校验</p>
            <h2 id="scope-dialog-title">发现非抽样清单材料</h2>
          </div>
        </header>

        <p id="scope-dialog-description" className="scope-dialog-summary">
          系统已阻止这些文件进入审阅列表，也不会新增业务。请审计师核对后处理；若材料确实不属于本次抽样，建议删除。
        </p>

        <div className="scope-dialog-list">
          {exceptions.map((exception, index) => {
            const ids = exception.detected_business_ids || []
            const deleting = deletingId === exception.exception_id
            return (
              <article className="scope-dialog-item" key={exception.exception_id}>
                <div className="scope-dialog-fileline">
                  <strong>{exception.file_name}</strong>
                  <span className="badge danger">已隔离</span>
                </div>
                <p>{exception.reason}</p>
                <dl className="scope-dialog-facts">
                  <div>
                    <dt>识别业务号</dt>
                    <dd>{ids.length ? ids.join('、') : '未识别，无法归属'}</dd>
                  </div>
                  <div>
                    <dt>当前影响</dt>
                    <dd>不会进入抽样业务、字段核对或底稿导出</dd>
                  </div>
                </dl>
                <button
                  ref={index === 0 ? recommendedRef : undefined}
                  type="button"
                  className="btn danger scope-delete-recommended"
                  disabled={Boolean(deletingId)}
                  onClick={() => void onDelete(exception)}
                >
                  {deleting ? '正在删除…' : '删除该文件（推荐）'}
                </button>
              </article>
            )
          })}
        </div>

        <footer className="scope-dialog-actions">
          <span>暂不删除不会放行该文件，稍后仍可从异常区继续处理。</span>
          <button type="button" className="btn" disabled={Boolean(deletingId)} onClick={onDismiss}>
            暂不删除，留在异常区
          </button>
        </footer>
      </section>
    </div>
  )
}
