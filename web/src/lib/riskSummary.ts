import type { ConclusionTrace, Job } from '../types'
import { activeSample, docsForChain } from './chainDocs'
import { listGospdChainIds } from './chainProgress'
import { countUnverifiedMismatches } from './fieldComparison'
import { fieldText } from './readableFields'
import { cutoffStatus, resultOverallStatus, threeWayMatchStatus } from './testStatus'

export type RiskBucket = {
  id: string
  label: string
  severity: 'block' | 'warn' | 'info'
  chainIds: string[]
  count: number
  step?: string
  /** Gate5 追溯项：点击下钻到具体 finding */
  findingIds?: string[]
}

export type RiskSummary = {
  buckets: RiskBucket[]
  stats: {
    sampleCount: number
    docCount: number
    docTypes: Record<string, number>
    blockingCount: number
    unackedCount: number
    mismatchRows: number
    emptyCritical: number
    sampleAmount?: string
    errorRate?: number
  }
}

const CRITICAL_KEYS = [
  'totalAmount',
  'quantity',
  'postingDate',
  'acceptanceDate',
  'documentNo',
  'buyerName',
  'supplierName',
] as const

function statusBad(st: string): boolean {
  const u = st.toUpperCase()
  return u.includes('FAIL') || u.includes('未通过') || u.includes('WARNING') || u.includes('WARN')
}

function emptyCriticalCount(docs: ReturnType<typeof docsForChain>): number {
  let n = 0
  for (const d of docs) {
    for (const k of CRITICAL_KEYS) {
      if (!fieldText(d, k)) n += 1
    }
  }
  return n
}

