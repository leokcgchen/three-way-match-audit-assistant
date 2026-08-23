import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Job, ReviewEvent } from '../types'

const { listReviewEvents, decideReviewEvent } = vi.hoisted(() => ({
  listReviewEvents: vi.fn(),
  decideReviewEvent: vi.fn(),
}))

vi.mock('../api', () => ({
  api: { listReviewEvents, decideReviewEvent },
}))
vi.mock('../components/DocPreview', () => ({
  DocPreview: () => <div>原件预览</div>,
}))

import { EventReviewPage } from './EventReviewPage'

const ledgerMismatchEvent: ReviewEvent = {
  event_id: 'evt-ledger', chain_id: 'SO25-0281', event_type: 'LEDGER_MISMATCH',
  severity: 'REVIEW', state: 'OPEN', title: '单据与账载信息不一致',
  reason: '发票金额与序时账不同', evidence: { file_name: 'invoice.pdf', page_no: 2 },
  ledger_value: 100, observed_value: 98, ai_suggestion: 98, confidence: 0.76,
  action_kind: 'REVIEW_FIELD', action_step: 'field_confirm', source_ref: 'ledger:invoice.pdf',
  invalidates: ['amount'],
}

const job = {
  job_id: 'job-review',
  title: '裁决测试',
  goal_ids: [],
  plan: {
    goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [],
    workbook_sheets: [], skipped_steps: [],
  },
  classified: [],
  fields_confirmed: false,
  active_step: 'event_review',
} satisfies Job

describe('EventReviewPage', () => {
  it('requires a reason before submitting an override', async () => {
    listReviewEvents.mockResolvedValue({
      events: [ledgerMismatchEvent],
      summary: { open: 1, blocking: 0, missing: 0, review: 1, sample: 0, passed: 0 },
    })
    const user = userEvent.setup()
    render(<EventReviewPage job={job} onJob={vi.fn()} onGo={vi.fn()} />)
    await screen.findByText('单据与账载信息不一致')
    await user.click(screen.getByRole('button', { name: '覆盖 AI 结论' }))
    await user.click(screen.getByRole('button', { name: '确认裁决并处理下一项' }))
    expect(screen.getByRole('alert')).toHaveTextContent('请填写覆盖理由')
    expect(decideReviewEvent).not.toHaveBeenCalled()
  })

  it('routes missing documents to upload instead of exposing a fake decision', async () => {
    const onGo = vi.fn()
    const user = userEvent.setup()
    listReviewEvents.mockResolvedValue({
      events: [{
        ...ledgerMismatchEvent,
        event_id: 'evt-missing',
        event_type: 'MISSING_DOCUMENT',
        severity: 'BLOCKING',
        title: '缺少业务凭证',
        action_kind: 'UPLOAD_EVIDENCE',
        action_step: 'sample_desk',
      }],
      summary: { open: 1, blocking: 1, missing: 1, review: 0, sample: 0, passed: 0 },
    })
    render(<EventReviewPage job={job} onJob={vi.fn()} onGo={onGo} />)
    await user.click(await screen.findByRole('button', { name: '补充资料' }))
    expect(onGo).toHaveBeenCalledWith('sample_desk')
    expect(screen.queryByRole('button', { name: '确认裁决并处理下一项' })).not.toBeInTheDocument()
  })
})
