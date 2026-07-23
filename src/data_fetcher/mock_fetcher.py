"""Mock 企业数据获取实现。"""

from __future__ import annotations

from src.data_fetcher.base_fetcher import CompanyDataFetcher
from src.data_fetcher.mock_data import MOCK_COMPANIES, build_unknown_profile
from src.models.contract_models import CompanyProfile
from src.utils.logger import logger


class MockFetcher(CompanyDataFetcher):
    """基于内置 Mock 数据的企业信息查询器。"""

    def get_data_source(self) -> str:
        return "MOCK"

    def fetch(self, company_name: str) -> CompanyProfile:
        query = (company_name or "").strip()
        logger.info("MockFetcher 开始查询企业: {}", query)

        if not query:
            profile = build_unknown_profile(company_name or "")
            logger.info("查询名称为空，返回未知画像")
            return profile

        matched = self._fuzzy_match(query)
        if matched is None:
            profile = build_unknown_profile(query)
            logger.info("未命中 Mock 数据，返回未知画像: {}", query)
            return profile

        # 返回副本，避免调用方修改污染全局 Mock 数据
        profile = matched.model_copy(deep=True)
        logger.info(
            "命中企业: key_or_name={}, status={}, abnormal={}, blacklisted={}",
            profile.company_name,
            profile.registration_status,
            profile.is_abnormal,
            profile.is_blacklisted,
        )
        return profile

    def _fuzzy_match(self, query: str) -> CompanyProfile | None:
        """支持全称或简称模糊匹配；多命中时返回第一个。"""
        # 1) 精确匹配简称 key
        if query in MOCK_COMPANIES:
            return MOCK_COMPANIES[query]

        # 2) 精确匹配全称
        for profile in MOCK_COMPANIES.values():
            if query == profile.company_name:
                return profile

        # 3) 模糊：查询词包含简称/全称，或简称/全称包含查询词
        for short_name, profile in MOCK_COMPANIES.items():
            candidates = (short_name, profile.company_name)
            for candidate in candidates:
                if query in candidate or candidate in query:
                    return profile

        return None
