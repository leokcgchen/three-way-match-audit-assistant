# Business Alias Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保存抽样清单中的业务编号与订单号，用联合索引把文件稳定归入清单内业务，并把冲突和相似候选交给审计师处理。

**Architecture:** `sample_population.py` 负责保留业务—订单关系；新建 `business_alias_index.py` 负责构建反向索引、解析文件名和产出可解释决策；`sample_scope.py` 只负责按决策放行或进入异常区。后端把联合索引提供给工作台，前端分别展示业务行联合索引和文件级命中证据。

**Tech Stack:** Python 3、openpyxl、FastAPI、pytest、React、TypeScript、Vitest、Testing Library。

## Global Constraints

- `business_id` 是最终审计业务主键，订单号是可多值别名。
- 界面显示 `业务编号 & 订单号`，底层不得保存为不可拆分的拼接主键。
- 确定性规范化后的唯一精确命中可以自动归类。
- 仅数字相似或单字符差异只能生成候选，不得自动归类。
- 强索引指向不同业务时必须进入异常区。
- OCR 和文件名不得扩充抽样清单。
- 迁移现有文件时不重跑 OCR。
- 不执行 Git add、commit、checkout、reset 或其他 Git 写操作。

---

### Task 1: 保留抽样清单订单号关系

**Files:**
- Modify: `src/audit/sample_population.py`
- Test: `tests/test_sample_population_excel.py`

**Interfaces:**
- Consumes: 工作簿表头、数据行、已选中的 `primary_key_col`
- Produces: 每行 `order_numbers: list[str]`，总体 `ambiguous_aliases: list[dict[str, Any]]`

- [ ] **Step 1: 写业务编号与订单号同时保留的失败测试**

```python
def test_business_primary_key_preserves_sales_order_aliases(tmp_path):
    path = tmp_path / "aliases.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["业务编号", "销售订单号", "账载日期", "金额"])
    ws.append(["YW-2025-3962", "SO-251209-7214", "2026-01-02", 113000])
    ws.append(["YW-2025-3971", "SO-251212-7259", "2025-12-31", 678000])
    wb.save(path)
    result = parse_sample_workbook(path)
    assert result["rows"][0]["business_id"] == "YW-2025-3962"
    assert result["rows"][0]["order_numbers"] == ["SO-251209-7214"]
```

- [ ] **Step 2: 写一业务多订单与重复订单失败测试**

```python
def test_aliases_merge_per_business_and_report_cross_business_duplicates(tmp_path):
    path = tmp_path / "duplicate-alias.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["业务编号", "订单编号"])
    ws.append(["YW-1", "SO-1"])
    ws.append(["YW-1", "SO-2"])
    ws.append(["YW-2", "SO-2"])
    wb.save(path)
    result = parse_sample_workbook(path)
    assert result["rows"][0]["order_numbers"] == ["SO-1", "SO-2"]
    assert result["ambiguous_aliases"] == [
        {"type": "order_number", "value": "SO-2", "business_ids": ["YW-1", "YW-2"]}
    ]
```

- [ ] **Step 3: 运行测试确认 RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_sample_population_excel.py -q -p no:cacheprovider`

Expected: 旧解析结果没有 `order_numbers` 和 `ambiguous_aliases`，新增断言失败。

- [ ] **Step 4: 实现订单列识别与关系合并**

```python
order_col = _pick_col(headers, _ORDER_COL_BEST, skip={primary_key_col})
order_number = _normalize_primary_key_value(take(row, order_col)) if order_col else ""
parsed_row = {
    "business_id": nid,
    "order_numbers": [order_number] if order_number else [],
    "book_date": book_date,
    "book_amount": book_amount,
    "customer": customer,
    "sheet": sheet,
}
```

按 `business_id` 合并行时，对 `order_numbers` 稳定去重；再构建 `order_number -> business_ids` 反向关系并输出跨业务重复项。

- [ ] **Step 5: 运行测试确认 GREEN**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_sample_population_excel.py -q -p no:cacheprovider`

Expected: 新旧抽样清单测试全部通过。

### Task 2: 联合索引与归类决策引擎

**Files:**
- Create: `src/workflow/business_alias_index.py`
- Create: `tests/test_business_alias_index.py`

**Interfaces:**
- Produces: `normalize_alias(value: Any) -> str`
- Produces: `build_alias_index(sample_population: dict[str, Any]) -> dict[str, Any]`
- Produces: `resolve_document_business(document: dict[str, Any], sample_population: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: 写唯一命中与双证据失败测试**

```python
def _population(business_id: str, order_numbers: list[str]) -> dict:
    return {
        "business_ids": [business_id],
        "rows": [{"business_id": business_id, "order_numbers": order_numbers}],
    }


def _two_business_population() -> dict:
    return {
        "business_ids": ["YW-1", "YW-2"],
        "rows": [
            {"business_id": "YW-1", "order_numbers": ["SO-1"]},
            {"business_id": "YW-2", "order_numbers": ["SO-2"]},
        ],
    }


