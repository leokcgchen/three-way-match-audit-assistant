"""Streamlit 前端：合同上传、三单数据录入（含序时账Excel导入）与报告展示。"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings
from src.ui.batch_page import render_batch_mode
from src.ui.ledger_import import (
    AMOUNT_KEYS,
    CUSTOMER_KEYS,
    DATE_KEYS,
    VOUCHER_KEYS,
    default_index,
    guess_column,
    guess_date_column,
    map_and_fill_ledger_data,
    parse_ledger_file,
)

API_BASE = "http://127.0.0.1:8000"
OPTIONAL_NONE = "（不选择）"

STATUS_COLORS = {
    "PASS": "#15803d",
    "WARNING": "#ca8a04",
    "FAIL": "#dc2626",
}

AUDIT_COLORS = {
    "Agrees": "#15803d",
    "Disagrees": "#dc2626",
    "N/A": "#64748b",
    "Not Selected": "#ca8a04",
}


def _init_form_state() -> None:
    defaults = {
        "enable_receipt": False,
        "receipt_date": date.today(),
        "received_qty": 0.0,
        "receiver_name": "",
        "receipt_notes": "",
        "enable_ledger": False,
        "entry_date": date.today(),
        "entry_amount": 0.0,
        "voucher_id": "",
        "customer_name": "",
        "ledger_file_loaded": False,
        "ledger_data_filled": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    st.set_page_config(
        page_title="合同合规审阅Agent",
        page_icon="📄",
        layout="wide",
    )
    _init_form_state()
    st.title("合同合规审阅Agent")

    mode_tab1, mode_tab2 = st.tabs(["单条审阅模式", "批量审阅模式"])

    with mode_tab1:
        render_single_mode()

    with mode_tab2:
        # 侧边栏批量计数在 batch 页写入 session_state
        render_batch_mode()

    with st.sidebar:
        st.header("运行配置")
        st.write(f"**数据源模式：** `{settings.DATA_SOURCE_MODE}`")
        st.write(f"**API地址：** `{API_BASE}`")
        # 根据最近交互粗略展示；批量页有独立计数
        batch_ledger = st.session_state.get("batch_ledger_count", 0)
        batch_receipt = st.session_state.get("batch_receipt_count", 0)
        if batch_ledger or batch_receipt or st.session_state.get("batch_cutoff_result") is not None:
            st.write("**当前模式：** `批量筛查`")
            st.write(f"已上传序时账 **{batch_ledger}** 条，签收单 **{batch_receipt}** 条")
            source = st.session_state.get("batch_contract_source")
            if source:
                if source == "文本提取":
                    ok_n = st.session_state.get("batch_contract_extract_ok", 0)
                    st.write(f"合同编号来源: 文本提取（成功 {ok_n} 条）")
                else:
                    st.write("合同编号来源: 独立列")
        else:
            has_three = st.session_state.get("enable_ledger") or st.session_state.get(
                "enable_receipt"
            )
            st.write(
                f"**当前模式：** `{'单条审阅（含截止性）' if has_three else '单条审阅'}`"
            )
            if has_three:
                st.success("📋 三单数据已录入")
            if st.session_state.get("ledger_file_loaded"):
                st.info("📊 序时账文件已加载")
            if st.session_state.get("ledger_data_filled"):
                st.success("📋 序时账数据已填充")
        if st.button("检查 API 健康状态"):
            try:
                resp = requests.get(f"{API_BASE}/health", timeout=5)
                st.success(resp.json())
            except Exception as exc:
                st.error(f"API 不可用: {exc}")

        st.divider()
        if st.button("🧪 运行计算逻辑自检"):
            from src.utils.audit_utils import run_builtin_cutoff_self_check

            report = run_builtin_cutoff_self_check()
            if report["all_passed"]:
                st.success(report["summary"])
            else:
                st.error(report["summary"])
            with st.expander("自检明细"):
                for item in report["details"]:
                    mark = "✅" if item["ok"] else "❌"
                    st.write(
                        f"{mark} {item['name']}: 期望 {item['expected']} / "
                        f"实际 {item['actual']} / 偏差 {item.get('deviation_days')}"
                    )


def render_single_mode() -> None:
    uploaded, receipt_payload, ledger_payload, _has_three_way = render_inputs()

    if uploaded is None:
        st.info("请先上传合同文件。")
        return

    if st.button("开始审阅", type="primary"):
        with st.spinner("正在调用后端审阅，请稍候..."):
            report = call_upload_api(
                uploaded,
                ledger_entry=ledger_payload,
                delivery_receipt=receipt_payload,
            )
        if report is None:
            return
        st.session_state["latest_report"] = report
        st.success(
            f"报告已生成：`{report.get('report_id')}` ｜ "
            f"已保存至 reports/ 目录"
        )
        show_quick_summary(report)

    report = st.session_state.get("latest_report")
    if not report:
        return

    render_report(report)


def render_inputs() -> tuple[Any, Optional[dict], Optional[dict], bool]:
    st.markdown(
        "上传 PDF / Word 合同后，可选择录入签收单与序时账信息，"
        "系统将自动完成解析、合规审阅、对手方核查与截止性测试。"
    )
    uploaded = st.file_uploader(
        "拖拽或选择合同文件",
        type=["pdf", "docx"],
        accept_multiple_files=False,
    )

    st.subheader("三单数据录入（可选）")
    st.caption("不勾选则仅做合同审阅；勾选后将随合同一并提交至 /upload。")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### 签收单信息（Delivery Receipt）")
        enable_receipt = st.checkbox("提交签收单信息", key="enable_receipt")
        receipt_date = st.date_input("签收日期", key="receipt_date")
        received_qty = st.number_input(
            "签收数量（可选）",
            min_value=0.0,
            step=1.0,
            key="received_qty",
        )
        receiver_name = st.text_input("签收人（可选）", key="receiver_name")
        receipt_notes = st.text_area("备注（可选）", key="receipt_notes")

    with col_b:
        st.markdown("#### 序时账信息（Ledger Entry）")
        render_ledger_file_importer()
        st.markdown("##### 手动确认 / 微调")
        enable_ledger = st.checkbox("提交序时账信息", key="enable_ledger")
        entry_date = st.date_input("入账日期", key="entry_date")
        entry_amount = st.number_input(
            "确认收入金额（万元）",
            min_value=0.0,
            step=0.1,
            key="entry_amount",
        )
        voucher_id = st.text_input("凭证编号（可选）", key="voucher_id")
        customer_name = st.text_input("客户名称（可选）", key="customer_name")

    receipt_payload = None
    if enable_receipt:
        receipt_payload = {
            "receipt_date": receipt_date.isoformat(),
            "received_quantity": received_qty if received_qty > 0 else None,
            "receiver_name": (receiver_name or "").strip() or None,
            "notes": (receipt_notes or "").strip() or None,
        }

    ledger_payload = None
    if enable_ledger:
        ledger_payload = {
            "entry_date": entry_date.isoformat(),
            "entry_amount": float(entry_amount),
            "voucher_id": (voucher_id or "").strip() or None,
            "customer_name": (customer_name or "").strip() or None,
        }

    has_three_way = receipt_payload is not None or ledger_payload is not None
    return uploaded, receipt_payload, ledger_payload, has_three_way


def render_ledger_file_importer() -> None:
    """序时账 Excel/CSV 上传、预览、列映射与导入填充。"""
    st.caption("支持格式：Excel (.xlsx/.xls)、CSV、JSONL (.jsonl)")
    ledger_file = st.file_uploader(
        "上传序时账文件（Excel/CSV/JSONL）",
        type=["xlsx", "xls", "csv", "jsonl"],
        accept_multiple_files=False,
        key="ledger_file_uploader",
    )

    if ledger_file is None:
        st.session_state["ledger_file_loaded"] = False
        return

    try:
        df = parse_ledger_file(ledger_file)
    except ValueError as exc:
        st.error(str(exc))
        st.session_state["ledger_file_loaded"] = False
        return
    except Exception as exc:
        st.error(f"读取文件失败: {exc}")
        st.session_state["ledger_file_loaded"] = False
        return

    if df.empty:
        st.warning("文件为空，请检查内容。")
        st.session_state["ledger_file_loaded"] = False
        return

    st.session_state["ledger_file_loaded"] = True
    st.caption(f"已读取 `{ledger_file.name}`，共 {len(df)} 行。")
    st.dataframe(df.head(5), use_container_width=True, hide_index=True)

    columns = [str(c) for c in df.columns.tolist()]
    optional_cols = [OPTIONAL_NONE] + columns

    st.markdown("##### 列映射")
    st.caption("请把「入账日期列」选成真正的日期字段；凭证号（如 SA25-0001）不能当日期。")
    guess_date = guess_date_column(df, DATE_KEYS)
    guess_amount = guess_column(columns, AMOUNT_KEYS)
    guess_voucher = guess_column(columns, VOUCHER_KEYS)
    guess_customer = guess_column(columns, CUSTOMER_KEYS)
    date_col = st.selectbox(
        "请选择入账日期列",
        options=columns,
        index=default_index(columns, guess_date),
        key="map_date_col",
    )
    amount_col = st.selectbox(
        "请选择收入金额列",
        options=columns,
        index=default_index(columns, guess_amount),
        key="map_amount_col",
    )
    voucher_col_raw = st.selectbox(
        "请选择凭证编号列（可选）",
        options=optional_cols,
        index=default_index(optional_cols, guess_voucher) if guess_voucher else 0,
        key="map_voucher_col",
    )
    customer_col_raw = st.selectbox(
        "请选择客户名称列（可选）",
        options=optional_cols,
        index=default_index(optional_cols, guess_customer) if guess_customer else 0,
        key="map_customer_col",
    )

    selected_row_1based = st.number_input(
        "导入第几行（对应预览/原表数据行，从 1 开始）",
        min_value=1,
        max_value=max(len(df), 1),
        value=1,
        step=1,
        key="ledger_import_row",
    )

    if st.button("导入此条数据", key="import_ledger_btn"):
        mapping = {
            "date_col": date_col,
            "amount_col": amount_col,
            "voucher_col": None
            if voucher_col_raw == OPTIONAL_NONE
            else voucher_col_raw,
            "customer_col": None
            if customer_col_raw == OPTIONAL_NONE
            else customer_col_raw,
        }
        try:
            payload = map_and_fill_ledger_data(
                df, int(selected_row_1based) - 1, mapping
            )
            # 写入表单 session_state（需在控件渲染前生效 → rerun）
            parsed = datetime.strptime(payload["entry_date"], "%Y-%m-%d").date()
            st.session_state["enable_ledger"] = True
            st.session_state["entry_date"] = parsed
            st.session_state["entry_amount"] = float(payload["entry_amount"])
            st.session_state["voucher_id"] = payload.get("voucher_id") or ""
            st.session_state["customer_name"] = payload.get("customer_name") or ""
            st.session_state["ledger_data_filled"] = True
            st.session_state["imported_ledger_preview"] = payload
            st.success("✅ 序时账数据已导入，请确认后点击「开始审阅」")
            st.rerun()
        except ValueError as exc:
            st.error(f"导入校验失败：{exc}")
            st.session_state["ledger_data_filled"] = False


def show_quick_summary(report: dict) -> None:
    audit = report.get("audit_program_result") or {}
    if audit:
        s1 = (audit.get("step1_distinct_obligations") or {}).get("conclusion_zh", "-")
        s2 = (audit.get("step2_transaction_price") or {}).get("conclusion_zh", "-")
        s3 = (audit.get("step3_revenue_recognition") or {}).get("conclusion_zh", "-")
        st.info(f"测试步骤结论速览：步骤1={s1}，步骤2={s2}，步骤3={s3}")
    else:
        st.error(
            "后端未返回程序表结论（audit_program_result）。"
            "请重启 API：先关闭旧进程后执行 python run_api.py"
        )

    cutoff = report.get("cutoff_test_result")
    if cutoff:
        st.info(
            f"截止性测试：{cutoff.get('test_status')} ｜ "
            f"偏差={cutoff.get('deviation_days')}天 ｜ "
            f"{cutoff.get('issue_description')}"
        )


def call_upload_api(
    uploaded_file,
    ledger_entry: Optional[dict] = None,
    delivery_receipt: Optional[dict] = None,
) -> dict | None:
    files = {
        "file": (
            uploaded_file.name,
            uploaded_file.getvalue(),
            uploaded_file.type or "application/octet-stream",
        )
    }
    data: dict[str, str] = {}
    if ledger_entry is not None:
        data["ledger_entry"] = json.dumps(ledger_entry, ensure_ascii=False)
    if delivery_receipt is not None:
        data["delivery_receipt"] = json.dumps(delivery_receipt, ensure_ascii=False)

    try:
        resp = requests.post(
            f"{API_BASE}/upload",
            files=files,
            data=data or None,
            timeout=120,
        )
    except requests.RequestException as exc:
        st.error(f"无法连接后端 API，请先启动 FastAPI（run_api.py）。错误: {exc}")
        return None

    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        st.error(f"审阅失败 ({resp.status_code}): {detail}")
        return None

    return resp.json()


def render_report(report: dict) -> None:
    contract = report.get("contract_info") or {}
    compliance = report.get("compliance_result") or {}
    audit = report.get("audit_program_result") or {}
    counterparty = report.get("counterparty_info") or {}
    cutoff = report.get("cutoff_test_result")

    tab_names = ["合同信息", "测试步骤结论", "合规明细", "对手方信息"]
    if cutoff is not None:
        tab_names.append("截止性测试")

    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_contract_tab(contract)
    with tabs[1]:
        render_audit_program_tab(audit)
    with tabs[2]:
        render_compliance_tab(compliance)
    with tabs[3]:
        render_counterparty_tab(counterparty)
    if cutoff is not None:
        with tabs[4]:
            render_cutoff_tab(cutoff)

    with st.expander(
        "下游JSON预览（to_downstream_json）— 供下游三单匹配Agent调用",
        expanded=False,
    ):
        st.caption("供下游三单匹配Agent调用")
        st.json(report.get("to_downstream_json") or {})

    st.divider()
    st.caption(
        f"报告ID: `{report.get('report_id', '-')}` ｜ "
        f"生成时间: `{report.get('generated_at', '-')}` ｜ "
        f"{report.get('human_judgment_summary', '')}"
    )


def render_cutoff_tab(cutoff: dict) -> None:
    st.subheader("截止性测试结果")
    if not cutoff:
        st.warning("未执行截止性测试，请上传签收单和序时账信息。")
        return

    status = cutoff.get("test_status", "WARNING")
    color = STATUS_COLORS.get(status, "#64748b")
    st.markdown(
        f"<div style='padding:12px 16px;border-radius:8px;background:{color};"
        f"color:white;font-size:20px;font-weight:600;display:inline-block;'>"
        f"截止性状态：{status}</div>",
        unsafe_allow_html=True,
    )

    deviation = cutoff.get("deviation_days")
    if deviation is None:
        deviation_text = "-"
    elif deviation > 0:
        deviation_text = f"+{deviation}天"
    else:
        deviation_text = f"{deviation}天"

    rows = [
        {"字段": "应确认收入日期", "值": cutoff.get("expected_revenue_date")},
        {"字段": "实际入账日期", "值": cutoff.get("actual_entry_date")},
        {"字段": "偏差天数", "值": deviation_text},
        {"字段": "问题描述", "值": cutoff.get("issue_description")},
        {"字段": "计算依据", "值": cutoff.get("calculation_basis")},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    trail = cutoff.get("calculation_trail")
    if trail:
        with st.expander("展开计算过程", expanded=False):
            for step in trail:
                action = step.get("action", "")
                has_error = bool(step.get("error"))
                if has_error or "判断" in action:
                    color_s = "#dc2626"
                elif step.get("formula") is not None or str(action).startswith("计算"):
                    color_s = "#16a34a"
                else:
                    color_s = "#2563eb"
                st.markdown(
                    f"<div style='border-left:4px solid {color_s};padding:6px 10px;"
                    f"margin:4px 0;background:#f8fafc;'>"
                    f"<b>Step {step.get('step')} · {action}</b><br/>"
                    f"input={step.get('input')!r} | formula={step.get('formula')!r} | "
                    f"output={step.get('output')!r}"
                    + (f" | error={step.get('error')!r}" if has_error else "")
                    + "</div>",
                    unsafe_allow_html=True,
                )


def render_contract_tab(contract: dict) -> None:
    rows = [
        {"字段": "合同编号", "值": contract.get("contract_id")},
        {"字段": "合同名称", "值": contract.get("contract_title")},
        {"字段": "签订日期", "值": contract.get("signing_date")},
        {"字段": "合同金额(万元)", "值": contract.get("total_contract_amount")},
        {"字段": "收入确认时点", "值": contract.get("revenue_recognition_point")},
        {"字段": "控制权转移时点", "值": contract.get("control_transfer_time")},
        {"字段": "合同期限", "值": contract.get("contract_term")},
        {"字段": "原文预览", "值": contract.get("raw_text_preview")},
    ]
    st.subheader("基础字段")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    parties = contract.get("parties") or []
    st.subheader("当事方")
    if parties:
        st.dataframe(pd.DataFrame(parties), use_container_width=True, hide_index=True)
    else:
        st.write("无")

    obligations = contract.get("performance_obligations") or []
    st.subheader("履约义务")
    if obligations:
        st.dataframe(
            pd.DataFrame(obligations), use_container_width=True, hide_index=True
        )
    else:
        st.write("无")


def render_audit_program_tab(audit: dict) -> None:
    st.subheader("KPMG 测试步骤结论（Agrees / Disagrees / N/A / Not Selected）")
    if not audit:
        st.warning("未返回程序表结论")
        return

    steps = [
        audit.get("step1_distinct_obligations") or {},
        audit.get("step2_transaction_price") or {},
        audit.get("step3_revenue_recognition") or {},
    ]
    rows = []
    for step in steps:
        rows.append(
            {
                "步骤": step.get("step_no"),
                "名称": step.get("step_name"),
                "结论": step.get("conclusion"),
                "中文": step.get("conclusion_zh"),
                "Notes注释": step.get("notes"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    cols = st.columns(3)
    for idx, step in enumerate(steps):
        conclusion = step.get("conclusion", "Not Selected")
        color = AUDIT_COLORS.get(conclusion, "#64748b")
        with cols[idx]:
            st.markdown(
                f"<div style='padding:10px;border-radius:8px;background:{color};"
                f"color:white;text-align:center;'>"
                f"<div>步骤{step.get('step_no')}</div>"
                f"<div style='font-size:22px;font-weight:700;'>"
                f"{step.get('conclusion_zh')} / {conclusion}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    if audit.get("pending_for_three_way_match"):
        st.info("步骤3仍待三单智能匹配 Agent 最终裁定（当前为 Not Selected / 未选）。")


def render_compliance_tab(compliance: dict) -> None:
    status = compliance.get("overall_status", "UNKNOWN")
    color = STATUS_COLORS.get(status, "#64748b")
    st.markdown(
        f"<div style='padding:12px 16px;border-radius:8px;background:{color};"
        f"color:white;font-size:20px;font-weight:600;display:inline-block;'>"
        f"总体状态：{status}</div>",
        unsafe_allow_html=True,
    )
    st.write(compliance.get("summary", ""))

    issues = compliance.get("issues") or []
    if issues:
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
    else:
        st.write("无问题清单")


def render_counterparty_tab(counterparty: dict) -> None:
    note = counterparty.get("confidence_note")
    if note:
        st.info(note)

    parties = counterparty.get("parties") or []
    if not parties:
        st.write("未获取到对手方信息")
        return

    for idx, company in enumerate(parties, start=1):
        title = company.get("company_name") or f"企业{idx}"
        with st.container(border=True):
            st.subheader(f"{idx}. {title}")
            cols = st.columns(2)
            fields = [
                ("登记状态", company.get("registration_status")),
                ("法定代表人", company.get("legal_representative")),
                ("注册资本", company.get("registered_capital")),
                ("成立日期", company.get("establishment_date")),
                ("经营异常", company.get("is_abnormal")),
                ("黑名单", company.get("is_blacklisted")),
                ("数据来源", company.get("data_source")),
                ("诉讼风险", company.get("litigation_risk_summary")),
            ]
            for i, (label, value) in enumerate(fields):
                cols[i % 2].write(f"**{label}：** {value}")
            st.write(f"**经营范围：** {company.get('business_scope')}")


main()
