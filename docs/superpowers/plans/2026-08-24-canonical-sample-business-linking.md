# Canonical Sample Business Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让抽样清单业务编号成为凭证归属、业务分组和序时账匹配的统一主键，并为每个匹配结果提供可核查解释。

**Architecture:** 在 `sample_scope.py` 中以当前抽样清单为词典解析并持久化 `sample_business_id`，由业务分组和序时账匹配直接消费该字段。处理流水线先完成抽样范围归属，再执行序时账匹配；前端用独立状态组件呈现查询双方与失败原因。

**Tech Stack:** Python 3、FastAPI、pandas、pytest、React、TypeScript、Vitest、Testing Library。

## Global Constraints

- 抽样清单业务编号是唯一审计业务主键。
- 订单号、发票号、合同号只参与业务内部勾稽，不覆盖抽样主键。
- 无法唯一归属的文件进入异常区，不得新建业务。
- 不显示裸露的“账未匹配”或把所有失败写成“订单号不存在”。
- 保持一对多三单匹配能力。
- 不执行 Git add、commit、checkout、reset 或其他 Git 写操作。

---

### Task 1: 抽样业务主键解析与范围约束

**Files:**
- Modify: `src/workflow/sample_scope.py`
- Test: `tests/test_sample_scope.py`

**Interfaces:**
- Produces: `resolve_sample_business_identity(document, sample_population) -> dict[str, Any]`
- Persists: `sample_business_id`、`business_index_source`、`business_index_candidates`

- [ ] **Step 1: 写失败测试**

增加真实编号用例：抽样清单为 `YW-2025-3962`，文件名为 `YW-2025-3962_发票_FP-260102-8305.pdf`，OCR 字段含不同的 `orderNo=SO-251209-7214`；断言文件归入 `YW-2025-3962` 且来源为 `filename`。

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest tests/test_sample_scope.py -q`

Expected: 旧编号形态校验拒绝 `YW-2025-3962`，新增断言失败。

- [ ] **Step 3: 实现最小主键解析器**

抽样总体值只做规范化和去重，不再调用旧 `looks_like_biz_id`；先匹配人工归属，再把抽样清单实际值与文件名做带边界完整匹配，最后读取明确业务主键字段。唯一命中时在文档副本中写入三个审计字段，多命中或无命中时返回结构化状态。

- [ ] **Step 4: 增加异常边界测试并实现**

覆盖 `YW-2025-9999` 进入 `OUT_OF_SAMPLE`、文件同时出现两个清单内 YW 编号进入 `AMBIGUOUS`、仅有 SO 订单号但没有 YW 归属进入 `UNASSIGNED`。

- [ ] **Step 5: 运行测试确认 GREEN**

Run: `python -m pytest tests/test_sample_scope.py -q`

Expected: 全部通过。

### Task 2: 业务分组与序时账统一消费主键

**Files:**
- Modify: `src/workflow/business_grouping.py`
- Modify: `src/legacy_ocr/ledger_parser.py`
- Modify: `src/workflow/pipeline.py`
- Modify: `src/api/workflow_router.py`
- Test: `tests/test_business_grouping.py`
- Test: `tests/test_ledger_parser.py`

**Interfaces:**
- Consumes: `document["sample_business_id"]`
- Produces: `ledger_query_biz_id`、`ledger_index_column`、`ledger_match_reason`

- [ ] **Step 1: 写业务分组失败测试**

两份分别带 `SO` 与发票号、但共享 `sample_business_id=YW-2025-3962` 的单据必须组成同一条 `YW-2025-3962` 业务链。

- [ ] **Step 2: 写序时账失败测试**

序时账主键为 `YW-2025-3962`，凭证同时带 `sample_business_id=YW-2025-3962` 和 `orderNo=SO-251209-7214`；断言只用 YW 查询并匹配成功。另断言失败输出包含凭证索引值、索引来源、序时账列和原因。

- [ ] **Step 3: 运行测试确认 RED**

Run: `python -m pytest tests/test_business_grouping.py tests/test_ledger_parser.py -q`

Expected: 旧分组忽略 `sample_business_id`，旧序时账查询优先单据编号，新增断言失败。

- [ ] **Step 4: 实现统一消费逻辑**

业务分组在人工业务框之后优先按 `sample_business_id` 建组。序时账存在该字段时只查询它；移除给所有发票附加全局订单候选的行为。序时账索引条目保留映射列名，并为成功、未找到和未取得主键生成结构化结果。

- [ ] **Step 5: 调整流水线顺序与重绑入口**

OCR 结束后先调用抽样范围解析，再对接受的凭证套用序时账；上传/重传序时账、手工应用映射、重分类和更换抽样清单后的重放均保持这一顺序。

- [ ] **Step 6: 运行测试确认 GREEN**

Run: `python -m pytest tests/test_business_grouping.py tests/test_ledger_parser.py tests/test_sample_scope.py -q`

Expected: 全部通过。

### Task 3: 上传凭证页的可解释匹配状态

**Files:**
- Create: `web/src/components/LedgerMatchStatus.tsx`
- Create: `web/src/components/LedgerMatchStatus.test.tsx`
- Modify: `web/src/pages/UploadPage.tsx`
- Modify: `web/src/types.ts`

**Interfaces:**
- Consumes: `ClassifiedDoc` 的主键与序时账解释字段
- Produces: 成功徽标或可展开的未匹配详情

- [ ] **Step 1: 写组件失败测试**

成功时展示 `已匹配序时账 · YW-2025-3962`；失败时摘要展示 `未匹配：业务编号 YW-2025-3962`，展开内容展示“凭证索引来源：文件名”“序时账索引列：business_id”“实际查询值”和具体失败原因；无主键时展示“无法关联：未取得抽样业务编号”。

- [ ] **Step 2: 运行测试确认 RED**

Run: `npm test -- --run src/components/LedgerMatchStatus.test.tsx`

Expected: 组件尚不存在，测试失败。

- [ ] **Step 3: 实现并接入组件**

使用原有语义色与 `<details>/<summary>`，不依赖颜色单独表达状态；替换 `UploadPage` 中的内联状态分支，并补齐 `ClassifiedDoc` 类型。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `npm test -- --run src/components/LedgerMatchStatus.test.tsx src/pages/UploadPage.v2.test.tsx`

Expected: 全部通过。

### Task 4: 当前任务重绑与交付验收

**Files:**
- Verify only: current job `5dd69ee5d6ef`

**Interfaces:**
- Consumes: 现有抽样清单、33 份已识别凭证和序时账
- Produces: 10 条抽样业务链及可解释的序时账状态

- [ ] **Step 1: 运行后端与前端定向回归**

Run: `python -m pytest tests/test_sample_scope.py tests/test_business_grouping.py tests/test_ledger_parser.py -q`

Run: `npm test -- --run src/components/LedgerMatchStatus.test.tsx src/pages/UploadPage.v2.test.tsx`

- [ ] **Step 2: 运行构建**

Run: `npm run build`

Expected: TypeScript 与 Vite 构建成功。

- [ ] **Step 3: 对当前任务执行无 OCR 重绑**

重新应用当前抽样范围与序时账映射，不重新扫描图片；断言 33 份凭证均带清单内 `sample_business_id`，10 条工作台业务链不再全部为 0 份。

- [ ] **Step 4: 浏览器核验**

打开上传凭证与总工作台，确认 YW 归属、业务数量、匹配状态和未匹配详情均符合设计。

- [ ] **Step 5: 记录交付状态**

按“已验证 / 带限制可交付 / 未验证”报告实际测试、构建、当前任务重绑和视觉检查结果。
