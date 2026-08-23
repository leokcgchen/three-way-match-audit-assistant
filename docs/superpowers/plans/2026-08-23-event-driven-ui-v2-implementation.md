# Event-Driven Audit UI V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有抽凭工作台改造成“工程化预处理＋规则与 AI 后台处理＋事件驱动人工裁决”的 V2 系统，使正常项自动推进、异常项集中裁决、全过程可追溯。

**Architecture:** 保留现有 Job、业务链、拆包、字段、测试、顾问候选与导出门禁数据，以新的 `review_events` 投影层统一转换为前端事件；不复制业务事实，不引入第二套工作流状态机。前端将现有多个技术页面收敛为工作台、资料整理、异常裁决和检查导出四个主入口，旧动作通过统一事件卡调用现有受控 API。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、pytest；React 19、TypeScript 6、Vite 8、Vitest、Testing Library；现有 CSS 设计系统。

## Global Constraints

- 原版回退标签 `pre-v2-event-ui-20260823` 不得移动或覆盖。
- 当前开发只在 `feature/event-driven-ui-v2` 分支进行。
- 正常业务默认隐藏，无事件时不得要求人工逐笔确认。
- 混装 PDF 未完成页面边界与业务归属确认前不得正式识别。
- AI 建议必须携带置信度、文件、页码或证据区域以及人可读理由。
- 人工覆盖必须记录操作者、时间、理由、前值、后值和影响范围。
- 任何未关闭阻断事件、未定界页面或留痕缺失都必须阻断正式导出。
- 每个画面同一时刻只显示一个主按钮；低频操作进入“更多”。
- 不新增前端依赖，不替换现有 React、FastAPI 或测试框架。
- 所有实现步骤必须先写失败测试，再写最小实现，再运行相关测试。

---

## Planned File Structure

- Create `src/workflow/review_events.py`：把现有工作流事实投影为统一事件，不修改原始事实。
- Create `src/workflow/review_event_decisions.py`：把统一裁决请求路由到现有字段、顾问候选、finding 和结论 API。
- Modify `src/api/workflow_router.py`：增加事件列表、摘要和裁决端点。
- Modify `src/workflow/sample_desk.py`：工作台行附带事件数量、缺件和自动通过状态。
- Modify `src/workflow/export_readiness.py`：导出门禁引用统一阻断事件。
- Create `tests/test_review_events.py`：事件投影单元测试。
- Create `tests/test_review_event_decisions.py`：裁决、留痕和局部失效测试。
- Create `tests/test_review_events_api.py`：FastAPI 合同测试。
- Modify `web/src/types.ts`：定义 `ReviewEvent`、`ReviewEventSummary`、`ReviewDecision`。
- Modify `web/src/api.ts`：增加事件 API 客户端。
- Create `web/src/lib/reviewEvents.ts`：事件排序、筛选、CTA 和人话文案。
- Create `web/src/lib/reviewEvents.test.ts`：纯函数测试。
- Create `web/src/components/EventSummaryBar.tsx`：工作台异常、缺件、通过汇总。
- Create `web/src/components/EventDecisionCard.tsx`：原件、差异、建议和裁决动作。
- Create `web/src/pages/EventReviewPage.tsx`：统一异常裁决中心。
- Modify `web/src/pages/SampleWorkbenchPage.tsx`：工作台默认隐藏通过项、统一主按钮和批量上传。
- Modify `web/src/components/SampleDeskList.tsx`：行内事件摘要、查看已通过和上传。
- Modify `web/src/pages/PacketUnpackPage.tsx`：去掉批量确认正常项主动作，增加撤销、并入下一单和“更多”。
- Modify `web/src/lib/documentIntake.ts`：实现可测试的页面整理命令。
- Modify `web/src/pages/WorkbookPage.tsx`：统一“阻断项／生成并下载”主按钮。
- Modify `web/src/App.tsx`：收敛为工作台、待裁决、导出、更多四入口。
- Modify `web/src/styles.css`：实现异常优先、上下文按钮和独立滚动布局。
- Create/modify matching Vitest component and layout tests beside the affected files.

---

### Task 1: Restore a Green Build Baseline

