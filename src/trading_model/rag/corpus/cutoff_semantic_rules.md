# 截止与控制权语义规则（移植自合同审阅 agent 提示词，已去掉样例正文）

来源：抽凭合同合规性审阅 agent `CUTOFF_SEMANTIC_EXTRACTION`。不替代职业判断。

- 不要把付款账期加入收入确认日期。
- 发货单不天然证明控制权转移；到货日不等于验收完成日。
- 国内签收：授权客户签收日。国内实质验收：验收完成或无异议期限届满日。
- FOB：清洁已装船提单日。CIF：通常仍为装船日，不得因运保费自动取到港日。
- DAP：指定地点置于买方处置并签收日。
- 必须分别识别：transport_term、incoterms_version、named_place、control_transfer_trigger、required_evidence_type、candidate_control_dates。
- 签收、数量收货、质量验收、最终验收必须分开。
