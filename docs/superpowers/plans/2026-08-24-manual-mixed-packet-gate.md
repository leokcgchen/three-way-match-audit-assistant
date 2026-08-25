# Manual Mixed-Packet Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 由审计师手动声明混装资料包，让普通多页凭证直接 OCR，并恢复当前 33 份凭证的“开始处理”入口。

**Architecture:** 上传接口接收显式 `mixed_packet` 标志并持久化 `mixed_packet_declared`，拆包门禁只认该人工声明，不再根据页数或 AI 推断。OCR 接口支持只处理普通待处理文件且保留未拆包文件；前端以复选框控制混装入口，并分别计算“可 OCR 文件”和“待拆包文件”。

**Tech Stack:** FastAPI、Python、React 19、TypeScript、Vitest、Testing Library、pytest。

## Global Constraints

- 不自动识别或推断混装资料包。
- 普通多页 PDF 作为一个完整单据进入 OCR。
- 只有审计师勾选并通过混装入口上传的文件进入拆包流程。
- 普通文件与混装文件并存时，普通文件可先 OCR，混装文件必须保留。
- 兼容当前任务，33 份文件无须重新上传。
- 不执行任何 Git 提交、切分支或合并操作。

---

### Task 1: 显式混装声明与旧误判恢复

**Files:**
- Modify: `src/api/workflow_router.py:1471-1626`
- Modify: `src/workflow/packet_engine.py:61-156`
- Modify: `tests/test_packet_api.py`

**Interfaces:**
- Consumes: multipart 字段 `mixed_packet: bool`。
- Produces: 待处理记录字段 `mixed_packet_declared: bool`、`upload_source: "standard" | "business_row" | "mixed_packet"`；`annotate_pending_kinds()` 只对人工声明项返回 packet kind。

- [ ] **Step 1: 写失败测试**

```python
def test_two_page_standard_document_does_not_require_unpack(tmp_path):
    # 普通入口上传两页订单，必须为 standard。
    ...
    assert pending[0]["packet_kind"] == "standard"
    assert pending[0]["mixed_packet_declared"] is False
    assert not packet_needs_review(body)


def test_manual_mixed_packet_requires_unpack(tmp_path):
    # multipart mixed_packet=true 是唯一开启拆包的条件。
    ...
    assert pending[0]["mixed_packet_declared"] is True
    assert packet_needs_review(body)
```

- [ ] **Step 2: 运行测试并确认旧规则失败**

Run: `pytest -q tests/test_packet_api.py -k "two_page_standard or manual_mixed"`

Expected: 普通两页 PDF 仍被标记为 `packet_single_chain`，测试失败。

- [ ] **Step 3: 实现显式上传模式**

```python
async def upload_documents(..., mixed_packet: bool = Form(False)) -> dict[str, Any]:
    ...
    "mixed_packet_declared": mixed_packet,
    "upload_source": (
        "mixed_packet" if mixed_packet
        else "business_row" if declared_business_ids
        else "standard"
    )
```

`annotate_pending_kinds()` 对 `mixed_packet_declared is True` 的 PDF 返回 packet kind；其余文件一律回写 `packet_kind="standard"`。旧记录没有 `mixed_packet_declared`，因此当前任务中历史误判的 11 份多页文件会自动恢复为普通凭证。

- [ ] **Step 4: 更新既有拆包测试为人工声明上传**

所有确实测试拆包的上传请求增加：

```python
data={"process": "false", "mixed_packet": "true"}
```

- [ ] **Step 5: 运行拆包测试**

Run: `pytest -q tests/test_packet_api.py`

Expected: PASS。

### Task 2: 普通凭证分批 OCR 且保留混装文件

**Files:**
- Modify: `src/api/workflow_router.py:1969-2202`
- Modify: `tests/test_process_selected_files.py`

**Interfaces:**
- Consumes: `ProcessFilesBody.file_names`，初次 OCR 和重新识别均可传入。
- Produces: `_run_ocr_background(..., remaining_pending: list[dict[str, Any]])`；识别完成后仅移除已处理文件。

- [ ] **Step 1: 写失败测试**

```python
def test_initial_process_selected_standard_files_keeps_manual_packet(monkeypatch, tmp_path):
    # ordinary.pdf 与 packet.pdf 同时待处理，只请求 ordinary.pdf。
    ...
    assert [spec["filename"] for spec in captured["specs"]] == ["ordinary.pdf"]
    assert [row["file_name"] for row in captured["remaining_pending"]] == ["packet.pdf"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest -q tests/test_process_selected_files.py::test_initial_process_selected_standard_files_keeps_manual_packet`

