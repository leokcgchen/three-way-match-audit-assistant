import type { BusinessChronology } from '../types'

type Props = { chronology: BusinessChronology; onSelectEvidence: (evidenceId: string) => void }

function shortLabel(label: string): string {
  if (label.includes('验收') || label.includes('签收')) return '验收'
  if (label.includes('开票')) return '开票'
  if (label.includes('订单')) return '订单'
  if (label.includes('合同')) return '合同'
  if (label.includes('入账')) return '入账'
  return label
}

function displayTime(value: string): string {
  const match = value.match(/T(\d{2}:\d{2})/)
  return match?.[1] || value
}

export function BusinessChronologyPanel({ chronology, onSelectEvidence }: Props) {
  const summary = chronology.events.map((event) => `${shortLabel(event.label)} ${displayTime(event.value)}`).join(' → ')
  return <section className="resolution-section chronology-panel" aria-labelledby="chronology-title">
    <header className="resolution-section-head"><div><h3 id="chronology-title">时序与业务过程</h3><p>日期用于判断业务先后与期间归属，不要求不同单据日期相等。</p></div>
      <span className={`resolution-badge ${chronology.status === 'PASS' ? 'is-ok' : chronology.status === 'CONFLICT' ? 'is-error' : 'is-warning'}`}>{chronology.status === 'PASS' ? '时序合理' : chronology.status === 'CONFLICT' ? '时序异常' : '待判断'}</span>
    </header>
    {summary && <p className="chronology-summary">{summary}</p>}
    <ol className="chronology-list">{chronology.events.map((event, index) => <li key={`${event.label}-${event.value}-${index}`}>
      {event.evidence_id ? <button type="button" className="evidence-link" onClick={() => onSelectEvidence(event.evidence_id!)}>
        <strong>{event.label}</strong><span>{event.value.replace('T', ' ')}</span>
      </button> : <><strong>{event.label}</strong><span>{event.value}</span></>}
    </li>)}</ol>
    {chronology.reason_text && <p className="resolution-reason">{chronology.reason_text}</p>}
  </section>
}
