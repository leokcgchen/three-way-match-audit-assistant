"""对手方数据获取模块单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_fetcher import MockFetcher, get_fetcher


def test_fetch_yunchuang_normal() -> None:
    fetcher = MockFetcher()
    profile = fetcher.fetch("云创科技")

    assert profile.data_source == "MOCK"
    assert profile.registration_status == "存续"
    assert profile.is_abnormal is False
    assert profile.is_blacklisted is False
    assert "云创科技" in profile.company_name
    assert fetcher.get_data_source() == "MOCK"
    print("test_fetch_yunchuang_normal: PASS")


def test_fetch_hengda_abnormal() -> None:
    fetcher = MockFetcher()
    profile = fetcher.fetch("恒达设备")

    assert profile.data_source == "MOCK"
    assert profile.is_abnormal is True
    assert isinstance(profile.is_abnormal, bool)
    assert profile.registration_status == "存续"
    print("test_fetch_hengda_abnormal: PASS")


def test_fetch_unknown_company() -> None:
    fetcher = MockFetcher()
    profile = fetcher.fetch("不存在公司")

    assert profile.data_source == "MOCK"
    assert profile.registration_status == "未知"
    assert profile.company_name == "不存在公司"
    assert profile.is_abnormal is False
    assert profile.is_blacklisted is False
    print("test_fetch_unknown_company: PASS")


def test_fuzzy_match_short_name() -> None:
    fetcher = MockFetcher()
    profile = fetcher.fetch("云创")

    assert profile.data_source == "MOCK"
    assert "云创科技" in profile.company_name
    assert profile.registration_status == "存续"
    print("test_fuzzy_match_short_name: PASS")


def test_factory_returns_mock() -> None:
    fetcher = get_fetcher("MOCK")
    assert isinstance(fetcher, MockFetcher)
    print("test_factory_returns_mock: PASS")


if __name__ == "__main__":
    test_fetch_yunchuang_normal()
    test_fetch_hengda_abnormal()
    test_fetch_unknown_company()
    test_fuzzy_match_short_name()
    test_factory_returns_mock()
    print("全部测试通过：对手方数据模块基本功能正常。")
