# One-to-Many Three-Way Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate deterministic one-to-many order/receipt/invoice allocation into the existing audit workflow so all documents in an auditor-controlled business group are accumulated, explained, persisted, and shown without breaking single-document matching or cutoff testing.

**Architecture:** Add a focused paper-fulfillment module under `src/three_way_match` and call it from the existing workflow before the legacy scalar matcher. Keep `ThreeWayMatchRequest` and cutoff APIs backward-compatible; persist one-to-many output as an additive `fulfillment` view. Store `complete_set` per chain in `gospd_sample_results`, expose it through `/chains`, and control it from the existing React business upload row.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, React 19, TypeScript 6, Vitest, Testing Library, Vite.

## Global Constraints

- `business_group_id` remains the authoritative human grouping signal; AI must not silently override it.
- Default semantics are “documents uploaded so far”; `complete_set` defaults to `false`.
- Partial fulfillment is yellow while `complete_set=false`; the same partial state is red with `SET_CLAIMED_INCOMPLETE` when `complete_set=true`.
- Over-receipt, over-invoice, duplicate allocation, and explicit amount overage are red.
- Ambiguous or unbound lines require human review and cannot auto-pass.
- One-to-many fulfillment must not alter the existing control-transfer/posting-date cutoff formula or trading-mode override.
- OCR-derived `postingDate` must not be treated as authoritative ledger posting evidence.
- Preserve the scalar `ThreeWayMatchRequest`, existing API response keys, exports, and single-document tests.
- Do not import the source project as a runtime dependency; copy only test fixtures and reimplement the approved semantics in target-native code.
- Do not copy the independent FastAPI service, SQLite task store, static UI, ERP simulator, or all 75 source rules.
- Do not commit, merge, or remove the current worktree; the user will handle branch integration.

---

### Task 1: Build the deterministic line allocator and rollup

**Files:**
- Create: `src/three_way_match/one_to_many.py`
- Create: `tests/test_three_way_one_to_many.py`
- Create: `tests/fixtures/one_to_many/classified_complete.json`
- Create: `tests/fixtures/one_to_many/classified_partial.json`
- Copy binary fixtures: `tests/fixtures/one_to_many/PO_SO001.pdf`, `GRN_POD001.pdf`, `GRN_POD002.pdf`, `GRN_POD003.pdf`, `INV_INV001_complete.pdf`, `INV_INV001_partial.pdf`

**Interfaces:**
- Consumes: target classified items shaped as `{file_name, doc_type, business_group_id?, fields: {orderNo?, documentNo?, items?}}`.
- Produces: `run_one_to_many(classified: list[dict[str, Any]], *, complete_set: bool = False, business_group_id: str | None = None) -> dict[str, Any]`.
- Output keys: `complete_set`, `light`, `flags`, `role_files`, `allocations`, `rows`, `summary`, `quantity_roles`, `amount_roles`.

- [ ] **Step 1: Copy the approved JSON/PDF fixtures into the target test tree**

Copy from:

```text
D:\Codex\抽凭系统设计\audit-image-lab\_review_three_way\match_engine_v1_1\tests\fixtures\one_to_many
```

Copy only the two JSON files and six named PDFs listed above. Do not copy source caches or generated acceptance directories.

- [ ] **Step 2: Write the failing complete-rollup test**

```python
def test_complete_fixture_accumulates_all_three_receipts() -> None:
    pack = _load_fixture("classified_complete.json")
    result = run_one_to_many(pack["classified"])
    row = result["rows"][0]
    assert row["ordered_qty"] == "100"
    assert row["received_qty"] == "100"
    assert row["invoiced_qty"] == "100"
    assert row["light"] == "GREEN"
    assert result["role_files"]["receipt"] == [
        "GRN_POD001.pdf", "GRN_POD002.pdf", "GRN_POD003.pdf"
    ]
```

- [ ] **Step 3: Run the test and verify RED**

Run:

