import type {
  CoverageMap,
  Job,
  FieldResolution,
  PromptCatalog,
  ReviewDecisionRequest,
  ReviewEventsResponse,
  WorkpaperGoal,
  WorkflowPlan,
} from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData
        ? {}
        : { 'Content-Type': 'application/json' }),
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    let jobFromError: unknown
    try {
      const body = await res.json()
      if (body?.detail && typeof body.detail === 'object' && body.detail.message) {
        detail = String(body.detail.message)
        jobFromError = body.detail.job
      } else {
        detail =
          typeof body.detail === 'string'
            ? body.detail
            : JSON.stringify(body.detail || body)
      }
    } catch {
      /* ignore */
    }
    const err = new Error(detail) as Error & { job?: unknown }
    if (jobFromError) err.job = jobFromError
    throw err
  }
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json() as Promise<T>
  return res as unknown as T
}

export type LedgerOption = {
  label: string
  posting_date: string
  biz_id?: string | null
  row_idx?: number
}

export type WorkbookPreview = {
  sheets: string[]
  sheet: string | null
  columns: string[]
  rows: Array<Record<string, string>>
  path?: string
  note?: string
}

export type ReceiptChoice = {
  index: number
  file_name?: string
  date?: string
  label: string
}

export type ExportReadinessStage = {
  id: string
  label: string
  status: string
  blocking: boolean
  reason: string
  action?: { step: string; label: string } | null
  affected_groups?: string[]
}

export type ExportReadiness = {
  schema_version: string
  ready: boolean
  summary: string
  blocked_count: number
  stages: ExportReadinessStage[]
  lights?: DeskLights
}

export type AmountCandidate = {
  candidate_id: string
  value: number | string
  raw_value?: string
  currency?: string
  tax_basis?: string
  label?: string
  source_type?: string
  evidence?: { raw_text?: string; page?: number }
  validation?: Array<{ code: string; passed: boolean; message?: string }>
}

export type AmountAmbiguity = {
  ambiguity_id: string
  file_name: string
  field_key: string
  field_name?: string
  status: string
  trigger_reasons?: string[]
  candidates: AmountCandidate[]
  ai_recommendation?: {
    candidate_id?: string | null
    reason?: string
    confidence?: number
    model?: string
    review_status?: string
    provider?: string
  } | null
  human_decision?: Record<string, unknown> | null
}

export type ChainInfo = {
  chain_id: string
  display_index?: string
  order_numbers?: string[]
  doc_count: number
  doc_types?: string[]
  file_names?: string[]
  tested?: boolean
  has_contract?: boolean
  has_amount?: boolean
  has_three_way?: boolean
  matching_confirmed?: boolean
  complete_set?: boolean
  is_active?: boolean
  /** null=未导入抽样清单；true/false=是否在清单内 */
  in_sample_population?: boolean | null
  light?: 'wait' | 'red' | 'green' | 'yellow'
  reason?: string
  label?: string
  missing_fields?: string[]
  missing_labels?: string[]
  present_labels?: string[]
  missing_doc_labels?: string[]
  uncertain_doc_labels?: string[]
  doc_slots?: Array<{ id: string; label: string; status: string; any_of?: string[] }>
  request_docs?: string[]
  diff_lines?: string[]
  required_fields?: Array<{
    key: string
    label?: string
    filled?: boolean
    source_types?: string[]
  }>
  event_count?: number
  blocking_event_count?: number
  missing_doc_types?: string[]
  auto_passed?: boolean
}

export type DeskLights = {
  green: number
  yellow: number
  red: number
  wait: number
  issues?: string[]
  request_docs?: string[]
  progress?: {
    sample_total?: number
    done?: number
    docs_missing?: number
    fields_missing?: number
    match_exception?: number
    fail_confirmed?: number
    await_human?: number
    in_progress?: number
  }
  legend?: Record<string, string>
}

export type MesMatchRule = {
  rule_id: string
  label: string
  layer?: string
  left?: string
  right?: string
  formula?: string
  pass_when?: string
  fail_example?: string
}

