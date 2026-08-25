import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewEventSummary } from '../types'
import { EventSummaryBar } from './EventSummaryBar'

const summary: ReviewEventSummary = {
  open: 3,
  blocking: 2,
  missing: 1,
  review: 1,
  sample: 0,
  passed: 7,
}

describe('EventSummaryBar', () => {
  it('shows decisions, missing items and automatic passes without technical gate terms', () => {
    render(<EventSummaryBar summary={summary} onPrimary={vi.fn()} />)
    expect(screen.getByText('待处理 3')).toBeInTheDocument()
    expect(screen.getByText('缺少凭证资料 1')).toBeInTheDocument()
    expect(screen.getByText('自动通过 7')).toBeInTheDocument()
    expect(screen.queryByText(/Gate/i)).not.toBeInTheDocument()
  })

  it('has exactly one primary action for the current state', async () => {
    const onPrimary = vi.fn()
    const user = userEvent.setup()
    render(<EventSummaryBar summary={summary} onPrimary={onPrimary} />)
    const primary = screen.getByRole('button', { name: '处理 3 个异常' })
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
    await user.click(primary)
    expect(onPrimary).toHaveBeenCalledOnce()
  })
})
