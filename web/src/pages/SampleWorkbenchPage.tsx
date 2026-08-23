import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { Job, ReviewEvent, ReviewEventSummary } from '../types'
import { SampleDeskList } from '../components/SampleDeskList'
import { EventSummaryBar } from '../components/EventSummaryBar'
import type { ChainInfo, DeskLights } from '../api'
import { activeSample, isGospdJob } from '../lib/chainDocs'
import { resultOverallStatus, threeWayCutoffCardStatus } from '../lib/testStatus'
import {
  buildWorkflowGuide,
  guideCtaTip,
  packetNeedsReview,
  type GuideAction,
} from '../lib/workflowGuide'
import { useJobChainIds } from '../lib/useJobChainIds'
import { confirmLinkagePrimary } from '../lib/confirmLinkage'
import { listChainsCached, peekChainsCache } from '../lib/chainsCache'
import {
  DESK_LIGHT_LEGEND_TIP,
  emptyDeskProgress,
  progressFromRows,
} from '../lib/deskLights'
import { sortReviewEvents } from '../lib/reviewEvents'

type Props = {
  job: Job
  onJob: (j: Job) => void
  onGo: (step: string) => void
}

type RunKind = 'evidence' | 'amount' | 'contract' | 'three_way'

/** 字段已齐、测试未跑时只自动触发一键审阅一次（跨 StrictMode 双挂载）。 */
const autoTestStarted = new Set<string>()

function testStatus(job: Job, key: keyof Job): string {
  const s = isGospdJob(job) ? activeSample(job) : null
  const v = (s && key in s ? s[key as string] : null) || job[key]
  if (!v || typeof v !== 'object') return '未跑'
  return resultOverallStatus(v) || '-'
}

function threeWayCardStatus(job: Job): string {
  const s = activeSample(job)
  return threeWayCutoffCardStatus({
    threeWay: s.three_way || job.three_way,
    threeWayMatch: s.three_way_match || job.three_way_match,
    cutoffTest: s.cutoff_test || job.cutoff_test,
  })
}

