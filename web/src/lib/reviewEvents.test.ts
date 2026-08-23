import { describe, expect, it } from 'vitest'

import type { ReviewEvent } from '../types'
import {
  eventHumanReason,
  eventPrimaryAction,
  sortReviewEvents,
} from './reviewEvents'

function makeEvent(overrides: Partial<ReviewEvent> = {}): ReviewEvent {
  return {
    event_id: 'evt-1',
    chain_id: 'SO25-0281',
    event_type: 'LOW_CONFIDENCE',
    severity: 'REVIEW',
    state: 'OPEN',
    title: '字段需要确认',
    reason: '识别结果置信度较低',
    evidence: {},
    ledger_value: null,
    observed_value: null,
    ai_suggestion: null,
    confidence: null,
    action_kind: 'REVIEW_FIELD',
    action_step: 'field_confirm',
    source_ref: 'field:invoice.pdf:amount',
    invalidates: [],
    ...overrides,
  }
}

describe('review event presentation rules', () => {
  it('orders blocking events before review and sample events', () => {
    const sorted = sortReviewEvents([
      makeEvent({ event_id: 'sample', severity: 'SAMPLE' }),
      makeEvent({ event_id: 'review', severity: 'REVIEW' }),
      makeEvent({ event_id: 'blocking', severity: 'BLOCKING' }),
    ])
    expect(sorted.map((row) => row.severity)).toEqual([
      'BLOCKING',
      'REVIEW',
      'SAMPLE',
    ])
  })

  it('uses risk score within the same severity', () => {
    const sorted = sortReviewEvents([
      makeEvent({ event_id: 'low', evidence: { risk_score: 20 } }),
      makeEvent({ event_id: 'high', evidence: { risk_score: 90 } }),
    ])
    expect(sorted[0].event_id).toBe('high')
  })

  it('describes ledger differences in plain language', () => {
    const text = eventHumanReason(
      makeEvent({
        event_type: 'LEDGER_MISMATCH',
        ledger_value: 100,
        observed_value: 98,
      }),
    )
    expect(text).toContain('账载值 100')
    expect(text).toContain('单据值 98')
  })

  it('maps event types to one contextual primary action', () => {
    expect(
      eventPrimaryAction(makeEvent({ event_type: 'MISSING_DOCUMENT' })),
    ).toBe('上传补充资料')
    expect(
      eventPrimaryAction(makeEvent({ event_type: 'QUALITY_SAMPLE' })),
    ).toBe('开始抽检')
  })
})
