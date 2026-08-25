import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../types'

const { patchFields, declareMixed } = vi.hoisted(() => ({
  patchFields: vi.fn(),
  declareMixed: vi.fn(),
}))
vi.mock('../api', () => ({ api: {
  patchFields,
  declareMixed,
  fileUrl: vi.fn(() => ''),
  refreshFieldResolution: vi.fn(() => new Promise(() => {})),
} }))
vi.mock('../lib/useActiveChainFiles', () => ({
  useActiveChainFiles: () => ({
    chainFileNames: ['bill-of-lading.pdf', 'unknown.pdf'],
    activeChain: { chain_id: 'YW-1', file_names: ['bill-of-lading.pdf', 'unknown.pdf'] },
    chainDocs: [
      {
        file_name: 'bill-of-lading.pdf',
        doc_type: 'other',
        raw_text: 'BILL OF LADING',
        fields: { documentNo: 'BL-001' },
      },
      {
        file_name: 'unknown.pdf',
        doc_type: 'other',
        raw_text: 'UNKNOWN',
        fields: { documentNo: 'U-001' },
      },
    ],
  }),
}))
vi.mock('../lib/useJobChainIds', () => ({ useJobChainIds: () => ['YW-1'] }))
vi.mock('../components/DocPreview', () => ({ DocPreview: () => <div /> }))
vi.mock('../components/CapturePreview', () => ({ CapturePreview: () => <div /> }))
vi.mock('../components/FieldComparisonMatrix', () => ({ FieldComparisonMatrix: () => <div /> }))
vi.mock('../components/AmountAmbiguityPanel', () => ({ AmountAmbiguityPanel: () => null }))
vi.mock('../lib/confirmLinkage', () => ({ confirmLinkagePrimary: vi.fn() }))

import { FieldConfirmPage } from './FieldConfirmPage'

const job: Job = {
  job_id: 'custom-type-job',
  title: '自定义当前文件类型',
  goal_ids: ['gospd01030'],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [
    { file_name: 'bill-of-lading.pdf', doc_type: 'other', fields: { documentNo: 'BL-001' } },
    { file_name: 'unknown.pdf', doc_type: 'other', fields: { documentNo: 'U-001' } },
  ],
  fields_confirmed: false,
  active_step: 'field_confirm',
  active_chain_id: 'YW-1',
}

describe('FieldConfirmPage current-file document name', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  it('asks for a concrete name when the current document type is other', () => {
    render(<FieldConfirmPage job={job} onJob={vi.fn()} />)

    expect(screen.getByLabelText('当前文件具体名称')).toBeInTheDocument()
    expect(screen.getByText('文件类型不确定，疑似内部存在杂乱的文件类型')).toBeInTheDocument()
    expect(screen.getByText('仅修改当前文件名称，不新增系统单据类型。')).toBeInTheDocument()
    expect(screen.getAllByText('其他（待确认）').length).toBeGreaterThan(0)
  })

  it('blocks an empty custom name and saves 海运提单 only for the current file', async () => {
    const user = userEvent.setup()
    const onJob = vi.fn()
    const savedJob = {
      ...job,
      classified: [
        { ...job.classified[0], custom_doc_type_name: '海运提单', doc_type_confirmed: true },
        job.classified[1],
      ],
    }
    patchFields.mockResolvedValue(savedJob)
    render(<FieldConfirmPage job={job} onJob={onJob} />)

    await user.click(screen.getByRole('button', { name: '字段对照' }))
    await user.click(screen.getByRole('button', { name: '展开字段编辑' }))
    const nameInput = screen.getByLabelText('当前文件具体名称')
    await user.type(nameInput, '海运提单')
    await user.click(screen.getByRole('button', { name: '保存本单' }))

    await waitFor(() => expect(patchFields).toHaveBeenCalledWith(
      job.job_id,
      expect.objectContaining({
        file_name: 'bill-of-lading.pdf',
        doc_type: 'other',
        custom_doc_type_name: '海运提单',
        doc_type_confirmed: true,
      }),
    ))
    expect(onJob).toHaveBeenCalledWith(savedJob)
  })

  it('moves the current PDF to unpack only after the auditor clicks the manual action', async () => {
    const user = userEvent.setup()
    const onJob = vi.fn()
    const onGo = vi.fn()
    const packetJob = {
      ...job,
      classified: [job.classified[1]],
      pending_files: [{
        file_name: 'bill-of-lading.pdf',
        doc_type: 'other',
        mixed_packet_declared: true,
      }],
      packet_run: { status: 'pending_analyze' as const, files: [], pages: [], warnings: [] },
      active_step: 'packet_unpack',
    }
    declareMixed.mockResolvedValue(packetJob)

    render(<FieldConfirmPage job={job} onJob={onJob} onGo={onGo} />)
    await user.click(screen.getByRole('button', { name: '转为混装资料包' }))

    await waitFor(() => {
      expect(declareMixed).toHaveBeenCalledWith(job.job_id, 'bill-of-lading.pdf')
      expect(onJob).toHaveBeenCalledWith(packetJob)
      expect(onGo).toHaveBeenCalledWith('packet_unpack')
    })
  })
})
