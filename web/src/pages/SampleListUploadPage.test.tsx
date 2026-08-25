import { render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import type { Job } from '../types'
import { SampleListUploadPage } from './SampleListUploadPage'

vi.mock('../api', () => ({
  api: {
    importSampleExcel: vi.fn(),
  },
}))

const readyJob: Job = {
  job_id: 'sample-list-ready',
  title: '抽样清单已就绪',
  goal_ids: ['gospd01030'],
  plan: {
    goal_ids: ['gospd01030'],
    goals: [],
    required_steps: [],
    step_labels: [],
    required_dimensions: [],
    workbook_sheets: [],
    skipped_steps: [],
  },
  classified: [],
  fields_confirmed: false,
  active_step: 'sample_upload',
  pending_files: [],
  sample_population: { business_ids: ['SO-1'], count: 1 },
}

it('keeps sample-list actions on this page without duplicating the global workflow guide', () => {
  render(<SampleListUploadPage job={readyJob} onJob={vi.fn()} onGo={vi.fn()} />)

  expect(screen.queryByRole('region', { name: '下一步' })).not.toBeInTheDocument()
  const card = screen.getByRole('region', { name: '上传抽样清单' })
  expect(within(card).getByRole('heading', { name: '上传抽样清单' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '更换抽样清单' })).not.toHaveClass('primary')
})

it('does not show next-step guidance before a sample list is uploaded', () => {
  render(
    <SampleListUploadPage
      job={{ ...readyJob, sample_population: { business_ids: [], count: 0 } }}
      onJob={vi.fn()}
      onGo={vi.fn()}
    />,
  )

  expect(screen.queryByRole('region', { name: '下一步' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '上传抽样清单' })).toHaveClass('primary')
  expect(screen.queryByRole('button', { name: '更换抽样清单' })).not.toBeInTheDocument()
})
