import type { ClassifiedDoc, Job } from '../types'

/** 当前笔分笔测试结果（GOSPD 读 sample，否则读 job 顶层镜像） */
export function activeSample(job: Job): Record<string, unknown> {
  const cid = String(job.active_chain_id || '').trim()
  const raw =
    cid && job.gospd_sample_results?.[cid]
      ? ({ ...(job.gospd_sample_results[cid] as Record<string, unknown>) } as Record<
          string,
          unknown
        >)
      : null
  if (raw) {
    // 兼容旧样本无 fields_confirmed：有测过/确认过则视为该笔已核对字段
    if (raw.fields_confirmed == null) {
      raw.fields_confirmed = Boolean(
        raw.matching_confirmed ||
          raw.evidence ||
          raw.three_way ||
          raw.amount_test ||
          raw.contract_terms ||
          raw.conclusion_confirmed,
      )
    }
    if (raw.matching_confirmed == null) {
      // 仅当前笔可回退顶层 Gate4，避免多笔误判
      const active = String(job.active_chain_id || '').trim()
      raw.matching_confirmed = Boolean(job.matching_confirmed) && (!cid || cid === active)
    }
    if (raw.conclusion_confirmed == null) {
      raw.conclusion_confirmed = false
    }
    return raw
  }
  return {
    evidence: job.evidence,
    amount_test: job.amount_test,
    contract_terms: job.contract_terms,
    three_way: job.three_way,
    three_way_match: job.three_way_match,
    cutoff_test: job.cutoff_test,
    matching_confirmed: job.matching_confirmed,
    fields_confirmed: job.fields_confirmed,
    conclusion_confirmed: job.conclusion_confirmed,
  }
}

export function isGospdJob(job: Job): boolean {
  const goals = job.plan?.goal_ids || job.goal_ids || []
  return goals.some((g) =>
    ['gospd01010', 'gospd01030', 'gospd01010_2', 'gospd01010_3', 'gospd01010_4'].includes(g),
  )
}

/**
 * 按后端 /chains 返回的 file_names 筛当前笔单据（与 group_classified_by_chain 同源）。
 * 无 file_names 时回退文件名/字段启发式。
 */
export function docsForChain(
  job: Job,
  chainId?: string | null,
  chainFileNames?: string[] | null,
): ClassifiedDoc[] {
  const all = job.classified || []
  const cid = String(chainId ?? job.active_chain_id ?? '').trim()

  if (chainFileNames?.length) {
    const set = new Set(chainFileNames)
    const hit = all.filter((d) => set.has(d.file_name))
    if (hit.length) return hit
  }

  if (!cid || cid === '未识别业务号') return all

  const hit = all.filter((d) => {
    if (d.file_name?.includes(cid)) return true
    const fields = d.fields || {}
    const keys = [
      fields.orderNo,
      fields.contractNo,
      fields.documentNo,
      fields.invoiceNo,
      d.ledger_matched_biz_id,
    ]
      .map((x) => String(x || '').trim())
      .filter(Boolean)
    return keys.some((k) => k.includes(cid) || cid.includes(k))
  })
  return hit.length ? hit : all
}

export function docByTypes(
  docs: ClassifiedDoc[],
  ...docTypes: string[]
): ClassifiedDoc | undefined {
  for (const t of docTypes) {
    const hit = docs.find((d) => d.doc_type === t)
    if (hit) return hit
  }
  return undefined
}

/** @deprecated use docsForChain */
export function docsForActiveChain(job: Job, chainFileNames?: string[] | null): ClassifiedDoc[] {
  return docsForChain(job, job.active_chain_id, chainFileNames)
}
