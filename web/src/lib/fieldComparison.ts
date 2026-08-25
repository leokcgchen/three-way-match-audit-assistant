import type { ClassifiedDoc, Job } from '../types'
import { activeSample, docByTypes, docsForChain } from './chainDocs'
import { fieldText } from './readableFields'

export type CompareColumn = {
  id: string
  label: string
  docTypes: string[]
  doc?: ClassifiedDoc
}

export type CompareRow = {
  fieldKey: string
  label: string
  ledger: string
  cells: Record<string, string>
  match: boolean
  hasGap: boolean
  threeWayStatus?: string
  /** 选槽理由（悬停） */
  pickReason?: string
  /** 数量：订单 / 签收验收 / 发票开票一行说明 */
  quantityRolesHint?: string
  /** 单元格含未保存草稿 */
  cellDraft?: Record<string, boolean>
  /** 仅展示并供人工核对，不按跨单据相等性判定 */
  manualReviewOnly?: boolean
}

export type TimelineResult = {
  status: 'PASS' | 'REVIEW' | 'FAIL'
  summary: string
}

const MANUAL_REVIEW_FIELDS = new Set([
  'documentNo',
  'contractNo',
  'invoiceNo',
  'invoiceCode',
  'receiptNo',
  'documentDate',
  'plannedDeliveryDate',
  'deliveryDate',
  'arrivalDateTime',
  'acceptanceDate',
  'acceptanceDateTime',
  'invoiceDateTime',
  'postingDate',
  'customerCode',
  'buyerCode',
  'clientCode',
  'itemCode',
  'batchNo',
  'carrierName',
  'vehiclePlate',
  'paymentTerms',
  'settlementTerms',
  'transportTerms',
  'controlTransferTerms',
  'remarks',
])

const FIELD_LABELS: Record<string, string> = {
  documentNo: '单据编号',
  contractNo: '合同编号',
  orderNo: '订单编号',
  invoiceNo: '发票号码',
  invoiceCode: '发票代码',
  receiptNo: '验收单号',
  documentDate: '单据日期',
  postingDate: '入账日期',
  deliveryDate: '发货日期',
  acceptanceDate: '文件日期',
  plannedDeliveryDate: '计划交付日期',
  arrivalDateTime: '到货时间',
  acceptanceDateTime: '验收完成时间',
  invoiceDateTime: '开票时间',
  totalAmount: '价税合计',
  amount: '金额（不含税）',
  taxAmount: '税额',
  taxRate: '税率',
  unitPrice: '单价（发票未税）',
  unitPriceNet: '未税单价',
  unitPriceGross: '含税单价',
  quantity: '数量（订单 / 签收验收 / 发票开票）',
  unit: '单位',
  supplierName: '卖方',
  buyerName: '购方',
  sellerTaxId: '卖方税号',
  buyerTaxId: '买方税号',
  sellerAddress: '卖方地址',
  buyerAddress: '买方地址/交货地点',
  goodsName: '货品名称',
  model: '规格型号',
  customerCode: '客户编码',
  itemCode: '物料编码',
  batchNo: '批次',
  carrierName: '承运单位',
  vehiclePlate: '车辆/运输识别',
  paymentTerms: '付款条款',
  settlementTerms: '结算条款',
  transportTerms: '运输条款',
  controlTransferTerms: '控制权转移条款',
  remarks: '备注',
}

const EXPANDED_FIELD_ORDER = [
  'orderNo', 'contractNo', 'receiptNo', 'invoiceNo', 'invoiceCode', 'documentNo',
  'documentDate', 'plannedDeliveryDate', 'deliveryDate', 'arrivalDateTime', 'acceptanceDate',
  'acceptanceDateTime', 'invoiceDateTime', 'postingDate',
  'supplierName', 'sellerTaxId', 'sellerAddress', 'buyerName', 'buyerTaxId', 'buyerAddress', 'customerCode',
  'goodsName', 'model', 'itemCode', 'batchNo', 'quantity', 'unit',
  'unitPriceGross', 'unitPriceNet', 'unitPrice', 'amount', 'taxRate', 'taxAmount', 'totalAmount',
  'carrierName', 'vehiclePlate', 'paymentTerms', 'settlementTerms', 'transportTerms',
  'controlTransferTerms', 'remarks',
]