def test_business_or_unique_order_alias_resolves_same_business():
    population = _population("YW-2025-3962", ["SO-251209-7214"])
    by_business = resolve_document_business(
        {"file_name": "YW-2025-3962_发票.pdf", "fields": {}}, population
    )
    by_order = resolve_document_business(
        {"file_name": "销售订单_SO-251209-7214.pdf", "fields": {}}, population
    )
    both = resolve_document_business(
        {"file_name": "YW-2025-3962_SO-251209-7214.pdf", "fields": {}}, population
    )
    assert by_business["business_id"] == by_order["business_id"] == "YW-2025-3962"
    assert both["confidence"] == "highest"
    assert {item["type"] for item in both["evidence"]} == {"business_id", "order_number"}
```

- [ ] **Step 2: 写冲突、重复别名和相似候选失败测试**

```python
def test_conflicting_strong_aliases_never_auto_resolve():
    result = resolve_document_business(
        {"file_name": "YW-1_SO-2.pdf", "fields": {}},
        _two_business_population(),
    )
    assert result["status"] == "CONFLICT"
    assert result["business_id"] is None

def test_similar_digits_are_candidates_not_matches():
    result = resolve_document_business(
        {"file_name": "YW-2025-3992.pdf", "fields": {}},
        _population("YW-2025-3962", []),
    )
    assert result["status"] != "MATCHED"
    assert result["business_id"] is None
```

- [ ] **Step 3: 运行测试确认 RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_business_alias_index.py -q -p no:cacheprovider`

Expected: 新模块尚不存在，测试收集失败。

- [ ] **Step 4: 实现规范化与反向索引**

```python
def normalize_alias(value: Any) -> str:
    return re.sub(r"[-_\s]", "", str(value or "").strip().upper())

def build_alias_index(sample_population: dict[str, Any]) -> dict[str, Any]:
    aliases: dict[str, set[str]] = defaultdict(set)
    for row in sample_population.get("rows") or []:
        business_id = str(row.get("business_id") or "").strip()
        for value in [business_id, *(row.get("order_numbers") or [])]:
            if value:
                aliases[normalize_alias(value)].add(business_id)
    return {"aliases": aliases}
```

索引条目同时保存 `type`、原始值和来源列；重复别名保留全部业务集合，不能取第一条。

- [ ] **Step 5: 实现决策矩阵**

文件名完整编号和 OCR 明确索引字段分别产出证据。所有精确证据汇总后：只指向一个业务返回 `MATCHED`；指向多个业务返回 `CONFLICT`；重复别名返回 `AMBIGUOUS_ALIAS`；只存在近似项返回 `SIMILAR_CANDIDATE`；无项返回 `UNASSIGNED`。

- [ ] **Step 6: 运行测试确认 GREEN**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_business_alias_index.py -q -p no:cacheprovider`

Expected: 全部通过。

### Task 3: 抽样范围、业务分组与无 OCR 迁移

**Files:**
- Modify: `src/workflow/sample_scope.py`
- Modify: `src/workflow/sample_desk.py`
- Modify: `src/api/workflow_router.py`
- Test: `tests/test_sample_scope.py`
- Test: `tests/test_sample_population_excel.py`

**Interfaces:**
- Consumes: `resolve_document_business(...)` 的结构化决策
- Persists: `business_index_status`、`business_index_confidence`、`business_index_evidence`
- Produces: 异常状态 `INDEX_CONFLICT`、`AMBIGUOUS_ALIAS`、`SIMILAR_CANDIDATE`

- [ ] **Step 1: 写订单号单独归类与冲突隔离失败测试**

```python
def test_unique_order_number_accepts_document_without_business_id():
    population = {
        "business_ids": ["YW-2025-3962"],
        "rows": [{
            "business_id": "YW-2025-3962",
            "order_numbers": ["SO-251209-7214"],
        }],
    }
    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "SO-251209-7214_签收单.pdf", "fields": {}}],
        population,
    )
    assert exceptions == []
    assert accepted[0]["sample_business_id"] == "YW-2025-3962"
    assert accepted[0]["business_index_evidence"][0]["type"] == "order_number"

