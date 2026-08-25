# Explainable Dynamic Field Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evidence-anchored dynamic field inventory, entity-resolution graph, deterministic comparison plan, and auditor-facing explanation UI, using business 3962 as the first golden acceptance case.

**Architecture:** Add a focused `src/workflow/field_resolution/` package that converts existing OCR fields and raw text into evidence nodes, resolves rule/model/human edges, and produces a versioned comparison plan. Persist the plan per sample business and render it with focused React components for consistency/recalculation, chronology, document-specific fields, and review issues. Existing fixed field catalogs remain fallback extraction hints, not the source of comparison truth.

**Tech Stack:** Python 3.12, FastAPI, Pydantic-compatible dictionaries, existing OCR/LLM adapters, pytest, React 19, TypeScript 6, Vitest, Testing Library, existing CSS design system.

## Global Constraints

- The sample list is the only population boundary; field resolution never creates a business.
- File and directory names are not evidence for field semantics or audit conclusions.
- Every usable field and relationship links to a real document, page, text range or bounding box, and excerpt.
- LLM output is advisory unless it passes the two-independent-evidence/no-counterevidence gate.
- Strong conflicts override weak similarities; deterministic amount, quantity, identifier, and date results cannot be overwritten by model prose.
- Three-way matching and cutoff remain separate result families.
- Existing dirty worktree changes must be preserved. Do not commit, merge, reset, clean, or rewrite unrelated files.
- Add no new third-party dependency.

---

### Task 1: Evidence and Resolution Contracts

**Files:**
- Create: `src/workflow/field_resolution/__init__.py`
- Create: `src/workflow/field_resolution/contracts.py`
- Test: `tests/test_field_resolution_contracts.py`

**Interfaces:**
- Produces: `make_evidence_node(...) -> dict[str, Any]`
- Produces: `make_resolution_edge(...) -> dict[str, Any]`
- Produces: `validate_evidence_node(node) -> list[str]`
- Produces: `validate_resolution_edge(edge, evidence_by_id) -> list[str]`
- Produces constants `EDGE_STATUSES`, `RELATION_TYPES`, `COMPARISON_DOMAINS`

- [ ] **Step 1: Write failing contract tests**

```python
def test_evidence_requires_real_anchor():
    node = make_evidence_node(
        document_id="order.pdf", document_role="order", field_key="goodsName",
        raw_value="伺服电机", excerpt="", page=None, char_start=None,
        char_end=None, bbox=None, source="llm", extractor="semantic-v1",
    )
    assert validate_evidence_node(node) == ["EVIDENCE_ANCHOR_MISSING"]

def test_edge_cannot_reference_unknown_evidence():
    edge = make_resolution_edge(
        concept="goods_identity", relation_type="SEMANTIC_EQUIVALENT",
        left_evidence_ids=["ev-missing"], right_evidence_ids=["ev-2"],
        decision_owner="model", status="CANDIDATE",
        reason_code="NAME_MODEL_SPLIT_EQUIVALENT",
    )
    assert validate_resolution_edge(edge, {}) == ["EVIDENCE_REFERENCE_INVALID"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_field_resolution_contracts.py -q`

Expected: collection fails because `src.workflow.field_resolution.contracts` does not exist.

- [ ] **Step 3: Implement minimal typed dictionary factories and validators**

Use stable schema versions (`field_evidence_node.v1`, `resolution_edge.v1`). Compute IDs from a SHA-256 hash of document identity, field key, location, and raw value. Validation must reject missing document IDs, empty values, anchors with neither char range nor bbox, invalid ranges, unknown relation/status values, and missing evidence references.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command; expect all Task 1 tests to pass.

---

### Task 2: Evidence Inventory From Existing OCR Results

**Files:**
- Create: `src/workflow/field_resolution/evidence_inventory.py`
- Modify: `src/models/field_values.py`
- Modify: `src/workflow/pipeline.py`
- Test: `tests/test_field_evidence_inventory.py`

**Interfaces:**
- Consumes: classified document dictionaries containing `fields`, `_field_meta`, `raw_text`, and `text_blocks`
- Produces: `build_document_evidence(document) -> list[dict[str, Any]]`
- Produces: `attach_document_evidence(document) -> dict[str, Any]`, storing `field_evidence_nodes`
- Produces: `evidence_for_field(document, field_key) -> list[dict[str, Any]]`

