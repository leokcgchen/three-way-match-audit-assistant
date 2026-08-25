# Sample Scope Quarantine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the uploaded sample list the only source of business rows, quarantine every uploaded document identified outside that list, and ask the auditor to review it with deletion as the recommended action.

**Architecture:** Add one backend sample-scope boundary that partitions OCR output into in-scope classified documents and durable `scope_exceptions`. Keep a second invariant in the desk projection so a populated sample list can never be expanded by OCR-derived chain IDs. Render the exceptions in a focused, accessible upload-page dialog and exception panel; deletion is explicit and removes both the exception record and its job-local file.

**Tech Stack:** FastAPI, Python 3, React, TypeScript, Vitest, Testing Library, pytest.

## Global Constraints

- The sample population is the authoritative and immutable source of business rows.
- An uploaded document must never create a business outside the sample population.
- Out-of-sample files remain available in an exception area until the auditor acts.
- The UI must actively ask the auditor to review newly detected exceptions and recommend deletion without deleting automatically.
- Existing unrelated dirty-worktree changes must remain untouched.

---

### Task 1: Enforce the sample boundary after OCR

**Files:**
- Create: `src/workflow/sample_scope.py`
- Modify: `src/api/workflow_router.py`
- Modify: `src/workflow/job_store.py`
- Test: `tests/test_sample_scope.py`

**Interfaces:**
- Consumes: OCR-produced document dictionaries and `sample_population.business_ids`.
- Produces: `partition_documents_by_sample_scope(documents, sample_population) -> tuple[list[dict], list[dict]]` and persisted `scope_exceptions` entries with `exception_id`, `file_name`, `detected_business_ids`, `reason`, `recommended_action`, and the quarantined `document`.

- [ ] **Step 1: Write failing tests for outside-sample, unassigned, and in-sample documents.**

```python
def test_outside_sample_document_is_quarantined():
    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "SO25-9999.pdf", "fields": {"orderNo": "SO25-9999"}}],
        {"business_ids": ["SO25-0001"]},
    )
    assert accepted == []
    assert exceptions[0]["detected_business_ids"] == ["SO25-9999"]
    assert exceptions[0]["recommended_action"] == "delete"

def test_matching_sample_document_remains_classified():
    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "SO25-0001.pdf", "fields": {"orderNo": "SO25-0001"}}],
        {"business_ids": ["SO25-0001"]},
    )
    assert len(accepted) == 1
    assert exceptions == []
```

- [ ] **Step 2: Run the focused tests and confirm they fail before implementation.**

Run: `.venv\Scripts\python.exe -m pytest tests\test_sample_scope.py -q`

Expected: FAIL because `src.workflow.sample_scope` does not exist.

- [ ] **Step 3: Implement deterministic normalized-ID matching and exception construction.**

```python
def partition_documents_by_sample_scope(documents, sample_population):
    sample_ids = normalized_population_ids(sample_population)
    accepted, exceptions = [], []
    for document in documents:
        detected = detected_strong_business_ids(document)
        if detected and any(matches_sample(value, sample_ids) for value in detected):
            accepted.append(document)
        else:
            exceptions.append(build_scope_exception(document, detected))
    return accepted, exceptions
```

- [ ] **Step 4: Apply the partition in synchronous and background OCR completion paths and merge exceptions by stable file identity.**

- [ ] **Step 5: Run `tests/test_sample_scope.py` and the focused upload/process API tests.**

- [ ] **Step 6: Commit the isolated backend boundary change.**

```powershell
git add src/workflow/sample_scope.py src/api/workflow_router.py src/workflow/job_store.py tests/test_sample_scope.py
git commit -m "fix: quarantine documents outside sample scope"
```

### Task 2: Prevent desk expansion and support explicit deletion

**Files:**
- Modify: `src/audit/sample_population.py`
- Modify: `src/api/workflow_router.py`
- Modify: `tests/test_sample_population_excel.py`
- Modify: `tests/test_sample_scope.py`

**Interfaces:**
- Consumes: `sample_population`, `scope_exceptions`, and `exception_id`.
- Produces: `desk_sample_ids(job)` that returns only population IDs when a population exists, plus `DELETE /jobs/{job_id}/scope-exceptions/{exception_id}`.

