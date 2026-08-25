# Evidence-First Field Extraction Decision Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$executing-plans` to implement this plan task-by-task. This plan is executed inline in the existing worktree; do not dispatch subagents and do not commit Git changes.

**Goal:** Make PDF/OCR field extraction evidence-first, role-safe, multi-line capable and auditable, then reprocess task `5dd69ee5d6ef` without losing historical or human-confirmed values.

**Architecture:** Preserve native PDF text and coordinates as the primary evidence layer. Run deterministic identifier, party, line-item and arithmetic extraction first; permit an explicitly enabled P3 LLM supplement to propose only unresolved candidates; validate candidates through an evidence gate before entity resolution and three-way rules consume them.

**Tech Stack:** Python 3, FastAPI, pdfplumber, Pydantic-compatible dictionaries, pytest, existing React/Vitest client.

## Global Constraints

- Work only in `D:\抽凭—合同合规性审阅agent\.worktrees\event-driven-ui-v2`.
- Do not create or switch Git branches and do not commit.
- Valid PDF text layers must not call remote OCR or a vision model.
- External LLM field supplementation defaults off and requires an explicit per-run flag.
- LLM output may create candidates only; it may not accept fields or issue final audit conclusions.
- Unsupported values remain `null`/missing, never `0` or invented defaults.
- Contract remains optional for base three-way matching.
- Every production behavior change begins with a failing test.

---

### Task 1: Preserve PDF word coordinates and strengthen evidence anchors

**Files:**
- Modify: `src/legacy_ocr/ocr_adapter.py`
- Modify: `src/workflow/pipeline.py`
- Modify: `src/workflow/field_resolution/evidence_inventory.py`
- Test: `tests/test_ocr_pdf_shortcut.py`
- Test: `tests/test_field_evidence_inventory.py`

**Interfaces:**
- Produce `extract_pdf_text_evidence(file_path: str) -> tuple[str, list[dict[str, Any]]]`.
- Each text block contains `text`, zero-based `page`, `bbox`, `char_start`, `char_end`, and `source="native_pdf_word"`.
- `process_uploaded_files` persists the blocks returned by the native PDF path.

- [ ] **Step 1: Write failing tests**

```python
def test_native_pdf_shortcut_returns_positioned_words(sample_pdf):
    text, blocks = extract_pdf_text_evidence(str(sample_pdf))
    assert text
    assert any(block["bbox"] and block["source"] == "native_pdf_word" for block in blocks)

def test_generic_number_without_label_context_is_not_decision_usable():
    doc = document(raw_text="日期 2026-01-02 数量 20", field="20")
    node = build_document_evidence(doc)[0]
    assert node["usable_for_decision"] is False
    assert node["metadata"]["reason_code"] == "AMBIGUOUS_TEXT_ONLY_ANCHOR"
```

- [ ] **Step 2: Run tests and verify expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ocr_pdf_shortcut.py tests\test_field_evidence_inventory.py -q
```

Expected: failure because native PDF extraction returns text only and generic raw-text matches are currently marked usable.

- [ ] **Step 3: Implement positioned native PDF extraction**

Use `pdfplumber.Page.extract_words()` to build ordered word blocks and preserve existing `_extract_pdf_text_layer()` as a compatibility wrapper. Assign character offsets while composing page text. For images/OCR, retain existing OCR blocks.

- [ ] **Step 4: Implement evidence usability rules**

Require bbox or explicit label context for identifiers, dates, quantities and amounts. A unique entity name may use an exact text block; a standalone number located only by `str.find()` remains anchored for navigation but `usable_for_decision=False`.

- [ ] **Step 5: Run targeted tests**

Expected: all Task 1 tests pass without network access.

---

### Task 2: Extract role-safe identifiers and all Chinese/English line items

**Files:**
- Modify: `src/legacy_ocr/ocr_adapter.py`
- Modify: `src/workflow/classify.py`
- Test: `tests/test_3962_document_extraction.py`
- Create: `tests/test_pdf_reconciliation_goldens.py`
- Test: `tests/test_table_anchored_fields.py`

**Interfaces:**
- Produce `extract_document_identifiers(text: str, doc_type: str) -> dict[str, str]`.
- Produce `extract_all_line_items(text: str, doc_type: str) -> list[dict[str, Any]]`.
- Identifier output uses separate `documentNo`, `orderNo`, `invoiceNo`, and `contractNo` roles.

- [ ] **Step 1: Write failing identifier-role tests**

```python
def test_receipt_own_number_is_not_replaced_by_related_order():
    fields = extract(RECEIPT_3962, "warehouse_receipt")
    assert fields["documentNo"] == "YS-260102-005"
    assert fields["orderNo"] == "SO-251209-7214"
    assert "invoiceNo" not in fields

