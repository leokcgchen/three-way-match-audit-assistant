# Human-AI Document Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在销售截止审计工作台中交付“人工确认业务归属、AI 建议类型、多人眼批量确认拆包”的文件接收流程，并确保多页 PDF、连续页合并和单据—业务多对多关系在进入 OCR 前经过服务端门禁。

**Architecture:** 保留现有 `SampleWorkbenchPage → PacketUnpackPage → packet_engine → OCR` 主链路。样本行上传只增加业务预选提示；`PacketUnit.business_ids` 成为权威关系，旧 `chain_id` 保留为首个业务的兼容镜像；拆包页拆出纯函数和小组件，以“文件联络表 + 单据属性面板”替代单文件大预览主导的布局。所有关键约束同时由前端提示和后端拒绝保证，不能只依赖 UI。

**Tech Stack:** React 19、TypeScript 6、Vite 8、Vitest、React Testing Library、FastAPI、Pydantic、pytest、pypdf、现有 HITL 审计日志。

## Global Constraints

- 只修改本计划列出的文件；仓库中现有未提交内容属于用户基线，不整理、不覆盖、不顺手重构。
- 首版只覆盖 GOSPD01030 销售截止主路径；不扩展到其他审计程序。
- 原始文件只读保存；多业务关系不能通过复制文件表达。
- 页是最小物理拆分单位；首版不做页内区域或明细行裁切。
- 所有多页 PDF 都必须出现明确的人工边界确认；单页 PDF 和图片不得被迫进入无意义的拆包步骤。
- 每个任务严格按 RED → GREEN → REFACTOR 执行；测试第一次失败时确认是预期缺失功能，而不是环境或导入错误。
- 每个任务只提交本任务触及的文件，逐项写出文件路径执行 `git add`，绝不执行 `git add .`。

---

## Task 1: 建立 `PacketUnit` 多业务兼容模型与纯校验函数

**Files:**

- Create: `src/workflow/packet_relations.py`
- Create: `tests/test_packet_relations.py`
- Modify: `src/workflow/packet_engine.py:154-176,310-371,374-520`
- Modify: `src/api/workflow_router.py:98-122,1526-1563`
- Modify: `web/src/types.ts:82-100`

- [ ] **Step 1: 写兼容关系与确认门禁的失败测试**

在 `tests/test_packet_relations.py` 写纯函数测试，至少覆盖：

```python
def test_normalize_business_ids_reads_legacy_chain_id():
    assert normalize_business_ids({"chain_id": "SO25-0281"}) == ["SO25-0281"]


def test_normalize_business_ids_deduplicates_and_preserves_order():
    unit = {"business_ids": ["SO25-0281", "SO25-0282", "SO25-0281"]}
    assert normalize_business_ids(unit) == ["SO25-0281", "SO25-0282"]


def test_validate_units_allows_one_physical_unit_linked_to_two_businesses():
    units = [{
        "unit_id": "u1", "source_file": "packet.pdf", "pages": [1, 2],
        "doc_type": "签收验收", "business_ids": ["SO25-0281", "SO25-0282"],
        "boundary_confirmed": True, "dropped": False,
    }]
    validate_confirmable_units(units, multi_page_files={"packet.pdf"}, start_ocr=True)


@pytest.mark.parametrize("field", ["business_ids", "boundary_confirmed", "doc_type"])
def test_validate_units_rejects_missing_ocr_gate(field):
    # 分别验证未归属、未确认多页边界、未识别类型在 start_ocr=True 时被拒绝。
```

另加一条测试证明 `start_ocr=False` 可保存尚未解决的类型，但不可启动 OCR。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_packet_relations.py -q
```

Expected: 因 `src.workflow.packet_relations` 尚不存在而失败。

- [ ] **Step 3: 实现最小纯函数模块**

在 `src/workflow/packet_relations.py` 实现：

```python
UNIDENTIFIED_BUSINESS_IDS = {"", "未识别业务号", "unidentified", "unresolved"}


