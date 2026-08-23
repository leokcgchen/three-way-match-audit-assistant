import { api } from '../api'
import type { ConclusionTrace, Job } from '../types'

type CacheEntry = {
  key: string
  data: ConclusionTrace
  at: number
}

let cache: CacheEntry | null = null
let inflight: Promise<ConclusionTrace> | null = null
let inflightKey = ''

function stampOf(
  job: Pick<Job, 'job_id' | 'updated_at' | 'active_chain_id' | 'finding_acknowledgements'>,
  chainId: string,
): string {
  const acks = job.finding_acknowledgements || {}
  const ackSig = Object.keys(acks)
    .sort()
    .map((k) => `${k}:${(acks as Record<string, { genuine?: boolean }>)[k]?.genuine ? 1 : 0}`)
    .join(',')
  return `${job.job_id}|${job.updated_at || ''}|${chainId}|${ackSig}`
}

/** 同戳一直复用，直到 invalidate；纯切页不再因 8s TTL 变慢。 */
export function conclusionTraceCached(
  job: Pick<Job, 'job_id' | 'updated_at' | 'active_chain_id' | 'finding_acknowledgements'>,
  opts?: { chainId?: string | null; force?: boolean; ttlMs?: number },
): Promise<ConclusionTrace> {
  const chainId = String(opts?.chainId || job.active_chain_id || '').trim()
  const key = stampOf(job, chainId)
  const ttl = opts?.ttlMs ?? Number.POSITIVE_INFINITY
  const now = Date.now()
  if (!opts?.force && cache && cache.key === key && (ttl === Number.POSITIVE_INFINITY || now - cache.at < ttl)) {
    return Promise.resolve(cache.data)
  }
  if (!opts?.force && inflight && inflightKey === key) {
    return inflight
  }
  inflightKey = key
  inflight = api
    .conclusionTrace(job.job_id, chainId || undefined)
    .then((data) => {
      cache = { key, data, at: Date.now() }
      return data
    })
    .finally(() => {
      if (inflightKey === key) {
        inflight = null
        inflightKey = ''
      }
    })
  return inflight
}

export function invalidateConclusionTraceCache(jobId?: string) {
  if (!jobId || (cache && cache.key.startsWith(`${jobId}|`))) {
    cache = null
  }
  inflight = null
  inflightKey = ''
}

export function peekConclusionTraceCache(
  job: Pick<Job, 'job_id' | 'updated_at' | 'active_chain_id' | 'finding_acknowledgements'>,
  chainId?: string | null,
): ConclusionTrace | null {
  const key = stampOf(job, String(chainId || job.active_chain_id || '').trim())
  if (cache && cache.key === key) return cache.data
  return null
}
