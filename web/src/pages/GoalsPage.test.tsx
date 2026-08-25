import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Job } from '../types'

vi.mock('../api', () => ({
  api: {
    listGoals: vi.fn(async () => ({
      goals: [{ goal_id: 'gospd01030', label: '销售截止', description: '', workbook_sheets: [] }],
    })),
    previewPlan: vi.fn(async () => null),
  },
}))

import { GoalsPage } from './GoalsPage'

const job = {
  job_id: 'goal-page',
  title: '目标页',
  goal_ids: ['gospd01030'],
  plan: {
    goal_ids: ['gospd01030'], goals: [], required_steps: [], step_labels: [],
    required_dimensions: [], workbook_sheets: [], skipped_steps: [],
  },
  classified: [],
  fields_confirmed: false,
  active_step: 'goals',
  pending_files: [],
  period_end: '2025-12-31',
} satisfies Job

describe('GoalsPage workflow handoff', () => {
  it('sends the auditor to sample-list upload after confirming the target', async () => {
    render(<GoalsPage job={job} onJob={vi.fn()} />)

    expect(await screen.findByRole('button', {
      name: '确认目标，并进入抽样清单上传',
    })).toBeEnabled()
    expect(screen.getByLabelText(/期间截止日/)).toHaveValue('2025-12-31')
    expect(screen.queryByText('工作台主路径：')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '当前阶段指引' })).not.toBeInTheDocument()
  })

  it('returns to the complete goal options when entering another review task', async () => {
    const { container, rerender } = render(<GoalsPage job={job} onJob={vi.fn()} />)
    await screen.findByRole('button', { name: /销售截止/ })
    const body = container.querySelector('.panel-body') as HTMLDivElement
    body.scrollTop = 480

    rerender(<GoalsPage job={{ ...job, job_id: 'goal-page-next' }} onJob={vi.fn()} />)

    expect(body.scrollTop).toBe(0)
    expect(screen.getByText('底稿目标选项')).toBeInTheDocument()
  })
})