def normalize_business_ids(unit: Mapping[str, Any]) -> list[str]:
    """优先读取 business_ids；仅在新字段缺失时读取旧 chain_id。"""


def with_business_ids(unit: Mapping[str, Any], business_ids: Iterable[str]) -> dict[str, Any]:
    """返回带去重 business_ids 和首个 chain_id 兼容镜像的新字典。"""


def validate_confirmable_units(
    units: Sequence[Mapping[str, Any]],
    *,
    multi_page_files: set[str],
    start_ocr: bool,
) -> None:
    """校验非废页归属、多页边界确认，以及启动 OCR 时的有效类型。"""
```

异常使用 `ValueError`，消息必须包含文件名/单元 ID 和失败原因，便于路由转换为 400 响应。

- [ ] **Step 4: 将兼容模型接入 packet engine 与 API schema**

在 `PacketUnitEdit` 增加：

```python
business_ids: list[str] = Field(default_factory=list)
suggested_doc_type: str = ""
doc_type_source: Literal["ai", "human"] = "ai"
boundary_confirmed: bool = False
business_binding_source: Literal["human"] | None = None
drop_reason: str = ""
```

保持 `chain_id: str = ""` 可选兼容。在 `_unit_to_dict`、`apply_unit_edits` 和 `materialize_units` 中调用 `with_business_ids`：

- 新逻辑始终使用 `business_ids`；
- `chain_id` 只保存首个业务；
- `source_packet` 同时写入 `business_ids`、`chain_id`、AI 建议、人工确认元数据；
- 派生文件仍只生成一次，文件名使用首个业务或 `multi-business`，不得按业务复制；
- `confirm_packet` 增加向后兼容的关键字参数 `start_ocr: bool = False`，在物理页覆盖检查后调用 `validate_confirmable_units`；路由必须显式传入 `body.start_ocr`。

路由捕获 `ValueError` 并返回 `HTTPException(status_code=400, detail=str(exc))`。

在 `web/src/types.ts` 为 `PacketUnit` 增加同名可选字段，并保留 `chain_id`。同时把 `PacketRun.files` 当前的匿名对象提取为导出的 `PacketFile` 类型，供后续联络表和纯函数共同引用：

```ts
export type PacketFile = {
  file_name: string
  path?: string
  kind?: string
  page_count?: number
  sha256?: string
}
```

- [ ] **Step 5: 运行目标测试和现有 packet 回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_packet_relations.py tests/test_packet_split.py tests/test_packet_api.py -q
```

Expected: 全部通过；旧的单 `chain_id` 请求仍可确认。

- [ ] **Step 6: 提交 Task 1**

```powershell
git add src/workflow/packet_relations.py src/workflow/packet_engine.py src/api/workflow_router.py web/src/types.ts tests/test_packet_relations.py
git commit -m "feat: support multi-business packet units"
```

---

## Task 2: 给行级上传增加“业务预选提示”，不把提示伪装成人工结论

**Files:**

- Create: `tests/test_business_hint_upload.py`
- Modify: `src/api/workflow_router.py:1272-1368`
- Modify: `src/workflow/packet_engine.py:130-280`
- Modify: `web/src/api.ts:336-344`
- Modify: `web/src/types.ts:206-217`

- [ ] **Step 1: 写上传提示的 API 失败测试**

在 `tests/test_business_hint_upload.py` 用现有 `TestClient`/job fixture 测试：