export function SampleWorkbenchPage({ job, onJob, onGo }: Props) {
  const [busy, setBusy] = useState<
    | RunKind
    | 'batch'
    | 'batch_review'
    | 'confirm_all'
    | 'linkage'
    | 'release'
    | 'switch'
    | 'period'
    | 'ingest'
    | null
  >(null)
  const ledgerInputRef = useRef<HTMLInputElement>(null)
  const mixedPacketInputRef = useRef<HTMLInputElement>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [uploadingId, setUploadingId] = useState<string | null>(null)
  const [uploadErrorById, setUploadErrorById] = useState<Record<string, string>>({})
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
  const required = job.plan?.required_steps || []
  const autoReviewing = Boolean(job.auto_review_processing)

  const guide = useMemo(() => buildWorkflowGuide(job, { chainIds }), [job, chainIds])

  const cards = useMemo(() => {
    const all: Array<{
      kind: RunKind
      step: string
      title: string
      needGate4: boolean
      status: string
      show: boolean
    }> = [
      {
        kind: 'evidence',
        step: 'relations_gate4',
        title: '串单匹配',
        needGate4: false,
        status: testStatus(job, 'evidence'),
        show: required.includes('evidence_match') || required.includes('relations_gate4'),
      },
      {
        kind: 'contract',
        step: 'contract_terms',
        title: '合同条款',
        needGate4: false,
        status: testStatus(job, 'contract_terms'),
        show: required.includes('contract_terms'),
      },
      {
        kind: 'amount',
        step: 'amount_test',
        title: '金额测试',
        needGate4: false,
        status: testStatus(job, 'amount_test'),
        show: required.includes('amount_test'),
      },
      {
        kind: 'three_way',
        step: 'three_way_cutoff',
        title: '三单+截止',
        needGate4: false,
        status: threeWayCardStatus(job),
        show: required.includes('three_way_cutoff'),
      },
    ]
    return all.filter((c) => c.show)
  }, [job, required])

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

  const gateBlock = (j: Job, needGate4: boolean): string | null => {
    const s = isGospdJob(j) ? activeSample(j) : null
    const fieldsOk = Boolean(s ? s.fields_confirmed : j.fields_confirmed)
    const matchOk = Boolean((s && s.matching_confirmed) || j.matching_confirmed)
    if (!fieldsOk) return '请先核对并确认字段'
    if (needGate4 && !matchOk) return '请先确认匹配关系'
    return null
  }

  /** 本步做完后若还有其它笔待办，自动切过去（横扫）；只切权威链，避免幽灵 HT */
  const maybeAutoSwitch = async (j: Job, doneMsg: string) => {
    const ids = chainIds.length ? chainIds : []
    const g = buildWorkflowGuide(j, { chainIds: ids })
    if (
      g.action.kind === 'switch_chain' &&
      ids.includes(g.action.chain_id)
    ) {
      const next = await api.setActiveChain(j.job_id, g.action.chain_id)
      onJob(next)
      setMsg(`${doneMsg} 已切换到 ${g.action.chain_id} 继续本步。`)
      return
    }
    onJob(j)
    setMsg(doneMsg)
  }

  const runOne = async (kind: RunKind) => {
    const card = cards.find((c) => c.kind === kind)
    const block = gateBlock(job, Boolean(card?.needGate4))
    if (block) {
      setErr(block)
      return
    }
    setBusy(kind)
    setErr('')
    setMsg('')
    try {
      let next: Job
      if (kind === 'evidence') next = await api.evidenceMatch(job.job_id)
      else if (kind === 'amount') next = await api.amountTest(job.job_id)
      else if (kind === 'contract') next = await api.contractTerms(job.job_id)
      else next = await api.threeWay(job.job_id)
      await maybeAutoSwitch(next, `${card?.title || kind}已完成。`)
    } catch (e) {
      setErr(await syncJobOnGateError(e))
    } finally {
      setBusy(null)
    }
  }

  const doLinkage = async () => {
    setBusy('linkage')
    setErr('')
    setMsg('')
    try {
      const out = await confirmLinkagePrimary(job)
      if (!out.matching_confirmed) {
        onJob(out.job)
        setMsg(out.message || '勾稽未完成，已自动继续。')
        return
      }
      await maybeAutoSwitch(out.job, out.message || '本笔勾稽已确认')
    } catch (e) {
      setErr(await syncJobOnGateError(e))
    } finally {
      setBusy(null)
    }
  }

  const doRelease = async () => {
    setBusy('release')
    setErr('')
    setMsg('')
    try {
      const out = await api.releaseActiveChain(job.job_id, {
        reason: '工作台本笔放行',
        ack_unacked: true,
      })
      await maybeAutoSwitch(out.job, out.message || '本笔已放行')
    } catch (e) {
      const msg = await syncJobOnGateError(e)
      setErr(msg)
      if (msg.includes('顾问候选')) {
        requestAnimationFrame(() => {
          document.getElementById('advisory-pending')?.scrollIntoView({
            behavior: 'smooth',
            block: 'nearest',
          })
        })
      }
      if (/字段确认|Gate3|字段相对/.test(msg)) {
        setMsg('绿灯已与后端对齐：请先到「核对字段」重新确认本笔字段。')
      }
    } finally {
      setBusy(null)
    }
  }

  const focusAdvisory = () => {
    requestAnimationFrame(() => {
      document.getElementById('advisory-pending')?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    })
  }

  const doSwitchChain = async (chainId: string) => {
    setBusy('switch')
    setErr('')
    setMsg('')
    try {
      onJob(await api.setActiveChain(job.job_id, chainId))
      setMsg(`已切换到 ${chainId}，继续本步即可。`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
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

  const doConfirmMatchingAll = async () => {
    setBusy('confirm_all')
    setErr('')
    setMsg('')
    try {
      const out = await api.confirmMatchingAll(job.job_id)
      if (out.job) onJob(out.job)
      setMsg(out.summary || ((out.blocked || []).length ? '串单需人工处理' : '串单确认完成'))
    } catch (e) {
      setErr(await syncJobOnGateError(e))
    } finally {
      setBusy(null)
    }
  }

  const doAction = async (action: GuideAction) => {
    if (action.kind === 'go') {
      if (action.step === 'relations_gate4' || action.step === 'evidence_match') {
        await doBatchReview(false)
        return
      }
      onGo(action.step)
      return
    }
    if (action.kind === 'run') {
      await runOne(action.test)
      return
    }
    if (action.kind === 'run_batch') {
      await doBatchReview(false)
      return
    }
    if (action.kind === 'batch_review') {
      await doBatchReview(false)
      return
    }
    if (action.kind === 'confirm_matching_all') {
      await doConfirmMatchingAll()
      return
    }
    if (action.kind === 'linkage') {
      await doLinkage()
      return
    }
    if (action.kind === 'release') {
      await doRelease()
      return
    }
    if (action.kind === 'focus_advisory') {
      focusAdvisory()
      return
    }
    if (action.kind === 'ingest_ledger') {
      ledgerInputRef.current?.click()
      return
    }
    if (action.kind === 'switch_chain') {
      await doSwitchChain(action.chain_id)
    }
  }

  const importLedger = async (file: File) => {
    setBusy('ingest')
    setErr('')
    setMsg('')
    try {
      const next = await api.importSampleExcel(job.job_id, file)
      onJob(next)
      const n = next.sample_population?.count ?? 0
      setMsg(`已立 ${n} 笔。凭证未重传，已按新账重绑并重跑匹配/测试。`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const [periodDraft, setPeriodDraft] = useState(String(job.period_end || ''))
  const needsPeriodEnd = useMemo(
    () => (job.goal_ids || []).includes('gospd01030') || (job.goal_ids || []).includes('gospd01010'),
    [job.goal_ids],
  )

  useEffect(() => {
    setPeriodDraft(String(job.period_end || ''))
  }, [job.job_id, job.period_end])

  const savePeriodEnd = async () => {
    setBusy('period')
    setErr('')
    try {
      const next = await api.setGoals(job.job_id, job.goal_ids || [], {
        period_end: periodDraft.trim() || undefined,
        entity_name: job.entity_name || undefined,
        calendar_mode: job.calendar_mode || undefined,
        fiscal_year_start: job.fiscal_year_start || undefined,
      })
      onJob(next)
      setMsg(
        periodDraft.trim()
          ? `报告期末已更新为 ${periodDraft.trim()}；三单/截止已失效，请重跑。`
          : '已清空报告期末；请配置后再导出 01030。',
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

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

  const uploadEvidence = async (row: ChainInfo | null, files: File[]) => {
    if (!files.length || uploadingId) return
    const targetId = row?.chain_id || '__mixed_packet__'
    setUploadingId(targetId)
    setErr('')
    setMsg('')
    setUploadErrorById((current) => ({ ...current, [targetId]: '' }))
    try {
      const businessHints = row
        ? Object.fromEntries(files.map((file) => [file.name, [row.chain_id]]))
        : undefined
      const next = await api.upload(job.job_id, files, {
        process: false,
        businessHints,
      })
      onJob(next)
      if (packetNeedsReview(next)) {
        setMsg(`已上传 ${files.length} 个文件；多页或混装资料须先确认拆包。`)
        onGo('packet_unpack')
      } else {
        setMsg(`已上传 ${files.length} 个文件并完成轻量分类，请核对类型后开始识别。`)
        onGo('upload_ocr')
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setUploadErrorById((current) => ({ ...current, [targetId]: message }))
    } finally {
      setUploadingId(null)
    }
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
          <h3>样本工作台</h3>
          <div className="hint">
            先立笔再传凭证。红=缺单/缺字段/测试失败；黄=类型存疑；绿=已通过。混装须先拆包确认再识别。
          </div>
        </div>
        <input
          ref={ledgerInputRef}
          type="file"
          accept=".xlsx,.xlsm"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (f) void importLedger(f)
          }}
        />
        <input
          ref={mixedPacketInputRef}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff"
          hidden
          aria-label="选择尚未确定业务归属的混装资料包"
          onChange={(event) => {
            const files = Array.from(event.target.files || [])
            event.target.value = ''
            if (files.length) void uploadEvidence(null, files)
          }}
        />
        <button
          type="button"
          className="btn compact"
          disabled={busy !== null || uploadingId !== null}
          onClick={() => mixedPacketInputRef.current?.click()}
        >
          {uploadingId === '__mixed_packet__' ? '上传中…' : '上传混装资料包'}
        </button>
        {popCount > 0 && (
          <label
            className="btn compact"
            data-tip="更换抽样清单会重立样本笔，并更新后面测试用的入账日/金额。"
          >
            更换抽样清单
            <input
              type="file"
              accept=".xlsx,.xlsm"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) void importLedger(f)
              }}
            />
          </label>
        )}
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
            份{pendingDocs > 0 ? ` · 待识 ${pendingDocs}` : ''}
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
        <div className="desk-kpi-item" data-tip={DESK_LIGHT_LEGEND_TIP}>
          <span className="desk-kpi-label">红黄绿</span>
          <strong className="desk-kpi-value desk-kpi-lights">
            <span className="desk-kpi-dot is-green">{lightKpi.green}</span>
            <span className="desk-kpi-dot is-yellow">{lightKpi.yellow}</span>
            <span className="desk-kpi-dot is-red">{lightKpi.red}</span>
          </strong>
          <span className="desk-kpi-unit">
            {progressKpi.fail_confirmed > 0
              ? `红含已确认 ${progressKpi.fail_confirmed} · 灰${lightKpi.wait}`
              : `绿可继续 · 黄人裁 · 红须处理 · 灰${lightKpi.wait}`}
          </span>
        </div>
        <div className="desk-kpi-item desk-kpi-goals">
          <span className="desk-kpi-label">底稿目标</span>
          <strong className="desk-kpi-value desk-kpi-goals-text">{goalText}</strong>
          <span className="desk-kpi-unit">
            {job.period_end ? `期末 ${job.period_end}` : needsPeriodEnd ? '期末未配' : '—'}
            {job.calendar_mode ? ` · ${job.calendar_mode}` : ''}
          </span>
        </div>
      </section>

      <EventSummaryBar
        summary={eventSummary}
        busy={busy !== null}
        onPrimary={handleEventPrimary}
      />

      <details className="desk-command desk-command-secondary">
        <summary>查看当前处理说明</summary>
        <div className="desk-command-main">
          <span className="desk-command-kicker">下一步</span>
          <strong className="desk-command-title">{guide.headline}</strong>
          <span className="desk-command-detail">{guide.detail}</span>
          {guide.sweepPending && guide.sweepPending.length > 0 && (
            <span className="hint">待办 {guide.sweepPending.join('、')}</span>
          )}
        </div>
        <ol className="hub-progress desk-command-progress">
          {guide.steps.map((s) => (
            <li key={s.id} className={`hub-progress-item is-${s.state}`}>
              <span className="hub-progress-dot" aria-hidden />
              <span>{s.label}</span>
            </li>
          ))}
        </ol>
        {guide.action.kind !== 'none' && (
          <span className="tip-anchor" data-tip={guideCtaTip(guide)}>
            <button
              type="button"
              className="btn compact desk-command-cta"
              disabled={busy !== null}
              onClick={() => void doAction(guide.action)}
            >
              {busy ? '处理中…' : guide.ctaLabel}
            </button>
          </span>
        )}
        {needsPeriodEnd && (
          <div className="desk-period">
            <label className="hint" htmlFor="desk-period-end">
              期末
            </label>
            <input
              id="desk-period-end"
              type="date"
              value={periodDraft}
              onChange={(e) => setPeriodDraft(e.target.value)}
              disabled={busy !== null}
            />
            <button
              type="button"
              className="btn compact"
              disabled={busy !== null || periodDraft === String(job.period_end || '')}
              onClick={() => void savePeriodEnd()}
            >
              保存
            </button>
            {!job.period_end && <span className="hint compact-err">必填</span>}
          </div>
        )}
      </details>

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
            uploadingId={uploadingId}
            uploadErrorById={uploadErrorById}
            onOpen={(row) => openSample(row)}
            onUpload={(row, files) => uploadEvidence(row, files)}
          />
        </div>
      </div>
    </div>
  )
}
