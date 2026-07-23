"""Mock 企业工商数据，与 fetcher 分离，便于后续替换数据源。"""

from __future__ import annotations

from typing import Dict

from src.models.contract_models import CompanyProfile

# key 使用简称，便于模糊匹配；company_name 存放带主体后缀的全称
MOCK_COMPANIES: Dict[str, CompanyProfile] = {
    "云创科技": CompanyProfile(
        company_name="上海云创科技有限公司",
        registration_status="存续",
        business_scope="计算机软件开发；信息技术咨询；云计算服务；数据处理。",
        legal_representative="陈思远",
        registered_capital="5000万人民币",
        establishment_date="2016-05-18",
        is_abnormal=False,
        is_blacklisted=False,
        litigation_risk_summary=None,
        data_source="MOCK",
    ),
    "恒达设备": CompanyProfile(
        company_name="苏州恒达设备制造有限公司",
        registration_status="存续",
        business_scope="机械设备制造；工业自动化设备销售；机电安装工程。",
        legal_representative="刘建国",
        registered_capital="2000万人民币",
        establishment_date="2012-11-03",
        is_abnormal=True,
        is_blacklisted=False,
        litigation_risk_summary="因未按期公示年报被列入经营异常名录",
        data_source="MOCK",
    ),
    "远航贸易": CompanyProfile(
        company_name="深圳远航贸易有限公司",
        registration_status="存续",
        business_scope="进出口贸易；电子产品销售；供应链管理服务。",
        legal_representative="王海波",
        registered_capital="1000万人民币",
        establishment_date="2018-07-22",
        is_abnormal=False,
        is_blacklisted=False,
        litigation_risk_summary="2025年合同纠纷1起",
        data_source="MOCK",
    ),
    "盛达建筑": CompanyProfile(
        company_name="杭州盛达建筑工程有限公司",
        registration_status="吊销",
        business_scope="房屋建筑工程施工；市政工程；建筑装修装饰工程。",
        legal_representative="赵明辉",
        registered_capital="3000万人民币",
        establishment_date="2009-03-10",
        is_abnormal=False,
        is_blacklisted=False,
        litigation_risk_summary="因未按期年检被依法吊销营业执照",
        data_source="MOCK",
    ),
    "智汇数据": CompanyProfile(
        company_name="北京智汇数据科技股份有限公司",
        registration_status="存续",
        business_scope="大数据分析；人工智能算法研发；数据安全服务；软件销售。",
        legal_representative="周婉清",
        registered_capital="8000万人民币",
        establishment_date="2015-09-28",
        is_abnormal=False,
        is_blacklisted=False,
        litigation_risk_summary=None,
        data_source="MOCK",
    ),
    "鑫源制造": CompanyProfile(
        company_name="天津鑫源精密制造有限公司",
        registration_status="存续",
        business_scope="精密零部件制造；模具加工；金属材料销售。",
        legal_representative="孙志强",
        registered_capital="1500万人民币",
        establishment_date="2011-12-15",
        is_abnormal=False,
        is_blacklisted=True,
        litigation_risk_summary="因严重违法失信被列入黑名单",
        data_source="MOCK",
    ),
}


def build_unknown_profile(company_name: str) -> CompanyProfile:
    """公司名称未命中时的默认画像。"""
    return CompanyProfile(
        company_name=company_name,
        registration_status="未知",
        business_scope=None,
        legal_representative=None,
        registered_capital=None,
        establishment_date=None,
        is_abnormal=False,
        is_blacklisted=False,
        litigation_risk_summary="未在Mock库中检索到该企业信息",
        data_source="MOCK",
    )
