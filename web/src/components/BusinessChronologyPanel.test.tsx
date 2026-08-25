import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BusinessChronologyPanel } from './BusinessChronologyPanel'

describe('BusinessChronologyPanel', () => {
  it('renders business order without pretending dates should be equal', () => {
    render(<BusinessChronologyPanel chronology={{
      events: [
        { label: '验收/控制权转移', value: '2026-01-02T09:40', evidence_id: 'ev-r', document_id: 'receipt.pdf', page: 1 },
        { label: '开票日期', value: '2026-01-02T14:50', evidence_id: 'ev-i', document_id: 'invoice.pdf', page: 1 },
      ],
      reporting_period_end: '2025-12-31', status: 'PASS', reason_text: '控制权转移与开票先后顺序合理。',
    }} onSelectEvidence={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '时序与业务过程' })).toBeInTheDocument()
    expect(screen.getByText('验收 09:40 → 开票 14:50')).toBeInTheDocument()
    expect(screen.queryByText('完全一致')).not.toBeInTheDocument()
  })
})

