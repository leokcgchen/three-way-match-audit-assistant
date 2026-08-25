import { describe, expect, it } from 'vitest'
import type { Job } from '../types'
import { buildReviewStageNav } from './reviewStageNav'

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: 'job-stage-nav',
    title: '阶段导航测试',
    goal_ids: [],
    plan: {
      goal_ids: [], goals: [], required_steps: [], step_labels: [],
      required_dimensions: [], workbook_sheets: [], skipped_steps: [],
    },
    classified: [],
    fields_confirmed: false,
    active_step: 'goals',
    sample_population: { business_ids: [], count: 0 },
    pending_files: [],
    ...overrides,
  }
}

describe('buildReviewStageNav', () => {
  it('keeps later stages locked before a target is selected', () => {
    expect(buildReviewStageNav(makeJob(), 'goals')).toEqual([
      expect.objectContaining({ label: '选择底稿目标', state: 'current' }),
      expect.objectContaining({ label: '上传抽样清单', state: 'locked' }),
      expect.objectContaining({ label: '总工作台', state: 'locked' }),
      expect.objectContaining({ label: '上传凭证', state: 'locked' }),
      expect.objectContaining({ label: '核对字段', state: 'locked' }),
      expect.objectContaining({ label: '确认结论', state: 'locked' }),
      expect.objectContaining({ label: '导出底稿', state: 'locked' }),
    ])
  })

  it('marks prior stages done, the selected stage current and the next stage available', () => {
    const job = makeJob({
      goal_ids: ['gospd01030'],
      sample_population: { business_ids: ['SO25-0281'], count: 1 },
      classified: [{ file_name: 'invoice.pdf', doc_type: 'invoice' }],
    })
    expect(buildReviewStageNav(job, 'field_confirm').map(({ label, state }) => ({ label, state })))
      .toEqual([
        { label: '选择底稿目标', state: 'done' },
        { label: '上传抽样清单', state: 'done' },
        { label: '总工作台', state: 'available' },
        { label: '上传凭证', state: 'done' },
        { label: '核对字段', state: 'current' },
        { label: '确认结论', state: 'locked' },
        { label: '导出底稿', state: 'locked' },
      ])
  })

  it('does not unlock field review while uploaded files are still waiting for recognition', () => {
    const job = makeJob({
      goal_ids: ['gospd01030'],
      sample_population: { business_ids: ['SO25-0281'], count: 1 },
      pending_files: [{ file_name: 'packet.pdf' }],
    })
    const stages = buildReviewStageNav(job, 'upload_ocr')
    expect(stages.find((item) => item.id === 'upload')?.state).toBe('current')
    expect(stages.find((item) => item.id === 'fields')?.state).toBe('locked')
  })
})
