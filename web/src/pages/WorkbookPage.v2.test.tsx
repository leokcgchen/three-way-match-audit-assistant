import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Job } from '../types'

const { exportReadiness } = vi.hoisted(() => ({ exportReadiness: vi.fn() }))
vi.mock('../api', () => ({
  api: {
    exportReadiness,
    workbookPreview: vi.fn(),
    exportWorkbook: vi.fn(),
    workbookDownloadUrl: vi.fn(() => '/download'),
  },
}))
vi.mock('../components/ChainPicker', () => ({ ChainPicker: () => null }))

import { WorkbookPage } from './WorkbookPage'

const job = {
  job_id: 'job-export', title: '导出测试', goal_ids: [],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [], fields_confirmed: false, active_step: 'workbook_export',
} satisfies Job

describe('WorkbookPage V2', () => {
  it('shows only the blocker CTA while export is blocked', async () => {
    exportReadiness.mockResolvedValue({
      schema_version: '1.2', ready: false, summary: '还有 2 项', blocked_count: 2,
      stages: [{ id: 'review_events', label: '待裁决事项', status: 'NEEDS_REVIEW', blocking: true, reason: '待处理', action: { step: 'event_review', label: '处理异常' }, affected_groups: [] }],
    })
    render(<WorkbookPage job={job} onJob={vi.fn()} onGo={vi.fn()} />)
    expect(await screen.findByRole('button', { name: '处理 2 个阻断项' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '生成并下载底稿' })).not.toBeInTheDocument()
    expect(document.querySelectorAll('.btn.primary')).toHaveLength(1)
  })

  it('uses complete status explanations in export readiness', async () => {
    exportReadiness.mockResolvedValue({
      schema_version: '1.2', ready: true, summary: '可以导出', blocked_count: 0,
      stages: [],
      lights: {
        green: 1, yellow: 1, red: 1, wait: 1,
        progress: { done: 1, docs_missing: 1, fields_missing: 1, match_exception: 1, await_human: 1 },
      },
    })
    render(<WorkbookPage job={job} onJob={vi.fn()} onGo={vi.fn()} />)

    expect(await screen.findByText(/绿色：可以继续或已经通过/)).toBeInTheDocument()
    expect(screen.getByText(/黄色：需要审计师人工判断/)).toBeInTheDocument()
    expect(screen.getByText(/缺少凭证资料/)).toBeInTheDocument()
    expect(screen.getByText(/缺少关键字段/)).toBeInTheDocument()
    expect(screen.getByText(/等待人工判断/)).toBeInTheDocument()
    expect(screen.queryByText(/黄人裁|待人裁|资料缺|字段缺/)).not.toBeInTheDocument()
  })
})
