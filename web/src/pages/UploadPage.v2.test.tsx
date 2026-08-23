import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Job } from '../types'

vi.mock('../api', () => ({
  api: { ocrStatus: vi.fn(async () => ({ configured: true })) },
}))
vi.mock('../components/ChainPicker', () => ({ ChainPicker: () => null }))
vi.mock('../components/PendingChainPreview', () => ({ PendingChainPreview: () => null }))

import { UploadPage } from './UploadPage'

const packetJob = {
  job_id: 'packet-upload', title: '混装上传', goal_ids: [],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [], fields_confirmed: false, active_step: 'upload_ocr',
  pending_files: [{
    file_name: '客户资料包.pdf', path: 'packet.pdf', size: 1024, doc_type: 'other',
    packet_kind: 'packet_multi_chain',
  }],
} satisfies Job

describe('UploadPage V2', () => {
  it('shows only unpack as the primary action while a mixed packet is waiting', () => {
    const { container } = render(
      <UploadPage job={packetJob} onJob={vi.fn()} onGo={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: '去拆包分笔' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /开始处理/ })).not.toBeInTheDocument()
    expect(container.querySelectorAll('.btn.primary')).toHaveLength(1)
  })
})