const HIDDEN_ALIAS_FIELDS = new Set(['sellerName', 'supplierAddress', 'supplierTaxId'])
const TECHNICAL_FIELDS = new Set([
  'rule_engine_status', 'decision_authority', 'schema_version', 'prompt_version',
  'receiptDateForCutoff', 'field_evidence_nodes', 'evidence', 'issues', 'audit_log',
])

function isDisplayableBusinessField(key: string): boolean {
  return Boolean(key)
    && !key.startsWith('_')
    && !key.startsWith('text_excerpt_for_')
    && !key.startsWith('reason_code')
    && !HIDDEN_ALIAS_FIELDS.has(key)
    && !TECHNICAL_FIELDS.has(key)
    && key !== 'documentType'
    && key !== 'items'
}

const SLOT_REASON_KEYS: Record<string, string[]> = {
  totalamount: ['total_amount', 'totalAmount'],
  amount: ['total_amount', 'amount'],
  quantity: ['quantity'],
  suppliername: ['supplier_name', 'supplierName'],
  buyername: ['supplier_name', 'buyerName'],
}

/** 与后端 sample_required_fields 对齐：按本笔已有单据裁剪，不再写死 10 行 */
const SYSTEM_REQUIRED: Record<string, string[]> = {
  contract: ['contractNo', 'documentNo', 'documentDate', 'buyerName', 'supplierName', 'totalAmount'],
  order: ['orderNo', 'documentNo', 'documentDate', 'buyerName', 'quantity', 'totalAmount'],
  delivery: ['documentNo', 'orderNo', 'quantity', 'deliveryDate', 'documentDate'],
  receipt: ['documentNo', 'orderNo', 'quantity', 'acceptanceDate', 'documentDate'],
  invoice: [
    'invoiceNo',
    'documentNo',
    'quantity',
    'totalAmount',
    'amount',
    'taxAmount',
    'postingDate',
    'documentDate',
    'supplierName',
    'buyerName',
  ],
  payment: ['documentNo', 'totalAmount', 'documentDate'],
}

const GOSPD01030_BY_TYPE: Record<string, string[]> = {
  contract: [],
  order: ['orderNo', 'buyerName', 'quantity', 'totalAmount'],
  invoice: ['invoiceNo', 'buyerName', 'totalAmount', 'postingDate', 'documentDate'],
  receipt: ['acceptanceDate', 'quantity', 'orderNo'],
  delivery: ['deliveryDate', 'quantity', 'orderNo'],
  payment: ['documentDate', 'totalAmount'],
}

export type RequiredCompareRow = {
  key: string
  label?: string
  filled?: boolean
  source_types?: string[]
}

export function requiredRowsFromDocs(
  docs: ClassifiedDoc[],
  goalIds?: string[] | null,
): RequiredCompareRow[] {
  const use01030 = !goalIds?.length || goalIds.includes('gospd01030')
  const present = new Set(
    docs.map((d) => String(d.doc_type || 'other')).filter((t) => t && t !== 'other'),
  )
  const keys: string[] = []
  const add = (k: string) => {
    if (k && !keys.includes(k)) keys.push(k)
  }
  for (const dt of present) {
    for (const k of SYSTEM_REQUIRED[dt] || []) add(k)
    if (use01030) for (const k of GOSPD01030_BY_TYPE[dt] || []) add(k)
  }
  return keys.map((key) => {
    const source_types = [...present].filter(
      (dt) =>
        (SYSTEM_REQUIRED[dt] || []).includes(key) || (GOSPD01030_BY_TYPE[dt] || []).includes(key),
    )
    return { key, label: FIELD_LABELS[key] || key, source_types }
  })
}

