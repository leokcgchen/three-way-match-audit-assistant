import { useEffect, useState } from 'react'
import type { Job } from '../types'
import { listGospdChainIds } from './chainProgress'
import { listChainsCached } from './chainsCache'

/** 权威业务笔列表：优先 /chains（短时去重），回退归链。 */
export function useJobChainIds(job: Job | null | undefined): string[] {
  const [chainIds, setChainIds] = useState<string[]>(() =>
    job ? listGospdChainIds(job) : [],
  )

  useEffect(() => {
    if (!job?.job_id) {
      setChainIds([])
      return
    }
    let cancelled = false
    setChainIds(listGospdChainIds(job))
    ;(async () => {
      try {
        const r = await listChainsCached(job)
        if (cancelled) return
        const ids = (r.chains || [])
          .map((c) => c.chain_id)
          .filter((id) => id && id !== '未识别业务号')
        setChainIds(ids.length ? ids : listGospdChainIds(job))
      } catch {
        if (!cancelled) setChainIds(listGospdChainIds(job))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [
    job?.job_id,
    job?.active_chain_id,
    (job?.classified || []).length,
    job?.fields_confirmed,
    job?.matching_confirmed,
    job?.conclusion_confirmed,
    job?.updated_at,
  ])

  return chainIds
}