- [ ] **Step 1: Write failing inventory tests**

```python
def test_inventory_anchors_exact_field_value_in_raw_text():
    doc = {
        "file_name": "order.pdf", "doc_type": "order",
        "raw_text": "货物名称 伺服电机\n规格型号 SM-130\n数量 20 台",
        "fields": {"goodsName": "伺服电机", "model": "SM-130", "quantity": 20},
    }
    nodes = build_document_evidence(doc)
    goods = next(x for x in nodes if x["field_key"] == "goodsName")
    assert goods["excerpt"] == "伺服电机"
    assert goods["char_start"] < goods["char_end"]
    assert goods["document_role"] == "order"

def test_inventory_keeps_unlocated_candidate_but_marks_it_invalid_for_decisions():
    doc = {"file_name": "scan.pdf", "doc_type": "invoice", "raw_text": "", "fields": {"totalAmount": 113000}}
    node = build_document_evidence(doc)[0]
    assert node["anchor_status"] == "UNLOCATED"
    assert node["usable_for_decision"] is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_field_evidence_inventory.py -q`

Expected: import failure for the new inventory module.

- [ ] **Step 3: Implement evidence inventory**

Search accepted/candidate/raw values in `raw_text` with deterministic boundaries. Prefer existing `text_blocks` page/bbox metadata when present. Preserve unlocated historical fields with `anchor_status="UNLOCATED"` and `usable_for_decision=False`. Never discard unknown non-private fields merely because they are not in `FIELD_LABELS`.

- [ ] **Step 4: Attach inventory after field extraction and after manual save**

In `_process_one_file`, call `attach_document_evidence` after `seed_field_meta`. In field-save paths, rebuild evidence only for changed fields while preserving prior versions in `field_evidence_history`.

- [ ] **Step 5: Run tests and verify GREEN**

Run Task 2 tests plus `tests/test_workflow_pipeline_smoke.py` and `tests/test_confirm_fields_chain.py`.

---

### Task 3: Deterministic Entity Resolution and Explainable Edges

**Files:**
- Create: `src/workflow/field_resolution/normalizers.py`
- Create: `src/workflow/field_resolution/resolution_engine.py`
- Test: `tests/test_field_resolution_engine.py`

**Interfaces:**
- Produces: `resolve_rule_edges(documents, evidence_nodes, sample_row) -> list[dict[str, Any]]`
- Produces: `normalize_legal_entity`, `normalize_address`, `normalize_goods`, `normalize_unit`, `parse_decimal`
- Edge output includes raw evidence IDs, transformations, confirmed facts, counterevidence, reason code, and status

- [ ] **Step 1: Write failing rule-edge tests**

Cover literal expectations for:

```python
def test_rule_edge_explains_exact_quantity_match():
    edges = resolve_rule_edges(docs_3962, evidence_3962, sample_3962)
    edge = edge_for(edges, "quantity")
    assert edge["relation_type"] == "EXACT_EQUAL"
    assert edge["status"] == "CONFIRMED"
    assert edge["confirmed_facts"] == ["订单数量20台", "签收数量20台", "发票数量20台"]

def test_same_customer_name_cannot_override_different_tax_ids():
    edge = resolve_rule_edges(two_docs_same_name_different_tax_id, evidence, {})[0]
    assert edge["status"] == "CONFLICT"
    assert edge["counter_evidence"][0]["reason_code"] == "TAX_ID_CONFLICT"
```

