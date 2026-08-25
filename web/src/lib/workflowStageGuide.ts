import type { ChainInfo } from '../api'
import type { Job } from '../types'

export type WorkflowStageGuide = {
  stageLabel: string
  task: string
  detail: string
  ctaLabel: string
  targetStep: string
}

export function deriveWorkflowStageGuide(job: Job, rows: ChainInfo[]): WorkflowStageGuide {
  if (!(job.goal_ids || []).length) {
    return {
      stageLabel: '选择底稿目标',
      task: '先确认本次审阅需要生成的底稿。',
      detail: '期间截止日也只在选择底稿目标页面填写。',
      ctaLabel: '选择底稿目标',
      targetStep: 'goals',
    }
  }

  const population = job.sample_population
  const sampleCount = Number(population?.count || population?.business_ids?.length || 0)
  if (sampleCount === 0) {
    return {
      stageLabel: '上传抽样清单',
      task: '导入并核对本次审阅的抽样业务。',
      detail: '抽样清单确认后，系统会据此建立业务处理队列。',
      ctaLabel: '上传抽样清单',
      targetStep: 'sample_upload',
    }
  }

  const hasMissingDocuments = rows.some(
    (row) =>
      row.doc_count === 0 ||
      row.reason === 'missing_docs' ||
      row.reason === 'wait_docs' ||
      Boolean(row.missing_doc_types?.length) ||
      Boolean(row.missing_doc_labels?.length),
  )
  const hasAnyDocument = rows.some((row) => Number(row.doc_count || 0) > 0)
  if (hasMissingDocuments || (!hasAnyDocument && (job.classified || []).length === 0)) {
    return {
      stageLabel: '上传凭证',
      task: '当前应为抽样业务补充凭证资料。',
      detail: '下一步请进入上传模块，按抽样清单中的业务上传凭证或混装资料包。',
      ctaLabel: '继续上传凭证',
      targetStep: 'upload_ocr:upload',
    }
  }

  if (job.ocr_processing || (job.pending_files || []).length > 0) {
    return {
      stageLabel: '上传凭证',
      task: '凭证已经进入识别队列。',
      detail: '请在待处理模块查看进度，识别完成后再核对字段。',
      ctaLabel: '查看待处理',
      targetStep: 'upload_ocr:pending',
    }
  }

  if ((job.classified || []).length === 0) {
    return {
      stageLabel: '上传凭证',
      task: '当前应上传并识别凭证。',
      detail: '下一步请进入上传模块。',
      ctaLabel: '继续上传凭证',
      targetStep: 'upload_ocr:upload',
    }
  }

  if (!job.fields_confirmed) {
    return {
      stageLabel: '核对字段',
      task: '识别已完成，请核对关键字段。',
      detail: '仅在展开字段编辑且实际修改后才需要保存本单。',
      ctaLabel: '核对字段',
      targetStep: 'field_confirm',
    }
  }

  if (!job.conclusion_confirmed) {
    return {
      stageLabel: '确认结论',
      task: '字段已经确认，请复核并确认审阅结论。',
      detail: '确认结论后即可进入底稿导出。',
      ctaLabel: '确认结论',
      targetStep: 'conclusion_gate5',
    }
  }

  return {
    stageLabel: '导出底稿',
    task: '审阅流程已完成，可以生成并导出底稿。',
    detail: '导出前请最后确认底稿目标和审阅结论。',
    ctaLabel: '导出底稿',
    targetStep: 'workbook_export',
  }
}
