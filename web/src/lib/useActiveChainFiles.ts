import { useEffect, useMemo, useState } from 'react'
import type { ChainInfo } from '../api'
import type { Job } from '../types'
import { docsForChain } from './chainDocs'
import { listChainsCached } from './chainsCache'

export function useActiveChainFiles(job: Job) {
  const [chains, setChains] = useState<ChainInfo[]>([])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await listChainsCached(job)
        if (!cancelled) setChains(r.chains || [])
      } catch {
        if (!cancelled) setChains([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [
    job.job_id,
    job.active_chain_id,
    job.updated_at,
    (job.classified || []).length,
    JSON.stringify(job.goal_ids || []),
  ])

  const activeChain = useMemo(() => {
    const cid = job.active_chain_id
    return (
      chains.find((c) => c.chain_id === cid) ||
      chains.find((c) => c.is_active) ||
      chains[0]
    )
  }, [chains, job.active_chain_id])

  const chainFileNames = activeChain?.file_names?.length ? activeChain.file_names : null

  const chainDocs = useMemo(
    () => docsForChain(job, job.active_chain_id, chainFileNames),
    [job, job.updated_at, chainFileNames],
  )

  return { chains, activeChain, chainFileNames, chainDocs }
}