const COLS: Array<{ id: string; label: string; docTypes: string[] }> = [
  { id: 'contract', label: '合同', docTypes: ['contract'] },
  { id: 'order', label: '订单', docTypes: ['order'] },
  { id: 'invoice', label: '发票', docTypes: ['invoice'] },
  { id: 'receipt', label: '签收/发货', docTypes: ['receipt', 'delivery'] },
]

const TW_KEY_ALIASES: Record<string, string[]> = {
  suppliername: ['supplier_name', 'supplier'],
  buyername: ['supplier_name', 'supplier'],
  totalamount: ['total_amount', 'amount'],
  quantity: ['quantity', 'qty'],
  documentdate: ['document_date', 'date'],
  postingdate: ['posting_date'],
  acceptancedate: ['acceptance_date', 'receipt_date'],
}

function normVal(v: string): string {
  return v.replace(/[\s,，￥¥$]/g, '').toLowerCase()
}

function normBizId(v: string): string {
  return v.replace(/[\s\-_/]/g, '').toUpperCase()
}

function valuesMatch(a: string, b: string): boolean {
  if (!a || !b) return false
  const na = normVal(a)
  const nb = normVal(b)
  if (na === nb) return true
  const fa = parseFloat(na)
  const fb = parseFloat(nb)
  if (!Number.isNaN(fa) && !Number.isNaN(fb) && Math.abs(fa - fb) < 0.02) return true
  return false
}

function ledgerSourceDoc(docs: ClassifiedDoc[]): ClassifiedDoc | undefined {
  return docByTypes(docs, 'invoice') || docByTypes(docs, 'order')
}

function normalizeLedgerDate(raw: unknown): string {
  const s = String(raw ?? '').trim()
  if (!s) return ''
  return s.slice(0, 10)
}

function dateValue(raw: string): string {
  const match = String(raw || '').trim().match(/^\d{4}-\d{1,2}-\d{1,2}/)
  if (!match) return ''
  const [year, month, day] = match[0].split('-').map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) return ''
  return [year, String(month).padStart(2, '0'), String(day).padStart(2, '0')].join('-')
}

function chronologyResult(columns: CompareColumn[], draftOverlay?: DraftFieldOverlay | null): TimelineResult {
  const byId = new Map(columns.map((column) => [column.id, column.doc]))
  const contractDate = cellFieldText(byId.get('contract'), 'documentDate', draftOverlay).text
  const dates = [
    ...(contractDate ? [['合同日', contractDate] as const] : []),
    ['订单日', cellFieldText(byId.get('order'), 'documentDate', draftOverlay).text] as const,
    [
      '签收/验收日',
      cellFieldText(byId.get('receipt'), 'acceptanceDate', draftOverlay).text || cellFieldText(byId.get('receipt'), 'documentDate', draftOverlay).text,
    ] as const,
    ['发票日', cellFieldText(byId.get('invoice'), 'documentDate', draftOverlay).text] as const,
  ]
  const normalized = dates.map(([label, raw]) => [label, dateValue(raw)] as const)
  const inverted = normalized.find(([, date], index) => index > 0 && date && normalized[index - 1][1] && date < normalized[index - 1][1])
  if (inverted) {
    return { status: 'FAIL', summary: `时序异常：${normalized.map(([label, date]) => `${label} ${date}`).join('，')}；应满足合同日≤订单日≤签收/验收日≤发票日。` }
  }
  const invalid = dates.filter(([, raw]) => !dateValue(raw)).map(([label]) => label)
  if (invalid.length) {
    return { status: 'REVIEW', summary: `时序待复核：缺少或无法识别${invalid.join('、')}。规则：合同日≤订单日≤签收/验收日≤发票日。` }
  }
  return { status: 'PASS', summary: `时序通过：${normalized.map(([label, date]) => `${label} ${date}`).join('，')}。` }
}

