"""数据源工厂：按配置返回对应的 CompanyDataFetcher 实现。"""

from __future__ import annotations

from config.settings import settings
from src.data_fetcher.base_fetcher import CompanyDataFetcher
from src.data_fetcher.mock_fetcher import MockFetcher
from src.utils.logger import logger


def get_fetcher(mode: str | None = None) -> CompanyDataFetcher:
    """
    根据 mode 或 settings.DATA_SOURCE_MODE 返回数据获取器。

    当前仅实现 MOCK；QCC / WIND / EASTMONEY 预留分支。
    """
    resolved = (mode or settings.DATA_SOURCE_MODE or "MOCK").strip().upper()
    logger.info("初始化数据源 Fetcher, mode={}", resolved)

    if resolved == "MOCK":
        return MockFetcher()

    if resolved in {"QCC", "QCC_API"}:
        raise NotImplementedError("企查查(QCC)数据源尚未实现，请将 DATA_SOURCE_MODE 设为 MOCK")

    if resolved in {"WIND", "WIND_API"}:
        raise NotImplementedError("万得(WIND)数据源尚未实现，请将 DATA_SOURCE_MODE 设为 MOCK")

    if resolved in {"EASTMONEY", "EAST_MONEY", "EAST_MONEY_API"}:
        raise NotImplementedError(
            "东方财富(EASTMONEY)数据源尚未实现，请将 DATA_SOURCE_MODE 设为 MOCK"
        )

    raise ValueError(f"不支持的数据源模式: {resolved}")