export function buildRiskSummary(
  job: Job,
  trace?: ConclusionTrace | null,
  mismatchRows?: number,
  chainFileNames?: string[] | null,
  /** 权威业务笔列表（来自 /chains）；缺省则归链回退 */
  chainIds?: string[] | null,
): RiskSummary {
  const docs = job.classified || []
  const activeDocs = docsForChain(job, job.active_chain_id, chainFileNames)
  // 单据齐套按当前笔；「上传单据」统计必须是全任务
  const docTypes: Record<string, number> = {}
  for (const d of docs) {
    const t = d.doc_type || 'other'
    docTypes[t] = (docTypes[t] || 0) + 1
  }

  const buckets: RiskBucket[] = []
  const chains =
    (chainIds || []).filter((c) => c && c !== '未识别业务号').length > 0
      ? (chainIds || []).filter((c) => c && c !== '未识别业务号')
      : listGospdChainIds(job)
  const sample = activeSample(job)
  const effectiveMismatch =
    mismatchRows ?? countUnverifiedMismatches(job, chainFileNames)

  const empty = emptyCriticalCount(activeDocs)
  if (empty > 0) {
    buckets.push({
      id: 'empty_fields',
      label: '关键字段仍空',
      severity: 'warn',
      chainIds: job.active_chain_id ? [job.active_chain_id] : chains,
      count: empty,
      step: 'field_confirm',
    })
  }

  if (effectiveMismatch > 0) {
    buckets.push({
      id: 'field_mismatch',
      label: '跨单据字段不一致',
      severity: 'warn',
      chainIds: job.active_chain_id ? [job.active_chain_id] : chains,
      count: effectiveMismatch,
      step: 'field_confirm',
    })
  }

  const fieldsOk = Boolean(sample.fields_confirmed ?? job.fields_confirmed)
  if (!fieldsOk && docs.length) {
    buckets.push({
      id: 'fields_hitl',
      label: '字段未人工确认',
      severity: 'block',
      chainIds: job.active_chain_id ? [job.active_chain_id] : chains,
      count: 1,
      step: 'field_confirm',
    })
  }

  const needGate4 = job.plan?.required_steps?.includes('relations_gate4')
  const gate4Ok = Boolean(sample.matching_confirmed ?? job.matching_confirmed)
  if (needGate4 && !gate4Ok) {
    buckets.push({
      id: 'gate4',
      label: '串单勾稽未确认',
      severity: 'block',
      chainIds: job.active_chain_id ? [job.active_chain_id] : chains,
      count: 1,
      step: 'relations_gate4',
    })
  }

  const unackedFindings = (trace?.findings || []).filter((f) => f.blocking && !f.acknowledged)
  if (unackedFindings.length > 0) {
    buckets.push({
      id: 'trace_unacked',
      label: '测试未过待确认',
      severity: 'block',
      chainIds: [
        ...new Set(
          unackedFindings.map((f) => f.chain_id).filter(Boolean) as string[],
        ),
      ].length
        ? [...new Set(unackedFindings.map((f) => f.chain_id).filter(Boolean) as string[])]
        : job.active_chain_id
          ? [job.active_chain_id]
          : chains,
      count: unackedFindings.length,
      step: 'conclusion_gate5',
      findingIds: unackedFindings.map((f) => f.finding_id),
    })
  }

  const pendingAdv = (job.advisory_candidates || []).filter(
    (c) => String(c.status || '').toUpperCase() === 'PROPOSED',
  )
  if (pendingAdv.length > 0) {
    buckets.push({
      id: 'advisory',
      label: '系统观察（顾问，不挡导出）',
      severity: 'info',
      chainIds: chains,
      count: pendingAdv.length,
      step: 'sample_desk',
    })
  }

  const samples = job.gospd_sample_results || {}
  const scanIds =
    chains.length > 0
      ? chains
      : job.active_chain_id
        ? [job.active_chain_id]
        : samples && Object.keys(samples).length
          ? Object.keys(samples)
          : ['_job']
  const threeWayBad: string[] = []
  const cutoffBad: string[] = []
  for (const cid of scanIds) {
    const fromSample =
      cid !== '_job' ? ((samples[cid] || {}) as Record<string, unknown>) : null
    const s =
      fromSample && Object.keys(fromSample).length
        ? fromSample
        : cid === job.active_chain_id || cid === '_job'
          ? (sample as Record<string, unknown>)
          : {}
    const tw = s.three_way || s.three_way_match || (cid === '_job' ? job.three_way : null)
    const cu = s.cutoff_test || (cid === '_job' ? job.cutoff_test : null)
    const twSt = tw ? threeWayMatchStatus(tw) || resultOverallStatus(tw) : ''
    const cuSt = cu
      ? resultOverallStatus(cu) || cutoffStatus(cu)
      : tw
        ? cutoffStatus(tw)
        : ''
    const labelCid = cid === '_job' ? job.active_chain_id || '当前笔' : cid
    if (twSt && statusBad(twSt)) threeWayBad.push(labelCid)
    if (cuSt && statusBad(cuSt)) cutoffBad.push(labelCid)
  }
  if (threeWayBad.length > 0) {
    buckets.push({
      id: 'three_way',
      label: '三单不符',
      severity: 'warn',
      chainIds: [...new Set(threeWayBad)],
      count: threeWayBad.length,
      step: 'three_way_cutoff',
    })
  }
  if (cutoffBad.length > 0) {
    buckets.push({
      id: 'cutoff',
      label: '截止日风险',
      severity: 'warn',
      chainIds: [...new Set(cutoffBad)],
      count: cutoffBad.length,
      step: 'three_way_cutoff',
    })
  }

  const inv = activeDocs.find((d) => d.doc_type === 'invoice')
  const sampleAmount = inv ? fieldText(inv, 'totalAmount') : ''
  const sampleCount = chains.length
  const failN = trace?.blocking_count ?? unackedFindings.length
  const errorRate = sampleCount > 0 ? Math.round((failN / sampleCount) * 100) : 0

  return {
    buckets,
    stats: {
      sampleCount,
      docCount: docs.length,
      docTypes,
      blockingCount: trace?.blocking_count ?? buckets.filter((b) => b.severity === 'block').length,
      unackedCount:
        trace?.unacked_blocking_count_active ??
        trace?.unacked_blocking_count ??
        unackedFindings.length,
      mismatchRows: effectiveMismatch,
      emptyCritical: empty,
      sampleAmount: sampleAmount || undefined,
      errorRate,
    },
  }
}

export function countChainIssueHint(job: Job, chainId: string, fileNames?: string[] | null): number {
  const docs = docsForChain(job, chainId, fileNames)
  let n = 0
  for (const d of docs) {
    for (const k of CRITICAL_KEYS) {
      if (!fieldText(d, k)) n += 1
    }
  }
  const sample = (job.gospd_sample_results || {})[chainId] || {}
  for (const k of ['evidence', 'amount_test', 'contract_terms'] as const) {
    const o = sample[k] as { status?: string; overall_status?: string } | undefined
    if (!o) continue
    const st = String(o.status || o.overall_status || '')
    if (statusBad(st)) n += 1
  }
  const tw = sample.three_way || sample.three_way_match
  const twSt = tw ? threeWayMatchStatus(tw) || resultOverallStatus(tw) : ''
  if (twSt && statusBad(twSt)) n += 1
  const cu = sample.cutoff_test
  const cuSt = cu
    ? resultOverallStatus(cu) || cutoffStatus(cu)
    : tw
      ? cutoffStatus(tw)
      : ''
  if (cuSt && statusBad(cuSt)) n += 1
  if (job.plan?.required_steps?.includes('relations_gate4') && !sample.matching_confirmed) {
    n += 1
  }
  return n
}