def test_english_invoice_keeps_invoice_contract_and_order_roles():
    fields = extract(INVOICE_3995, "invoice")
    assert fields["documentNo"] == fields["invoiceNo"] == "CI-260119-0068"
    assert fields["contractNo"] == "SC-251226-3995"
    assert fields["orderNo"] == "SO-251229-7498"
```

- [ ] **Step 2: Write failing multi-line tests**

```python
def test_3992_extracts_every_item_row():
    fields = extract(ORDER_3992, "purchase_order")
    assert [(x["model"], x["quantity"]) for x in fields["items"]] == [
        ("VL-50", "10"), ("VC-500", "15"), ("VL-200", "20")
    ]

def test_3995_english_receipt_extracts_line_and_parties():
    fields = extract(RECEIPT_3995, "warehouse_receipt")
    assert fields["documentNo"] == "YS-260120-057"
    assert fields["buyerName"] == "NordWerk Verpackung GmbH"
    assert fields["items"][0]["model"] == "PKG-600"
```

- [ ] **Step 3: Run and verify RED**

Expected: 3992 returns one row; 3995 fields are absent or malformed.

- [ ] **Step 4: Implement minimal deterministic parsers**

Add label-role identifier patterns before generic patterns. Parse all matching table rows rather than returning the first. Add English contract/order/receipt/commercial-invoice/bill-of-lading labels and CNY/USD amount handling. Do not infer an absent goods name from another document.

- [ ] **Step 5: Add the 3986 strong-conflict fixture**

```python
def test_3986_preserves_receipt_model_conflict():
    order = extract(ORDER_3986, "purchase_order")
    receipt = extract(RECEIPT_3986, "warehouse_receipt")
    assert order["items"][0]["model"] == "MVC-300"
    assert receipt["items"][0]["model"] == "MC-300"
```

- [ ] **Step 6: Run targeted extraction tests**

Expected: 3962, 3978, 3986, 3992 and 3995 golden cases pass.

---

### Task 3: Introduce the P3 LLM candidate contract and explicit call gate

**Files:**
- Modify: `src/llm/prompts.py`
- Modify: `src/llm/batch_assist.py`
- Modify: `src/legacy_ocr/ocr_adapter.py`
- Create: `src/workflow/field_resolution/llm_candidates.py`
- Test: `tests/test_field_gap_fill.py`
- Test: `tests/test_batch_llm_assist.py`
- Create: `tests/test_llm_field_candidate_contract.py`

**Interfaces:**
- Produce `validate_llm_field_supplement(payload, *, document, evidence_by_id) -> tuple[list[dict], list[str]]`.
- Add `allow_llm_field_supplement: bool = False` to field extraction/process options.
- Prompt schema version: `llm_field_supplement.v2`; prompt version: `field-supplement-p3-v2`.

- [ ] **Step 1: Write failing default-off test**

```python
def test_batch_llm_assist_is_off_when_key_exists_but_flag_is_absent(monkeypatch):
    monkeypatch.delenv("BATCH_LLM_ASSIST", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "valid-looking-key")
    assert batch_llm_assist_enabled() is False
```

- [ ] **Step 2: Write failing schema rejection tests**

```python
@pytest.mark.parametrize("mutation,code", [
    ({"evidence_ids": ["missing"]}, "EVIDENCE_REFERENCE_INVALID"),
    ({"final_professional_conclusion": "PASS"}, "FINAL_CONCLUSION_FORBIDDEN"),
    ({"raw_value": "invented"}, "RAW_VALUE_NOT_IN_SOURCE"),
])
def test_invalid_llm_candidates_are_rejected(mutation, code):
    payload = valid_payload_with(mutation)
    _, errors = validate_llm_field_supplement(payload, document=DOC, evidence_by_id=EVIDENCE)
    assert code in errors
