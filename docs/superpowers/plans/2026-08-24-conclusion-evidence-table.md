# Conclusion Evidence Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace prose-first conclusion cards with traceable, read-only evidence tables while preserving the existing field-confirmation matrix.

**Architecture:** Add a focused read-only conclusion evidence component fed by existing conclusion findings. Pass a small navigation callback into it; persist a one-shot field-locator payload in session storage so the existing field-confirmation page can restore matrix mode and original-document highlighting after navigation.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, CSS.

## Global Constraints

- Do not change backend matching or cutoff calculations.
- Keep natural-language failure explanations.
- Do not perform Git operations.

---

### Task 1: Evidence tables

**Files:**
- Create: `web/src/components/ConclusionEvidenceTable.tsx`
- Test: `web/src/components/ConclusionEvidenceTable.test.tsx`
- Modify: `web/src/pages/ConclusionPage.tsx`

- [ ] Write failing tests for horizontal three-document comparison, cutoff date evidence, and trace callbacks.
- [ ] Run the tests and confirm they fail because the new evidence tables are absent.
- [ ] Implement the smallest read-only tables and integrate them into conclusion findings.
- [ ] Run the tests and confirm they pass.

### Task 2: Cross-page source positioning

**Files:**
- Create: `web/src/lib/fieldTraceNavigation.ts`
- Test: `web/src/lib/fieldTraceNavigation.test.ts`
- Modify: `web/src/pages/FieldConfirmPage.tsx`

- [ ] Write failing tests for storing and consuming a one-shot locator.
- [ ] Implement locator persistence and consume it when the field page opens.
- [ ] Verify the field page opens matrix mode, selects the source document and highlights the field.

### Task 3: Presentation and delivery gate

**Files:**
- Modify: `web/src/styles.css`

- [ ] Add responsive table scrolling, explicit text states, focus styles, and evidence-card spacing.
- [ ] Run targeted tests, the full frontend suite, and the production build.

### Task 4: Separate matching fields from source identity

**Files:**
- Modify: `web/src/components/ConclusionEvidenceTable.tsx`
- Modify: `web/src/components/ConclusionEvidenceTable.test.tsx`
- Modify: `src/three_way_match/audit_trace.py`
- Test: `tests/test_three_way_audit_trace.py`

- [ ] Write failing tests proving the main table contains only customer, amount, and quantity.
- [ ] Write a failing test proving same numeric suffix across different prefixes cannot alone auto-confirm binding.
- [ ] Add a neutral, collapsible source-identity/date table with trace links and no consistency status.
- [ ] Require an explicit shared business reference for automatic document binding; otherwise route to manual confirmation.
- [ ] Run targeted frontend/backend tests, full frontend tests, full Python tests, and the production build.
