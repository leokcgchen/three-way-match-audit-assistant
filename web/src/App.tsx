import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type { Job } from './types'
import { GoalsPage } from './pages/GoalsPage'
import { UploadPage } from './pages/UploadPage'
import { FieldConfirmPage } from './pages/FieldConfirmPage'
import { ConclusionPage } from './pages/ConclusionPage'
import { WorkbookPage } from './pages/WorkbookPage'
import { HardCasesPage } from './pages/HardCasesPage'
import { PromptLabPage } from './pages/PromptLabPage'
import { SampleWorkbenchPage } from './pages/SampleWorkbenchPage'
import { PacketUnpackPage } from './pages/PacketUnpackPage'
import { TipHost } from './components/TipHost'
import { MastheadProgress } from './components/MastheadProgress'
import { packetNeedsReview } from './lib/workflowGuide'
import { invalidateChainsCache, listChainsCached } from './lib/chainsCache'
import { invalidateConclusionTraceCache } from './lib/conclusionTraceCache'
import { emptyDeskProgress, progressFromRows, type DeskProgress } from './lib/deskLights'
import './styles.css'

const STEP_TIP: Record<string, string> = {
  goals: '只选底稿目标和期末。抽样清单在工作台传，不在本页。',
  sample_desk: '审阅中枢：上传抽样清单、看下一步、切业务笔、一键审阅。',
  upload_ocr: '上传合同/订单/发票等凭证（抽样清单在工作台传）。',
  packet_unpack: '看切开是否对，改类型并归到业务笔，确认后再识别。',
  field_confirm: '红灯笔对照原件补缺字段。',
  conclusion_gate5: '测试未通过时，看对不上的数据并确认是不通过还是单据问题。',
  workbook_export: '按目标生成 Excel 审阅底稿。',
  hard_cases: '已处理过的识别难点备忘，供演示讲解。',
  prompts: '只读查看系统提示词，供调试，不参与审阅。',
}

const STEP_META: Record<string, string> = {
  goals: '底稿目标',
  sample_desk: '工作台',
  upload_ocr: '上传凭证',
  packet_unpack: '拆包分笔',
  field_confirm: '人工核对',
  conclusion_gate5: '确认结论',
  workbook_export: '导出底稿',
  hard_cases: '识难录',
  prompts: '提示词工程',
}

type JobListItem = {
  job_id: string
  title?: string
  goal_ids?: string[]
  goal_labels?: string[]
  updated_at?: string
  doc_count?: number
  pending_count?: number
  stage?: string
  has_workbook?: boolean
}

function formatJobTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

/** 侧栏任务下拉：一眼能看出目标 / 进度 / 单据数 / 编号 */
function jobOptionLabel(j: JobListItem): string {
  const goals =
    (j.goal_labels || []).filter(Boolean).join('+') ||
    (j.goal_ids || []).join('+') ||
    '未选目标'
  const docs = j.doc_count ?? 0
  const pending = j.pending_count ?? 0
  const docPart = pending > 0 ? `${docs}单+待${pending}` : `${docs}单`
  const stage = j.stage || ''
  const when = formatJobTime(j.updated_at)
  const id = (j.job_id || '').slice(0, 6)
  return [goals, docPart, stage, when, `#${id}`].filter(Boolean).join(' · ')
}

function newJobTitle(): string {
  const d = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `新建审阅 ${hh}:${mi}`
}