Also test address punctuation normalization, currency/unit normalization, exact order reference, missing evidence, and short numeric false matches.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_field_resolution_engine.py -q`

- [ ] **Step 3: Implement rule normalizers and edge resolver**

Normalization must record each applied transformation. Legal suffix removal alone cannot confirm an entity; exact tax ID conflicts force `CONFLICT`. Rule edges may confirm identifiers, parties, goods codes/models, quantities, units, currency, and gross amounts only when all used nodes are decision-usable.

- [ ] **Step 4: Run Task 3 tests and existing three-way tests**

Run Task 3 plus `tests/test_three_way_audit_trace.py`, `tests/test_three_way_one_to_many.py`, and `tests/test_three_way_one_to_many_pipeline.py`.

---

### Task 4: Semantic Proposal Gate With Independent Evidence

**Files:**
- Create: `src/workflow/field_resolution/semantic_adapter.py`
- Modify: `src/llm/prompts.py`
- Test: `tests/test_field_semantic_resolution.py`

**Interfaces:**
- Produces: `build_semantic_resolution_prompt(evidence_nodes, unresolved_concepts) -> str`
- Produces: `validate_semantic_proposal(payload, evidence_by_id, deterministic_edges) -> dict[str, Any]`
- Produces: `apply_semantic_proposals(...) -> list[dict[str, Any]]`
- No provider call occurs in this module; it validates provider output injected by the existing adapter

- [ ] **Step 1: Write failing semantic gate tests**

```python
def test_name_and_model_are_two_independent_dimensions():
    proposal = semantic_goods_proposal(
        evidence_ids=["order-name-model", "receipt-name", "receipt-model"],
        dimensions=["goods_name", "model"], counter_evidence=[],
    )
    result = validate_semantic_proposal(proposal, evidence_by_id, [])
    assert result["status"] == "CONFIRMED"

def test_duplicate_paraphrases_are_not_independent_evidence():
    proposal = semantic_goods_proposal(
        evidence_ids=["name-a", "name-b"], dimensions=["goods_name", "goods_name"], counter_evidence=[],
    )
    assert validate_semantic_proposal(proposal, evidence_by_id, [])["status"] == "CANDIDATE"

def test_model_cannot_override_rule_amount_conflict():
    result = validate_semantic_proposal(model_pass, evidence_by_id, [rule_amount_conflict])
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "DETERMINISTIC_CONFLICT_PRECEDENCE"
```

Also reject missing evidence IDs, excerpts absent from source nodes, invalid JSON shape, model-generated values, and a single weak clue.

- [ ] **Step 2: Run tests and verify RED**

Run Task 4 tests; expect missing-module failure.

- [ ] **Step 3: Implement the controlled prompt and validator**

The JSON schema must require concept, source evidence IDs, semantic dimensions, transformations, supporting facts, counterevidence, and proposed relation. Confirmation requires at least two distinct semantic dimensions, valid anchors, no deterministic conflict, and no model change to identifier/amount/quantity/date facts.

- [ ] **Step 4: Run tests and verify GREEN**

Run Task 4 tests and `tests/test_workflow_pipeline_smoke.py`.

---

### Task 5: Line-Item and One-to-Many Resolution

**Files:**
- Create: `src/workflow/field_resolution/line_items.py`
- Modify: `src/three_way_match/one_to_many.py`
- Test: `tests/test_field_line_item_resolution.py`

**Interfaces:**
- Produces: `extract_line_nodes(document) -> list[dict[str, Any]]`
- Produces: `match_line_groups(order_lines, receipt_lines, invoice_lines) -> list[dict[str, Any]]`
- Each group records item evidence, match keys, unit conversions, quantity/amount calculations, tolerance, and result

- [ ] **Step 1: Write failing multi-item tests**

Test reordered rows, same name/different model, one order line to two receipt lines, two invoice lines to one order line, unit mismatch, quantity shortage, quantity overage, and amount formula failure. Literal golden assertion:

```python
def test_two_receipts_sum_to_order_quantity_with_explanation():
    group = match_line_groups([order_sm130_20], [receipt_sm130_8, receipt_sm130_12], [invoice_sm130_20])[0]
    assert group["quantity_result"] == "PASS"
    assert group["calculation"] == "8台 + 12台 = 20台；发票20台"
