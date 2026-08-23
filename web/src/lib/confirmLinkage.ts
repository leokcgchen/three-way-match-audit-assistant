import { api } from '../api'
import type { Job, RelationRow } from '../types'
import { activeSample, isGospdJob } from './chainDocs'

export type LinkageConfirmResult = {
  job: Job
  fields_confirmed?: boolean
  matching_confirmed: boolean
  message: string
  next_action?: string
  pending_relation_count?: number
  evidence_seeded?: boolean
}

function pendingRelations(job: Job): RelationRow[] {
  const sample = isGospdJob(job) ? activeSample(job) : null
  const rels = ((sample?.relations as RelationRow[] | undefined) ||
    job.relations ||
    []) as RelationRow[]
  return rels.filter((r) => (r.status || 'PROPOSED') === 'PROPOSED')
}

/** 主路径：一并确认建议关系 + 本笔勾稽（字段已齐时不重复折腾） */
export async function confirmLinkagePrimary(job: Job): Promise<LinkageConfirmResult> {
  const pending = pendingRelations(job)
  if (pending.length > 0) {
    // 双保险：先走 verify-all 写顶层，再走后端编排（含分笔镜像）
    try {
      await api.verifyAllRelations(job.job_id, '人工核对一并确认建议关系')
    } catch {
      /* 后端 confirm_chain_linkage 仍会 accept */
    }
  }
  return api.confirmChainLinkage(job.job_id, {
    auto_evidence: true,
    auto_accept_relations: true,
  })
}
