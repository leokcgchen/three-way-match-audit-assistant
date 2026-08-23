# Workbench Clarity Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the audit workbench immediately understandable by replacing compressed legends and the dense process explainer with precise hover help, a single next-action card, fixed header actions, a visible rail toggle, and clickable stage navigation.

**Architecture:** Reuse the existing `journeyProgressPlan` six-stage vocabulary as the single source for left-rail navigation. Keep business workflow facts and APIs unchanged; only derive presentation state in small frontend helpers and render it through existing React pages. Every behavior is locked by component or pure-function tests before CSS and JSX changes.

**Tech Stack:** React 19, TypeScript 6, Vitest, Testing Library, existing CSS design tokens and `data-tip` tooltip host.

## Global Constraints

- Do not modify backend workflow state or add audit stages.
- Keep the top progress bar as the only numeric progress display.
- Main workbench states expose one primary action only.
- Green, yellow, red and gray meanings must be available through mouse hover and keyboard focus; color cannot be the only signal.
- Header action buttons use a fixed `12px` gap and remain grouped at all supported widths.
- Validate at 768px, 1024px, 1202px and 1440px without horizontal overflow.

---

### Task 1: Precise Status Help and Header Controls

**Files:**
- Modify: `web/src/pages/SampleWorkbenchPage.tsx:587-695`
- Modify: `web/src/App.tsx:535-550`
- Modify: `web/src/styles.css:540-560,2290-2365`
- Create: `web/src/pages/SampleWorkbenchPage.clarity.test.tsx`

**Interfaces:**
- Consumes: existing `DESK_LIGHT_LEGEND_TIP`, `lightKpi`, `progressKpi`, `mixedPacketInputRef`, `ledgerInputRef`.
- Produces: `.desk-head-actions`, focusable `.desk-kpi-dot`, and high-emphasis `.rail-toggle` styles used by visual QA.

- [ ] **Step 1: Write failing status, button-group and toggle tests**

Render `SampleWorkbenchPage` with deterministic chain/event API mocks and assert:

```tsx
expect(screen.queryByText(/黄人裁/)).not.toBeInTheDocument()
expect(screen.getByLabelText('绿色 2 笔')).toHaveAttribute(
  'data-tip',
  expect.stringContaining('单据齐全'),
)
expect(screen.getByLabelText('黄色 1 笔')).toHaveAttribute(
  'data-tip',
  expect.stringContaining('人工判断'),
)
expect(screen.getByLabelText('红色 3 笔')).toHaveAttribute(
  'data-tip',
  expect.stringContaining('必须处理'),
)
expect(container.querySelector('.desk-head-actions')).toContainElement(
  screen.getByRole('button', { name: '上传混装资料包' }),
)
```

Extend `App.navigation.test.tsx`:

```tsx
expect(screen.getByRole('button', { name: '收起左侧导航' }))
  .toHaveClass('rail-toggle')
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
Set-Location web
& '.\node_modules\.bin\vitest.cmd' run src/pages/SampleWorkbenchPage.clarity.test.tsx src/App.navigation.test.tsx
```

Expected: FAIL because colored counts are not separately labeled, actions have no wrapper, and the toggle lacks the explicit accessible name.

- [ ] **Step 3: Implement exact status help and fixed controls**

Wrap the two header actions:

```tsx
<div className="desk-head-actions">
  <button className="btn compact">上传混装资料包</button>
  {popCount > 0 ? <label className="btn compact">更换抽样清单…</label> : null}
</div>
```

Render each number as an independently focusable tooltip target:

```tsx
<span
  className="desk-kpi-dot is-green"
  tabIndex={0}
  aria-label={`绿色 ${lightKpi.green} 笔`}
  data-tip="绿色：单据齐全、必要字段齐全且规则未发现异常，可以继续或已通过。"
>{lightKpi.green}</span>
```

Repeat for yellow, red and gray with the approved full wording. Delete the abbreviated legend line. Add `aria-label={railCollapsed ? '展开左侧导航' : '收起左侧导航'}` to the rail button.

Use existing tokens in CSS:

```css
.desk-head-actions { display:flex; align-items:center; gap:12px; flex:0 0 auto; }
.desk-head-actions .btn { display:inline-flex; align-items:center; justify-content:center; min-height:2.25rem; }
.rail-toggle { color:#fff; background:var(--navy); border-color:var(--navy); }
.rail-toggle:hover, .rail-toggle:focus-visible { background:#001a80; box-shadow:var(--focus); }
```

- [ ] **Step 4: Run focused tests and type check**

Run:

```powershell
& '.\node_modules\.bin\vitest.cmd' run src/pages/SampleWorkbenchPage.clarity.test.tsx src/App.navigation.test.tsx
& '.\node_modules\.bin\tsc.cmd' -b
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/pages/SampleWorkbenchPage.tsx web/src/pages/SampleWorkbenchPage.clarity.test.tsx web/src/App.tsx web/src/App.navigation.test.tsx web/src/styles.css
git commit -m "fix: clarify workbench status controls"
```