**Files:**
- Modify: `web/src/lib/userJourney.ts`
- Modify: `web/src/lib/workflowGuide.ts`
- Modify only if assertions are stale: the 12 currently failing tests under `tests/`
- Modify only if behavior is defective: corresponding modules under `src/workflow/`, `src/api/`, and `src/legacy_ocr/`

**Interfaces:**
- Consumes: existing public functions and current baseline test expectations.
- Produces: `pytest tests -q`, `npm test`, and `npm run build` all exit 0 before V2 feature work.

- [ ] **Step 1: Capture the exact baseline failures**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest tests -q
```

Expected: reproduce the recorded 12 failures without new failures.

- [ ] **Step 2: Add or tighten regression assertions for each intended behavior**

For example, replace environment-dependent API calls in `tests/test_api_cutoff.py` with FastAPI `TestClient` and explicitly pass the required confirmation contract:

```python
response = client.post(
    "/api/v1/cutoff?fields_confirmed=true",
    json=payload,
    headers={"X-Fields-Confirmed": "true"},
)
assert response.status_code == 200
```

Expected: tests fail only where production behavior is genuinely inconsistent with the approved controlled workflow.

- [ ] **Step 3: Fix the two TypeScript unused-variable errors**

Remove unused `needMatch` from `userJourney.ts` and unused `sampleForChain` from `workflowGuide.ts`, or use them if their result is required by current behavior.

- [ ] **Step 4: Resolve backend regressions without weakening gates**

Keep field-confirmation, packet-confirmation, evidence and conclusion gates intact. Update stale tests when the current gate is the approved behavior; change production only when a test exposes loss of advisory blocking, date extraction, setting schema or summary copy.

- [ ] **Step 5: Verify all three baseline gates**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest tests -q
Set-Location web
& '.\node_modules\.bin\vitest.cmd' run
& '.\node_modules\.bin\tsc.cmd' -b
& '.\node_modules\.bin\vite.cmd' build
```

Expected: backend and frontend tests pass; TypeScript and Vite build exit 0.

- [ ] **Step 6: Commit**

```powershell
git add tests src web/src
git commit -m "fix: restore controlled workflow baseline"
```

---

### Task 2: Unified Review Event Projection

**Files:**
- Create: `src/workflow/review_events.py`
- Create: `tests/test_review_events.py`
- Modify: `src/workflow/sample_desk.py`

**Interfaces:**
- Consumes: `build_export_readiness(job)`, `build_conclusion_trace(job)`, `blocking_advisory_for_export(job)`, packet blockers, classified docs and desk chains.
- Produces:
  - `build_review_events(job: dict[str, Any]) -> list[dict[str, Any]]`
  - `review_event_summary(events: list[dict[str, Any]]) -> dict[str, int]`
  - stable event fields: `event_id`, `chain_id`, `event_type`, `severity`, `state`, `title`, `reason`, `evidence`, `ledger_value`, `observed_value`, `ai_suggestion`, `confidence`, `action_kind`, `action_step`, `source_ref`, `invalidates`.

- [ ] **Step 1: Write failing projection tests**

```python
def test_missing_invoice_becomes_blocking_event():
    events = build_review_events(job_missing_invoice())
    event = next(row for row in events if row["event_type"] == "MISSING_DOCUMENT")
    assert event["severity"] == "BLOCKING"
    assert event["chain_id"] == "SO25-0281"
    assert event["action_step"] == "sample_desk"

def test_clean_chain_has_no_manual_event():
    assert build_review_events(clean_completed_job()) == []
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_review_events.py -v`

Expected: import or assertion failure because the projection does not exist.

- [ ] **Step 3: Implement deterministic event IDs and normalization**

Use a stable SHA-256 digest of `job_id|chain_id|event_type|source_ref`; never use timestamps in IDs. Deduplicate repeated readiness/advisory/finding signals that describe the same business issue.

- [ ] **Step 4: Project all eight approved event families**

Create events for missing documents, low confidence, ledger mismatch, relationship ambiguity, rule conflict, audit-test failure, provenance gap and quality-sample selection. Preserve evidence and source references instead of copying only human-readable messages.

- [ ] **Step 5: Add event counts to desk rows**

Each row returned by `build_desk_chains` gains `event_count`, `blocking_event_count`, `missing_doc_types`, and `auto_passed`. `auto_passed` is true only when tests are complete and there are no open events.

