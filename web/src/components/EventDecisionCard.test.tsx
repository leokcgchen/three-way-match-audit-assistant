import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ReviewEvent } from '../types'
import { EventDecisionCard } from './EventDecisionCard'

export const ledgerMismatchEvent: ReviewEvent = {
  event_id: 'evt-ledger',
  chain_id: 'SO25-0281',
  event_type: 'LEDGER_MISMATCH',
  severity: 'REVIEW',
  state: 'OPEN',
  title: '单据与账载信息不一致',
  reason: '发票金额与序时账不同',
  evidence: { file_name: 'invoice.pdf', page_no: 2, field_name: 'totalAmount' },
  ledger_value: 100,
  observed_value: 98,
  ai_suggestion: 98,
  confidence: 0.76,
  action_kind: 'REVIEW_FIELD',
  action_step: 'field_confirm',
  source_ref: 'ledger:invoice.pdf',
  invalidates: ['amount', 'gate5', 'workbook'],
}

describe('EventDecisionCard', () => {
  it('shows evidence, ledger value, AI advice, confidence and trigger reason together', () => {
    render(<EventDecisionCard event={ledgerMismatchEvent} />)
    expect(screen.getByText('账载值')).toBeInTheDocument()
    expect(screen.getByText('100')).toBeInTheDocument()
    expect(screen.getByText('单据原始值')).toBeInTheDocument()
    expect(screen.getByText('AI 建议')).toBeInTheDocument()
    expect(screen.getByText(/置信度 76%/)).toBeInTheDocument()
    expect(screen.getByText(ledgerMismatchEvent.reason)).toBeInTheDocument()
    expect(screen.getByText(/invoice\.pdf · 第 2 页/)).toBeInTheDocument()
  })
})