/** 按 ledger_mapping + 已匹配 biz_id 定位序时账原始行 */
export function findLedgerRow(
  job: Job,
  docs: ClassifiedDoc[],
): Record<string, unknown> | null {
  const mapping = job.ledger_mapping
  const rows = job.ledger_rows
  if (!mapping?.posting_date || !rows?.length) return null

  const src = ledgerSourceDoc(docs)
  const matchedBiz = src?.ledger_matched_biz_id
  const postingCol = mapping.posting_date
  const bizCol = mapping.biz_id

  if (matchedBiz && bizCol) {
    const norm = normBizId(String(matchedBiz))
    for (const row of rows) {
      const cell = row[bizCol]
      if (cell != null && normBizId(String(cell)) === norm) return row
    }
  }

  const posting = src?.ledger_posting_date
  if (posting) {
    const pd = normalizeLedgerDate(posting)
    for (const row of rows) {
      if (normalizeLedgerDate(row[postingCol]) !== pd) continue
      if (!bizCol || !matchedBiz) return row
      const cell = row[bizCol]
      if (cell != null && normBizId(String(cell)) === normBizId(String(matchedBiz))) {
        return row
      }
    }
  }
  return null
}

function ledgerCell(job: Job, docs: ClassifiedDoc[], fieldKey: string): string {
  const src = ledgerSourceDoc(docs)
  const row = findLedgerRow(job, docs)
  const mapping = job.ledger_mapping

  if (fieldKey === 'postingDate') {
    if (src?.ledger_posting_date) return String(src.ledger_posting_date)
    if (row && mapping?.posting_date) {
      return normalizeLedgerDate(row[mapping.posting_date])
    }
    return fieldText(src, 'postingDate')
  }

  if (fieldKey === 'totalAmount') {
    if (src?.ledger_amount != null && src.ledger_amount !== '') {
      return String(src.ledger_amount)
    }
    if (row && mapping?.amount) {
      const v = row[mapping.amount]
      if (v != null && v !== '') return String(v)
    }
    return ''
  }

  if (fieldKey === 'documentNo' || fieldKey === 'invoiceNo' || fieldKey === 'orderNo') {
    if (src?.ledger_matched_biz_id) return String(src.ledger_matched_biz_id)
    if (row && mapping?.biz_id) {
      const v = row[mapping.biz_id]
      if (v != null && v !== '') return String(v)
    }
    return ''
  }

  if (fieldKey === 'documentDate' && row && mapping?.posting_date) {
    return normalizeLedgerDate(row[mapping.posting_date])
  }

  return ''
}

function threeWayBlob(job: Job): Record<string, unknown> | null {
  const sample = activeSample(job)
  const tw = (sample.three_way || sample.three_way_match || job.three_way) as
    | Record<string, unknown>
    | null
    | undefined
  return tw && typeof tw === 'object' ? tw : null
}

function threeWayRowMap(job: Job): Map<string, string> {
  const tw = threeWayBlob(job)
  const match = (tw?.match_result as Record<string, unknown> | undefined) || {}
  const raw = (tw?.comparisons || match.comparisons || []) as Array<Record<string, unknown>>
  const m = new Map<string, string>()
  for (const c of raw) {
    const name = String(c.field_name || c.field || '').toLowerCase()
    if (!name) continue
    const st =
      c.is_consistent === false
        ? 'FAIL'
        : c.is_consistent === true
          ? String(c.status || 'PASS')
          : String(c.status || '')
    m.set(name, st)
  }
  return m
}

function threeWayPickReason(job: Job, fieldKey: string): string {
  const tw = threeWayBlob(job)
  if (!tw) return ''
  const match = (tw.match_result as Record<string, unknown> | undefined) || {}
  const slots = {
    ...((match.slot_reasons as Record<string, string>) || {}),
    ...((tw.slot_reasons as Record<string, string>) || {}),
  }
  const aliases = SLOT_REASON_KEYS[fieldKey.toLowerCase()] || [fieldKey]
  for (const a of aliases) {
    if (slots[a]) return String(slots[a])
  }
  const raw = (tw.comparisons || match.comparisons || []) as Array<Record<string, unknown>>
  for (const c of raw) {
    const name = String(c.field_name || '').toLowerCase()
    if (aliases.some((a) => a.toLowerCase() === name) && c.pick_reason) {
      return String(c.pick_reason)
    }
  }
  return ''
}

