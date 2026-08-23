import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Job } from '../types'

vi.mock('../api', () => ({
  api: {
    listReviewEvents: vi.fn(async () => ({
      events: [],
      summary: { open: 0, blocking: 0, missing: 0, review: 0, sample: 0, passed: 2 },
    })),
  },
}))

vi.mock('../lib/chainsCache', () => ({
  peekChainsCache: vi.fn(() => null),
  listChainsCached: vi.fn(async () => ({
    chains: [],
    lights: {
      green: 2,
      yellow: 1,
      red: 3,
      wait: 4,
      progress: { sample_total: 10, done: 2 },
    },
  })),
}))

vi.mock('../components/SampleDeskList', () => ({ SampleDeskList: () => <div /> }))

import { SampleWorkbenchPage } from './SampleWorkbenchPage'

const job: Job = {
  job_id: 'job-clarity',
  title: '清晰度测试',
  goal_ids: ['gospd01030'],
  plan: {
    goal_ids: ['gospd01030'],
    goals: [{ goal_id: 'gospd01030', label: '销售截止（期后）', description: '', workbook_sheets: [] }],
    required_steps: [],
    step_labels: [],
    required_dimensions: [],
    workbook_sheets: [],
    skipped_steps: [],
  },
  classified: [],
  fields_confirmed: false,
  active_step: 'sample_desk',
  sample_population: { business_ids: Array.from({ length: 10 }, (_, i) => `SO-${i + 1}`), count: 10 },
  pending_files: [],
}

describe('SampleWorkbenchPage clarity controls', () => {
  it('explains every status count and groups the header actions', async () => {
    const { container } = render(
      <SampleWorkbenchPage job={job} onJob={vi.fn()} onGo={vi.fn()} />,
    )

    expect(await screen.findByLabelText('绿色 2 笔')).toHaveAttribute(
      'data-tip',
      expect.stringContaining('单据齐全'),
    )
    expect(screen.getByLabelText('黄色 1 笔')).toHaveAttribute(
      'data-tip',
      expect.stringContaining('人工判断'),
    )
    expect(screen.getByLabelText('红色 3 笔')).toHaveAttribute(
      'data-tip',
      expect.stringContaining('必须处理'),
    )
    expect(screen.getByLabelText('灰色 4 笔')).toHaveAttribute(
      'data-tip',
      expect.stringContaining('尚未出灯'),
    )
    expect(screen.queryByText(/黄人裁/)).not.toBeInTheDocument()

    const actions = container.querySelector('.desk-head-actions')
    expect(actions).toContainElement(screen.getByRole('button', { name: '上传混装资料包' }))
    expect(actions).toHaveTextContent('更换抽样清单')
  })
})
