/** 工作台枢纽：根据任务状态算出唯一「下一步」，文案尽量短而明确。 */

import type { Job } from '../types'
import {
  chainHasFail,
  earliestSweepPhase,
  isMultiChainJob,
  listGospdChainIds,
  type SweepPhase,
} from './chainProgress'
import { activeSample, isGospdJob } from './chainDocs'
import { resultOverallStatus } from './testStatus'
import { journeyProgressPlan } from './userJourney'

export type GuideAction =
  | { kind: 'go'; step: string }
  | { kind: 'run'; test: 'evidence' | 'amount' | 'contract' | 'three_way' }
  | { kind: 'run_batch' }
  | { kind: 'batch_review' }
  | { kind: 'confirm_matching_all' }
  | { kind: 'release' }
  | { kind: 'linkage' }
  | { kind: 'switch_chain'; chain_id: string }
  | { kind: 'focus_advisory' }
  | { kind: 'ingest_ledger' }
  | { kind: 'none' }

export type GuideStep = {
  id: string
  label: string
  /** done | current | todo | blocked */
  state: 'done' | 'current' | 'todo' | 'blocked'
}

export type WorkflowGuide = {
  headline: string
  detail: string
  ctaLabel: string
  action: GuideAction
  steps: GuideStep[]
  canRunTests: boolean
  /** 多笔横扫时：当前全局步骤上仍待办的业务笔 */
  sweepPending?: string[]
  sweepPhase?: SweepPhase
}

export type GuideOptions = {
  /** 来自 /chains 的权威链列表；缺省则从 job 推断 */
  chainIds?: string[]
}

function hasDocs(job: Job): boolean {
  return (job.classified || []).length > 0 || (job.pending_files || []).length > 0
}

function hasClassified(job: Job): boolean {
  return (job.classified || []).length > 0
}

function pendingNeedProcess(job: Job): boolean {
  return (job.pending_files || []).length > 0
}

export function packetNeedsReview(job: Job): boolean {
  const hasDeclaredPacket = (job.pending_files || []).some(
    (p) => !p.from_packet && p.mixed_packet_declared === true,
  )
  if (!hasDeclaredPacket) return false
  const st = String(job.packet_run?.status || '')
  if (st === 'needs_review' || st === 'pending_analyze' || st === 'analyzing') return true
  return (job.pending_files || []).some(
    (p) =>
      !p.from_packet &&
      p.mixed_packet_declared === true &&
      (p.packet_kind === 'packet_single_chain' || p.packet_kind === 'packet_multi_chain'),
  )
}

function testDone(
  ctx: Record<string, unknown> | Job,
  key: 'evidence' | 'amount_test' | 'contract_terms' | 'three_way',
): boolean {
  const v = ctx[key]
  return Boolean(v && typeof v === 'object')
}

function testFail(
  ctx: Record<string, unknown> | Job,
  key: 'evidence' | 'amount_test' | 'contract_terms' | 'three_way',
): boolean {
  const st = resultOverallStatus(ctx[key]).toUpperCase()
  return st.includes('FAIL') || st.includes('未通过') || st.includes('ERROR')
}

function pushSteps(
  steps: GuideStep[],
  plan: Array<{ id: string; label: string; done: boolean; current: boolean; blocked?: boolean }>,
) {
  for (const p of plan) {
    steps.push({
      id: p.id,
      label: p.label,
      state: p.done ? 'done' : p.blocked ? 'blocked' : p.current ? 'current' : 'todo',
    })
  }
}

function phaseLabel(phase: SweepPhase): string {
  switch (phase) {
    case 'fields':
      return '核对字段'
    case 'evidence':
      return '串单（匹配）'
    case 'gate4':
      return '串单'
    case 'tests':
      return '跑测试'
    case 'gate5':
      return '确认结论'
    case 'export':
      return '导出底稿'
    default:
      return '完成'
  }
}

/**
 * 多笔横扫：同一步把所有笔做完再进下一步；当前笔已完成该步则引导切换。
 * 先后上传导致只剩一笔未齐时，自然退化为逐笔。
 */