---

### Task 2: Single Next-Action Card

**Files:**
- Modify: `web/src/pages/SampleWorkbenchPage.tsx:697-765`
- Modify: `web/src/styles.css` near existing `.desk-command*` rules
- Modify: `web/src/pages/SampleWorkbenchPage.clarity.test.tsx`

**Interfaces:**
- Consumes: existing `guide.headline`, `guide.detail`, `guide.ctaLabel`, `guide.action`, `doAction()` and `needsPeriodEnd`.
- Produces: one always-visible `.desk-next-action` card with at most one `.btn.primary` action.

- [ ] **Step 1: Add failing action-card tests**

```tsx
expect(screen.queryByText('查看当前处理说明')).not.toBeInTheDocument()
const card = screen.getByRole('region', { name: '下一步' })
expect(within(card).getByText(/上传抽样清单/)).toBeInTheDocument()
expect(within(card).queryByRole('list')).not.toBeInTheDocument()
expect(within(card).getAllByRole('button')).toHaveLength(1)
```

- [ ] **Step 2: Run and confirm failure**

Run:

```powershell
& '.\node_modules\.bin\vitest.cmd' run src/pages/SampleWorkbenchPage.clarity.test.tsx
```

Expected: FAIL because the workflow is inside a `<details>` element and contains the full stage list.

- [ ] **Step 3: Replace the dense details module**

Render:

```tsx
<section className="desk-next-action" aria-label="下一步">
  <div className="desk-next-copy">
    <span className="desk-next-kicker">下一步</span>
    <strong>{guide.headline}</strong>
    <span>{shortGuideDetail(guide.detail)}</span>
  </div>
  {guide.action.kind !== 'none' ? (
    <button className="btn primary" onClick={() => void doAction(guide.action)}>
      {busy ? '处理中…' : guide.ctaLabel}
    </button>
  ) : null}
</section>
```

Keep the period-end date and save operation inside the card only when `needsPeriodEnd`; the date save button must be secondary so the guide CTA remains the one primary action. Remove `guide.steps` rendering from the main workbench.

- [ ] **Step 4: Verify test and responsive CSS**

Run:

```powershell
& '.\node_modules\.bin\vitest.cmd' run src/pages/SampleWorkbenchPage.clarity.test.tsx
& '.\node_modules\.bin\tsc.cmd' -b
```

Expected: all pass, with `.desk-next-action` using a two-column layout above 820px and a single-column layout below it.

- [ ] **Step 5: Commit**

```powershell
git add web/src/pages/SampleWorkbenchPage.tsx web/src/pages/SampleWorkbenchPage.clarity.test.tsx web/src/styles.css
git commit -m "feat: focus workbench on the next action"
```

---

### Task 3: Clickable Six-Stage Review Navigation

**Files:**
- Create: `web/src/lib/reviewStageNav.ts`
- Create: `web/src/lib/reviewStageNav.test.ts`
- Modify: `web/src/App.tsx:590-680`
- Modify: `web/src/App.navigation.test.tsx`
- Modify: `web/src/styles.css` near `.primary-rail-nav` and `.step-btn`

**Interfaces:**
- Consumes: `Job`, current shell `step`, `journeyProgressPlan(mark)` and existing `goStep(stepId)`.
- Produces:
  - `buildReviewStageNav(job: Job, currentStep: string): ReviewStageNavItem[]`
  - `ReviewStageNavItem = { id: string; step: string; label: string; state: 'done' | 'current' | 'available' | 'locked' }`

- [ ] **Step 1: Write failing pure-function tests**

```ts
expect(buildReviewStageNav(emptyJob, 'goals')).toEqual([
  expect.objectContaining({ label: '选择底稿目标', state: 'current' }),
  expect.objectContaining({ label: '上传抽样清单', state: 'locked' }),
  expect.objectContaining({ label: '上传凭证', state: 'locked' }),
  expect.objectContaining({ label: '核对字段', state: 'locked' }),
  expect.objectContaining({ label: '确认结论', state: 'locked' }),
  expect.objectContaining({ label: '导出底稿', state: 'locked' }),
])
```

Add a populated-job case verifying earlier stages are `done`, the current stage is `current`, the next stage is `available`, and later stages remain `locked`.

Add shell-navigation assertions that make the sidebar language and hierarchy explicit:

```tsx
expect(screen.getByText('高级工具')).toBeInTheDocument()
expect(screen.getByRole('button', { name: '识难录' })).toBeInTheDocument()
expect(screen.getByRole('button', { name: '提示词工程' })).toBeInTheDocument()
expect(screen.queryByText('更多')).not.toBeInTheDocument()
expect(screen.queryByText('审阅设置')).not.toBeInTheDocument()
expect(container.querySelectorAll('.rail .idx')).toHaveLength(0)
```

- [ ] **Step 2: Run and confirm failure**

