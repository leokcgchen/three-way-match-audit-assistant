import { describe, expect, it } from 'vitest'

import type { ChainInfo } from '../api'
import type { Job } from '../types'
import { deriveWorkflowStageGuide } from './workflowStageGuide'

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: 'guide-job', title: '流程指引', goal_ids: ['gospd01030'],
    plan: { goal_ids: ['gospd01030'], goals: [], required_steps: [], step_labels: [], required_dimensions: [], workbook_sheets: [], skipped_steps: [] },
    classified: [], fields_confirmed: false, active_step: 'sample_desk', pending_files: [],
    sample_population: { business_ids: ['SO-1'], count: 1 },
    ...overrides,
  }
}

describe('deriveWorkflowStageGuide', () => {
  it('directs missing-document businesses to the voucher upload tab', () => {
    const rows: ChainInfo[] = [{ chain_id: 'SO-1', doc_count: 0, light: 'red', reason: 'missing_docs', label: '缺凭证' }]
    expect(deriveWorkflowStageGuide(makeJob(), rows)).toMatchObject({
      stageLabel: '上传凭证',
      ctaLabel: '继续上传凭证',
      targetStep: 'upload_ocr:upload',
    })
  })

  it('moves recognized work to field confirmation', () => {
    const job = makeJob({ classified: [{ file_name: 'invoice.pdf', doc_type: 'invoice' }] })
    expect(deriveWorkflowStageGuide(job, [])).toMatchObject({
      stageLabel: '核对字段',
      targetStep: 'field_confirm',
    })
  })

  it('uses the registered conclusion route after fields are confirmed', () => {
    const job = makeJob({
      classified: [{ file_name: 'invoice.pdf', doc_type: 'invoice' }],
      fields_confirmed: true,
      conclusion_confirmed: false,
    })
    expect(deriveWorkflowStageGuide(job, [])).toMatchObject({
      stageLabel: '确认结论',
      targetStep: 'conclusion_gate5',
    })
  })

  it('uses the registered workbook export route after conclusion confirmation', () => {
    const job = makeJob({
      classified: [{ file_name: 'invoice.pdf', doc_type: 'invoice' }],
      fields_confirmed: true,
      conclusion_confirmed: true,
    })
    expect(deriveWorkflowStageGuide(job, [])).toMatchObject({
      stageLabel: '导出底稿',
      targetStep: 'workbook_export',
    })
  })
})