- [ ] **Step 6: Verify and commit**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_review_events.py tests/test_desk_progress.py tests/test_required_docs.py -v`

```powershell
git add src/workflow/review_events.py src/workflow/sample_desk.py tests/test_review_events.py
git commit -m "feat: project workflow facts into review events"
```

---

### Task 3: Event API and Controlled Decisions

**Files:**
- Create: `src/workflow/review_event_decisions.py`
- Create: `tests/test_review_event_decisions.py`
- Create: `tests/test_review_events_api.py`
- Modify: `src/api/workflow_router.py`
- Modify: `src/audit/hitl_log.py`

**Interfaces:**
- Consumes: Task 2 `build_review_events`; existing field edit, advisory decision, finding acknowledgement, release and invalidation functions.
- Produces:
  - `GET /api/v1/workflow/jobs/{job_id}/events?state=OPEN&include_passed=false`
  - `POST /api/v1/workflow/jobs/{job_id}/events/{event_id}/decision`
  - `apply_review_decision(job_id: str, event_id: str, decision: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: Write API contract tests**

```python
def test_events_endpoint_hides_passed_by_default(client, seeded_job):
    response = client.get(f"/api/v1/workflow/jobs/{seeded_job}/events")
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"events", "summary"}
    assert all(row["state"] == "OPEN" for row in body["events"])

def test_override_requires_reason(client, event_job):
    response = client.post(
        f"/api/v1/workflow/jobs/{event_job.job_id}/events/{event_job.event_id}/decision",
        json={"decision": "OVERRIDE", "value": "2025-12-31", "reason": ""},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run and confirm 404/failure**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_review_events_api.py tests/test_review_event_decisions.py -v`

- [ ] **Step 3: Implement read endpoint**

Return events sorted by severity `BLOCKING > REVIEW > SAMPLE`, then risk score, then creation/source order. Return summary keys `open`, `blocking`, `missing`, `review`, `sample`, `passed`.

- [ ] **Step 4: Implement controlled decision routing**

Support `ACCEPT_AI`, `OVERRIDE`, `MANUAL_VALUE`, `AUDIT_FAIL`, and `DOCUMENT_ISSUE`. Reject unsupported decisions for an event's `action_kind`. Require a reason for override, audit fail and document issue. Missing-document events are resolved only through evidence upload, not by a decision shortcut.

- [ ] **Step 5: Persist audit trail and targeted invalidation**

Call `append_hitl_event` with event ID, evidence, before value, after value, reason, operator, affected targets and replay result. Reuse `JobStore.invalidate_targets` and existing replay functions rather than clearing the entire job.

- [ ] **Step 6: Verify and commit**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_review_events.py tests/test_review_events_api.py tests/test_review_event_decisions.py tests/test_hitl_gate_loop.py -v`

```powershell
git add src/workflow/review_event_decisions.py src/api/workflow_router.py src/audit/hitl_log.py tests/test_review_event_decisions.py tests/test_review_events_api.py
git commit -m "feat: add controlled review event API"
```

---

### Task 4: Frontend Event Types, Sorting and API Client

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Create: `web/src/lib/reviewEvents.ts`
- Create: `web/src/lib/reviewEvents.test.ts`

**Interfaces:**
- Consumes: Task 3 JSON contracts.
- Produces:
  - `ReviewEvent`, `ReviewEventSummary`, `ReviewDecisionRequest`
  - `sortReviewEvents(events)`
  - `eventPrimaryAction(event)`
  - `eventHumanReason(event)`
  - `api.listReviewEvents(jobId, options)`
  - `api.decideReviewEvent(jobId, eventId, body)`

- [ ] **Step 1: Write failing TypeScript tests**

```typescript
it('orders blocking events before review and sample events', () => {
  const sorted = sortReviewEvents([
    makeEvent({ severity: 'SAMPLE' }),
    makeEvent({ severity: 'BLOCKING' }),
  ])
  expect(sorted[0].severity).toBe('BLOCKING')
})

it('describes the required human decision in plain language', () => {
  expect(eventHumanReason(makeEvent({ event_type: 'LEDGER_MISMATCH' })))
    .toContain('账载值')
})
```

- [ ] **Step 2: Run and confirm import failure**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/lib/reviewEvents.test.ts`

- [ ] **Step 3: Add exact TypeScript contracts and API calls**

Use string unions for event type, severity, state and decision. Preserve unknown evidence payloads as `Record<string, unknown>`; do not use `any`.

- [ ] **Step 4: Implement pure sorting and copy helpers**

All event priority and button-label logic lives in `reviewEvents.ts`, not inside React components.

- [ ] **Step 5: Verify and commit**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/lib/reviewEvents.test.ts; & '.\node_modules\.bin\tsc.cmd' -b`

```powershell
git add web/src/types.ts web/src/api.ts web/src/lib/reviewEvents.ts web/src/lib/reviewEvents.test.ts
git commit -m "feat: add frontend review event contracts"
```

---

### Task 5: Simplify Navigation and Workbench

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/pages/SampleWorkbenchPage.tsx`
- Modify: `web/src/components/SampleDeskList.tsx`
- Create: `web/src/components/EventSummaryBar.tsx`
- Create: `web/src/components/EventSummaryBar.test.tsx`
- Modify: `web/src/components/BusinessWarehouseRow.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: event API and desk row event counts.
- Produces: four visible navigation roots `工作台`, `待裁决`, `导出`, `更多`; workbench main CTA `处理 N 个异常`; default hidden passed rows.

- [ ] **Step 1: Write failing navigation and workbench tests**

```typescript
it('shows only four top-level navigation entries', () => {
  render(<App />)
  expect(screen.getByRole('navigation')).toHaveTextContent('工作台')
  expect(screen.getByRole('navigation')).toHaveTextContent('待裁决')
  expect(screen.getByRole('navigation')).toHaveTextContent('导出')
  expect(screen.getByRole('navigation')).toHaveTextContent('更多')
  expect(screen.queryByText('上传凭证')).not.toBeInTheDocument()
})

it('hides auto-passed rows until 查看已通过 is pressed', async () => {
  render(<SampleDeskList rows={[openRow, passedRow]} />)
  expect(screen.queryByText(passedRow.chain_id)).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '查看已通过' }))
  expect(screen.getByText(passedRow.chain_id)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run and confirm failure**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/components/EventSummaryBar.test.tsx src/components/BusinessWarehouseRow.test.tsx`

- [ ] **Step 3: Replace technical rail steps with four business entries**

Keep existing internal step IDs for compatibility, but expose only business labels. Move prompts, hard cases, forced reruns and layout options into `更多`.

- [ ] **Step 4: Make workbench exception-first**

Render open rows first; hide `auto_passed` rows by default. The single primary CTA uses event summary to open packet review, event review or export blocker. Keep batch import and per-row `上传` as secondary/context actions.

- [ ] **Step 5: Add missing-document copy and passed toggle**

Generate client-ready text from structured `missing_doc_types`; never parse display strings to determine missing documents.

- [ ] **Step 6: Verify and commit**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run; & '.\node_modules\.bin\tsc.cmd' -b`

```powershell
git add web/src/App.tsx web/src/pages/SampleWorkbenchPage.tsx web/src/components/SampleDeskList.tsx web/src/components/EventSummaryBar.tsx web/src/components/EventSummaryBar.test.tsx web/src/components/BusinessWarehouseRow.tsx web/src/styles.css
git commit -m "feat: make the workbench exception first"
```

---

### Task 6: Complete the Engineering Packet Review Experience

**Files:**
- Modify: `web/src/lib/documentIntake.ts`
- Modify: `web/src/lib/documentIntake.test.ts`
- Modify: `web/src/pages/PacketUnpackPage.tsx`
- Modify: `web/src/pages/PacketUnpackPage.layout.test.ts`
- Modify: `web/src/components/PacketContactSheet.tsx`
- Modify: `web/src/components/PacketContactSheet.test.tsx`
- Modify: `web/src/components/PacketInspector.tsx`
- Modify: `web/src/components/PacketInspector.test.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: existing `PacketUnit`, packet analyze/confirm APIs.
- Produces:
  - `mergeUnitWithNext(units, unitId)`
  - `applyPacketCommand(units, command) -> PacketUnit[]`
  - reversible local history for split, merge, drop and restore.

- [ ] **Step 1: Write failing command and layout tests**

```typescript
it('merges a selected unit with the next contiguous unit', () => {
  const result = mergeUnitWithNext(twoContiguousUnits, 'u1')
  expect(result.filter((u) => !u.dropped)).toHaveLength(1)
  expect(result[0].pages).toEqual([1, 2])
})

it('keeps the large preview scrollable above the fixed gate', () => {
  const css = readFileSync('src/styles.css', 'utf8')
  expect(css).toMatch(/packet-review-main[^}]*overflow-y:\s*auto/s)
  expect(css).toMatch(/packet-full-preview[^}]*max-height:/s)
})
```

- [ ] **Step 2: Run and confirm failure**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/lib/documentIntake.test.ts src/pages/PacketUnpackPage.layout.test.ts`

