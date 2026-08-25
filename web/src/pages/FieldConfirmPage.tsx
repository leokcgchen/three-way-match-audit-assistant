import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { DocPreview } from '../components/DocPreview'
import { CapturePreview } from '../components/CapturePreview'
import { FieldComparisonMatrix } from '../components/FieldComparisonMatrix'
import { ExplainableFieldMatrix } from '../components/ExplainableFieldMatrix'
import { FieldReasonDrawer } from '../components/FieldReasonDrawer'
import { BusinessChronologyPanel } from '../components/BusinessChronologyPanel'
import { ResolutionIssuesPanel } from '../components/ResolutionIssuesPanel'
import { useActiveChainFiles } from '../lib/useActiveChainFiles'
import { useJobChainIds } from '../lib/useJobChainIds'
import { buildWorkflowGuide } from '../lib/workflowGuide'
import { effectiveFields, highlightLocateValue } from '../lib/readableFields'
import { confirmLinkagePrimary } from '../lib/confirmLinkage'
import { AmountAmbiguityPanel } from '../components/AmountAmbiguityPanel'
import { activeSample, isGospdJob } from '../lib/chainDocs'
import { consumeFieldTraceTarget } from '../lib/fieldTraceNavigation'
import { DOCUMENT_TYPE_LABELS, documentTypeLabel } from '../lib/documentTypeDisplay'
import type {
  ClassifiedDoc,
  ExplainableComparisonRow,
  FieldEvidenceNode,
  FieldResolution,
  Job,
} from '../types'

const FIELD_CN: Record<string, string> = {
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
  paymentTerms: '付款条款',
  controlTransferTerms: '控制权转移',
  settlementTerms: '结算条款',
  transportTerms: '运输条款',
  totalAmount: '价税合计',
  amount: '金额',
  taxAmount: '税额',
  taxRate: '税率',
  unitPrice: '单价（发票未税）',
  unitPriceNet: '未税单价',
  unitPriceGross: '含税单价',
  quantity: '数量',
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
  remarks: '备注',
}

/** 审计师默认真核的关键字段（其余折叠，确认仍覆盖全部） */
const CRITICAL_BY_TYPE: Record<string, string[]> = {
  contract: [
    'contractNo',
    'documentNo',
    'buyerName',
    'supplierName',
    'totalAmount',
    'paymentTerms',
    'controlTransferTerms',
    'documentDate',
  ],
  order: [
    'orderNo',
    'documentNo',
    'contractNo',
    'buyerName',
    'supplierName',
    'quantity',
    'totalAmount',
    'documentDate',
    'paymentTerms',
  ],
  delivery: ['documentNo', 'orderNo', 'quantity', 'deliveryDate', 'documentDate', 'buyerName'],
  receipt: [
    'documentNo',
    'orderNo',
    'quantity',
    'acceptanceDate',
    'deliveryDate',
    'documentDate',
    'buyerName',
  ],
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
  payment: ['documentNo', 'totalAmount', 'documentDate', 'buyerName', 'supplierName'],
  other: ['documentNo', 'totalAmount', 'quantity', 'documentDate'],
}

type Props = {
  job: Job
  onJob: (job: Job) => void
  /** @deprecated 字段核对固定全屏；保留参数以免旧调用报错 */
  embedded?: boolean
  onOpenFull?: () => void
  onDirtyChange?: (dirty: boolean) => void
  onGo?: (step: string) => void
}

function editableKeys(doc: ClassifiedDoc): string[] {
  const fields = doc.fields || {}
  const preferred = Object.keys(FIELD_CN)
  const keys: string[] = []
  for (const k of preferred) {
    if (k in fields || ['contract', 'order', 'invoice', 'receipt', 'delivery'].includes(doc.doc_type)) {
      if (!keys.includes(k)) keys.push(k)
    }
  }
  for (const k of Object.keys(fields)) {
    if (!k.startsWith('_') && !keys.includes(k) && k !== 'documentType' && k !== 'items') keys.push(k)
  }
  return keys
}

function normalizeFieldValue(key: string, value: unknown): string {
  let text = ''
  if (value != null) {
    if (typeof value === 'object') {
      try {
        text = JSON.stringify(value)
      } catch {
        text = String(value)
      }
    } else {
      text = String(value)
    }
  }
  text = text.trim()
  if (['quantity', 'amount', 'taxAmount', 'totalAmount', 'taxRate'].includes(key)) {
    const numeric = Number(text.replace(/,/g, ''))
    return text && Number.isFinite(numeric) ? String(numeric) : text
  }
  return text
}

