# Uncertain Document Type Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“无法确定单据类型”与“人工声明混装资料包”彻底分离，并允许审计师把当前 `other` 文件命名为“海运提单”等具体名称，同时改善跨单据核对表的日期、购方和卖方展示。

**Architecture:** 保留现有固定 `doc_type` 枚举作为规则引擎入口，新增仅用于当前文件展示的 `custom_doc_type_name` 和人工确认状态；统一由前端展示函数决定名称。未知类型继续完成 OCR 后进入核对字段，只有人工明确声明的文件进入拆包流程。字段横向对照仍使用原字段键，只调整展示名称与参考字段覆盖范围。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、JSON job store、React 18、TypeScript、Vitest、Testing Library、pytest。

## Global Constraints

- 自定义名称只对当前文件生效，不进入全局字典，也不改变其他文件。
- 自定义文件底层必须保持 `doc_type="other"`，规则引擎不得把“海运提单”当成新的固定枚举。
- 保存自定义名称不得重新调用 OCR；原始文件、正文、页码和已抽取字段必须保留。
- “文件类型不确定”不得自动触发拆包；只有 `mixed_packet_declared=true` 才进入混装拆包。
- 旧数据中的 `other` 不自动猜名称、不重跑 OCR，显示为待人工确认。
- 核对字段中的中性日期名称为“文件日期”；“购方”和“卖方”同时作为核对参考。
- 不执行 Git 提交、切分支或重置；保留当前工作区全部既有改动。

---

### Task 1: 当前文件自定义类型元数据

**Files:**
- Modify: `src/api/workflow_router.py`
- Modify: `src/workflow/job_store.py`
- Modify: `web/src/api.ts`
- Modify: `web/src/types.ts`
- Test: `tests/test_document_custom_type.py`

**Interfaces:**
- Consumes: 现有 `PATCH /api/v1/workflow/jobs/{job_id}/documents/fields`。
- Produces: `PatchFieldsBody.custom_doc_type_name: Optional[str]`、`PatchFieldsBody.doc_type_confirmed: Optional[bool]`，以及 `JobStore.patch_document_fields(..., custom_doc_type_name=None, doc_type_confirmed=None)`。

- [ ] **Step 1: 写失败的后端测试**

```python
def test_patch_other_document_saves_current_file_custom_name_without_reocr(client, seeded_job):
    before = seeded_job["classified"][0]
    response = client.patch(
        f"/api/v1/workflow/jobs/{seeded_job['job_id']}/documents/fields",
        json={
            "file_name": before["file_name"],
            "fields": before["fields"],
            "doc_type": "other",
            "custom_doc_type_name": "海运提单",
            "doc_type_confirmed": True,
        },
    )
    saved = response.json()["classified"][0]
    assert saved["doc_type"] == "other"
    assert saved["custom_doc_type_name"] == "海运提单"
    assert saved["doc_type_confirmed"] is True
    assert saved["raw_text"] == before["raw_text"]
    assert saved["fields"] == before["fields"]
```

- [ ] **Step 2: 运行测试并确认因请求字段尚未落库而失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_document_custom_type.py -q`

Expected: FAIL，返回文档没有 `custom_doc_type_name` 或 `doc_type_confirmed`。

- [ ] **Step 3: 扩展 API 与 job store，严格校验当前文件名称**

```python
class PatchFieldsBody(BaseModel):
    file_name: str
    fields: dict[str, Any]
    doc_type: Optional[str] = None
    custom_doc_type_name: Optional[str] = None
    doc_type_confirmed: Optional[bool] = None
```

在 `patch_document_fields` 中：仅当 `doc_type == "other"` 时接受去首尾空格后的名称；确认 `other` 时名称为空则抛出 `ValueError("请填写当前文件的具体单据名称")`；切换到固定类型时清除旧的 `custom_doc_type_name`；只更新元数据和字段，不调用 `reclassify_document` 或 OCR。

- [ ] **Step 4: 扩展前端请求和类型定义**

```ts
export type ClassifiedDoc = {
  // existing properties
  custom_doc_type_name?: string
  doc_type_confirmed?: boolean
  type_uncertain?: boolean
}
```

`api.patchFields` 的 body 同步增加 `custom_doc_type_name?: string` 与 `doc_type_confirmed?: boolean`。

- [ ] **Step 5: 运行后端测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_document_custom_type.py -q`

Expected: PASS；固定类型清除旧自定义名、空名称被 400 拒绝、正文和字段未发生 OCR 重抽。

