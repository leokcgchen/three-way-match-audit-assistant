import { useEffect, useRef, useState } from 'react'
import { api, type AmountAmbiguity } from '../api'
import type { Job } from '../types'

type Props = {
  job: Job
  onJob: (j: Job) => void
  onOpenCount?: (n: number) => void
  /** 点开某张金额卡时，把下方预览切到对应单据 */
  onFocusFile?: (fileName: string) => void
}

/**
 * 金额待确认面板。
 * 默认折叠成一条，避免挡住字段对照；展开后再选候选。
 */
export function AmountAmbiguityPanel({ job, onJob, onOpenCount, onFocusFile }: Props) {
  const [items, setItems] = useState<AmountAmbiguity[]>([])
  const [busyId, setBusyId] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [manual, setManual] = useState<Record<string, string>>({})
  const [expandReason, setExpandReason] = useState<Record<string, boolean>>({})
  /** 有待确认时默认展开，便于直接点「采用」；可手动收起腾空间 */
  const [expanded, setExpanded] = useState(true)
  const chainId = job.active_chain_id || undefined
  const loadGen = useRef(0)
  const activeChainRef = useRef(chainId)
  activeChainRef.current = chainId

  const isCurrentChainRequest = (requestChain: string | undefined, gen: number) =>
    requestChain === activeChainRef.current && gen === loadGen.current

  const applyItems = (next: AmountAmbiguity[]) => {
    setItems(next)
    onOpenCount?.(next.length)
    // 仍有待确认则保持展开；全部确认完再收起
    setExpanded(next.length > 0)
  }

  const load = async (opts?: { rescan?: boolean }) => {
    const gen = ++loadGen.current
    setLoading(true)
    try {
      const res = await api.listAmountAmbiguities(job.job_id, chainId, {
        rescan: opts?.rescan === true,
      })
      if (!isCurrentChainRequest(chainId, gen)) return []
      applyItems(res.items || [])
      setErr('')
      return res.items || []
    } catch (e) {
      if (!isCurrentChainRequest(chainId, gen)) return []
      applyItems([])
      setErr(e instanceof Error ? e.message : String(e))
      return []
    } finally {
      if (isCurrentChainRequest(chainId, gen)) setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    // A previous chain's open cards must never keep the new chain's confirm
    // button disabled while its request is in flight.
    applyItems([])
    setBusyId('')
    setErr('')
    setExpanded(true)
    void (async () => {
      await load({ rescan: false })
      if (cancelled) return
    })()
    return () => {
      cancelled = true
      loadGen.current += 1
    }
    // 刻意不含 job.updated_at，避免采用后循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, job.active_chain_id])

  if (!items.length && !err && !loading) return null

  const focusRow = (row: AmountAmbiguity) => {
    const name = String(row.file_name || '').trim()
    if (name) onFocusFile?.(name)
  }

  const openPanel = () => {
    setExpanded(true)
    if (items[0]) focusRow(items[0])
  }

  const decide = async (
    row: AmountAmbiguity,
    decision: 'ACCEPT_CANDIDATE' | 'MANUAL_VALUE' | 'DEFER',
    candidateId?: string,
  ) => {
    const requestChain = chainId
    const requestGen = loadGen.current
    setBusyId(row.ambiguity_id)
    setErr('')
    focusRow(row)
    try {
      const body =
        decision === 'MANUAL_VALUE'
          ? {
              decision,
              value: manual[row.ambiguity_id],
              reason: '字段页手工录入',
            }
          : {
              decision,
              candidate_id: candidateId,
              reason: decision === 'DEFER' ? '暂存' : '采用候选',
            }
      const out = await api.decideAmountAmbiguity(job.job_id, row.ambiguity_id, body)
      if (!isCurrentChainRequest(requestChain, requestGen)) return
      onJob(out.job)
      if (decision === 'ACCEPT_CANDIDATE' || decision === 'MANUAL_VALUE') {
        applyItems(items.filter((x) => x.ambiguity_id !== row.ambiguity_id))
      }
      await load({ rescan: false })
    } catch (e) {
      if (!isCurrentChainRequest(requestChain, requestGen)) return
      setErr(e instanceof Error ? e.message : String(e))
      await load({ rescan: false })
    } finally {
      if (isCurrentChainRequest(requestChain, requestGen)) setBusyId('')
    }
  }

  const rescanAndEnrich = async () => {
    const requestChain = chainId
    const requestGen = loadGen.current
    setLoading(true)
    setErr('')
    try {
      const out = await api.scanAmountAmbiguities(job.job_id, chainId)
      if (!isCurrentChainRequest(requestChain, requestGen)) return
      applyItems(out.items || [])
      onJob(out.job)
    } catch (e) {
      if (!isCurrentChainRequest(requestChain, requestGen)) return
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      if (isCurrentChainRequest(requestChain, requestGen)) setLoading(false)
    }
  }

  const aiReview = async (row: AmountAmbiguity) => {
    const requestChain = chainId
    const requestGen = loadGen.current
    setBusyId(row.ambiguity_id)
    setErr('')
    focusRow(row)
    try {
      const out = await api.aiReviewAmountAmbiguity(job.job_id, row.ambiguity_id)
      if (!isCurrentChainRequest(requestChain, requestGen)) return
      onJob(out.job)
      await load({ rescan: false })
    } catch (e) {
      if (!isCurrentChainRequest(requestChain, requestGen)) return
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      if (isCurrentChainRequest(requestChain, requestGen)) setBusyId('')
    }
  }

  const summary = items
    .slice(0, 2)
    .map((r) => `${r.file_name?.split(/[/\\]/).pop() || r.file_name}·${r.field_name || r.field_key}`)
    .join('；')

  if (!expanded) {
    return (
      <section className="amount-amb-panel is-collapsed" aria-live="polite">
        <div className="amount-amb-strip">
          <div>
            <span className="eyebrow">金额待确认</span>
            <strong>待确认 {items.length}</strong>
            <p className="hint">
              {loading
                ? '加载中…'
                : summary
                  ? `${summary}${items.length > 2 ? '…' : ''} · 先对照下方字段，再点开选金额`
                  : '有金额歧义需人工选择'}
            </p>
            {err && <p className="err">{err}</p>}
          </div>
          <button
            type="button"
            className="btn compact primary"
            disabled={loading || !items.length}
            onClick={openPanel}
            data-tip="展开后选候选或手工录入；下方字段对照会继续可见。"
          >
            展开处理
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="amount-amb-panel is-expanded" aria-live="polite">
      <div className="amount-amb-head">
        <div>
          <span className="eyebrow">金额待确认</span>
          <strong>待确认 {items.length}</strong>
          <p>
            选候选或手工录入后写入字段；下方对照表可一起看。
            {loading ? ' 加载中…' : ''}
          </p>
        </div>
        <div className="toolbar">
          <button
            type="button"
            className="btn compact"
            disabled={loading || !!busyId}
            onClick={() => setExpanded(false)}
            data-tip="收起金额区，腾出空间看字段对照。"
          >
            收起
          </button>
          <button
            type="button"
            className="btn compact"
            disabled={loading || !!busyId}
            onClick={() => void rescanAndEnrich()}
            data-tip="重扫 OCR 字段并重新跑视觉建议（较慢，一般不必点）"
          >
            重扫增强
          </button>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      <div className="amount-amb-list">
        {items.map((row) => {
          const rec = row.ai_recommendation?.candidate_id
          const busy = busyId === row.ambiguity_id
          const title = row.field_name || row.field_key
          const reason = String(row.ai_recommendation?.reason || '').trim()
          const reasonOpen = !!expandReason[row.ambiguity_id]
          const reasonShort = reason.length > 90 && !reasonOpen ? `${reason.slice(0, 90)}…` : reason
          return (
            <article
              key={row.ambiguity_id}
              className="amount-amb-card"
              onClick={() => focusRow(row)}
            >
              <header>
                <strong>
                  {row.file_name} · {title}
                  <span className="hint"> ({row.field_key})</span>
                </strong>
                <span className="toolbar">
                  <span className="badge pending">{row.status}</span>
                  <button
                    type="button"
                    className="btn compact"
                    onClick={(e) => {
                      e.stopPropagation()
                      focusRow(row)
                    }}
                    data-tip="切换到这张单据的原件预览，核对金额候选。"
                  >
                    查看原件
                  </button>
                </span>
              </header>
              <p className="hint amount-amb-reason">
                触发：{(row.trigger_reasons || []).join('、') || '—'}
                {reasonShort ? ` · AI：${reasonShort}` : ''}
                {reason.length > 90 ? (
                  <button
                    type="button"
                    className="btn-link"
                    onClick={(e) => {
                      e.stopPropagation()
                      setExpandReason((m) => ({ ...m, [row.ambiguity_id]: !reasonOpen }))
                    }}
                  >
                    {reasonOpen ? '收起' : '展开'}
                  </button>
                ) : null}
              </p>
              <div className="amount-amb-cands">
                {(row.candidates || []).map((c) => (
                  <div
                    key={c.candidate_id}
                    className={`amount-amb-cand${rec === c.candidate_id ? ' recommended' : ''}`}
                  >
                    <div>
                      <strong>
                        {c.label || c.candidate_id} · {String(c.value)}
                      </strong>
                      <div className="hint">
                        {c.raw_value || ''}
                        {rec === c.candidate_id ? ' · AI推荐' : ''}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn compact primary"
                      disabled={busy || loading}
                      onClick={(e) => {
                        e.stopPropagation()
                        void decide(row, 'ACCEPT_CANDIDATE', c.candidate_id)
                      }}
                      data-tip="用这个候选金额作为该字段的正式值。"
                    >
                      采用
                    </button>
                  </div>
                ))}
              </div>
              <div className="amount-amb-actions" onClick={(e) => e.stopPropagation()}>
                <input
                  className="field-input"
                  placeholder="手工录入金额"
                  value={manual[row.ambiguity_id] || ''}
                  onChange={(e) =>
                    setManual((m) => ({ ...m, [row.ambiguity_id]: e.target.value }))
                  }
                />
                <button
                  type="button"
                  className="btn compact"
                  disabled={busy || loading || !manual[row.ambiguity_id]}
                  onClick={() => void decide(row, 'MANUAL_VALUE')}
                  data-tip="用你输入的金额覆盖候选，写入该字段。"
                >
                  手工确认
                </button>
                <button
                  type="button"
                  className="btn compact"
                  disabled={busy || loading}
                  onClick={() => void decide(row, 'DEFER')}
                  data-tip="先不拍板，稍后回来再选。"
                >
                  暂存
                </button>
                <button
                  type="button"
                  className="btn compact"
                  disabled={busy || loading}
                  onClick={() => void aiReview(row)}
                  data-tip="手动重跑视觉模型（识别阶段已预跑时可不必点）"
                >
                  视觉复核
                </button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
