import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const job = {
  job_id: 'job-nav',
  title: '导航测试',
  goal_ids: ['gospd01010'],
  plan: {
    goal_ids: ['gospd01010'],
    goals: [{ goal_id: 'gospd01010', label: '销售截止', description: '', workbook_sheets: [] }],
    required_steps: [],
    step_labels: [],
    required_dimensions: [],
    workbook_sheets: [],
    skipped_steps: [],
  },
  classified: [],
  fields_confirmed: false,
  active_step: 'sample_desk',
  sample_population: { business_ids: [], count: 0 },
  pending_files: [],
}

vi.mock('./api', () => ({
  api: {
    health: vi.fn(async () => ({ status: 'ok', phase: 'test' })),
    listJobs: vi.fn(async () => ({
      jobs: [{ job_id: 'job-nav', goal_ids: ['gospd01010'], doc_count: 0 }],
    })),
    getJob: vi.fn(async () => job),
    listChains: vi.fn(async () => ({ chains: [], lights: { green: 0, yellow: 0, red: 0, wait: 0 } })),
    setActiveStep: vi.fn(async () => job),
  },
}))

vi.mock('./pages/SampleWorkbenchPage', () => ({
  SampleWorkbenchPage: () => <div>工作台内容</div>,
}))
vi.mock('./pages/ConclusionPage', () => ({ ConclusionPage: () => <div>裁决内容</div> }))
vi.mock('./pages/WorkbookPage', () => ({ WorkbookPage: () => <div>导出内容</div> }))

import App from './App'

describe('primary navigation', () => {
  beforeEach(() => localStorage.clear())

  it('uses explicit review stages and restores the original advanced-tool names', async () => {
    const { container } = render(<App />)
    expect(screen.getByRole('button', { name: '收起左侧导航' })).toHaveClass('rail-toggle')
    const nav = await screen.findByRole('navigation', { name: '主导航' })
    expect(within(nav).getByText('选择底稿目标')).toBeInTheDocument()
    expect(within(nav).getByText('上传抽样清单')).toBeInTheDocument()
    expect(within(nav).getByText('上传凭证')).toBeInTheDocument()
    expect(within(nav).getByText('核对字段')).toBeInTheDocument()
    expect(within(nav).getByText('确认结论')).toBeInTheDocument()
    expect(within(nav).getByText('导出底稿')).toBeInTheDocument()
    expect(within(nav).getByText('待裁决')).toBeInTheDocument()
    expect(screen.getByText('高级工具')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '识难录' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提示词工程' })).toBeInTheDocument()
    expect(screen.queryByText('更多')).not.toBeInTheDocument()
    expect(screen.queryByText('审阅设置')).not.toBeInTheDocument()
    expect(container.querySelectorAll('.rail .idx')).toHaveLength(0)
  })
})