- [ ] **Step 3: Implement reversible packet commands**

Store previous `PacketUnit[]` snapshots in a bounded stack of 20 entries. Disable undo when empty. Merges are allowed only for the same source file and contiguous pages.

- [ ] **Step 4: Align buttons with the approved V2 tree**

Remove `批量确认正常项` as a primary action. Keep `确认整理并识别` as the only primary button. Add `并入下一单`, `撤销上一步`, conditional selection actions, and a `更多` disclosure for re-analysis, blank-page cleanup and original-file access.

- [ ] **Step 5: Fix independent scrolling and bottom-gate obstruction**

The file list, contact sheet and inspector scroll independently. The large preview uses a bounded viewport with `overflow:auto`. Add bottom padding equal to or greater than the fixed gate height.

- [ ] **Step 6: Verify and commit**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/lib/documentIntake.test.ts src/pages/PacketUnpackPage.layout.test.ts src/components/PacketContactSheet.test.tsx src/components/PacketInspector.test.tsx`

```powershell
git add web/src/lib/documentIntake.ts web/src/lib/documentIntake.test.ts web/src/pages/PacketUnpackPage.tsx web/src/pages/PacketUnpackPage.layout.test.ts web/src/components/PacketContactSheet.tsx web/src/components/PacketContactSheet.test.tsx web/src/components/PacketInspector.tsx web/src/components/PacketInspector.test.tsx web/src/styles.css
git commit -m "feat: complete reversible packet review"
```

---

### Task 7: Build the Exception Adjudication Center

**Files:**
- Create: `web/src/pages/EventReviewPage.tsx`
- Create: `web/src/pages/EventReviewPage.test.tsx`
- Create: `web/src/components/EventDecisionCard.tsx`
- Create: `web/src/components/EventDecisionCard.test.tsx`
- Modify: `web/src/components/DocPreview.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: Task 4 event API; existing file preview URLs and evidence coordinates.
- Produces: a queue-driven adjudication page with one primary button `确认裁决并处理下一项`.

