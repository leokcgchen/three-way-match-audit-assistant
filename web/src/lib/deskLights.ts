/** 红黄绿灯统一口径（与老师定义、后端 LIGHT_LEGEND 对齐）。 */

export const DESK_LIGHT_LEGEND = {
  green: '绿色：单据已识别、必要字段齐全且规则未发现异常，可以继续或已经通过',
  yellow: '黄色：分类、匹配关系或专业判断仍有疑问，需要审计师人工判断',
  red: '红色：缺少单据、关键字段缺失、无法归属或规则明确冲突，必须处理后才能继续',
  wait: '灰色：凭证尚未上传、识别或测试仍在进行，尚未形成判断结果',
} as const

export const DESK_LIGHT_LEGEND_INLINE =
  '绿色：可以继续或已经通过 · 黄色：需要审计师人工判断 · 红色：必须处理后才能继续 · 灰色：尚未形成判断结果'

export const DESK_LIGHT_LEGEND_TIP = [
  DESK_LIGHT_LEGEND.green,
  DESK_LIGHT_LEGEND.yellow,
  DESK_LIGHT_LEGEND.red,
  DESK_LIGHT_LEGEND.wait,
].join('；')

export type DeskProgress = {
  sample_total?: number
  done?: number
  docs_missing?: number
  fields_missing?: number
  match_exception?: number
  fail_confirmed?: number
  await_human?: number
  in_progress?: number
}

export function emptyDeskProgress(): Required<DeskProgress> {
  return {
    sample_total: 0,
    done: 0,
    docs_missing: 0,
    fields_missing: 0,
    match_exception: 0,
    fail_confirmed: 0,
    await_human: 0,
    in_progress: 0,
  }
}

/** 前端兜底：无后端 progress 时按 reason 互斥拆分。 */
export function progressFromRows(
  rows: Array<{ light?: string; reason?: string }>,
): Required<DeskProgress> {
  const out = emptyDeskProgress()
  out.sample_total = rows.length
  for (const row of rows) {
    const reason = String(row.reason || '')
    const light = String(row.light || 'wait')
    if (light === 'green' || reason === 'ok') out.done += 1
    else if (reason === 'fail_closed') {
      out.done += 1
      out.fail_confirmed += 1
    } else if (reason === 'wait_docs' || reason === 'missing_docs') out.docs_missing += 1
    else if (reason === 'fields_gap') out.fields_missing += 1
    else if (reason === 'docs_uncertain' || reason === 'amount_ambiguity') out.await_human += 1
    else if (reason === 'test_fail') out.match_exception += 1
    else if (reason === 'tests_pending' || light === 'wait') out.in_progress += 1
    else if (light === 'yellow') out.await_human += 1
    else if (light === 'red') out.match_exception += 1
    else out.in_progress += 1
  }
  return out
}