- [ ] **Step 1: Change the old regression test so OCR-only `SO25-7777` is excluded when the list contains only `SO25-0001`.**

```python
def test_desk_ids_are_strictly_bounded_by_population():
    ids = desk_sample_ids(job_with_population_and_outside_document)
    assert ids == ["SO25-0001"]
```

- [ ] **Step 2: Add an API test proving deletion removes the exception, classified copy, pending copy, packet unit references, and job-local file.**

- [ ] **Step 3: Run both tests and confirm the old append rule and missing endpoint fail.**

- [ ] **Step 4: Make populated sample lists authoritative in `desk_sample_ids`; retain legacy chain discovery only for jobs without a sample list.**

- [ ] **Step 5: Implement exact-ID exception deletion with a resolved-path guard restricted to `job_workdir(job_id)`.**

- [ ] **Step 6: Run the focused desk, chain, upload, process, and deletion tests.**

- [ ] **Step 7: Commit the invariant and deletion endpoint.**

```powershell
git add src/audit/sample_population.py src/api/workflow_router.py tests/test_sample_population_excel.py tests/test_sample_scope.py
git commit -m "fix: keep workbench rows within sample population"
```

### Task 3: Add auditor-facing exception review

**Files:**
- Create: `web/src/components/SampleScopeExceptionDialog.tsx`
- Create: `web/src/components/SampleScopeExceptionDialog.test.tsx`
- Modify: `web/src/pages/UploadPage.tsx`
- Modify: `web/src/pages/UploadPage.v2.test.tsx`
- Modify: `web/src/api.ts`
- Modify: `web/src/types.ts`
- Modify: the existing upload-page stylesheet that owns `.upload-page` styles.

**Interfaces:**
- Consumes: `job.scope_exceptions` and `api.deleteScopeException(jobId, exceptionId)`.
- Produces: one dialog per newly detected exception batch, a persistent exception panel, recommended destructive action `删除该文件`, and secondary action `暂不删除，留在异常区`.

- [ ] **Step 1: Write failing component tests for dialog copy, detected business ID, recommended deletion, and non-destructive dismissal.**

```tsx
it('asks the auditor to review and recommends deletion', () => {
  render(<SampleScopeExceptionDialog exceptions={[outside]} onDelete={vi.fn()} onDismiss={vi.fn()} />)
  expect(screen.getByRole('dialog', { name: '发现非抽样清单材料' })).toBeInTheDocument()
  expect(screen.getByText('SO25-9999')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '删除该文件（推荐）' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the component and page tests and confirm they fail before implementation.**

- [ ] **Step 3: Add TypeScript types and the typed deletion API method.**

- [ ] **Step 4: Build a native-dialog component with focus management, explicit risk explanation, visible recommendation text, and keyboard-operable actions.**

- [ ] **Step 5: Integrate automatic first-seen opening and a persistent exception section into the upload page.**

- [ ] **Step 6: Run focused frontend tests, the complete frontend suite, TypeScript build, and lint.**

- [ ] **Step 7: Start the application and visually verify upload, automatic dialog, dismissal, persistent exception state, and deletion at desktop and narrow viewport widths.**

- [ ] **Step 8: Commit the auditor-facing workflow.**

```powershell
git add web/src/components/SampleScopeExceptionDialog.tsx web/src/components/SampleScopeExceptionDialog.test.tsx web/src/pages/UploadPage.tsx web/src/pages/UploadPage.v2.test.tsx web/src/api.ts web/src/types.ts web/src
git commit -m "feat: guide auditors through sample-scope exceptions"
```

## Acceptance Matrix

| Acceptance criterion | Verification | Required |
|---|---|---:|
| Outside-sample OCR result never creates a desk business | Backend unit and API tests | Yes |
| Exception persists until auditor action | API and page tests | Yes |
| Auditor receives an active review prompt | Component test and browser check | Yes |
| Deletion is recommended but never automatic | Component test and browser check | Yes |
| Explicit deletion removes job state and job-local file only | API test with path guard | Yes |
| Existing upload/OCR/workbench flow does not regress | Full frontend suite, focused backend suite, build | Yes |