- [ ] **Step 1: Write failing component tests**

```typescript
it('shows evidence, ledger value, AI advice, confidence and trigger reason together', () => {
  render(<EventDecisionCard event={ledgerMismatchEvent} />)
  expect(screen.getByText('账载值')).toBeInTheDocument()
  expect(screen.getByText('AI 建议')).toBeInTheDocument()
  expect(screen.getByText(/置信度/)).toBeInTheDocument()
  expect(screen.getByText(ledgerMismatchEvent.reason)).toBeInTheDocument()
})

it('requires a reason before submitting an override', async () => {
  render(<EventReviewPage job={job} />)
  await user.click(screen.getByRole('button', { name: '覆盖 AI 结论' }))
  await user.click(screen.getByRole('button', { name: '确认裁决并处理下一项' }))
  expect(screen.getByRole('alert')).toHaveTextContent('请填写覆盖理由')
})
```

- [ ] **Step 2: Run and confirm failure**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/pages/EventReviewPage.test.tsx src/components/EventDecisionCard.test.tsx`

- [ ] **Step 3: Build queue navigation**

Load open events, preserve current event ID in URL/session state, and provide previous/next controls. After a successful decision, refetch events and open the next highest-priority event; return to workbench when none remain.

- [ ] **Step 4: Build the decision card**

Show source file, page, highlighted evidence, ledger value, observed value, AI suggestion, confidence, human-readable trigger and affected tests. Hide irrelevant fields instead of showing empty placeholders.

- [ ] **Step 5: Implement conditional decisions**

Render only decisions allowed by `action_kind`. Require reason for `OVERRIDE`, `AUDIT_FAIL`, and `DOCUMENT_ISSUE`. For `MISSING_DOCUMENT`, show `补充资料` and route to row upload rather than a fake resolve action.

- [ ] **Step 6: Preserve existing specialized panels**

Embed or deep-link existing amount ambiguity, field comparison and conclusion evidence panels when their event source requires specialized input. Do not duplicate their domain calculations in the new page.

- [ ] **Step 7: Verify and commit**

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/pages/EventReviewPage.test.tsx src/components/EventDecisionCard.test.tsx; & '.\node_modules\.bin\tsc.cmd' -b`

