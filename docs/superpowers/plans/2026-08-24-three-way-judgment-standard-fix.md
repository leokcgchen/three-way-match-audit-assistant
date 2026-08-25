# Three-Way Judgment Standard Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复业务链二次筛空、零值覆盖和重复佐证累计，使 SO25-0296 的三单匹配为绿色通过，同时保持截止性独立失败。

**Architecture:** `run_three_way()` 接收的当前链单据视为已经完成范围裁剪；一对多引擎只在明确存在业务组元数据时再次过滤。履约累计以强事件键去重，空结果输出 `NOT_TESTED`，决策协调器只允许有有效履约行和明确异常标志的结果改变三单决策。

**Tech Stack:** Python 3.12、pytest、FastAPI、React/Vitest（回归验证）、Vite。

## Global Constraints

- 三单一致性字段仅为客户名称、价税合计和数量。
- 编号只做串联，日期只做时序/截止性。
- 不修改截止性算法，不修改历史 OCR 原始字段。
- 不新增第三方依赖，不执行 Git 提交、合并或推送。
- 所有生产代码修改前必须先观察到对应回归测试按预期失败。

---

### Task 1: 修复已裁剪业务链的二次筛选

**Files:**
- Modify: `tests/test_three_way_one_to_many_pipeline.py`
- Modify: `src/workflow/pipeline.py`

**Interfaces:**
- Consumes: `run_three_way(classified, business_group_id=...)`
- Produces: `_explicit_group_filter(classified, business_group_id) -> str | None`，仅在输入单据确实携带匹配的显式业务组元数据时返回过滤值。

- [ ] **Step 1: 写入失败回归测试**

在 `tests/test_three_way_one_to_many_pipeline.py` 增加：

```python
def test_pre_scoped_chain_is_not_filtered_empty_by_external_chain_id() -> None:
    pack = _load_fixture("classified_complete.json")
    result = run_three_way(pack["classified"], business_group_id="SO25-0296")

    assert result["fulfillment"]["rows"]
    assert result["match_result"]["quantity_roles"] == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert result["match_result"]["decision"] == "AUTO_PASS"
```

- [ ] **Step 2: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many_pipeline.py::test_pre_scoped_chain_is_not_filtered_empty_by_external_chain_id -q`

Expected: FAIL；当前实现把夹具筛空，`fulfillment.rows == []` 或数量为 0。

- [ ] **Step 3: 最小实现显式过滤判断**

在 `src/workflow/pipeline.py` 中加入：

```python
def _explicit_group_filter(
    classified: list[dict[str, Any]], business_group_id: Optional[str]
) -> Optional[str]:
    wanted = str(business_group_id or "").strip()
    if not wanted:
        return None
    for item in classified:
        if str(item.get("business_group_id") or "").strip() == wanted:
            return wanted
        if wanted in {str(value).strip() for value in item.get("business_ids") or []}:
            return wanted
    return None
```

调用 `run_one_to_many()` 时传入 `_explicit_group_filter(classified, business_group_id)`；没有显式元数据时保留已经裁剪好的全部输入单据。

- [ ] **Step 4: 运行绿灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many_pipeline.py::test_pre_scoped_chain_is_not_filtered_empty_by_external_chain_id -q`

Expected: PASS。

---

### Task 2: 空履约结果不得覆盖 OCR 或降级

**Files:**
- Modify: `tests/test_three_way_one_to_many.py`
- Modify: `tests/test_three_way_one_to_many_pipeline.py`
- Modify: `src/three_way_match/one_to_many.py`
- Modify: `src/workflow/pipeline.py`

**Interfaces:**
- Produces: 空履约返回 `light="NOT_TESTED"`、`quantity_roles={}`、`amount_roles={}`。
- Decision rule: `fulfillment.rows` 为空时 `_apply_fulfillment_decision()` 原样返回三单字段结果。

- [ ] **Step 1: 写入空履约单元测试**

```python
def test_no_order_lines_is_not_treated_as_partial_fulfillment() -> None:
    docs = [
        {"file_name": "order.pdf", "doc_type": "order", "fields": {"orderNo": "SO-1"}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "fields": {"orderNo": "SO-1"}},
        {"file_name": "invoice.pdf", "doc_type": "invoice", "fields": {"orderNo": "SO-1"}},
    ]
    result = run_one_to_many(docs)

    assert result["rows"] == []
    assert result["light"] == "NOT_TESTED"
    assert result["quantity_roles"] == {}
    assert "部分履约" not in result["summary"]
```

