"""截止性测试 Agent — Streamlit 调试控制台。"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings
from src.models.schemas import CutoffResponse
from src.reporting.workbook_generator import WorkbookGenerator

API_BASE = "http://localhost:8000"
CUTOFF_URL = f"{API_BASE}/api/v1/cutoff"
DEFAULT_RECEIPT = date(2026, 6, 1)
DEFAULT_ENTRY = date(2026, 6, 11)


def _api_health() -> bool:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _post_cutoff(payload: dict[str, Any]) -> tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.post(CUTOFF_URL, json=payload, timeout=30)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            return None, f"HTTP {r.status_code}: {detail}"
        return r.json(), None
    except requests.RequestException as exc:
        return None, f"请求失败（请确认已启动 API）：{exc}"


def _status_banner(status: str) -> None:
    if status == "PASS":
        st.success(f"测试状态：**{status}**")
    elif status == "WARNING":
        st.warning(f"测试状态：**{status}**")
    else:
        st.error(f"测试状态：**{status}**")


def _show_single_result(data: dict[str, Any]) -> None:
    _status_banner(str(data.get("测试状态", "")))
    c1, c2, c3 = st.columns(3)
    c1.metric("风险等级", data.get("风险等级") or "-")
    c2.metric("应确认日期", data.get("应确认日期") or "-")
    c3.metric("偏差天数", data.get("偏差天数") if data.get("偏差天数") is not None else "-")
    st.markdown(f"**问题描述：** {data.get('问题描述') or '-'}")
    st.markdown(f"**计算依据：** {data.get('计算依据') or '-'}")
    path = data.get("底稿文件路径")
    if path:
        abs_path = ROOT / path
        st.markdown(f"**底稿文件路径：** `{path}`")
        if abs_path.is_file():
            st.download_button(
                "下载底稿 CSV",
                data=abs_path.read_bytes(),
                file_name=abs_path.name,
                mime="text/csv",
                key=f"dl_single_{data.get('报告ID', path)}",
            )
        else:
            st.caption("文件尚未落盘或路径不可读，可到「查看已生成底稿」刷新。")
    with st.expander("完整 JSON"):
        st.json(data)


def _parse_optional_int(text: str) -> Optional[int]:
    raw = (text or "").strip()
    if not raw:
        return None
    return int(raw)


def _build_payload_from_form(
    biz_id: str,
    contract_id: str,
    customer: str,
    payment_desc: str,
    payment_days_text: str,
    receipt: date,
    entry: date,
    amount: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "业务编号": biz_id.strip(),
        "签收日期": receipt.isoformat(),
        "入账日期": entry.isoformat(),
        "入账金额": float(amount),
    }
    if contract_id.strip():
        payload["合同编号"] = contract_id.strip()
    if customer.strip():
        payload["客户名称"] = customer.strip()
    if payment_desc.strip():
        payload["合同账期描述"] = payment_desc.strip()
    days = _parse_optional_int(payment_days_text)
    if days is not None:
        payload["合同账期天数"] = days
    return payload


def _response_to_flat_row(data: dict[str, Any]) -> dict[str, Any]:
    fill = data.get("底稿回填") or {}
    return {
        "报告ID": data.get("报告ID"),
        "业务编号": data.get("业务编号"),
        "测试状态": data.get("测试状态"),
        "风险等级": data.get("风险等级"),
        "应确认日期": data.get("应确认日期"),
        "偏差天数": data.get("偏差天数"),
        "问题描述": data.get("问题描述"),
        "计算依据": data.get("计算依据"),
        "底稿文件路径": data.get("底稿文件路径"),
        "凭证号": fill.get("凭证号"),
        "客户名称": fill.get("客户名称"),
        "合同编号": fill.get("合同编号"),
        "审计结论": fill.get("审计结论"),
    }


def _export_workbook_bytes(responses: list[dict[str, Any]]) -> bytes:
    models = [CutoffResponse.model_validate(item) for item in responses]
    # 写到临时路径再读回，保证与正式生成器一致
    tmp = settings.REPORTS_DIR / "_debug_batch_export.csv"
    WorkbookGenerator.generate_from_responses(models, str(tmp))
    data = tmp.read_bytes()
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return data


def render_single_test() -> None:
    st.subheader("🧪 单条截止性测试（API调试）")
    with st.form("single_cutoff_form"):
        biz_id = st.text_input("业务编号", value="SO-DEBUG-001")
        c1, c2 = st.columns(2)
        with c1:
            contract_id = st.text_input("合同编号（可选）", value="")
            payment_desc = st.text_input("合同账期描述（可选）", value="签收后10日")
            receipt = st.date_input("签收日期", value=DEFAULT_RECEIPT)
            amount = st.number_input("入账金额", min_value=0.0, value=500.0, step=100.0)
        with c2:
            customer = st.text_input("客户名称（可选）", value="")
            payment_days_text = st.text_input("合同账期天数（可选，优先使用）", value="10")
            entry = st.date_input("入账日期", value=DEFAULT_ENTRY)
        submitted = st.form_submit_button("执行测试", type="primary")

    if not submitted:
        return
    if not biz_id.strip():
        st.error("业务编号不能为空")
        return
    try:
        payload = _build_payload_from_form(
            biz_id,
            contract_id,
            customer,
            payment_desc,
            payment_days_text,
            receipt,
            entry,
            amount,
        )
    except ValueError:
        st.error("合同账期天数须为整数")
        return

    with st.spinner("调用 /api/v1/cutoff …"):
        data, err = _post_cutoff(payload)
    if err:
        st.error(err)
        return
    assert data is not None
    _show_single_result(data)


def render_batch_test() -> None:
    st.subheader("📦 批量测试（上传JSONL）")
    st.caption("每行一个 CutoffRequest JSON 对象。")
    uploaded = st.file_uploader("上传 .jsonl 文件", type=["jsonl", "json"])
    if st.button("批量执行", type="primary", disabled=uploaded is None):
        if uploaded is None:
            return
        raw = uploaded.getvalue().decode("utf-8-sig")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        payloads: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        for i, line in enumerate(lines, start=1):
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError as exc:
                parse_errors.append(f"第 {i} 行 JSON 无效: {exc}")
        if parse_errors:
            st.error("\n".join(parse_errors[:5]))
            return

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        progress = st.progress(0.0, text="批量调用中…")
        for idx, payload in enumerate(payloads, start=1):
            data, err = _post_cutoff(payload)
            if err:
                errors.append(f"业务编号={payload.get('业务编号', '?')}: {err}")
            elif data:
                results.append(data)
            progress.progress(idx / max(len(payloads), 1), text=f"{idx}/{len(payloads)}")
        progress.empty()

        st.session_state["batch_results"] = results
        st.session_state["batch_errors"] = errors

    results = st.session_state.get("batch_results") or []
    errors = st.session_state.get("batch_errors") or []
    if not results and not errors:
        return

    total = len(results)
    n_pass = sum(1 for r in results if r.get("测试状态") == "PASS")
    n_warn = sum(1 for r in results if r.get("测试状态") == "WARNING")
    n_fail = sum(1 for r in results if r.get("测试状态") == "FAIL")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("总条数", total)
    m2.metric("PASS", n_pass)
    m3.metric("WARNING", n_warn)
    m4.metric("FAIL", n_fail)

    if errors:
        st.warning(f"{len(errors)} 条调用失败")
        with st.expander("失败详情"):
            for e in errors:
                st.text(e)

    if results:
        df = pd.DataFrame([_response_to_flat_row(r) for r in results])
        st.dataframe(df, use_container_width=True)
        csv_bytes = _export_workbook_bytes(results)
        st.download_button(
            "导出合并底稿 CSV",
            data=csv_bytes,
            file_name="底稿_批量导出_GOSPD01010.csv",
            mime="text/csv",
            key="dl_batch_workbook",
        )


def render_workbook_viewer() -> None:
    st.subheader("📄 查看已生成底稿")
    reports_dir = Path(settings.REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_files = sorted(reports_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csv_files:
        st.info("reports/ 下暂无 CSV 文件。先跑单条/批量测试即可生成。")
        return

    labels = [p.name for p in csv_files]
    choice = st.selectbox("选择 CSV 文件", labels)
    path = reports_dir / choice
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        st.error(f"读取失败: {exc}")
        return
    st.caption(f"路径：`reports/{choice}` · 行数 {len(df)}")
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "下载CSV",
        data=path.read_bytes(),
        file_name=choice,
        mime="text/csv",
        key=f"dl_view_{choice}",
    )


def main() -> None:
    st.set_page_config(page_title="截止性测试调试控制台", layout="wide")
    st.title("截止性测试 Agent · 调试控制台")
    st.caption("开发测试 / 离线验证入口（调用本地 API）")

    healthy = _api_health()
    if healthy:
        st.success(f"API 已连接：{API_BASE}")
    else:
        st.error(
            f"无法连接 API（{API_BASE}）。请先运行 `python run_api.py` 或双击 `start_api.bat`。"
        )

    st.divider()
    render_single_test()
    st.divider()
    render_batch_test()
    st.divider()
    render_workbook_viewer()


if __name__ == "__main__":
    main()
