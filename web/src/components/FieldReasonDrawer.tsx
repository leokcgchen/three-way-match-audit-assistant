import { useEffect, useRef } from 'react'
import type { ExplainableComparisonRow, FieldEvidenceNode } from '../types'

type Props = {
  open: boolean
  row: ExplainableComparisonRow | null
  evidenceNodes: FieldEvidenceNode[]
  onClose: () => void
  onSelectEvidence: (evidenceId: string) => void
}

export function FieldReasonDrawer({ open, row, evidenceNodes, onClose, onSelectEvidence }: Props) {
  const returnFocus = useRef<HTMLElement | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useEffect(() => {
    if (!open && returnFocus.current) {
      const target = returnFocus.current
      queueMicrotask(() => target.focus())
    }
  }, [open])

  if (!open || !row) return null
  const evidenceById = new Map(evidenceNodes.map((node) => [node.evidence_id, node]))
  const sources = row.evidence_ids.map((id) => evidenceById.get(id)).filter(Boolean) as FieldEvidenceNode[]

  return <div className="reason-drawer-backdrop" onMouseDown={(event) => {
    if (event.currentTarget === event.target) onClose()
  }}>
    <aside className="reason-drawer" role="dialog" aria-modal="true" aria-labelledby="reason-drawer-title">
      <header className="reason-drawer-head">
        <div><span className="eyebrow">判断依据</span><h3 id="reason-drawer-title">{row.label}：判断依据</h3></div>
        <button ref={closeRef} type="button" className="btn compact" onClick={onClose} aria-label="关闭判断依据">关闭</button>
      </header>
      <div className="reason-drawer-body">
        <section><h4>结论如何形成</h4><p>{row.reason_text || row.reason_code || '等待审计师补充判断依据。'}</p></section>
        {!!row.transformations?.length && <section><h4>采用的转换</h4><ul>{row.transformations.map((item) => <li key={item}>{item}</li>)}</ul></section>}
        {row.calculation && <section><h4>复算过程</h4><p className="mono">{row.calculation}</p></section>}
        <section><h4>原始证据</h4>{sources.length ? <div className="reason-source-list">{sources.map((node) => {
          const fileName = node.metadata?.file_name || node.document_id
          return <button key={node.evidence_id} type="button" className="reason-source" onClick={() => onSelectEvidence(node.evidence_id)}>
            <span>{fileName}</span><small>第 {node.page || 1} 页</small><q>{node.excerpt}</q>
          </button>
        })}</div> : <p className="hint">本项没有可定位证据，不能用于自动结论。</p>}</section>
        {!!row.counter_evidence?.length && <section className="reason-counter"><h4>反证或限制</h4><ul>{row.counter_evidence.map((item, index) => <li key={index}>{String(item.message || item.reason_code || '存在待解释反证')}</li>)}</ul></section>}
      </div>
    </aside>
  </div>
}