- [ ] **Step 2: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py::test_no_order_lines_is_not_treated_as_partial_fulfillment -q`

Expected: FAIL；当前返回黄灯及 0 汇总。

- [ ] **Step 3: 实现空结果的 `NOT_TESTED` 语义**

在 `run_one_to_many()` 汇总处，当 `rows` 为空时返回：

```python
light = "NOT_TESTED"
summary = "未提取到可累计的订单行，履约累计未测"
quantity_roles = {}
amount_roles = {}
```

有履约行时保留现有红黄绿逻辑和数值汇总。

- [ ] **Step 4: 写入决策协调回归测试**

在 pipeline 测试中构造客户及订单/发票金额一致、但三份单据均无数量行的数据，断言结果不得包含 `PARTIAL_SET`，且不得出现“当前资料部分履约”。

- [ ] **Step 5: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many_pipeline.py -k "empty_fulfillment" -q`

Expected: FAIL；当前 `_apply_fulfillment_decision()` 把空黄灯降成 `HOLD_REVIEW`。

- [ ] **Step 6: 增加两层防护**

```python
if not fulfillment.get("rows"):
    return result
```

并仅在 `fulfillment.rows` 非空时使用累计数量/金额更新 `match_request`，防止 0 覆盖 OCR 字段。

- [ ] **Step 7: 运行 Task 2 测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py tests/test_three_way_one_to_many_pipeline.py -q`

Expected: PASS。

---

### Task 3: 同一履约事件的重复佐证只累计一次

**Files:**
- Modify: `tests/test_three_way_one_to_many.py`
- Modify: `src/three_way_match/one_to_many.py`

**Interfaces:**
- Produces: `duplicate_evidence_files: list[dict[str, str]]`。
- Strong event key: 角色、业务单据号、关联订单号、业务日期、总数量；业务单据号为空时不自动去重。

- [ ] **Step 1: 写入重复佐证失败测试**

构造订单数量 912、两份具有相同 `documentNo=YS25-0296`、`orderNo=SO25-0296`、`acceptanceDate=2025-12-27`、数量 912 的验收文件，以及发票数量 912；断言：

```python
assert result["quantity_roles"] == {
    "ordered_qty": 912.0,
    "received_qty": 912.0,
    "invoiced_qty": 912.0,
}
assert result["light"] == "GREEN"
assert len(result["duplicate_evidence_files"]) == 1
```

- [ ] **Step 2: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py -k "corroborating_receipts" -q`

Expected: FAIL；当前累计签收为 1824 并判超额。

- [ ] **Step 3: 实现强事件键去重**

在 `one_to_many.py` 增加 `_document_event_key(item, role)`：

- 订单号优先取 `orderNo/documentNo`；
- 验收号优先取 `documentNo/receiptNo`；
- 发票号优先取 `invoiceNo/documentNo`；
- 日期按角色取 `acceptanceDate/deliveryDate/documentDate`；
- 总数量为该文件有效行数量之和；
- 缺业务单据号时返回 `None`，避免模糊去重。

每个角色保留第一个强事件键文件参与累计，后续相同键文件写入 `duplicate_evidence_files`，但不加入错误标志，不重复累计。

- [ ] **Step 4: 运行 Task 3 测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py -q`

Expected: PASS，且既有“同一来源行重复”硬错误测试仍通过。

---

### Task 4: 多发票累计与统一金额口径

**Files:**
- Modify: `tests/test_three_way_one_to_many.py`
- Modify: `tests/test_three_way_one_to_many_pipeline.py`
- Modify: `src/three_way_match/one_to_many.py`

**Interfaces:**
- Produces: 明细金额口径 `gross_total | amount_plus_tax | amount_only`，以及累计 `amount_roles`。

- [ ] **Step 1: 写入多发票和含税金额失败测试**

构造订单数量 100、含税金额 1,130；签收数量 100；两张发票分别为 60/678 和 40/452。每张明细同时提供 `amount`、`taxAmount`、`totalAmount`，断言累计开票数量 100、累计含税金额 1,130、结果绿色通过。

- [ ] **Step 2: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py -k "multiple_invoices_use_gross_amount" -q`

Expected: FAIL；当前 `_amount()` 优先使用未税 `amount`。

- [ ] **Step 3: 实现统一金额解析**

金额选择顺序固定为：有效 `totalAmount` → `amount + taxAmount` → `amount`。逐行保留口径，汇总结果增加 `amount_basis`；不得把不同口径静默相加后宣称含税总额。

