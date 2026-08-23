import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Job } from '../types'

const TYPE_CN: Record<string, string> = {
  contract: '合同',
  order: '订单',
  delivery: '发货',
  receipt: '签收',
  invoice: '发票',
  payment: '回款',
  other: '其他',
}

type Props = {
  job: Job
  refreshKey?: string
}

export function PendingChainPreview({ job, refreshKey }: Props) {
  const [chains, setChains] = useState<
    Array<{
      chain_id: string
      doc_count: number
      file_names: string[]
      doc_types: string[]
      pending_only?: boolean
    }>
  >([])
  const [err, setErr] = useState('')

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const r = await api.previewChains(job.job_id)
        if (!cancelled) {
          setChains(r.chains || [])
          setErr('')
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [job.job_id, refreshKey, (job.pending_files || []).length, (job.classified || []).length])

  const total = (job.pending_files || []).length + (job.classified || []).length
  if (total < 2) return null

  return (
    <div className="chain-preview card-compact">
      <div className="chain-preview-head">
        <strong>业务笔预览（按 SO/HT 文件名归组）</strong>
        <span className="hint">OCR 完成后会按字段再精修分链</span>
      </div>
      {err && <p className="err">{err}</p>}
      <div className="chain-preview-grid">
        {chains.map((c) => (
          <div className="chain-preview-item" key={c.chain_id}>
            <div className="chain-preview-id">{c.chain_id}</div>
            <div className="hint">
              {c.doc_count} 单 ·{' '}
              {[...new Set(c.doc_types || [])]
                .map((t) => TYPE_CN[t] || t)
                .join(' / ')}
              {c.pending_only ? ' · 待 OCR' : ''}
            </div>
            <ul className="chain-preview-files">
              {(c.file_names || []).slice(0, 4).map((f) => (
                <li key={f}>{f}</li>
              ))}
              {(c.file_names || []).length > 4 ? (
                <li className="hint">…共 {c.file_names.length} 个文件</li>
              ) : null}
            </ul>
          </div>
        ))}
        {!chains.length && !err && (
          <p className="hint">未能从文件名识别 SO/HT，请核对文件命名或手动改类型</p>
        )}
      </div>
    </div>
  )
}
