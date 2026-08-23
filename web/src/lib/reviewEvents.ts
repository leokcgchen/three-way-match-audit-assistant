import type { ReviewEvent } from '../types'

const SEVERITY_ORDER: Record<ReviewEvent['severity'], number> = {
  BLOCKING: 0,
  REVIEW: 1,
  SAMPLE: 2,
}

function riskScore(event: ReviewEvent): number {
  const value = event.evidence.risk_score
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function sortReviewEvents(events: ReviewEvent[]): ReviewEvent[] {
  return [...events].sort((left, right) => {
    const severity = SEVERITY_ORDER[left.severity] - SEVERITY_ORDER[right.severity]
    if (severity !== 0) return severity
    const risk = riskScore(right) - riskScore(left)
    if (risk !== 0) return risk
    return left.source_ref.localeCompare(right.source_ref, 'zh-CN')
  })
}

export function eventPrimaryAction(event: ReviewEvent): string {
  switch (event.event_type) {
    case 'MISSING_DOCUMENT':
      return '上传补充资料'
    case 'PROVENANCE_GAP':
      return '查看原件'
    case 'RULE_CONFLICT':
    case 'AUDIT_TEST_FAILED':
      return '确认异常结论'
    case 'QUALITY_SAMPLE':
      return '开始抽检'
    default:
      return '查看并裁决'
  }
}

export function eventHumanReason(event: ReviewEvent): string {
  if (event.event_type === 'LEDGER_MISMATCH') {
    return `账载值 ${displayValue(event.ledger_value)}，单据值 ${displayValue(event.observed_value)}，请确认采用哪个结果。`
  }
  if (event.event_type === 'MISSING_DOCUMENT') {
    const missing = event.evidence.missing_doc_types
    if (Array.isArray(missing) && missing.length > 0) {
      return `这笔业务还缺少：${missing.map(String).join('、')}。补齐后系统会自动继续。`
    }
  }
  if (event.event_type === 'LOW_CONFIDENCE' && event.confidence !== null) {
    return `${event.reason}（AI 置信度 ${Math.round(event.confidence * 100)}%）`
  }
  return event.reason || event.title
}