---

### Task 2: 未知类型与混装状态分离

**Files:**
- Modify: `src/api/workflow_router.py`
- Modify: `src/workflow/pipeline.py`
- Modify: `web/src/pages/UploadPage.tsx`
- Modify: `web/src/lib/workflowGuide.ts`
- Test: `tests/test_process_selected_files.py`
- Test: `web/src/pages/UploadPage.v2.test.tsx`

**Interfaces:**
- Consumes: `light_confident`、`doc_type_source`、`mixed_packet_declared`。
- Produces: 分类完成后的 `type_uncertain: boolean`；拆包判断仍只读取 `mixed_packet_declared === true`。

- [ ] **Step 1: 写失败测试，证明未知类型可以 OCR 且不会进入拆包**

```python
def test_unrecognized_pdf_is_uncertain_other_not_mixed(processed_unknown_pdf):
    assert processed_unknown_pdf["doc_type"] == "other"
    assert processed_unknown_pdf["type_uncertain"] is True
    assert processed_unknown_pdf.get("mixed_packet_declared") is not True
    assert processed_unknown_pdf["raw_text"]
```

前端测试断言未知类型提示为“文件类型不确定，疑似内部存在杂乱的文件类型”，页面仍允许“开始处理”；只有人工勾选“存在混装资料包”上传的文件显示“去拆包分笔”。

- [ ] **Step 2: 运行定向测试并确认旧提示或旧状态失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_process_selected_files.py -q`

Run: `cd web; npm test -- --run src/pages/UploadPage.v2.test.tsx`

- [ ] **Step 3: 在处理结果上写入明确的不确定状态**

在上传 pending 记录和 OCR 结果合并处使用：

```python
item["type_uncertain"] = bool(
    item.get("doc_type") == "other"
    and not item.get("doc_type_confirmed")
    and not item.get("mixed_packet_declared")
)
```

不得根据语言、文件页数、`doc_type == "other"` 或置信度自动写 `mixed_packet_declared`。

- [ ] **Step 4: 更新上传页文案与状态标签**

未知类型显示完整说明和“识别后到核对字段逐页确认类型”；人工混装保持独立标签“人工声明混装资料包”。普通未知文件不得出现“PDF 混装，可能需要拆包”。

- [ ] **Step 5: 运行定向测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_process_selected_files.py tests/test_packet_api.py -q`

Run: `cd web; npm test -- --run src/pages/UploadPage.v2.test.tsx`

Expected: 全部 PASS。

---

### Task 3: 核对字段中的当前文件自定义命名

**Files:**
- Create: `web/src/lib/documentTypeDisplay.ts`
- Create: `web/src/lib/documentTypeDisplay.test.ts`
- Modify: `web/src/pages/FieldConfirmPage.tsx`
- Modify: `web/src/pages/FieldConfirmPage.editing.test.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `ClassifiedDoc.custom_doc_type_name`、`doc_type`、`doc_type_confirmed`。
- Produces: `documentTypeLabel(doc: Pick<ClassifiedDoc, 'doc_type' | 'custom_doc_type_name'>): string`。

- [ ] **Step 1: 写展示函数和交互的失败测试**

```ts
expect(documentTypeLabel({ doc_type: 'other', custom_doc_type_name: '海运提单' })).toBe('海运提单')
expect(documentTypeLabel({ doc_type: 'other' })).toBe('其他（待确认）')
expect(documentTypeLabel({ doc_type: 'invoice', custom_doc_type_name: '错误旧值' })).toBe('发票')
```

组件测试：选择“其他”后出现“当前文件具体名称”输入框；空值不能保存并出现明确错误；填“海运提单”后 `patchFields` 只发送当前文件名、`doc_type: 'other'`、自定义名称和确认状态。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd web; npm test -- --run src/lib/documentTypeDisplay.test.ts src/pages/FieldConfirmPage.editing.test.tsx`

- [ ] **Step 3: 实现统一展示函数并替换核对页固定名称读取**

```ts
export function documentTypeLabel(doc: Pick<ClassifiedDoc, 'doc_type' | 'custom_doc_type_name'>): string {
  if (doc.doc_type === 'other') return doc.custom_doc_type_name?.trim() || '其他（待确认）'
  return TYPE_LABELS[doc.doc_type] || doc.doc_type || '未分类'
}
```

单据列表、折叠下拉框和原件预览标题都调用该函数。

- [ ] **Step 4: 增加仅当前文件的名称编辑器**