1. `business_hints='{"two-page.pdf":["SO25-0281"]}'` 被保存到对应 `pending_files[].declared_business_ids`；
2. 不合法 JSON 返回 400；
3. 不在当前抽样业务集合中的业务号返回 400，不静默创建新业务；
4. packet analyze 生成的单元带预选 `business_ids`，但 `business_binding_source` 为空、`boundary_confirmed=False`；
5. 没有 `business_hints` 的旧上传请求保持原行为。

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_business_hint_upload.py -q
```

Expected: 上传接口不识别 `business_hints` 或响应中没有 `declared_business_ids`。

- [ ] **Step 3: 扩展上传表单与 pending 元数据**

在 `upload_documents` 增加：

```python
business_hints: str = Form("")
```

解析为 `dict[str, list[str]]`，逐项去重，并与当前 job 的抽样业务 ID 集合比对。每个 `pending_files` 记录增加：

```python
"declared_business_ids": hints.get(filename, []),
"upload_source": "business_row" if hints.get(filename) else "mixed_packet",
```

调用 `append_hitl_event` 记录 `business_row_upload_hint`，payload 只记录文件名、业务号和数量，不记录文件内容。

- [ ] **Step 4: 在分析结果中保留预选但不越权确认**

`analyze_pending_packets` 根据源文件名读取 `declared_business_ids`，写入每个草稿单元：

```python
"business_ids": declared_ids,
"chain_id": declared_ids[0] if declared_ids else UNIDENTIFIED_CHAIN,
"business_binding_source": None,
"boundary_confirmed": False if page_count > 1 else True,
```

注意：单页文件可视为物理边界天然确认，但业务提示仍须由用户在最终提交前确认；前端会把其显示为“预选”。

- [ ] **Step 5: 扩展前端 API 类型**

将 `api.upload` 选项改为：

```ts
opts?: {
  force?: boolean
  process?: boolean
  businessHints?: Record<string, string[]>
}
```

有值时：

```ts
fd.append('business_hints', JSON.stringify(opts.businessHints))
```

在 pending file 类型增加 `declared_business_ids?: string[]` 与 `upload_source?: 'business_row' | 'mixed_packet'`。

- [ ] **Step 6: 运行上传与 packet 回归并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_business_hint_upload.py tests/test_process_upload_append.py tests/test_packet_api.py -q
git add src/api/workflow_router.py src/workflow/packet_engine.py web/src/api.ts web/src/types.ts tests/test_business_hint_upload.py
git commit -m "feat: preserve business context on upload"
```

---

## Task 3: 建立前端测试基座并抽出拆包状态纯函数

**Files:**

- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Create: `web/vitest.config.ts`
- Create: `web/src/test/setup.ts`
- Create: `web/src/lib/documentIntake.ts`
- Create: `web/src/lib/documentIntake.test.ts`
- Modify: `web/src/pages/PacketUnpackPage.tsx:23-70,139-228,253-430`

- [ ] **Step 1: 安装最小测试依赖**

在 `web` 目录执行：

```powershell
npm install --save-dev vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

给 `package.json` 增加：

```json
"test": "vitest run",
"test:watch": "vitest"
```

`web/vitest.config.ts` 使用 `jsdom`，并加载 `src/test/setup.ts` 中的 `@testing-library/jest-dom/vitest`。

- [ ] **Step 2: 写状态纯函数的失败测试**

`documentIntake.test.ts` 至少覆盖：

- `businessIdsForUnit` 兼容新 `business_ids` 与旧 `chain_id`；
- `reviewSummary` 汇总总页数、待确认、未归属、异常；
- `confirmNormalUnits` 只确认无异常且已归属的多页单元；
- `splitUnitAtPage` 保证前后页连续且两个新单元都标记为人工边界确认；
- `mergeUnitWithPrevious` 仅合并同一源文件的相邻连续页；
- `canStartOcr` 明确列出未解决原因，而不是只返回布尔值。

- [ ] **Step 3: 运行前端测试并确认 RED**

```powershell
npm test -- --run src/lib/documentIntake.test.ts
```

Expected: 因 `documentIntake.ts` 不存在或函数未实现而失败。

- [ ] **Step 4: 实现纯函数并替换页面内联逻辑**

导出如下接口：

```ts
export type IntakeBlocker = {
  unitId: string
  sourceFile: string
  code: 'unassigned' | 'boundary_unconfirmed' | 'type_unresolved' | 'needs_review'
  message: string
}