```powershell
& 'D:\抽凭—合同合规性审阅agent\.venv\Scripts\python.exe' -m pytest tests\test_three_way_one_to_many.py::test_complete_fixture_accumulates_all_three_receipts -q -p no:cacheprovider
```

Expected: collection fails because `src.three_way_match.one_to_many` does not exist.

- [ ] **Step 4: Implement the minimal allocator**

Implement these focused types and entry point:

```python
@dataclass
class LineAllocation:
    source_file: str
    source_line_id: str
    source_role: str
    order_line_id: str | None
    qty: Decimal
    amount: Decimal | None
    bind_status: str
    bind_rank: int | None
    basis: list[str]
    review_status: str
    rejected_reason: str | None = None

def run_one_to_many(
    classified: list[dict[str, Any]],
    *,
    complete_set: bool = False,
    business_group_id: str | None = None,
) -> dict[str, Any]:
    ...
```

Required implementation behavior:

- retain every `order`, `receipt`/`delivery`, and `invoice` item;
- filter by `business_group_id` only when the argument is supplied;
- convert `fields.items[]` to lines, or create one synthetic line from header quantity;
- bind by exact normalized order reference, line number, item code, then unique spec/unit combination;
- never choose among equal-ranked candidates;
- reject a repeated `(source_file, source_line_id, source_role)` key;
- aggregate `Decimal` quantities and amounts by order line;
- serialize decimals as non-scientific strings.

- [ ] **Step 5: Add failing state-semantics tests**

Add separate tests for:

```python
def test_partial_is_yellow_until_auditor_claims_complete(): ...
def test_partial_claimed_complete_is_red(): ...
def test_over_receipt_is_red_with_exact_difference(): ...
def test_duplicate_source_line_is_not_counted_twice(): ...
def test_ambiguous_line_binding_requires_review(): ...
def test_manual_business_group_overrides_different_order_numbers(): ...
```

Assertions must include exact flags: `PARTIAL_FULFILLMENT`, `PARTIAL_INVOICE`, `SET_CLAIMED_INCOMPLETE`, `OVER_RECEIPT`, `DUPLICATE_SOURCE_LINE`, and `AMBIGUOUS_LINK` or `UNBOUND`.

- [ ] **Step 6: Verify each test fails for the missing behavior, then implement the status table**

Use this order:

```python
if hard_flags:
    light = "RED"
elif complete_set and partial_flags:
    flags.append("SET_CLAIMED_INCOMPLETE")
    light = "RED"
elif review_flags or partial_flags:
    light = "YELLOW"
elif received_qty == ordered_qty and invoiced_qty == received_qty:
    light = "GREEN"
else:
    light = "YELLOW"
```

- [ ] **Step 7: Run the focused allocator tests and record the checkpoint**

Run the entire file and require all tests to pass. Do not commit.

---

### Task 2: Integrate aggregation into the existing workflow and persistence

**Files:**
- Modify: `src/workflow/pipeline.py:837-1115`
- Modify: `src/three_way_match/models.py:12-130`
- Modify: `src/workflow/three_way_persist.py:18-76`
- Test: `tests/test_three_way_one_to_many_pipeline.py`
- Test: `tests/test_three_way_match.py`
- Test: `tests/test_cutoff_receipt_control_date.py`
- Test: `tests/test_trading_mode_bridge.py`

**Interfaces:**
- Consumes: `run_one_to_many(...)` from Task 1 and the existing `run_three_way(...)` arguments.
- Produces: `run_three_way(..., complete_set: bool = False, business_group_id: str | None = None)` with additive `fulfillment` output.
- Persists: `three_way.fulfillment` and `three_way_match.fulfillment`; existing keys remain unchanged.

- [ ] **Step 1: Write a failing pipeline regression test using `classified_complete.json`**

```python
def test_pipeline_uses_all_receipts_in_the_business_group() -> None:
    pack = _load_fixture("classified_complete.json")
    result = run_three_way(pack["classified"])
    assert result["match_result"]["quantity_roles"] == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert result["fulfillment"]["light"] == "GREEN"
```

