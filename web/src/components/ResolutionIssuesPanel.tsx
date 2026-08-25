import { useState } from 'react'
import type { ResolutionIssue } from '../types'

type Decision = { edgeId: string; decision: 'CONFIRMED' | 'REJECTED'; reason: string }
type Props = { issues: ResolutionIssue[]; onDecision: (decision: Decision) => Promise<void> | void }

export function ResolutionIssuesPanel({ issues, onDecision }: Props) {
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  return <section className="resolution-section issues-panel" aria-labelledby="issues-title">
    <header className="resolution-section-head"><div><h3 id="issues-title">待解释事项</h3><p>这些事项不会被系统静默消除，需要证据或审计师判断。</p></div><span className="resolution-count is-warning">待处理 {issues.filter((issue) => issue.resolution_status === 'PENDING').length} 项</span></header>
    {issues.length === 0 ? <p className="hint">当前没有待解释事项。</p> : <div className="resolution-issue-list">{issues.map((issue) => {
      const reason = reasons[issue.edge_id] || ''
      const resolved = issue.resolution_status && issue.resolution_status !== 'PENDING'
      return <article key={issue.edge_id} className={`resolution-issue ${resolved ? 'is-resolved' : ''}`}>
        <div><span className="resolution-badge is-warning">{issue.severity === 'WARNING' ? '需解释' : issue.severity}</span><h4>{issue.title}</h4><p>{issue.message}</p>
          {!!issue.values?.length && <p className="mono">{issue.values.join(' ↔ ')}</p>}
        </div>
        {resolved ? <span className="resolution-badge is-ok">已处理</span> : <div className="issue-actions">
          <label htmlFor={`issue-reason-${issue.edge_id}`}>{issue.issue_code === 'CUSTOMER_CODE_MAPPING_REQUIRED' ? '客户编码映射说明' : '处理说明'}</label>
          <textarea id={`issue-reason-${issue.edge_id}`} value={reason} onChange={(event) => setReasons((current) => ({ ...current, [issue.edge_id]: event.target.value }))} rows={2} />
          <button type="button" className="btn primary compact" disabled={reason.trim().length < 2 || busy === issue.edge_id} onClick={async () => {
            setBusy(issue.edge_id)
            try { await onDecision({ edgeId: issue.edge_id, decision: 'CONFIRMED', reason: reason.trim() }) } finally { setBusy('') }
          }}>确认已解释</button>
        </div>}
      </article>
    })}</div>}
  </section>
}
