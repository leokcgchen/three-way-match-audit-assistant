import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const job = {
  job_id: 'job-v2', title: 'V2 路由验收', goal_ids: ['gospd01030'],
  plan: {
    goal_ids: ['gospd01030'], goals: [], required_steps: [], step_labels: [],
    required_dimensions: [], workbook_sheets: [], skipped_steps: [],
  },
  classified: [{ file_name: 'invoice.pdf', doc_type: 'invoice' }],
  fields_confirmed: true, conclusion_confirmed: true, active_step: 'sample_desk',
  sample_population: { business_ids: ['SO25-0001'], count: 1 }, pending_files: [],
}

vi.mock('./api', () => ({
  api: {
    health: vi.fn(async () => ({ status: 'ok', phase: 'v2' })),
    listJobs: vi.fn(async () => ({ jobs: [{ job_id: 'job-v2', goal_ids: ['gospd01030'] }] })),
    getJob: vi.fn(async () => job),
    listChains: vi.fn(async () => ({
      chains: [], lights: { green: 0, yellow: 0, red: 0, wait: 1 },
    })),
    setActiveStep: vi.fn(async (_jobId: string, stepId: string) => ({ ...job, active_step: stepId })),
  },
}))

vi.mock('./pages/SampleWorkbenchPage', () => ({ SampleWorkbenchPage: () => <div>异常优先工作台</div> }))
vi.mock('./pages/EventReviewPage', () => ({ EventReviewPage: () => <div>精确事件裁决页</div> }))
vi.mock('./pages/WorkbookPage', () => ({ WorkbookPage: () => <div>单一导出操作页</div> }))

import App from './App'

describe('V2 simplified application journey', () => {
  beforeEach(() => localStorage.clear())

  it('uses navigable review stages plus the exact event queue and export page', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByText('异常优先工作台')).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: '主导航' })
    expect(within(nav).getAllByRole('button')).toHaveLength(8)
    expect(within(nav).getByText('选择底稿目标')).toBeInTheDocument()
    expect(within(nav).getByText('总工作台')).toBeInTheDocument()
    expect(within(nav).getByText('导出底稿')).toBeInTheDocument()
    expect(within(nav).queryByText('更多')).not.toBeInTheDocument()

    await user.click(within(nav).getByRole('button', { name: /待裁决/ }))
    expect(await screen.findByText('精确事件裁决页')).toBeInTheDocument()

    await user.click(within(nav).getByRole('button', { name: /导出底稿/ }))
    expect(await screen.findByText('单一导出操作页')).toBeInTheDocument()
  })
})