export function businessIdsForUnit(unit: PacketUnit): string[]
export function reviewSummary(units: PacketUnit[], files: PacketFile[]): ReviewSummary
export function confirmNormalUnits(units: PacketUnit[]): PacketUnit[]
export function splitUnitAtPage(units: PacketUnit[], unitId: string, page: number): PacketUnit[]
export function mergeUnitWithPrevious(units: PacketUnit[], unitId: string): PacketUnit[]
export function intakeBlockers(units: PacketUnit[], files: PacketFile[]): IntakeBlocker[]
```

`PacketUnpackPage` 调用这些函数，删除重复的页拆分/合并判断，但暂不改整体布局。

- [ ] **Step 5: 运行测试、类型检查和构建**

```powershell
npm test
npm run lint
npm run build
```

Expected: 测试、lint、TypeScript 构建全部通过。

- [ ] **Step 6: 提交 Task 3**

```powershell
git add web/package.json web/package-lock.json web/vitest.config.ts web/src/test/setup.ts web/src/lib/documentIntake.ts web/src/lib/documentIntake.test.ts web/src/pages/PacketUnpackPage.tsx
git commit -m "test: add document intake state harness"
```

---

## Task 4: 把样本行改成真正的业务仓库上传目标

**Files:**

- Create: `web/src/components/BusinessWarehouseRow.tsx`
- Create: `web/src/components/BusinessWarehouseRow.test.tsx`
- Modify: `web/src/components/SampleDeskList.tsx:1-15,120-220,340-400`
- Modify: `web/src/pages/SampleWorkbenchPage.tsx:1-50,300-380,657-665`
- Modify: `web/src/styles.css:2564-2620,2784-2825`

- [ ] **Step 1: 写上传按钮、拖放和可访问性的失败测试**

在组件测试中验证：

```tsx
it('opens the hidden picker from 请上传 and reports selected files', async () => {})
it('accepts dropped files and preserves the target business id', async () => {})
it('does not open the business when the upload action is clicked', async () => {})
it('supports Enter to open the business and a separate keyboard upload control', async () => {})
it('shows an in-row busy or error state without moving focus', async () => {})
```

测试必须断言 `onUpload(row, files)` 收到目标行，而不只是断言 DOM 变化。

- [ ] **Step 2: 运行组件测试并确认 RED**

```powershell
npm test -- --run src/components/BusinessWarehouseRow.test.tsx
```

Expected: 组件尚不存在。

- [ ] **Step 3: 实现独立行组件，避免嵌套按钮**

当前整行是 `<button>`，不能在其中合法嵌套“请上传”。改为：

```tsx
<li
  className={rowClass}
  onDragEnter={handleDragEnter}
  onDragOver={handleDragOver}
  onDragLeave={handleDragLeave}
  onDrop={handleDrop}
>
  <button type="button" className="business-warehouse-main" onClick={() => onOpen(row)}>
    {/* 状态、摘要、槽位与已关联单据 */}
  </button>
  <div className="business-warehouse-upload">
    <input ref={inputRef} type="file" multiple accept={ACCEPTED_EVIDENCE} hidden />
    <button type="button" onClick={() => inputRef.current?.click()}>请上传</button>
  </div>
