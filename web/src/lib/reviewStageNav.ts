import type { Job } from '../types'
import { journeyProgressPlan, type JourneyMark } from './userJourney'

export type ReviewStageState = 'done' | 'current' | 'available' | 'locked'

export type ReviewStageNavItem = {
  id: string
  step: string
  label: string
  state: ReviewStageState
}

const ROUTES: Record<string, string> = {
  goals: 'goals',
  ledger: 'sample_desk',
  upload: 'upload_ocr',
  fields: 'field_confirm',
  gate5: 'conclusion_gate5',
  export: 'workbook_export',
}

const LABELS: Record<string, string> = {
  goals: '选择底稿目标',
  ledger: '上传抽样清单',
  upload: '上传凭证',
  fields: '核对字段',
  gate5: '确认结论',
  export: '导出底稿',
}

function markFromJob(job: Job): JourneyMark {
  const ledgerOk = Number(job.sample_population?.count || 0) > 0 ||
    Boolean(job.sample_population?.business_ids?.length)
  const uploadOk = Boolean(job.classified?.length) && !job.pending_files?.length
  return {
    goalsOk: Boolean(job.goal_ids?.length),
    ledgerOk,
    uploadOk,
    fieldsOk: Boolean(job.fields_confirmed),
    matchOk: Boolean(job.matching_confirmed),
    testsOk: Boolean(job.amount_test || job.contract_terms || job.three_way || job.cutoff_test),
    conclusionOk: Boolean(job.conclusion_confirmed),
    exported: Boolean(job.workbook_path || job.workbook_paths?.length),
    needConclusion: true,
    needExport: true,
  }
}

export function buildReviewStageNav(job: Job, currentStep: string): ReviewStageNavItem[] {
  return journeyProgressPlan(markFromJob(job)).map((item) => {
    const step = ROUTES[item.id]
    let state: ReviewStageState
    if (item.blocked) state = 'locked'
    else if (step === currentStep) state = 'current'
    else if (item.done) state = 'done'
    else state = 'available'
    return { id: item.id, step, label: LABELS[item.id] || item.label, state }
  })
}