Expected: 当前非 force 分支忽略 `file_names` 或全局拆包门禁返回 409。

- [ ] **Step 3: 实现初次选择处理**

```python
if requested and not force:
    selected = set(requested)
    process_pending = [row for row in pending if name(row) in selected]
    remaining_pending = [row for row in pending if name(row) not in selected]
```

只对 `process_pending` 执行 `packet_blocks_process`。后台完成后写回 `remaining_pending`，不得使用 `pending_files=[]` 清空整队列。

- [ ] **Step 4: 运行处理接口测试**

Run: `pytest -q tests/test_process_selected_files.py tests/test_packet_api.py`

Expected: PASS。

### Task 3: 前端人工勾选、OCR 按钮与当前任务兼容

**Files:**
- Modify: `web/src/api.ts:374-390`
- Modify: `web/src/pages/UploadPage.tsx:36-155,354-427`
- Modify: `web/src/pages/UploadPage.v2.test.tsx`
- Modify: `web/src/index.css`（仅在现有样式无法承载复选框说明时）

**Interfaces:**
- Consumes: `api.upload(..., { mixedPacket?: boolean })`。
- Produces: 复选框“存在混装资料包”、按人工声明过滤的 `processablePending`、普通 OCR 提交文件名数组。

- [ ] **Step 1: 改写并新增失败测试**

```tsx
it('shows mixed upload only after the auditor opts in', async () => {
  render(<UploadPage job={ordinaryJob} onJob={vi.fn()} />)
  expect(screen.queryByRole('button', { name: '上传混装资料包' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: '存在混装资料包' }))
  expect(screen.getByRole('button', { name: '上传混装资料包' })).toBeEnabled()
})

it('keeps OCR available for ordinary multi-page files', () => {
  render(<UploadPage job={legacyMisclassifiedJob} onJob={vi.fn()} onProcess={onProcess} />)
  expect(screen.getByRole('button', { name: '开始处理（33）' })).toBeEnabled()
})
```

- [ ] **Step 2: 运行前端测试并确认失败**

Run: `npm test -- UploadPage.v2.test.tsx`

Expected: 混装按钮默认仍显示，且 packet 标记仍隐藏 OCR。

- [ ] **Step 3: 扩展上传 API**

```ts
opts?: { force?: boolean; process?: boolean; businessHints?: Record<string, string[]>; mixedPacket?: boolean }
if (opts?.mixedPacket) fd.append('mixed_packet', 'true')
```

- [ ] **Step 4: 实现页面状态和按钮规则**

默认 `mixedPacketMode=false`。混装按钮仅在勾选后显示，并使用 `api.upload(..., { process: false, mixedPacket: true })`。普通上传显式传 `mixedPacket:false`。

`processablePending` 包含未人工声明为混装的记录；首次按钮显示 `开始处理（processablePending.length）`，点击调用：

```ts
await onProcess(false, processablePending.map((row) => row.file_name))
```

若只剩未拆包文件，则显示拆包提示但不伪装成 OCR 可处理状态。

- [ ] **Step 5: 运行前端定向测试与构建**

Run: `npm test -- UploadPage.v2.test.tsx`

Run: `npm run build`

Expected: 两项均 PASS。

### Task 4: 全链路验收

**Files:**
- Verify only: 当前运行任务 `5dd69ee5d6ef`

**Interfaces:**
- Consumes: 当前 API 任务数据和前端页面。
- Produces: 可复核的验收结果，不修改 Git。

- [ ] **Step 1: 运行后端回归**

Run: `pytest -q tests/test_process_selected_files.py tests/test_packet_api.py`

Expected: PASS。

- [ ] **Step 2: 检查当前任务重新归类**

刷新或重启服务后读取当前任务，确认 33 份均可作为普通凭证处理，`packet_needs_review=False`。

- [ ] **Step 3: 检查页面**

确认默认不显示“上传混装资料包”，显示绿色“开始处理（33）”；勾选后才显示混装上传入口。

- [ ] **Step 4: 报告验证边界**

若未实际启动正式 OCR，不宣称 OCR 识别内容完成；只报告按钮、队列、接口门禁和构建已验证。

