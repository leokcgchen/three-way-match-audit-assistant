"""四类合同清晰性规则（对齐实施手册 §7）：歧义→WARNING，本集不因条款不清输出FAIL。"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from src.contract_terms.models import ContractClarityIssue, TestDimension


def _clip(text: str, limit: int = 160) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def _find(text: str, pattern: str, flags: int = re.I) -> Optional[str]:
    m = re.search(pattern, text or "", flags=flags)
    if not m:
        return None
    start = max(0, m.start() - 30)
    end = min(len(text), m.end() + 80)
    return _clip(text[start:end])


def _has(text: str, pattern: str, flags: int = re.I) -> bool:
    return bool(re.search(pattern, text or "", flags=flags))


def _negated_around(text: str, keyword_pat: str) -> bool:
    for m in re.finditer(keyword_pat, text or "", flags=re.I):
        left = text[max(0, m.start() - 28) : m.start()]
        if re.search(r"(不包含|不含|不涉及|不承担|不得|无需|不存在|无|未|不另|不属于)", left):
            return True
    return False


def evaluate_consideration(text: str) -> List[ContractClarityIssue]:
    issues: List[ContractClarityIssue] = []
    if _has(text, r"随行就市") and not _has(
        text, r"(市场指数|价格指数|基准指数|调整公式|计算公式)"
    ):
        issues.append(
            ContractClarityIssue(
                issue_code="CONSIDERATION_FORMULA_AMBIGUOUS",
                dimension="交易对价",
                description="合同采用随行就市结算，但未指定市场指数、价格确定日、调整公式及确认程序，无法唯一确定交易价格形成机制。",
                excerpt=_find(text, r"随行就市") or "",
            )
        )
    if _has(text, r"(原材料|材料成本|成本).{0,30}(协商|商议).{0,20}(调价|调整|价格)") or _has(
        text, r"协商.{0,10}调整.{0,10}产品价格"
    ):
        if not _has(text, r"(市场指数|价格指数|基准指数|调整公式|计算公式|变动阈值)"):
            issues.append(
                ContractClarityIssue(
                    issue_code="VARIABLE_CONSIDERATION_UNRESOLVED",
                    dimension="交易对价",
                    description="合同约定原材料价格波动时可协商调价，但未明确阈值、数据来源、未决期间执行价格及调整公式。",
                    excerpt=_find(text, r"(协商|商议).{0,20}(调价|调整|价格)") or "",
                )
            )
    if _has(text, r"返利") and not (
        _negated_around(text, r"返利")
        or _has(text, r"(不包含|不含|不存在|无).{0,24}返利")
    ):
        has_rate_and_base = _has(
            text, r"返利.{0,80}(\d+(?:\.\d+)?\s*%).{0,80}(采购|销售|回款|订单)"
        ) or _has(text, r"(\d+(?:\.\d+)?\s*%).{0,40}返利")
        has_settlement = _has(text, r"返利.{0,60}(兑现|抵扣|现金|结算方式)")
        if not (has_rate_and_base and has_settlement):
            issues.append(
                ContractClarityIssue(
                    issue_code="REBATE_TERM_AMBIGUOUS",
                    dimension="交易对价",
                    description="合同提及返利，但返利比例、计算基础、兑现方式或追溯范围不明确。",
                    excerpt=_find(text, r"返利") or "",
                )
            )
    if _has(
        text, r"(开票前|开具发票前).{0,40}(有权|可以|可).{0,20}(调整|变更).{0,20}(价格|价款)"
    ) or _has(text, r"未提出异议.{0,12}视为接受"):
        issues.append(
            ContractClarityIssue(
                issue_code="UNILATERAL_PRICE_ADJUSTMENT",
                dimension="交易对价",
                description="合同允许卖方在开票前单方调价，或以买方未提出异议视为接受，价格变更程序不具有双方确认的可执行性。",
                excerpt=_find(text, r"(开票前|未提出异议)") or "",
            )
        )
    return issues


def evaluate_payment(text: str) -> List[ContractClarityIssue]:
    issues: List[ContractClarityIssue] = []
    if _has(text, r"货到付款"):
        if not _has(
            text,
            r"(货到|到货|签收|验收).{0,30}\d+\s*(个)?(自然|日历|工作)?[日天]",
        ) and not _has(text, r"\d+\s*(个)?(自然|日历|工作)?[日天].{0,20}(付款|支付|结清)"):
            issues.append(
                ContractClarityIssue(
                    issue_code="PAYMENT_DUE_DATE_UNDEFINED",
                    dimension="支付条款",
                    description="合同仅约定货到付款，未明确付款起算节点与可计算的付款期限。",
                    excerpt=_find(text, r"货到付款") or "",
                )
            )
    if _has(text, r"及时结清|及时清偿|及时付清|及时支付") or _has(text, r"适当顺延|酌情顺延"):
        issues.append(
            ContractClarityIssue(
                issue_code="PAYMENT_PERIOD_AMBIGUOUS",
                dimension="支付条款",
                description="付款期限表述为及时结清或可按内部审批顺延，缺少可计算的起算事件与确定天数。",
                excerpt=_find(text, r"及时结清|及时清偿|及时付清|适当顺延|酌情顺延") or "",
            )
        )
    if _has(text, r"(首款|预付款|定金).{0,80}(尾款|余款)") or _has(
        text, r"(分期|首期|尾款).{0,40}(支付|付款)"
    ):
        if (
            _has(text, r"(另行通知|后续通知|待通知|另行确定|后续确定)")
            and _has(text, r"首款|尾款|预付款")
            and not _has(text, r"\d+\s*%")
        ) or _has(
            text,
            r"(比例|金额|日期|节点).{0,40}(另行通知|后续通知|待通知)",
        ):
            issues.append(
                ContractClarityIssue(
                    issue_code="INSTALLMENT_TERM_UNDEFINED",
                    dimension="支付条款",
                    description="首款/尾款的比例、节点或付款日期待后续通知，分期支付条件不可执行。",
                    excerpt=_find(text, r"首款|尾款|预付款|另行通知|后续通知") or "",
                )
            )
    if _has(text, r"\bD\s*/\s*P\b|\bDP\b.{0,20}basis|付款交单"):
        if not _has(
            text,
            r"(即期|远期|sight|usance|\d+\s*(banking|银行)?\s*days?\b|\d+\s*个?(银行)?日)",
        ):
            issues.append(
                ContractClarityIssue(
                    issue_code="DP_TENOR_UNDEFINED",
                    dimension="支付条款",
                    description="出口采用D/P结算，但未说明即期、远期或提示后付款天数，付款到期日不可计算。",
                    excerpt=_find(text, r"D\s*/\s*P|DP|付款交单") or "",
                )
            )
    # OCR/摘录丢段时：正文较长却完全看不到支付用语 → 不得当 PASS
    if len(re.sub(r"\s+", "", text or "")) >= 180 and not _has(
        text,
        r"付款|支付|结清|结算|账期|货款|货到付款|信用证|发票.{0,12}(后|前).{0,12}(付|支付)|"
        r"\bL\s*/?\s*C\b|D\s*/\s*P|payment|payable|invoice",
    ):
        issues.append(
            ContractClarityIssue(
                issue_code="PAYMENT_TERMS_MISSING",
                dimension="支付条款",
                description="合同正文未见可识别的支付/结算条款，可能因OCR丢段或合同要件缺失，无法认定付款条件清晰。",
                excerpt="",
            )
        )
    return issues


def evaluate_performance(text: str) -> List[ContractClarityIssue]:
    issues: List[ContractClarityIssue] = []
    if _has(text, r"(installation|commissioning|familiarization|培训|安装|调试)"):
        clear_incidental = False
        if _has(text, r"sole promised output|唯一主要履约义务是交付") and _has(
            text, r"incident|附随|不单独"
        ):
            clear_incidental = True
        if _has(text, r"(No installation|不含安装|不承担安装|不包含安装)"):
            clear_incidental = True
        unclear_request = _has(
            text,
            r"(may request assistance|arrange any such assistance|可以要求|双方.*安排.*协助|另行.*安排.*(安装|调试|培训|assistance))",
        )
        if unclear_request and not clear_incidental:
            issues.append(
                ContractClarityIssue(
                    issue_code="PERFORMANCE_OBLIGATION_BOUNDARY_UNCLEAR",
                    dimension="履约义务",
                    description="合同将商品交付与安装、调试或培训协助混写，未清晰界定是否构成可区分的独立履约义务。",
                    excerpt=_find(
                        text,
                        r"installation|commissioning|familiarization|安装|调试|培训|assistance",
                    )
                    or "",
                )
            )
    # 软件/升级/持续支持：排除模板中的「不包含…持续技术支持」
    if _has(
        text,
        r"(may submit requests concerning updates|updates?,?\s*adjustments? or technical support)",
    ) or (
        _has(text, r"(功能升级|持续技术支持|持续.{0,4}支持)")
        and not _has(text, r"(不包含|不含|不承担).{0,40}(持续技术支持|功能升级|持续.{0,4}支持)")
    ):
        if not _has(text, r"(No .{0,40}feature-upgrade|不包含.{0,40}升级|仅承担标准缺陷)"):
            issues.append(
                ContractClarityIssue(
                    issue_code="SOFTWARE_SERVICE_BOUNDARY_UNCLEAR",
                    dimension="履约义务",
                    description="嵌入软件、缺陷修复、功能升级或持续技术支持的边界未区分，难以判断是否存在独立服务履约义务。",
                    excerpt=_find(
                        text,
                        r"updates?|technical support|功能升级|持续技术支持|calibration",
                    )
                    or "",
                )
            )
    if _has(text, r"\btooling\b|模具"):
        clear_tooling = _has(
            text,
            r"(production tool|生产工具|不出售|不交付给买方|tooling remains with|模具属于卖方|不属于交付标的)",
        )
        unclear_tooling = _has(
            text,
            r"(tooling (design|arrangement|ownership)|trial production|试制|模具.{0,20}(交付|验收|所有权|归属)|separate accept)",
        )
        if unclear_tooling and not clear_tooling:
            issues.append(
                ContractClarityIssue(
                    issue_code="TOOLING_AND_GOODS_BOUNDARY_UNCLEAR",
                    dimension="履约义务",
                    description="模具设计、试制与量产件供应是否分别交付及验收约定不清。",
                    excerpt=_find(text, r"tooling|模具|trial production|试制") or "",
                )
            )

    zh_neg = _has(text, r"(不包含|不含|不承担|不涉及).{0,16}(固定)?(驻场|待命)")
    zh_unclear = _has(text, r"是否驻场") or _has(
        text, r"(驻场人员|待命服务|专项改善).{0,48}(另行|书面确认|协商确定|按项目)"
    )
    if (zh_unclear or _has(text, r"(提供|安排|负责|包含).{0,12}(驻场|待命)")) and not zh_neg:
        issues.append(
            ContractClarityIssue(
                issue_code="STAND_READY_SERVICE_UNCLEAR",
                dimension="履约义务",
                description="一般质量沟通、持续驻场、待命服务或专项改善的边界不清，可能存在待命类服务承诺。",
                excerpt=_find(text, r"驻场|待命|专项改善|是否驻场") or "",
            )
        )

    en_positive = _has(
        text, r"(shall provide|will provide|includes?|undertake).{0,40}stand[-\s]?ready"
    )
    en_neg = _has(
        text,
        r"(No .{0,60}stand[-\s]?ready|does not create a stand[-\s]?ready|not included.{0,40}stand[-\s]?ready)",
    )
    if en_positive and not en_neg:
        issues.append(
            ContractClarityIssue(
                issue_code="STAND_READY_SERVICE_UNCLEAR",
                dimension="履约义务",
                description="一般质量沟通、持续驻场、待命服务或专项改善的边界不清，可能存在待命类服务承诺。",
                excerpt=_find(text, r"stand[-\s]?ready") or "",
            )
        )
    return issues


def evaluate_control_transfer(text: str) -> List[ContractClarityIssue]:
    issues: List[ContractClarityIssue] = []
    if _has(text, r"\bFOB\b"):
        carrier_done = _has(
            text,
            r"(handed over to the carrier|carrier takes cha|码头.{0,10}承运人.{0,10}接管|接管即完成交付|takes charge)",
        )
        if carrier_done:
            issues.append(
                ContractClarityIssue(
                    issue_code="CONTROL_TRANSFER_TRIGGER_CONFLICT",
                    dimension="运输及控制权转移",
                    description="合同同时使用FOB与码头承运人接管即完成交付等表述，装船日与接管日冲突，控制权节点不唯一。",
                    excerpt=_find(text, r"FOB|handed over to the carrier|takes charge|接管")
                    or "",
                )
            )
    if _has(text, r"\bCIF\b"):
        dest_confirm = _has(
            text,
            r"(handover condition|destination port|到港|目的港交接|confirmed by reference to the shipping documents and the handover)",
        )
        load_clear = _has(
            text,
            r"(control transfer occur when the goods are loaded on board|装船时转移|loaded on board.{0,40}control)",
        )
        if dest_confirm and not load_clear:
            issues.append(
                ContractClarityIssue(
                    issue_code="CIF_CONTROL_POINT_AMBIGUOUS",
                    dimension="运输及控制权转移",
                    description="CIF合同同时引用装船单据与目的港交接状态，未明确控制权在装船还是到港转移。",
                    excerpt=_find(text, r"CIF|handover|destination|到港|装船") or "",
                )
            )
    if _has(text, r"\bDAP\b"):
        vague_place = _has(
            text,
            r"(area designated by the Buyer|买方指定区域|买方指定地点|Buyer.?s (site|location)|买方地点)",
        )
        concrete = _has(
            text, r"DAP.{0,80}(\d+|Warehouse|仓库|Industrial|Road|Street|路|号)"
        ) and _has(text, r"(Incoterms|置于买方处置|placed at the Buyer)")
        if vague_place and not concrete:
            issues.append(
                ContractClarityIssue(
                    issue_code="DAP_DELIVERY_PLACE_UNDEFINED",
                    dimension="运输及控制权转移",
                    description="DAP仅写买方指定区域或买方地点，缺少完整地址、置于买方处置状态及授权签收要求。",
                    excerpt=_find(text, r"DAP|designated|指定区域|指定地点") or "",
                )
            )
    return issues


def evaluate_all_clarity_rules(text: str) -> List[ContractClarityIssue]:
    issues: List[ContractClarityIssue] = []
    for fn in (
        evaluate_consideration,
        evaluate_payment,
        evaluate_performance,
        evaluate_control_transfer,
    ):
        issues.extend(fn(text))
    seen = set()
    uniq: List[ContractClarityIssue] = []
    for it in issues:
        if it.issue_code in seen:
            continue
        seen.add(it.issue_code)
        uniq.append(it)
    return uniq


def primary_issue(
    issues: List[ContractClarityIssue],
) -> Tuple[Optional[str], TestDimension, str]:
    if not issues:
        return None, "无", "合同关键条款完整、可执行，可形成唯一审计判断"
    first = issues[0]
    return first.issue_code, first.dimension, first.description