```powershell
git add web/src/pages/EventReviewPage.tsx web/src/pages/EventReviewPage.test.tsx web/src/components/EventDecisionCard.tsx web/src/components/EventDecisionCard.test.tsx web/src/components/DocPreview.tsx web/src/App.tsx web/src/styles.css
git commit -m "feat: add exception adjudication center"
```

---

### Task 8: Unify Export Blockers and the Final CTA

**Files:**
- Modify: `src/workflow/export_readiness.py`
- Modify: `tests/test_export_readiness_adapt.py`
- Modify: `web/src/pages/WorkbookPage.tsx`
- Create: `web/src/pages/WorkbookPage.v2.test.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: open blocking events and current workbook generation API.
- Produces: a readiness payload whose `blocked_count` equals open blocking events plus non-event structural gates, and exactly one active primary CTA.

- [ ] **Step 1: Write failing backend and frontend tests**

```python
def test_open_blocking_event_prevents_export():
    readiness = build_export_readiness(job_with_open_ledger_event())
    assert readiness["ready"] is False
    assert readiness["blocked_count"] >= 1
```

```typescript
it('shows only the blocker CTA while export is blocked', () => {
  render(<WorkbookPage job={blockedJob} />)
  expect(screen.getByRole('button', { name: /处理 2 个阻断项/ })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '生成并下载底稿' })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run and confirm failure**

Run backend and frontend tests for the two files.

- [ ] **Step 3: Make review events part of the readiness source of truth**

Deduplicate structural gates already represented as events. Keep exact action steps so the blocker CTA opens the right business and event.

- [ ] **Step 4: Simplify the export page**

When blocked, render `处理 N 个阻断项` as the only primary CTA. When ready, replace it with `生成并下载底稿`. Show preview and redownload only after a workbook exists.

- [ ] **Step 5: Verify and commit**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_export_readiness_adapt.py -v`

Run: `Set-Location web; & '.\node_modules\.bin\vitest.cmd' run src/pages/WorkbookPage.v2.test.tsx`

```powershell
git add src/workflow/export_readiness.py tests/test_export_readiness_adapt.py web/src/pages/WorkbookPage.tsx web/src/pages/WorkbookPage.v2.test.tsx web/src/styles.css
git commit -m "feat: unify event blockers and export"
```

---

### Task 9: Add Auto-Pass Quality Sampling

**Files:**
- Create: `src/workflow/quality_sampling.py`
- Create: `tests/test_quality_sampling.py`
- Modify: `config/settings.py`
- Modify: `.env.example`
- Modify: `src/workflow/review_events.py`
- Modify: `src/api/workflow_router.py`

**Interfaces:**
- Consumes: completed event-free chains and stable job/chain IDs.
- Produces:
  - `select_quality_samples(job, *, risk_rate: float, random_rate: float, seed: str) -> list[str]`
  - settings `QUALITY_RISK_SAMPLE_RATE` and `QUALITY_RANDOM_SAMPLE_RATE`
  - `QUALITY_SAMPLE` review events.

- [ ] **Step 1: Write deterministic sampling tests**

```python
def test_sampling_is_stable_for_same_job_and_seed():
    first = select_quality_samples(job, risk_rate=0.5, random_rate=0.1, seed="v2")
    second = select_quality_samples(job, risk_rate=0.5, random_rate=0.1, seed="v2")
    assert first == second

def test_open_or_failed_chains_are_not_quality_samples():
    assert "SO-RED" not in select_quality_samples(job_with_red_chain(), risk_rate=1, random_rate=1, seed="v2")
```

- [ ] **Step 2: Run and confirm failure**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_quality_sampling.py -v`

- [ ] **Step 3: Implement deterministic risk and random selection**

Use stable hashing, not process-global randomness. Risk candidates include material amounts, cutoff-window proximity and complex relationships. Sampling never changes the underlying audit result until a human finds an error.

- [ ] **Step 4: Project sample events and close outcomes**

Quality events have severity `SAMPLE`, appear in the adjudication center, and record `CORRECT` or `FALSE_NEGATIVE`. A false negative creates a real blocking/review event with the discovered reason.

- [ ] **Step 5: Verify and commit**

Run: `& '.venv\Scripts\python.exe' -m pytest tests/test_quality_sampling.py tests/test_review_events.py -v`

```powershell
git add src/workflow/quality_sampling.py tests/test_quality_sampling.py config/settings.py .env.example src/workflow/review_events.py src/api/workflow_router.py
git commit -m "feat: sample auto-passed audit chains"
```

---

### Task 10: End-to-End Migration and Quality Gate

**Files:**
- Create: `tests/test_event_driven_v2_e2e.py`
- Create: `web/src/App.v2.test.tsx`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-23-event-driven-ui-v2-design.md` only if implementation exposes a clarified, approved constraint.

**Interfaces:**
- Consumes: Tasks 1–9.
- Produces: one validated workflow from sample import through packet review, auto-pass/event routing, human adjudication and export.

- [ ] **Step 1: Write the backend end-to-end test**

```python
def test_v2_normal_and_exception_paths(client):
    job_id = create_job_with_two_samples(client)
    upload_standard_complete_sample(client, job_id, "SO-GREEN")
    upload_mixed_missing_invoice_sample(client, job_id, "SO-RED")
    process_job(client, job_id)
    events = client.get(f"/api/v1/workflow/jobs/{job_id}/events").json()
    assert all(row["chain_id"] != "SO-GREEN" for row in events["events"])
    assert any(row["chain_id"] == "SO-RED" for row in events["events"])
    assert client.get(f"/api/v1/workflow/jobs/{job_id}/export-readiness").json()["ready"] is False
```

- [ ] **Step 2: Write the frontend journey test**

Cover these visible states: four-entry navigation, workbench hides green, `处理 N 个异常` opens the exact event, packet page has one main CTA, override requires reason, resolving the last blocker unlocks `生成并下载底稿`.

- [ ] **Step 3: Run the complete automated suite**

```powershell
& '.venv\Scripts\python.exe' -m pytest tests -q
Set-Location web
& '.\node_modules\.bin\vitest.cmd' run
& '.\node_modules\.bin\tsc.cmd' -b
& '.\node_modules\.bin\vite.cmd' build
```

Expected: all commands exit 0.

- [ ] **Step 4: Run visual and ergonomic QA at desktop and narrow widths**

Verify at 1440×900, 1280×720 and 768×1024:

- workbench shows multiple rows without horizontal overflow;
- packet thumbnails and large preview scroll to their bottom;
- fixed gate never covers content;
- only one primary button is visible per screen state;
- keyboard focus reaches upload, toggles, page selection and decision controls;
- event evidence and decision actions remain visible without overlapping.

- [ ] **Step 5: Update user-facing documentation**

Document the four navigation entries, normal/exception paths, packet boundary confirmation, event decisions, quality sampling and safe rollback tag.

- [ ] **Step 6: Final security and repository checks**

Run `git diff --check`; verify `.env`, client uploads, exports, browser profiles and local OCR cache are not tracked. Confirm `pre-v2-event-ui-20260823^{}` still resolves to `a31d558`.

- [ ] **Step 7: Commit**

```powershell
git add tests/test_event_driven_v2_e2e.py web/src/App.v2.test.tsx README.md
git commit -m "test: verify event-driven audit workflow v2"
```

---

## Spec Coverage Self-Review

- Engineering preprocessing and PDF split/merge: Tasks 2 and 6.
- Deterministic rules before AI: Task 2 preserves and projects existing rule outputs.
- AI confidence, evidence and rationale: Tasks 2, 3 and 7.
- No-event auto-pass and hidden passed rows: Tasks 2 and 5.
- Eight event families: Tasks 2, 3 and 9.
- Human final decision and reasoned override: Tasks 3 and 7.
- Targeted replay and audit trail: Task 3.
- Four-entry navigation and one primary CTA: Tasks 5–8.
- Export blockers: Task 8.
- Risk/random quality sampling: Task 9.
- Current-to-V2 migration and complete validation: Task 10.
- No placeholders or undefined cross-task interfaces remain in this plan.
