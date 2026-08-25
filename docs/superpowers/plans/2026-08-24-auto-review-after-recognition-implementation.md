# 识别完成后自动逐笔审阅与灯号回写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让普通 OCR 和重新识别在字段落库后自动逐笔完成审阅测试并回写真实红黄绿灯。

**Architecture:** 复用现有 `finish_after_classify()` 和 `run_batch_review()`，不新增第二套审阅引擎。扩展字段证据门禁，使常见发票编号标签及由已定位明细构成的汇总字段能够自动通过；所有识别入口统一调用同一收尾函数，并保存批量审阅摘要与逐笔失败原因。

**Tech Stack:** Python 3.11、FastAPI、pytest、现有 `JOB_STORE` 与 GOSPD 分笔状态模型。

## Global Constraints

- 灯号必须来自实际保存的逐笔测试结果，不允许前端硬编码绿灯。
- 文件名不能单独作为字段证据；自动确认仍要求原件文字或坐标定位。
- 人工接受值保持最高优先级，识别重跑不得覆盖。
- 单笔异常不能阻断其他业务继续测试。
- 保持当前分支和现有未提交改动，不执行 Git 提交。

---

### Task 1: 修复字段证据门禁

**Files:**
- Modify: `src/workflow/field_resolution/evidence_gate.py`
- Test: `tests/test_field_evidence_gate.py`

**Interfaces:**
- Consumes: `document["fields"]`、`document["field_evidence_nodes"]`、`evidence_for_field()`。
- Produces: `evaluate_candidate(document, field_key) -> FieldGateDecision`，新增理由码 `DERIVED_FROM_VERIFIED_LINE_ITEMS`。

- [ ] **Step 1: 写入发票号码常见标签失败测试**

```python
def test_invoice_number_with_no_prefix_can_be_system_verified() -> None:
    doc = {
        "doc_type": "invoice",
        "raw_text": "No FP-260102-8305\n增值税专用发票",
        "text_blocks": [{"text": "FP-260102-8305", "page": 0, "bbox": [1, 2, 3, 4]}],
        "fields": {"invoiceNo": "FP-260102-8305"},
    }
    seed_field_meta(doc, source="ocr")
    assert evaluate_candidate(doc, "invoiceNo").status == "SYSTEM_VERIFIED"
```

- [ ] **Step 2: 写入明细加总证据失败测试**

```python
def test_aggregate_quantity_is_verified_from_positioned_line_items() -> None:
    doc = {
        "doc_type": "order",
        "raw_text": "数量 10\n数量 15\n数量 20",
        "text_blocks": [
            {"text": "10", "page": 0, "bbox": [1, 1, 2, 2]},
            {"text": "15", "page": 0, "bbox": [1, 3, 2, 4]},
            {"text": "20", "page": 0, "bbox": [1, 5, 2, 6]},
        ],
        "fields": {
            "quantity": 45,
            "items": [{"quantity": 10}, {"quantity": 15}, {"quantity": 20}],
        },
    }
    seed_field_meta(doc, source="ocr")
    decision = evaluate_candidate(doc, "quantity")
    assert decision.status == "SYSTEM_VERIFIED"
    assert decision.reason_code == "DERIVED_FROM_VERIFIED_LINE_ITEMS"
    assert len(decision.evidence_ids) == 3
```

- [ ] **Step 3: 运行测试并确认按预期失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_field_evidence_gate.py -q`

Expected: 两个新用例分别因 `ROLE_CONFLICT` 与汇总值 `UNLOCATED` 失败。

- [ ] **Step 4: 实现最小证据规则**

在 `evidence_gate.py` 中：

```python
_DERIVED_LINE_KEYS = {
    "quantity": ("quantity", "qty"),
    "amount": ("amount", "netAmount"),
    "totalAmount": ("totalAmount",),
}

def _verified_line_item_sum(document: dict[str, Any], field_key: str) -> FieldGateDecision | None:
    """仅当每个构成行都能定位且加总等于当前字段值时返回 SYSTEM_VERIFIED。"""
```

发票角色的 `invoiceNo` 在原件上下文出现 `No FP-...` 或 `Invoice No` 时允许通过；汇总校验必须逐行使用可用证据节点并返回全部 `evidence_ids`。加总不一致或任一行无定位时继续保留人工复核，不降级放行。

- [ ] **Step 5: 运行字段证据测试并确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_field_evidence_gate.py -q`

