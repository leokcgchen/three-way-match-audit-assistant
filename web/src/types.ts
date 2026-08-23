export type WorkpaperGoal = {
  goal_id: string
  label: string
  description: string
  workbook_sheets: string[]
}

export type StepLabel = { step_id: string; label: string }

export type WorkflowPlan = {
  goal_ids: string[]
  goals: WorkpaperGoal[]
  required_steps: string[]
  step_labels: StepLabel[]
  required_dimensions: string[]
  workbook_sheets: string[]
  skipped_steps: string[]
  note?: string
}

export type ClassifiedDoc = {
  file_name: string
  path?: string
  doc_type: string
  ocr_source?: string
  raw_text?: string
  text_blocks?: unknown[]
  fields?: Record<string, unknown>
  _field_meta?: Record<
    string,
    {
      status?: string
      accepted_value?: unknown
      normalized_candidate?: unknown
      raw_value?: unknown
    }
  >
  manual_edited?: boolean
  error?: string
  ledger_match_ok?: boolean
  ledger_posting_date?: string
  ledger_matched_biz_id?: string
  ledger_amount?: number | string
  ledger_match_manual?: boolean
  ledger_evaluated?: boolean
  ledger_match_message?: string
  manual_override?: boolean
  excluded_from_match?: boolean
  business_group_id?: string
  business_group_manual?: boolean
}

export type RelationRow = {
  relation_id: string
  from_id?: string
  to_id?: string
  rel_type?: string
  status?: string
  shared_keys?: string[]
  excerpt?: string
  note?: string
  extra?: {
    from_role?: string
    to_role?: string
    source?: string
    [key: string]: unknown
  }
}

export type AdvisoryCandidate = {
  candidate_id: string
  task_type?: string
  kind?: string
  status?: string
  business_id?: string
  fingerprint?: string
  payload?: Record<string, unknown>
  evidence?: { excerpt?: string; source_doc?: string }
  note?: string
}

export type ReviewEventType =
  | 'MISSING_DOCUMENT'
  | 'LOW_CONFIDENCE'
  | 'LEDGER_MISMATCH'
  | 'RELATIONSHIP_AMBIGUITY'
  | 'RULE_CONFLICT'
  | 'AUDIT_TEST_FAILED'
  | 'PROVENANCE_GAP'
  | 'QUALITY_SAMPLE'

export type ReviewEventSeverity = 'BLOCKING' | 'REVIEW' | 'SAMPLE'
export type ReviewEventState = 'OPEN' | 'RESOLVED'
export type ReviewDecision =
  | 'ACCEPT_AI'
  | 'OVERRIDE'
  | 'MANUAL_VALUE'
  | 'AUDIT_FAIL'
  | 'DOCUMENT_ISSUE'

export type ReviewEvent = {
  event_id: string
  chain_id: string
  event_type: ReviewEventType
  severity: ReviewEventSeverity
  state: ReviewEventState
  title: string
  reason: string
  evidence: Record<string, unknown>
  ledger_value: unknown
  observed_value: unknown
  ai_suggestion: unknown
  confidence: number | null
  action_kind:
    | 'UPLOAD_EVIDENCE'
    | 'REVIEW_FIELD'
    | 'DECIDE_ADVISORY'
    | 'DECIDE_FINDING'
    | 'REVIEW_EVIDENCE'
    | 'REVIEW_SAMPLE'
  action_step: string
  source_ref: string
  invalidates: string[]
  resolved_at?: string
  operator?: string
  decision?: ReviewDecision
  decision_reason?: string
}

export type ReviewEventSummary = {
  open: number
  blocking: number
  missing: number
  review: number
  sample: number
  passed: number
}

export type ReviewDecisionRequest = {
  decision: ReviewDecision
  value?: unknown
  reason?: string
  operator?: string
}

export type ReviewEventsResponse = {
  events: ReviewEvent[]
  summary: ReviewEventSummary
}

export type PacketUnit = {
  unit_id: string
  source_file: string
  source_path?: string
  page_start: number
  page_end: number
  pages: number[]
  card_type?: string
  doc_type: string
  host_type?: string
  split_reason?: string
  chain_id: string
  business_ids?: string[]
  suggested_doc_type?: string
  doc_type_source?: 'ai' | 'human'
  boundary_confirmed?: boolean
  business_binding_source?: 'human'
  drop_reason?: string
  confirmed_at?: string | null
  confirmed_by?: string | null
  keys?: Record<string, string>
  excerpt?: string
  needs_review?: boolean
  review_reasons?: string[]
  dropped?: boolean
  type_candidates?: Array<{ id: string; label?: string; score?: number }>
}

export type PacketFile = {
  file_name: string
  path?: string
  kind?: string
  page_count?: number
  sha256?: string
}

export type PacketRun = {
  run_id?: string
  status?: string
  created_at?: string | null
  confirmed_at?: string | null
  warnings?: string[]
  files?: PacketFile[]
  pages?: Array<{
    source_file: string
    page: number
    quality?: string
    page_role?: string
    needs_review?: boolean
    text_preview?: string
  }>
}

