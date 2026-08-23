import type { ClassifiedDoc } from '../types'

type FieldSlot = {
  status?: string
  accepted_value?: unknown
  normalized_candidate?: unknown
  raw_value?: unknown
  highlight_text?: unknown
}

function fieldMeta(doc: ClassifiedDoc): Record<string, FieldSlot> {
  const top = (doc as { _field_meta?: Record<string, FieldSlot> })._field_meta
  const nested = (doc.fields as { _field_meta?: Record<string, FieldSlot> } | undefined)?._field_meta
  return top || nested || {}
}

/** 与后端 effective_fields 对齐：ACCEPTED > candidate > fields */
export function effectiveFieldValue(doc: ClassifiedDoc | undefined, key: string): unknown {
  if (!doc) return undefined
  const meta = fieldMeta(doc)
  const slot = meta[key]
  if (slot && typeof slot === 'object') {
    if (slot.status === 'ACCEPTED' && slot.accepted_value != null) return slot.accepted_value
    if (slot.normalized_candidate != null) return slot.normalized_candidate
  }
  return (doc.fields || {})[key]
}

/** 原件高亮优先用 highlight_text / raw_value（金额采纳后 accepted 常是裸数字）。 */
export function highlightLocateValue(doc: ClassifiedDoc | undefined, key: string, fallback?: unknown): string {
  if (!doc) return fallback == null ? '' : String(fallback)
  const slot = fieldMeta(doc)[key]
  if (slot && typeof slot === 'object') {
    for (const k of ['highlight_text', 'raw_value'] as const) {
      const v = slot[k]
      if (v != null && String(v).trim()) return String(v).trim()
    }
  }
  if (fallback != null && String(fallback).trim()) return String(fallback).trim()
  const eff = effectiveFieldValue(doc, key)
  return eff == null ? '' : String(eff)
}

/** 与后端 rule_readable_fields 对齐：有三值 meta 则仅 ACCEPTED */
export function ruleReadableValue(doc: ClassifiedDoc | undefined, key: string): unknown {
  if (!doc) return undefined
  const meta = fieldMeta(doc)
  if (Object.keys(meta).length) {
    const slot = meta[key]
    if (slot?.status === 'ACCEPTED' && slot.accepted_value != null) return slot.accepted_value
    return undefined
  }
  return (doc.fields || {})[key]
}

export function fieldText(
  doc: ClassifiedDoc | undefined,
  key: string,
  mode: 'effective' | 'accepted' = 'effective',
): string {
  const v = mode === 'accepted' ? ruleReadableValue(doc, key) : effectiveFieldValue(doc, key)
  if (v == null) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v).trim()
}

export function effectiveFields(doc: ClassifiedDoc): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(doc.fields || {}) }
  const meta = fieldMeta(doc)
  for (const [key, slot] of Object.entries(meta)) {
    if (key.startsWith('_') || !slot) continue
    if (slot.status === 'ACCEPTED' && slot.accepted_value != null) {
      out[key] = slot.accepted_value
    } else if (out[key] == null && slot.normalized_candidate != null) {
      out[key] = slot.normalized_candidate
    }
  }
  return out
}