function buildMultiChainSweepGuide(job: Job, chainIds: string[]): WorkflowGuide {
  const required = job.plan?.required_steps || []
  const needGate4 = required.includes('relations_gate4')
  const needGate5 = required.includes('conclusion_gate5')
  const needExport = required.includes('workbook_export')

  const { phase, pending, flags } = earliestSweepPhase(job, chainIds)
  const active = String(job.active_chain_id || '').trim()
  const activeNeeds = pending.includes(active)
  const nextPending = pending.find((cid) => cid !== active) || pending[0]

  const allFields = flags.every((f) => f.fieldsOk)
  const allGate4 = !needGate4 || flags.every((f) => f.gate4Ok)
  const allTests = flags.every((f) => f.testsOk)
  const allGate5 = !needGate5 || flags.every((f) => f.gate5Ok)
  const exported = Boolean(job.workbook_path || (job.workbook_paths || []).length)

  const steps: GuideStep[] = []
  pushSteps(
    steps,
    journeyProgressPlan({
      goalsOk: true,
      ledgerOk: true,
      uploadOk: true,
      fieldsOk: allFields,
      matchOk: allGate4,
      testsOk: allTests,
      conclusionOk: allGate5,
      exported,
      needMatch: needGate4,
      needConclusion: needGate5,
      needExport,
    }),
  )

  const nextPhaseHint =
    phase === 'fields' || phase === 'evidence' || phase === 'gate4'
      ? '跑测试'
      : phase === 'tests'
        ? needGate5
          ? '确认结论'
          : '导出底稿'
        : phase === 'gate5'
          ? '导出底稿'
          : '完成'
  const sweepNote = `建议：同一步横扫各笔（本步还剩 ${pending.length} 笔），全部齐了再进「${nextPhaseHint}」；先后上传的新笔会回到它缺的最早步骤。`

  const activeFlags = flags.find((f) => f.chainId === active)
  const activeDone = Boolean(
    active &&
      activeFlags?.fieldsOk &&
      activeFlags?.gate4Ok &&
      activeFlags?.testsOk &&
      activeFlags?.gate5Ok,
  )

  if (phase === 'export') {
    return {
      headline: '各笔已齐，可以导出底稿',
      detail: '不通过的笔只要已人工确认，也会写入底稿。一次导出全部业务。',
      ctaLabel: '去导出',
      action: { kind: 'go', step: 'workbook_export' },
      steps,
      canRunTests: true,
      sweepPhase: phase,
    }
  }

  if (phase === 'done') {
    return {
      headline: '全部业务笔已完成',
      detail: '可再传凭证（新笔从它缺的最早步骤接着做）；或复查导出。',
      ctaLabel: '上传下一笔',
      action: { kind: 'go', step: 'upload_ocr' },
      steps,
      canRunTests: true,
      sweepPhase: 'done',
    }
  }

  // 当前笔已收口、清单还有待办：点开看结果，不要把人拦在横扫上
  if (activeDone) {
    const others = pending.filter((cid) => cid !== active)
    const failed = Boolean(active && chainHasFail(job, active))
    return {
      headline: failed ? '本笔已确认不通过' : '本笔已通过，可看审阅结果',
      detail: others.length
        ? `${failed ? '打开可再看原因' : '测试已跑完，打开即可查看'}。其余还要处理：${others.join('、')}。清单全部收口后才能导出底稿。`
        : failed
          ? '打开可再看原因。'
          : '测试已跑完，打开即可查看。',
      ctaLabel: '看本笔结果',
      action: { kind: 'go', step: 'conclusion_gate5' },
      steps,
      canRunTests: true,
      sweepPending: pending,
      sweepPhase: phase,
    }
  }

  // 当前笔已做完本步 → 切到下一笔
  if (pending.length > 0 && !activeNeeds && nextPending) {
    return {
      headline: `切换到 ${nextPending} 继续「${phaseLabel(phase)}」`,
      detail: `当前笔本步已齐。${sweepNote}`,
      ctaLabel: `切换到 ${nextPending}`,
      action: { kind: 'switch_chain', chain_id: nextPending },
      steps,
      canRunTests: false,
      sweepPending: pending,
      sweepPhase: phase,
    }
  }

  if (phase === 'fields' || phase === 'evidence' || phase === 'gate4') {
    return {
      headline: `核对字段（${pending.length}/${chainIds.length} 笔待完成）`,
      detail: '红灯笔进核对页补缺字段；齐了自动往下跑。',
      ctaLabel: '去核对字段',
      action: { kind: 'go', step: 'field_confirm' },
      steps,
      canRunTests: false,
      sweepPending: pending,
      sweepPhase: phase,
    }
  }

  if (phase === 'tests') {
    return {
      headline: `跑测试（还差 ${pending.length} 笔）`,
      detail: sweepNote,
      ctaLabel: '一键审阅',
      action: { kind: 'batch_review' },
      steps,
      canRunTests: true,
      sweepPending: pending,
      sweepPhase: phase,
    }
  }

  if (phase === 'gate5') {
    const anyFail = active ? chainHasFail(job, active) : false
    return {
      headline: `确认结论（${pending.length}/${chainIds.length} 笔待完成）`,
      detail: anyFail
        ? `${sweepNote} 确认结论时可批量记本笔单据问题。`
        : sweepNote,
      ctaLabel: '确认结论',
      action: { kind: 'release' },
      steps,
      canRunTests: true,
      sweepPending: pending,
      sweepPhase: phase,
    }
  }

  return {
    headline: '暂无待办',
    detail: '从工作台开始即可。',
    ctaLabel: '留在工作台',
    action: { kind: 'none' },
    steps,
    canRunTests: false,
    sweepPhase: phase,
  }
}

