import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ExplainableFieldMatrix } from './ExplainableFieldMatrix'
import type { ComparisonPlan } from '../types'

const plan = {
  schema_version: 'comparison_plan.v1',
  chain_id: 'YW-2025-3962',
  overall_status: 'PASS_WITH_WARNING',
  three_way_status: 'PASS_WITH_WARNING',
  cutoff_status: 'PASS',
  domains: {
    consistency: [
      {
        row_id: 'goods', edge_id: 'edge-goods', concept: 'goods_identity', label: '货物',
        result: 'PASS', relation_type: 'SEMANTIC_EQUIVALENT', reason_code: 'NAME_MODEL_SPLIT_EQUIVALENT',
        reason_text: '订单品名和签收单品名、型号共同支持。', evidence_ids: ['ev-order', 'ev-receipt'],
        values: [
          { evidence_id: 'ev-order', document_id: 'order.pdf', document_role: 'order', field_key: 'goodsName', value: '伺服电机 SM-130', page: 1, excerpt: '伺服电机 SM-130' },
          { evidence_id: 'ev-receipt', document_id: 'receipt.pdf', document_role: 'receipt', field_key: 'goodsName', value: '伺服电机', page: 1, excerpt: '伺服电机' },
        ],
        transformations: ['拆分品名与型号'], counter_evidence: [],
      },
      {
        row_id: 'optional', edge_id: 'edge-optional', concept: 'transport', label: '运输安排',
        result: 'NOT_APPLICABLE', relation_type: 'DOCUMENT_SPECIFIC', reason_code: 'NOT_APPLICABLE',
        reason_text: '发票无须重复列示运输安排。', evidence_ids: [], values: [], transformations: [], counter_evidence: [],
      },
    ],
    recalculation: [
      { row_id: 'amount', concept: 'line_amount', label: 'SM-130 金额复算', result: 'PASS', calculation: '20 × 5000 + 13000 = 113000', evidence_ids: ['ev-order'], reason_codes: [] },
    ],
    chronology: { events: [], reporting_period_end: '2025-12-31', status: 'PASS', reason_text: '' },
    document_specific: [],
    issues: [],
  },
} as const

describe('ExplainableFieldMatrix', () => {
  it('separates consistency and recalculation and uses audit-readable badges', async () => {
    const user = userEvent.setup()
    const onExplain = vi.fn()
    render(<ExplainableFieldMatrix plan={plan as unknown as ComparisonPlan} onExplain={onExplain} onSelectEvidence={vi.fn()} />)

    expect(screen.getByRole('heading', { name: '一致性与复算' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: '跨单据一致性字段' })).toBeInTheDocument()
    expect(screen.getByText('实质一致')).toBeInTheDocument()
    expect(screen.getByText('复算一致')).toBeInTheDocument()
    expect(screen.getByText('不适用')).toBeInTheDocument()
    expect(screen.getByText('不一致 0 项')).toBeInTheDocument()

    const row = screen.getByRole('row', { name: /货物/ })
    await user.click(within(row).getByRole('button', { name: '为什么' }))
    expect(onExplain).toHaveBeenCalledWith(expect.objectContaining({ row_id: 'goods' }))
  })
})
