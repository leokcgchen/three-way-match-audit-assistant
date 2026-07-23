"""企业工商数据获取抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.contract_models import CompanyProfile


class CompanyDataFetcher(ABC):
    """对手方数据获取基类，真实 API 与 Mock 均实现此接口。"""

    @abstractmethod
    def fetch(self, company_name: str) -> CompanyProfile:
        """根据企业名称获取工商画像。"""

    @abstractmethod
    def get_data_source(self) -> str:
        """返回当前数据源标识，如 MOCK / QCC_API。"""
