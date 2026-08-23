# GOSPD01030 定向 LLM 提示词与 JSON 输出合同

**角色：** 填制计划组件（非写入器、非审计结论批准人）  
**完整提示词正文：** 见 `docs/GOSPD01030_底稿填制指引与Prompt.md` 第五节起  
**确定性写入：** `src/reporting/gospd01030_filler.py`（唯一允许改 xlsx 的代码路径）

---

## 1. 权限边界（硬约束）

| 允许 | 禁止 |
|------|------|
| 输出填制计划 JSON | 直接编辑 Excel / 覆盖公式 / 改 DV |
| 引用已接受事实与规则引擎结果 | 把推断写成事实 |
| 标记 NOT_TESTED / NEEDS_REVIEW / 冲突码 | 用付款账期/回款日替代控制权日 |
| 建议 COPY_TEMPLATE_ROW | INSERT_BLANK_ROW；擅自新增工作表（除非 human_config 授权日志页） |

---

## 2. 最小 JSON 合同（计划 → 写入器）

写入器当前**不消费** LLM JSON（规则引擎直填）；本契约用于 Prompt Lab / 未来两阶段填制对齐。字段名稳定，不得私自增删。

```json
{
  "procedure_id": "GOSPD01030",
  "execution_status": "COMPLETED | BLOCKED | NEEDS_REVIEW | ERROR",
  "engagement": {
    "entity_name": "string|null",
    "period_end": "YYYY-MM-DD|null",
    "currency": "string",
    "unit": "string"
  },
  "eval_answers": {
    "transport_terms": {"value": "string|null", "status": "OK|NEEDS_REVIEW|TEMPLATE_VALIDATION_CONFLICT"},
    "skip_system_invoice": {"value": "string|null", "status": "OK|NEEDS_REVIEW"},
    "check_sales_order": {"value": "string|null", "status": "OK|NEEDS_REVIEW"}
  },
  "samples": [
    {
      "sample_id": "string",
      "chain_id": "string",
      "voucher_no": "string|null",
      "posting_date": "YYYY-MM-DD|null",
      "customer": "string|null",
      "amt_book": "decimal-string|null",
      "qty_book": "number|null",
      "invoice_no": "string|null",
      "invoice_amt": "decimal-string|null",
      "order_no": "string|null",
      "transport": "string|null",
      "delivery_type": "string|null",
      "delivery_no": "string|null",
      "control_date": "YYYY-MM-DD|null",
      "qty_doc": "number|null",
      "amt_delivery": "decimal-string|null",
      "amt_delivery_status": "OK|NOT_APPLICABLE|INSUFFICIENT_EVIDENCE",
      "cutoff_independent": "YES 是|No 否|null",
      "ar_period_independent": "YES 是|No 否|null",
      "formula_actions": {
        "K": "PRESERVE_FORMULA",
        "S": "PRESERVE_FORMULA",
        "T": "PRESERVE_FORMULA",
        "V": "PRESERVE_FORMULA"
      },
      "all_ok": "string|null",
      "exception": "string|null",
      "statuses": ["PASS|EXCEPTION|NOT_TESTED|NOT_APPLICABLE|NEEDS_REVIEW|FORMULA_LOGIC_CONFLICT"],
      "evidence_refs": [{"document_id": "string", "file_name": "string", "page": 1, "field": "string"}]
    }
  ],
  "blocked_reasons": ["string"]
}
```

### 写入映射（计划字段 → 模板）

| JSON | 单元格/行为 |
|------|-------------|
| engagement.period_end | M5（空则 NOT_TESTED，禁默认年） |
| eval_answers.transport_terms | E13 |
| skip_system_invoice / check_sales_order | E14 / E15 |
| samples[*] 事实列 | B–T 对应输入列 |
| formula_actions | K/S/T/V 只保留公式 |
| cutoff_independent | 仅日志比对；**禁止写 V** |
| ar_period_independent | 仅日志「步骤3」；并入 W/X |
| all_ok | W（必须 ∈ 实时 DV） |
| exception | X |

---

## 3. 状态码与写入器现口径对齐

| 码 | 含义 | 写入器行为 |
|----|------|------------|
| NOT_TESTED | 未配置期末 / Gate4 未确认 / 未跑测试 | 不写正式「是」；日志记录 |
| INSUFFICIENT_EVIDENCE | 缺控制权日等 | 期间空；W 不得 YES |
| FORMULA_LOGIC_CONFLICT | 独立判断 ≠ V 公式口径 | 保留 V；W 不写「是」；X/日志记冲突 |
| NOT_APPLICABLE | R 无可比金额 | R 空+灰度，不填 0 |
| TEMPLATE_VALIDATION_CONFLICT | 题目与下拉不匹配 | 停写该格 |

---

## 4. 验收挂钩

计划输出是否合规，最终以 `docs/GOSPD01030_验收矩阵与质量门禁.md` 的 G5–G10、UC01–UC08 为准；可执行门禁：`scripts/accept_gospd01030_gates.py`。