function threeWayQtyHint(job: Job): string {
  const tw = threeWayBlob(job)
  if (!tw) return ''
  const match = (tw.match_result as Record<string, unknown> | undefined) || {}
  const roles = (tw.quantity_roles || match.quantity_roles || {}) as Record<string, unknown>
  const o = roles.ordered_qty
  const r = roles.received_qty
  const i = roles.invoiced_qty
  if (o == null && r == null && i == null) return ''
  return `订单数量 ${o ?? '—'}，签收/验收数量 ${r ?? '—'}，发票开票数量 ${i ?? '—'}`
}

function threeWayStatusForKey(twMap: Map<string, string>, fieldKey: string): string | undefined {
  const aliases = TW_KEY_ALIASES[fieldKey.toLowerCase()] || [fieldKey.toLowerCase()]
  for (const a of aliases) {
    const st = twMap.get(a)
    if (st) return st
  }
  return undefined
}

export function verifiedFieldKeys(job: Job, chainId?: string | null): Set<string> {
  const cid = chainId || job.active_chain_id || 'job'
  const root = job.field_row_verifications || {}
  const chain = root[cid] || {}
  const keys = new Set<string>()
  for (const [k, v] of Object.entries(chain)) {
    if (v?.verified) keys.add(k)
  }
  return keys
}

/** 未保存草稿：file_name → field_key → 文本 */
export type DraftFieldOverlay = Record<string, Record<string, string>>

function cellFieldText(
  doc: ClassifiedDoc | undefined,
  key: string,
  draftOverlay?: DraftFieldOverlay | null,
): { text: string; isDraft: boolean } {
  if (!doc) return { text: '', isDraft: false }
  const saved = fieldText(doc, key)
  const draftRaw = draftOverlay?.[doc.file_name]?.[key]
  if (draftRaw !== undefined) {
    const draft = String(draftRaw).trim()
    if (draft !== saved) return { text: draft, isDraft: true }
  }
  return { text: saved, isDraft: false }
}

