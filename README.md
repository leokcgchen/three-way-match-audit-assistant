# 合同截止性测试 Agent

接收三单系统 JSON，执行截止性测试，并自动追加 GOSPD01010 底稿 CSV。

## 启动

| 用途 | 命令 |
|------|------|
| API 服务 | `python run_api.py` 或双击 `start_api.bat` |
| 调试 UI | `python run_debug_ui.py` 或双击 `start_debug_ui.bat` |

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health
- 截止性接口：`POST /api/v1/cutoff`
- 三单联动：`POST /api/v1/three-way-match`
- OCR 适配：`src/legacy_ocr`（千帆 PaddleOCR + LLM，Key 见 `.env`）
- 底稿输出：`reports/底稿_GOSPD01010.csv`

## 调试控制台

Streamlit 调试页包含三个功能区：

1. **单条截止性测试（API调试）** — 表单调用本地 API
2. **批量测试（上传JSONL）** — 每行一个 CutoffRequest
3. **查看已生成底稿** — 预览 / 下载 `reports/*.csv`

> 使用调试 UI 前请先启动 API。

## 示例请求

```bash
curl -X POST http://localhost:8000/api/v1/cutoff ^
  -H "Content-Type: application/json" ^
  -d "{\"业务编号\":\"SO-001\",\"签收日期\":\"2026-06-01\",\"入账日期\":\"2026-06-01\",\"入账金额\":500,\"合同账期天数\":10}"
```

截止性公式：**应确认日 = 控制权转移日（签收/验收日）**，与序时账过账日比对；`合同账期天数` 仍可传入并写入底稿，但不参与应确认日计算。