import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ConclusionFinding, Job } from '../types'
import { CutoffEvidenceTable, ThreeWayEvidenceTable } from './ConclusionEvidenceTable'

const job: Job = {
  job_id: 'job-1',
  title: '结论证据',
  goal_ids: ['gospd01010'],
  active_chain_id: 'S-001',
  active_step: 'conclusion_gate5',
  fields_confirmed: true,
  plan: {
    goal_ids: [], goals: [], required_steps: [], step_labels: [],
    required_dimensions: [], workbook_sheets: [], skipped_steps: [],
  },
  classified: [
    { file_name: 'order.pdf', doc_type: 'order', fields: { orderNo: 'SO25-0281', documentDate: '2025-12-12', quantity: '357', totalAmount: '10942.9', buyerName: '甲公司' } },
    { file_name: 'receipt.pdf', doc_type: 'receipt', fields: { documentNo: 'YS26-0281', orderNo: 'SO25-0281', quantity: '357', buyerName: '甲公司', acceptanceDate: '2026-01-02' } },
    { file_name: 'invoice.pdf', doc_type: 'invoice', fields: { invoiceNo: '25322025000000002811', documentDate: '2025-12-20', quantity: '357', totalAmount: '10942.9', buyerName: '甲公司', postingDate: '2025-12-20' } },
  ],
  three_way: {
    match_result: {
      comparisons: [
        { field_name: 'supplier_name', is_consistent: true },
        { field_name: 'total_amount', is_consistent: true },
        { field_name: 'quantity', is_consistent: true },
      ],
    },
  },
}

describe('ConclusionEvidenceTable', () => {
  it('renders the same horizontal document evidence as a read-only, traceable matrix', async () => {
    const user = userEvent.setup()
    const onTrace = vi.fn()

    render(<ThreeWayEvidenceTable job={job} chainFileNames={['order.pdf', 'receipt.pdf', 'invoice.pdf']} onTrace={onTrace} />)

    expect(screen.getByRole('table', { name: '三单字段横向对照' })).toBeInTheDocument()
    const comparisonTable = screen.getByRole('table', { name: '三单字段横向对照' })
    expect(within(comparisonTable).getByRole('columnheader', { name: '订单' })).toBeInTheDocument()
    expect(within(comparisonTable).getByRole('columnheader', { name: '签收/发货' })).toBeInTheDocument()
    expect(within(comparisonTable).getByRole('columnheader', { name: '发票' })).toBeInTheDocument()
    expect(within(comparisonTable).getByRole('rowheader', { name: '客户名称' })).toBeInTheDocument()
    expect(within(comparisonTable).getByRole('rowheader', { name: '价税合计' })).toBeInTheDocument()
    expect(within(comparisonTable).getByRole('rowheader', { name: '数量' })).toBeInTheDocument()
    expect(within(comparisonTable).queryByText('订单编号')).not.toBeInTheDocument()
    expect(within(comparisonTable).queryByText('发票号码')).not.toBeInTheDocument()
    expect(within(comparisonTable).queryByText('单据日期')).not.toBeInTheDocument()
    expect(screen.queryByText('需复核')).not.toBeInTheDocument()

    await user.click(screen.getByText('查看来源标识与日期（不参与三单一致性）'))
    const sourceTable = screen.getByRole('table', { name: '来源标识与日期' })
    expect(within(sourceTable).getByText('订单编号')).toBeInTheDocument()
    expect(within(sourceTable).getByText('发票号码')).toBeInTheDocument()
    expect(within(sourceTable).getByText('单据日期')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '订单 数量 357，查看核对字段及原件位置' }))
    expect(onTrace).toHaveBeenCalledWith({
      jobId: 'job-1', chainId: 'S-001', fileName: 'order.pdf', fieldKey: 'quantity',
    })
  })

  it('renders cutoff dates as evidence links and keeps the prose reason below the table', async () => {
    const user = userEvent.setup()
    const onTrace = vi.fn()
    const finding: ConclusionFinding = {
      finding_id: 'cutoff-1', step: 'cutoff', step_label: '截止性', module: 'cutoff',
      title: '截止性测试未通过', status: 'FAIL', blocking: true, method: '',
      summary: '控制权于期后转移，收入却记在期内。',
      fields_used: [
        { doc_type: 'receipt', file_name: 'receipt.pdf', field_key: 'acceptanceDate', field_label: '签收/控制权日', value: '2026-01-02' },
        { doc_type: 'invoice', file_name: 'invoice.pdf', field_key: 'postingDate', field_label: '序时账入账日', value: '2025-12-20' },
      ],
      period: { 报告期末: '2025-12-31', 偏差天数: -13 },
    }

    render(<CutoffEvidenceTable finding={finding} jobId="job-1" chainId="S-001" onTrace={onTrace} />)

    const decision = screen.getByLabelText('截止性结论：不通过')
    expect(decision).toHaveClass('tw-decision-err')
    expect(within(decision).getByText('截止性结论')).toBeInTheDocument()
    expect(within(decision).getByText('不通过')).toBeInTheDocument()
    expect(within(decision).getByText(
      '本笔截止性测试不通过。判断基准为报告期末日 2025-12-31，以签收/控制权转移日与入账日判断收入归属期间：控制权转移日 2026-01-02 位于期后，入账日 2025-12-20 位于期内，属于跨期末提前确认，入账相对控制权转移日提前 13 天。因此收入未记入正确会计期间，相关应收账款期间需要复核。',
    )).toBeInTheDocument()
    expect(screen.getByRole('table', { name: '截止性期间判断证据' })).toBeInTheDocument()
    expect(screen.getByText('报告期末')).toBeInTheDocument()
    expect(screen.getByText('偏差天数')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '签收/控制权日 2026-01-02，查看核对字段及原件位置' }))
    expect(onTrace).toHaveBeenCalledWith({
      jobId: 'job-1', chainId: 'S-001', fileName: 'receipt.pdf', fieldKey: 'acceptanceDate',
    })
  })

  it('uses the same green decision-card language when cutoff passes', () => {
    const finding: ConclusionFinding = {
      finding_id: 'cutoff-pass', step: 'cutoff', step_label: '截止性', module: 'cutoff',
      title: '截止性测试通过', status: 'PASS', blocking: false, method: '',
      summary: '控制权转移日与入账日均属于报告期内。',
      fields_used: [
        { doc_type: 'receipt', file_name: 'receipt.pdf', field_key: 'acceptanceDate', field_label: '签收/控制权日', value: '2025-12-20' },
        { doc_type: 'invoice', file_name: 'invoice.pdf', field_key: 'postingDate', field_label: '序时账入账日', value: '2025-12-21' },
      ],
      period: { 报告期末: '2025-12-31', 偏差天数: 1 },
    }

    render(<CutoffEvidenceTable finding={finding} jobId="job-1" chainId="S-001" onTrace={vi.fn()} />)

    const decision = screen.getByLabelText('截止性结论：通过')
    expect(decision).toHaveClass('tw-decision-ok')
    expect(within(decision).getByText('通过')).toBeInTheDocument()
  })
})