export default function App() {
  const [job, setJob] = useState<Job | null>(null)
  const [apiOk, setApiOk] = useState(false)
  const [phase, setPhase] = useState('')
  const [err, setErr] = useState('')
  const [step, setStep] = useState('goals')
  const [jobList, setJobList] = useState<JobListItem[]>([])
  const [ocrBusy, setOcrBusy] = useState(false)
  const [postReviewBusy, setPostReviewBusy] = useState(false)
  const [ocrMsg, setOcrMsg] = useState('')
  const ocrInflight = useRef(false)
  const [hubVisited, setHubVisited] = useState({ desk: false, conclusion: false })
  const [deskProgress, setDeskProgress] = useState<DeskProgress | null>(null)
  const [railCollapsed, setRailCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gospd.railCollapsed') === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    if (step === 'sample_desk') {
      setHubVisited((v) => (v.desk ? v : { ...v, desk: true }))
    } else if (step === 'conclusion_gate5') {
      setHubVisited((v) => (v.conclusion ? v : { ...v, conclusion: true }))
    }
  }, [step])

  useEffect(() => {
    if (!job?.job_id) {
      setDeskProgress(null)
      return
    }
    let cancelled = false
    listChainsCached(job)
      .then((r) => {
        if (cancelled) return
        const fromApi = r.lights?.progress
        setDeskProgress(
          fromApi
            ? { ...emptyDeskProgress(), ...fromApi }
            : progressFromRows(r.chains || []),
        )
      })
      .catch(() => {
        if (!cancelled) setDeskProgress(null)
      })
    return () => {
      cancelled = true
    }
  }, [job?.job_id, job?.updated_at, (job?.classified || []).length, job?.sample_population?.count])

  const mastheadSampleTotal = useMemo(() => {
    if (!deskProgress) return 0
    return deskProgress.sample_total || 0
  }, [deskProgress])

  const toggleRail = () => {
    setRailCollapsed((v) => {
      const next = !v
      try {
        localStorage.setItem('gospd.railCollapsed', next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  const refreshJobList = async () => {
    try {
      const r = await api.listJobs()
      setJobList((r.jobs || []) as JobListItem[])
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const h = await api.health()
        if (cancelled) return
        setApiOk(h.status === 'ok')
        setPhase(h.phase || '')
        // 刷新不新建空任务：优先恢复最近有内容的；列表空才创建
        const listed = await api.listJobs()
        if (cancelled) return
        const jobs = (listed.jobs || []) as JobListItem[]
        setJobList(jobs)
        if (jobs.length > 0) {
          const pick =
            jobs.find((j) => (j.doc_count || 0) > 0 || (j.goal_ids || []).length > 0) ||
            jobs[0]
          const restored = await api.getJob(pick.job_id)
          if (cancelled) return
          setJob(restored)
          setStep(restored.active_step || 'goals')
        } else {
          const created = await api.createJob(newJobTitle())
          if (cancelled) return
          setJob(created)
          setStep('goals')
          await refreshJobList()
        }
      } catch (e) {
        if (!cancelled) {
          setApiOk(false)
          setErr(e instanceof Error ? e.message : String(e))
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const switchJob = async (jobId: string) => {
    if (!jobId) return
    try {
      const j = await api.getJob(jobId)
      setJob(j)
      setStep(j.active_step || 'goals')
      setErr('')
      await refreshJobList()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const newJob = async () => {
    try {
      const created = await api.createJob(newJobTitle())
      setJob(created)
      setStep('goals')
      await refreshJobList()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const hasGoals = (job?.goal_ids?.length || 0) > 0
  const requiredSteps = job?.plan?.required_steps || []
  const goalLabels = (job?.plan?.goals || []).map((g) => g.label).filter(Boolean)

  const goStep = async (stepId: string) => {
    // 导出门禁/旧配方可能仍抛引擎步名：映射到壳层已有页面，避免「未知步骤」空白
    const STEP_ALIAS: Record<string, string> = {
      three_way_cutoff: 'sample_desk',
      amount_test: 'sample_desk',
      contract_terms: 'sample_desk',
      evidence_match: 'field_confirm',
      relations_gate4: 'field_confirm',
    }
    stepId = STEP_ALIAS[stepId] || stepId
    if (stepId === 'field_confirm' && job && packetNeedsReview(job)) {
      stepId = 'packet_unpack'
    }
    // 字段核对需要最大工作区：进页收起侧栏；离开时恢复用户偏好（不改落盘）
    if (stepId === 'field_confirm') {
      setRailCollapsed(true)
    } else if (step === 'field_confirm') {
      try {
        setRailCollapsed(localStorage.getItem('gospd.railCollapsed') === '1')
      } catch {
        setRailCollapsed(false)
      }
    }
    // 先切页；active_step 后台落盘但不回灌整包 Job（避免 bump 缓存戳拖慢纯切换）
    setStep(stepId)
    if (!job || stepId === 'goals' || stepId === 'prompts' || stepId === 'hard_cases') return
    const jobId = job.job_id
    if (job.active_step === stepId) return
    setJob((prev) => (prev && prev.job_id === jobId ? { ...prev, active_step: stepId } : prev))
    void api.setActiveStep(jobId, stepId).catch((e) => {
      setErr(e instanceof Error ? e.message : String(e))
    })
  }

  const onJobUpdate = (j: Job) => {
    // 仅当服务端内容戳变化时打穿缓存：确认结论/跑测试/切链落盘等必更新；
    // 纯本地乐观补丁（同 updated_at）不误伤，让切页仍可命中缓存。
    setJob((prev) => {
      if (!prev || prev.job_id !== j.job_id || prev.updated_at !== j.updated_at) {
        invalidateChainsCache(j.job_id)
        invalidateConclusionTraceCache(j.job_id)
      }
      return j
    })
    void refreshJobList()
  }

  const pollOcrUntilDone = async (jobId: string): Promise<Job> => {
    for (;;) {
      const j = await api.getJob(jobId)
      onJobUpdate(j)
      const prog = j.ocr_progress
      if (j.ocr_processing_message) {
        setOcrMsg(j.ocr_processing_message)
      } else if (prog && prog.total > 0) {
        setOcrMsg(`正在识别 (${prog.done}/${prog.total})${prog.file ? `：${prog.file}` : ''}`)
      }
      // OCR 一结束就返回，不再等后台审阅（避免整站假死）
      if (!j.ocr_processing) {
        setOcrMsg(
          j.auto_review_processing
            ? j.ocr_processing_message || '识别完成，后台自动审阅中…可继续操作'
            : `识别完成：单据 ${(j.classified || []).length}` +
                (j.ocr_issues?.length ? ` · 问题 ${j.ocr_issues.length}` : ''),
        )
        return j
      }
      await new Promise((r) => setTimeout(r, 900))
    }
  }

  const pollAutoReviewUntilDone = async (jobId: string) => {
    setPostReviewBusy(true)
    try {
      for (let i = 0; i < 180; i++) {
        const j = await api.getJob(jobId)
        onJobUpdate(j)
        if (!j.auto_review_processing) {
          setOcrMsg(
            `自动审阅完成：单据 ${(j.classified || []).length}` +
              (j.ocr_issues?.length ? ` · 问题 ${j.ocr_issues.length}` : ''),
          )
          return
        }
        setOcrMsg(j.ocr_processing_message || '后台自动审阅中…可继续切页操作')
        await new Promise((r) => setTimeout(r, 1200))
      }
    } finally {
      setPostReviewBusy(false)
    }
  }

  /** 识别挂在 App 层：后台 OCR + 轮询进度，切菜单不中断 */
  const runOcrProcess = async (force = false) => {
    if (!job || ocrInflight.current) return
    ocrInflight.current = true
    setOcrBusy(true)
    setOcrMsg(force ? '提交强制重识别…' : '提交识别任务…')
    setErr('')
    const jobId = job.job_id
    try {
      let next = await api.process(jobId, { force }).catch(async (e) => {
        const msg = e instanceof Error ? e.message : String(e)
        const jobFromErr = (e as Error & { job?: Job }).job
        if (jobFromErr) onJobUpdate(jobFromErr)
        if (msg.includes('拆包')) {
          setOcrMsg('请先完成拆包分笔')
          setStep('packet_unpack')
          return jobFromErr || (await api.getJob(jobId))
        }
        if (msg.includes('409') || msg.includes('仍在进行')) {
          return api.getJob(jobId)
        }
        throw e
      })
      if (next.ocr_processing) {
        next = await pollOcrUntilDone(jobId)
      } else if (next.ledger_path && next.ledger_mapping) {
        try {
          next = await api.applyLedger(jobId)
          onJobUpdate(next)
        } catch {
          /* ignore */
        }
      }
      onJobUpdate(next)
      if (next.auto_review_processing) {
        void pollAutoReviewUntilDone(jobId)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      try {
        onJobUpdate(await api.getJob(jobId))
      } catch {
        /* ignore */
      }
      setOcrMsg('')
      throw e
    } finally {
      ocrInflight.current = false
      setOcrBusy(false)
    }
  }

  useEffect(() => {
    if (!job?.ocr_processing || ocrInflight.current) return
    ocrInflight.current = true
    setOcrBusy(true)
    setOcrMsg(job.ocr_processing_message || '识别进行中…')
    void pollOcrUntilDone(job.job_id)
      .then((j) => {
        if (j.auto_review_processing) void pollAutoReviewUntilDone(j.job_id)
      })
      .finally(() => {
        ocrInflight.current = false
        setOcrBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id, job?.ocr_processing])

  useEffect(() => {
    if (!job?.auto_review_processing || ocrBusy || postReviewBusy || ocrInflight.current) return
    void pollAutoReviewUntilDone(job.job_id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id, job?.auto_review_processing])

  const body = () => {
    if (!job) {
      return (
        <div className="panel-body">
          {err ? <p className="err">API 未就绪：{err}</p> : <p>正在加载任务…</p>}
        </div>
      )
    }
    if (step === 'goals') {
      return (
        <GoalsPage
          job={job}
          onJob={(j) => {
            onJobUpdate(j)
            setStep('sample_desk')
          }}
        />
      )
    }
    // 工作台 ↔ 结论：进入过一次后保活；导出页不保活重页，减少后台 effect
    const hubSteps = step === 'sample_desk' || step === 'conclusion_gate5'
    const mountDesk = step === 'sample_desk' || (hubVisited.desk && hubSteps)
    const mountConclusion = step === 'conclusion_gate5' || (hubVisited.conclusion && hubSteps)

    if (
      step === 'sample_desk' ||
      step === 'conclusion_gate5' ||
      step === 'upload_ocr' ||
      step === 'packet_unpack' ||
      step === 'field_confirm' ||
      step === 'evidence_match' ||
      step === 'relations_gate4' ||
      step === 'workbook_export' ||
      step === 'hard_cases' ||
      step === 'prompts'
    ) {
      return (
        <>
          {mountDesk && (
            <div
              className={`step-keep-alive${step === 'sample_desk' ? '' : ' is-hidden'}`}
              aria-hidden={step !== 'sample_desk'}
            >
              <SampleWorkbenchPage job={job} onJob={onJobUpdate} onGo={(s) => void goStep(s)} />
            </div>
          )}
          {mountConclusion && (
            <div
              className={`step-keep-alive${step === 'conclusion_gate5' ? '' : ' is-hidden'}`}
              aria-hidden={step !== 'conclusion_gate5'}
            >
              <ConclusionPage job={job} onJob={onJobUpdate} onGo={(s) => void goStep(s)} />
            </div>
          )}
          {step === 'upload_ocr' && (
            <UploadPage
              job={job}
              onJob={onJobUpdate}
              ocrBusy={ocrBusy}
              ocrMsg={ocrMsg}
              onProcess={(force) => runOcrProcess(force)}
              onGo={(s) => void goStep(s)}
            />
          )}
          {step === 'packet_unpack' && (
            <PacketUnpackPage
              job={job}
              onJob={onJobUpdate}
              ocrBusy={ocrBusy}
              onProcess={(force) => runOcrProcess(force)}
            />
          )}
          {(step === 'field_confirm' || step === 'evidence_match' || step === 'relations_gate4') && (
            <FieldConfirmPage
              job={job}
              onJob={onJobUpdate}
              onBackToDesk={() => void goStep('sample_desk')}
            />
          )}
          {step === 'workbook_export' && (
            <WorkbookPage job={job} onJob={onJobUpdate} onGo={(s) => void goStep(s)} />
          )}
          {step === 'hard_cases' && <HardCasesPage />}
          {step === 'prompts' && <PromptLabPage />}
        </>
      )
    }
    return <div className="panel-body">未知步骤 {step}</div>
  }

  return (
    <div className="app">
      <header className="masthead">
        <div className="masthead-spine" />
        <div className="masthead-inner">
          <div className="masthead-brand">
            <div className="kicker">GOSPD WORKBENCH · AUDIT LEDGER</div>
            <h1>抽凭审阅工作台</h1>
            <p>工作台中枢 · 抽样清单立笔 · 红灯才进核对 · 清单收口后导出</p>
          </div>
          <MastheadProgress progress={deskProgress} sampleTotal={mastheadSampleTotal} />
          <div className={`status-pill${apiOk ? ' ok' : ''}`}>
            {apiOk ? `API · ${phase || 'ok'}` : 'API 离线'}
          </div>
        </div>
      </header>
      {(ocrBusy || postReviewBusy || job?.auto_review_processing) && (
        <div className={`ocr-banner${postReviewBusy || job?.auto_review_processing ? ' is-soft' : ''}`} role="status">
          {ocrMsg ||
            (ocrBusy
              ? '识别处理中…可切换其它菜单，完成后结果会自动刷新。'
              : '后台自动审阅中…可继续切页，完成后样本灯会更新。')}
        </div>
      )}
      <div className={`shell${railCollapsed ? ' rail-collapsed' : ''}`}>
        <aside className="rail" aria-label="导航">
          <div className="rail-collapse-bar">
            <button
              type="button"
              className="btn compact rail-toggle"
              onClick={toggleRail}
              data-tip={railCollapsed ? '展开左侧导航' : '收起左侧导航，放大工作区'}
              aria-expanded={!railCollapsed}
            >
              {railCollapsed ? '»' : '«'}
            </button>
            {!railCollapsed && <span className="rail-collapse-hint">收起放大工作区</span>}
          </div>
          <div className="rail-body">
          <div className="rail-section">
            <div className="rail-label">审阅任务</div>
            <div className="hint" style={{ marginBottom: 6, fontSize: '0.72rem' }}>
              格式：目标 · 单据数 · 进度 · 时间 · 编号
            </div>
            <select
              className="field-select"
              style={{ marginBottom: 6 }}
              value={job?.job_id || ''}
              onChange={(e) => void switchJob(e.target.value)}
              data-tip="切换历史审阅任务（当前进程内的会话）。"
            >
              {jobList.map((j) => (
                <option key={j.job_id} value={j.job_id}>
                  {jobOptionLabel(j)}
                </option>
              ))}
              {job && !jobList.some((j) => j.job_id === job.job_id) && (
                <option value={job.job_id}>当前 · #{job.job_id.slice(0, 6)}</option>
              )}
            </select>
            <button
              type="button"
              className="btn"
              style={{ width: '100%' }}
              onClick={() => void newJob()}
              data-tip="新开一个审阅任务，当前任务仍保留在上方下拉里。"
            >
              新建审阅任务
            </button>
          </div>

          {!hasGoals && (
            <div className="rail-section">
              <div className="rail-label">目标</div>
              <button
                type="button"
                className={`step-btn root${step === 'goals' ? ' active' : ''}`}
                onClick={() => goStep('goals')}
                data-tip={STEP_TIP.goals}
              >
                <span className="idx">目</span>
                {STEP_META.goals}
              </button>
            </div>
          )}

          <div className={`rail-branch${hasGoals ? ' open' : ''}`}>
            <div className="rail-label">{hasGoals ? '审阅枢纽' : '流程步骤'}</div>
            {!hasGoals && (
              <div className="rail-empty">先确认底稿目标，再进入工作台</div>
            )}
            {hasGoals && (
              <>
                <button
                  type="button"
                  className={`goal-parent${step === 'goals' ? ' is-on' : ''}`}
                  onClick={() => goStep('goals')}
                  data-tip="查看或改底稿目标。"
                >
                  <div className="goal-parent-title">当前目标</div>
                  <ul className="goal-parent-list">
                    {(goalLabels.length ? goalLabels : job?.goal_ids || []).map((lab) => (
                      <li key={lab}>{lab}</li>
                    ))}
                  </ul>
                </button>
                <button
                  type="button"
                  className={`step-btn hub-home${step === 'sample_desk' ? ' active' : ''}`}
                  onClick={() => goStep('sample_desk')}
                  data-tip={STEP_TIP.sample_desk}
                >
                  <span className="idx">台</span>
                  <span className="step-text">
                    {STEP_META.sample_desk}
                    <span className="hub-tag">中枢</span>
                  </span>
                </button>
                <div className="rail-spokes">
                  {(['upload_ocr', 'packet_unpack', 'field_confirm'] as const)
                    .filter(
                      (sid) =>
                        sid === 'upload_ocr' ||
                        (sid === 'packet_unpack' && job && packetNeedsReview(job)) ||
                        requiredSteps.includes(sid) ||
                        (sid === 'field_confirm' &&
                          ((job?.classified || []).length > 0 ||
                            requiredSteps.includes('relations_gate4') ||
                            requiredSteps.includes('evidence_match'))),
                    )
                    .map((sid) => (
                      <button
                        key={sid}
                        type="button"
                        className={`step-btn child${
                          step === sid ||
                          (sid === 'field_confirm' &&
                            (step === 'evidence_match' || step === 'relations_gate4'))
                            ? ' active'
                            : ''
                        }`}
                        onClick={() => void goStep(sid)}
                        data-tip={STEP_TIP[sid]}
                      >
                        <span className="tree-mark" aria-hidden />
                        <span className="idx">
                          {sid === 'field_confirm' ? '核' : sid === 'packet_unpack' ? '拆' : '传'}
                        </span>
                        <span className="step-text">{STEP_META[sid] || sid}</span>
                      </button>
                    ))}
                  <details
                    className="rail-detail-steps"
                    open={step === 'conclusion_gate5' || step === 'workbook_export'}
                  >
                    <summary className="rail-detail-summary">结论 / 导出</summary>
                    {(['conclusion_gate5', 'workbook_export'] as const)
                      .filter((sid) => requiredSteps.includes(sid) || sid === 'workbook_export')
                      .map((sid) => (
                        <button
                          key={sid}
                          type="button"
                          className={`step-btn child${step === sid ? ' active' : ''}`}
                          onClick={() => void goStep(sid)}
                          data-tip={STEP_TIP[sid]}
                        >
                          <span className="tree-mark" aria-hidden />
                          <span className="idx">{sid === 'workbook_export' ? '出' : '结'}</span>
                          <span className="step-text">{STEP_META[sid] || sid}</span>
                        </button>
                      ))}
                  </details>
                </div>
              </>
            )}
          </div>

          <details className="rail-section rail-tools">
            <summary className="rail-label">高级工具</summary>
            <button
              type="button"
              className={`step-btn root${step === 'hard_cases' ? ' active' : ''}`}
              onClick={() => goStep('hard_cases')}
              data-tip={STEP_TIP.hard_cases}
            >
              <span className="idx">录</span>
              {STEP_META.hard_cases}
            </button>
            <button
              type="button"
              className={`step-btn root${step === 'prompts' ? ' active' : ''}`}
              onClick={() => goStep('prompts')}
              data-tip={STEP_TIP.prompts}
            >
              <span className="idx">工</span>
              {STEP_META.prompts}
            </button>
          </details>

          {err && (
            <p className="err" style={{ padding: 8 }}>
              {err}
            </p>
          )}
          </div>
        </aside>
        <main className="main">
          {body()}
        </main>
      </div>
      <TipHost />
    </div>
  )
}
