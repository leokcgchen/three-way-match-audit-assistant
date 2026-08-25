import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FieldReasonDrawer } from './FieldReasonDrawer'

const row = {
  row_id: 'goods', edge_id: 'edge-goods', concept: 'goods_identity', label: '货物', result: 'PASS',
  relation_type: 'SEMANTIC_EQUIVALENT', reason_code: 'NAME_MODEL_SPLIT_EQUIVALENT',
  reason_text: '订单品名、签收品名和型号共同支持实质一致。',
  evidence_ids: ['ev-order', 'ev-receipt'], transformations: ['拆分品名与型号'], counter_evidence: [],
  values: [],
}
const evidence = [
  { evidence_id: 'ev-order', document_id: 'doc-1', document_role: 'order', field_key: 'goodsName', raw_value: '伺服电机 SM-130', normalized_value: '伺服电机 SM-130', excerpt: '伺服电机 SM-130', page: 1, metadata: { file_name: 'YW-2025-3962_销售订单.pdf' } },
  { evidence_id: 'ev-receipt', document_id: 'doc-2', document_role: 'receipt', field_key: 'model', raw_value: 'SM-130', normalized_value: 'SM-130', excerpt: 'SM-130', page: 1, metadata: { file_name: 'YW-2025-3962_签收验收单.pdf' } },
]

function Harness({ onSelectEvidence }: { onSelectEvidence: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" onClick={() => setOpen(true)}>查看原因</button>
    <FieldReasonDrawer open={open} row={row} evidenceNodes={evidence} onClose={() => setOpen(false)} onSelectEvidence={onSelectEvidence} />
  </>
}

describe('FieldReasonDrawer', () => {
  it('shows source anchors and restores focus after Escape', async () => {
    const user = userEvent.setup()
    const onSelectEvidence = vi.fn()
    render(<Harness onSelectEvidence={onSelectEvidence} />)
    const trigger = screen.getByRole('button', { name: '查看原因' })
    await user.click(trigger)

    expect(screen.getByRole('dialog', { name: '货物：判断依据' })).toBeInTheDocument()
    expect(screen.getByText('拆分品名与型号')).toBeInTheDocument()
    expect(screen.getByText('YW-2025-3962_销售订单.pdf')).toBeInTheDocument()
    expect(screen.getAllByText('第 1 页')).toHaveLength(2)
    expect(screen.queryByText('缺少证据')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /YW-2025-3962_销售订单.pdf/ }))
    expect(onSelectEvidence).toHaveBeenCalledWith('ev-order')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