Expected: 全部通过。

---

### Task 2: 统一普通识别与重新识别的自动审阅收尾

**Files:**
- Modify: `src/api/workflow_router.py`
- Modify: `src/workflow/sample_desk.py`
- Test: `tests/test_recognition_reprocess.py`
- Test: `tests/test_batch_review_flow.py`

**Interfaces:**
- Consumes: `finish_after_classify(job_id: str) -> dict[str, Any]`、`run_batch_review(job_id, force_rerun=False)`。
- Produces: `job["auto_review_last_run"]`，包含 `status`、`summary`、`ran`、`skipped`、`failed`、`warnings` 和时间戳。

- [ ] **Step 1: 写入重新识别必须进入自动审阅的失败测试**

通过 `TestClient` 创建任务，替换耗时的重新提取函数为确定性返回值；让 `finish_after_classify()` 给任务写入可观察标记。POST `/recognition/reprocess` 后断言响应包含该标记。当前实现只执行 `JOB_STORE.update()`，测试应失败。

- [ ] **Step 2: 写入自动审阅摘要持久化失败测试**

构造 GOSPD 任务，替换批量测试计算为固定结果：

```python
{
    "summary": "已处理 1 笔；跳过 1 笔；失败 0 笔",
    "ran": [{"chain_id": "YW-1", "actions": ["三单+截止"]}],
    "skipped": [{"chain_id": "YW-2", "reason": "字段未确认"}],
    "failed": [],
}
```

调用 `finish_after_classify()`，断言 `auto_review_last_run` 保存摘要及逐笔原因。当前实现静默吞错且不保存报告，测试应失败。

- [ ] **Step 3: 运行两个定向测试并确认失败原因正确**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recognition_reprocess.py tests/test_batch_review_flow.py -q`

- [ ] **Step 4: 实现统一收尾与可观察报告**

在 `finish_after_classify()` 中无论本轮自动确认数量是否为零，都调用 `run_batch_review()`；把返回值裁剪为小型摘要写入 `auto_review_last_run`。捕获单笔/批量异常时记录到 `failed` 或 `warnings`，不使用空的 `except: pass`。

在 `/recognition/reprocess` 路由中：

```python
JOB_STORE.update(job_id, **patch, auto_review_processing=True)
try:
    finish_after_classify(job_id)
finally:
    result = JOB_STORE.update(job_id, auto_review_processing=False)
return result
```

- [ ] **Step 5: 运行定向测试并确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recognition_reprocess.py tests/test_batch_review_flow.py -q`

---

### Task 3: 回归自动批量测试与当前任务灯号

**Files:**
- Test: `tests/test_batch_review_flow.py`
- Test: `tests/test_three_way_one_to_many_pipeline.py`
- Runtime state: current job `5dd69ee5d6ef`

**Interfaces:**
- Consumes: `/api/v1/workflow/jobs/{job_id}/batch-review` 或 `finish_after_classify(job_id)`。
- Produces: 每笔 `gospd_sample_results[chain_id]` 中保存 `evidence`、`three_way`、`cutoff_test` 与结论状态。

- [ ] **Step 1: 运行自动审阅与一对多回归测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_review_flow.py tests/test_three_way_one_to_many.py tests/test_three_way_one_to_many_pipeline.py -q`

- [ ] **Step 2: 运行完整后端测试**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: 无新增失败。

- [ ] **Step 3: 对当前任务执行一次正式自动审阅回放**

调用当前任务的统一识别后收尾入口，使所有可自动业务逐笔执行测试并写回结果。不得自动解决 3992 的人工金额冲突，也不得把 3995 的不确定单据类型强制判为正常。

- [ ] **Step 4: 核对真实灯号与逐笔原因**

检查 `/jobs/5dd69ee5d6ef/chains`：所有业务不得无故长期停留“测试进行中”；绿灯必须具备已保存测试，黄灯和红灯必须给出具体原因。独立复算 3992 的三行数量 45、金额 305100，并确认其人工金额冲突仍保留。

- [ ] **Step 5: 运行前端回归与构建**

Run: `npm test -- --run`

Run: `npm run build`

Working directory: `web`

Expected: 测试和构建均通过。

