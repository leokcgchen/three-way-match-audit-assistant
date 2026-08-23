/** 统一读取测试结果状态：overall_status 优先于 match_result（避免截止 FAIL 被三单 PASS 盖住）。 */

export function resultOverallStatus(result: unknown): string {
  if (!result || typeof result !== 'object') return ''
  const o = result as Record<string, unknown>
  const match = (o.match_result as Record<string, unknown> | undefined) || undefined
  const cutoff = (o.cutoff_result as Record<string, unknown> | undefined) || undefined
  const top = o.overall_status ?? o.status
  if (top != null && String(top).trim()) return String(top)
  if (match?.overall_status != null && String(match.overall_status).trim()) {
    return String(match.overall_status)
  }
  if (match?.status != null && String(match.status).trim()) return String(match.status)
  // 仅截止结果对象时
  const c =
    cutoff?.['测试状态'] ?? cutoff?.status ?? cutoff?.overall_status ?? o.cutoff_test_status
  if (c != null && String(c).trim()) return String(c)
  return ''
}

/** 仅三单匹配状态（禁止用综合 overall 冒充三单结论）。 */
export function threeWayMatchStatus(result: unknown): string {
  if (!result || typeof result !== 'object') return ''
  const o = result as Record<string, unknown>
  const dedicated = o.three_way_status ?? o.match_status
  if (dedicated != null && String(dedicated).trim()) return String(dedicated)
  const match = (o.match_result as Record<string, unknown> | undefined) || undefined
  if (match) {
    const s = match.overall_status ?? match.status
    if (s != null && String(s).trim()) return String(s)
  }
  if (o.cutoff_result || o.cutoff_test_status || o.cutoff_status) {
    return ''
  }
  return resultOverallStatus(result)
}

/** 工作台卡片：三单与截止分开展示。 */
export function threeWayCutoffCardStatus(args: {
  threeWay?: unknown
  threeWayMatch?: unknown
  cutoffTest?: unknown
}): string {
  const tw = args.threeWayMatch || args.threeWay
  const cu = args.cutoffTest
  const a = tw
    ? threeWayMatchStatus(tw) || resultOverallStatus(tw) || '已跑'
    : '未跑'
  const b = cu
    ? resultOverallStatus(cu) || cutoffStatus(cu) || '已跑'
    : tw
      ? cutoffStatus(tw) || '未跑'
      : '未跑'
  if (a === '未跑' && b === '未跑') return '未跑'
  return `三单 ${a} · 截止 ${b}`
}

export function cutoffStatus(result: unknown): string {
  if (!result || typeof result !== 'object') return ''
  const o = result as Record<string, unknown>
  const cutoff = (o.cutoff_result as Record<string, unknown> | undefined) || {}
  const c =
    o.cutoff_status ??
    o.cutoff_test_status ??
    cutoff['测试状态'] ??
    cutoff.status ??
    cutoff.overall_status ??
    ''
  return c != null ? String(c) : ''
}

export function cutoffSummary(result: unknown): string {
  if (!result || typeof result !== 'object') return ''
  const o = result as Record<string, unknown>
  const cutoff = (o.cutoff_result as Record<string, unknown> | undefined) || {}
  const expected = cutoff['应确认日期'] ?? cutoff.expected_revenue_date
  const deviation =
    cutoff['偏差天数'] != null ? cutoff['偏差天数'] : cutoff.deviation_days
  const issue = cutoff['问题描述'] ?? cutoff.message ?? cutoff.summary ?? ''
  const parts: string[] = []
  if (expected) parts.push(`应确认 ${expected}`)
  if (deviation != null && deviation !== '') parts.push(`偏差 ${deviation} 天`)
  if (issue) parts.push(String(issue))
  return parts.join(' · ')
}
