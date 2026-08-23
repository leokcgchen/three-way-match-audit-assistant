import { useEffect, useMemo, useState } from 'react'

import { api } from '../api'
import { EventDecisionCard } from '../components/EventDecisionCard'
import { sortReviewEvents } from '../lib/reviewEvents'
import type { Job, ReviewDecision, ReviewEvent } from '../types'

type Props = {
  job: Job
  onJob: (job: Job) => void
  onGo: (step: string) => void
}

type Choice = { decision: ReviewDecision; label: string }

function choicesFor(event: ReviewEvent): Choice[] {
  if (event.event_type === 'MISSING_DOCUMENT') return []
  if (event.action_kind === 'REVIEW_EVIDENCE') {
    return [{ decision: 'DOCUMENT_ISSUE', label: '作为单据问题处理' }]
  }
  if (event.action_kind === 'DECIDE_FINDING') {
    return [
      { decision: 'AUDIT_FAIL', label: '确认为审计不通过' },
      { decision: 'DOCUMENT_ISSUE', label: '作为单据问题放行' },
    ]
  }
  if (event.action_kind === 'REVIEW_SAMPLE') {
    return [
      { decision: 'CORRECT', label: '复核无误' },
      { decision: 'FALSE_NEGATIVE', label: '发现漏判' },
    ]
  }
  return [
    { decision: 'ACCEPT_AI', label: '接受 AI 建议' },
    { decision: 'OVERRIDE', label: '覆盖 AI 结论' },
    { decision: 'MANUAL_VALUE', label: '手工录入' },
  ]
}

function requiresReason(decision: ReviewDecision | ''): boolean {
  return ['OVERRIDE', 'AUDIT_FAIL', 'DOCUMENT_ISSUE', 'FALSE_NEGATIVE'].includes(decision)
}

export function EventReviewPage({ job, onJob, onGo }: Props) {
  const storageKey = `audit-event-review:${job.job_id}`
  const [events, setEvents] = useState<ReviewEvent[]>([])
  const [currentId, setCurrentId] = useState(() => sessionStorage.getItem(storageKey) || '')
  const [choice, setChoice] = useState<ReviewDecision | ''>('')
  const [reason, setReason] = useState('')
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = async (): Promise<ReviewEvent[]> => {
    const result = await api.listReviewEvents(job.job_id)
    const open = sortReviewEvents(result.events.filter((event) => event.state === 'OPEN'))
    setEvents(open)
    setCurrentId((current) => {
      if (open.some((event) => event.event_id === current)) return current
      return open[0]?.event_id || ''
    })
    return open
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void refresh()
      .catch((reasonValue) => {
        if (!cancelled) setError(reasonValue instanceof Error ? reasonValue.message : String(reasonValue))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // Refresh when a server-side job mutation changes its authoritative stamp.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, job.updated_at])

  useEffect(() => {
    if (!currentId) return
    sessionStorage.setItem(storageKey, currentId)
    const url = new URL(window.location.href)
    url.hash = `event=${encodeURIComponent(currentId)}`
    window.history.replaceState(null, '', url)
    setChoice('')
    setReason('')
    setValue('')
    setError('')
  }, [currentId, storageKey])

  const currentIndex = Math.max(0, events.findIndex((event) => event.event_id === currentId))
  const event = events[currentIndex]
  const options = useMemo(() => (event ? choicesFor(event) : []), [event])

  const submit = async () => {
    if (!event || !choice || busy) return
    if (requiresReason(choice) && !reason.trim()) {
      setError(choice === 'OVERRIDE' ? '请填写覆盖理由' : '请填写裁决理由')
      return
    }
    if (choice === 'MANUAL_VALUE' && !value.trim()) {
      setError('请输入人工值')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.decideReviewEvent(job.job_id, event.event_id, {
        decision: choice,
        value: choice === 'MANUAL_VALUE' || choice === 'OVERRIDE' ? value : undefined,
        reason,
      })
      onJob(result.job)
      const remaining = await refresh()
      if (remaining.length === 0) onGo('sample_desk')
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : String(reasonValue))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <div className="panel-body">正在整理待裁决事项…</div>
  if (!event) {
    return (
      <div className="panel event-review-empty">
        <h2>没有待裁决事项</h2>
        <p>正常项已自动通过，可以返回工作台继续。</p>
        <button type="button" className="btn primary" onClick={() => onGo('sample_desk')}>返回工作台</button>
      </div>
    )
  }

  if (event.event_type === 'MISSING_DOCUMENT') {
    return (
      <div className="event-review-page">
        <EventDecisionCard event={event} jobId={job.job_id} />
        <div className="event-review-gate">
          <p>缺件不能用人工按钮直接放行，请回到对应业务行补充资料。</p>
          <button type="button" className="btn primary" onClick={() => onGo('sample_desk')}>补充资料</button>
        </div>
      </div>
    )
  }

  return (
    <div className="event-review-page">
      <header className="event-review-toolbar">
        <div>
          <span className="eyebrow">待裁决</span>
          <h2>{currentIndex + 1} / {events.length}</h2>
        </div>
        <div>
          <button
            type="button"
            className="btn compact"
            disabled={currentIndex <= 0}
            onClick={() => setCurrentId(events[currentIndex - 1]?.event_id || currentId)}
          >上一项</button>
          <button
            type="button"
            className="btn compact"
            disabled={currentIndex >= events.length - 1}
            onClick={() => setCurrentId(events[currentIndex + 1]?.event_id || currentId)}
          >下一项</button>
        </div>
      </header>

      <EventDecisionCard event={event} jobId={job.job_id} />

      <section className="event-decision-form" aria-label="人工裁决">
        <div className="event-choice-row">
          {options.map((option) => (
            <button
              key={option.decision}
              type="button"
              className={`btn${choice === option.decision ? ' is-selected' : ''}`}
              aria-pressed={choice === option.decision}
              onClick={() => setChoice(option.decision)}
            >{option.label}</button>
          ))}
        </div>
        {(choice === 'OVERRIDE' || choice === 'MANUAL_VALUE') ? (
          <label>人工值<input value={value} onChange={(input) => setValue(input.target.value)} /></label>
        ) : null}
        {choice ? (
          <label>
            {choice === 'OVERRIDE' ? '覆盖理由' : '裁决理由'}
            <textarea value={reason} onChange={(input) => setReason(input.target.value)} />
          </label>
        ) : null}
        {event.action_step && !['event_review', 'sample_desk'].includes(event.action_step) ? (
          <button type="button" className="btn compact" onClick={() => onGo(event.action_step)}>
            打开专业核对
          </button>
        ) : null}
        {error ? <p className="err" role="alert">{error}</p> : null}
        <button
          type="button"
          className="btn primary event-decision-submit"
          disabled={!choice || busy}
          onClick={() => void submit()}
        >
          {busy ? '正在保存…' : '确认裁决并处理下一项'}
        </button>
      </section>
    </div>
  )
}
