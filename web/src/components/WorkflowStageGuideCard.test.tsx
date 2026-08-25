import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

import type { Job } from '../types'
import { WorkflowStageGuideCard } from './WorkflowStageGuideCard'

const job = {
  job_id: 'guide-test',
  title: '阶段指引测试',
  goal_ids: ['gospd01030'],
  plan: { goal_ids: [], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
  classified: [],
  fields_confirmed: false,
  active_step: 'goals',
  pending_files: [],
  sample_population: { business_ids: ['SO-1'], count: 1 },
} satisfies Job

afterEach(cleanup)

describe('WorkflowStageGuideCard', () => {
  it.each([
    ['goals', '选择底稿目标', '上传抽样清单'],
    ['sample_upload', '上传抽样清单', '总工作台'],
    ['sample_desk', '总工作台', '上传凭证'],
    ['upload_ocr', '上传凭证', '核对字段'],
    ['field_confirm', '核对字段', '确认结论'],
    ['conclusion_gate5', '确认结论', '导出底稿'],
    ['workbook_export', '导出底稿', '完成本轮审阅'],
  ])('uses page-specific guidance for %s', (step, current, next) => {
    render(<WorkflowStageGuideCard job={job} step={step} onGo={vi.fn()} />)

    const guide = screen.getByRole('region', { name: '当前阶段指引' })
    expect(guide).toHaveTextContent(`当前阶段：${current}`)
    expect(guide).toHaveTextContent(`下一步：${next}`)
  })
})
