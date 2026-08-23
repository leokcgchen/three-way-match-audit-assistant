/** 三单结论卡：决策四态 / HOLD 分因（结论页用；工作台不展示）。 */

export type ThreeWayDecisionView = {
  decision?: string | null
  decision_reasons?: string[] | null
  hold_reason_code?: string | null
  quantity_roles?: Record<string, unknown> | null
  slot_reasons?: Record<string, string> | null
  erp_review?: { status?: string; note?: string } | null
  status?: string | null
}

const DECISION_LABEL: Record<string, string> = {
  AUTO_PASS: '通过',
  HOLD_REVIEW: '待复核',
  PASS_WITH_WARNING: '带预警通过',
  NOT_APPLICABLE: '不适用',
}

const HOLD_LABEL: Record<string, string> = {
  PAPER_FIELD: '纸面字段对不上',
  AMBIGUOUS_BINDING: '绑定不唯一',
  DOCUMENT_MISSING: '缺单据',
  AWAITING_ERP: '缺公司 ERP 状态',
}

export function decisionLabel(decision?: string | null): string {
  const key = String(decision || '').toUpperCase()
  return DECISION_LABEL[key] || (decision ? String(decision) : '—')
}

export function holdReasonLabel(code?: string | null): string {
  const key = String(code || '').toUpperCase()
  return HOLD_LABEL[key] || ''
}

export function decisionTone(decision?: string | null): 'ok' | 'warn' | 'err' | 'muted' {
  const key = String(decision || '').toUpperCase()
  if (key === 'AUTO_PASS') return 'ok'
  // 待复核 / 带预警：黄灯口径（需人裁决），不用红
  if (key === 'PASS_WITH_WARNING' || key === 'HOLD_REVIEW') return 'warn'
  if (key === 'NOT_APPLICABLE') return 'muted'
  return 'muted'
}

export function quantityRolesLine(roles?: Record<string, unknown> | null): string {
  if (!roles) return ''
  const o = roles.ordered_qty ?? roles.ordered
  const r = roles.received_qty ?? roles.received
  const i = roles.invoiced_qty ?? roles.invoiced
  if (o == null && r == null && i == null) return ''
  return `订单数量 ${o ?? '—'}，签收/验收数量 ${r ?? '—'}，发票开票数量 ${i ?? '—'}`
}

/** 去掉旧版「得分/匹配得分」展示残留（引擎已不以得分放行）。 */
export function stripMatchScoreLanguage(text: string): string {
  if (!text) return text
  let s = text
  s = s.replace(/[，,]?\s*(?:三单)?匹配得分\s*[:：]?\s*\d+(?:\.\d+)?\s*分?/g, '')
  s = s.replace(/[，,]?\s*得分\s*[:：]?\s*\d+(?:\.\d+)?\s*分?/g, '')
  s = s.replace(/（得分\s*\d+(?:\.\d+)?\s*分?）/g, '')
  s = s.replace(/\(\s*得分\s*\d+(?:\.\d+)?\s*\)/g, '')
  s = s.replace(/三单匹配通过，\s*/g, '三单匹配通过：')
  s = s.replace(/三单匹配失败，\s*/g, '三单匹配失败：')
  s = s.replace(/三单匹配需关注，\s*/g, '三单匹配需关注：')
  s = s.replace(/[，,]{2,}/g, '，')
  s = s.replace(/：\s*：/g, '：')
  s = s.replace(/\s{2,}/g, ' ')
  return s.replace(/^[，,\s;；]+|[，,\s;；]+$/g, '')
}

/** 把历史结果里残留的「订/收/开」扩成完整中文（结论页展示兜底）。 */
export function expandThreeWayShorthand(text: string): string {
  if (!text) return text
  let s = stripMatchScoreLanguage(text)
  s = s.replace(/数量（订\/收\/开分槽）/g, '数量（订单数量、签收/验收数量、发票开票数量，分角色对照）')
  s = s.replace(/数量（订\/收\/开）/g, '数量（订单数量、签收/验收数量、发票开票数量）')
  s = s.replace(/订\/收\/开分槽/g, '订单/签收验收/发票开票分角色')
  s = s.replace(/订\/收\/开三方/g, '订单、签收/验收、发票三方')
  s = s.replace(/订\/收\/开/g, '订单、签收/验收、发票开票')
  s = s.replace(
    /订\s*([\d.]+)\s*\/\s*收\s*([\d.]+)\s*\/\s*开\s*([\d.]+)/g,
    '订单数量 $1，签收/验收数量 $2，发票开票数量 $3',
  )
  s = s.replace(
    /数量订\s*([\d.]+)\s*\/\s*收\s*([\d.]+)\s*\/\s*开\s*([\d.]+)/g,
    '数量：订单 $1，签收/验收 $2，发票开票 $3',
  )
  s = s.replace(
    /订\s+([\d.—\-]+)\s*vs\s*收\s+([\d.—\-]+)\s*vs\s*开\s+([\d.—\-]+)/g,
    '订单数量 $1 vs 签收/验收数量 $2 vs 发票开票数量 $3',
  )
  s = s.replace(/(^|[^\u4e00-\u9fff])订\s+([\d.—\-]+)/g, '$1订单数量 $2')
  s = s.replace(/(^|[^\u4e00-\u9fff])收\s+([\d.—\-]+)/g, '$1签收/验收数量 $2')
  s = s.replace(/(^|[^\u4e00-\u9fff])开\s+([\d.—\-]+)/g, '$1发票开票数量 $2')
  return s
}

export function pickThreeWayDecision(
  sample: Record<string, unknown> | null | undefined,
  job?: Record<string, unknown> | null,
): ThreeWayDecisionView | null {
  const tw = (sample?.three_way ||
    sample?.three_way_match ||
    job?.three_way ||
    job?.three_way_match) as Record<string, unknown> | null | undefined
  if (!tw || typeof tw !== 'object') return null
  const match = (tw.match_result as Record<string, unknown> | undefined) || {}
  const decision = (tw.decision as string) || (match.decision as string) || ''
  if (!decision && !tw.hold_reason_code && !match.decision) {
    const status = String(tw.three_way_status || tw.status || match.overall_status || '')
    if (!status) return null
    return {
      decision:
        status === 'PASS'
          ? 'AUTO_PASS'
          : status === 'WARNING'
            ? 'PASS_WITH_WARNING'
            : status === 'FAIL'
              ? 'HOLD_REVIEW'
              : status,
      decision_reasons: [],
      hold_reason_code: null,
      quantity_roles: (tw.quantity_roles || match.quantity_roles || {}) as Record<string, unknown>,
      slot_reasons: (tw.slot_reasons || match.slot_reasons || {}) as Record<string, string>,
      erp_review: (tw.erp_review || match.erp_review || null) as ThreeWayDecisionView['erp_review'],
      status,
    }
  }
  return {
    decision,
    decision_reasons: (tw.decision_reasons || match.decision_reasons || []) as string[],
    hold_reason_code: (tw.hold_reason_code || match.hold_reason_code || null) as string | null,
    quantity_roles: (tw.quantity_roles || match.quantity_roles || {}) as Record<string, unknown>,
    slot_reasons: (tw.slot_reasons || match.slot_reasons || {}) as Record<string, string>,
    erp_review: (tw.erp_review || match.erp_review || null) as ThreeWayDecisionView['erp_review'],
    status: String(tw.three_way_status || tw.status || match.overall_status || ''),
  }
}
