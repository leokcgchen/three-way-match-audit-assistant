import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FieldResolution, Job } from '../types'

const { refreshFieldResolution } = vi.hoisted(() => ({ refreshFieldResolution: vi.fn() }))
vi.mock('../api', () => ({ api: { refreshFieldResolution, fileUrl: vi.fn(() => '') } }))
vi.mock('../lib/useActiveChainFiles', () => ({
  useActiveChainFiles: () => ({
    chainFileNames: ['order.pdf', 'receipt.pdf', 'invoice.pdf'],
    activeChain: { chain_id: 'YW-2025-3962', file_names: ['order.pdf', 'receipt.pdf', 'invoice.pdf'] },
    chainDocs: [
      { file_name: 'order.pdf', doc_type: 'order', fields: { orderNo: 'SO-251209-7214' } },
      { file_name: 'receipt.pdf', doc_type: 'receipt', fields: { orderNo: 'SO-251209-7214' } },
      { file_name: 'invoice.pdf', doc_type: 'invoice', fields: { orderNo: 'SO-251209-7214' } },
    ],
  }),
}))
vi.mock('../lib/useJobChainIds', () => ({ useJobChainIds: () => ['YW-2025-3962'] }))
vi.mock('../components/DocPreview', () => ({ DocPreview: () => <div data-testid="doc-preview" /> }))
vi.mock('../components/CapturePreview', () => ({ CapturePreview: () => <div /> }))
vi.mock('../components/FieldComparisonMatrix', () => ({ FieldComparisonMatrix: () => <div>旧对照表</div> }))
vi.mock('../components/AmountAmbiguityPanel', () => ({ AmountAmbiguityPanel: () => null }))
vi.mock('../lib/confirmLinkage', () => ({ confirmLinkagePrimary: vi.fn() }))

import { FieldConfirmPage } from './FieldConfirmPage'

const resolution: FieldResolution = {
  schema_version: 'field_resolution.v1', resolution_id: 'fr-3962', source_hash: 'source', chain_id: 'YW-2025-3962',
  evidence_nodes: [
    { evidence_id: 'ev-order', document_id: 'order.pdf', document_role: 'order', field_key: 'goodsName', raw_value: '伺服电机 SM-130', normalized_value: '伺服电机 SM-130', excerpt: '伺服电机 SM-130', page: 1, usable_for_decision: true, metadata: { file_name: 'order.pdf' } },
  ],
  edges: [], line_groups: [], issues: [], audit_log: [],
  comparison_plan: {
    schema_version: 'comparison_plan.v1', chain_id: 'YW-2025-3962', overall_status: 'PASS_WITH_WARNING', three_way_status: 'PASS_WITH_WARNING', cutoff_status: 'PASS',
    domains: {
      consistency: [{ row_id: 'goods', edge_id: 'edge-goods', concept: 'goods_identity', label: '货物', result: 'PASS', relation_type: 'EXACT_EQUAL', reason_code: 'RAW_VALUE_EQUAL', reason_text: '三份单据货品与型号一致。', evidence_ids: ['ev-order'], values: [{ evidence_id: 'ev-order', document_id: 'order.pdf', document_role: 'order', field_key: 'goodsName', value: '伺服电机 SM-130', page: 1, excerpt: '伺服电机 SM-130' }], transformations: [], counter_evidence: [] }],
      recalculation: [{ row_id: 'amount', concept: 'line_amount', label: 'SM-130 金额复算', result: 'PASS', calculation: '20 × 5000 + 13000 = 113000', evidence_ids: ['ev-order'], reason_codes: [] }],
      chronology: { events: [{ label: '验收/控制权转移', value: '2026-01-02T09:40', evidence_id: 'ev-order' }, { label: '开票日期', value: '2026-01-02T14:50', evidence_id: 'ev-order' }], reporting_period_end: '2025-12-31', status: 'PASS', reason_text: '先验收后开票，时序合理。' },
      document_specific: [{ row_id: 'doc-no', field_key: 'documentNo', label: '单据编号', value: 'SO-251209-7214', document_id: 'order.pdf', document_role: 'order', evidence_id: 'ev-order', comparison_effect: 'NONE' }],
      issues: [{ issue_code: 'CUSTOMER_CODE_MAPPING_REQUIRED', severity: 'WARNING', title: '客户编码待映射', message: '销售系统与仓储系统客户编码不同。', edge_id: 'edge-code', evidence_ids: ['ev-order'], values: ['KH-330212-0142', 'KH-NB-0062'], resolution_status: 'PENDING' }],
    },
  },
}

const job: Job = {
  job_id: 'job-3962', title: '3962 字段核对', goal_ids: ['gospd01010'],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [
    { file_name: 'order.pdf', doc_type: 'order', fields: { orderNo: 'SO-251209-7214' } },
    { file_name: 'receipt.pdf', doc_type: 'receipt', fields: { orderNo: 'SO-251209-7214' } },
    { file_name: 'invoice.pdf', doc_type: 'invoice', fields: { orderNo: 'SO-251209-7214' } },
  ],
  fields_confirmed: false, active_step: 'field_confirm', active_chain_id: 'YW-2025-3962',
}

describe('FieldConfirmPage expanded comparison matrix', () => {
  it('keeps the legacy horizontal matrix as the primary view and explanation as optional detail', async () => {
    refreshFieldResolution.mockResolvedValueOnce(resolution)
    const user = userEvent.setup()
    render(<FieldConfirmPage job={job} onJob={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '字段对照' }))
    expect(await screen.findByText('旧对照表')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '一致性与复算' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看判断依据' }))
    expect(await screen.findByRole('heading', { name: '一致性与复算' })).toBeInTheDocument()
    expect(screen.getByText('20 × 5000 + 13000 = 113000')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '时序与业务过程' })).toBeInTheDocument()
    expect(screen.getByText('验收 09:40 → 开票 14:50')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '单据专有信息' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '客户编码待映射' })).toBeInTheDocument()
    const consistency = screen.getByRole('table', { name: '跨单据一致性字段' })
    expect(within(consistency).queryByText(/日期/)).not.toBeInTheDocument()
  })
})