export function buildFieldComparison(
  job: Job,
  chainFileNames?: string[] | null,
  draftOverlay?: DraftFieldOverlay | null,
  requiredRows?: RequiredCompareRow[] | null,
): {
  columns: CompareColumn[]
  rows: CompareRow[]
  docs: ClassifiedDoc[]
  timing: TimelineResult
} {
  const docs = docsForChain(job, job.active_chain_id, chainFileNames)
  const columns: CompareColumn[] = COLS.map((c) => ({
    id: c.id,
    label: c.label,
    docTypes: c.docTypes,
    doc: docByTypes(docs, ...c.docTypes),
  }))
  const twMap = threeWayRowMap(job)
  const presentCols = columns.filter((c) => c.doc)
  const baseSpec =
    requiredRows && requiredRows.length
      ? requiredRows
      : requiredRowsFromDocs(docs, job.goal_ids)
  const spec = baseSpec.map((row) => ({ ...row, source_types: [...(row.source_types || [])] }))
  const extractedKeys = new Set<string>()
  for (const doc of docs) {
    for (const key of Object.keys(doc.fields || {})) {
      if (isDisplayableBusinessField(key)) {
        extractedKeys.add(key)
      }
    }
  }
  const orderedKeys = [
    ...EXPANDED_FIELD_ORDER.filter((key) => extractedKeys.has(key)),
    ...[...extractedKeys].filter((key) => !EXPANDED_FIELD_ORDER.includes(key)).sort(),
  ]
  for (const key of orderedKeys) {
    const sourceTypes = [
      ...new Set(
        docs
          .filter((item) => fieldText(item, key))
          .map((item) => String(item.doc_type || 'other'))
          .filter((docType) => docType !== 'other'),
      ),
    ]
    const existing = spec.find((row) => row.key === key)
    if (existing) {
      existing.source_types = [...new Set([...(existing.source_types || []), ...sourceTypes])]
      if (!existing.label) existing.label = FIELD_LABELS[key] || key
    } else if (sourceTypes.length) {
      spec.push({ key, label: FIELD_LABELS[key] || key, source_types: sourceTypes })
    }
  }
  const fieldRank = new Map(EXPANDED_FIELD_ORDER.map((key, index) => [key, index]))
  spec.sort((left, right) => {
    const leftRank = fieldRank.get(left.key) ?? (EXPANDED_FIELD_ORDER.length + 100)
    const rightRank = fieldRank.get(right.key) ?? (EXPANDED_FIELD_ORDER.length + 100)
    return leftRank - rightRank || String(left.label || left.key).localeCompare(String(right.label || right.key), 'zh-CN')
  })

  const rows: CompareRow[] = []
  for (const item of spec) {
    const key = item.key
    const sourceTypes = item.source_types || []
    const applyCols = presentCols.filter((col) => {
      if (!sourceTypes.length) return true
      const dt = String(col.doc?.doc_type || '')
      return sourceTypes.includes(dt) || col.docTypes.some((t) => sourceTypes.includes(t))
    })
    const cells: Record<string, string> = {}
    const cellDraft: Record<string, boolean> = {}
    const vals: string[] = []
    for (const col of columns) {
      const { text, isDraft } = cellFieldText(col.doc, key, draftOverlay)
      cells[col.id] = text
      if (isDraft) cellDraft[col.id] = true
      if (text && applyCols.includes(col)) vals.push(text)
    }
    const ledger = ledgerCell(job, docs, key)

    const manualReviewOnly = MANUAL_REVIEW_FIELDS.has(key)
    let match = true
    if (manualReviewOnly) {
      // 单据号和单据日期各有业务语义，不用跨单据“相等”制造假错配；保留逐格定位和人工确认。
      match = true
    } else if (applyCols.length === 0) {
      match = vals.length === 0
    } else if (vals.length === 0) {
      match = false
    } else {
      const ref = vals[0]
      match = vals.every((v) => valuesMatch(v, ref))
      if (ledger && (key === 'totalAmount' || key === 'postingDate')) {
        match = match && valuesMatch(ledger, ref)
      }
    }

    const filledApply = applyCols.filter((col) => cells[col.id]).length
    const hasGap = !manualReviewOnly && applyCols.length > 0 && filledApply < applyCols.length
    const hasDraft = Object.keys(cellDraft).length > 0
    const baseLabel = item.label || FIELD_LABELS[key] || key
    const qtyHint = key === 'quantity' ? threeWayQtyHint(job) : ''

    rows.push({
      fieldKey: key,
      label: key === 'quantity' ? FIELD_LABELS.quantity || baseLabel : baseLabel,
      ledger,
      cells,
      match: match && !hasGap && !hasDraft,
      hasGap,
      threeWayStatus: threeWayStatusForKey(twMap, key),
      pickReason: threeWayPickReason(job, key),
      quantityRolesHint: qtyHint || undefined,
      cellDraft,
      manualReviewOnly,
    })
  }
  return { columns, rows, docs, timing: chronologyResult(columns, draftOverlay) }
}

export function countUnverifiedMismatches(
  job: Job,
  chainFileNames?: string[] | null,
  draftOverlay?: DraftFieldOverlay | null,
  requiredRows?: RequiredCompareRow[] | null,
): number {
  const { rows, timing } = buildFieldComparison(job, chainFileNames, draftOverlay, requiredRows)
  const verified = verifiedFieldKeys(job, job.active_chain_id)
  const timingMismatch = timing.status === 'FAIL' ? 1 : 0
  return rows.filter((r) => !r.match && !verified.has(r.fieldKey)).length + timingMismatch
}

export function resolveDocForCell(
  columns: CompareColumn[],
  colId: string,
): ClassifiedDoc | undefined {
  return columns.find((c) => c.id === colId)?.doc
}