export type Job = {
  job_id: string
  title: string
  goal_ids: string[]
  plan: WorkflowPlan
  classified: ClassifiedDoc[]
  fields_confirmed: boolean
  fields_confirm_sig?: string | null
  active_step: string
  period_end?: string | null
  entity_name?: string | null
  /** natural_month | fiscal_445 | period_end_only */
  calendar_mode?: string | null
  fiscal_year_start?: string | null
  sample_population?: {
    business_ids?: string[]
    count?: number
    source?: string
    note?: string
    imported_at?: string
    cannot_claim?: string
    rows?: Array<{
      business_id?: string
      book_date?: string
      book_amount?: number | null
      customer?: string
      sheet?: string
    }>
    sheets?: string[]
  } | null
  evidence?: Record<string, unknown> | null
  relations?: RelationRow[]
  duplicates?: {
    findings?: Array<Record<string, unknown>>
    summary?: Record<string, unknown>
    blocks_downstream_hint?: boolean
    auditor_override?: { acknowledged?: boolean; reason?: string; at?: string }
    ran?: boolean
    version?: string
  }
  matching_confirmed?: boolean
  amount_test?: Record<string, unknown> | null
  contract_terms?: Record<string, unknown> | null
  three_way?: Record<string, unknown> | null
  three_way_match?: Record<string, unknown> | null
  cutoff_test?: Record<string, unknown> | null
  conclusion_confirmed?: boolean
  manual_three_way?: Record<string, unknown> | null
  business_group_confirmations?: Record<string, unknown> | null
  ledger_path?: string
  ledger_rows?: Array<Record<string, unknown>> | null
  ledger_columns?: string[] | null
  ledger_mapping?: Record<string, string | null> | null
  ledger_auto_ok?: boolean | null
  ledger_standard_map?: Record<string, string> | null
  workbook_path?: string
  workbook_paths?: Array<{
    format: string
    label: string
    path: string
    file_name: string
  }>
  advisory_candidates?: AdvisoryCandidate[]
  ocr_issues?: Array<Record<string, unknown>> | string[]
  ocr_processing?: boolean
  auto_review_processing?: boolean
  ocr_processing_message?: string | null
  ocr_progress?: { done: number; total: number; file?: string } | null
  active_chain_id?: string | null
  updated_at?: string | null
  gospd_sample_results?: Record<string, Record<string, unknown>> | null
  field_plan?: FieldPlan | null
  workbook_row_edits?: Record<string, Record<string, Record<string, unknown>>> | null
  finding_acknowledgements?: Record<
    string,
    { genuine?: boolean; reason?: string; at?: string }
  >
  field_row_verifications?: Record<
    string,
    Record<string, { verified?: boolean; at?: string; reason?: string }>
  > | null
  pending_files?: Array<{
    file_name: string
    path?: string
    slot_hint?: string
    size?: number
    doc_type?: string
    doc_type_source?: string
    light_confident?: boolean
    packet_kind?: string
    page_count?: number
    from_packet?: boolean
    declared_business_ids?: string[]
    upload_source?: 'business_row' | 'mixed_packet'
  }>
  packet_run?: PacketRun | null
  packet_units?: PacketUnit[]
  packet_confirmed?: boolean
  review_event_decisions?: Record<string, Record<string, unknown>>
}

export type FieldPlanSlot = {
  system_required: string[]
  selected_optional: string[]
  custom: string[]
}

export type FieldPlan = {
  confirmed?: boolean
  confirmed_at?: string | null
  global_extra?: string[]
  by_type?: Record<string, FieldPlanSlot>
}

export type FieldCatalog = {
  doc_types: string[]
  field_labels: Record<string, string>
  by_type: Record<
    string,
    {
      system_required: Array<{ key: string; label: string; locked?: boolean }>
      optional: Array<{ key: string; label: string; locked?: boolean }>
    }
  >
}

export type WorkbookRowPreview = {
  chain_id: string
  sample_no?: number
  system: {
    all_ok?: string
    exception?: string
    period_ok?: string
    formula_v?: string
    formula_conflict?: string
    customer?: string
  }
  values: { all_ok?: string; exception?: string }
  edits?: Record<string, string>
  readonly_formula?: Record<string, string>
  w_options?: string[]
}

export type WorkbookRowsPreview = {
  supported: boolean
  format?: string | null
  label?: string
  message?: string
  editable?: Array<{ key: string; label: string; kind?: string }>
  readonly_formula?: Array<{ key: string; label: string; hint?: string }>
  rows: WorkbookRowPreview[]
}

export type ConclusionFinding = {
  finding_id: string
  chain_id?: string | null
  step: string
  step_label: string
  module?: string
  title: string
  status: string
  blocking: boolean
  method: string
  summary: string
  fields_used: Array<{
    doc_type?: string
    file_name?: string
    field_key?: string
    field_label?: string
    value?: unknown
  }>
  comparisons?: Array<Record<string, unknown>>
  period?: Record<string, unknown>
  decision?: string | null
  decision_reasons?: string[]
  hold_reason_code?: string | null
  quantity_roles?: Record<string, unknown>
  slot_reasons?: Record<string, string>
  erp_review?: { status?: string; note?: string }
  go_field_confirm?: boolean
  retest_path?: string
  acknowledged?: boolean
  ack_reason?: string
}

export type ConclusionTrace = {
  chain_id?: string | null
  findings: ConclusionFinding[]
  blocking_count: number
  unacked_blocking_count: number
  /** GOSPD：仅当前笔未确认的阻塞项（Gate5 放行看这个） */
  unacked_blocking_count_active?: number
  blocking_count_active?: number
  can_confirm_as_genuine_path: boolean
  message: string
}


export type CoverageMap = {
  dimensions: Array<{
    dimension_id: string
    label: string
    status: string
    note?: string
    result_status?: string
  }>
  filtered_by_goals?: string[]
  summary?: Record<string, number>
  program_matrix?: {
    version?: string
    selected_goals?: string[]
    note?: string
    matrices?: Array<{
      goal_id?: string
      label?: string
      assertions?: Array<{
        id?: string
        label?: string
        ceavop?: string
        programs?: string[]
        evidence?: string[]
        cannot_claim?: string
      }>
    }>
  } | null
}

export type PromptCatalog = {
  prompt_version?: string
  system_prompt?: string
  wired_count?: number
  design_only_count?: number
  entries?: Array<Record<string, unknown>>
  principles?: string[]
}
