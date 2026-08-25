import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Job, ReviewEvent, ReviewEventSummary } from '../types'
import { SampleDeskList } from '../components/SampleDeskList'
import { EventSummaryBar } from '../components/EventSummaryBar'
import type { ChainInfo, DeskLights } from '../api'
import { packetNeedsReview } from '../lib/workflowGuide'
import { useJobChainIds } from '../lib/useJobChainIds'
import { listChainsCached, peekChainsCache } from '../lib/chainsCache'
import { emptyDeskProgress, progressFromRows } from '../lib/deskLights'
import { sortReviewEvents } from '../lib/reviewEvents'

type Props = {
  job: Job
  onJob: (j: Job) => void
  onGo: (step: string) => void
}

/** 字段已齐、测试未跑时只自动触发一键审阅一次（跨 StrictMode 双挂载）。 */
const autoTestStarted = new Set<string>()

export function SampleWorkbenchPage({ job, onJob, onGo }: Props) {
  const [busy, setBusy] = useState<'batch' | 'batch_review' | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [deskRows, setDeskRows] = useState<ChainInfo[]>(() => peekChainsCache(job)?.chains || [])
  const [deskLights, setDeskLights] = useState<DeskLights | null>(
    () => peekChainsCache(job)?.lights || null,
  )
  const [reviewEvents, setReviewEvents] = useState<ReviewEvent[]>([])
  const [eventSummary, setEventSummary] = useState<ReviewEventSummary>({
    open: 0,
    blocking: 0,
    missing: 0,
    review: 0,
    sample: 0,
    passed: 0,
  })
  const chainIds = useJobChainIds(job)
  const autoReviewing = Boolean(job.auto_review_processing)

  const syncJobOnGateError = async (e: unknown) => {
    const err = e as Error & { job?: Job }
    const msg = err instanceof Error ? err.message : String(e)
    if (err?.job && typeof err.job === 'object' && (err.job as Job).job_id) {
      onJob(err.job as Job)
      return msg
    }
    if (/字段确认|Gate3|字段相对|匹配确认|Gate4|勾稽|顾问候选/.test(msg)) {
      try {
        onJob(await api.getJob(job.job_id))
      } catch {
        /* ignore */
      }
    }
    return msg
  }

  const doBatchReview = async (force = false) => {
    setBusy('batch_review')
    setErr('')
    setMsg('')
    try {
      const out = await api.batchReview(job.job_id, force)
      if (out.job) onJob(out.job)
      setMsg(out.summary || '一键审阅完成')
    } catch (e) {
      setErr(await syncJobOnGateError(e))
    } finally {
      setBusy(null)
    }
  }

  const needsPeriodEnd = useMemo(
    () => (job.goal_ids || []).includes('gospd01030') || (job.goal_ids || []).includes('gospd01010'),
    [job.goal_ids],
  )

  const totalDocs = (job.classified || []).length + (job.pending_files || []).length
  const pendingDocs = (job.pending_files || []).length
  const popCount = job.sample_population?.count ?? 0
  const sampleCount = popCount || deskRows.length || chainIds.length
  const goalText =
    (job.plan?.goals || []).map((g) => g.label).join('、') ||
    (job.goal_ids || []).join('、') ||
    '未选目标'
  const needUnpack = packetNeedsReview(job)
  const lightKpi = useMemo(() => {
    const counts = { green: 0, yellow: 0, red: 0, wait: 0 }
    for (const row of deskRows) {
      const light = row.light || 'wait'
      if (light === 'green') counts.green += 1
      else if (light === 'yellow') counts.yellow += 1
      else if (light === 'red') counts.red += 1
      else counts.wait += 1
    }
    if (deskLights) {
      return {
        green: deskLights.green ?? counts.green,
        yellow: deskLights.yellow ?? counts.yellow,
        red: deskLights.red ?? counts.red,
        wait: deskLights.wait ?? counts.wait,
      }
    }
    return counts
  }, [deskRows, deskLights])

  const progressKpi = useMemo(() => {
    if (deskLights?.progress) {
      return { ...emptyDeskProgress(), ...deskLights.progress }
    }
    return progressFromRows(deskRows)
  }, [deskRows, deskLights])

  useEffect(() => {
    if (!job.job_id) return
    let cancelled = false
    const cached = peekChainsCache(job)
    if (cached?.chains?.length) {
      setDeskRows(cached.chains)
      setDeskLights(cached.lights || null)
    }
    listChainsCached(job)
      .then((r) => {
        if (!cancelled) {
          setDeskRows(r.chains || [])
          setDeskLights(r.lights || null)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          // 失败保留上次列表，避免切页瞬间「样本全空」
          setErr(e instanceof Error ? e.message : String(e))
        }
      })
    return () => {
      cancelled = true
    }
  }, [job.job_id, job.updated_at, popCount, (job.classified || []).length, pendingDocs])

  useEffect(() => {
    let cancelled = false
    void api
      .listReviewEvents(job.job_id, { includePassed: true })
      .then((result) => {
        if (cancelled) return
        setReviewEvents(sortReviewEvents(result.events.filter((row) => row.state === 'OPEN')))
        setEventSummary(result.summary)
      })
      .catch(() => {
        if (cancelled) return
        const open = deskRows.reduce((sum, row) => sum + (row.event_count || 0), 0)
        setEventSummary({
          open,
          blocking: deskRows.reduce(
            (sum, row) => sum + (row.blocking_event_count || 0),
            0,
          ),
          missing: deskRows.filter((row) => (row.missing_doc_types || []).length > 0).length,
          review: Math.max(0, open - deskRows.reduce(
            (sum, row) => sum + (row.blocking_event_count || 0),
            0,
          )),
          sample: 0,
          passed: deskRows.filter((row) => row.auto_passed).length,
        })
      })
    return () => {
      cancelled = true
    }
  }, [job.job_id, job.updated_at, deskRows])

  useEffect(() => {
    // 后台 auto_review / 本页 busy 时不要再叠跑一键审阅
    if (busy || autoReviewing || job.ocr_processing) return
    const pending = deskRows.filter((r) => r.reason === 'tests_pending')
    if (!pending.length) return
    const key = `${job.job_id}:${pending.map((r) => r.chain_id).sort().join(',')}`
    if (autoTestStarted.has(key)) return
    autoTestStarted.add(key)
    void doBatchReview(false)
  }, [deskRows, job.job_id, busy, autoReviewing, job.ocr_processing])

  const openSample = (row: ChainInfo) => {
    setErr('')
    // 先跳转目标页，再异步切链——避免干等整包 Job 才进页
    let target = 'conclusion_gate5'
    if (row.reason === 'wait_docs' || row.reason === 'missing_docs') {
      target = 'upload_ocr'
    } else if (
      row.reason === 'fields_gap' ||
      row.reason === 'amount_ambiguity' ||
      row.reason === 'docs_uncertain'
    ) {
      target = 'field_confirm'
    } else if (row.reason === 'test_fail' || row.reason === 'fail_closed') {
      target = 'conclusion_gate5'
    } else if (row.reason === 'tests_pending') {
      void doBatchReview()
      return
    }
    if (row.chain_id && row.chain_id !== job.active_chain_id) {
      // 乐观切笔：页面立刻对着该笔；后台落盘并镜像测试字段
      onJob({ ...job, active_chain_id: row.chain_id })
      void api
        .setActiveChain(job.job_id, row.chain_id)
        .then((next) => onJob(next))
        .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    }
    onGo(target)
  }

  const handleEventPrimary = () => {
    if (needUnpack) {
      onGo('packet_unpack')
      return
    }
    const event = reviewEvents[0]
    if (!event) {
      onGo('workbook_export')
      return
    }
    if (event.event_type !== 'MISSING_DOCUMENT') {
      onGo('event_review')
      return
    }
    const row = deskRows.find((item) => item.chain_id === event.chain_id)
    if (row) {
      openSample(row)
      return
    }
    onGo(event.action_step || 'conclusion_gate5')
  }

  return (
    <div className="panel panel-fill desk-cockpit">
      <div className="panel-head desk-head-compact">
        <div>
          <h3>总工作台</h3>
          <div className="hint">
            汇总所有抽样业务的处理状态，并提示审计师当前阶段与下一步操作。
          </div>
        </div>
        <div className="desk-head-actions">
          <button
            type="button"
            className="btn compact"
            onClick={() => onGo('sample_upload')}
          >
            抽样清单
          </button>
        </div>
      </div>

      <section className="desk-kpi" aria-label="全局进度总览">
        <div className="desk-kpi-item">
          <span className="desk-kpi-label">全部样本</span>
          <strong className="desk-kpi-value">{sampleCount}</strong>
          <span className="desk-kpi-unit">笔</span>
        </div>
        <div className="desk-kpi-item">
          <span className="desk-kpi-label">全部单据</span>
          <strong className="desk-kpi-value">{totalDocs}</strong>
          <span className="desk-kpi-unit">
            份{pendingDocs > 0 ? ` · 等待识别 ${pendingDocs}` : ''}
          </span>
        </div>
        <div
          className="desk-kpi-item"
          data-tip="已完成=绿灯通过，或红灯但已在结论页人工确认收口。待办=尚未收口的样本笔。"
        >
          <span className="desk-kpi-label">已完成</span>
          <strong className="desk-kpi-value">{progressKpi.done}</strong>
          <span className="desk-kpi-unit">
            {sampleCount > 0
              ? `${Math.round((progressKpi.done / sampleCount) * 100)}% · 待办 ${Math.max(
                  0,
                  sampleCount - progressKpi.done,
                )}`
              : '—'}
          </span>
        </div>
        <div className="desk-kpi-item">
          <span className="desk-kpi-label">样本状态灯</span>
          <strong className="desk-kpi-value desk-kpi-lights">
            <span className="desk-kpi-dot is-green" tabIndex={0} aria-label={`绿色 ${lightKpi.green} 笔`} data-tip="绿色：单据齐全、必要字段齐全且规则未发现异常，可以继续或已通过。">{lightKpi.green}</span>
            <span className="desk-kpi-dot is-yellow" tabIndex={0} aria-label={`黄色 ${lightKpi.yellow} 笔`} data-tip="黄色：文件类型、匹配关系或专业判断仍有疑问，需要审计师人工判断。">{lightKpi.yellow}</span>
            <span className="desk-kpi-dot is-red" tabIndex={0} aria-label={`红色 ${lightKpi.red} 笔`} data-tip="红色：缺少单据、关键字段缺失、无法归属或规则明确冲突，必须处理。">{lightKpi.red}</span>
            <span className="desk-kpi-dot is-wait" tabIndex={0} aria-label={`灰色 ${lightKpi.wait} 笔`} data-tip="灰色：凭证尚未上传、识别或测试仍在进行，尚未出灯。">{lightKpi.wait}</span>
          </strong>
          <span className="desk-kpi-unit">悬浮或聚焦数字查看完整含义</span>
        </div>
        <div className="desk-kpi-item desk-kpi-goals">
          <span className="desk-kpi-label">底稿目标</span>
          <strong className="desk-kpi-value desk-kpi-goals-text">{goalText}</strong>
          <span className="desk-kpi-unit">
            {job.period_end ? `期间截止日 ${job.period_end}` : needsPeriodEnd ? '期间截止日未填写' : '—'}
            {job.calendar_mode ? ` · ${job.calendar_mode}` : ''}
          </span>
        </div>
      </section>

      <EventSummaryBar
        summary={eventSummary}
        busy={busy !== null}
        onPrimary={handleEventPrimary}
      />

      {(err || msg) && (
        <div className="desk-flash">
          {err && <p className="err">{err}</p>}
          {msg && <p className="ok-text">{msg}</p>}
        </div>
      )}

      {popCount > 0 && totalDocs === 0 && (
        <p className="hint" style={{ margin: '0 12px' }}>
          已立 {popCount} 笔，单据还是 0。点下一步去上传这些笔的凭证。
        </p>
      )}
      {needUnpack && (
        <p className="desk-unpack-banner" role="status">
          有混装扫描件未确认拆包：请先去拆包分笔，确认后才能开始识别；未确认不得当绿灯。
          <button
            type="button"
            className="btn compact"
            disabled={busy !== null}
            onClick={() => onGo('packet_unpack')}
          >
            去拆包分笔
          </button>
        </p>
      )}

      <div className="desk-stage">
        <div className="desk-stage-block">
          <SampleDeskList
            rows={deskRows}
            lights={deskLights}
            activeId={job.active_chain_id}
            busy={busy !== null}
            mode="overview"
            onOpen={(row) => openSample(row)}
          />
        </div>
      </div>
    </div>
  )
}