- [ ] **Step 4: 运行多发票测试和既有累计测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py tests/test_three_way_one_to_many_pipeline.py -q`

Expected: PASS。

---

### Task 5: 唯一合同的受限替代锚点

**Files:**
- Modify: `tests/test_three_way_one_to_many_pipeline.py`
- Modify: `tests/test_three_way_audit_trace.py`
- Modify: `src/workflow/pipeline.py`
- Modify: `src/three_way_match/audit_trace.py`

**Interfaces:**
- Produces: `anchor_source="CONTRACT_AS_ORDER_ANCHOR"`。
- Contract eligibility: 唯一合同、客户存在、合同编号存在、数量或金额可复算、全部签收/发票共享该合同引用。

- [ ] **Step 1: 写入合格合同锚点与不合格合同失败测试**

合格夹具为一份合同 + 一份签收 + 两张发票，共享 `HT-1`，累计数量与金额一致；断言三单通过并披露合同锚点。不合格夹具缺少合同经济字段或存在两份合同，断言 `HOLD_REVIEW`。

- [ ] **Step 2: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many_pipeline.py -k "contract_anchor" -q`

Expected: FAIL；当前无订单直接返回缺少订单。

- [ ] **Step 3: 实现 `_contract_order_anchor()`**

仅在资格条件全部满足时，将合同字段复制成内部订单锚点供累计和字段勾稽使用；原始 `classified` 保持合同类型，结果明确写入 `anchor_source`。审计轨迹的必要锚点调整为“订单或合格合同”。

- [ ] **Step 4: 运行 Task 5 测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many_pipeline.py tests/test_three_way_audit_trace.py -q`

Expected: PASS。

---

### Task 6: 多发票逐张客户、引用和截止性判断

**Files:**
- Modify: `tests/test_three_way_one_to_many_pipeline.py`
- Modify: `src/workflow/pipeline.py`
- Modify: `src/workflow/three_way_persist.py`

**Interfaces:**
- Produces: `invoice_checks: list[dict]`，每项包含 `file_name`、`customer_status`、`business_reference_status`、`cutoff_status`、`cutoff_result`。
- Produces: `invoice_cutoff_status`，按 `FAIL > WARNING > PASS > SKIPPED` 汇总。

- [ ] **Step 1: 写入逐发票失败测试**

测试两张金额合计正确的发票：一张客户一致，另一张客户不一致，断言不得自动通过。另一个测试让两张发票分别期内与跨期，断言总体截止性为 `FAIL`，且保留两张逐项结果。

- [ ] **Step 2: 运行红灯测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many_pipeline.py -k "each_invoice" -q`

Expected: FAIL；当前只检查合并后选中的一张发票。

- [ ] **Step 3: 实现逐发票审计**

逐张发票使用订单/合同锚点客户进行规范化精确比较；业务引用使用跨单据完全相同引用规则；有签收日期和入账日期时调用现有 `perform_cutoff()`，不复制截止性公式。任一客户/引用失败将三单决策设为 `HOLD_REVIEW`；截止性只更新独立截止结果，不反向修改三单结论。

- [ ] **Step 4: 持久化并测试逐发票结果**

`three_way_sample_patch()` 必须保存 `invoice_checks` 和 `invoice_cutoff_status`。运行 Task 6 测试，Expected: PASS。

---

### Task 7: 当前业务复跑与完整质量门

**Files:**
- Verify: `tests/test_three_way_one_to_many.py`
- Verify: `tests/test_three_way_one_to_many_pipeline.py`
- Verify: `tests/test_three_way_audit_trace.py`
- Verify: `web/src/components/ConclusionEvidenceTable.test.tsx`

**Interfaces:**
- API: `POST /api/v1/workflow/jobs/b8a5726d992e/three-way-cutoff`
- Expected current chain: `SO25-0296`

- [ ] **Step 1: 运行后端针对性测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_three_way_one_to_many.py tests/test_three_way_one_to_many_pipeline.py tests/test_three_way_audit_trace.py -q`

Expected: 全部 PASS。

- [ ] **Step 2: 运行后端全量测试**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: 0 failed。

- [ ] **Step 3: 运行前端全量测试与生产构建**

Run: `node node_modules/vitest/vitest.mjs run`

Run: `node node_modules/typescript/bin/tsc -b` 后运行 `node node_modules/vite/bin/vite.js build`

Expected: 0 failed，构建退出码 0。

- [ ] **Step 4: 通过正式 API 复跑 SO25-0296**

调用当前任务的三单+截止接口，不传手工覆盖；重新读取任务并核对：

```text
three_way.decision == AUTO_PASS
three_way.quantity_roles == 912 / 912 / 912
three_way.fulfillment.light == GREEN
three_way.cutoff_status == FAIL
```

- [ ] **Step 5: 浏览器视觉检查**

确认结论页三单卡为绿色“通过”，经济字段表数量为 912/912/912；截止性卡仍显示 FAIL，且没有“当前资料部分履约”或 0/0/0。