```

- [ ] **Step 2: Run tests and verify RED**

Run Task 5 tests.

- [ ] **Step 3: Implement deterministic grouping and aggregation**

Use unique material/model first, normalized goods name second, and quantity/price only as supporting keys. Ambiguous equal-cost assignments remain `REVIEW`; do not silently choose the first row. Reuse current one-to-many amount logic where its evidence contract is sufficient.

- [ ] **Step 4: Run tests and verify GREEN**

Run Task 5 tests and all existing `test_*one_to_many*.py` tests.

---

### Task 6: Dynamic Comparison Plan, Persistence, and API

**Files:**
- Create: `src/workflow/field_resolution/comparison_plan.py`
- Create: `src/workflow/field_resolution/orchestrator.py`
- Modify: `src/workflow/job_store.py`
- Modify: `src/workflow/chain_workspace.py`
- Modify: `src/api/workflow_router.py`
- Test: `tests/test_dynamic_comparison_plan.py`
- Test: `tests/test_dynamic_comparison_api.py`

**Interfaces:**
- Produces: `build_comparison_plan(job, chain_id, semantic_payload=None) -> dict[str, Any]`
- Produces: `refresh_comparison_plan(job_id, chain_id, force=False) -> dict[str, Any]`
- Adds per-sample `field_resolution` with `schema_version`, `evidence_nodes`, `edges`, `line_groups`, `comparison_plan`, `issues`, and `audit_log`
- Adds `POST /api/v1/workflow/jobs/{job_id}/field-resolution/refresh`
- Adds `POST /api/v1/workflow/jobs/{job_id}/field-resolution/edges/{edge_id}/decision`

- [ ] **Step 1: Write failing plan precedence tests**

Assert the exact precedence `CONFLICT > MISSING_EVIDENCE > PASS_WITH_WARNING > PASS`, document-specific fields do not affect status, dates enter chronology not equality rows, and customer-code mapping produces `PASS_WITH_WARNING` for 3962.

- [ ] **Step 2: Write failing API behavior tests**

Verify refresh is idempotent for unchanged document hashes, invalid edge IDs return 404, a human confirmation records actor/time/reason without mutating raw evidence, and changing an accepted field invalidates only affected edges/plans.

- [ ] **Step 3: Run tests and verify RED**

Run both Task 6 test files.

- [ ] **Step 4: Implement comparison plan and orchestration**

Generate five domains: `consistency`, `recalculation`, `chronology`, `document_specific`, `issues`. Derive natural-language reason text from structured reason codes, facts, transformations, calculations, and counterevidence; never store free-form reason as the sole support.

- [ ] **Step 5: Persist versioned plans and expose API actions**

Use document and accepted-field hashes to cache. Human decisions append audit records and preserve prior plan versions. Job reads may return the current plan but must not call a remote model implicitly.

- [ ] **Step 6: Run tests and verify GREEN**

Run Task 6 tests plus `tests/test_sample_scope.py`, `tests/test_confirm_fields_chain.py`, and `tests/test_conclusion_trace.py`.

---

### Task 7: Explainable Comparison UI

**Files:**
- Create: `web/src/components/ExplainableFieldMatrix.tsx`
- Create: `web/src/components/ExplainableFieldMatrix.test.tsx`
- Create: `web/src/components/FieldReasonDrawer.tsx`
- Create: `web/src/components/FieldReasonDrawer.test.tsx`
- Create: `web/src/components/BusinessChronologyPanel.tsx`
- Create: `web/src/components/BusinessChronologyPanel.test.tsx`
- Create: `web/src/components/ResolutionIssuesPanel.tsx`
- Create: `web/src/components/ResolutionIssuesPanel.test.tsx`
- Modify: `web/src/pages/FieldConfirmPage.tsx`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes `job.gospd_sample_results[chain_id].field_resolution.comparison_plan`
- Emits existing `onSelectCell(doc, fieldKey)` behavior using evidence document/value
- Calls new refresh and edge-decision APIs
- Falls back to `FieldComparisonMatrix` for historical jobs without a dynamic plan

- [ ] **Step 1: Write failing matrix tests**

Render the 3962 plan and assert visible sections and states: “一致性与复算”, “时序与业务过程”, “单据专有信息”, “待解释事项”, “实质一致”, “复算一致”, and “客户编码待映射”. Assert “不适用” does not increment mismatch count.

- [ ] **Step 2: Write failing explanation drawer tests**

Click the goods relationship and assert the drawer shows source filenames, page numbers, excerpts, `拆分品名与型号`, supporting facts, and no missing-evidence claim. Keyboard Escape closes the drawer and focus returns to the trigger.

- [ ] **Step 3: Write failing chronology and issue-action tests**

Assert chronology renders `验收 09:40 → 开票 14:50` without an equality badge. Assert customer-code mapping action requires a reason and sends the edge ID, chain ID, decision, and reason.

- [ ] **Step 4: Run tests and verify RED**

Run: `npm test -- ExplainableFieldMatrix.test.tsx FieldReasonDrawer.test.tsx BusinessChronologyPanel.test.tsx ResolutionIssuesPanel.test.tsx` from `web`.

- [ ] **Step 5: Implement focused accessible components**

Use semantic tables, buttons, headings, `dialog`, visible text plus color, existing green/yellow/red tokens, and current spacing/radius conventions. Keep source location links keyboard accessible. Do not put chronology or document-specific fields in the equality matrix.

- [ ] **Step 6: Integrate with FieldConfirmPage and preserve legacy fallback**

Show dynamic components when a current plan exists. Keep current document preview/highlight wiring. Do not add another fixed frontend field catalog.

- [ ] **Step 7: Run tests and verify GREEN**

Run Task 7 tests, `npm test`, `npm run lint`, and `npm run build`.

---

### Task 8: 3962 Golden Case and Adversarial Acceptance

**Files:**
- Create: `tests/fixtures/explainable_fields/3962_expected.json`
- Create: `tests/test_3962_dynamic_resolution_e2e.py`
- Create: `web/src/pages/FieldConfirmPage.dynamicResolution.test.tsx`
- Modify: `scripts/accept_gospd01030_e2e.py`

**Interfaces:**
- Uses the existing 3962 order, receipt, and invoice mock files
- Compares a normalized result to a literal checked-in expectation; volatile IDs/timestamps are removed before comparison

- [ ] **Step 1: Write the failing backend golden test**

Assert:

```python
assert result["overall_status"] == "PASS_WITH_WARNING"
assert result["three_way_status"] == "PASS_WITH_WARNING"
assert result["cutoff_status"] == "PASS"
assert issue_codes(result) == ["CUSTOMER_CODE_MAPPING_REQUIRED"]
assert calculation(result, "gross_amount") == "20 × 5000 + 13000 = 113000"
assert chronology(result) == ["2026-01-02T09:40", "2026-01-02T14:50"]
```

- [ ] **Step 2: Add adversarial tests before implementation changes**

Cover same-name/different-tax-ID, same-product-name/different-model, invalid amount formula, reordered item rows, one-to-many under/over delivery, missing model service, invented evidence ID, duplicate evidence dimensions, and old jobs without page anchors.

- [ ] **Step 3: Run tests and verify RED for missing integrated behavior**

Run the backend golden test and the page test.

- [ ] **Step 4: Complete only integration gaps exposed by the tests**

Wire fixture discovery, orchestration invocation after OCR/field confirmation, and UI loading. Do not weaken expected values to make tests pass.

- [ ] **Step 5: Run complete verification**

Backend: `.venv\Scripts\python.exe -m pytest -q`

Frontend from `web`: `npm test`, `npm run lint`, `npm run build`

Manual browser acceptance at 1202×792 and 1440×900:

- 3962 shows the five domains without horizontal semantic confusion;
- clicking values highlights the correct original document;
- clicking “为什么” shows evidence, transformations, formula, and counterevidence;
- customer code remains a warning and cannot be silently auto-mapped;
- date rows never show “完全一致” merely because dates differ;
- legacy tasks render the old matrix rather than a blank page.

---

## Plan Self-Review

- Every design requirement maps to a task: contracts/evidence (1–2), rule/model entity resolution (3–4), multi-item aggregation (5), plan/persistence/API (6), UI (7), golden and adversarial QA (8).
- No task relies on a newly added dependency.
- Type names are stable across tasks: `field_evidence_nodes`, `resolution_edge`, `field_resolution`, `comparison_plan`, and five comparison domains.
- The plan preserves the sample boundary, original values, legacy fallback, and separation of three-way versus cutoff.
- Git commit steps are intentionally omitted because the user explicitly requested no Git writes.