</li>
```

拖入时显示“将 N 个文件关联到业务 {chain_id}”；`dragenter/dragleave` 使用计数器避免经过子元素时闪烁。上传按钮、主行按钮均有明确 `aria-label` 和可见焦点。

- [ ] **Step 4: 接入样本工作台上传处理**

`SampleDeskList` props 增加：

```ts
onUpload: (row: ChainInfo, files: File[]) => Promise<void>
uploadingId?: string | null
uploadErrorById?: Record<string, string>
```

`SampleWorkbenchPage` 的 handler：

1. 构造 `{ [file.name]: [row.chain_id] }`；
2. 调用 `api.upload(job.job_id, files, { process: false, businessHints })`；
3. 更新 job；
4. 若响应存在多页 packet 且需要确认，进入现有 `packet_unpack` 阶段；
5. 否则沿用单页文件的轻分类/识别流程；
6. 错误保留在目标行，不清空其他行状态。

页面顶部增加“上传混装资料包”入口，调用同一方法但不传 `businessHints`。

- [ ] **Step 5: 实现紧凑纵向仓库样式**

在现有 `.desk-sample-row` 基础上增加固定右侧操作列，保证：

- 1366px 宽时业务摘要与上传按钮仍在一行；
- 行高保持可扫视，已完成项视觉降噪；
- 拖放目标有边框、图标和文字三重提示；
- 上传中不改变行高；
- `:focus-visible` 清晰；
- 触控目标不小于 40px。

- [ ] **Step 6: 运行前端测试与构建并提交**

```powershell
npm test -- --run src/components/BusinessWarehouseRow.test.tsx
npm run lint
npm run build
git add web/src/components/BusinessWarehouseRow.tsx web/src/components/BusinessWarehouseRow.test.tsx web/src/components/SampleDeskList.tsx web/src/pages/SampleWorkbenchPage.tsx web/src/styles.css
git commit -m "feat: add business warehouse uploads"
```

---

## Task 5: 将拆包页升级为多 PDF 联络表和批量人工确认工作台

**Files:**

- Create: `web/src/components/PacketContactSheet.tsx`
- Create: `web/src/components/PacketContactSheet.test.tsx`
- Create: `web/src/components/PacketInspector.tsx`
- Create: `web/src/components/PacketInspector.test.tsx`
- Modify: `web/src/pages/PacketUnpackPage.tsx:72-720`
- Modify: `web/src/api.ts:370-395`
- Modify: `web/src/styles.css:3779-4010`

- [ ] **Step 1: 写联络表与属性面板的失败测试**

`PacketContactSheet.test.tsx` 覆盖：

- 同屏渲染两份 PDF 的页缩略图与明确文件分组；
- 单据边界以分组带、页码和标签表达，不只靠颜色；
- 点击页选择单元，Shift 选择连续页，Ctrl/Meta 多选单元；
- “从本页拆开”“并入上一张”“去掉/恢复空白页”调用正确回调；
- 批量确认只更新无异常项；
- 缩略图失败时显示页码占位和“打开原件”。

`PacketInspector.test.tsx` 覆盖：

- AI 类型建议与当前有效类型同时可见；
- 人工改类型后 `doc_type_source='human'`；
- 业务使用复选框多选，一份单据可勾两笔业务；
- 业务搜索无结果时不允许静默创建未知业务；
- 人工改业务后 `business_binding_source='human'`。

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
npm test -- --run src/components/PacketContactSheet.test.tsx src/components/PacketInspector.test.tsx
```

Expected: 两个组件尚不存在。

- [ ] **Step 3: 实现组件边界**

`PacketContactSheet` 只负责文件/页/单元的批量可视化与选择，不直接调用 API：

```ts
type PacketContactSheetProps = {
  files: PacketFile[]
  units: PacketUnit[]
  selectedUnitIds: string[]
  thumbnails: Record<string, string>
  onSelectionChange(ids: string[]): void
  onSplit(unitId: string, page: number): void
  onMerge(unitId: string): void
  onDropPage(sourceFile: string, page: number): void
  onRestoreUnit(unitId: string): void
}
```

`PacketInspector` 负责选中单元的类型与多业务关系，批量编辑时只改共同选择字段。

- [ ] **Step 4: 重组 `PacketUnpackPage` 布局和状态**

页面改成：