- [ ] **Step 2: Run the regression and verify RED**

Expected current failure: `received_qty` is `30.0` because the pipeline selects only the latest receipt.

- [ ] **Step 3: Extend the workflow entry point without changing the scalar API model**

Change the signature to:

```python
def run_three_way(
    classified: list[dict[str, Any]],
    manual: Optional[dict[str, Any]] = None,
    selected_receipt_idx: Optional[int] = None,
    *,
    period_end: Optional[str] = None,
    calendar_mode: Optional[str] = None,
    fiscal_year_start: Optional[str] = None,
    complete_set: bool = False,
    business_group_id: Optional[str] = None,
) -> dict[str, Any]:
```

Call `run_one_to_many` before scalar request assembly. Overlay its aggregate role values onto a copy of the scalar request:

```python
request = request.model_copy(update={
    "order": request.order.model_copy(update={"quantity": ordered_qty, "total_amount": order_amount}),
    "warehouse_receipt": request.warehouse_receipt.model_copy(update={"quantity": received_qty, "total_amount": receipt_amount}),
    "invoice": request.invoice.model_copy(update={"quantity": invoiced_qty, "total_amount": invoice_amount}),
})
```

Only replace an amount when the allocator has real amount evidence; preserve `0` for unmeasured receipt amount so the existing order-to-invoice fallback remains valid.

- [ ] **Step 4: Add fulfillment-to-decision tests, verify RED, then implement mapping**

Required mapping:

| Fulfillment | Scalar status | Decision | Hold code |
|---|---|---|---|
| GREEN | preserve scalar result | preserve scalar result | preserve |
| YELLOW partial | WARNING | HOLD_REVIEW | PARTIAL_SET |
| YELLOW ambiguous/unbound | WARNING | HOLD_REVIEW | AMBIGUOUS_BINDING |
| RED claimed incomplete | FAIL | HOLD_REVIEW | PARTIAL_SET |
| RED over/duplicate | FAIL | HOLD_REVIEW | PAPER_FIELD |

Extend `HoldReasonCode` with `PARTIAL_SET`. Append deterministic reasons rather than replacing existing scalar reasons.

- [ ] **Step 5: Persist the additive fulfillment view**

Add to `three_way_sample_patch`:

```python
"fulfillment": result.get("fulfillment") or {},
```

inside `three_way_match`, while retaining the complete serialized result under `three_way`.

- [ ] **Step 6: Run focused workflow, scalar compatibility, cutoff, and trading-mode tests**

Require the new pipeline file and the listed existing test files to pass. Do not commit.

---

### Task 3: Persist the auditor's per-chain complete-set declaration

**Files:**
- Modify: `src/api/workflow_router.py:201-227,725-850`
- Modify: `src/workflow/chain_workspace.py:175-219`
- Test: `tests/test_chain_complete_set.py`

**Interfaces:**
- Produces: `PUT /api/v1/workflow/jobs/{job_id}/chains/{chain_id}/complete-set`.
- Request: `{"complete_set": true}`.
- Response: updated `Job`.
- Chain list addition: `complete_set: bool` in each `/chains` row.

- [ ] **Step 1: Write failing API tests**

Cover:

```python
def test_complete_set_is_saved_only_for_the_requested_chain(): ...
def test_complete_set_change_clears_only_that_chains_three_way_and_conclusion(): ...
def test_complete_set_rejects_unknown_chain(): ...
def test_chains_response_exposes_complete_set_default_false(): ...
```

- [ ] **Step 2: Run the tests and verify RED**

Expected: `404` because the endpoint does not exist and `/chains` lacks `complete_set`.

- [ ] **Step 3: Add the body and endpoint**

```python
class CompleteSetBody(BaseModel):
    complete_set: bool

@router.put("/jobs/{job_id}/chains/{chain_id}/complete-set")
def put_chain_complete_set(job_id: str, chain_id: str, body: CompleteSetBody) -> dict[str, Any]:
    ...
```

