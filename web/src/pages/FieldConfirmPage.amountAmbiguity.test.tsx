import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Job } from '../types'

const { confirmFields, confirmLinkagePrimary } = vi.hoisted(() => ({
  confirmFields: vi.fn(),
  confirmLinkagePrimary: vi.fn(),
}))

vi.mock('../api', () => ({ api: { confirmFields, fileUrl: vi.fn(() => '') } }))
vi.mock('../lib/useActiveChainFiles', () => ({
  useActiveChainFiles: () => ({
    chainFileNames: ['order.pdf'],
    activeChain: { chain_id: 'SO25-0282', file_names: ['order.pdf'] },
    chainDocs: [{ file_name: 'order.pdf', doc_type: 'order', fields: { orderNo: 'SO25-0282' } }],
  }),
}))
vi.mock('../lib/useJobChainIds', () => ({ useJobChainIds: () => ['SO25-0282'] }))
vi.mock('../components/DocPreview', () => ({ DocPreview: () => <div /> }))
vi.mock('../components/CapturePreview', () => ({ CapturePreview: () => <div /> }))
vi.mock('../components/FieldComparisonMatrix', () => ({ FieldComparisonMatrix: () => <div /> }))
vi.mock('../components/AmountAmbiguityPanel', () => ({
  AmountAmbiguityPanel: ({ onOpenCount }: { onOpenCount?: (count: number) => void }) => (
    <button type="button" onClick={() => onOpenCount?.(0)}>金额加载为空</button>
  ),
}))
vi.mock('../lib/confirmLinkage', () => ({ confirmLinkagePrimary }))

import { FieldConfirmPage } from './FieldConfirmPage'

const job: Job = {
  job_id: 'job-1', title: '字段确认', goal_ids: ['gospd01010'],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [{ file_name: 'order.pdf', doc_type: 'order', fields: { orderNo: 'SO25-0282' } }],
  fields_confirmed: false, active_step: 'field_confirm', active_chain_id: 'SO25-0282',
}

describe('FieldConfirmPage amount ambiguity lifecycle', () => {
  it('clears a resolved amount-ambiguity error after the active chain loads zero cards', async () => {
    confirmFields.mockRejectedValueOnce(new Error('还有 1 项金额歧义未关闭'))
    const user = userEvent.setup()
    render(<FieldConfirmPage job={job} onJob={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '确认本笔字段' }))
    expect((await screen.findAllByText('还有 1 项金额歧义未关闭')).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: '金额加载为空' }))

    expect(screen.queryAllByText('还有 1 项金额歧义未关闭')).toHaveLength(0)
  })

  it('does not clear a non-amount confirmation error when amount cards are empty', async () => {
    confirmFields.mockRejectedValueOnce(new Error('还缺必需单据：发票'))
    const user = userEvent.setup()
    render(<FieldConfirmPage job={job} onJob={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '确认本笔字段' }))
    expect((await screen.findAllByText('还缺必需单据：发票')).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: '金额加载为空' }))

    expect(screen.getAllByText('还缺必需单据：发票')).not.toHaveLength(0)
  })

  it('does not automatically run linkage for server active A after confirming displayed chain B', async () => {
    confirmFields.mockResolvedValueOnce({
      ...job,
      active_chain_id: 'SO25-0281',
      gospd_sample_results: {
        'SO25-0281': { fields_confirmed: false },
        'SO25-0282': { fields_confirmed: true },
      },
    })
    const user = userEvent.setup()
    render(<FieldConfirmPage job={job} onJob={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '确认本笔字段' }))

    expect(confirmFields).toHaveBeenCalledWith('job-1', 'SO25-0282')
    expect(confirmLinkagePrimary).not.toHaveBeenCalled()
  })
})
