import { useEffect, useMemo, useState } from 'react'
import { api, type ChainInfo } from '../api'
import type { Job } from '../types'
import { earliestSweepPhase, listGospdChainIds } from '../lib/chainProgress'
import { countChainIssueHint } from '../lib/riskSummary'
import { cutoffStatus, threeWayMatchStatus } from '../lib/testStatus'
import { listChainsCached } from '../lib/chainsCache'

type Props = {
  job: Job
  onJob: (j: Job) => void
  /** 结论页：只留切笔下拉，不占结论区高度 */
  compact?: boolean
}

export function ChainPicker({ job, onJob, compact = false }: Props) {
  const [chains, setChains] = useState<ChainInfo[]>([])
  const [gospd, setGospd] = useState(false)
  const [err, setErr] = useState('')

  const required = new Set(job.plan?.required_steps || [])
  const needContract = required.has('contract_terms')
  const needAmount = required.has('amount_test')
  const needThree = required.has('three_way_cutoff')
  const needGate4 = required.has('relations_gate4')

  const reload = async () => {
    try {
      const r = await listChainsCached(job)
      setChains(r.chains || [])
      setGospd(Boolean(r.gospd_mode))
      setErr('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    void reload()
  }, [
    job.job_id,
    job.active_chain_id,
    (job.classified || []).length,
    JSON.stringify(job.goal_ids || []),
    job.contract_terms ? 1 : 0,
    job.amount_test ? 1 : 0,
    job.three_way ? 1 : 0,
    job.matching_confirmed ? 1 : 0,
  ])

  const active = job.active_chain_id || chains.find((c) => c.is_active)?.chain_id || ''
  const sweep = useMemo(() => {
    const ids =
      chains.length > 0
        ? chains.map((c) => c.chain_id).filter((id) => id !== '未识别业务号')
        : listGospdChainIds(job)
    if (ids.length <= 1) return null
    return earliestSweepPhase(job, ids)
  }, [job, chains])

  const onSelect = async (chainId: string) => {
    if (!chainId || chainId === active) return
    try {
      onJob(await api.setActiveChain(job.job_id, chainId))
      await reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const flagText = (c: ChainInfo) => {
    const parts: string[] = []
    if (c.in_sample_population === true) parts.push('样内')
    else if (c.in_sample_population === false) parts.push('样外')
    if (needGate4) parts.push(c.matching_confirmed ? '勾稽✓' : '勾稽·')
    if (needContract) parts.push(c.has_contract ? '条款✓' : '条款·')
    if (needAmount) parts.push(c.has_amount ? '金额✓' : '金额·')
    if (needThree) {
      const sample = (job.gospd_sample_results || {})[c.chain_id] || {}
      const tw = sample.three_way
      const cu = sample.cutoff_test
      const m = threeWayMatchStatus(tw).toUpperCase()
      const cut = (cutoffStatus(cu) || cutoffStatus(tw)).toUpperCase()
      if (!c.has_three_way && !tw) parts.push('三单·')
      else if (m.includes('FAIL') || m.includes('未通过')) parts.push('三单✗')
      else if (m.includes('WARN')) parts.push('三单!')
      else if (m) parts.push('三单✓')
      else parts.push('三单·')
      if (cut.includes('FAIL') || cut.includes('未通过')) parts.push('截止✗')
      else if (cut.includes('WARN')) parts.push('截止!')
      else if (cut && !cut.includes('SKIP') && cut !== 'NOT_TESTED') parts.push('截止✓')
    }
    return parts.join(' ') || '—'
  }

  if (!gospd) return null

  if (compact) {
    const pendingN = sweep?.pending.length || 0
    return (
      <div className="chain-switch">
        <label htmlFor="chain-switch-select">本笔</label>
        {!chains.length ? (
          <p className="preview-empty">还没有样本笔。</p>
        ) : (
          <select
            id="chain-switch-select"
            className="field-select"
            value={active}
            onChange={(e) => void onSelect(e.target.value)}
            aria-label="切换业务笔"
          >
            {chains.map((c) => (
              <option key={c.chain_id} value={c.chain_id}>
                {c.chain_id} · {flagText(c)}
              </option>
            ))}
          </select>
        )}
        {pendingN > 0 ? <span className="hint">另有 {pendingN} 笔本步未完</span> : null}
        {err ? <p className="err">{err}</p> : null}
      </div>
    )
  }

  return (
    <div className="plan-box mb-12">
      <strong>当前业务笔</strong>
      <div className="hint mt-8">
        先在工作台用抽样清单立笔，再传凭证。齐则绿灯；缺字段/多金额红灯进核对。随时可切笔。
        {sweep && sweep.pending.length > 0 ? (
          <>
            <br />
            本步待办（{sweep.phase}）：{sweep.pending.join('、')}
          </>
        ) : null}
      </div>
      {err && <p className="err">{err}</p>}
      {!chains.length ? (
        <p className="preview-empty mt-8">还没有样本笔。请回工作台上传抽样清单，再传凭证。</p>
      ) : (
        <select
          className="field-select mt-8"
          value={active}
          onChange={(e) => void onSelect(e.target.value)}
        >
          {chains.map((c) => (
            <option key={c.chain_id} value={c.chain_id}>
              {c.chain_id} · {c.doc_count}单 · {c.tested ? '必测已齐' : '未测完'} ·{' '}
              {flagText(c)}
            </option>
          ))}
        </select>
      )}
      {chains.length > 0 && (
        <ul className="conclusion-list mt-8">
          {chains.map((c) => {
            const issues = countChainIssueHint(job, c.chain_id, c.file_names)
            const stepPending = Boolean(sweep?.pending.includes(c.chain_id))
            return (
            <li key={c.chain_id}>
              <span>
                {c.is_active ? '▶ ' : ''}
                {c.chain_id}
                {stepPending ? (
                  <span className="chain-issue-badge" data-tip="当前步骤还没做完。">
                    本步
                  </span>
                ) : null}
                {issues > 0 ? (
                  <span className="chain-issue-badge" data-tip="还有待核或不通过项。">
                    {issues}
                  </span>
                ) : null}
              </span>
              <code>{c.tested ? '必测已齐' : '待测'}</code>
            </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
