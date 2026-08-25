import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../types'
import { storeFieldTraceTarget } from '../lib/fieldTraceNavigation'

const { patchFields } = vi.hoisted(() => ({ patchFields: vi.fn() }))
vi.mock('../api', () => ({ api: {
  patchFields,
  fileUrl: vi.fn(() => ''),
  refreshFieldResolution: vi.fn(() => new Promise(() => {})),
} }))
vi.mock('../lib/useActiveChainFiles', () => ({
  useActiveChainFiles: () => ({
    chainFileNames: ['invoice.pdf'],
    activeChain: { chain_id: 'SO-1', file_names: ['invoice.pdf'] },
    chainDocs: [{
      file_name: 'invoice.pdf',
      doc_type: 'invoice',
      fields: { invoiceNo: '12345678', performanceObligations: ['交付商品'] },
    }],
  }),
}))
vi.mock('../lib/useJobChainIds', () => ({ useJobChainIds: () => ['SO-1'] }))
vi.mock('../components/DocPreview', () => ({ DocPreview: () => <div /> }))
vi.mock('../components/CapturePreview', () => ({ CapturePreview: () => <div /> }))
vi.mock('../components/FieldComparisonMatrix', () => ({ FieldComparisonMatrix: () => <div /> }))
vi.mock('../components/AmountAmbiguityPanel', () => ({ AmountAmbiguityPanel: () => null }))
vi.mock('../lib/confirmLinkage', () => ({ confirmLinkagePrimary: vi.fn() }))

import { FieldConfirmPage } from './FieldConfirmPage'

const job: Job = {
  job_id: 'edit-job', title: '字段编辑', goal_ids: ['gospd01010'],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [{
    file_name: 'invoice.pdf',
    doc_type: 'invoice',
    fields: { invoiceNo: '12345678', performanceObligations: ['交付商品'] },
  }],
  fields_confirmed: false, active_step: 'field_confirm', active_chain_id: 'SO-1',
}

describe('FieldConfirmPage conditional save', () => {
  beforeEach(() => sessionStorage.clear())

  it('does not report unsaved changes while loading saved structured fields', async () => {
    const onDirtyChange = vi.fn()
    render(<FieldConfirmPage job={job} onJob={vi.fn()} onDirtyChange={onDirtyChange} />)

    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false))
    expect(onDirtyChange).not.toHaveBeenCalledWith(true)
  })

  it('shows save only after the expanded editor has an actual change', async () => {
    const user = userEvent.setup()
    const onDirtyChange = vi.fn()
    render(<FieldConfirmPage job={job} onJob={vi.fn()} onDirtyChange={onDirtyChange} />)
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false))
    expect(screen.queryByRole('button', { name: '回工作台' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '字段对照' }))
    expect(screen.queryByRole('button', { name: '保存本单' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '展开字段编辑' }))
    expect(screen.queryByRole('button', { name: '保存本单' })).not.toBeInTheDocument()
    const input = screen.getByLabelText('发票号码')
    await user.clear(input)
    await user.type(input, '12345679')
    expect(screen.getByRole('button', { name: '保存本单' })).toBeEnabled()
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true))
    await user.clear(input)
    await user.type(input, '12345678')
    expect(screen.queryByRole('button', { name: '保存本单' })).not.toBeInTheDocument()
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false))
  })

  it('does not report an incomplete added field as unsaved and clears dirty on unmount', async () => {
    const user = userEvent.setup()
    const onDirtyChange = vi.fn()
    const { unmount } = render(
      <FieldConfirmPage job={job} onJob={vi.fn()} onDirtyChange={onDirtyChange} />,
    )
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false))
    await user.click(screen.getByRole('button', { name: '字段对照' }))
    await user.click(screen.getByRole('button', { name: '展开字段编辑' }))
    await user.click(screen.getByText('追加字段'))
    await user.type(screen.getByPlaceholderText('字段名'), 'customReference')
    expect(onDirtyChange).not.toHaveBeenCalledWith(true)

    await user.type(screen.getByPlaceholderText('字段值'), 'A-001')
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true))
    unmount()
    expect(onDirtyChange).toHaveBeenLastCalledWith(false)
  })

  it('consumes a conclusion evidence link and reports the original field location', async () => {
    storeFieldTraceTarget({
      jobId: 'edit-job', chainId: 'SO-1', fileName: 'invoice.pdf', fieldKey: 'invoiceNo',
    })

    render(<FieldConfirmPage job={job} onJob={vi.fn()} />)

    expect(await screen.findByText('已定位原件：invoice.pdf · 发票号码')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '字段对照' })).toHaveClass('primary')
  })
})
