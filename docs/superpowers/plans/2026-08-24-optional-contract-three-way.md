# Optional Contract in Three-Way Match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让订单、签收/验收与发票组成的完整三单不再因缺合同编号被阻断。

**Architecture:** 以 `sample_required_fields` 为后端门禁唯一口径，并同步前端本地兜底字段集合。合同数据保留在动态展示层；时序判断根据实际存在的日期节点构建，不补造合同要求。

**Tech Stack:** Python 3.11、pytest、React 19、TypeScript、Vitest。

## Global Constraints

- 不提交 Git，不清理或覆盖工作树中的既有修改。
- 不改变明确包含合同条款测试的其他底稿目标。
- 合同编号有值时仍可展示和定位。

---

### Task 1: 后端字段门禁

**Files:**
- Modify: `src/workflow/sample_required_fields.py`
- Modify: `src/workflow/field_catalog.py`
- Test: `tests/test_sample_population_excel.py`

**Interfaces:**
- Consumes: `required_fields_for_docs(docs, goal_ids)`。
- Produces: 不含虚假 `contractNo` 缺口的必填字段结果。

- [x] **Step 1: 写入失败测试**：构造订单、签收/验收、发票齐全且没有合同编号的 01030 业务，断言缺字段中没有 `contractNo`。
- [x] **Step 2: 运行测试确认 RED**：运行该测试，预期旧实现返回 `contractNo`。
- [x] **Step 3: 最小修复**：从 01030 订单必填和通用订单系统必填中移除 `contractNo`，保留为订单可选字段。
- [x] **Step 4: 运行测试确认 GREEN**：同一测试通过，并运行相关样本工作台测试。

### Task 2: 前端字段与时序口径

**Files:**
- Modify: `web/src/lib/fieldComparison.ts`
- Test: `web/src/lib/fieldComparison.test.ts`

**Interfaces:**
- Consumes: `requiredRowsFromDocs`、`buildFieldComparison`。
- Produces: 合同可选的字段矩阵与动态时间线。

- [x] **Step 1: 写入失败测试**：断言订单、签收/验收、发票无合同号时，必填行无 `contractNo`，时序结果不是因合同日缺失而 `REVIEW`。
- [x] **Step 2: 运行测试确认 RED**：预期旧实现仍包含合同号或提示缺合同日。
- [x] **Step 3: 最小修复**：同步必填集合，并根据实际存在的日期节点构建时序比较。
- [x] **Step 4: 运行测试确认 GREEN**：字段比较测试通过。

### Task 3: 回归验收

**Files:**
- Verify: `tests/`
- Verify: `web/src/`

**Interfaces:**
- Consumes: 后端与前端最终实现。
- Produces: 可复核的测试、构建与页面行为证据。

- [x] **Step 1: 运行后端相关测试和完整测试套件**。
- [x] **Step 2: 运行完整 Vitest、TypeScript 编译和 Vite 构建**。
- [x] **Step 3: 打开当前接口并确认 3962 的 `missing_labels` 不再包含合同编号；页面刷新后使用同一结果**。
- [x] **Step 4: 运行 `git diff --check`，确认没有空白错误且不提交 Git**。
