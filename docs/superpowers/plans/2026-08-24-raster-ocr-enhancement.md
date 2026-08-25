# Raster OCR Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$subagent-driven-development` (recommended) or `$executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有图片增强算法接入 OCR 前处理，并验证每页不超过 3 秒。

**Architecture:** 在 `prepare_for_ocr` 的几何校正之后增加一个有界的增强步骤。增强只生成一个候选，使用缩略图质量门决定采用或回退，然后沿现有 `ocr_path` 交给 PaddleOCR。

**Tech Stack:** Python、OpenCV、NumPy、pytest。

## Global Constraints

- 仅覆盖光栅图片，不处理扫描型 PDF。
- 不增加第二次 OCR 调用。
- 工作图上限约 240 万像素。
- 单页完整预处理必须低于 3 秒。
- 不提交、不合并、不推送 Git。

---

### Task 1: 增强路由与质量回退

**Files:**
- Modify: `src/image_preprocess/service.py`
- Test: `tests/test_image_preprocess.py`

**Interfaces:**
- Consumes: `route_defects(pixels)`, `apply_fast_recipe(pixels, route, 0)`, `apply_color_scan(pixels)`。
- Produces: `PreprocessResult.meta` 中的 `enhancement_route`、`enhancement_applied`、`enhancement_status`、`preprocess_elapsed_ms`。

- [ ] 先写灰暗图片应用增强和失败回退测试。
- [ ] 运行测试，确认因正式链路未接入而失败。
- [ ] 实现单候选增强与缩略图质量门。
- [ ] 复跑测试，确认通过。

### Task 2: 三秒性能门与回归

**Files:**
- Test: `tests/test_image_preprocess.py`

**Interfaces:**
- Consumes: `prepare_for_ocr(source_path, cache_dir=...)`。
- Produces: 240 万像素图片端到端性能证据。

- [ ] 先写 240 万像素、3 秒上限性能测试。
- [ ] 运行相关预处理测试与 OCR 适配器测试。
- [ ] 核对 `ocr_path` 仍指向增强输出，并记录实测耗时。
