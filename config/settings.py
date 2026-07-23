from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    MOCK_DATA_DIR: Path = DATA_DIR / "mock"
    REPORTS_DIR: Path = BASE_DIR / "reports"
    LOGS_DIR: Path = BASE_DIR / "logs"
    LOG_LEVEL: str = "INFO"

    QCC_API_KEY: Optional[str] = None
    QCC_API_BASE_URL: str = "https://api.qichacha.com"
    WIND_API_KEY: Optional[str] = None
    WIND_API_BASE_URL: str = "https://api.wind.com.cn"
    EASTMONEY_API_KEY: Optional[str] = None
    DATA_SOURCE_MODE: str = "MOCK"

    class Config:
        env_file = ".env"


settings = Settings()
