import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { ClassifiedDoc } from '../types'
import { BusinessIndexEvidence } from './BusinessIndexEvidence'

describe('BusinessIndexEvidence', () => {
  it('shows the canonical business and explains an exact order alias match', async () => {
    const user = userEvent.setup()
    const document = {
      file_name: 'SO-251209-7214_签收单.pdf',
      doc_type: 'receipt',
      sample_business_id: 'YW-2025-3962',
      business_index_status: 'MATCHED',
      business_index_confidence: 'high',
      business_index_evidence: [
        {
          type: 'order_number',
          detected: 'SO-251209-7214',
          matched: 'SO-251209-7214',
          source: 'filename',
          match_method: 'normalized_exact',
          business_ids: ['YW-2025-3962'],
        },
      ],
    } satisfies ClassifiedDoc

    render(<BusinessIndexEvidence document={document} />)

    expect(screen.getByText('归入业务 YW-2025-3962')).toBeInTheDocument()
    await user.click(screen.getByText('查看归类依据'))
    expect(screen.getByText('文件名中的订单号')).toBeInTheDocument()
    expect(screen.getAllByText('SO-251209-7214').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('规范化后精确匹配')).toBeInTheDocument()
  })

  it('falls back to a compact assigned-business label for migrated records', () => {
    const document = {
      file_name: 'legacy.pdf',
      doc_type: 'invoice',
      sample_business_id: 'YW-2025-3962',
    } satisfies ClassifiedDoc

    render(<BusinessIndexEvidence document={document} />)

    expect(screen.getByText('归入业务 YW-2025-3962')).toBeInTheDocument()
    expect(screen.queryByText('查看归类依据')).not.toBeInTheDocument()
  })
})