1. 顶部固定工具栏：文件、页、建议单据、待确认、未归属、异常计数及“批量确认无异常项”；
2. 左侧文件导航：文件名、页数、文件状态，点击滚动到对应联络表分组；
3. 中间联络表：默认显示所有 packet 文件，而不是一次只看一个；
4. 右侧固定 `PacketInspector`：类型、建议、业务搜索和复选框；
5. 底部固定门禁栏：逐类 blocker 数量和“确认并开始识别”。

保留原大图预览，但改为选中页后的按需查看，不占默认主区域。

“批量确认无异常项”只将满足以下条件的单元设为 `boundary_confirmed=true`：有业务预选/人工归属、类型非空、`needs_review=false`、非 dropped。异常项维持突出显示。

- [ ] **Step 5: 扩展确认请求并显示服务端门禁错误**

`api.packetConfirm` 的 unit body 增加：

```ts
business_ids: string[]
suggested_doc_type?: string
doc_type_source?: 'ai' | 'human'
boundary_confirmed: boolean
business_binding_source?: 'human'
drop_reason?: string
```

提交时仍发送 `chain_id: business_ids[0] || unit.chain_id` 供旧后端兼容。`start_ocr=true` 前先用 `intakeBlockers` 禁用按钮并列出具体阻断项；即使前端绕过，Task 1 的后端仍会拒绝。

提交成功时用 `append_hitl_event` 的 packet confirm 事件补充 counts：`manual_boundary_changes`、`manual_type_overrides`、`business_link_changes`、`batch_confirmed_units`。

- [ ] **Step 6: 实现人体工学与响应式样式**

CSS 验收点：

- 1920px 宽：左 220–260px、中间自适应、右 300–360px；
- 1366px 宽：左栏可收起，右栏不遮挡联络表；
- 缩略图使用 `repeat(auto-fill, minmax(150px, 1fr))`，但最大宽度受限，避免过大；
- 顶部和底部工具条 sticky，滚动只发生在主内容区；
- 正常项使用中性边框，待确认/异常使用文字、图标与高对比边框；
- 多业务关联显示“关联 2 笔”等文字标识；
- `prefers-reduced-motion` 下关闭非必要过渡。

- [ ] **Step 7: 运行前端测试、lint、构建并提交**

```powershell
npm test
npm run lint
npm run build
git add web/src/components/PacketContactSheet.tsx web/src/components/PacketContactSheet.test.tsx web/src/components/PacketInspector.tsx web/src/components/PacketInspector.test.tsx web/src/pages/PacketUnpackPage.tsx web/src/api.ts web/src/styles.css
git commit -m "feat: add batch packet review workbench"
```

---

## Task 6: 完成 API 集成、审计追踪与端到端回归

**Files:**

- Modify: `tests/test_packet_api.py`
- Modify: `tests/test_process_upload_append.py`
- Create: `tests/test_document_intake_workflow.py`
- Modify: `src/api/workflow_router.py:1272-1368,1526-1563`
- Modify: `src/workflow/packet_engine.py:179-280,310-520`

- [ ] **Step 1: 写完整主路径的失败测试**

`tests/test_document_intake_workflow.py` 构造一个两页 PDF，并走真实 API：

1. 创建 job 和两笔抽样业务；
2. 以第一笔业务提示上传两页 PDF；
3. packet analyze 返回 `boundary_confirmed=False`；
4. `start_ocr=True` 且未确认时返回 400；
5. 将两页合为一个单据，人工确认边界，关联两笔业务，覆盖类型；
6. packet confirm 只生成一个派生文件；
7. 派生记录的 `source_packet.business_ids` 有两笔业务，`chain_id` 是首个兼容镜像；
8. 审计日志包含上传提示、边界确认、类型覆盖、多业务关联和最终提交；
9. 页数守恒，无遗漏、无物理重复。

