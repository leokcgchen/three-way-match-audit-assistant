import type { ReviewEvent } from '../types'
import { DocPreview } from './DocPreview'

type Props = {
  event: ReviewEvent
  jobId?: string
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function sourceInfo(event: ReviewEvent) {
  const fileName = String(event.evidence.file_name || event.evidence.source_doc || '')
  const rawPage = event.evidence.page_no ?? event.evidence.page
  const page = typeof rawPage === 'number' ? rawPage : Number(rawPage || 0)
  const field = String(event.evidence.field_name || '')
  return { fileName, page: Number.isFinite(page) ? page : 0, field }
}

export function EventDecisionCard({ event, jobId }: Props) {
  const source = sourceInfo(event)
  const hasValues =
    event.ledger_value !== null ||
    event.observed_value !== null ||
    event.ai_suggestion !== null ||
    event.confidence !== null
  return (
    <article className="event-decision-card">
      <header>
        <div>
          <span className={`event-severity is-${event.severity.toLowerCase()}`}>
            {event.severity === 'BLOCKING' ? '阻断' : event.severity === 'SAMPLE' ? '抽检' : '待判断'}
          </span>
          <h2>{event.title}</h2>
          {event.chain_id ? <span className="event-chain-id">业务 {event.chain_id}</span> : null}
        </div>
        <span className="event-position-source">
          {source.fileName || '系统规则'}{source.page > 0 ? ` · 第 ${source.page} 页` : ''}
        </span>
      </header>

      <section className="event-trigger-reason" aria-label="触发原因">
        <strong>为什么需要你判断</strong>
        <p>{event.reason}</p>
      </section>

      {hasValues ? (
        <dl className="event-value-grid">
          {event.ledger_value !== null ? <div><dt>账载值</dt><dd>{display(event.ledger_value)}</dd></div> : null}
          {event.observed_value !== null ? <div><dt>单据原始值</dt><dd>{display(event.observed_value)}</dd></div> : null}
          {event.ai_suggestion !== null ? <div><dt>AI 建议</dt><dd>{display(event.ai_suggestion)}</dd></div> : null}
          {event.confidence !== null ? (
            <div><dt>置信度</dt><dd>置信度 {Math.round(event.confidence * 100)}%</dd></div>
          ) : null}
        </dl>
      ) : null}

      {jobId && source.fileName ? (
        <div className="event-source-preview">
          <DocPreview
            jobId={jobId}
            fileName={source.fileName}
            page={source.page || 1}
            highlightField={source.field || null}
            highlightValue={event.observed_value == null ? null : String(event.observed_value)}
          />
        </div>
      ) : null}

      {event.invalidates.length > 0 ? (
        <p className="event-impact"><strong>裁决影响：</strong>{event.invalidates.join('、')}</p>
      ) : null}
    </article>
  )
}