Validate `chain_id` against `list_business_chains` plus the sample population. Build a patch that preserves unrelated sample fields but clears only:

```python
{
    "complete_set": body.complete_set,
    "three_way": None,
    "three_way_match": None,
    "conclusion_confirmed": False,
    "conclusion_confirm_sig": None,
}
```

Preserve the existing `cutoff_test`: the completeness declaration changes paper fulfillment, not the control-transfer/posting-date calculation. Write through `JOB_STORE.save_chain_sample`. Append a HITL event named `set_chain_complete_set` with before/after values and `chain_id`.

- [ ] **Step 4: Expose state in `/chains`**

Add:

```python
"complete_set": bool(sample.get("complete_set")),
```

to each enriched chain row.

- [ ] **Step 5: Run API tests and existing chain tests**

Require `tests/test_chain_complete_set.py`, `tests/test_confirm_fields_chain.py`, and `tests/test_business_hint_upload.py` to pass. Do not commit.

---

### Task 4: Add the complete-set control to the business upload row

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/components/BusinessWarehouseRow.tsx`
- Modify: `web/src/components/BusinessUploadQueue.tsx`
- Modify: `web/src/pages/UploadPage.tsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/components/BusinessWarehouseRow.test.tsx`
- Modify: `web/src/components/BusinessUploadQueue.test.tsx`

**Interfaces:**
- `ChainInfo.complete_set?: boolean`.
- `api.setChainCompleteSet(jobId: string, chainId: string, completeSet: boolean): Promise<Job>`.
- `BusinessWarehouseRow.onCompleteSetChange?: (row: ChainInfo, next: boolean) => void | Promise<void>`.

- [ ] **Step 1: Write failing row-control tests**

Assert that upload mode renders an accessible checkbox named `本笔已齐套：SO25-0281`, overview mode does not render it, and toggling calls `onCompleteSetChange(row, true)` without triggering `onOpen`.

- [ ] **Step 2: Run the component tests and verify RED**

Expected: checkbox and callback are absent.

- [ ] **Step 3: Add typed API support and the controlled checkbox**

Add to `ChainInfo`:

```ts
complete_set?: boolean
```

Add to `api`:

```ts
setChainCompleteSet: (jobId: string, chainId: string, completeSet: boolean) =>
  req<Job>(`/api/v1/workflow/jobs/${jobId}/chains/${encodeURIComponent(chainId)}/complete-set`, {
    method: 'PUT',
    body: JSON.stringify({ complete_set: completeSet }),
  }),
