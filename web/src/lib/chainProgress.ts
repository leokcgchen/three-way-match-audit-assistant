/** GOSPD 多笔：按「步骤横扫」统计各笔进度（先做完同一步再进下一步）。 */

import type { Job } from '../types'
import { activeSample, isGospdJob } from './chainDocs'
import { resolveJobChainIds } from './chainGroup'
import { resultOverallStatus } from './testStatus'

export type ChainStepFlags = {
  chainId: string
  fieldsOk: boolean
  evidenceOk: boolean
  gate4Ok: boolean
  contractOk: boolean
  amountOk: boolean
  threeOk: boolean
  testsOk: boolean
  gate5Ok: boolean
  missingTests: string[]
}

function testDone(ctx: Record<string, unknown>, key: string): boolean {
  const v = ctx[key]
  return Boolean(v && typeof v === 'object')
}

/**
 * 从 job 推断业务链 ID（无 /chains 时的回退）。
 * 必须与后端归链一致：同笔 SO+HT 合并，禁止把合同号拆成幽灵笔。
 */
export function listGospdChainIds(job: Job, apiChainIds?: string[] | null): string[] {
  return resolveJobChainIds(job, apiChainIds)
}

export function sampleForChain(job: Job, chainId: string): Record<string, unknown> {
  const cid = String(chainId || '').trim()
  if (!cid) return activeSample(job)
  const raw = job.gospd_sample_results?.[cid]
  if (raw && typeof raw === 'object') {
    const s = { ...(raw as Record<string, unknown>) }
    if (s.fields_confirmed == null) {
      s.fields_confirmed = Boolean(
        s.matching_confirmed ||
          s.evidence ||
          s.three_way ||
          s.amount_test ||
          s.contract_terms ||
          s.conclusion_confirmed,
      )
    }
    if (s.matching_confirmed == null && cid === String(job.active_chain_id || '')) {
      s.matching_confirmed = Boolean(job.matching_confirmed)
    }
    if (s.conclusion_confirmed == null) s.conclusion_confirmed = false
    return s
  }
  // 尚无 sample：仅当前笔可回退顶层镜像
  if (cid === String(job.active_chain_id || '')) return activeSample(job)
  return {
    fields_confirmed: false,
    matching_confirmed: false,
    conclusion_confirmed: false,
  }
}

export function chainStepFlags(job: Job, chainId: string): ChainStepFlags {
  const required = job.plan?.required_steps || []
  const needGate4 = required.includes('relations_gate4')
  const needGate5 = required.includes('conclusion_gate5')
  const needEvidence = required.includes('evidence_match')
  const needAmount = required.includes('amount_test')
  const needContract = required.includes('contract_terms')
  const needThree = required.includes('three_way_cutoff')

  const s = sampleForChain(job, chainId)
  const fieldsOk = Boolean(s.fields_confirmed)
  const gate4Ok =
    !needGate4 ||
    Boolean(
      s.matching_confirmed ||
        (chainId === String(job.active_chain_id || '') && job.matching_confirmed),
    )
  const evidenceOk = !needEvidence || testDone(s, 'evidence')
  const contractOk = !needContract || testDone(s, 'contract_terms')
  const amountOk = !needAmount || testDone(s, 'amount_test')
  const threeOk = !needThree || testDone(s, 'three_way')
  const missingTests: string[] = []
  if (needEvidence && !evidenceOk) missingTests.push('证据')
  if (needContract && !contractOk) missingTests.push('合同条款')
  if (needAmount && !amountOk) missingTests.push('金额')
  if (needThree && !threeOk) missingTests.push('三单+截止')
  const testsOk = missingTests.length === 0
  // 证据算在「跑测试」横扫里；Gate4 前必须先有证据
  const gate5Ok = !needGate5 || Boolean(s.conclusion_confirmed)

  return {
    chainId,
    fieldsOk,
    evidenceOk,
    gate4Ok,
    contractOk,
    amountOk,
    threeOk,
    testsOk,
    gate5Ok,
    missingTests,
  }
}

export type SweepPhase =
  | 'fields'
  | 'evidence'
  | 'gate4'
  | 'tests'
  | 'gate5'
  | 'export'
  | 'done'

/** 多笔横扫：全局最早未完成步骤 */
export function earliestSweepPhase(
  job: Job,
  chainIds: string[],
): { phase: SweepPhase; pending: string[]; flags: ChainStepFlags[] } {
  const required = job.plan?.required_steps || []
  const needGate4 = required.includes('relations_gate4')
  const needGate5 = required.includes('conclusion_gate5')
  const needExport = required.includes('workbook_export')
  const needEvidence = required.includes('evidence_match')

  const flags = chainIds.map((cid) => chainStepFlags(job, cid))
  const pendingOf = (pred: (f: ChainStepFlags) => boolean) =>
    flags.filter(pred).map((f) => f.chainId)

  let pending = pendingOf((f) => !f.fieldsOk)
  if (pending.length) return { phase: 'fields', pending, flags }

  if (needEvidence) {
    pending = pendingOf((f) => !f.evidenceOk)
    if (pending.length) return { phase: 'evidence', pending, flags }
  }

  if (needGate4) {
    pending = pendingOf((f) => !f.gate4Ok)
    if (pending.length) return { phase: 'gate4', pending, flags }
  }

  pending = pendingOf((f) => !f.testsOk)
  if (pending.length) return { phase: 'tests', pending, flags }

  if (needGate5) {
    pending = pendingOf((f) => !f.gate5Ok)
    if (pending.length) return { phase: 'gate5', pending, flags }
  }

  const exported = Boolean(job.workbook_path || (job.workbook_paths || []).length)
  if (needExport && !exported) return { phase: 'export', pending: [], flags }

  return { phase: 'done', pending: [], flags }
}

export function isMultiChainJob(job: Job, chainIds?: string[]): boolean {
  if (!isGospdJob(job)) return false
  const ids = chainIds?.length ? chainIds : listGospdChainIds(job)
  return ids.length > 1
}

export function chainHasFail(job: Job, chainId: string): boolean {
  const s = sampleForChain(job, chainId)
  for (const key of ['evidence', 'amount_test', 'contract_terms', 'three_way'] as const) {
    if (!s[key]) continue
    const st = resultOverallStatus(s[key]).toUpperCase()
    if (st.includes('FAIL') || st.includes('未通过') || st.includes('ERROR')) return true
  }
  return false
}