```

- [ ] **Step 3: Run and verify RED**

Expected: default currently resolves to on and no strict candidate validator exists.

- [ ] **Step 4: Implement the candidate schema and prompt**

Return only candidate facts, evidence IDs, counterevidence, reason codes, confidence and review routing. Reject extra/unsupported authority fields. Keep deterministic arithmetic outside the prompt.

- [ ] **Step 5: Thread explicit call permission through extraction**

Rules run first. Call LLM only when unresolved fields remain and `allow_llm_field_supplement=True`. Failure returns rule results plus `NEEDS_REVIEW`; never Mock facts.

- [ ] **Step 6: Run prompt and gate tests**

Expected: all Task 3 tests pass and no test performs network I/O.

---

### Task 4: Replace bulk auto-accept with a per-field evidence gate

**Files:**
- Create: `src/workflow/field_resolution/evidence_gate.py`
- Modify: `src/models/field_values.py`
- Modify: `src/workflow/sample_desk.py`
- Modify: `src/workflow/field_resolution/contracts.py`
- Test: `tests/test_confirm_fields_accept.py`
- Create: `tests/test_field_evidence_gate.py`
- Test: `tests/test_3962_dynamic_resolution_e2e.py`

**Interfaces:**
- Produce `evaluate_candidate(document, field_key) -> FieldGateDecision`.
- Produce `accept_system_verified_fields(document) -> list[str]`.
- Decision statuses: `SYSTEM_VERIFIED`, `NEEDS_REVIEW`, `ROLE_CONFLICT`, `UNLOCATED`.

- [ ] **Step 1: Write failing gate tests**

```python
def test_unlocated_auto_field_is_not_accepted():
    doc = candidate_doc(field="totalAmount", node_status="UNLOCATED")
    assert accept_system_verified_fields(doc) == []
    assert doc["_field_meta"]["totalAmount"]["status"] != "ACCEPTED"

def test_receipt_order_number_cannot_verify_as_document_number():
    doc = receipt_with_document_no_from_label("关联订单号")
    decision = evaluate_candidate(doc, "documentNo")
    assert decision.status == "ROLE_CONFLICT"
```

- [ ] **Step 2: Run and verify RED**

Expected: existing `accept_all_current_fields` accepts both cases.

- [ ] **Step 3: Implement the per-field gate**

Validate source location, label role, document role, deterministic normalization, conflicts and type/unit. Preserve manual acceptance behavior but record it as human authority rather than system location.

- [ ] **Step 4: Update sample auto-review**

Replace `accept_all_current_fields(source="auto_fields_ok")` with the gate. If any required comparison field remains unverified, keep the sample in review rather than marking fields confirmed.

- [ ] **Step 5: Run targeted gate and workflow tests**

Expected: auto-confirm only consumes system-verified fields; manual confirmation tests remain valid.

---

### Task 5: Feed complete line evidence into entity resolution and one-to-many matching

**Files:**
- Modify: `src/workflow/field_resolution/line_items.py`
- Modify: `src/workflow/field_resolution/engine.py`
- Modify: `src/three_way_match/one_to_many.py`
- Test: `tests/test_field_line_item_resolution.py`
- Test: `tests/test_three_way_one_to_many.py`
- Test: `tests/test_three_way_one_to_many_pipeline.py`
- Test: `tests/test_one_to_many_e2e.py`

**Interfaces:**
- `extract_line_nodes` uses line-specific evidence IDs instead of every document evidence ID.
- `match_line_groups` preserves unassigned and conflicting source lines and never treats missing quantities as zero.

- [ ] **Step 1: Write failing 3978 aggregation test**

```python
def test_3978_two_receipts_aggregate_with_trace():
    result = match_business(ORDER_40, [RECEIPT_18, RECEIPT_22], [INVOICE_40])
    group = result["groups"][0]
    assert group["received_quantity"] == "40"
    assert group["calculation"] == "18套 + 22套 = 40套；发票40套"
```

- [ ] **Step 2: Write failing strong-counterevidence test**

```python
def test_3986_model_conflict_blocks_amount_quantity_pass():
    result = match_business(ORDER_MVC, [RECEIPT_MC], [INVOICE_MVC])
    assert result["status"] == "REVIEW"
    assert "MODEL_CONFLICT" in result["reason_codes"]
```

- [ ] **Step 3: Write failing missing-line test**

```python
def test_missing_quantity_is_not_coerced_to_zero():
    group = match_line_groups([order_line(None)], [], [])[0]
    assert group["ordered_quantity"] is None
    assert group["quantity_result"] == "NOT_TESTED"