另加回归测试：单页 PDF/图片继续跳过拆包确认；旧 `chain_id` payload 继续工作。

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_document_intake_workflow.py -q
```

Expected: 至少在多业务持久化、门禁或审计事件断言处失败。

- [ ] **Step 3: 补齐主链路最小实现**

只修复集成测试暴露的断点：

- 路由将业务提示传入 packet analysis；
- `confirm_packet` 只物化一次多业务单据；
- `start_ocr` 仅在所有服务端门禁通过后执行；
- packet confirm 的 HITL 事件包含前后关系和批量计数；
- 错误消息使用稳定中文文案，前端可以直接展示。

不得为通过测试而放宽页覆盖或未知业务校验。

- [ ] **Step 4: 运行后端目标回归**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_document_intake_workflow.py tests/test_packet_relations.py tests/test_business_hint_upload.py tests/test_packet_api.py tests/test_packet_split.py tests/test_process_upload_append.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 运行完整自动化验收**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Set-Location web
npm test
npm run lint
npm run build
```

Expected: pytest、Vitest、lint、生产构建全部退出码 0。若完整 pytest 存在与本功能无关的基线失败，记录测试名和证据，不修改无关模块；本计划的目标测试必须全绿。

- [ ] **Step 6: 执行浏览器关键路径与视觉 QA**

启动现有服务：

```powershell
Set-Location D:\抽凭—合同合规性审阅agent
.\start_workbench.bat
```

在 `http://127.0.0.1:5173` 使用真实两页测试 PDF 逐项检查：

- 业务行拖入和“请上传”均把文件预选到正确业务；
- 多页 PDF 自动进入拆包工作台；
- 两份 PDF 可同屏扫视，正常项可一次批量确认；
- 拆开、合并、空白页去除/恢复、类型覆盖、多业务勾选可用；
- 未确认/未归属/未知类型时 OCR 按钮被阻断，服务端也返回 400；
- 确认后只生成一份多业务派生单据；
- 在 1366×768、1440×900、1920×1080 检查无水平溢出、sticky 工具条不遮挡内容、焦点可见；
- 仅用键盘完成打开业务、上传、选择单元、修改类型和最终确认。

把发现的问题按“功能阻断 / 可访问性 / 视觉细节”分类；功能阻断和可访问性问题必须修复并重跑对应测试，视觉细节至少达到规格验收标准后才可交付。

- [ ] **Step 7: 提交集成与 QA 修复**

```powershell
git add tests/test_document_intake_workflow.py tests/test_packet_api.py tests/test_process_upload_append.py src/api/workflow_router.py src/workflow/packet_engine.py
git commit -m "test: cover document intake workflow"
```

若视觉 QA 产生代码修复，先用 `git status --short` 核对范围，再只暂存实际修改过的下列 UI 文件并另作一个提交：

```powershell
git add web/src/components/BusinessWarehouseRow.tsx web/src/components/PacketContactSheet.tsx web/src/components/PacketInspector.tsx web/src/pages/PacketUnpackPage.tsx web/src/styles.css
git commit -m "fix: polish document intake review"
```

---

## Final Acceptance Matrix

- [ ] 业务行有独立“请上传”按钮，整行支持拖放，且不存在嵌套按钮。
- [ ] 行级上传仅形成业务预选，最终业务关系由人工确认。
- [ ] 所有多页 PDF 必须人工批量确认边界；单页文件无额外机械步骤。
- [ ] 拆分、连续页合并、空白页去除/恢复满足页数守恒。
- [ ] 一个物理单据可关联多笔业务，但只物化一次、只统计一次。
- [ ] AI 类型建议可人工覆盖，建议值、有效值和来源均可追踪。
- [ ] 前后端都阻止漏页、物理重复页、未归属、未确认多页边界和未知类型进入 OCR。
- [ ] 正常项支持批量确认，异常项显著但不过度污染视觉。
- [ ] 旧 `chain_id` 数据和现有单页/OCR/审计测试流程保持兼容。
- [ ] pytest、Vitest、lint、生产构建和三档桌面窗口视觉检查有明确通过证据。
