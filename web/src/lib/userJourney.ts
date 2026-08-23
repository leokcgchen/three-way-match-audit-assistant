/** 给人看的主路径（与配方内部 step_id 分开，避免目标页再摊 Gate4/上传与识别）。 */

export const JOURNEY_STEPS = [
  '选底稿目标',
  '抽样清单',
  '上传凭证',
  '核对字段',
  '确认结论',
  '导出底稿',
] as const

const SKIP_CN: Record<string, string> = {
  contract_terms: '合同条款（本目标不强制）',
  amount_test: '金额测试（本目标不强制）',
  evidence_match: '证据匹配',
  relations_gate4: '勾稽',
  three_way_cutoff: '三单+截止',
  field_confirm: '人工核对',
  upload_ocr: '上传凭证',
  conclusion_gate5: '确认结论',
  workbook_export: '导出底稿',
}

export function journeyLine(goalIds: string[]): string {
  if (goalIds.includes('gospd01030') && goalIds.length === 1) {
    return '工作台中枢：抽样清单立笔 → 上传凭证 → 缺字段才核对 → 测试不通过才确认结论 → 清单收口后导出。'
  }
  return '工作台中枢：立样本 → 上传凭证 → 核对 → 测试 → 导出。'
}

export function skipStepLabel(stepId: string): string {
  return SKIP_CN[stepId] || stepId
}

export type JourneyMark = {
  goalsOk: boolean
  ledgerOk: boolean
  uploadOk: boolean
  fieldsOk: boolean
  matchOk: boolean
  testsOk: boolean
  conclusionOk: boolean
  exported: boolean
  needMatch?: boolean
  needConclusion?: boolean
  needExport?: boolean
}

/** 全站进度条同一套八步，避免半截改成「人工核对 / 一键审阅」。 */
export function journeyProgressPlan(m: JourneyMark): Array<{
  id: string
  label: string
  done: boolean
  current: boolean
  blocked?: boolean
}> {
  const needMatch = m.needMatch !== false
  const needConclusion = m.needConclusion !== false
  const needExport = m.needExport !== false
  const seq: Array<{ id: string; label: string; ok: boolean; show: boolean }> = [
    { id: 'goals', label: '选底稿目标', ok: m.goalsOk, show: true },
    { id: 'ledger', label: '抽样清单', ok: m.ledgerOk, show: true },
    { id: 'upload', label: '上传凭证', ok: m.uploadOk, show: true },
    { id: 'fields', label: '核对字段', ok: m.fieldsOk, show: true },
    { id: 'gate5', label: '确认结论', ok: m.conclusionOk, show: needConclusion },
    { id: 'export', label: '导出底稿', ok: m.exported, show: needExport },
  ]
  const visible = seq.filter((s) => s.show)
  const currentIdx = visible.findIndex((s) => !s.ok)
  return visible.map((s, i) => {
    const priorOk = visible.slice(0, i).every((p) => p.ok)
    return {
      id: s.id,
      label: s.label,
      done: s.ok,
      current: currentIdx === i,
      blocked: !s.ok && !priorOk ? true : undefined,
    }
  })
}
