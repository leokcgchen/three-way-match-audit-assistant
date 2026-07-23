"""Streamlit 前端：合同上传与审阅报告展示。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings

API_BASE = "http://127.0.0.1:8000"

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


def main() -> None:
    st.set_page_config(
        page_title="合同合规审阅Agent",
        page_icon="📄",
        layout="wide",
    )
    st.title("合同合规审阅Agent")

    with st.sidebar:
        st.header("运行配置")
        st.write(f"**数据源模式：** `{settings.DATA_SOURCE_MODE}`")
        st.write(f"**API地址：** `{API_BASE}`")
        if st.button("检查 API 健康状态"):
            try:
                resp = requests.get(f"{API_BASE}/health", timeout=5)
                st.success(resp.json())
            except Exception as exc:
                st.error(f"API 不可用: {exc}")

    st.markdown("上传 PDF / Word 合同后，系统将自动完成解析、合规审阅与对手方核查。")
    uploaded = st.file_uploader(
        "拖拽或选择合同文件",
        type=["pdf", "docx"],
        accept_multiple_files=False,
    )

    if uploaded is None:
        st.info("请先上传合同文件。")
        return

    if st.button("开始审阅", type="primary"):
        with st.spinner("正在调用后端审阅，请稍候..."):
            report = call_upload_api(uploaded)
        if report is None:
            return
        st.session_state["latest_report"] = report
        st.success(
            f"报告已生成：`{report.get('report_id')}` ｜ "
            f"已保存至 reports/ 目录"
        )
        audit = report.get("audit_program_result") or {}
        if not audit:
            st.error(
                "后端未返回程序表结论（audit_program_result）。"
                "请重启 API：先关闭旧进程后执行 python run_api.py"
            )
        else:
            s1 = (audit.get("step1_distinct_obligations") or {}).get("conclusion_zh", "-")
            s2 = (audit.get("step2_transaction_price") or {}).get("conclusion_zh", "-")
            s3 = (audit.get("step3_revenue_recognition") or {}).get("conclusion_zh", "-")
            st.info(f"测试步骤结论速览：步骤1={s1}，步骤2={s2}，步骤3={s3}")

    report = st.session_state.get("latest_report")
    if not report:
        return

    render_report(report)


def call_upload_api(uploaded_file) -> dict | None:
    files = {
        "file": (
            uploaded_file.name,
            uploaded_file.getvalue(),
            uploaded_file.type or "application/octet-stream",
        )
    }
    try:
        resp = requests.post(f"{API_BASE}/upload", files=files, timeout=120)
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

    tab1, tab2, tab3, tab4 = st.tabs(
        ["合同信息", "测试步骤结论", "合规明细", "对手方信息"]
    )

    with tab1:
        render_contract_tab(contract)

    with tab2:
        render_audit_program_tab(audit)

    with tab3:
        render_compliance_tab(compliance)

    with tab4:
        render_counterparty_tab(counterparty)

    with st.expander("to_downstream_json（下游三单匹配接口预览）", expanded=False):
        st.json(report.get("to_downstream_json") or {})

    st.divider()
    st.caption(
        f"报告ID: `{report.get('report_id', '-')}` ｜ "
        f"生成时间: `{report.get('generated_at', '-')}` ｜ "
        f"{report.get('human_judgment_summary', '')}"
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
