from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    MOCK_DATA_DIR: Path = DATA_DIR / "mock"
    REPORTS_DIR: Path = BASE_DIR / "reports"
    LOGS_DIR: Path = BASE_DIR / "logs"
    LOG_LEVEL: str = "INFO"
    API_PORT: int = 8000

    class Config:
        env_file = ".env"


settings = Settings()