/**
 * 算出当前唯一下一步。
 * GOSPD 多笔：步骤横扫（同一步做完所有笔再进下一步）。
 * 单笔 / 非 GOSPD：仍按当前笔竖向推进。
 */
export function buildWorkflowGuide(job: Job, opts?: GuideOptions): WorkflowGuide {
  const required = job.plan?.required_steps || []
  const needGate4 = required.includes('relations_gate4')
  const needGate5 = required.includes('conclusion_gate5')
  const needExport = required.includes('workbook_export')
  const needEvidence = required.includes('evidence_match')
  const needAmount = required.includes('amount_test')
  const needContract = required.includes('contract_terms')
  const needThree = required.includes('three_way_cutoff')

  const goalsOk = (job.goal_ids || []).length > 0
  const classifiedOk = hasClassified(job)
  const popOk = Boolean((job.sample_population?.business_ids || []).length)

  const steps: GuideStep[] = []

  const journeyOpts = {
    needMatch: needGate4,
    needConclusion: needGate5,
    needExport,
  }

  if (!goalsOk) {
    pushSteps(
      steps,
      journeyProgressPlan({
        goalsOk: false,
        ledgerOk: false,
        uploadOk: false,
        fieldsOk: false,
        matchOk: false,
        testsOk: false,
        conclusionOk: false,
        exported: false,
        ...journeyOpts,
      }),
    )
    return {
      headline: '先选底稿目标',
      detail: '选好目标后，系统会按该底稿列出必做步骤。',
      ctaLabel: '去选目标',
      action: { kind: 'go', step: 'goals' },
      steps,
      canRunTests: false,
    }
  }

  if (!popOk && !hasDocs(job)) {
    pushSteps(
      steps,
      journeyProgressPlan({
        goalsOk: true,
        ledgerOk: false,
        uploadOk: false,
        fieldsOk: false,
        matchOk: false,
        testsOk: false,
        conclusionOk: false,
        exported: false,
        ...journeyOpts,
      }),
    )
    return {
      headline: '先上传抽样清单',
      detail: '在工作台立样本笔。入账日和金额留给后面测试，这里只看业务号是否对上。',
      ctaLabel: '上传抽样清单',
      action: { kind: 'ingest_ledger' },
      steps,
      canRunTests: false,
    }
  }

  if (!classifiedOk) {
    const waitingOcr = pendingNeedProcess(job)
    pushSteps(
      steps,
      journeyProgressPlan({
        goalsOk: true,
        ledgerOk: popOk,
        uploadOk: false,
        fieldsOk: false,
        matchOk: false,
        testsOk: false,
        conclusionOk: false,
        exported: false,
        ...journeyOpts,
      }),
    )
    if (packetNeedsReview(job)) {
      return {
        headline: '混装凭证：先拆包分笔',
        detail: '看切开是否对，改类型并拖到对应业务笔，确认后再识别。未确认不能进字段核对。',
        ctaLabel: '去拆包分笔',
        action: { kind: 'go', step: 'packet_unpack' },
        steps,
        canRunTests: false,
      }
    }
    if (waitingOcr || job.ocr_processing) {
      return {
        headline: job.ocr_processing ? '正在识别单据…' : '点「开始处理」完成识别',
        detail: job.ocr_processing
          ? '识别完成后回到工作台，会提示下一步。'
          : '识别完成后才能核对字段和跑测试。',
        ctaLabel: job.ocr_processing ? '查看识别进度' : '去开始识别',
        action: { kind: 'go', step: 'upload_ocr' },
        steps,
        canRunTests: false,
      }
    }
    return {
      headline: '上传凭证（可一次多笔）',
      detail: '先在工作台立笔，再在本页传 PDF/图片。新笔从它缺的最早步骤接着做。',
      ctaLabel: '去上传凭证',
      action: { kind: 'go', step: 'upload_ocr' },
      steps,
      canRunTests: false,
    }
  }

  const chainIds =
    opts?.chainIds?.filter((c) => c && c !== '未识别业务号') ||
    (isGospdJob(job) ? listGospdChainIds(job) : [])

  if (isMultiChainJob(job, chainIds)) {
    return buildMultiChainSweepGuide(job, chainIds)
  }

  // —— 单笔 / 非 GOSPD：原竖向逻辑 ——
  const sample = isGospdJob(job) ? activeSample(job) : null
  const ctx: Record<string, unknown> | Job = sample || job
  const fieldsOk = Boolean(sample ? sample.fields_confirmed : job.fields_confirmed)
  const gate4Ok =
    !needGate4 || Boolean((sample && sample.matching_confirmed) || job.matching_confirmed)
  const evidenceOk = !needEvidence || testDone(ctx, 'evidence')
  const contractOk = !needContract || testDone(ctx, 'contract_terms')
  const amountOk = !needAmount || testDone(ctx, 'amount_test')
  const threeOk = !needThree || testDone(ctx, 'three_way')
  const testsOk = evidenceOk && contractOk && amountOk && threeOk
  const gate5Ok = !needGate5 || Boolean(sample ? sample.conclusion_confirmed : job.conclusion_confirmed)
  const exported = Boolean(job.workbook_path || (job.workbook_paths || []).length)

  const singleJourney = (mark: {
    fieldsOk: boolean
    matchOk: boolean
    testsOk: boolean
    conclusionOk: boolean
    exported: boolean
  }) =>
    journeyProgressPlan({
      goalsOk: true,
      ledgerOk: true,
      uploadOk: true,
      ...mark,
      ...journeyOpts,
    })

  if (!fieldsOk) {
    pushSteps(
      steps,
      singleJourney({
        fieldsOk: false,
        matchOk: false,
        testsOk: false,
        conclusionOk: false,
        exported: false,
      }),
    )
    return {
      headline: '核对字段',
      detail: '红灯笔对照原件补缺字段。齐了会自动往下跑测试。',
      ctaLabel: '去核对字段',
      action: { kind: 'go', step: 'field_confirm' },
      steps,
      canRunTests: false,
    }
  }

  if ((needEvidence && !evidenceOk) || (needGate4 && !gate4Ok)) {
    pushSteps(
      steps,
      singleJourney({
        fieldsOk: true,
        matchOk: false,
        testsOk: false,
        conclusionOk: false,
        exported: false,
      }),
    )
    return {
      headline: '跑测试',
      detail: '字段已齐，自动跑匹配与必测，不必停下来确认串单。',
      ctaLabel: '一键审阅',
      action: { kind: 'batch_review' },
      steps,
      canRunTests: true,
    }
  }

  if (!testsOk) {
    const missing: string[] = []
    if (needContract && !contractOk) missing.push('合同条款')
    if (needAmount && !amountOk) missing.push('金额')
    if (needThree && !threeOk) missing.push('三单+截止')
    pushSteps(
      steps,
      singleJourney({
        fieldsOk: true,
        matchOk: true,
        testsOk: false,
        conclusionOk: false,
        exported: false,
      }),
    )
    return {
      headline: missing.length ? `跑测试（还差 ${missing.join('、')}）` : '跑测试',
      detail: '自动补跑本笔缺失必测。',
      ctaLabel: '一键审阅',
      action: { kind: 'batch_review' },
      steps,
      canRunTests: true,
    }
  }

  const anyFail =
    (needEvidence && testFail(ctx, 'evidence')) ||
    (needAmount && testFail(ctx, 'amount_test')) ||
    (needContract && testFail(ctx, 'contract_terms')) ||
    (needThree && testFail(ctx, 'three_way'))

  if (needGate5 && !gate5Ok) {
    pushSteps(
      steps,
      singleJourney({
        fieldsOk: true,
        matchOk: true,
        testsOk: true,
        conclusionOk: false,
        exported: false,
      }),
    )
    return {
      headline: anyFail ? '确认结论（可批量记单据问题）' : '确认结论',
      detail: anyFail
        ? '确认时将未处理的不通过项一并记为单据问题；详情可追溯。'
        : '确认后可导出底稿。',
      ctaLabel: '确认结论',
      action: { kind: 'release' },
      steps,
      canRunTests: true,
    }
  }

  if (needExport && !exported) {
    pushSteps(
      steps,
      singleJourney({
        fieldsOk: true,
        matchOk: true,
        testsOk: true,
        conclusionOk: true,
        exported: false,
      }),
    )
    return {
      headline: '可以导出底稿了',
      detail: '生成官方格式工作底稿并下载。',
      ctaLabel: '去导出',
      action: { kind: 'go', step: 'workbook_export' },
      steps,
      canRunTests: true,
    }
  }

  pushSteps(
    steps,
    singleJourney({
      fieldsOk: true,
      matchOk: true,
      testsOk: true,
      conclusionOk: true,
      exported: true,
    }),
  )

  if (hasDocs(job)) {
    return {
      headline: '本笔已完成',
      detail: '可上传下一笔业务继续审；或复查导出文件。',
      ctaLabel: '上传下一笔',
      action: { kind: 'go', step: 'upload_ocr' },
      steps,
      canRunTests: true,
    }
  }

  return {
    headline: '暂无待办',
    detail: '从工作台开始即可。',
    ctaLabel: '留在工作台',
    action: { kind: 'none' },
    steps,
    canRunTests: false,
  }
}