```

Render a real checkbox with hint text explaining that checking it declares all required evidence uploaded.

- [ ] **Step 4: Implement optimistic state with failure rollback in `UploadPage`**

Track the busy/error chain separately from upload state. On success call `onJob(next)` and invalidate the chain cache; on failure preserve the prior checked state and show the API error in the row.

- [ ] **Step 5: Run row, queue, and upload-page tests**

Add one test for successful persistence and one for failed-request rollback. Require all focused frontend tests to pass. Do not commit.

---

### Task 5: Show one-to-many fulfillment evidence in the three-way result card

**Files:**
- Modify: `web/src/lib/threeWayDecision.ts`
- Modify: `web/src/components/ThreeWayDecisionCard.tsx`
- Modify: `web/src/styles.css`
- Create: `web/src/components/ThreeWayDecisionCard.test.tsx`

**Interfaces:**
- Extend `ThreeWayDecisionView` with optional `fulfillment` containing `light`, `complete_set`, `flags`, `role_files`, `rows`, and `allocations`.
- Existing callers without `fulfillment` must render exactly as before.

- [ ] **Step 1: Write failing rendering tests**

Test a fixture view that shows:

- `订单 1 · 签收/验收 3 · 发票 2`;
- `订单 100 · 累计签收 100 · 累计开票 100`;
- green/yellow/red label;
- expandable file and allocation details;
- `SET_CLAIMED_INCOMPLETE` translated to `已声明齐套但资料仍不完整`.

- [ ] **Step 2: Run the test and verify RED**

Expected: role counts and fulfillment details are absent.

- [ ] **Step 3: Add the view mapper and additive card section**

Keep the current decision header and reasons. Append a `<details>` section only when fulfillment rows exist. Translate all supported flags with a fixed map; never display raw internal codes when a Chinese label exists.

- [ ] **Step 4: Add accessible status styling**

Use text plus color, not color alone. Ensure the details summary is keyboard focusable and allocation tables remain horizontally scrollable on narrow screens.

- [ ] **Step 5: Run card tests and adjacent conclusion-page tests**

Require both new fulfillment rendering and legacy no-fulfillment rendering to pass. Do not commit.

---

### Task 6: End-to-end workflow regression and delivery verification

**Files:**
- Create: `tests/test_one_to_many_e2e.py`
- Modify only if a verified defect appears: `src/workflow/sample_desk.py`, `src/workflow/review_events.py`, or export-readiness code.
- Update: `docs/superpowers/specs/2026-08-24-one-to-many-three-way-integration-design.md` only when implementation facts differ from the approved design.

**Interfaces:**
- Exercises: create job → set goals/sample population → seed one business group with all fixture documents → set `complete_set` → run three-way → inspect `/chains` and persisted sample.

- [ ] **Step 1: Write the failing end-to-end test**

Assert that:

```python
sample = job["gospd_sample_results"]["SO001"]
assert sample["complete_set"] is True
assert sample["three_way"]["fulfillment"]["rows"][0]["received_qty"] == "100"
assert sample["three_way"]["fulfillment"]["rows"][0]["invoiced_qty"] == "100"
assert chain["complete_set"] is True
```

Add a second scenario in which the partial invoice fixture changes from yellow to red after the auditor checks `complete_set`.

- [ ] **Step 2: Run the end-to-end test and verify RED, then fix only demonstrated integration defects**

Do not add unrelated refactors. Any production fix requires a failing regression assertion first.

- [ ] **Step 3: Run focused backend verification**

Run all new one-to-many, pipeline, chain API, existing scalar three-way, cutoff, trading-mode, sample-scope, and event tests.

- [ ] **Step 4: Run the complete backend suite**

Use the target virtual environment with `-p no:cacheprovider`. Record passed/failed counts and distinguish pre-existing failures from regressions.

- [ ] **Step 5: Run complete frontend verification**

```powershell
pnpm.cmd --dir web test
pnpm.cmd --dir web build
pnpm.cmd --dir web lint
```

Require tests and build to exit `0`. Report lint warnings separately from errors.

- [ ] **Step 6: Verify the real browser entry**

Confirm port `5173` points to the current worktree, returns HTTP `200`, and contains the new complete-set control and fulfillment card source signatures. Refresh the visible HTML and manually check keyboard operation, error rollback, narrow-width overflow, and red/yellow/green text labels.

- [ ] **Step 7: Review the final diff without committing**

Inspect only files touched by this plan. Confirm no source-project caches, logs, virtual environments, static workbench, SQLite stores, or 75-rule modules were copied. Leave the branch and worktree uncommitted for the user.

---

## Plan Self-Review Record

- Spec coverage: Tasks 1-6 cover grouping authority, line binding, accumulation, state semantics, cutoff isolation, persistence, UI, audit trace, fixtures, compatibility, and full verification.
- Scope: no independent service, database, static UI, ERP simulator, or 75-rule migration.
- Type consistency: `run_one_to_many`, `complete_set`, `fulfillment`, `quantity_roles`, `PARTIAL_SET`, and `setChainCompleteSet` use the same names across backend, API, persistence, and frontend tasks.
- Placeholder scan: every step contains concrete files, interfaces, commands, expected failures, and completion checks.
- Integration policy: no commit, merge, worktree removal, or branch cleanup is included.
