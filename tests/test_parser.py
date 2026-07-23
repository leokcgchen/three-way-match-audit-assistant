"""合同解析模块单元测试（纯文本模拟，不依赖真实文件）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.parsers import ContractParser


SAMPLE_CONTRACT = """
软件开发服务合同
合同编号：HT-2026-001
合同名称：软件开发服务合同
甲方：上海示例科技有限公司
乙方：北京合规审阅有限公司
签订日期：2026-03-15
合同金额：500万元
总价：不适用
一、乙方应向甲方提供软件开发服务，并按约定交付产品。
二、货物验收合格后，控制权转移至甲方。
三、本合同收入按时点法确认。
"""


SAMPLE_AMOUNT_IN_YUAN = """
采购合同
合同编号：CG-2026-88
甲方：测试甲方
乙方：测试乙方
签订日期：2026/01/08
合同金额：5,000,000元
乙方负责交付设备，并提供安装服务。
验收合格后完成控制权转移。
"""


def test_extract_basic_fields() -> None:
    parser = ContractParser()
    info = parser.extract(SAMPLE_CONTRACT)

    assert info.contract_id == "HT-2026-001"
    assert info.contract_title == "软件开发服务合同"
    assert info.signing_date == "2026-03-15"
    assert len(info.parties) == 2
    assert info.parties[0].name == "上海示例科技有限公司"
    assert info.parties[1].name == "北京合规审阅有限公司"
    assert info.total_contract_amount == 500.0
    assert info.revenue_recognition_point == "时点"
    assert info.control_transfer_time is not None
    assert "验收合格后" in info.control_transfer_time
    assert len(info.performance_obligations) >= 1
    assert info.raw_text_preview is not None
    assert len(info.raw_text_preview) <= 500
    print("test_extract_basic_fields: PASS")


def test_amount_convert_from_yuan() -> None:
    parser = ContractParser()
    info = parser.extract(SAMPLE_AMOUNT_IN_YUAN)

    assert info.total_contract_amount == 500.0
    assert info.contract_id == "CG-2026-88"
    print("test_amount_convert_from_yuan: PASS")


def test_missing_fields_safe() -> None:
    parser = ContractParser()
    info = parser.extract("这是一段没有任何合同字段的文本。")

    assert info.contract_id is None
    assert info.signing_date is None
    assert info.parties == []
    assert info.total_contract_amount is None
    assert info.performance_obligations == []
    assert info.raw_text_preview is not None
    print("test_missing_fields_safe: PASS")


if __name__ == "__main__":
    test_extract_basic_fields()
    test_amount_convert_from_yuan()
    test_missing_fields_safe()
    print("全部测试通过：合同解析模块基本功能正常。")
