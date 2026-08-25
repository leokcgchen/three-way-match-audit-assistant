import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ResolutionIssuesPanel } from './ResolutionIssuesPanel'

describe('ResolutionIssuesPanel', () => {
  it('requires an explanation before resolving customer-code mapping', async () => {
    const user = userEvent.setup()
    const onDecision = vi.fn().mockResolvedValue(undefined)
    render(<ResolutionIssuesPanel issues={[{
      issue_code: 'CUSTOMER_CODE_MAPPING_REQUIRED', severity: 'WARNING', title: '客户编码待映射',
      message: '销售系统与仓储系统编码不同。', edge_id: 'edge-code', evidence_ids: ['ev-a', 'ev-b'],
      values: ['KH-330212-0142', 'KH-NB-0062'], resolution_status: 'PENDING',
    }]} onDecision={onDecision} />)

    expect(screen.getByRole('heading', { name: '待解释事项' })).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: '确认已解释' })
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('客户编码映射说明'), '已取得销售系统与仓储系统客户编码映射表')
    await user.click(submit)
    expect(onDecision).toHaveBeenCalledWith({
      edgeId: 'edge-code', decision: 'CONFIRMED', reason: '已取得销售系统与仓储系统客户编码映射表',
    })
  })
})