Run:

```powershell
& '.\node_modules\.bin\vitest.cmd' run src/lib/reviewStageNav.test.ts src/App.navigation.test.tsx
```

Expected: FAIL because `buildReviewStageNav` does not exist and App only exposes root navigation.

- [ ] **Step 3: Implement the stage adapter**

Map presentation IDs to shell steps:

```ts
const ROUTES = {
  goals: 'goals', ledger: 'sample_desk', upload: 'upload_ocr',
  fields: 'field_confirm', gate5: 'conclusion_gate5', export: 'workbook_export',
} as const
```

Build `JourneyMark` from persisted job facts: selected goals, sample population, pending/classified documents, field confirmation, sample conclusion confirmation and workbook paths. Use `journeyProgressPlan()` to preserve the existing stage order and blocked calculation, then translate it to navigation states.

- [ ] **Step 4: Render stage navigation in App**

Under the `审阅` label, render six buttons from `buildReviewStageNav(job, step)`. Buttons in `locked` state use `disabled`; current uses `aria-current="step"`; done receives a textual `✓` indicator. Keep `待裁决` as a separate root button below the stage list. Remove the former standalone `工作台` and `导出` roots because those routes now exist in the six-stage list.

Delete the opaque `更多` group and the standalone `审阅设置` entry. Restore the pre-V2 advanced-tool hierarchy as a separate `高级工具` group containing the original complete labels `识难录` and `提示词工程`. The goal-setting route remains available through the first review-stage button, `选择底稿目标`.

Do not render `.idx` or any other single-character logo before left-rail labels. This applies to the six stages, `待裁决`, and both advanced-tool entries; retain only full function names plus necessary textual state marks.

- [ ] **Step 5: Verify navigation tests and type check**

Run:

```powershell
& '.\node_modules\.bin\vitest.cmd' run src/lib/reviewStageNav.test.ts src/App.navigation.test.tsx src/App.v2.test.tsx
& '.\node_modules\.bin\tsc.cmd' -b
```

Expected: all pass; clicking an available stage calls the existing shell route, while locked stages cannot navigate.

- [ ] **Step 6: Commit**

```powershell
git add web/src/lib/reviewStageNav.ts web/src/lib/reviewStageNav.test.ts web/src/App.tsx web/src/App.navigation.test.tsx web/src/styles.css
git commit -m "feat: add navigable audit stages"
```

---

### Task 4: Full Regression and Visual Quality Gate

**Files:**
- Modify only if verification reveals a scoped defect: files changed in Tasks 1-3.

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: verified V2 workbench at supported desktop and narrow widths.

- [ ] **Step 1: Run the complete frontend suite**

```powershell
Set-Location web
& '.\node_modules\.bin\vitest.cmd' run
& '.\node_modules\.bin\tsc.cmd' -b
& '.\node_modules\.bin\oxlint.cmd' src
& '.\node_modules\.bin\vite.cmd' build
```

Expected: zero test/type/build failures; existing lint warnings may remain only if unrelated to this change.

- [ ] **Step 2: Run affected backend integration smoke test**

```powershell
Set-Location ..
& '.\.venv\Scripts\python.exe' -m pytest tests/test_event_driven_v2_e2e.py -q
```

Expected: pass; this UI-only polish must not alter workflow API behavior.

- [ ] **Step 3: Browser visual QA**

At 768px, 1024px, 1202px and 1440px verify:

- header buttons remain a fixed-gap group;
- rail toggle is visually prominent in expanded and collapsed states;
- each colored count shows the approved full tooltip by mouse and keyboard focus;
- the main area shows one next-action card and no six-step duplicate;
- six left stages align vertically; completed/current/available/locked states are distinguishable without relying only on color;
- `高级工具` is independently visible and expands to the full original labels `识难录` and `提示词工程`;
- neither `更多` nor `审阅设置` appears, and no left-rail button has a standalone single-character logo;
- no horizontal overflow or clipped labels.

- [ ] **Step 4: Repository checks and commit any QA-only fix**

```powershell
git diff --check
git status --short
```

If visual QA required a scoped correction, rerun Steps 1-3 and commit it:

```powershell
git add web/src
git commit -m "fix: polish responsive audit navigation"
```

## Spec Coverage Self-Review

- Status wording and separate hover/focus help: Task 1.
- Fixed header button spacing and centered labels: Task 1.
- Colored rail toggle: Task 1.
- Dense process module replaced with one next action: Task 2.
- Six clickable left stages with completion/current/locked states: Task 3.
- Explicit `高级工具` hierarchy, restored tool labels, and removal of `更多`/`审阅设置`: Task 3.
- Removal of every standalone single-character left-rail logo: Task 3.
- Top progress remains numeric and backend facts remain unchanged: Tasks 3-4.
- Keyboard, responsive and one-primary-action acceptance: Tasks 1-4.
- No placeholders, undefined interfaces or unassigned spec requirements remain.
