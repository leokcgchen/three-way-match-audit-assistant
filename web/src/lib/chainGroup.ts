/** 与后端 group_classified_by_chain 对齐：SO+HT 合并为一笔，避免幽灵链。 */

import type { ClassifiedDoc, Job } from '../types'

const STRONG_RE = /\b((?:SO|PO|KJHT|EXHT|HT|CT)\d{2}-\d{4})\b/gi

function normBiz(value: unknown): string {
  return String(value || '')
    .trim()
    .toUpperCase()
    .replace(/\s+/g, '')
}

function isStrongBiz(key: string): boolean {
  const u = (key || '').toUpperCase()
  if (!u) return false
  if (u.startsWith('SO') || u.startsWith('PO')) return true
  if (u.includes('HT') || u.startsWith('CT') || u.startsWith('CONTRACT')) return true
  return false
}

function preferChainKey(keys: string[]): string {
  const strong = keys.filter(isStrongBiz)
  const pool = strong.length ? strong : keys
  if (!pool.length) return ''
  for (const k of pool) {
    const u = k.toUpperCase()
    if (u.startsWith('SO') || u.startsWith('PO')) return k
  }
  for (const k of pool) {
    const u = k.toUpperCase()
    if (u.includes('HT') || u.startsWith('CT')) return k
  }
  return pool[0]
}

function extractFromText(text: string): string[] {
  const out: string[] = []
  const re = new RegExp(STRONG_RE.source, 'gi')
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const n = normBiz(m[1])
    if (n && !out.includes(n)) out.push(n)
  }
  return out
}

function docAllKeys(doc: ClassifiedDoc): string[] {
  const keys: string[] = []
  const add = (v: unknown) => {
    const n = normBiz(v)
    if (n && isStrongBiz(n) && !keys.includes(n)) keys.push(n)
  }
  const fields = doc.fields || {}
  for (const k of ['orderNo', 'salesOrderNo', 'contractNo', 'documentNo']) {
    add(fields[k])
  }
  for (const k of extractFromText(String(doc.file_name || ''))) add(k)
  return keys
}

/** 归链后的业务笔 ID（与 /chains 同源逻辑的前端回退） */
export function groupChainIdsFromClassified(classified: ClassifiedDoc[]): string[] {
  const parent = new Map<string, string>()

  const find = (x: string): string => {
    if (!parent.has(x)) parent.set(x, x)
    let cur = x
    while (parent.get(cur) !== cur) {
      const p = parent.get(cur)!
      parent.set(cur, parent.get(p) || p)
      cur = parent.get(cur)!
    }
    return cur
  }

  const union = (a: string, b: string) => {
    const ra = find(a)
    const rb = find(b)
    if (ra === rb) return
    if (ra.startsWith('SO') && !rb.startsWith('SO')) parent.set(rb, ra)
    else if (rb.startsWith('SO') && !ra.startsWith('SO')) parent.set(ra, rb)
    else parent.set(rb, ra)
  }

  const docKeys: Array<{ doc: ClassifiedDoc; strong: string[] }> = []
  const soList: string[] = []
  const htList: string[] = []

  for (const doc of classified || []) {
    const fields = doc.fields || {}
    const all = docAllKeys(doc)
    let so = ''
    let ht = ''
    if (doc.doc_type === 'order' || doc.doc_type === 'invoice') {
      so = normBiz(fields.orderNo || fields.salesOrderNo || fields.documentNo)
    } else {
      so = normBiz(fields.orderNo || fields.salesOrderNo)
    }
    if (doc.doc_type === 'contract') {
      ht = normBiz(fields.contractNo || fields.documentNo)
    } else {
      ht = normBiz(fields.contractNo)
    }
    if (so && isStrongBiz(so) && !all.includes(so)) all.push(so)
    if (ht && isStrongBiz(ht) && !all.includes(ht)) all.push(ht)
    const strong = all.filter(isStrongBiz)
    if (so && ht && isStrongBiz(so) && isStrongBiz(ht)) union(so, ht)
    for (let i = 0; i < strong.length; i++) {
      for (let j = i + 1; j < strong.length; j++) union(strong[i], strong[j])
    }
    if (doc.doc_type === 'order' && so && isStrongBiz(so)) soList.push(so)
    if (doc.doc_type === 'order' && so && ht && isStrongBiz(ht)) union(so, ht)
    if (doc.doc_type === 'contract' && ht && isStrongBiz(ht)) htList.push(ht)
    if (doc.doc_type === 'invoice' && so && ht && isStrongBiz(so) && isStrongBiz(ht)) {
      union(so, ht)
    }
    docKeys.push({ doc, strong })
  }

  const soUniq = [...new Set(soList)]
  const htUniq = [...new Set(htList)]
  if (soUniq.length === 1 && htUniq.length === 1) union(soUniq[0], htUniq[0])

  const rootDisplay = new Map<string, string>()
  for (const { strong } of docKeys) {
    for (const k of strong) {
      const r = find(k)
      const cand = preferChainKey([k, r, rootDisplay.get(r) || r])
      const prev = rootDisplay.get(r) || r
      rootDisplay.set(r, preferChainKey([prev, cand, r]))
    }
  }

  const ids = new Set<string>()
  for (const { strong } of docKeys) {
    if (!strong.length) continue
    const root = find(strong[0])
    const cid = rootDisplay.get(root) || root
    if (cid && cid !== '未识别业务号') ids.add(cid)
  }
  return [...ids].sort((a, b) => {
    const pri = (c: string) => (c.startsWith('SO') ? 0 : c.includes('HT') ? 1 : 2)
    return pri(a) - pri(b) || a.localeCompare(b, 'zh')
  })
}

/** 权威链列表：优先 API 传入；否则用归链回退（不再把 HT 拆成幽灵笔） */
export function resolveJobChainIds(job: Job, apiChainIds?: string[] | null): string[] {
  const fromApi = (apiChainIds || []).map((x) => String(x || '').trim()).filter((x) => x && x !== '未识别业务号')
  if (fromApi.length) return fromApi

  const grouped = groupChainIdsFromClassified(job.classified || [])
  if (grouped.length) return grouped

  const ids = new Set<string>()
  for (const k of Object.keys(job.gospd_sample_results || {})) {
    const id = String(k || '').trim()
    if (id && id !== '未识别业务号') ids.add(id)
  }
  const active = String(job.active_chain_id || '').trim()
  if (active && active !== '未识别业务号') ids.add(active)
  return [...ids].sort((a, b) => a.localeCompare(b, 'zh'))
}