`docType === 'other'` 时显示输入框，标签为“当前文件具体名称”，提示“仅修改当前文件名称，不新增系统单据类型”。加载新文件时从 `custom_doc_type_name` 初始化；保存后当前文件立即显示该名称。类型或名称实际变化才触发未保存拦截。

- [ ] **Step 5: 运行交互测试**

Run: `cd web; npm test -- --run src/lib/documentTypeDisplay.test.ts src/pages/FieldConfirmPage.editing.test.tsx`

Expected: PASS。

---

### Task 4: 核对表的中性日期及购销双方参考字段

**Files:**
- Modify: `web/src/pages/FieldConfirmPage.tsx`
- Modify: `web/src/lib/fieldComparison.ts`
- Modify: `web/src/lib/fieldComparison.test.ts`
- Modify: `src/workflow/sample_required_fields.py`
- Test: `tests/test_sample_required_fields.py`

**Interfaces:**
- Consumes: 原字段键 `documentDate`、`acceptanceDate`、`buyerName`、`supplierName`。
- Produces: UI 标签“文件日期”“购方”“卖方”；规则仍使用原字段键。

- [ ] **Step 1: 写失败测试**

前端测试断言：`acceptanceDate` 的对照行标签为“文件日期”；`buyerName` 和 `supplierName` 均出现在订单、发票及已有值所在单据的参考行；双方名称不因某份单据缺值被错误判定为必然不一致。

- [ ] **Step 2: 运行测试并确认旧标签或缺少卖方而失败**

Run: `cd web; npm test -- --run src/lib/fieldComparison.test.ts`

- [ ] **Step 3: 修改展示标签和参考字段清单**

将 `acceptanceDate` 的展示标签改为“文件日期”，`supplierName` 改为“卖方”，`buyerName` 保持“购方”。在 `SYSTEM_REQUIRED`、`GOSPD01030_BY_TYPE` 和后端同源必需字段生成中，为合同、订单、签收/验收、发票补齐适用的购方/卖方来源；它们是参考核对字段，缺失只显示“待补充/未提取”，不能伪造值。

- [ ] **Step 4: 保持日期字段语义不变**

截止性规则继续优先使用 `acceptanceDate` 作为控制权转移依据；本任务只改变核对表和编辑器标签，不把发票日期、签收日或入账日合并成同一个后端字段。

- [ ] **Step 5: 运行前后端定向测试**

Run: `cd web; npm test -- --run src/lib/fieldComparison.test.ts`

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sample_required_fields.py -q`

Expected: PASS。

---

### Task 5: 兼容旧数据并验证完整流程

**Files:**
- Modify: `docs/superpowers/specs/2026-08-24-uncertain-document-type-review-design.md`（仅在实现与规格存在字段名差异时同步）
- Test: `tests/test_document_custom_type.py`
- Test: `web/src/pages/FieldConfirmPage.editing.test.tsx`
- Test: `web/src/pages/UploadPage.v2.test.tsx`

**Interfaces:**
- Consumes: Tasks 1–4 的 API、类型和展示函数。
- Produces: 可验收的未知类型 → OCR → 核对字段 → 当前文件命名流程。

- [ ] **Step 1: 增加旧数据兼容断言**

旧 `other` 文档缺少新字段时必须正常加载为“其他（待确认）”；保存“海运提单”后刷新仍保持；同一任务中另一份 `other` 仍是“其他（待确认）”。

- [ ] **Step 2: 运行后端完整测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests -q`

Expected: 退出码 0；若有环境依赖型跳过，记录具体数量和原因。

- [ ] **Step 3: 运行前端完整测试与构建**

Run: `cd web; npm test -- --run`

Run: `cd web; npm run build`

Expected: 测试和 TypeScript/Vite 构建退出码均为 0。

- [ ] **Step 4: 浏览器人工验收**

在 `http://127.0.0.1:5173/` 依次检查：未知外文 PDF 不进入拆包；OCR 后核对字段显示完整不确定提示；“其他”可命名“海运提单”；保存后只有当前文件名称改变；原件预览和全部页仍可翻阅；对照表显示“文件日期、购方、卖方”。

- [ ] **Step 5: 检查没有 Git 写操作和敏感信息泄漏**

Run: `git status --short`

Expected: 仅报告工作区现状；不执行 `git add`、`git commit`、`git reset` 或 `git checkout`。确认 `.env` 仍被忽略，测试日志和文档不包含 OCR 密钥。
