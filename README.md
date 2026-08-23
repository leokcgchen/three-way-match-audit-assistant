# 抽凭 — 合同合规性审阅 Agent

主路径：选底稿目标 → 工作台立笔（裁剪序时账）→ 上传凭证 → 缺字段才核对 → 测试不通过才确认结论 → 导出 GOSPD 底稿。

## 启动

| 用途 | 命令 |
|------|------|
| **React 工作台** | 双击 `start_workbench.bat`（API :8000 + UI :5173） |
| 仅 API | `python run_api.py` 或 `start_api.bat` |

- 工作台：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## 截止性口径

应确认日 = 控制权转移日（签收/验收日），与序时账过账日比对；合同付款账期不参与截止公式。

## GOSPD01030

| 文档 / 门禁 | 路径 |
|-------------|------|
| 填制指引与 Prompt | `docs/GOSPD01030_底稿填制指引与Prompt.md` |
| 验收矩阵 | `docs/GOSPD01030_验收矩阵与质量门禁.md` |
| LLM JSON 合同 | `docs/GOSPD01030_定向LLM提示词与JSON输出合同.md` |
| 可执行门禁 | `python scripts/accept_gospd01030_gates.py` |
| 端到端签收 | `python scripts/accept_gospd01030_e2e.py` |
| 真 OCR 端到端 | `ACCEPT_01030_OCR=1 python scripts/accept_gospd01030_ocr_e2e.py` |
| 剩余边界说明 | `docs/剩余能力边界与已落地.md` |
