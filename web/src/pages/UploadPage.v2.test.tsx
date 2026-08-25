import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job } from '../types'

vi.mock('../api', () => ({
  api: {
    ocrStatus: vi.fn(async () => ({ configured: true })),
    listChains: vi.fn(async () => ({ chains: [{ chain_id: 'SO-1', doc_count: 0, reason: 'missing_docs' }] })),
    setChainCompleteSet: vi.fn(),
    deleteScopeException: vi.fn(),
  },
}))
vi.mock('../components/ChainPicker', () => ({ ChainPicker: () => null }))
vi.mock('../components/PendingChainPreview', () => ({ PendingChainPreview: () => null }))

import { UploadPage } from './UploadPage'
import { api } from '../api'

const packetJob = {
  job_id: 'packet-upload', title: '混装上传', goal_ids: [],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [], fields_confirmed: false, active_step: 'upload_ocr',
  pending_files: [{
    file_name: '客户资料包.pdf', path: 'packet.pdf', size: 1024, doc_type: 'other',
    packet_kind: 'packet_multi_chain',
    mixed_packet_declared: true,
  }],
} satisfies Job

describe('UploadPage V2', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.listChains).mockResolvedValue({
      chains: [{ chain_id: 'SO-1', doc_count: 0, reason: 'missing_docs', complete_set: false }],
    })
    vi.mocked(api.setChainCompleteSet).mockResolvedValue(packetJob)
  })

  it('shows mixed-package upload only after the auditor opts in', async () => {
    const job = { ...packetJob, pending_files: [], sample_population: { count: 1, business_ids: ['SO-1'] } }
    const user = userEvent.setup()
    render(<UploadPage job={job} onJob={vi.fn()} initialTab="upload" />)
    expect(screen.queryByRole('button', { name: '上传混装资料包' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: '存在混装资料包' }))
    expect(screen.getByRole('button', { name: '上传混装资料包' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '演示数据（非正式 OCR）' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '强制重识别' })).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '待处理业务（待上传或待补充凭证）' })).toBeInTheDocument()
  })

  it('shows one dynamic OCR action', () => {
    const firstRun = {
      ...packetJob,
      pending_files: [{
        ...packetJob.pending_files![0],
        packet_kind: undefined,
        mixed_packet_declared: false,
      }],
    }
    const { rerender } = render(<UploadPage job={firstRun} onJob={vi.fn()} initialTab="pending" />)
    expect(screen.getByRole('button', { name: /开始处理/ })).toBeEnabled()
    rerender(<UploadPage job={{ ...firstRun, ocr_has_run: true, pending_files: [], classified: [{ file_name: 'invoice.pdf', doc_type: 'invoice' }] }} onJob={vi.fn()} initialTab="done" />)
    expect(screen.getByRole('button', { name: '重新识别' })).toBeDisabled()
  })

  it('shows only unpack as the primary action while a mixed packet is waiting', () => {
    const { container } = render(
      <UploadPage job={packetJob} onJob={vi.fn()} onGo={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: '去拆包分笔' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /开始处理/ })).not.toBeInTheDocument()
    expect(container.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('keeps OCR available for legacy multi-page files that were never manually declared mixed', async () => {
    const onProcess = vi.fn(async () => undefined)
    const user = userEvent.setup()
    const pendingFiles = Array.from({ length: 33 }, (_, index) => ({
      file_name: `YW-${index + 1}.pdf`,
      path: `YW-${index + 1}.pdf`,
      size: 1024,
      doc_type: index % 3 === 0 ? 'order' : 'invoice',
      packet_kind: index < 11 ? 'packet_single_chain' : 'standard',
      page_count: index < 11 ? 2 : 1,
    }))
    const legacyJob = {
      ...packetJob,
      pending_files: pendingFiles,
      packet_run: { status: 'needs_review' as const, files: [], pages: [], warnings: [] },
    }

    render(<UploadPage job={legacyJob} onJob={vi.fn()} onProcess={onProcess} initialTab="pending" />)
    const button = screen.getByRole('button', { name: '开始处理（33）' })
    expect(button).toBeEnabled()
    expect(screen.queryByRole('button', { name: '去拆包分笔' })).not.toBeInTheDocument()
    await user.click(button)
    expect(onProcess).toHaveBeenCalledWith(false, pendingFiles.map((row) => row.file_name))
  })

  it('explains uncertain file types without offering unpack', () => {
    const uncertainJob = {
      ...packetJob,
      pending_files: [{
        file_name: 'foreign-bill-of-lading.pdf',
        path: 'foreign-bill-of-lading.pdf',
        size: 1024,
        doc_type: 'other',
        light_confident: false,
        type_uncertain: true,
        mixed_packet_declared: false,
      }],
    }

    render(<UploadPage job={uncertainJob} onJob={vi.fn()} initialTab="pending" />)

    expect(screen.getByText('文件类型不确定，疑似内部存在杂乱的文件类型')).toBeInTheDocument()
    expect(screen.getByText('识别后请到“核对字段”逐页查看，并为当前文件确认一个固定名称。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始处理（1）' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '去拆包分笔' })).not.toBeInTheDocument()
  })

  it('opens an exception review and keeps a persistent exception area', () => {
    const job = {
      ...packetJob,
      pending_files: [],
      scope_exceptions: [{
        exception_id: 'scope-outside',
        file_name: 'SO25-9999.pdf',
        scope_status: 'OUT_OF_SAMPLE' as const,
        detected_business_ids: ['SO25-9999'],
        reason: '业务号不在当前抽样清单中。',
        recommended_action: 'delete' as const,
      }],
    }

    render(<UploadPage job={job} onJob={vi.fn()} initialTab="done" />)

    expect(screen.getByRole('dialog', { name: '发现非抽样清单材料' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '异常区（1）' })).toBeInTheDocument()
    expect(screen.getAllByText('SO25-9999.pdf').length).toBeGreaterThan(0)
  })

  it('deletes only after the auditor confirms the recommended action', async () => {
    const onJob = vi.fn()
    const user = userEvent.setup()
    const exception = {
      exception_id: 'scope-outside-delete',
      file_name: 'SO25-9999.pdf',
      scope_status: 'OUT_OF_SAMPLE' as const,
      detected_business_ids: ['SO25-9999'],
      reason: '业务号不在当前抽样清单中。',
      recommended_action: 'delete' as const,
    }
    const job = { ...packetJob, pending_files: [], scope_exceptions: [exception] }
    vi.mocked(api.deleteScopeException).mockResolvedValue({ ...job, scope_exceptions: [] })
    render(<UploadPage job={job} onJob={onJob} initialTab="done" />)

    expect(api.deleteScopeException).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '删除该文件（推荐）' }))

    await waitFor(() => {
      expect(api.deleteScopeException).toHaveBeenCalledWith(job.job_id, exception.exception_id)
      expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ scope_exceptions: [] }))
    })
  })

  it('opens the dialog again when the same deleted file is uploaded again', async () => {
    const user = userEvent.setup()
    const exception = {
      exception_id: 'scope-repeat',
      file_name: 'SO25-9999.pdf',
      scope_status: 'OUT_OF_SAMPLE' as const,
      detected_business_ids: ['SO25-9999'],
      reason: '业务号不在当前抽样清单中。',
      recommended_action: 'delete' as const,
    }
    const base = { ...packetJob, pending_files: [] }
    const { rerender } = render(
      <UploadPage job={{ ...base, scope_exceptions: [exception] }} onJob={vi.fn()} initialTab="done" />,
    )
    await user.click(screen.getByRole('button', { name: '暂不删除，留在异常区' }))
    expect(screen.queryByRole('dialog', { name: '发现非抽样清单材料' })).not.toBeInTheDocument()

    rerender(<UploadPage job={{ ...base, scope_exceptions: [] }} onJob={vi.fn()} initialTab="done" />)
    rerender(
      <UploadPage job={{ ...base, scope_exceptions: [exception] }} onJob={vi.fn()} initialTab="done" />,
    )

    expect(screen.getByRole('dialog', { name: '发现非抽样清单材料' })).toBeInTheDocument()
  })

  it('saves a complete-set decision and keeps the row checked', async () => {
    const user = userEvent.setup()
    const onJob = vi.fn()
    const updated = { ...packetJob, updated_at: '2026-08-24T10:00:00' }
    vi.mocked(api.setChainCompleteSet).mockResolvedValueOnce(updated)

    render(<UploadPage job={{ ...packetJob, pending_files: [] }} onJob={onJob} initialTab="upload" />)
    const checkbox = await screen.findByRole('checkbox', { name: '本笔已齐套：SO-1' })
    await user.click(checkbox)

    await waitFor(() => {
      expect(api.setChainCompleteSet).toHaveBeenCalledWith(packetJob.job_id, 'SO-1', true)
      expect(onJob).toHaveBeenCalledWith(updated)
      expect(checkbox).toBeChecked()
    })
  })

  it('rolls the complete-set decision back when saving fails', async () => {
    const user = userEvent.setup()
    vi.mocked(api.setChainCompleteSet).mockRejectedValueOnce(new Error('保存齐套状态失败'))

    render(<UploadPage job={{ ...packetJob, pending_files: [] }} onJob={vi.fn()} initialTab="upload" />)
    const checkbox = await screen.findByRole('checkbox', { name: '本笔已齐套：SO-1' })
    await user.click(checkbox)

    await waitFor(() => {
      expect(checkbox).not.toBeChecked()
      expect(screen.getByRole('alert')).toHaveTextContent('保存齐套状态失败')
    })
  })
})
