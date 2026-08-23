import { api, type ChainInfo, type DeskLights } from '../api'
import type { Job } from '../types'

export type ChainsResponse = {
  chains: ChainInfo[]
  lights?: DeskLights
  active_chain_id?: string | null
  gospd_mode?: boolean
  sample_population?: Job['sample_population']
}

type CacheEntry = {
  jobId: string
  stamp: string
  data: ChainsResponse
  at: number
}

let cache: CacheEntry | null = null
let inflight: Promise<ChainsResponse> | null = null
let inflightKey = ''

function stampOf(job: Pick<Job, 'job_id' | 'updated_at' | 'active_chain_id'> | string, updatedAt?: string | null) {
  if (typeof job === 'string') return `${job}|${updatedAt || ''}`
  return `${job.job_id}|${job.updated_at || ''}|${job.active_chain_id || ''}`
}

/**
 * 同戳一直复用，直到 invalidate（写操作）或 force。
 * 纯切页不设短 TTL，避免「有时快有时慢」。
 */
export function listChainsCached(
  job: Pick<Job, 'job_id' | 'updated_at' | 'active_chain_id'> | string,
  opts?: { updatedAt?: string | null; force?: boolean; ttlMs?: number },
): Promise<ChainsResponse> {
  const jobId = typeof job === 'string' ? job : job.job_id
  const stamp = stampOf(job, opts?.updatedAt)
  const ttl = opts?.ttlMs ?? Number.POSITIVE_INFINITY
  const now = Date.now()
  if (
    !opts?.force &&
    cache &&
    cache.jobId === jobId &&
    cache.stamp === stamp &&
    (ttl === Number.POSITIVE_INFINITY || now - cache.at < ttl)
  ) {
    return Promise.resolve(cache.data)
  }
  if (!opts?.force && inflight && inflightKey === stamp) {
    return inflight
  }
  inflightKey = stamp
  inflight = api
    .listChains(jobId)
    .then((data) => {
      cache = { jobId, stamp, data, at: Date.now() }
      return data
    })
    .finally(() => {
      if (inflightKey === stamp) {
        inflight = null
        inflightKey = ''
      }
    })
  return inflight
}

export function invalidateChainsCache(jobId?: string) {
  if (!jobId || (cache && cache.jobId === jobId)) cache = null
  inflight = null
  inflightKey = ''
}

/** 同步读缓存（保活页首次渲染可免转圈） */
export function peekChainsCache(
  job: Pick<Job, 'job_id' | 'updated_at' | 'active_chain_id'>,
): ChainsResponse | null {
  const stamp = stampOf(job)
  if (cache && cache.jobId === job.job_id && cache.stamp === stamp) return cache.data
  return null
}
