# Adaptive Sample Primary Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让抽样清单自动优先采用“业务编号类”主键，并在 ERP 表头未知时使用安全的唯一性推断。

**Architecture:** 在 `sample_population.py` 内增加独立的候选列评分器，统一输出选中列、方法、置信度和候选轨迹。工作簿解析器先识别表头和主键，再把选中列显式传给逐行解析，避免表头识别、编号格式校验和错误文案混在一起。

**Tech Stack:** Python 3、openpyxl、pytest。

## Global Constraints

- “业务编号/业务 ID/业务索引号”优先于“销售订单号/订单编号”。
- 凭证号、发票号、行号、日期、金额、客户名称不得仅凭唯一性成为主键。
- 接受 `YW-2025-3986`、`SO-251218-7365` 等多段编号。
- 无可靠候选时返回候选诊断，不再要求固定 ERP 表头。
- 不执行 Git add、commit、checkout、reset 或其他 Git 写操作。

---

### Task 1: 主键候选评分器

**Files:**
- Modify: `src/audit/sample_population.py`
- Test: `tests/test_sample_population_excel.py`

**Interfaces:**
- Consumes: `headers: list[str]`、`data_rows: list[list[Any]]`
- Produces: `_select_primary_key(headers, data_rows) -> dict[str, Any] | None`
- Selection keys: `column`, `index`, `method`, `confidence`, `candidates`

- [x] **Step 1: 写当前真实表格的失败测试**

构造同时包含“业务编号、销售订单号、凭证号、发票号码”的工作簿，业务编号为 `YW-2025-3986`，订单号为 `SO-251218-7365`；断言 `business_ids` 来自“业务编号”，并断言 `primary_key_column == "业务编号"`、`primary_key_method == "keyword"`。

- [x] **Step 2: 写未知 ERP 表头的唯一性兜底失败测试**

构造表头 `Case Ref / Posting Date / Amount / Customer`；`Case Ref` 为非连续且全唯一的 `CASE-A9/CASE-B7`，断言自动采用 `Case Ref`。另构造 `行号/日期/金额/客户名称`，断言不得把这些列选成主键。

- [x] **Step 3: 运行定向测试并确认按预期失败**

Run: `python -m pytest tests/test_sample_population_excel.py -q`

Expected: 新增用例因旧逻辑优先销售订单、拒绝多段连字符或缺少元数据而失败。

- [x] **Step 4: 实现最小评分器**

在 `sample_population.py` 增加：

```python
_PRIMARY_KEY_STRONG = ("业务编号", "业务id", "业务索引", "业务流水", "样本编号", "审计索引")
_PRIMARY_KEY_SECONDARY = ("销售订单", "采购订单", "订单编号", "订单号", "交易号", "参考号", "reference", "case ref")
_PRIMARY_KEY_BLOCKED = ("凭证", "发票", "行号", "序号", "日期", "金额", "客户", "名称")

def _select_primary_key(headers: list[str], data_rows: list[list[Any]]) -> dict[str, Any] | None:
    candidates = _rank_primary_key_candidates(headers, data_rows)
    return candidates[0] if candidates else None
```

评分顺序必须是强关键词、次关键词、唯一性兜底；候选必须达到非空率和唯一率门槛，兜底需排除连续整数、日期、金额和长描述列。

- [x] **Step 5: 放宽抽样主键值校验**

新增 `_is_usable_primary_key_value`，允许多段连字符、下划线、中文/英文/数字混合标识符；仅拒绝空值、超长文本、换行长描述和无字母数字的符号串。该规则只用于抽样总体，不修改 OCR 全局编号正则。

- [x] **Step 6: 运行定向测试并确认通过**

Run: `python -m pytest tests/test_sample_population_excel.py -q`

Expected: 新旧抽样清单测试全部通过。

### Task 2: 工作簿接入、诊断与真实文件复验

**Files:**
- Modify: `src/audit/sample_population.py`
- Modify: `src/api/workflow_router.py`
- Test: `tests/test_sample_population_excel.py`

**Interfaces:**
- `parse_sample_workbook(path)` 返回新增字段：`primary_key_column`、`primary_key_method`、`primary_key_confidence`、`primary_key_candidates`
- `build_sample_population(..., primary_key_column="", primary_key_method="", primary_key_confidence=0.0, primary_key_candidates=None)` 接收并保留上述识别轨迹

- [x] **Step 1: 写错误信息失败测试**

构造没有可靠标识符列的工作簿，断言错误包含“未能自动确定唯一业务索引列”和实际识别到的表头，不包含“必须使用销售订单号/订单编号”。

- [x] **Step 2: 运行新增错误测试并确认失败**

Run: `python -m pytest tests/test_sample_population_excel.py -q`

Expected: 旧固定文案导致失败。

- [x] **Step 3: 接入表头扫描与解析轨迹**

扫描前 12 行；跳过单格标题行；对候选表头后的数据调用 `_select_primary_key`。把选中列显式传给 `_parse_sheet_rows(headers, data_rows, sheet=sheet_name, primary_key_col=selection["column"])`，并将识别轨迹返回和保存到 `sample_population`。

- [x] **Step 4: 改写失败诊断**

区分“识别到候选列但无可用值”和“没有可靠候选列”；错误中列出扫描到的表头/候选列，不再出现固定表头要求。

- [x] **Step 5: 运行完整后端测试**

Run: `python -m pytest tests/test_sample_population_excel.py tests/test_column_mapper.py tests/test_ledger_parser.py -q`

Expected: 全部通过。

- [x] **Step 6: 复验当前用户文件**

Run: `python -c "from src.audit.sample_population import parse_sample_workbook; r=parse_sample_workbook(r'D:/Dev/Temp/cutoff_jobs/5dd69ee5d6ef/sample_population.xlsx'); print(r['primary_key_column'], r['primary_key_method'], len(r['business_ids']), r['business_ids'][0])"`

Input: `D:/Dev/Temp/cutoff_jobs/5dd69ee5d6ef/sample_population.xlsx`

Expected: `primary_key_column` 为“业务编号”，首个业务 ID 为 `YW-2025-3986`，且能够解析所有有效数据行。

- [x] **Step 7: 运行项目回归**

Run: `python -m pytest tests -q`

Expected: 无失败。若全量耗时或环境限制阻塞，至少运行直接相关测试集并明确报告限制。