export const api = {
  health: () => req<{ status: string; phase?: string }>('/health'),
  ocrStatus: () =>
    req<{ configured: boolean; message?: string }>('/api/v1/workflow/ocr-status'),
  visionStatus: () =>
    req<{
      enabled: boolean
      configured: boolean
      model?: string
      api_url?: string
      prompt_version?: string
    }>('/api/v1/workflow/vision-status'),
  listAmountAmbiguities: (
    jobId: string,
    chainId?: string,
    opts?: { rescan?: boolean },
  ) => {
    const qs = new URLSearchParams()
    if (chainId) qs.set('chain_id', chainId)
    if (opts?.rescan) qs.set('rescan', 'true')
    const q = qs.toString() ? `?${qs.toString()}` : ''
    return req<{ items: AmountAmbiguity[]; count: number }>(
      `/api/v1/workflow/jobs/${jobId}/amount-ambiguities${q}`,
    )
  },
  scanAmountAmbiguities: (jobId: string, chainId?: string, enrich = true) => {
    const qs = new URLSearchParams()
    if (chainId) qs.set('chain_id', chainId)
    qs.set('enrich', enrich ? 'true' : 'false')
    const q = `?${qs.toString()}`
    return req<{
      job: Job
      items: AmountAmbiguity[]
      count: number
      enrich?: Record<string, unknown>
    }>(`/api/v1/workflow/jobs/${jobId}/amount-ambiguities/scan${q}`, { method: 'POST' })
  },
  enrichAmountAmbiguities: (jobId: string, chainId?: string) => {
    const q = chainId ? `?chain_id=${encodeURIComponent(chainId)}` : ''
    return req<{
      job: Job
      items: AmountAmbiguity[]
      count: number
      enrich?: Record<string, unknown>
    }>(`/api/v1/workflow/jobs/${jobId}/amount-ambiguities/enrich${q}`, { method: 'POST' })
  },
  decideAmountAmbiguity: (
    jobId: string,
    ambiguityId: string,
    body: {
      decision: 'ACCEPT_CANDIDATE' | 'MANUAL_VALUE' | 'DEFER'
      candidate_id?: string
      value?: number | string
      reason?: string
    },
  ) =>
    req<{ job: Job; ambiguity: AmountAmbiguity }>(
      `/api/v1/workflow/jobs/${jobId}/amount-ambiguities/${encodeURIComponent(ambiguityId)}/decide`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  aiReviewAmountAmbiguity: (jobId: string, ambiguityId: string, page = 0) =>
    req<{ job: Job; review: Record<string, unknown>; page?: Record<string, unknown> }>(
      `/api/v1/workflow/jobs/${jobId}/amount-ambiguities/${encodeURIComponent(ambiguityId)}/ai-review?page=${page}`,
      { method: 'POST' },
    ),
  prompts: () => req<PromptCatalog>('/api/v1/workflow/prompts/catalog'),
  listGoals: () => req<{ goals: WorkpaperGoal[] }>('/api/v1/workflow/goals'),
  previewPlan: (goal_ids: string[]) =>
    req<WorkflowPlan>('/api/v1/workflow/plan', {
      method: 'POST',
      body: JSON.stringify({ goal_ids }),
    }),
  listJobs: () => req<{ jobs: Job[] }>('/api/v1/workflow/jobs'),
  createJob: (title = '') =>
    req<Job>('/api/v1/workflow/jobs', {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  getJob: (jobId: string) => req<Job>(`/api/v1/workflow/jobs/${jobId}`),
  listReviewEvents: (
    jobId: string,
    options?: { state?: 'OPEN' | 'RESOLVED' | 'ALL'; includePassed?: boolean },
  ) => {
    const qs = new URLSearchParams()
    if (options?.state) qs.set('state', options.state)
    if (options?.includePassed) qs.set('include_passed', 'true')
    const query = qs.toString() ? `?${qs.toString()}` : ''
    return req<ReviewEventsResponse>(
      `/api/v1/workflow/jobs/${jobId}/events${query}`,
    )
  },
  decideReviewEvent: (
    jobId: string,
    eventId: string,
    body: ReviewDecisionRequest,
  ) =>
    req<{
      decision: Record<string, unknown>
      audit_event: Record<string, unknown>
      job: Job
    }>(
      `/api/v1/workflow/jobs/${jobId}/events/${encodeURIComponent(eventId)}/decision`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  setGoals: (
    jobId: string,
    goal_ids: string[],
    opts?: {
      period_end?: string
      entity_name?: string
      calendar_mode?: string
      fiscal_year_start?: string
    },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/goals`, {
      method: 'PUT',
      body: JSON.stringify({
        goal_ids,
        period_end: opts?.period_end || undefined,
        entity_name: opts?.entity_name || undefined,
        calendar_mode: opts?.calendar_mode || undefined,
        fiscal_year_start: opts?.fiscal_year_start || undefined,
      }),
    }),
  setSamplePopulation: (
    jobId: string,
    body: { business_ids: string[]; source?: string; note?: string },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/sample-population`, {
      method: 'PUT',
      body: JSON.stringify({
        business_ids: body.business_ids,
        source: body.source || 'workbench_import',
        note: body.note || '',
      }),
    }),
  importSampleExcel: (jobId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<Job>(`/api/v1/workflow/jobs/${jobId}/sample-population/excel`, {
      method: 'POST',
      body: fd,
    })
  },
  setActiveStep: (jobId: string, step_id: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/active-step`, {
      method: 'PATCH',
      body: JSON.stringify({ step_id }),
    }),
  listChains: (jobId: string) =>
    req<{
      chains: ChainInfo[]
      lights?: DeskLights
      active_chain_id?: string | null
      gospd_mode?: boolean
      sample_population?: Job['sample_population']
    }>(`/api/v1/workflow/jobs/${jobId}/chains`),
  mesMatchRules: () =>
    req<{ version?: string; purpose?: string; rules: MesMatchRule[] }>(
      `/api/v1/workflow/mes-match-rules`,
    ),
  previewChains: (jobId: string) =>
    req<{
      chains: Array<{
        chain_id: string
        doc_count: number
        file_names: string[]
        doc_types: string[]
        pending_only?: boolean
      }>
      total_files: number
      gospd_mode?: boolean
    }>(`/api/v1/workflow/jobs/${jobId}/chains/preview`),
  setActiveChain: (jobId: string, chain_id: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/active-chain`, {
      method: 'PUT',
      body: JSON.stringify({ chain_id }),
    }),
  setChainCompleteSet: (jobId: string, chainId: string, completeSet: boolean) =>
    req<Job>(
      `/api/v1/workflow/jobs/${jobId}/chains/${encodeURIComponent(chainId)}/complete-set`,
      {
        method: 'PUT',
        body: JSON.stringify({ complete_set: completeSet }),
      },
    ),
  upload: async (
    jobId: string,
    files: File[],
    opts?: {
      force?: boolean
      process?: boolean
      businessHints?: Record<string, string[]>
      mixedPacket?: boolean
    },
  ) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    if (opts?.force) fd.append('force', 'true')
    fd.append('process', opts?.process === true ? 'true' : 'false')
    if (opts?.businessHints) {
      fd.append('business_hints', JSON.stringify(opts.businessHints))
    }
    if (opts?.mixedPacket) fd.append('mixed_packet', 'true')
    return req<Job>(`/api/v1/workflow/jobs/${jobId}/upload`, { method: 'POST', body: fd })
  },
  deleteScopeException: (jobId: string, exceptionId: string) =>
    req<Job>(
      `/api/v1/workflow/jobs/${jobId}/scope-exceptions/${encodeURIComponent(exceptionId)}`,
      { method: 'DELETE' },
    ),
  process: (jobId: string, opts?: { force?: boolean; fileNames?: string[] }) => {
    const q = opts?.force ? '?force=true' : ''
    return req<Job>(`/api/v1/workflow/jobs/${jobId}/process${q}`, {
      method: 'POST',
      body: JSON.stringify({ file_names: opts?.fileNames || [] }),
    })
  },
  fieldCatalog: () => req<import('./types').FieldCatalog>('/api/v1/workflow/field-catalog'),
  putFieldPlan: (
    jobId: string,
    plan: import('./types').FieldPlan,
    opts?: { confirm?: boolean },
  ) => {
    const q = opts?.confirm ? '?confirm=true' : ''
    return req<Job>(`/api/v1/workflow/jobs/${jobId}/field-plan${q}`, {
      method: 'PUT',
      body: JSON.stringify({
        by_type: plan.by_type,
        global_extra: plan.global_extra || [],
        confirmed: opts?.confirm ? true : undefined,
      }),
    })
  },
  patchPendingType: (jobId: string, file_name: string, doc_type: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/pending/type`, {
      method: 'PATCH',
      body: JSON.stringify({ file_name, doc_type }),
    }),
  classifyLight: (jobId: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/classify-light`, { method: 'POST' }),
  packetAnalyze: (jobId: string, body?: { file_modes?: Record<string, string>; use_vlm?: boolean }) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/packet/analyze`, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    }),
  declareMixed: (jobId: string, file_name: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/documents/declare-mixed`, {
      method: 'POST',
      body: JSON.stringify({ file_name }),
    }),
  packetConfirm: (
    jobId: string,
    body: {
      units: Array<{
        unit_id: string
        source_file: string
        source_path?: string
        pages: number[]
        doc_type: string
        card_type?: string
        dropped?: boolean
        chain_id: string
        business_ids?: string[]
        suggested_doc_type?: string
        doc_type_source?: 'ai' | 'human'
        boundary_confirmed?: boolean
        business_binding_source?: 'human'
        drop_reason?: string
        keys?: Record<string, string>
      }>
      file_modes?: Record<string, string>
      start_ocr?: boolean
    },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/packet/confirm`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  fileUrl: (jobId: string, fileName: string) =>
    `/api/v1/workflow/jobs/${jobId}/documents/${encodeURIComponent(fileName)}/file`,
  highlightUrl: (jobId: string, fileName: string, field: string, value?: string) => {
    const q = new URLSearchParams({ field })
    if (value != null && String(value).trim()) q.set('value', String(value).trim())
    return `/api/v1/workflow/jobs/${jobId}/documents/${encodeURIComponent(fileName)}/highlight?${q}`
  },
  previewPage: async (jobId: string, fileName: string, page = 0) => {
    const url = `/api/v1/workflow/jobs/${jobId}/documents/${encodeURIComponent(fileName)}/preview-page?page=${page}`
    const res = await fetch(url)
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = await res.json()
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body)
      } catch {
        /* ignore */
      }
      throw new Error(detail)
    }
    const blob = await res.blob()
    return {
      blob,
      meta: {
        page_index: Number(res.headers.get('X-Page-Index') || page),
        page_count: Number(res.headers.get('X-Page-Count') || 1),
        pdf_width: Number(res.headers.get('X-Pdf-Width') || 0),
        pdf_height: Number(res.headers.get('X-Pdf-Height') || 0),
        image_width: Number(res.headers.get('X-Image-Width') || 0),
        image_height: Number(res.headers.get('X-Image-Height') || 0),
        kind: res.headers.get('X-Preview-Kind') || '',
      },
    }
  },
  textBlocks: (jobId: string, fileName: string, page = 0) =>
    req<{
      page_index: number
      page_count: number
      blocks: Array<{ id: string; text: string; bbox: number[]; source?: string }>
      kind?: string
    }>(
      `/api/v1/workflow/jobs/${jobId}/documents/${encodeURIComponent(fileName)}/text-blocks?page=${page}`,
    ),
  captureText: (
    jobId: string,
    fileName: string,
    body: {
      page_index: number
      x0: number
      y0: number
      x1: number
      y1: number
      field?: string
    },
  ) =>
    req<{
      text: string
      source?: string
      message?: string
      parts?: string[]
      page_index?: number
    }>(`/api/v1/workflow/jobs/${jobId}/documents/${encodeURIComponent(fileName)}/capture-text`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  patchFields: (
    jobId: string,
    body: {
      file_name: string
      fields: Record<string, unknown>
      doc_type?: string
      custom_doc_type_name?: string
      doc_type_confirmed?: boolean
      reason?: string
    },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/documents/fields`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  reclassify: (jobId: string, file_name: string, doc_type: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/documents/reclassify`, {
      method: 'POST',
      body: JSON.stringify({ file_name, doc_type }),
    }),
  confirmFields: (jobId: string, chainId: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/hitl/fields/confirm`, {
      method: 'POST',
      body: JSON.stringify({ chain_id: chainId }),
    }),
  confirmChainLinkage: (
    jobId: string,
    opts?: { auto_evidence?: boolean; auto_accept_relations?: boolean },
  ) =>
    req<{
      job: Job
      fields_confirmed: boolean
      matching_confirmed: boolean
      message: string
      next_action?: string
      pending_relation_count?: number
      evidence_seeded?: boolean
    }>(`/api/v1/workflow/jobs/${jobId}/hitl/chain-linkage/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        auto_evidence: opts?.auto_evidence ?? true,
        auto_accept_relations: opts?.auto_accept_relations ?? true,
      }),
    }),
  releaseActiveChain: (jobId: string, body?: { reason?: string; ack_unacked?: boolean }) =>
    req<{
      job: Job
      acknowledged_finding_ids: string[]
      message: string
    }>(`/api/v1/workflow/jobs/${jobId}/hitl/chain/release`, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    }),
  gapFillFields: (jobId: string, scope: 'active' | 'all' = 'active') =>
    req<{ job: Job; summary: Record<string, unknown> }>(
      `/api/v1/workflow/jobs/${jobId}/fields/gap-fill?scope=${encodeURIComponent(scope)}`,
      { method: 'POST' },
    ),
  uploadLedger: async (jobId: string, file: File) => {
    const fd = new FormData()
    fd.append('ledger', file)
    return req<Job>(`/api/v1/workflow/jobs/${jobId}/ledger`, { method: 'POST', body: fd })
  },
  applyLedger: (jobId: string, mapping?: Record<string, string | null>) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/ledger/apply`, {
      method: 'POST',
      body: JSON.stringify({ mapping: mapping || {} }),
    }),
  ledgerOptions: (jobId: string) =>
    req<{ options: LedgerOption[]; count: number }>(
      `/api/v1/workflow/jobs/${jobId}/ledger/options`,
    ),
  manualLedgerMatch: (jobId: string, body: { file_name: string; label?: string; row_idx?: number }) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/ledger/manual-match`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  evidenceMatch: (jobId: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/evidence-match`, { method: 'POST' }),
  adoptDisambiguation: (jobId: string, proposal: Record<string, unknown>) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/disambiguation/adopt`, {
      method: 'POST',
      body: JSON.stringify({ proposal }),
    }),
  relations: (jobId: string) =>
    req<{ relations: unknown[]; duplicates: unknown }>(
      `/api/v1/workflow/jobs/${jobId}/relations`,
    ),
  decideRelation: (jobId: string, rid: string, status: string, reason = '') =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/relations/${encodeURIComponent(rid)}/decide`, {
      method: 'POST',
      body: JSON.stringify({ status, reason }),
    }),
  verifyAllRelations: (jobId: string, reason = '') =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/relations/verify-all`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  confirmMatching: (jobId: string, reason = '') =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/hitl/matching/confirm`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  confirmBusinessGroup: (jobId: string, groupId: string, reason = '审计师确认业务组捆绑') =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/hitl/business-groups/confirm`, {
      method: 'POST',
      body: JSON.stringify({ group_id: groupId, reason }),
    }),
  moveBusinessGroupDocument: (
    jobId: string,
    fileName: string,
    targetGroupId: string,
    reason = '人工调整业务组',
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/business-groups/move-document`, {
      method: 'POST',
      body: JSON.stringify({
        file_name: fileName,
        target_group_id: targetGroupId,
        reason,
      }),
    }),
  acknowledgeDuplicates: (jobId: string, reason = '') =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/duplicates/acknowledge`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  amountTest: (jobId: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/amount-test`, { method: 'POST' }),
  contractTerms: (jobId: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/contract-terms`, { method: 'POST' }),
  threeWay: (
    jobId: string,
    body?: { manual?: Record<string, unknown>; receipt_idx?: number | null },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/three-way-cutoff`, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    }),
  batchReview: (jobId: string, forceRerun = false) =>
    req<{
      job: Job
      summary: string
      ran: Array<{ chain_id: string; actions: string[] }>
      skipped: Array<{ chain_id: string; reason: string }>
      failed: Array<{ chain_id: string; step?: string; error: string }>
      need_gate4: string[]
    }>(`/api/v1/workflow/jobs/${jobId}/batch-review`, {
      method: 'POST',
      body: JSON.stringify({ force_rerun: forceRerun }),
    }),
  confirmMatchingAll: (jobId: string) =>
    req<{
      job: Job
      summary: string
      confirmed: string[]
      blocked: Array<{ chain_id: string; reason: string }>
    }>(`/api/v1/workflow/jobs/${jobId}/hitl/matching/confirm-all`, {
      method: 'POST',
    }),
  receiptChoices: (jobId: string) =>
    req<{ choices: ReceiptChoice[] }>(`/api/v1/workflow/jobs/${jobId}/receipt-choices`),
  confirmConclusion: (jobId: string, reason = '', asFail = false) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/hitl/conclusion/confirm`, {
      method: 'POST',
      body: JSON.stringify({ reason, as_fail: asFail }),
    }),
  conclusionTrace: (jobId: string, chainId?: string) => {
    const q = chainId ? `?chain_id=${encodeURIComponent(chainId)}` : ''
    return req<import('./types').ConclusionTrace>(
      `/api/v1/workflow/jobs/${jobId}/conclusion-trace${q}`,
    )
  },
  acknowledgeFinding: (
    jobId: string,
    body: { finding_id: string; genuine?: boolean; reason?: string },
  ) =>
    req<{ job: Job; trace: import('./types').ConclusionTrace }>(
      `/api/v1/workflow/jobs/${jobId}/hitl/finding/acknowledge`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      },
    ),
  acknowledgeFindingBatch: (
    jobId: string,
    body?: { genuine?: boolean; reason?: string; scope?: 'active' | 'all'; chain_id?: string },
  ) =>
    req<{
      job: Job
      trace: import('./types').ConclusionTrace
      acknowledged_finding_ids: string[]
      message: string
    }>(`/api/v1/workflow/jobs/${jobId}/hitl/finding/acknowledge-batch`, {
      method: 'POST',
      body: JSON.stringify(body || { scope: 'active', genuine: true }),
    }),
  verifyFieldRow: (
    jobId: string,
    body: { chain_id?: string; field_key: string; verified: boolean; reason?: string },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/hitl/field-row/verify`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  refreshFieldResolution: (jobId: string, chainId: string, force = false) =>
    req<FieldResolution>(`/api/v1/workflow/jobs/${jobId}/field-resolution/refresh`, {
      method: 'POST',
      body: JSON.stringify({ chain_id: chainId, force }),
    }),
  decideFieldResolutionEdge: (
    jobId: string,
    edgeId: string,
    body: { chain_id: string; decision: 'CONFIRMED' | 'REJECTED'; reason: string },
  ) =>
    req<FieldResolution>(
      `/api/v1/workflow/jobs/${jobId}/field-resolution/edges/${encodeURIComponent(edgeId)}/decision`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  previewWorkbookRows: (jobId: string) =>
    req<import('./types').WorkbookRowsPreview>(
      `/api/v1/workflow/jobs/${jobId}/workbook-rows/preview`,
    ),
  putWorkbookRowEdits: (
    jobId: string,
    body: { format: string; chain_id: string; edits: Record<string, string> },
  ) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/workbook-rows/edits`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  interpret: (jobId: string, family: string, payload?: Record<string, unknown>) =>
    req<{ interpretation: Record<string, unknown>; job: Job }>(
      `/api/v1/workflow/jobs/${jobId}/interpret`,
      {
        method: 'POST',
        body: JSON.stringify({ family, payload }),
      },
    ),
  exportWorkbook: (jobId: string) =>
    req<Job>(`/api/v1/workflow/jobs/${jobId}/workbook/export`, { method: 'POST' }),
  exportReadiness: (jobId: string) =>
    req<ExportReadiness>(`/api/v1/workflow/jobs/${jobId}/export-readiness`),
  listAdvisory: (jobId: string) =>
    req<{
      counts: Record<string, number>
      pending: Array<Record<string, unknown>>
      candidates: Array<Record<string, unknown>>
    }>(`/api/v1/workflow/jobs/${jobId}/advisory`),
  decideAdvisory: (
    jobId: string,
    candidateId: string,
    status: 'VERIFIED' | 'REJECTED',
    reason = '',
    auto_replay = false,
  ) =>
    req<{
      job: Job
      replayed?: string[]
      skipped?: string[]
    }>(`/api/v1/workflow/jobs/${jobId}/advisory/${encodeURIComponent(candidateId)}/decide`, {
      method: 'POST',
      // 默认不自动复跑：避免 Gate4 后接受顾问又清匹配、逼用户重点确认
      body: JSON.stringify({ status, reason, auto_replay }),
    }),
  workbookPreview: (jobId: string, sheet?: string, format?: string) => {
    const q = new URLSearchParams()
    if (sheet) q.set('sheet', sheet)
    if (format) q.set('format', format)
    const qs = q.toString()
    return req<WorkbookPreview>(
      `/api/v1/workflow/jobs/${jobId}/workbook/preview${qs ? `?${qs}` : ''}`,
    )
  },
  workbookDownloadUrl: (jobId: string, format?: string) => {
    const q = format ? `?format=${encodeURIComponent(format)}` : ''
    return `/api/v1/workflow/jobs/${jobId}/workbook/download${q}`
  },
  coverage: (jobId: string) =>
    req<CoverageMap>(`/api/v1/workflow/jobs/${jobId}/coverage`),
}
