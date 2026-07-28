"""Streamlit 批量审阅模式页面。"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from src.agent import ContractComplianceAgent
from src.parsers import ContractParser
from src.rules.batch_cutoff import batch_cutoff_check, export_cutoff_excel
from src.utils.date_extractor import (
    extract_contract_id_from_row,
    extract_contract_id_from_text,
    extract_date_from_text,
    is_date_column_candidate,
)
from src.ui.ledger_import import (
    AMOUNT_KEYS,
    CUSTOMER_KEYS,
    DATE_KEYS,
    DATE_PLACEHOLDER,
    ID_KEYS,
    PAYMENT_KEYS,
    QTY_KEYS,
    RECEIPT_DATE_KEYS,
    clear_bad_date_selection,
    default_index,
    extract_payment_days_from_text,
    guess_column,
    guess_date_column,
    is_likely_date_series,
    list_date_candidate_columns,
    parse_amount_series,
    parse_date_series,
    parse_ledger_file,
    truncate_samples,
)

OPTIONAL_NONE = "（不选择）"


def render_batch_mode() -> None:
    st.subheader("批量审阅模式")
    st.caption("上传序时账 + 签收单，按合同编号自动匹配并批量执行截止性测试。")

    ledger_df, ledger_count = _upload_and_map_ledger()
    receipt_df, receipt_count = _upload_and_map_receipt()
    payment_days = _resolve_payment_days(receipt_df)

    st.session_state["batch_ledger_count"] = ledger_count
    st.session_state["batch_receipt_count"] = receipt_count

    if ledger_df is not None and receipt_df is not None and not receipt_df.empty:
        led_keys = set(ledger_df["合同编号"].astype(str).str.strip().str.upper())
        rec_keys = set(receipt_df["合同编号"].astype(str).str.strip().str.upper())
        overlap = led_keys & rec_keys
        st.caption(
            f"匹配预检：序时账 {len(led_keys)} 个合同编号，签收单 {len(rec_keys)} 个，"
            f"可对齐 {len(overlap)} 个。"
            + (
                " ⚠️ 当前为 0，测试结果会大量显示「无签收单」——请确认两侧「合同编号列」选的是同一业务字段。"
                if not overlap
                else ""
            )
        )
        if overlap:
            sample = "、".join(sorted(list(overlap))[:3])
            st.caption(f"可对齐样例：{sample}")
        elif led_keys and rec_keys:
            st.caption(
                "序时账样例："
                + "、".join(sorted(list(led_keys))[:3])
                + " ｜ 签收单样例："
                + "、".join(sorted(list(rec_keys))[:3])
            )

    if st.button("批量执行截止性测试", type="primary"):
        if ledger_df is None or ledger_df.empty:
            st.error("请先上传并完成序时账列映射。")
            return
        if payment_days is None:
            st.error("请设置账期天数（选项 A/B/C）。")
            return
        if receipt_df is None or receipt_df.empty:
            st.error("请先上传并完成签收单列映射（含日期提取预览确认）。")
            return
        extract_flag = bool(st.session_state.get("extract_date_from_text", False))
        extract_contract = bool(
            st.session_state.get("extract_contract_from_text", False)
        )
        contract_text_cols = st.session_state.get("contract_text_columns") or []
        # 若映射结果里已是标准日期，不再二次“当文本提取”；但仍保留状态列
        already_extracted = (
            "date_extract_status" in receipt_df.columns
            and extract_flag
        )
        try:
            result = batch_cutoff_check(
                ledger_df=ledger_df,
                receipt_df=receipt_df,
                payment_days=int(payment_days),
                match_key="合同编号",
                extract_date_from_text=extract_flag and not already_extracted,
                receipt_date_column="receipt_date",
                extract_contract_from_text=extract_contract,
                contract_text_columns=list(contract_text_cols) if extract_contract else None,
            )
        except Exception as exc:
            st.error(f"批量执行失败: {exc}")
            return
        st.session_state["batch_cutoff_result"] = result
        st.session_state["batch_contract_source"] = (
            "文本提取" if extract_contract else "独立列"
        )
        st.success(f"批量截止性测试完成，共 {len(result)} 条结果。")

    result = st.session_state.get("batch_cutoff_result")
    if result is None or not isinstance(result, pd.DataFrame):
        return

    _render_batch_stats(result)
    # 结果表：轨迹列过长，展示时截断提示
    display = result.copy()
    if "计算轨迹" in display.columns:
        display["计算轨迹"] = display["计算轨迹"].map(
            lambda x: (str(x)[:80] + "…") if isinstance(x, str) and len(str(x)) > 80 else x
        )
    st.dataframe(display, use_container_width=True, hide_index=True)
    _render_calculation_trail_viewer(result)
    _render_export_button(result)


def _upload_and_map_ledger() -> tuple[Optional[pd.DataFrame], int]:
    st.markdown("#### 1. 序时账上传")
    st.caption("支持格式：Excel (.xlsx/.xls)、CSV、JSONL (.jsonl)")
    file = st.file_uploader(
        "上传序时账 Excel/CSV/JSONL",
        type=["xlsx", "xls", "csv", "jsonl"],
        key="batch_ledger_file",
    )
    if file is None:
        return None, 0
    try:
        df = parse_ledger_file(file)
    except Exception as exc:
        st.error(f"序时账读取失败: {exc}")
        return None, 0

    st.caption(f"已读取 {len(df)} 行")
    st.dataframe(df.head(5), use_container_width=True, hide_index=True)

    file_sig = f"{file.name}:{getattr(file, 'size', len(df))}"
    if st.session_state.get("batch_ledger_file_sig") != file_sig:
        st.session_state["batch_ledger_file_sig"] = file_sig
        for k in (
            "batch_ledger_id",
            "batch_ledger_date",
            "batch_ledger_amount",
            "batch_ledger_customer",
            "extract_contract_from_text",
            "contract_text_columns",
        ):
            st.session_state.pop(k, None)

    cols = [str(c) for c in df.columns]
    optional = [OPTIONAL_NONE] + cols
    guess_id = guess_column(cols, ID_KEYS)
    guess_date = guess_date_column(df, DATE_KEYS, exclude=[guess_id] if guess_id else None)
    guess_amount = guess_column(cols, AMOUNT_KEYS)
    guess_customer = guess_column(cols, CUSTOMER_KEYS)
    clear_bad_date_selection(df, "batch_ledger_date", st.session_state)
    date_options = [DATE_PLACEHOLDER] + cols
    c1, c2 = st.columns(2)
    with c1:
        id_col = st.selectbox(
            "合同编号列",
            cols,
            index=default_index(cols, guess_id),
            key="batch_ledger_id",
        )
        extract_contract = st.checkbox(
            "🔍 合同编号藏在文本描述中（如摘要/备注），需从中提取",
            key="extract_contract_from_text",
        )
        date_col = st.selectbox(
            "入账日期列",
            date_options,
            index=default_index(
                date_options, guess_date, df=df, prefer_date=True
            ),
            key="batch_ledger_date",
        )
    with c2:
        amount_col = st.selectbox(
            "金额列",
            cols,
            index=default_index(cols, guess_amount),
            key="batch_ledger_amount",
        )
        customer_col = st.selectbox(
            "客户名称列（可选）",
            optional,
            index=default_index(optional, guess_customer) if guess_customer else 0,
            key="batch_ledger_customer",
        )

    text_col_defaults = [
        c
        for c in cols
        if any(k in c for k in ("摘要", "备注", "说明", "描述", "销售订单", "订单号"))
    ]
    contract_text_cols: list[str] = []
    if extract_contract:
        contract_text_cols = st.multiselect(
            "请选择包含合同编号的文本列（可多选，按顺序尝试提取）",
            options=cols,
            default=text_col_defaults or ([id_col] if id_col else []),
            key="contract_text_columns",
        )
        if not contract_text_cols:
            st.warning("请至少选择一列作为合同编号文本来源（如「摘要」）。")
            return None, len(df)

        preview = df.head(5).copy()
        preview["提取后合同编号预览"] = preview.apply(
            lambda r: extract_contract_id_from_row(r, contract_text_cols)
            or "❌ 未提取到",
            axis=1,
        )
        st.markdown("##### 合同编号提取预览（前5行）")
        st.dataframe(preview, use_container_width=True, hide_index=True)
        extracted_all = df.apply(
            lambda r: extract_contract_id_from_row(r, contract_text_cols), axis=1
        )
        ok_n = int(extracted_all.notna().sum())
        st.caption(f"预提取统计：成功 {ok_n} 条，失败 {len(df) - ok_n} 条")

    if not extract_contract:
        if not id_col or df[id_col].isna().all():
            st.warning("合同编号列无效或全为空。")
            return None, len(df)

    if date_col == DATE_PLACEHOLDER or date_col not in df.columns:
        candidates = list_date_candidate_columns(df)
        tip = f"可选手：{', '.join(candidates)}" if candidates else "当前文件未检测到明显日期列，请确认文件是否包含入账日期字段"
        st.info(f"请选择「入账日期列」。{tip}")
        return None, len(df)

    if not is_likely_date_series(df[date_col]):
        sample = truncate_samples(df[date_col].dropna().astype(str).head(3).tolist())
        candidates = list_date_candidate_columns(df)
        st.warning(
            f"「入账日期列」当前为「{date_col}」，内容不像日期（样例: {sample}）。"
            + (f"建议改选：{', '.join(candidates)}" if candidates else "请改选真正的日期列。")
        )
        return None, len(df)

    try:
        if extract_contract:
            extracted_ids = df.apply(
                lambda r: extract_contract_id_from_row(r, contract_text_cols), axis=1
            )
            std = pd.DataFrame(
                {
                    # 占位；执行时 batch_cutoff 会按文本列再提取并覆盖
                    "合同编号": extracted_ids.fillna("").astype(str),
                    "entry_date": parse_date_series(df[date_col], date_col),
                    "entry_amount": parse_amount_series(df[amount_col], amount_col),
                    "提取后合同编号": extracted_ids,
                    "contract_id_extract_status": extracted_ids.map(
                        lambda x: "SUCCESS" if pd.notna(x) and str(x).strip() else "FAIL"
                    ),
                }
            )
            for col in contract_text_cols:
                std[col] = df[col].values
        else:
            std = pd.DataFrame(
                {
                    "合同编号": df[id_col].astype(str).str.strip(),
                    "entry_date": parse_date_series(df[date_col], date_col),
                    "entry_amount": parse_amount_series(df[amount_col], amount_col),
                }
            )
        if customer_col != OPTIONAL_NONE:
            std["customer_name"] = df[customer_col]
    except Exception as exc:
        st.error(f"序时账列映射/校验失败: {exc}")
        return None, len(df)

    return std, len(std)


def _upload_and_map_receipt() -> tuple[Optional[pd.DataFrame], int]:
    st.markdown("#### 2. 签收单上传")
    st.caption("支持格式：Excel (.xlsx/.xls)、CSV、JSONL (.jsonl)")
    file = st.file_uploader(
        "上传签收单 Excel/CSV/JSONL",
        type=["xlsx", "xls", "csv", "jsonl"],
        key="batch_receipt_file",
    )
    if file is None:
        return None, 0
    try:
        df = parse_ledger_file(file)
    except Exception as exc:
        st.error(f"签收单读取失败: {exc}")
        return None, 0

    st.caption(f"已读取 {len(df)} 行")
    st.dataframe(df.head(5), use_container_width=True, hide_index=True)

    file_sig = f"{file.name}:{getattr(file, 'size', len(df))}"
    if st.session_state.get("batch_receipt_file_sig") != file_sig:
        st.session_state["batch_receipt_file_sig"] = file_sig
        for k in (
            "batch_receipt_id",
            "batch_receipt_date",
            "batch_receipt_qty",
            "batch_receipt_payment",
            "extract_date_from_text",
        ):
            st.session_state.pop(k, None)

    cols = [str(c) for c in df.columns]
    optional = [OPTIONAL_NONE] + cols
    guess_id = guess_column(cols, ID_KEYS)
    guess_date = guess_date_column(
        df, RECEIPT_DATE_KEYS, exclude=[guess_id] if guess_id else None
    )
    guess_qty = guess_column(cols, QTY_KEYS)
    guess_pay = guess_column(cols, PAYMENT_KEYS)

    extract_mode = bool(st.session_state.get("extract_date_from_text", False))
    if not extract_mode:
        clear_bad_date_selection(df, "batch_receipt_date", st.session_state)

    # 文本列智能提示：优先备注/摘要类
    text_hint_col = None
    for key in ("签收备注", "备注", "合同PDF摘要", "摘要", "说明", "描述"):
        hit = guess_column(cols, (key,))
        if hit and is_date_column_candidate(df[hit].tolist()):
            text_hint_col = hit
            break
    if text_hint_col is None:
        for col in cols:
            if is_date_column_candidate(df[col].tolist()) and not is_likely_date_series(
                df[col]
            ):
                text_hint_col = col
                break

    date_options = [DATE_PLACEHOLDER] + cols
    default_date = guess_date or (text_hint_col if extract_mode else None)
    c1, c2 = st.columns(2)
    with c1:
        id_col = st.selectbox(
            "合同编号列",
            cols,
            index=default_index(cols, guess_id),
            key="batch_receipt_id",
        )
        date_col = st.selectbox(
            "签收日期列",
            date_options,
            index=default_index(
                date_options, default_date, df=df, prefer_date=not extract_mode
            ),
            key="batch_receipt_date",
        )
        extract_mode = st.checkbox(
            "🔍 此列包含文本描述,需从中提取日期",
            key="extract_date_from_text",
        )
        if text_hint_col and not guess_date:
            st.caption(
                f"提示：列「{text_hint_col}」中疑似含日期文本，可勾选上方提取选项。"
            )
    with c2:
        qty_col = st.selectbox(
            "签收数量列（可选）",
            optional,
            index=default_index(optional, guess_qty) if guess_qty else 0,
            key="batch_receipt_qty",
        )
        payment_col = st.selectbox(
            "账期列（可选，天数）",
            optional,
            index=default_index(optional, guess_pay) if guess_pay else 0,
            key="batch_receipt_payment",
        )

    # 若摘要里写了「签收后N日」，提示可用于账期
    summary_col = guess_column(cols, ("摘要", "summary", "pdf", "备注"))
    if summary_col:
        sniffed = None
        for val in df[summary_col].dropna().astype(str).head(5):
            sniffed = extract_payment_days_from_text(val)
            if sniffed is not None:
                break
        if sniffed is not None:
            st.caption(
                f"检测到摘要中含「签收后{sniffed}日」，账期可在下方选项B手动填 {sniffed}。"
            )

    if date_col == DATE_PLACEHOLDER or date_col not in df.columns:
        candidates = list_date_candidate_columns(df)
        tip = (
            f"可选手：{', '.join(candidates)}"
            if candidates
            else "也可选择文本列并勾选「需从中提取日期」"
        )
        st.info(f"请选择「签收日期列」。{tip}")
        return None, len(df)

    # 提取模式：预览提取结果，并把提取后的日期写入映射结果（供测试直接使用）
    if extract_mode:
        preview = df.head(5).copy()
        preview["提取后日期预览"] = preview[date_col].map(
            lambda v: extract_date_from_text(None if pd.isna(v) else str(v))
            or "❌ 未提取到日期"
        )
        st.markdown("##### 日期提取预览（前5行）")
        st.dataframe(preview, use_container_width=True, hide_index=True)

        extracted_series = df[date_col].map(
            lambda v: extract_date_from_text(None if pd.isna(v) else str(v))
        )
        ok_n = int(extracted_series.notna().sum())
        fail_n = int(len(df) - ok_n)
        st.caption(f"预提取统计：成功 {ok_n} 条，失败 {fail_n} 条")

        std = pd.DataFrame(
            {
                "合同编号": df[id_col].astype(str).str.strip(),
                # 直接写入提取后的标准日期，避免“预览成功但执行未带上”
                "receipt_date": extracted_series,
                "date_extract_status": extracted_series.map(
                    lambda x: "SUCCESS" if pd.notna(x) else "FAIL"
                ),
                "receipt_date_raw": df[date_col],
            }
        )
        if qty_col != OPTIONAL_NONE:
            std["received_quantity"] = pd.to_numeric(df[qty_col], errors="coerce")
        if payment_col != OPTIONAL_NONE:
            std["payment_days"] = pd.to_numeric(df[payment_col], errors="coerce")
        return std, len(std)

    if not is_likely_date_series(df[date_col]):
        sample = truncate_samples(df[date_col].dropna().astype(str).head(3).tolist())
        candidates = list_date_candidate_columns(df)
        st.warning(
            f"「签收日期列」当前为「{date_col}」，内容不像日期（样例: {sample}）。"
            + (
                f"建议改选：{', '.join(candidates)}；"
                if candidates
                else ""
            )
            + "或勾选「🔍 此列包含文本描述,需从中提取日期」。"
        )
        return None, len(df)

    try:
        std = pd.DataFrame(
            {
                "合同编号": df[id_col].astype(str).str.strip(),
                "receipt_date": parse_date_series(df[date_col], date_col),
            }
        )
        if qty_col != OPTIONAL_NONE:
            std["received_quantity"] = pd.to_numeric(df[qty_col], errors="coerce")
        if payment_col != OPTIONAL_NONE:
            std["payment_days"] = pd.to_numeric(df[payment_col], errors="coerce")
    except Exception as exc:
        st.error(f"签收单列映射/校验失败（请检查合同编号与签收日期列）: {exc}")
        return None, len(df)

    return std, len(std)


def _resolve_payment_days(receipt_df: Optional[pd.DataFrame]) -> Optional[int]:
    st.markdown("#### 3. 合同账期设置")
    mode = st.radio(
        "账期来源",
        options=["选项B：手动输入统一账期", "选项A：从合同文件提取", "选项C：从签收单账期列读取"],
        key="batch_payment_mode",
    )

    if mode.startswith("选项B"):
        days = st.number_input(
            "统一账期天数（签收后N日）",
            min_value=0,
            value=10,
            step=1,
            key="batch_manual_payment_days",
        )
        return int(days)

    if mode.startswith("选项A"):
        contract_file = st.file_uploader(
            "上传合同 PDF/Word 以提取账期",
            type=["pdf", "docx"],
            key="batch_contract_for_payment",
        )
        if contract_file is None:
            return None
        try:
            suffix = Path(contract_file.name).suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(contract_file.getvalue())
                tmp_path = tmp.name
            info = ContractParser().parse(tmp_path)
            days = ContractComplianceAgent()._extract_payment_days(info)
            if days is None:
                st.warning("未能从合同中提取账期，请改用手动输入。")
                return None
            st.info(f"已从合同提取账期：签收/交付/验收后 {days} 日")
            return int(days)
        except Exception as exc:
            st.error(f"合同账期提取失败: {exc}")
            return None

    # 选项C
    if receipt_df is None or "payment_days" not in receipt_df.columns:
        st.warning("签收单未映射账期列，无法使用选项C。")
        return None
    series = receipt_df["payment_days"].dropna()
    if series.empty:
        st.warning("签收单账期列为空。")
        return None
    days = int(series.mode().iloc[0]) if not series.mode().empty else int(series.iloc[0])
    st.info(f"使用签收单账期列众数/首值：{days} 日")
    return days


def _render_batch_stats(result: pd.DataFrame) -> None:
    st.markdown("#### 4. 结果统计")
    counts = result["cutoff_status"].value_counts().to_dict()
    total = len(result)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总记录数", total)
    c2.metric("PASS", counts.get("PASS", 0))
    c3.metric("WARNING", counts.get("WARNING", 0))
    c4.metric("FAIL（高风险）", counts.get("FAIL", 0))
    c5.metric("无签收单", counts.get("NO_RECEIPT", 0))

    if "date_extract_status" in result.columns:
        extract_counts = result["date_extract_status"].value_counts().to_dict()
        success_n = int(extract_counts.get("SUCCESS", 0))
        fail_n = int(extract_counts.get("FAIL", 0))
        if success_n or fail_n:
            st.info(f"📅 日期自动提取: 成功 {success_n} 条, 失败 {fail_n} 条")

    if "contract_id_extract_status" in result.columns:
        cid_counts = result["contract_id_extract_status"].value_counts().to_dict()
        ok_n = int(cid_counts.get("SUCCESS", 0))
        bad_n = int(cid_counts.get("FAIL", 0))
        if ok_n or bad_n:
            st.info(f"📄 合同编号提取: 成功 {ok_n} 条，失败 {bad_n} 条")
            st.session_state["batch_contract_extract_ok"] = ok_n
            st.session_state["batch_contract_extract_fail"] = bad_n
    source = st.session_state.get("batch_contract_source", "独立列")
    if source == "文本提取":
        ok_n = st.session_state.get("batch_contract_extract_ok", 0)
        st.caption(f"合同编号来源: 文本提取（成功 {ok_n} 条）")
    else:
        st.caption("合同编号来源: 独立列")


def _render_calculation_trail_viewer(result: pd.DataFrame) -> None:
    """选择一条结果并展开 calculation_trail。"""
    if "计算轨迹" not in result.columns or result.empty:
        return
    st.markdown("##### 计算过程展开")
    labels = []
    for i, row in result.iterrows():
        cid = row.get("合同编号", i)
        status = row.get("cutoff_status", "")
        labels.append(f"#{i} | {cid} | {status}")
    choice = st.selectbox("选择记录查看计算过程", labels, key="batch_trail_row_select")
    if not choice:
        return
    idx = labels.index(choice)
    row = result.iloc[idx]
    raw = row.get("计算轨迹")
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        st.info("该记录无计算轨迹。")
        return
    import json

    try:
        trail = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        st.warning("计算轨迹解析失败。")
        st.code(str(raw))
        return
    if st.button("展开计算过程", key="batch_expand_trail_btn"):
        st.session_state["batch_trail_expanded"] = True
    if not st.session_state.get("batch_trail_expanded"):
        st.caption("点击上方按钮展开逐步计算依据。")
        return

    for step in trail:
        action = step.get("action", "")
        step_no = step.get("step", "")
        has_error = bool(step.get("error"))
        has_formula = step.get("formula") is not None
        if has_error:
            color = "#dc2626"  # 红：结论/错误
            kind = "错误/结论"
        elif has_formula or action.startswith("计算") or action.startswith("判断"):
            color = "#15803d" if "判断" in action else "#16a34a"  # 绿：计算
            kind = "计算" if "判断" not in action else "结论"
            if "判断" in action:
                color = "#dc2626"
        else:
            color = "#2563eb"  # 蓝：输入
            kind = "输入"
        if "判断" in action:
            color = "#dc2626"
            kind = "结论"
        st.markdown(
            f"<div style='border-left:4px solid {color};padding:8px 12px;margin:6px 0;"
            f"background:#f8fafc;'>"
            f"<b style='color:{color};'>Step {step_no} · {kind}</b> — {action}<br/>"
            f"<span style='color:#64748b;font-size:0.9em;'>"
            f"input={step.get('input')!r} ｜ formula={step.get('formula')!r} ｜ "
            f"output={step.get('output')!r}"
            + (f" ｜ error={step.get('error')!r}" if has_error else "")
            + "</span></div>",
            unsafe_allow_html=True,
        )


def _render_export_button(result: pd.DataFrame) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"截止性测试结果_{stamp}.xlsx"
    out_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    if st.button("导出结果Excel"):
        export_cutoff_excel(result, str(out_path))
        st.session_state["batch_export_path"] = str(out_path)

    export_path = st.session_state.get("batch_export_path")
    if export_path and Path(export_path).exists():
        with open(export_path, "rb") as f:
            st.download_button(
                label="下载导出的 Excel",
                data=f.read(),
                file_name=Path(export_path).name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