def test_business_and_order_alias_conflict_is_quarantined():
    population = {
        "business_ids": ["YW-2025-3962", "YW-2025-3971"],
        "rows": [
            {"business_id": "YW-2025-3962", "order_numbers": ["SO-251209-7214"]},
            {"business_id": "YW-2025-3971", "order_numbers": ["SO-251212-7259"]},
        ],
    }
    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "YW-2025-3962_SO-251212-7259.pdf", "fields": {}}],
        population,
    )
    assert accepted == []
    assert exceptions[0]["scope_status"] == "INDEX_CONFLICT"
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_sample_scope.py tests/test_sample_population_excel.py -q -p no:cacheprovider`

Expected: 当前范围解析器没有订单号别名和新冲突状态，新增断言失败。

- [ ] **Step 3: 让范围层消费联合索引决策**

`MATCHED` 文档写入：

```python
document["sample_business_id"] = decision["business_id"]
document["business_index_status"] = "MATCHED"
document["business_index_confidence"] = decision["confidence"]
document["business_index_evidence"] = decision["evidence"]
```

其他状态映射到异常区并保留检测值、候选业务、冲突原因和推荐动作。

- [ ] **Step 4: 工作台业务行附加联合索引**

`build_desk_chains` 按 `chain_id` 查找抽样行并附加：

```python
row["order_numbers"] = list(sample_row.get("order_numbers") or [])
row["display_index"] = " & ".join([cid, *row["order_numbers"]])
```

- [ ] **Step 5: 保持更换清单和现有任务重绑不调用 OCR**

复用 `replay_after_sample_replace`：重新执行范围归属、业务分组和序时账套账；不调用 `process_uploaded_files`。新增路由测试断言原 `ocr_source` 与识别字段保持不变。

- [ ] **Step 6: 运行测试确认 GREEN**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_sample_scope.py tests/test_sample_population_excel.py tests/test_business_alias_index.py -q -p no:cacheprovider`

Expected: 全部通过。

### Task 4: 联合索引与文件证据界面

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/types.ts`
- Modify: `web/src/components/BusinessWarehouseRow.tsx`
- Modify: `web/src/components/BusinessWarehouseRow.test.tsx`
- Create: `web/src/components/BusinessIndexEvidence.tsx`
- Create: `web/src/components/BusinessIndexEvidence.test.tsx`
- Modify: `web/src/pages/UploadPage.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- `ChainInfo.order_numbers?: string[]`
- `ChainInfo.display_index?: string`
- `ClassifiedDoc.business_index_evidence?: BusinessIndexEvidenceRow[]`

- [ ] **Step 1: 写工作台联合索引失败测试**

```tsx
it('shows business and order numbers as one readable index', () => {
  render(<BusinessWarehouseRow row={{ ...row, display_index: 'YW-2025-3962 & SO-251209-7214' }} {...props} />)
  expect(screen.getByText('联合索引：YW-2025-3962 & SO-251209-7214')).toBeInTheDocument()
})
```

- [ ] **Step 2: 写文件级证据失败测试**

```tsx
it('shows both matched aliases and their sources', async () => {
  render(<BusinessIndexEvidence document={documentWithTwoEvidenceRows} />)
  expect(screen.getByText('已归类：YW-2025-3962')).toBeInTheDocument()
  expect(screen.getByText('命中索引：业务编号 + 订单号')).toBeInTheDocument()
})
```

- [ ] **Step 3: 运行测试确认 RED**

Run: `node node_modules/vitest/vitest.mjs run src/components/BusinessWarehouseRow.test.tsx src/components/BusinessIndexEvidence.test.tsx`

Expected: 新属性和组件尚不存在，新增断言失败。

- [ ] **Step 4: 实现联合索引展示**

业务行在主业务编号下方显示次级文本；多个订单号只显示前两个并用 `<details>` 查看全部。文件证据组件使用 `<details>/<summary>` 展示检测值、清单值、来源和规范化方法，颜色仅作辅助，状态文字必须完整。

- [ ] **Step 5: 接入上传凭证列表并补齐类型**

在文件名下渲染：

```tsx
<BusinessIndexEvidence document={d} />
```

异常文件继续由现有异常区展示，不在正常已识别表中重复出现。

- [ ] **Step 6: 运行测试确认 GREEN**

Run: `node node_modules/vitest/vitest.mjs run src/components/BusinessWarehouseRow.test.tsx src/components/BusinessIndexEvidence.test.tsx src/pages/UploadPage.v2.test.tsx`

Expected: 全部通过。

### Task 5: 当前任务迁移与交付验收

**Files:**
- Verify: current job `5dd69ee5d6ef`

**Interfaces:**
- Consumes: 当前抽样清单、33 份已识别凭证、现有序时账
- Produces: 10 个业务联合索引和文件级归类证据

- [ ] **Step 1: 运行完整后端测试**

Run: `\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`

Expected: 无失败。

- [ ] **Step 2: 运行完整前端测试与构建**

Run: `node node_modules/vitest/vitest.mjs run`

Run: `node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build`

Expected: 无测试失败，生产构建成功。

- [ ] **Step 3: 对当前任务执行无 OCR 联合索引重绑**

重新导入已保存的 `sample_population.xlsx`，调用既有重放入口；检查：10 个业务保留对应订单号，33 份文件全部有 `sample_business_id`，原 `ocr_source` 和 OCR 字段不变。

- [ ] **Step 4: 核对真实归类结果**

验证以下三类：文件名含 YW+SO、仅含 YW、构造的仅含 SO 回归样本。当前真实任务应保持 10 笔业务和 33 份凭证，不新增业务，不产生静默冲突。

- [ ] **Step 5: 浏览器核验并记录状态**

检查总工作台业务行和上传凭证已识别列表的联合索引、展开证据与异常提示。按“已验证 / 带限制可交付 / 未验证”报告实际证据。