/** 工作台/顶栏主按钮：名字短，悬停补一句「点了会怎样」。 */
export function guideCtaTip(guide: WorkflowGuide): string {
  const a = guide.action
  switch (a.kind) {
    case 'batch_review':
      return '对本笔自动跑匹配和必测；不改你已确认的字段。'
    case 'release':
      return '确认本笔结论可进入导出；不通过项可一并记为单据问题。'
    case 'linkage':
      return '确认本笔合同/订单/发票等已对上号，之后才能跑测试。'
    case 'switch_chain':
      return `切到业务 ${a.chain_id}，继续当前这一步。`
    case 'confirm_matching_all':
      return '把待确认的单据关系全部采纳为正式勾稽。'
    case 'run_batch':
      return '一次跑完本笔还缺的测试项。'
    case 'run':
      return '只跑这一项测试。'
    case 'ingest_ledger':
      return '上传抽样清单（Excel）。工作台立样本笔；日期金额留给测试。'
    case 'focus_advisory':
      return '先处理系统提出的观察项，不挡日常核对。'
    case 'go': {
      const map: Record<string, string> = {
        field_confirm: '打开核对页：红灯笔对照原件补缺字段。',
        packet_unpack: '打开拆包页：切错就合并/拆开，再拖到对应业务笔。',
        upload_ocr: '去上传凭证或查看识别进度。',
        goals: '选择本次要做的底稿目标。',
        workbook_export: '检查门禁后生成 Excel 底稿。',
        conclusion_gate5: '打开结论页：看对不上的数据，确认是不通过还是单据问题。',
        relations_gate4: '确认本笔单据勾稽关系。',
        evidence_match: '查看或重跑单据匹配。',
        sample_desk: '回到工作台看下一步。',
        amount_test: '打开金额测试明细。',
        contract_terms: '打开合同条款测试明细。',
        three_way_cutoff: '打开三单匹配与截止测试明细。',
      }
      return map[a.step] || guide.detail
    }
    default:
      return guide.detail
  }
}