export function FieldConfirmPage({ job, onJob, embedded, onOpenFull, onDirtyChange, onGo }: Props) {
  const { chainFileNames, chainDocs, activeChain } = useActiveChainFiles(job)
  const chainIds = useJobChainIds(job)
  const docs = chainDocs
  const [idx, setIdx] = useState(0)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [docType, setDocType] = useState('other')
  const [customDocTypeName, setCustomDocTypeName] = useState('')
  const [hl, setHl] = useState<string | null>(null)
  /** 点击/聚焦时快照，避免边打字边反复请求高亮 */
  const [hlValue, setHlValue] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [ambOpenCount, setAmbOpenCount] = useState(0)
  const [gapBusy, setGapBusy] = useState(false)
  const [addKey, setAddKey] = useState('')
  const [addVal, setAddVal] = useState('')
  const [editReason, setEditReason] = useState('')
  /** 只有审计师实际操作过字段编辑控件后，才允许进入未保存判断。 */
  const [hasUserEdited, setHasUserEdited] = useState(false)
  const [captureOn, setCaptureOn] = useState(false)
  const [docsCollapsed, setDocsCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gospd.fieldDocsCollapsed') === '1'
    } catch {
      return false
    }
  })
  const [fieldsCollapsed, setFieldsCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gospd.fieldPaneCollapsed') === '1'
    } catch {
      return false
    }
  })
  const [viewMode, setViewMode] = useState<'doc' | 'matrix'>(() => {
    try {
      return sessionStorage.getItem('gospd.fieldViewMode') === 'matrix' ? 'matrix' : 'doc'
    } catch {
      return 'doc'
    }
  })
  const [matrixDoc, setMatrixDoc] = useState<ClassifiedDoc | null>(null)
  const [matrixRefreshKey, setMatrixRefreshKey] = useState(0)
  const [fetchedResolution, setFetchedResolution] = useState<{
    chainId: string
    value: FieldResolution
  } | null>(null)
  const [reasonRow, setReasonRow] = useState<ExplainableComparisonRow | null>(null)
  const [showResolutionDetails, setShowResolutionDetails] = useState(false)
  const [resolutionBusy, setResolutionBusy] = useState(false)
  const resolutionRequestRef = useRef('')
  /** 对照表点格后保留高亮，避免切 doc 的 effect 立刻清 hl */
  const matrixHlRef = useRef<{ file: string; field: string; value: string } | null>(null)

  const sample = isGospdJob(job) ? activeSample(job) : null
  const fieldsOk = Boolean(sample ? sample.fields_confirmed : job.fields_confirmed)
  const persistedResolution = (sample?.field_resolution || null) as FieldResolution | null
  const resolutionChainId = String(activeChain?.chain_id || job.active_chain_id || '').trim()
  const dynamicResolution = persistedResolution || (
    fetchedResolution?.chainId === resolutionChainId ? fetchedResolution.value : null
  )
  const dynamicResolutionId = dynamicResolution?.resolution_id || ''

  useEffect(() => {
    const requestKey = `${job.job_id}:${resolutionChainId}`
    if (viewMode !== 'matrix' || dynamicResolutionId || !resolutionChainId) return
    if (resolutionRequestRef.current === requestKey) return
    let cancelled = false
    resolutionRequestRef.current = requestKey
    setResolutionBusy(true)
    api.refreshFieldResolution(job.job_id, resolutionChainId)
      .then((resolution) => {
        if (!cancelled && resolutionRequestRef.current === requestKey) {
          setFetchedResolution({ chainId: resolutionChainId, value: resolution })
        }
      })
      .catch((error) => {
        if (!cancelled && resolutionRequestRef.current === requestKey) {
          setErr(error instanceof Error ? error.message : String(error))
        }
      })
      .finally(() => {
        if (!cancelled && resolutionRequestRef.current === requestKey) {
          resolutionRequestRef.current = ''
          setResolutionBusy(false)
        }
      })
    return () => { cancelled = true }
  }, [viewMode, dynamicResolutionId, resolutionChainId, job.job_id])

  const setView = (mode: 'doc' | 'matrix') => {
    setViewMode(mode)
    try {
      sessionStorage.setItem('gospd.fieldViewMode', mode)
    } catch {
      /* ignore */
    }
    if (mode === 'matrix') {
      setFieldsCollapsed(true)
      persistPane('gospd.fieldPaneCollapsed', true)
    }
  }

  const persistPane = (key: string, value: boolean) => {
    try {
      localStorage.setItem(key, value ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  const doc = docs[idx]
  const currentMatrixDoc = matrixDoc
    ? docs.find((item) => item.file_name === matrixDoc.file_name) || matrixDoc
    : null
  const previewDoc = currentMatrixDoc || doc
  const isDirty = useMemo(() => {
    if (!hasUserEdited) return false
    const target = previewDoc || doc
    if (!target) return false
    const saved = effectiveFields(target)
    const changedField = Object.entries(draft).some(
      ([key, value]) => normalizeFieldValue(key, value) !== normalizeFieldValue(key, saved[key]),
    )
    const completeAddedField = Boolean(addKey.trim() && addVal.trim())
    const changedTypeName =
      docType === 'other' &&
      customDocTypeName.trim() !== String(target.custom_doc_type_name || '').trim()
    return changedField || docType !== (target.doc_type || 'other') || changedTypeName || completeAddedField
  }, [hasUserEdited, previewDoc, doc, draft, docType, customDocTypeName, addKey, addVal])

  useEffect(() => {
    onDirtyChange?.(isDirty)
    const warn = (event: BeforeUnloadEvent) => {
      if (!isDirty) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [isDirty, onDirtyChange])

  useEffect(
    () => () => {
      onDirtyChange?.(false)
    },
    [onDirtyChange],
  )

  const allowDiscardChanges = () =>
    !isDirty || window.confirm('当前单据有未保存的字段修改，确定放弃修改并切换吗？')

  const matrixDraftOverlay = useMemo(() => {
    const target = previewDoc || doc
    if (viewMode !== 'matrix' || !target?.file_name) return null
    return { [target.file_name]: draft }
  }, [viewMode, previewDoc?.file_name, doc?.file_name, draft])

  const loadDraftFromDoc = (item: ClassifiedDoc) => {
    const eff = effectiveFields(item)
    const next: Record<string, string> = {}
    for (const k of editableKeys(item)) {
      const v = eff[k]
      next[k] = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
    }
    setDraft(next)
    setDocType(item.doc_type || 'other')
    setCustomDocTypeName(item.custom_doc_type_name || '')
    setAddKey('')
    setAddVal('')
    setEditReason('')
    setHasUserEdited(false)
  }

  const onMatrixCell = (d: ClassifiedDoc, fieldKey: string) => {
    if (previewDoc?.file_name !== d.file_name && !allowDiscardChanges()) return
    const i = docs.findIndex((x) => x.file_name === d.file_name)
    const eff = effectiveFields(d)
    const draftVal = previewDoc?.file_name === d.file_name ? draft[fieldKey] : undefined
    const display =
      draftVal !== undefined && String(draftVal).trim() !== ''
        ? draftVal
        : eff[fieldKey]
    const value =
      draftVal !== undefined && String(draftVal).trim() !== ''
        ? String(draftVal)
        : highlightLocateValue(d, fieldKey, display)
    matrixHlRef.current = { file: d.file_name, field: fieldKey, value }
    if (i >= 0) setIdx(i)
    setMatrixDoc(d)
    setHl(fieldKey)
    setHlValue(value)
    setCaptureOn(false)
  }

  const pickHighlight = (k: string | null, toggle = true) => {
    if (!k) {
      setHl(null)
      setHlValue('')
      return
    }
    if (toggle && hl === k) {
      setHl(null)
      setHlValue('')
      return
    }
    setHl(k)
    setHlValue(highlightLocateValue(doc, k, draft[k] ?? ''))
  }

  useEffect(() => {
    setIdx(0)
    setMatrixDoc(null)
    setHl(null)
    setHlValue('')
    setCaptureOn(false)
  }, [job.active_chain_id, chainFileNames?.join('|')])

  useEffect(() => {
    const target = consumeFieldTraceTarget(job.job_id, job.active_chain_id || '')
    if (!target) return
    const targetDoc = docs.find((item) => item.file_name === target.fileName)
    if (!targetDoc) {
      setMsg(`已进入核对字段，但未找到原件：${target.fileName}`)
      return
    }
    const targetIndex = docs.findIndex((item) => item.file_name === target.fileName)
    const value = highlightLocateValue(targetDoc, target.fieldKey, effectiveFields(targetDoc)[target.fieldKey])
    matrixHlRef.current = { file: targetDoc.file_name, field: target.fieldKey, value }
    setViewMode('matrix')
    setFieldsCollapsed(true)
    if (targetIndex >= 0) setIdx(targetIndex)
    setMatrixDoc(targetDoc)
    setHl(target.fieldKey)
    setHlValue(value)
    setCaptureOn(false)
    setMsg(`已定位原件：${targetDoc.file_name} · ${FIELD_CN[target.fieldKey] || target.fieldKey}`)
  }, [job.job_id, job.active_chain_id, chainFileNames?.join('|')])

  useEffect(() => {
    if (!doc) {
      setDraft({})
      return
    }
    loadDraftFromDoc(doc)
    const pending = matrixHlRef.current
    if (pending && pending.file === doc.file_name) {
      setHl(pending.field)
      setHlValue(pending.value)
      matrixHlRef.current = null
    } else {
      setHl(null)
      setHlValue('')
      setCaptureOn(false)
    }
  }, [doc?.file_name, job.job_id, job.fields_confirm_sig, job.updated_at])

  const requiredRows = activeChain?.required_fields || []
  const requiredForDoc = useMemo(() => {
    if (!requiredRows.length) return []
    return requiredRows
      .filter((r) => !r.source_types?.length || r.source_types.includes(docType))
      .map((r) => r.key)
  }, [requiredRows, docType])
  const keys = useMemo(() => {
    const base = doc ? editableKeys(doc) : []
    for (const k of requiredForDoc) {
      if (!base.includes(k)) base.push(k)
    }
    return base
  }, [doc, requiredForDoc])
  const criticalKeys = useMemo(() => {
    if (requiredForDoc.length) return requiredForDoc.filter((k) => keys.includes(k))
    const pref = CRITICAL_BY_TYPE[docType] || CRITICAL_BY_TYPE.other
    return pref.filter((k) => keys.includes(k))
  }, [docType, keys, requiredForDoc])
  const extraKeys = useMemo(
    () => keys.filter((k) => !criticalKeys.includes(k)),
    [keys, criticalKeys],
  )
  const emptyCritical = useMemo(
    () =>
      criticalKeys.filter((k) => {
        const v = draft[k] ?? doc?.fields?.[k]
        return v == null || String(v).trim() === ''
      }),
    [criticalKeys, draft, doc],
  )
  const missingCn = useMemo(() => {
    if (activeChain?.missing_labels?.length) return activeChain.missing_labels
    const keys = activeChain?.missing_fields?.length
      ? activeChain.missing_fields
      : emptyCritical
    return keys.map((k) => FIELD_CN[k] || k)
  }, [activeChain?.missing_labels, activeChain?.missing_fields, emptyCritical])

  const renderFieldRow = (k: string) => (
    <div className="field-row" key={k}>
      <button type="button" className={hl === k ? 'on' : ''} onClick={() => pickHighlight(k)}>
        {FIELD_CN[k] || k}
        {emptyCritical.includes(k) ? <span className="badge warn">空</span> : null}
      </button>
      <div className="field-input-wrap">
        <input
          aria-label={FIELD_CN[k] || k}
          value={draft[k] ?? ''}
          onChange={(e) => {
            const v = e.target.value
            setHasUserEdited(true)
            setDraft((d) => ({ ...d, [k]: v }))
            if (hl === k) setHlValue(v)
          }}
          onFocus={() => {
            pickHighlight(k, false)
            if (captureOn && hl !== k) setCaptureOn(false)
          }}
        />
        <button
          type="button"
          className={`btn compact field-capture-btn${captureOn && hl === k ? ' primary' : ''}`}
          data-tip="在原件上点选或拖框，把看到的文字填进这个字段。"
          onClick={() => {
            pickHighlight(k, false)
            setCaptureOn(true)
          }}
        >
          取证
        </button>
      </div>
    </div>
  )

  const save = async () => {
    const target = previewDoc || doc
    if (!target) return
    if (docType === 'other' && !customDocTypeName.trim()) {
      setErr('请填写当前文件的具体单据名称')
      return
    }
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const fields: Record<string, unknown> = { ...(target.fields || {}) }
      const saved = effectiveFields(target)
      for (const [k, text] of Object.entries(draft)) {
        if (normalizeFieldValue(k, text) === normalizeFieldValue(k, saved[k])) continue
        const t = (text || '').trim()
        if (!t) {
          delete fields[k]
          continue
        }
        if (['quantity', 'amount', 'taxAmount', 'totalAmount', 'taxRate'].includes(k)) {
          const n = Number(String(t).replace(/,/g, ''))
          fields[k] = Number.isFinite(n) ? n : t
        } else fields[k] = t
      }
      if (addKey.trim() && addVal.trim()) fields[addKey.trim()] = addVal
      const next = await api.patchFields(job.job_id, {
        file_name: target.file_name,
        fields,
        doc_type: docType,
        custom_doc_type_name: docType === 'other' ? customDocTypeName.trim() : undefined,
        doc_type_confirmed: true,
        reason: editReason || undefined,
      })
      onJob(next)
      setMatrixRefreshKey((k) => k + 1)
      const savedDoc = (next.classified || []).find((d) => d.file_name === target.file_name)
      if (savedDoc) {
        setMatrixDoc(savedDoc)
        loadDraftFromDoc(savedDoc)
        if (hl) {
          const eff = effectiveFields(savedDoc)
          const v = eff[hl]
          setHlValue(v == null ? '' : String(v))
        }
      }
      setCaptureOn(false)
      setMsg('本单已保存（确认状态已失效，需重新全部确认）。对照表已刷新。')
      setAddKey('')
      setAddVal('')
      setEditReason('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const onResolutionEvidence = (evidenceId: string) => {
    const node = dynamicResolution?.evidence_nodes.find((item) => item.evidence_id === evidenceId)
    if (!node) return
    const fileName = String(node.metadata?.file_name || node.document_id || '')
    const target = docs.find((item) =>
      item.file_name === fileName || item.file_fingerprint === node.document_id,
    )
    if (!target) {
      setErr(`证据对应原件未找到：${fileName || evidenceId}`)
      return
    }
    onMatrixCell(target, node.field_key)
  }

  const decideResolutionIssue = async ({
    edgeId,
    decision,
    reason,
  }: {
    edgeId: string
    decision: 'CONFIRMED' | 'REJECTED'
    reason: string
  }) => {
    if (!resolutionChainId) return
    const resolution = await api.decideFieldResolutionEdge(job.job_id, edgeId, {
      chain_id: resolutionChainId,
      decision,
      reason,
    })
    setFetchedResolution({ chainId: resolutionChainId, value: resolution })
    setMsg('判断依据已记录，原始字段和证据保持不变。')
  }

  const declareCurrentMixed = async () => {
    const target = previewDoc || doc
    if (!target || !target.file_name.toLowerCase().endsWith('.pdf')) return
    if (!allowDiscardChanges()) return
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const next = await api.declareMixed(job.job_id, target.file_name)
      onDirtyChange?.(false)
      onJob(next)
      onGo?.('packet_unpack')
    } catch (reason) {
      setErr(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  /** 确认后横扫下一笔；只切权威链，避免幽灵 HT 空转 */
  const advanceAfterConfirm = async (j: Job, baseMsg: string) => {
    const g = buildWorkflowGuide(j, { chainIds })
    if (
      g.action.kind === 'switch_chain' &&
      chainIds.includes(g.action.chain_id)
    ) {
      const next = await api.setActiveChain(j.job_id, g.action.chain_id)
      onJob(next)
      setMsg(`${baseMsg} 已切换到 ${g.action.chain_id}，请继续本页人工核对该笔。`)
      setMatrixDoc(null)
      setIdx(0)
      return
    }
    onJob(j)
    if (g.sweepPhase && g.sweepPhase !== 'fields' && g.sweepPhase !== 'evidence' && g.sweepPhase !== 'gate4') {
      setMsg(`${baseMsg} 人工核对已齐，请继续「${g.ctaLabel}」。`)
    } else if (g.action.kind === 'go' && g.action.step === 'field_confirm') {
      setMsg(`${baseMsg} 请继续在本页完成其余笔的人工核对。`)
    } else {
      setMsg(baseMsg)
    }
  }

  const confirmAll = async () => {
    setBusy(true)
    setErr('')
    try {
      const confirmedChain = job.active_chain_id || activeChain?.chain_id || ''
      const next = await api.confirmFields(job.job_id, confirmedChain)
      if (confirmedChain && next.active_chain_id && next.active_chain_id !== confirmedChain) {
        onJob(next)
        setMsg(`已确认 ${confirmedChain} 字段；服务器当前笔为 ${next.active_chain_id}，未自动处理其他业务笔。`)
        return
      }
      try {
        const linked = await confirmLinkagePrimary(next)
        await advanceAfterConfirm(linked.job, '字段已确认，测试将自动继续。')
      } catch {
        await advanceAfterConfirm(next, '本笔字段已确认。')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const gapFill = async () => {
    setGapBusy(true)
    setErr('')
    setMsg('正在补抽当前笔缺失字段（先启发式，语义字段再调 LLM）…')
    try {
      // 默认只补当前笔，避免 7×LLM 串行像卡住
      const r = await api.gapFillFields(job.job_id, 'active')
      const next = r.job
      onJob(next)
      setMatrixRefreshKey((k) => k + 1)
      setMatrixDoc(null)
      const target = previewDoc || doc
      if (target) {
        const updated = (next.classified || []).find((d) => d.file_name === target.file_name)
        if (updated) loadDraftFromDoc(updated)
      }
      const n = Number(r.summary?.fields_filled || 0)
      const d = Number(r.summary?.docs_touched || 0)
      const hydrated = Number(r.summary?.text_hydrated || 0)
      const llmOk = r.summary?.llm_configured !== false
      const scope = String(r.summary?.scope || 'active')
      const skipped = (r.summary?.skipped_no_text || []) as string[]
      const details = (r.summary?.details || []) as Array<{
        file_name?: string
        filled?: string[]
        missing?: string[]
        skipped_reason?: string
        llm_used?: boolean
      }>
      const filledLines = details
        .filter((x) => (x.filled || []).length > 0)
        .map((x) => `${x.file_name}: ${(x.filled || []).join('、')}`)
      const llmDocs = details.filter((x) => x.llm_used).length
      if (n > 0) {
        setMsg(
          `已补抽${scope === 'active' ? '当前笔' : ''} ${d} 份共 ${n} 个字段` +
            `${hydrated ? `（${hydrated} 份从 PDF 取正文）` : ''}` +
            `${llmDocs ? `，其中 ${llmDocs} 份调用了 LLM` : '（多为启发式，未空等 LLM）'}。` +
            ` 请点单元格核对原件高亮后再确认。` +
            (filledLines.length ? ` 命中：${filledLines.slice(0, 3).join('；')}` : ''),
        )
      } else {
        const stillMissing = details
          .filter((x) => (x.missing || []).length && !(x.filled || []).length)
          .slice(0, 2)
          .map((x) => `${x.file_name}: 仍缺 ${(x.missing || []).slice(0, 4).join('、')}`)
        setMsg(
          (llmOk
            ? '当前笔未发现可补抽的缺失字段（已有值不会覆盖）。'
            : 'LLM Key 未配置：已尝试启发式；配置千帆 Key 后可补付款/控制权等条款。') +
            (stillMissing.length ? ` ${stillMissing.join('；')}` : '') +
            (skipped.length ? ` 跳过无正文：${skipped.slice(0, 2).join('、')}` : ''),
        )
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setGapBusy(false)
    }
  }

  if (!docs.length) {
    return (
      <div className={embedded ? 'desk-embed' : 'panel panel-fill'}>
        {!embedded && (
          <div className="panel-head">
            <div>
              <h3>人工核对</h3>
              <div className="hint">红灯笔才进本页。请先在工作台立笔并识别凭证。</div>
            </div>
          </div>
        )}
        <div className={embedded ? 'desk-embed-body' : 'panel-body'}>
          <p className="preview-empty">
            {job.classified?.length
              ? `当前笔（${job.active_chain_id || '-'}）暂无单据，请切换业务链或重新上传。`
              : '请先到“上传抽样清单”页面导入清单，再到“上传凭证”页面上传并识别。'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className={embedded ? 'desk-embed field-hitl' : 'page-shell field-hitl'}>
      <div className="field-hitl-chrome">
        <div className="toolbar between field-hitl-toolbar">
          <div className="hint">
            人工核对 ·{' '}
            {job.active_chain_id ? (
              <span className="badge ok">当前笔 {job.active_chain_id}</span>
            ) : null}{' '}
            {fieldsOk ? (
              <span className="badge ok">字段已确认</span>
            ) : (
              <span className="badge pending">字段待确认</span>
            )}
            {(activeChain?.missing_fields?.length || emptyCritical.length) > 0 && (
              <span className="badge warn ml-8">
                缺：{missingCn.join('、') || `${(activeChain?.missing_fields || emptyCritical).length} 项`}
              </span>
            )}
            {ambOpenCount > 0 && (
              <span className="badge warn ml-8">金额待确认 {ambOpenCount}</span>
            )}
          </div>
          {(err || msg) && (
            <div className="field-hitl-flash">
              {err && <span className="err">{err}</span>}
              {msg && <span className="ok-text">{msg}</span>}
            </div>
          )}
        </div>
      </div>

          <div className="toolbar between field-hitl-fields-actions">
            <div className="toolbar">
              {embedded && onOpenFull && (
                <button
                  type="button"
                  className="btn compact"
                  onClick={onOpenFull}
                  data-tip="放大到整页对照原件改字段。"
                >
                  全屏核对
                </button>
              )}
              <button
                type="button"
                className={`btn compact${viewMode === 'doc' ? ' primary' : ''}`}
                onClick={() => setView('doc')}
                data-tip="一张单据一张单据地看原件和字段。"
              >
                按单据
              </button>
              <button
                type="button"
                className={`btn compact${viewMode === 'matrix' ? ' primary' : ''}`}
                onClick={() => setView('matrix')}
                data-tip="同一字段在合同/订单/发票之间横比，找出不一致。"
              >
                字段对照
              </button>
              {viewMode === 'matrix' && (
                <>
                  {!fieldsCollapsed && isDirty && (
                    <button
                      type="button"
                      className="btn compact"
                      disabled={busy || !previewDoc}
                      onClick={() => void save()}
                    >
                      保存本单
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn compact"
                    disabled={busy}
                    onClick={() => {
                      setFieldsCollapsed(false)
                      persistPane('gospd.fieldPaneCollapsed', false)
                    }}
                    data-tip="打开右侧字段编辑区，改完后记得保存本单。"
                  >
                    展开字段编辑
                  </button>
                </>
              )}
            </div>
            <div className="toolbar">
              <span className="tip-anchor" data-tip="只补当前业务笔里还空着的字段，不覆盖你已改过的值。">
                <button
                  type="button"
                  className="btn"
                  disabled={gapBusy || busy || !docs.length}
                  onClick={() => void gapFill()}
                >
                  {gapBusy ? '补抽中…' : '补抽当前笔缺失字段'}
                </button>
              </span>
              <span
                className="tip-anchor"
                data-tip={
                  ambOpenCount > 0
                    ? `还有 ${ambOpenCount} 项金额歧义未关闭，关闭后才能确认字段。`
                    : '锁定本笔已核对的字段，后续测试按此取值。'
                }
              >
                <button
                  className="btn primary"
                  disabled={busy || gapBusy || ambOpenCount > 0 || isDirty}
                  onClick={() => void confirmAll()}
                >
                  确认本笔字段
                </button>
              </span>
            </div>
          </div>
          <div className="field-hitl-amount-zone">
            <AmountAmbiguityPanel
              job={job}
              onJob={(j) => {
                onJob(j)
                setMatrixRefreshKey((k) => k + 1)
              }}
              onOpenCount={(count) => {
                setAmbOpenCount(count)
                if (count === 0) {
                  setErr((current) =>
                    /金额歧义|金额待确认/.test(current) ? '' : current,
                  )
                }
              }}
              onFocusFile={(name) => {
                const i = docs.findIndex((d) => d.file_name === name)
                if (i < 0) return
                if (i !== idx && !allowDiscardChanges()) return
                setIdx(i)
                setMatrixDoc(docs[i])
                setHl(null)
                setHlValue('')
                setCaptureOn(false)
              }}
            />
          </div>
          <div
            className={[
              'tri',
              viewMode === 'matrix' ? 'matrix-mode' : '',
              docsCollapsed ? 'docs-collapsed' : '',
              fieldsCollapsed || viewMode === 'matrix' ? 'fields-collapsed' : '',
            ]
              .filter(Boolean)
              .join(' ')}
          >
        <div className={`pane pane-docs${docsCollapsed ? ' is-collapsed' : ''}`}>
          <div className="pane-title">
            {docsCollapsed ? (
              <button
                type="button"
                className="btn compact pane-expand"
                data-tip="展开左侧单据列表。"
                onClick={() => {
                  setDocsCollapsed(false)
                  persistPane('gospd.fieldDocsCollapsed', false)
                }}
              >
                单据 »
              </button>
            ) : (
              <>
                <span>单据列表</span>
                <button
                  type="button"
                  className="btn compact"
                  data-tip="收起单据列表，放大原件预览。"
                  onClick={() => {
                    setDocsCollapsed(true)
                    persistPane('gospd.fieldDocsCollapsed', true)
                  }}
                >
                  «
                </button>
              </>
            )}
          </div>
          {!docsCollapsed && (
            <div className="pane-scroll">
              {docs.map((d, i) => (
                <button
                  key={d.file_name}
                  type="button"
                  className={`doc-item${i === idx ? ' active' : ''}`}
                  onClick={() => {
                    if (i !== idx && !allowDiscardChanges()) return
                    setIdx(i)
                    setMatrixDoc(null)
                    setHl(null)
                    setHlValue('')
                    setCaptureOn(false)
                    setErr('')
                    setMsg('')
                  }}
                >
                  {d.manual_edited ? <span className="badge warn">已改</span> : null}{' '}
                  {documentTypeLabel(d)}
                  <small>{d.file_name}</small>
                </button>
              ))}
            </div>
          )}
        </div>
        {viewMode === 'matrix' && (
          <div className="pane pane-matrix">
            <div className="pane-title">
              <span>字段对照 · 跨单据横比</span>
              <button
                type="button"
                className="btn compact"
                disabled={!dynamicResolution?.comparison_plan}
                aria-expanded={showResolutionDetails}
                onClick={() => setShowResolutionDetails((value) => !value)}
                data-tip="主表保持全部字段横向展开；需要时再查看规则、复算和证据链。"
              >
                {showResolutionDetails ? '收起判断依据' : resolutionBusy ? '判断依据准备中…' : '查看判断依据'}
              </button>
            </div>
            <div className="pane-scroll">
              <FieldComparisonMatrix
                key={`matrix-${matrixRefreshKey}`}
                job={job}
                chainFileNames={chainFileNames}
                onSelectCell={onMatrixCell}
                onJob={onJob}
                draftOverlay={matrixDraftOverlay}
                refreshKey={matrixRefreshKey}
                requiredRows={requiredRows}
              />
              {showResolutionDetails && dynamicResolution?.comparison_plan ? (
                <div className="resolution-workspace resolution-workspace-secondary" aria-label="判断依据">
                  <ExplainableFieldMatrix
                    plan={dynamicResolution.comparison_plan}
                    onExplain={setReasonRow}
                    onSelectEvidence={onResolutionEvidence}
                  />
                  <BusinessChronologyPanel
                    chronology={dynamicResolution.comparison_plan.domains.chronology}
                    onSelectEvidence={onResolutionEvidence}
                  />
                  <section className="resolution-section" aria-labelledby="document-specific-title">
                    <header className="resolution-section-head"><div>
                      <h3 id="document-specific-title">单据专有信息</h3>
                      <p>用于追溯单据身份和业务过程，不计入三单一致性结果。</p>
                    </div></header>
                    <div className="document-specific-grid">
                      {dynamicResolution.comparison_plan.domains.document_specific.map((item) => (
                        <button
                          key={item.row_id}
                          type="button"
                          className="document-specific-item"
                          onClick={() => item.evidence_id && onResolutionEvidence(item.evidence_id)}
                        >
                          <span>{FIELD_CN[item.field_key] || item.label || item.field_key}</span>
                          <strong>{String(item.value ?? '—')}</strong>
                          <small>{item.document_role || '单据'}</small>
                        </button>
                      ))}
                    </div>
                  </section>
                  <ResolutionIssuesPanel
                    issues={dynamicResolution.comparison_plan.domains.issues}
                    onDecision={decideResolutionIssue}
                  />
                </div>
              ) : null}
            </div>
          </div>
        )}
        <div className="pane pane-preview">
          <div className="pane-title">
            <span>
              {captureOn && hl
                ? `取证 · ${FIELD_CN[hl] || hl}`
                : `原件预览 · ${previewDoc ? documentTypeLabel(previewDoc) : '-'}${hl ? ` · ${FIELD_CN[hl] || hl}` : ''}`}
            </span>
            <span className="toolbar">
              {docsCollapsed && (
                <>
                  <select
                    className="field-select compact"
                    value={idx}
                    onChange={(e) => {
                      const nextIndex = Number(e.target.value)
                      if (nextIndex !== idx && !allowDiscardChanges()) return
                      setIdx(nextIndex)
                      setMatrixDoc(null)
                      setHl(null)
                      setHlValue('')
                      setCaptureOn(false)
                    }}
                    data-tip="切换当前预览的单据。"
                    style={{ maxWidth: 160 }}
                  >
                    {docs.map((d, i) => (
                      <option key={d.file_name} value={i}>
                        {documentTypeLabel(d)} · {i + 1}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn compact"
                    data-tip="展开左侧单据列表。"
                    onClick={() => {
                      setDocsCollapsed(false)
                      persistPane('gospd.fieldDocsCollapsed', false)
                    }}
                  >
                    单据
                  </button>
                </>
              )}
              {fieldsCollapsed && (
                <button
                  type="button"
                  className="btn compact"
                  data-tip="展开右侧抽取字段，方便改值。"
                  onClick={() => {
                    setFieldsCollapsed(false)
                    persistPane('gospd.fieldPaneCollapsed', false)
                  }}
                >
                  字段
                </button>
              )}
              {hl && !captureOn && (
                <button
                  type="button"
                  className="btn compact primary"
                  data-tip="在原件上点选或拖框，把看到的文字填进当前字段。"
                  onClick={() => setCaptureOn(true)}
                >
                  取证回填
                </button>
              )}
              {captureOn && (
                <button type="button" className="btn compact" onClick={() => setCaptureOn(false)}>
                  退出取证
                </button>
              )}
              <a
                className="btn compact a-as-btn"
                href={api.fileUrl(job.job_id, previewDoc?.file_name || doc.file_name)}
                target="_blank"
                rel="noreferrer"
              >
                下载原件
              </a>
              {hl && !captureOn && (
                <button
                  type="button"
                  className="btn compact"
                  onClick={() => setHl(null)}
                  data-tip="去掉原件上的字段定位框。"
                >
                  清除高亮
                </button>
              )}
            </span>
          </div>
          <div className="pane-scroll">
            {captureOn && hl && previewDoc ? (
              <CapturePreview
                jobId={job.job_id}
                fileName={previewDoc.file_name}
                fieldKey={hl}
                fieldLabel={FIELD_CN[hl] || hl}
                onApply={(text) => {
                  setHasUserEdited(true)
                  setDraft((d) => ({ ...d, [hl]: text }))
                  setHlValue(text)
                  setMsg(`已从原件取证填入「${FIELD_CN[hl] || hl}」，对照表已更新；请点「保存本单」落库。`)
                }}
                onExit={() => setCaptureOn(false)}
              />
            ) : previewDoc ? (
              <DocPreview
                jobId={job.job_id}
                fileName={previewDoc.file_name}
                highlightField={hl}
                highlightValue={hl ? hlValue : null}
              />
            ) : (
              <p className="preview-empty">请选择单据或对照表单元格</p>
            )}
          </div>
        </div>        <div className={`pane pane-fields${fieldsCollapsed ? ' is-collapsed' : ''}`}>
          <div className="pane-title">
            {fieldsCollapsed ? (
              <button
                type="button"
                className="btn compact pane-expand"
                data-tip="展开右侧抽取字段。"
                onClick={() => {
                  setFieldsCollapsed(false)
                  persistPane('gospd.fieldPaneCollapsed', false)
                }}
              >
                « 字段
              </button>
            ) : (
              <>
                <span>抽取字段 · 关键优先</span>
                <button
                  type="button"
                  className="btn compact"
                  data-tip="收起字段区，放大原件预览。"
                  onClick={() => {
                    setFieldsCollapsed(true)
                    persistPane('gospd.fieldPaneCollapsed', true)
                  }}
                >
                  »
                </button>
              </>
            )}
          </div>
          {!fieldsCollapsed && (
          <div className="pane-scroll">
            {previewDoc?.doc_type === 'other' && !previewDoc.doc_type_confirmed && (
              <div className="type-uncertain-banner" role="status">
                <strong>文件类型不确定，疑似内部存在杂乱的文件类型</strong>
                <span>请逐页查看当前文件，并在下方确认一个仅对本文件生效的具体名称。</span>
              </div>
            )}
            <label className="hint">识别类型</label>
            <select
              className="field-select"
              value={docType}
              onChange={(e) => {
                setHasUserEdited(true)
                setDocType(e.target.value)
              }}
            >
              {Object.entries(DOCUMENT_TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            {docType === 'other' && (
              <div className="custom-document-type">
                <label htmlFor="current-file-document-type">当前文件具体名称</label>
                <input
                  id="current-file-document-type"
                  className="field-input"
                  value={customDocTypeName}
                  placeholder="例如：海运提单"
                  maxLength={80}
                  onChange={(event) => {
                    setHasUserEdited(true)
                    setCustomDocTypeName(event.target.value)
                    if (err === '请填写当前文件的具体单据名称') setErr('')
                  }}
                />
                <p className="hint">仅修改当前文件名称，不新增系统单据类型。</p>
              </div>
            )}
            {previewDoc?.file_name.toLowerCase().endsWith('.pdf') && (
              <div className="manual-mixed-action">
                <button
                  type="button"
                  className="btn compact"
                  disabled={busy}
                  onClick={() => void declareCurrentMixed()}
                >
                  转为混装资料包
                </button>
                <span className="hint">仅在逐页确认同一 PDF 内确有多张不同单据时使用。</span>
              </div>
            )}
            <div className="hint mt-8 mb-8">
              下列为本笔必填字段（随本笔单据类型变化）；点字段名可高亮原件。确认仍覆盖本单全部字段。
            </div>
            {criticalKeys.map(renderFieldRow)}
            {extraKeys.length > 0 && (
              <details className="mt-8 field-more">
                <summary>更多字段（{extraKeys.length}）· 确认时同样生效</summary>
                {extraKeys.map(renderFieldRow)}
              </details>
            )}
            {(activeChain?.missing_fields?.length || emptyCritical.length) > 0 && (
              <p className="err mt-8">
                本笔必填仍空：
                {(activeChain?.missing_fields?.length
                  ? activeChain.missing_fields
                  : emptyCritical
                )
                  .map((k) => FIELD_CN[k] || k)
                  .join('、')}
                。可补录或对照原件后仍确认（须你判断可接受）。
              </p>
            )}
            <label className="hint mt-8">修改理由（可选）</label>
            <input
              className="field-input"
              placeholder="为何改字段 / 改类型"
              value={editReason}
              onChange={(e) => setEditReason(e.target.value)}
            />
            <details className="mt-8">
              <summary>追加字段</summary>
              <input
                className="field-input"
                placeholder="字段名"
                value={addKey}
                onChange={(e) => {
                  setHasUserEdited(true)
                  setAddKey(e.target.value)
                }}
              />
              <input
                className="field-input"
                placeholder="字段值"
                value={addVal}
                onChange={(e) => {
                  setHasUserEdited(true)
                  setAddVal(e.target.value)
                }}
              />
            </details>
            {hl && (
              <details className="mt-8" open>
                <summary>原文摘录 · {FIELD_CN[hl] || hl}</summary>
                <pre className="json-view">
                  {(() => {
                    const raw = String(doc.raw_text || '')
                    const val = String(draft[hl] || doc.fields?.[hl] || '').trim()
                    if (!raw) return '（无 OCR 正文）'
                    if (!val) return raw.slice(0, 800)
                    const i = raw.indexOf(val)
                    if (i < 0) return raw.slice(0, 800)
                    const a = Math.max(0, i - 80)
                    const b = Math.min(raw.length, i + val.length + 80)
                    return raw.slice(a, b)
                  })()}
                </pre>
              </details>
            )}
            <details className="mt-8">
              <summary data-tip="查看该字段原始抽取、规范化候选和最终采纳值，方便对差异。">
                三值轨迹（raw / candidate / accepted）
              </summary>
              {(() => {
                const meta = ((doc as { _field_meta?: Record<string, Record<string, unknown>> })
                  ._field_meta ||
                  ((doc.fields || {}) as { _field_meta?: Record<string, Record<string, unknown>> })
                    ._field_meta ||
                  {}) as Record<string, Record<string, unknown>>
                const entries = Object.entries(meta)
                if (!entries.length) {
                  return <p className="hint mt-8">本单暂无三值元数据（旧批次或未种子化）。</p>
                }
                return (
                  <table className="data-table mt-8">
                    <thead>
                      <tr>
                        <th>字段</th>
                        <th>raw</th>
                        <th>candidate</th>
                        <th>accepted</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.slice(0, 40).map(([k, slot]) => (
                        <tr key={k}>
                          <td>
                            <code>{k}</code>
                          </td>
                          <td className="hint">{String(slot.raw_value ?? '')}</td>
                          <td className="hint">{String(slot.normalized_candidate ?? '')}</td>
                          <td className="hint">{String(slot.accepted_value ?? '')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              })()}
            </details>
            <div className="field-actions">
              {msg && <div className="ok-text">{msg}</div>}
              {err && <div className="err">{err}</div>}
            </div>
          </div>
          )}
        </div>
      </div>
      <FieldReasonDrawer
        open={Boolean(reasonRow)}
        row={reasonRow}
        evidenceNodes={(dynamicResolution?.evidence_nodes || []) as FieldEvidenceNode[]}
        onClose={() => setReasonRow(null)}
        onSelectEvidence={(evidenceId) => {
          onResolutionEvidence(evidenceId)
          setReasonRow(null)
        }}
      />
    </div>
  )
}