```

- [ ] **Step 4: Run and verify RED**

Expected: current implementation uses zero defaults, broad document evidence IDs and lacks explicit model conflict output.

- [ ] **Step 5: Implement full line matching and conflict precedence**

Match by material code/model/name in deterministic order. Preserve unmatched/ambiguous lines. A model or identifier conflict outranks amount/quantity equality. Use Decimal and complete calculation traces.

- [ ] **Step 6: Run one-to-many and field-resolution tests**

Expected: 3978 passes with trace; 3986 routes to review; 3992 connects all three rows.

---

### Task 6: Add versioned, recoverable reprocessing for the current task

**Files:**
- Create: `src/workflow/recognition_versions.py`
- Modify: `src/api/workflow_router.py`
- Modify: `src/workflow/pipeline.py`
- Create: `tests/test_recognition_reprocess.py`
- Test: `tests/test_process_selected_files.py`

**Interfaces:**
- Produce `snapshot_recognition(job) -> dict[str, Any]`.
- Produce `reprocess_classified_documents(job, *, allow_llm_field_supplement=False) -> dict[str, Any]`.
- API accepts a reprocess request that defaults to local deterministic extraction.

- [ ] **Step 1: Write failing history-preservation test**

```python
def test_reprocess_supersedes_auto_values_but_preserves_history():
    updated = reprocess_classified_documents(JOB_WITH_AUTO_WRONG_DOCUMENT_NO)
    doc = updated["classified"][0]
    assert doc["fields"]["documentNo"] == "FP-260102-8305"
    assert doc["recognition_history"][-1]["fields"]["documentNo"] == "SO-251209-7214"
```

- [ ] **Step 2: Write failing manual-conflict test**

```python
def test_reprocess_does_not_overwrite_human_accepted_value():
    updated = reprocess_classified_documents(JOB_WITH_HUMAN_VALUE)
    doc = updated["classified"][0]
    assert doc["fields"]["documentNo"] == "HUMAN-VALUE"
    assert doc["reprocess_conflicts"][0]["field_key"] == "documentNo"
```

- [ ] **Step 3: Run and verify RED**

Expected: no versioned reprocessing API exists.

- [ ] **Step 4: Implement atomic reprocessing**

Reuse stored source paths and native PDF text. Store pre-run snapshot and per-document versions. Supersede historical `auto_fields_ok/confirm_all` values, preserve human sources, rebuild evidence and derived results, and keep the prior active version on error.

- [ ] **Step 5: Run reprocessing tests**

Expected: local-only reprocessing, history preservation and rollback tests pass.

- [ ] **Step 6: Reprocess task `5dd69ee5d6ef`**

First export/read a snapshot, then invoke the local deterministic reprocess path with LLM supplementation disabled. Verify 3962, 3978, 3986, 3992 and 3995 in the returned job state before allowing the UI to consume the new version.

---

### Task 7: Full regression and live acceptance

**Files:**
- Verify: all modified backend and frontend files
- Update only if required by changed API shape: `web/src/**`

**Interfaces:**
- No new interface; this task verifies the final integrated behavior.

- [ ] **Step 1: Run focused golden suite**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pdf_reconciliation_goldens.py tests\test_llm_field_candidate_contract.py tests\test_field_evidence_gate.py tests\test_recognition_reprocess.py -q
```

- [ ] **Step 2: Run full backend suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 3: Run frontend tests and build**

```powershell
pnpm --dir web test -- --run
pnpm --dir web build
```

- [ ] **Step 4: Query the live job**

Verify:

- 3962 own identifiers and 20-unit line are correct;
- 3978 receipt quantities aggregate to 40;
- 3986 exposes the MC-300/MVC-300 conflict;
- 3992 exposes all 3 lines;
- 3995 English identifiers, parties and USD 86,000 values are populated;
- no decision-usable field lacks valid evidence;
- external LLM calls remain absent unless explicitly enabled.

- [ ] **Step 5: Open the live UI**

Check expanded field matrix, one-to-many result, conflict display and source navigation at `http://127.0.0.1:5173/`.

- [ ] **Step 6: Report delivery status**

Use only `已验证`, `带限制可交付`, or `未验证`, with exact test counts, build outcome, live-query evidence and remaining limitations. Do not commit Git.

